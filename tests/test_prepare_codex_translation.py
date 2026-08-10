"""Offline tests for the interactive translation prepare helper."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_codex_translation import (
    _INSTRUCTIONS_PATH,
    prepare_codex_translation,
)

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "paper"
    / "golden"
    / "paper.pdf"
)


class PrepareCodexTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_pdf_writes_fixed_language_and_page_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "translation"
            source_path, manifest_path = await prepare_codex_translation(
                str(_FIXTURE), workdir
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(source_path.name, "source.pdf")
            self.assertEqual(manifest_path.name, "translation_manifest.json")
            self.assertEqual(manifest["source_language"], "en")
            self.assertEqual(manifest["target_language"], "ja")
            self.assertEqual(len(manifest["pages"]), 4)
            self.assertEqual(
                manifest["pdf"]["sha256"],
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                manifest["translation_policy"]["instructions_sha256"],
                hashlib.sha256(_INSTRUCTIONS_PATH.read_bytes()).hexdigest(),
            )

    async def test_existing_draft_and_non_https_url_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "existing"
            workdir.mkdir()
            (workdir / "translation_draft.json").write_text(
                "{}", encoding="utf-8"
            )
            with self.assertRaisesRegex(FileExistsError, "new workdir"):
                await prepare_codex_translation(
                    str(_FIXTURE), workdir, replace_workdir=True
                )
            with self.assertRaisesRegex(ValueError, "public HTTPS"):
                await prepare_codex_translation(
                    "http://example.com/paper.pdf",
                    Path(directory) / "url",
                )


if __name__ == "__main__":
    unittest.main()
