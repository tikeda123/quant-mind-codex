"""Validate, register, reopen, and search an interactive cited draft."""

import argparse
import asyncio
from pathlib import Path

from quantmind.configs import CitedPaperDraftInput, PaperCitedDraftCfg
from quantmind.flows import PaperFlow
from quantmind.library import (
    LocalKnowledgeLibrary,
    SemanticQuery,
)


async def run(
    *,
    manifest_path: Path,
    draft_path: Path,
    database_path: Path,
    cache_dir: Path,
) -> None:
    """Finalize files, register atomically, and prove reopen retrieval."""
    result = await PaperFlow(PaperCitedDraftCfg()).build(
        CitedPaperDraftInput(
            manifest_path=manifest_path,
            draft_path=draft_path,
        )
    )
    library = await LocalKnowledgeLibrary.open_local(
        database_path, cache_dir=cache_dir
    )
    try:
        registration = await library.put_annotated_paper(result)
    finally:
        await library.close()

    library = await LocalKnowledgeLibrary.open_local(
        database_path, cache_dir=cache_dir
    )
    try:
        restored = await library.get_annotated_paper(
            registration.registration_id
        )
        hits = await library.search(
            SemanticQuery(text="この論文の主要な方法は何ですか", top_k=5)
        )
    finally:
        await library.close()

    print(f"registration_id={registration.registration_id}")
    print(f"source_revision_id={restored.source_revision.id}")
    print(f"pages={len(restored.source_revision.parsed.pages)}")
    print(f"annotations={len(restored.annotation_set.annotations)}")
    print(f"search_hits={len(hits)}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    return parser


if __name__ == "__main__":
    arguments = _parser().parse_args()
    asyncio.run(
        run(
            manifest_path=arguments.manifest,
            draft_path=arguments.draft,
            database_path=arguments.database,
            cache_dir=arguments.cache_dir,
        )
    )
