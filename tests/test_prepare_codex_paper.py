"""Offline tests for the bounded interactive-paper prepare helper."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_codex_paper import (
    _INSTRUCTIONS_PATH,
    prepare_codex_paper,
)

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "paper"
    / "golden"
    / "paper.pdf"
)


class PrepareCodexPaperTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_pdf_writes_revalidated_fixed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "paper-work"
            source_path, manifest_path = await prepare_codex_paper(
                str(_FIXTURE), workdir
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(source_path, workdir.resolve() / "source.pdf")
            self.assertEqual(source_path.read_bytes(), _FIXTURE.read_bytes())
            self.assertEqual(manifest["source"]["kind"], "local")
            self.assertEqual(manifest["pdf"]["path"], str(source_path))
            self.assertEqual(
                manifest["pdf"]["sha256"],
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(len(manifest["pages"]), 4)
            self.assertEqual(
                manifest["draft_policy"]["instructions_sha256"],
                hashlib.sha256(_INSTRUCTIONS_PATH.read_bytes()).hexdigest(),
            )

    async def test_existing_workdir_requires_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            await prepare_codex_paper(str(_FIXTURE), workdir)
            with self.assertRaises(FileExistsError):
                await prepare_codex_paper(str(_FIXTURE), workdir)

            first_hash = hashlib.sha256(
                (workdir / "source.pdf").read_bytes()
            ).hexdigest()
            await prepare_codex_paper(
                str(_FIXTURE), workdir, replace_workdir=True
            )
            self.assertEqual(
                hashlib.sha256(
                    (workdir / "source.pdf").read_bytes()
                ).hexdigest(),
                first_hash,
            )

    async def test_workdir_and_staged_file_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                await prepare_codex_paper(str(_FIXTURE), linked)

            workdir = root / "work"
            workdir.mkdir()
            target = root / "target.pdf"
            target.write_bytes(b"unchanged")
            (workdir / "source.pdf").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                await prepare_codex_paper(
                    str(_FIXTURE), workdir, replace_workdir=True
                )
            self.assertEqual(target.read_bytes(), b"unchanged")

    async def test_replace_never_reuses_an_existing_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            (workdir / "draft.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "new workdir"):
                await prepare_codex_paper(
                    str(_FIXTURE), workdir, replace_workdir=True
                )

    async def test_rejects_non_pdf_and_non_https_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / "source.txt"
            text_path.write_text("not a PDF", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "application/pdf"):
                await prepare_codex_paper(
                    str(text_path), Path(directory) / "work"
                )
            with self.assertRaisesRegex(ValueError, "public HTTPS"):
                await prepare_codex_paper(
                    "http://example.com/paper.pdf",
                    Path(directory) / "url-work",
                )


if __name__ == "__main__":
    unittest.main()
