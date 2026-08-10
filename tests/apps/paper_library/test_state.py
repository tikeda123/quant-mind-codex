"""Tests for the isolated human-organization sidecar."""

import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from apps.paper_library.state import (
    PaperLibraryStateStore,
    StateConflictError,
)


def _tiny_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1, 1), "white").save(output, format="PNG")
    return output.getvalue()


_TINY_PNG = _tiny_png()


class PaperLibraryStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.path = Path(self._directory.name) / "ui.sqlite3"
        self.store = PaperLibraryStateStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self._directory.cleanup()

    def test_schema_reopen_and_state_crud_with_optimistic_lock(self) -> None:
        source_id = uuid4()
        initial = self.store.get_state(source_id)
        self.assertEqual(initial.reading_status, "inbox")
        updated = self.store.update_state(
            source_id,
            expected_version=initial.version,
            display_title="My paper",
            reading_status="reading",
            starred=True,
            personal_memo="Next: reproduce the experiment.",
            last_opened_page=2,
            page_count=4,
        )
        self.assertEqual(updated.version, 2)
        self.assertTrue(updated.starred)
        with self.assertRaises(StateConflictError):
            self.store.update_state(
                source_id,
                expected_version=initial.version,
                display_title=None,
                reading_status="read",
                starred=False,
                personal_memo="",
                last_opened_page=1,
                page_count=4,
            )

        self.store.close()
        self.store = PaperLibraryStateStore(self.path)
        self.assertEqual(self.store.get_state(source_id), updated)

    def test_tags_collections_limits_and_case_insensitive_duplicates(
        self,
    ) -> None:
        source_id = uuid4()
        tagged = self.store.set_tags(source_id, ("Macro", "macro", " Rates "))
        self.assertEqual(tagged.tags, ("Macro", "Rates"))
        self.assertEqual(self.store.list_tags(), ("Macro", "Rates"))
        collection = self.store.create_collection("Research", "Active papers")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.create_collection("research")
        organized = self.store.set_collections(
            source_id, (collection.collection_id,)
        )
        self.assertEqual(organized.collections, ("Research",))

        with self.assertRaisesRegex(ValueError, "50 tags"):
            self.store.set_tags(
                source_id,
                tuple(f"tag-{index}" for index in range(51)),
            )

    def test_unseen_canonical_candidates_use_default_inbox_state(self) -> None:
        source_id = uuid4()
        self.assertEqual(
            self.store.filter_source_ids(
                candidate_source_ids=(source_id,),
                reading_status="inbox",
            ),
            (source_id,),
        )
        self.assertEqual(
            self.store.get_state(source_id).reading_status, "inbox"
        )

    def test_page_and_memo_limits_fail_closed(self) -> None:
        source_id = uuid4()
        state = self.store.get_state(source_id)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.store.update_state(
                source_id,
                expected_version=state.version,
                display_title=None,
                reading_status="inbox",
                starred=False,
                personal_memo="",
                last_opened_page=5,
                page_count=4,
            )
        with self.assertRaisesRegex(ValueError, "20000"):
            self.store.update_state(
                source_id,
                expected_version=state.version,
                display_title=None,
                reading_status="inbox",
                starred=False,
                personal_memo="x" * 20_001,
                last_opened_page=None,
                page_count=4,
            )

    def test_orphans_are_reported_and_never_deleted(self) -> None:
        known = uuid4()
        orphan = uuid4()
        self.store.get_state(known)
        self.store.get_state(orphan)

        self.assertEqual(
            self.store.orphaned_source_ids({known}),
            (orphan,),
        )
        self.assertEqual(
            self.store.get_state(orphan).source_revision_id, orphan
        )

    def test_visual_annotation_round_trip_review_and_limits(self) -> None:
        source_id = uuid4()
        linked_annotation_id = uuid4()
        saved = self.store.add_visual_annotation(
            source_id,
            image_content=_TINY_PNG,
            original_filename="frontier.png",
            media_type="image/png",
            caption="Efficient frontier",
            alt_text="A risk-return curve.",
            creator="Researcher",
            provenance="Created for explanation",
            review_status="attention",
            review_note="Check the dates.",
            linked_annotation_id=linked_annotation_id,
        )

        self.assertEqual(saved.width, 1)
        self.assertEqual(saved.height, 1)
        self.assertEqual(saved.image_content, _TINY_PNG)
        self.assertEqual(
            self.store.list_visual_annotations(source_id), (saved,)
        )
        self.assertEqual(self.store.inspect().visual_annotation_count, 1)
        self.assertEqual(
            self.store.inspect().attention_visual_annotation_count, 1
        )
        self.assertEqual(self.store.orphaned_source_ids(set()), (source_id,))

        updated = self.store.update_visual_annotation_review(
            saved.visual_annotation_id,
            expected_version=saved.version,
            review_status="verified",
            review_note="Compared with the paper.",
        )
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.review_status, "verified")
        with self.assertRaises(StateConflictError):
            self.store.update_visual_annotation_review(
                saved.visual_annotation_id,
                expected_version=saved.version,
                review_status="unreviewed",
                review_note="",
            )
        with self.assertRaisesRegex(ValueError, "already attached"):
            self.store.add_visual_annotation(
                source_id,
                image_content=_TINY_PNG,
                original_filename="duplicate.png",
                media_type="image/png",
                caption="Duplicate",
                alt_text="Duplicate image.",
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.store.add_visual_annotation(
                uuid4(),
                image_content=_TINY_PNG,
                original_filename="wrong.jpg",
                media_type="image/jpeg",
                caption="Wrong type",
                alt_text="Wrong media type.",
            )
        with self.assertRaisesRegex(ValueError, "valid image"):
            self.store.add_visual_annotation(
                uuid4(),
                image_content=b"not-an-image",
                original_filename="bad.png",
                media_type="image/png",
                caption="Invalid",
                alt_text="Invalid image.",
            )

    def test_schema_one_is_migrated_without_losing_state(self) -> None:
        source_id = uuid4()
        original = self.store.get_state(source_id)
        self.store.close()
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE visual_annotations")
            database.execute("PRAGMA user_version = 1")
            database.execute(
                "UPDATE ui_meta SET value = '1' WHERE key = 'schema_version'"
            )
        self.store = PaperLibraryStateStore(self.path)

        self.assertEqual(self.store.get_state(source_id), original)
        self.assertEqual(self.store.list_visual_annotations(source_id), ())
        with sqlite3.connect(self.path) as database:
            self.assertEqual(
                database.execute("PRAGMA user_version").fetchone()[0], 3
            )

    def test_translation_page_review_round_trip_and_conflict(self) -> None:
        translation_id = uuid4()
        initial = self.store.get_translation_page_review(translation_id, 2)
        self.assertEqual(initial.review_status, "unreviewed")
        updated = self.store.update_translation_page_review(
            translation_id,
            2,
            expected_version=initial.version,
            review_status="verified",
            review_note="Compared with the English source.",
        )
        self.assertEqual(updated.version, 2)
        self.assertEqual(
            self.store.list_translation_page_reviews(translation_id),
            (updated,),
        )
        with self.assertRaises(StateConflictError):
            self.store.update_translation_page_review(
                translation_id,
                2,
                expected_version=initial.version,
                review_status="attention",
                review_note="stale",
            )

    def test_schema_two_adds_translation_reviews(self) -> None:
        self.store.close()
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE translation_page_reviews")
            database.execute("PRAGMA user_version = 2")
            database.execute(
                "UPDATE ui_meta SET value = '2' WHERE key = 'schema_version'"
            )
        self.store = PaperLibraryStateStore(self.path)

        review = self.store.get_translation_page_review(uuid4(), 1)
        self.assertEqual(review.review_status, "unreviewed")
        with sqlite3.connect(self.path) as database:
            self.assertEqual(
                database.execute("PRAGMA user_version").fetchone()[0], 3
            )


if __name__ == "__main__":
    unittest.main()
