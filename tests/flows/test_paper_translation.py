"""Offline tests for deterministic interactive translation finalization."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from quantmind.configs import (
    PaperTranslationDraftCfg,
    PaperTranslationDraftInput,
)
from quantmind.configs.paper import LocalFilePath
from quantmind.flows.paper import PaperFlow
from scripts.prepare_codex_translation import prepare_codex_translation

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "paper"
    / "golden"
    / "paper.pdf"
)


async def _write_translation_files(
    workdir: Path,
) -> tuple[Path, Path]:
    _, manifest_path = await prepare_codex_translation(str(_FIXTURE), workdir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    draft = {
        "schema_version": "1",
        "source_content_hash": manifest["pdf"]["sha256"],
        "source_language": "en",
        "target_language": "ja",
        "generator": {
            "kind": "codex-interactive",
            "model_label": None,
            "draft_policy_version": "paper-translation-draft-v1",
            "instructions_sha256": manifest["translation_policy"][
                "instructions_sha256"
            ],
        },
        "pages": [
            {
                "page_number": page["page_number"],
                "translated_text": (
                    ""
                    if page["is_empty"]
                    else f"ページ{page['page_number']}の日本語訳。"
                ),
            }
            for page in manifest["pages"]
        ],
    }
    draft_path = workdir / "translation_draft.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    return manifest_path, draft_path


class PaperTranslationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_complete_translation_without_agent_provider(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, draft_path = await _write_translation_files(
                Path(directory)
            )
            summary_call = AsyncMock(side_effect=AssertionError("LLM called"))
            structure_call = AsyncMock(side_effect=AssertionError("LLM called"))
            with (
                patch(
                    "quantmind.flows._paper_summary."
                    "_AgentsPaperSummaryProvider.summarize",
                    new=summary_call,
                ),
                patch(
                    "quantmind.flows.paper._structure."
                    "_AgentsPaperStructureProvider.structure",
                    new=structure_call,
                ),
            ):
                result = await PaperFlow(PaperTranslationDraftCfg()).build(
                    PaperTranslationDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

            self.assertEqual(len(result.translation.pages), 4)
            self.assertEqual(
                result.translation.pages[0].source_text,
                result.source_revision.parsed.pages[0].text,
            )
            self.assertFalse(summary_call.called)
            self.assertFalse(structure_call.called)

    async def test_same_files_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, draft_path = await _write_translation_files(
                Path(directory)
            )
            flow = PaperFlow(PaperTranslationDraftCfg())
            input_value = PaperTranslationDraftInput(
                manifest_path=manifest_path,
                draft_path=draft_path,
            )
            self.assertEqual(
                await flow.build(input_value),
                await flow.build(input_value),
            )

    async def test_partial_blank_and_language_drafts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, draft_path = await _write_translation_files(
                Path(directory)
            )
            payload = json.loads(draft_path.read_text(encoding="utf-8"))
            payload["pages"].pop()
            draft_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cover every source page"):
                await PaperFlow(PaperTranslationDraftCfg()).build(
                    PaperTranslationDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

            manifest_path, draft_path = await _write_translation_files(
                Path(directory) / "blank"
            )
            payload = json.loads(draft_path.read_text(encoding="utf-8"))
            nonempty = next(
                page for page in payload["pages"] if page["translated_text"]
            )
            nonempty["translated_text"] = ""
            draft_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "is blank"):
                await PaperFlow(PaperTranslationDraftCfg()).build(
                    PaperTranslationDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

            manifest_path, draft_path = await _write_translation_files(
                Path(directory) / "language"
            )
            payload = json.loads(draft_path.read_text(encoding="utf-8"))
            payload["target_language"] = "en"
            draft_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "literal_error"):
                await PaperFlow(PaperTranslationDraftCfg()).build(
                    PaperTranslationDraftInput(
                        manifest_path=manifest_path,
                        draft_path=draft_path,
                    )
                )

    async def test_wrong_input_pair_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            await PaperFlow(PaperTranslationDraftCfg()).build(
                LocalFilePath(path=_FIXTURE)
            )


if __name__ == "__main__":
    unittest.main()
