import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quantmind.library import LocalKnowledgeLibrary
from quantmind.library._internal.index_embeddings import (
    _LOCAL_EMBEDDING_DIMENSIONS,
    _LOCAL_EMBEDDING_MODEL,
    _LocalE5EmbeddingProvider,
)


class _Encoded:
    def __init__(self, values: list[list[float]]) -> None:
        self._values = values

    def tolist(self) -> list[list[float]]:
        return self._values


class _FakeSentenceTransformer:
    def __init__(self, dimension: int = _LOCAL_EMBEDDING_DIMENSIONS) -> None:
        self.dimension = dimension
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, texts: list[str], **kwargs: object) -> _Encoded:
        self.calls.append((texts, kwargs))
        return _Encoded(
            [
                [float(index + 1) for index in range(self.dimension)]
                for _ in texts
            ]
        )


class LocalE5EmbeddingProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_and_query_use_distinct_fixed_prefixes(self) -> None:
        fake = _FakeSentenceTransformer()
        constructor_calls: list[tuple[str, dict[str, object]]] = []

        def construct(repository: str, **kwargs: object):
            constructor_calls.append((repository, kwargs))
            return fake

        provider = _LocalE5EmbeddingProvider(cache_dir="model-cache")
        with patch(
            "quantmind.library._internal.index_embeddings.importlib.import_module",
            return_value=SimpleNamespace(SentenceTransformer=construct),
        ):
            document = await provider.embed(
                ["alpha"],
                model=_LOCAL_EMBEDDING_MODEL,
                dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                purpose="document",
            )
            query = await provider.embed(
                ["beta"],
                model=_LOCAL_EMBEDDING_MODEL,
                dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                purpose="query",
            )

        self.assertEqual(len(document[0]), _LOCAL_EMBEDDING_DIMENSIONS)
        self.assertEqual(len(query[0]), _LOCAL_EMBEDDING_DIMENSIONS)
        self.assertEqual(fake.calls[0][0], ["passage: alpha"])
        self.assertEqual(fake.calls[1][0], ["query: beta"])
        self.assertTrue(fake.calls[0][1]["normalize_embeddings"])
        self.assertTrue(fake.calls[0][1]["convert_to_numpy"])
        self.assertEqual(len(constructor_calls), 1)
        self.assertEqual(constructor_calls[0][1]["local_files_only"], True)
        self.assertEqual(constructor_calls[0][1]["device"], "cpu")
        self.assertEqual(constructor_calls[0][1]["trust_remote_code"], False)

    async def test_empty_input_does_not_load_the_model(self) -> None:
        provider = _LocalE5EmbeddingProvider()
        with patch(
            "quantmind.library._internal.index_embeddings.importlib.import_module"
        ) as import_module:
            values = await provider.embed(
                [],
                model=_LOCAL_EMBEDDING_MODEL,
                dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                purpose="document",
            )
        self.assertEqual(values, [])
        import_module.assert_not_called()

    async def test_cache_miss_never_falls_back_to_network(self) -> None:
        def fail(*args: object, **kwargs: object):
            del args, kwargs
            raise OSError("cache miss")

        provider = _LocalE5EmbeddingProvider()
        with patch(
            "quantmind.library._internal.index_embeddings.importlib.import_module",
            return_value=SimpleNamespace(SentenceTransformer=fail),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "not available in the configured cache"
            ):
                await provider.embed(
                    ["alpha"],
                    model=_LOCAL_EMBEDDING_MODEL,
                    dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                    purpose="document",
                )

    async def test_rejects_wrong_model_dimension_and_closed_use(self) -> None:
        provider = _LocalE5EmbeddingProvider()
        with self.assertRaisesRegex(ValueError, "identity is fixed"):
            await provider.embed(
                ["alpha"],
                model="another-model",
                dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                purpose="document",
            )
        with self.assertRaisesRegex(ValueError, "fixed at 384"):
            await provider.embed(
                ["alpha"],
                model=_LOCAL_EMBEDDING_MODEL,
                dimensions=2,
                purpose="document",
            )
        await provider.close()
        await provider.close()
        with self.assertRaisesRegex(RuntimeError, "provider is closed"):
            await provider.embed(
                ["alpha"],
                model=_LOCAL_EMBEDDING_MODEL,
                dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                purpose="document",
            )

    async def test_rejects_cached_model_with_wrong_dimension(self) -> None:
        with patch(
            "quantmind.library._internal.index_embeddings.importlib.import_module",
            return_value=SimpleNamespace(
                SentenceTransformer=lambda *args, **kwargs: (
                    _FakeSentenceTransformer(dimension=2)
                )
            ),
        ):
            provider = _LocalE5EmbeddingProvider()
            with self.assertRaisesRegex(RuntimeError, "unexpected dimension"):
                await provider.embed(
                    ["alpha"],
                    model=_LOCAL_EMBEDDING_MODEL,
                    dimensions=_LOCAL_EMBEDDING_DIMENSIONS,
                    purpose="document",
                )

    async def test_open_local_is_lazy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.db"
            with patch(
                "quantmind.library._internal.index_embeddings.importlib.import_module"
            ) as import_module:
                library = await LocalKnowledgeLibrary.open_local(path)
                await library.close()
            import_module.assert_not_called()
