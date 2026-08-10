#!/usr/bin/env python3
"""Explicitly cache QuantMind's one fixed local embedding model revision."""

import argparse
from pathlib import Path

from sentence_transformers import SentenceTransformer

_MODEL_REPOSITORY = "intfloat/multilingual-e5-small"
_MODEL_REVISION = "fd1525a9fd15316a2d503bf26ab031a61d056e98"
_MODEL_DIMENSIONS = 384


def cache_model(cache_dir: Path) -> Path:
    """Download and validate the pinned model only on explicit invocation."""
    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(
        _MODEL_REPOSITORY,
        revision=_MODEL_REVISION,
        cache_folder=str(cache_dir),
        device="cpu",
        trust_remote_code=False,
    )
    get_dimension = getattr(model, "get_embedding_dimension", None)
    dimension = (
        get_dimension()
        if callable(get_dimension)
        else model.get_sentence_embedding_dimension()
    )
    if dimension != _MODEL_DIMENSIONS:
        raise RuntimeError(
            f"unexpected embedding dimension {dimension}; "
            f"expected {_MODEL_DIMENSIONS}"
        )
    print(f"cached_model={_MODEL_REPOSITORY}@{_MODEL_REVISION}")
    print(f"cache_dir={cache_dir}")
    print(f"dimensions={dimension}")
    return cache_dir


def main() -> int:
    """Parse the one required cache path and cache the fixed revision."""
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly download the fixed multilingual E5 model. Normal "
            "library and UI execution never downloads model files."
        )
    )
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    cache_model(args.cache_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
