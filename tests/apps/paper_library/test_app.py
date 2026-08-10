"""Streamlit shell smoke tests for the local paper library."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

_ROOT = Path(__file__).resolve().parents[3]


class PaperLibraryAppTests(unittest.TestCase):
    def test_entrypoint_bootstraps_imports_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "QUANTMIND_LIBRARY_DB": str(root / "library.sqlite3"),
                    "QUANTMIND_UI_DB": str(root / "ui.sqlite3"),
                    "QUANTMIND_MODEL_CACHE": str(root / "models"),
                    "QUANTMIND_INTAKE_ROOT": str(root / "intake"),
                }
            )
            app_path = _ROOT / "apps" / "paper_library" / "app.py"
            command = (
                "from streamlit.testing.v1 import AppTest\n"
                f"app = AppTest.from_file({str(app_path)!r}, "
                "default_timeout=20).run()\n"
                "if app.exception:\n"
                "    raise RuntimeError(app.exception[0].message)\n"
            )

            completed = subprocess.run(
                [sys.executable, "-c", command],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )

    def test_empty_dashboard_runs_with_six_unique_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "QUANTMIND_LIBRARY_DB": str(root / "library.sqlite3"),
                "QUANTMIND_UI_DB": str(root / "ui.sqlite3"),
                "QUANTMIND_MODEL_CACHE": str(root / "models"),
                "QUANTMIND_INTAKE_ROOT": str(root / "intake"),
            }
            with patch.dict(os.environ, environment, clear=False):
                app = AppTest.from_file(
                    _ROOT / "apps" / "paper_library" / "app.py",
                    default_timeout=20,
                ).run()

            self.assertEqual(list(app.exception), [])
            self.assertIn("ダッシュボード", [item.value for item in app.title])
            self.assertEqual(
                [item.label for item in app.metric],
                [
                    "保存論文",
                    "検索可能",
                    "要確認",
                    "破損",
                    "未読",
                    "注釈",
                    "日本語訳",
                ],
            )
            source = (_ROOT / "apps" / "paper_library" / "app.py").read_text(
                encoding="utf-8"
            )
            self.assertEqual(source.count("url_path="), 6)

    def test_custom_navigation_has_no_legacy_page_scripts(self) -> None:
        legacy_pages = _ROOT / "apps" / "paper_library" / "pages"

        self.assertEqual(list(legacy_pages.glob("*.py")), [])

    def test_ui_has_no_codex_api_or_destructive_controls(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (_ROOT / "apps" / "paper_library").rglob("*.py")
        ).casefold()
        for forbidden in (
            "openai_api_key",
            "codex task",
            "quantmind.library._internal",
            'st.button("削除',
            'st.button("再埋め込み',
            'st.button("repair',
        ):
            self.assertNotIn(forbidden, source)

        detail_source = (
            _ROOT / "apps" / "paper_library" / "views" / "paper_detail.py"
        ).read_text(encoding="utf-8")
        for required in (
            "画像注釈",
            "画像注釈として保存",
            "原論文の引用証拠ではなく",
            "確認状態を更新",
        ):
            self.assertIn(required, detail_source)


if __name__ == "__main__":
    unittest.main()
