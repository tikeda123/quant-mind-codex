"""Tests for the dedicated event-loop app service and path boundaries."""

import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from streamlit.testing.v1 import AppTest

from apps.paper_library.service import (
    PaperLibraryAppService,
    validate_loopback_address,
)
from quantmind.library import LocalKnowledgeLibrary
from tests.library.test_paper import _FakeEmbeddingProvider
from tests.paper_helpers import (
    build_annotated_paper_result,
    build_paper_translation_result,
)


def _tiny_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1, 1), "white").save(output, format="PNG")
    return output.getvalue()


_TINY_PNG = _tiny_png()


def _render_paper_detail(service: object, source_revision_id: str) -> None:
    from typing import cast

    import streamlit as st

    from apps.paper_library.service import PaperLibraryAppService
    from apps.paper_library.views import paper_detail

    st.session_state["selected_source_revision_id"] = source_revision_id
    paper_detail.render(cast(PaperLibraryAppService, service))


class PaperLibraryAppServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.provider = _FakeEmbeddingProvider()

        async def opener() -> LocalKnowledgeLibrary:
            return await LocalKnowledgeLibrary.open(
                self.root / "knowledge.sqlite3",
                embedding_model="fake-2d",
                embedding_dimensions=2,
                _embedding_provider=self.provider,
            )

        self.service = PaperLibraryAppService(
            knowledge_db_path=self.root / "knowledge.sqlite3",
            sidecar_db_path=self.root / "sidecar.sqlite3",
            model_cache_path=self.root / "model-cache",
            intake_work_root=self.root / "intake",
            _library_opener=opener,
        )

    def tearDown(self) -> None:
        self.service.close()
        self._directory.cleanup()

    def test_repeated_browse_calls_do_not_embed_or_cross_paths(self) -> None:
        self.assertEqual(self.service.list_papers().total_count, 0)
        self.assertEqual(
            self.service.inspect_library().source_revision_count, 0
        )
        self.assertEqual(self.service.list_papers().entries, ())
        self.assertEqual(self.service.list_source_ids(), ())
        self.assertEqual(self.provider.calls, [])
        self.assertNotEqual(
            self.service.knowledge_db_path,
            self.service.sidecar_db_path,
        )

    def test_register_and_sidecar_update_leave_canonical_bytes_unchanged(
        self,
    ) -> None:
        result = build_annotated_paper_result()
        record = self.service.register(result)
        before = hashlib.sha256(
            self.service.knowledge_db_path.read_bytes()
        ).hexdigest()
        state = self.service.state.get_state(result.source_revision.id)
        self.service.state.update_state(
            result.source_revision.id,
            expected_version=state.version,
            display_title="Personal title",
            reading_status="reading",
            starred=True,
            personal_memo="Unverified personal memo",
            last_opened_page=1,
            page_count=2,
        )
        after = hashlib.sha256(
            self.service.knowledge_db_path.read_bytes()
        ).hexdigest()

        self.assertEqual(before, after)
        self.assertEqual(
            self.service.get_paper_details(
                result.source_revision.id,
                registration_id=record.registration_id,
            ).source,
            result.source_revision,
        )

    def test_visual_annotation_validates_link_and_stays_in_sidecar(
        self,
    ) -> None:
        result = build_annotated_paper_result()
        self.service.register(result)
        before = hashlib.sha256(
            self.service.knowledge_db_path.read_bytes()
        ).hexdigest()
        linked_id = result.annotation_set.annotations[0].annotation_id

        visual = self.service.add_visual_annotation(
            result.source_revision.id,
            image_content=_TINY_PNG,
            original_filename="explanation.png",
            media_type="image/png",
            caption="Explanation",
            alt_text="An explanatory chart.",
            linked_annotation_id=linked_id,
        )

        self.assertEqual(visual.linked_annotation_id, linked_id)
        self.assertEqual(
            self.service.list_visual_annotations(result.source_revision.id),
            (visual,),
        )
        self.assertEqual(
            before,
            hashlib.sha256(
                self.service.knowledge_db_path.read_bytes()
            ).hexdigest(),
        )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.service.add_visual_annotation(
                result.source_revision.id,
                image_content=_TINY_PNG,
                original_filename="other.png",
                media_type="image/png",
                caption="Other",
                alt_text="Other image.",
                linked_annotation_id=uuid4(),
            )

    def test_paper_detail_renders_visual_annotation_management(self) -> None:
        result = build_annotated_paper_result()
        self.service.register(result)
        self.service.add_visual_annotation(
            result.source_revision.id,
            image_content=_TINY_PNG,
            original_filename="explanation.png",
            media_type="image/png",
            caption="Efficient frontier explanation",
            alt_text="A risk-return curve.",
            review_status="attention",
            review_note="The publication year needs review.",
        )

        app = AppTest.from_function(
            _render_paper_detail,
            args=(self.service, str(result.source_revision.id)),
            default_timeout=20,
        ).run()

        self.assertEqual(list(app.exception), [])
        self.assertIn("画像注釈", [tab.label for tab in app.tabs])
        self.assertIn(
            "Efficient frontier explanation",
            [item.value for item in app.subheader],
        )
        self.assertIn(
            "要確認: The publication year needs review.",
            [item.value for item in app.warning],
        )
        self.assertIn(
            "画像注釈として保存",
            [item.label for item in app.button],
        )

    def test_translation_registration_review_and_ui(self) -> None:
        annotated = build_annotated_paper_result()
        translated = build_paper_translation_result()
        self.service.register(annotated)
        record = self.service.register_translation(translated)
        initial = self.service.state.get_translation_page_review(
            translated.translation.id,
            1,
        )
        reviewed = self.service.update_translation_page_review(
            translated.translation.id,
            1,
            expected_version=initial.version,
            review_status="verified",
            review_note="Compared with the source.",
        )

        self.assertEqual(reviewed.review_status, "verified")
        details = self.service.get_paper_details(translated.source_revision.id)
        self.assertEqual(details.translation_registrations, (record,))
        app = AppTest.from_function(
            _render_paper_detail,
            args=(self.service, str(translated.source_revision.id)),
            default_timeout=20,
        ).run()

        self.assertEqual(list(app.exception), [])
        self.assertIn("日本語訳", [tab.label for tab in app.tabs])
        self.assertTrue(
            any("翻訳済み 2/2 pages" in item.value for item in app.caption)
        )

    def test_upload_validation_work_root_and_close_are_bounded(self) -> None:
        workdir = self.service.create_intake_workdir()
        with self.assertRaisesRegex(ValueError, "not a PDF"):
            self.service.save_uploaded_pdf(workdir, b"not-pdf")
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.service.save_uploaded_pdf(outside, b"%PDF-valid")
        saved = self.service.save_draft(workdir, b'{"schema_version":"1"}')
        self.assertTrue(saved.exists())
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            self.service.save_draft(workdir, b'{"schema_version":"1"}')

        self.service.close()
        self.service.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.service.list_papers()

    def test_non_loopback_and_relative_paths_are_rejected(self) -> None:
        self.assertEqual(validate_loopback_address("localhost"), "127.0.0.1")
        self.assertEqual(validate_loopback_address("::1"), "::1")
        for address in ("0.0.0.0", "192.168.1.2", "example.com"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    validate_loopback_address(address)

        with self.assertRaisesRegex(ValueError, "absolute"):
            PaperLibraryAppService(
                knowledge_db_path="relative.sqlite3",
                sidecar_db_path=self.root / "another-sidecar.sqlite3",
                model_cache_path=self.root / "cache",
                intake_work_root=self.root / "work",
            )


if __name__ == "__main__":
    unittest.main()
