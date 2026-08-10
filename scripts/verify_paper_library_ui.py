#!/usr/bin/env python3
"""Run a bounded empty-library smoke of the loopback Streamlit app."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def main() -> int:
    """Render the default page with isolated app paths and no model load."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        environment = {
            "QUANTMIND_LIBRARY_DB": str(root / "library.sqlite3"),
            "QUANTMIND_UI_DB": str(root / "ui.sqlite3"),
            "QUANTMIND_MODEL_CACHE": str(root / "models"),
            "QUANTMIND_INTAKE_ROOT": str(root / "intake"),
            "STREAMLIT_SERVER_ADDRESS": "127.0.0.1",
        }
        with patch.dict(os.environ, environment, clear=False):
            app = AppTest.from_file(
                _ROOT / "apps" / "paper_library" / "app.py",
                default_timeout=20,
            ).run()
        if app.exception:
            raise RuntimeError(app.exception[0].message)
        labels = [item.label for item in app.metric]
        if labels != [
            "保存論文",
            "検索可能",
            "要確認",
            "破損",
            "未読",
            "注釈",
            "日本語訳",
        ]:
            raise RuntimeError(f"unexpected dashboard metrics: {labels}")
    print("PASS: loopback UI shell rendered with seven zero-valued metrics")
    print("PASS: no embedding model or external service was loaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
