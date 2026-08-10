"""Tests for bounded one-page PDF evidence rendering."""

import unittest
from pathlib import Path

from apps.paper_library.pdf_preview import (
    PaperPreviewError,
    _clear_preview_cache,
    _preview_cache_info,
    render_cited_page,
)

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "paper"
    / "golden"
    / "paper.pdf"
)


class PaperPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_preview_cache()

    def test_renders_page_and_caches_same_evidence(self) -> None:
        pdf = _FIXTURE.read_bytes()
        first = render_cited_page(
            pdf,
            page_number=1,
            quote="QuantMind Golden Paper",
        )
        second = render_cited_page(
            pdf,
            page_number=1,
            quote="QuantMind Golden Paper",
        )

        self.assertEqual(first, second)
        self.assertTrue(first.png_bytes.startswith(b"\x89PNG"))
        self.assertTrue(first.highlight_found)
        self.assertGreaterEqual(first.highlight_count, 1)
        self.assertEqual(_preview_cache_info()[0], 1)

    def test_missing_quote_is_rendered_without_false_validation_error(
        self,
    ) -> None:
        rendered = render_cited_page(
            _FIXTURE.read_bytes(),
            page_number=2,
            quote="this quote does not exist visually",
        )
        self.assertFalse(rendered.highlight_found)
        self.assertEqual(rendered.highlight_count, 0)

    def test_page_range_corrupt_pdf_and_scale_are_rejected(self) -> None:
        with self.assertRaisesRegex(PaperPreviewError, "outside"):
            render_cited_page(
                _FIXTURE.read_bytes(),
                page_number=99,
                quote=None,
            )
        with self.assertRaisesRegex(PaperPreviewError, "開けません"):
            render_cited_page(b"not a PDF", page_number=1, quote=None)
        with self.assertRaisesRegex(PaperPreviewError, "scale"):
            render_cited_page(
                _FIXTURE.read_bytes(),
                page_number=1,
                quote=None,
                scale=10,
            )


if __name__ == "__main__":
    unittest.main()
