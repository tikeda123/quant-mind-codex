"""Tests for human-facing bibliographic display fallbacks."""

import unittest
from datetime import datetime, timezone

from apps.paper_library.components.paper_card import (
    display_authors,
    display_publication,
)


class PaperCardDisplayTests(unittest.TestCase):
    def test_reviewed_bibliography_overrides_canonical_values(self) -> None:
        published_at = datetime(2020, 1, 2, tzinfo=timezone.utc)

        self.assertEqual(
            display_authors(("Canonical Author",), ("Reviewed Author",)),
            "Reviewed Author",
        )
        self.assertEqual(
            display_publication(published_at, "Mar. 1952"),
            "Mar. 1952",
        )

    def test_missing_overrides_fall_back_without_blank_cells(self) -> None:
        published_at = datetime(2020, 1, 2, tzinfo=timezone.utc)

        self.assertEqual(
            display_authors(("Canonical Author",)), "Canonical Author"
        )
        self.assertEqual(display_authors(()), "著者未取得")
        self.assertEqual(display_publication(published_at), "2020-01-02")
        self.assertEqual(display_publication(None), "未取得")


if __name__ == "__main__":
    unittest.main()
