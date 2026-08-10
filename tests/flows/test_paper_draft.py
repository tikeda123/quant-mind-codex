"""Offline tests for deterministic cited-draft finalization."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from quantmind.configs import CitedPaperDraftInput, PaperCitedDraftCfg
from quantmind.configs.paper import LocalFilePath
from quantmind.flows.paper import PaperFlow
from quantmind.preprocess.format import parse_pdf
from quantmind.rag import SentenceSplitterConfig, chunk_parsed_document

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "paper"
    / "golden"
    / "paper.pdf"
)
_INSTRUCTIONS_HASH = hashlib.sha256(b"test cited draft policy").hexdigest()


def _unique_quote(text: str, peers: tuple[str, ...]) -> str:
    compact = text.strip()
    for size in (24, 40, 80, min(200, len(compact))):
        quote = compact[:size].strip()
        if len(quote) >= 8 and sum(quote in peer for peer in peers) == 1:
            return quote
    if len(compact) <= 500 and sum(compact in peer for peer in peers) == 1:
        return compact
    raise AssertionError("fixture chunk has no short unique quote")


async def _write_staged_files(
    workdir: Path,
    *,
    draft_suffix: str = "",
) -> tuple[Path, Path]:
    raw = _FIXTURE.read_bytes()
    source_path = workdir / "source.pdf"
    source_path.write_bytes(raw)
    parsed = await parse_pdf(raw)
    chunks = chunk_parsed_document(
        parsed,
        config=SentenceSplitterConfig(chunk_size=128, chunk_overlap=16),
    )
    peers = tuple(chunk.text for chunk in chunks)
    selected = []
    pages: set[int] = set()
    for chunk in chunks:
        quote = _unique_quote(chunk.text, peers)
        selected.append((chunk.page_number, quote))
        pages.add(chunk.page_number)
        if len(selected) >= 3 and len(pages) >= 2:
            break
    if len(selected) < 3 or len(pages) < 2:
        raise AssertionError("golden PDF lacks citation coverage")

    source_hash = hashlib.sha256(raw).hexdigest()
    manifest = {
        "schema_version": "1",
        "source": {
            "kind": "local",
            "requested_uri": _FIXTURE.resolve().as_uri(),
            "resolved_uri": source_path.resolve().as_uri(),
            "media_type": "application/pdf",
            "fetched_at": "2026-08-10T00:00:00Z",
            "available_at": "2026-08-10T00:00:00Z",
            "published_at": None,
            "arxiv_id": None,
            "title": "Golden Paper",
            "authors": ["QuantMind Tests"],
        },
        "pdf": {
            "path": str(source_path.resolve()),
            "sha256": source_hash,
            "size_bytes": len(raw),
        },
        "parser": {
            "name": parsed.parser_name,
            "version": parsed.parser_version,
            "cleanup_policy": parsed.cleanup_version,
        },
        "pages": [
            {
                "page_number": page.page_number,
                "text": page.text,
                "text_sha256": hashlib.sha256(
                    page.text.encode("utf-8")
                ).hexdigest(),
                "is_empty": not page.text.strip(),
            }
            for page in parsed.pages
        ],
        "draft_policy": {
            "version": "cited-paper-draft-v1",
            "instructions_sha256": _INSTRUCTIONS_HASH,
        },
    }
    draft = {
        "schema_version": "1",
        "source_content_hash": source_hash,
        "generator": {
            "kind": "codex-interactive",
            "model_label": None,
            "draft_policy_version": "cited-paper-draft-v1",
            "instructions_sha256": _INSTRUCTIONS_HASH,
        },
        "summary": {
            "text": f"A page-cited summary{draft_suffix}.",
            "citations": [
                {"page_number": page, "quote": quote}
                for page, quote in selected
            ],
        },
        "annotations": [
            {
                "kind": "source_fact",
                "text": "A fact stated by the source.",
                "citations": [
                    {
                        "page_number": selected[0][0],
                        "quote": selected[0][1],
                    }
                ],
            },
            {
                "kind": "codex_interpretation",
                "text": "An interpretation kept distinct from source fact.",
                "citations": [
                    {
                        "page_number": selected[1][0],
                        "quote": selected[1][1],
                    }
                ],
            },
        ],
    }
    manifest_path = workdir / "manifest.json"
    draft_path = workdir / "draft.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    return manifest_path, draft_path


class CitedPaperDraftFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_annotations_without_an_agent_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, draft_path = await _write_staged_files(
                Path(directory)
            )
            semantic_call = AsyncMock(side_effect=AssertionError("LLM called"))
            structure_call = AsyncMock(side_effect=AssertionError("LLM called"))
            with (
                patch(
                    "quantmind.flows._paper_summary."
                    "_AgentsPaperSummaryProvider.summarize",
                    new=semantic_call,
                ),
                patch(
                    "quantmind.flows.paper._structure."
                    "_AgentsPaperStructureProvider.structure",
                    new=structure_call,
                ),
            ):
                result = await PaperFlow(
                    PaperCitedDraftCfg(
                        chunk_size=128,
                        chunk_overlap=16,
                    )
                ).build(
                    CitedPaperDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

            self.assertEqual(result.source_revision.title, "Golden Paper")
            self.assertGreaterEqual(len(result.global_summary.citations), 3)
            self.assertEqual(len(result.annotation_set.annotations), 2)
            self.assertFalse(semantic_call.called)
            self.assertFalse(structure_call.called)

    async def test_same_staged_files_have_deterministic_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, draft_path = await _write_staged_files(
                Path(directory)
            )
            flow = PaperFlow(
                PaperCitedDraftCfg(chunk_size=128, chunk_overlap=16)
            )
            input_value = CitedPaperDraftInput(
                manifest_path=manifest_path,
                draft_path=draft_path,
            )
            first = await flow.build(input_value)
            second = await flow.build(input_value)

            self.assertEqual(first, second)

    async def test_changed_draft_changes_only_draft_derived_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            manifest_path, draft_path = await _write_staged_files(workdir)
            cfg = PaperCitedDraftCfg(chunk_size=128, chunk_overlap=16)
            first = await PaperFlow(cfg).build(
                CitedPaperDraftInput(
                    manifest_path=manifest_path,
                    draft_path=draft_path,
                )
            )
            _, draft_path = await _write_staged_files(
                workdir, draft_suffix=" with a revision"
            )
            changed = await PaperFlow(cfg).build(
                CitedPaperDraftInput(
                    manifest_path=manifest_path,
                    draft_path=draft_path,
                )
            )

            self.assertEqual(
                first.source_revision.id, changed.source_revision.id
            )
            self.assertEqual(first.chunk_set.id, changed.chunk_set.id)
            self.assertNotEqual(
                first.global_summary.id, changed.global_summary.id
            )
            self.assertNotEqual(
                first.annotation_set.id, changed.annotation_set.id
            )

    async def test_pdf_and_quote_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            manifest_path, draft_path = await _write_staged_files(workdir)
            source_path = workdir / "source.pdf"
            source_path.write_bytes(source_path.read_bytes() + b"drift")

            with self.assertRaisesRegex(ValueError, "hash"):
                await PaperFlow(
                    PaperCitedDraftCfg(chunk_size=128, chunk_overlap=16)
                ).build(
                    CitedPaperDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

            manifest_path, draft_path = await _write_staged_files(workdir)
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            draft["summary"]["citations"][0]["quote"] = (
                "this exact quote is absent from the paper"
            )
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                await PaperFlow(
                    PaperCitedDraftCfg(chunk_size=128, chunk_overlap=16)
                ).build(
                    CitedPaperDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

    async def test_extra_draft_field_and_wrong_input_pair_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, draft_path = await _write_staged_files(
                Path(directory)
            )
            payload = json.loads(draft_path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            draft_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extra_forbidden"):
                await PaperFlow(PaperCitedDraftCfg()).build(
                    CitedPaperDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

        with self.assertRaises(TypeError):
            await PaperFlow(PaperCitedDraftCfg()).build(
                LocalFilePath(path=_FIXTURE)
            )

    def test_cfg_bounds_are_validated(self) -> None:
        with self.assertRaises(ValidationError):
            PaperCitedDraftCfg(chunk_size=32, chunk_overlap=32)
        with self.assertRaises(ValidationError):
            PaperCitedDraftCfg(
                min_summary_citations=1,
                min_summary_pages=2,
            )


if __name__ == "__main__":
    unittest.main()
