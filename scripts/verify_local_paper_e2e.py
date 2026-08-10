#!/usr/bin/env python3
"""Verify the API-key-free cited-paper path with a pre-cached local model."""

import argparse
import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path

from prepare_codex_paper import prepare_codex_paper

from quantmind.configs import CitedPaperDraftInput, PaperCitedDraftCfg
from quantmind.flows import PaperFlow
from quantmind.library import LocalKnowledgeLibrary, SemanticQuery

_ROOT = Path(__file__).resolve().parents[1]
_PDF = _ROOT / "tests" / "fixtures" / "paper" / "golden" / "paper.pdf"
_DRAFT = _ROOT / "tests" / "fixtures" / "paper" / "draft" / "draft.json"


async def verify(cache_dir: Path) -> None:
    """Run prepare through reopen/search without an LLM or remote fetch."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workdir = root / "work"
        _, manifest_path = await prepare_codex_paper(str(_PDF), workdir)
        draft_path = workdir / "draft.json"
        shutil.copyfile(_DRAFT, draft_path)
        result = await PaperFlow(PaperCitedDraftCfg()).build(
            CitedPaperDraftInput(
                manifest_path=manifest_path,
                draft_path=draft_path,
            )
        )
        database = root / "library.sqlite3"
        library = await LocalKnowledgeLibrary.open_local(
            database, cache_dir=cache_dir
        )
        try:
            first = await library.put_annotated_paper(result)
            second = await library.put_annotated_paper(result)
            if first != second:
                raise RuntimeError("idempotent registration changed")
        finally:
            await library.close()

        library = await LocalKnowledgeLibrary.open_local(
            database, cache_dir=cache_dir
        )
        try:
            restored = await library.get_annotated_paper(first.registration_id)
            raw_asset = await library.get_paper_asset(
                restored.source_revision.id,
                restored.source_revision.raw_asset_id,
            )
            if hashlib.sha256(raw_asset.content).hexdigest() != (
                restored.source_revision.source.content_hash
            ):
                raise RuntimeError("restored PDF hash mismatch")
            for query in (
                "cross-sectional momentum portfolio",
                "クロスセクショナル・モメンタムのポートフォリオ",
            ):
                hits = await library.search(SemanticQuery(text=query, top_k=5))
                if not hits or not any(
                    hit.locator.source_revision_id
                    == restored.source_revision.id
                    for hit in hits
                ):
                    raise RuntimeError(f"expected top-5 hit missing: {query}")
        finally:
            await library.close()
    print("PASS: prepare/finalize/register/reopen preserved exact PDF evidence")
    print("PASS: repeated registration was idempotent")
    print(
        "PASS: cached local English and Japanese searches returned top-5 hits"
    )


def main() -> int:
    """Require an explicit model cache and run the local acceptance slice."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(verify(args.cache_dir.expanduser().resolve()))
    except RuntimeError as exc:
        if "configured cache" in str(exc):
            raise SystemExit(
                "Fixed model cache is missing. Run "
                "scripts/cache_local_embedding_model.py explicitly first."
            ) from exc
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
