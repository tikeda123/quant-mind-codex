"""Validate, register, and reopen an interactive page translation."""

import argparse
import asyncio
from pathlib import Path

from quantmind.configs import (
    PaperTranslationDraftCfg,
    PaperTranslationDraftInput,
)
from quantmind.flows import PaperFlow
from quantmind.library import LocalKnowledgeLibrary


async def run(
    *,
    manifest_path: Path,
    draft_path: Path,
    database_path: Path,
    cache_dir: Path | None,
) -> None:
    """Finalize files and prove vectorless translation round-trip."""
    result = await PaperFlow(PaperTranslationDraftCfg()).build(
        PaperTranslationDraftInput(
            manifest_path=manifest_path,
            draft_path=draft_path,
        )
    )
    library = await LocalKnowledgeLibrary.open_local(
        database_path,
        cache_dir=cache_dir,
    )
    try:
        registration = await library.put_translation(result)
        restored = await library.open_translation(result.translation.id)
    finally:
        await library.close()

    print(f"translation_registration_id={registration.registration_id}")
    print(f"source_revision_id={restored.source_revision_id}")
    print(f"translation_id={restored.id}")
    print(f"translated_pages={len(restored.pages)}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path)
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
