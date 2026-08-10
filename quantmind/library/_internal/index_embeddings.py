"""Private embedding clients used to build and query the local index."""

import asyncio
import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from openai import AsyncOpenAI

_EmbeddingPurpose = Literal["document", "query"]

_LOCAL_EMBEDDING_REPOSITORY = "intfloat/multilingual-e5-small"
_LOCAL_EMBEDDING_REVISION = "fd1525a9fd15316a2d503bf26ab031a61d056e98"
_LOCAL_EMBEDDING_POLICY = "e5-query-passage-v1"
_LOCAL_EMBEDDING_MODEL = (
    f"{_LOCAL_EMBEDDING_REPOSITORY}@{_LOCAL_EMBEDDING_REVISION}"
    f"#{_LOCAL_EMBEDDING_POLICY}"
)
_LOCAL_EMBEDDING_DIMENSIONS = 384


class _EmbeddingProvider(Protocol):
    """Embedding seam used by production code and deterministic tests."""

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        dimensions: int | None,
        purpose: _EmbeddingPurpose,
    ) -> Sequence[Sequence[float]]:
        """Embed texts in input order."""
        ...

    async def close(self) -> None:
        """Release provider-owned resources."""
        ...


class _OpenAIEmbeddingProvider:
    """Generate index embeddings without exposing provider response types."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        dimensions: int | None,
        purpose: _EmbeddingPurpose,
    ) -> list[list[float]]:
        del purpose
        client = self._client
        if client is None:
            client = AsyncOpenAI()
            self._client = client
        kwargs: dict[str, Any] = {
            "input": list(texts),
            "model": model,
            "encoding_format": "float",
        }
        if dimensions is not None:
            kwargs["dimensions"] = dimensions
        response = await client.embeddings.create(**kwargs)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class _LocalE5EmbeddingProvider:
    """Generate fixed, normalized multilingual E5 embeddings on local CPU."""

    def __init__(self, *, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = (
            str(Path(cache_dir).expanduser().resolve())
            if cache_dir is not None
            else None
        )
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str,
        dimensions: int | None,
        purpose: _EmbeddingPurpose,
    ) -> list[list[float]]:
        if self._closed:
            raise RuntimeError("Local embedding provider is closed")
        if model != _LOCAL_EMBEDDING_MODEL:
            raise ValueError("Local embedding model identity is fixed")
        if dimensions not in (None, _LOCAL_EMBEDDING_DIMENSIONS):
            raise ValueError(
                "Local embedding dimensions are fixed at "
                f"{_LOCAL_EMBEDDING_DIMENSIONS}"
            )
        if not texts:
            return []
        prefix = "passage: " if purpose == "document" else "query: "
        prefixed = [f"{prefix}{text}" for text in texts]
        async with self._lock:
            values = await asyncio.to_thread(self._encode, prefixed)
        return [list(map(float, value)) for value in values]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._model
        if model is None:
            try:
                sentence_transformers = importlib.import_module(
                    "sentence_transformers"
                )
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Local embeddings require the 'full' optional dependencies"
                ) from exc
            try:
                model = sentence_transformers.SentenceTransformer(
                    _LOCAL_EMBEDDING_REPOSITORY,
                    revision=_LOCAL_EMBEDDING_REVISION,
                    cache_folder=self._cache_dir,
                    device="cpu",
                    local_files_only=True,
                    trust_remote_code=False,
                )
            except Exception as exc:
                raise RuntimeError(
                    "The fixed local embedding model is not available in the "
                    "configured cache; download the exact revision before "
                    "running offline"
                ) from exc
            get_dimension = getattr(model, "get_embedding_dimension", None)
            dimension = (
                get_dimension()
                if callable(get_dimension)
                else model.get_sentence_embedding_dimension()
            )
            if dimension != _LOCAL_EMBEDDING_DIMENSIONS:
                raise RuntimeError(
                    "The cached local embedding model has an unexpected "
                    f"dimension: {dimension}"
                )
            self._model = model
        encoded = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        values = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        return [list(value) for value in values]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            self._model = None
