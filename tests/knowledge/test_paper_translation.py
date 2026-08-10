"""Canonical page-aligned paper translation tests."""

import unittest

from pydantic import ValidationError

from quantmind.knowledge import PaperTranslatedResult, PaperTranslation
from tests.paper_helpers import build_paper_translation_result


class PaperTranslationTests(unittest.TestCase):
    def test_translation_is_complete_self_contained_and_deterministic(
        self,
    ) -> None:
        first = build_paper_translation_result()
        second = build_paper_translation_result()

        self.assertEqual(first, second)
        self.assertEqual(len(first.translation.pages), 2)
        self.assertEqual(
            first.translation.pages[0].source_text,
            first.source_revision.parsed.pages[0].text,
        )
        self.assertEqual(
            first.translation.source_content_hash,
            first.source_revision.parsed.source_hash,
        )
        restored = PaperTranslation.model_validate_json(
            first.translation.model_dump_json()
        )
        self.assertEqual(restored, first.translation)

    def test_changed_draft_changes_translation_identity_only(self) -> None:
        first = build_paper_translation_result(draft_marker="first")
        changed = build_paper_translation_result(draft_marker="changed")

        self.assertEqual(first.source_revision.id, changed.source_revision.id)
        self.assertNotEqual(first.translation.id, changed.translation.id)

    def test_blank_or_partial_translation_is_rejected(self) -> None:
        result = build_paper_translation_result()
        with self.assertRaisesRegex(ValueError, "cover every source page"):
            PaperTranslation.from_draft(
                result.source_revision,
                producer=result.translation.producer,
                translated_pages=("1ページだけ",),
            )
        with self.assertRaises(ValidationError):
            PaperTranslation.from_draft(
                result.source_revision,
                producer=result.translation.producer,
                translated_pages=("", "2ページ"),
            )

    def test_source_text_tampering_fails_cross_artifact_validation(
        self,
    ) -> None:
        result = build_paper_translation_result()
        page = result.translation.pages[0].model_copy(
            update={"source_text": "tampered"}
        )
        translation = result.translation.model_copy(
            update={"pages": (page, *result.translation.pages[1:])}
        )
        with self.assertRaises(ValidationError):
            PaperTranslatedResult(
                source_revision=result.source_revision,
                translation=translation,
            )


if __name__ == "__main__":
    unittest.main()
