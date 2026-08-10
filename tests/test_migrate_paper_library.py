import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from apps.paper_library.service import PaperLibraryAppService
from quantmind.library import LocalKnowledgeLibrary
from scripts.migrate_paper_library import (
    MigrationError,
    inspect_database,
    migrate_database_pair,
    verify_migrated_library,
    write_acceptance_report,
)
from tests.library.test_paper import _FakeEmbeddingProvider
from tests.paper_helpers import (
    build_annotated_paper_result,
    build_paper_translation_result,
)


def _tiny_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 1), "navy").save(output, format="PNG")
    return output.getvalue()


class PaperLibraryMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.canonical = self.root / "source-library.sqlite3"
        self.sidecar = self.root / "source-ui.sqlite3"
        provider = _FakeEmbeddingProvider()

        async def opener() -> LocalKnowledgeLibrary:
            return await LocalKnowledgeLibrary.open(
                self.canonical,
                embedding_model="fake-2d",
                embedding_dimensions=2,
                _embedding_provider=provider,
            )

        service = PaperLibraryAppService(
            knowledge_db_path=self.canonical,
            sidecar_db_path=self.sidecar,
            model_cache_path=self.root / "model-cache",
            intake_work_root=self.root / "intake",
            _library_opener=opener,
        )
        result = build_annotated_paper_result()
        service.register(result)
        translation = build_paper_translation_result()
        service.register_translation(translation)
        state = service.state.get_state(result.source_revision.id)
        service.state.update_state(
            result.source_revision.id,
            expected_version=state.version,
            display_title="Personal title",
            display_authors=("Personal Author",),
            display_publication="Mar. 1952",
            reading_status="reading",
            starred=True,
            personal_memo="Keep this personal note.",
            last_opened_page=2,
            page_count=2,
        )
        service.state.set_tags(result.source_revision.id, ("portfolio",))
        collection = service.state.create_collection(
            "Foundations", "Foundational papers"
        )
        service.state.set_collections(
            result.source_revision.id, (collection.collection_id,)
        )
        service.add_visual_annotation(
            result.source_revision.id,
            image_content=_tiny_png(),
            original_filename="explanation.png",
            media_type="image/png",
            caption="Efficient frontier",
            alt_text="A compact explanatory figure.",
            linked_annotation_id=result.annotation_set.annotations[
                0
            ].annotation_id,
        )
        review = service.state.get_translation_page_review(
            translation.translation.id, 1
        )
        service.update_translation_page_review(
            translation.translation.id,
            1,
            expected_version=review.version,
            review_status="verified",
            review_note="Compared with the source.",
        )
        service.close()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_migrates_every_canonical_and_sidecar_value(self) -> None:
        canonical_before = inspect_database(self.canonical)
        sidecar_before = inspect_database(self.sidecar)
        destination = self.root / "migrated"

        actual, manifest = migrate_database_pair(
            source_library_db=self.canonical,
            source_ui_db=self.sidecar,
            destination_root=destination,
        )

        self.assertEqual(actual, destination.resolve())
        self.assertEqual(
            inspect_database(
                destination / "paper-library.sqlite3"
            ).manifest_value(),
            canonical_before.manifest_value(),
        )
        self.assertEqual(
            inspect_database(
                destination / "paper-library-ui.sqlite3"
            ).manifest_value(),
            sidecar_before.manifest_value(),
        )
        self.assertEqual(
            manifest["canonical"]["logical_sha256"],
            canonical_before.logical_sha256,
        )
        self.assertEqual(
            manifest["sidecar"]["logical_sha256"],
            sidecar_before.logical_sha256,
        )
        self.assertTrue((destination / "migration-manifest.json").is_file())
        self.assertTrue((destination / "intake").is_dir())

    def test_refuses_an_existing_destination_without_overwriting(self) -> None:
        destination = self.root / "existing"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("unchanged", encoding="utf-8")

        with self.assertRaisesRegex(MigrationError, "must not already exist"):
            migrate_database_pair(
                source_library_db=self.canonical,
                source_ui_db=self.sidecar,
                destination_root=destination,
            )

        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_public_api_acceptance_reopens_assets_and_search(self) -> None:
        destination, manifest = migrate_database_pair(
            source_library_db=self.canonical,
            source_ui_db=self.sidecar,
            destination_root=self.root / "accepted",
        )
        model_cache = self.root / "accepted-model-cache"
        model_cache.mkdir()
        provider = _FakeEmbeddingProvider()

        async def open_fake(
            database: str | Path, *, cache_dir: str | Path
        ) -> LocalKnowledgeLibrary:
            self.assertEqual(Path(cache_dir), model_cache.resolve())
            return await LocalKnowledgeLibrary.open(
                database,
                embedding_model="fake-2d",
                embedding_dimensions=2,
                _embedding_provider=provider,
            )

        with patch.object(
            LocalKnowledgeLibrary,
            "open_local",
            side_effect=open_fake,
        ):
            operational = verify_migrated_library(
                destination,
                model_cache=model_cache,
                queries=("Transformer architecture",),
            )
        report = write_acceptance_report(
            destination,
            manifest=manifest,
            operational=operational,
        )

        self.assertEqual(operational["status"], "passed")
        self.assertEqual(operational["source_revision_count"], 1)
        self.assertEqual(operational["translation_count"], 1)
        self.assertEqual(operational["visual_annotation_count"], 1)
        self.assertGreater(
            operational["search_hit_counts"]["Transformer architecture"],
            0,
        )
        self.assertTrue(report.is_file())

    def test_detects_a_source_write_and_removes_partial_destination(
        self,
    ) -> None:
        from scripts import migrate_paper_library

        destination = self.root / "unstable"
        original_backup = migrate_paper_library._backup_database
        call_count = 0

        def backup_then_modify(source: Path, target: Path) -> None:
            nonlocal call_count
            original_backup(source, target)
            call_count += 1
            if call_count == 1:
                connection = sqlite3.connect(self.canonical)
                try:
                    connection.execute(
                        """
                        UPDATE paper_catalog
                        SET title = COALESCE(title, '') || ' changed'
                        """
                    )
                    connection.commit()
                finally:
                    connection.close()

        with patch.object(
            migrate_paper_library,
            "_backup_database",
            side_effect=backup_then_modify,
        ):
            with self.assertRaisesRegex(MigrationError, "changed during"):
                migrate_database_pair(
                    source_library_db=self.canonical,
                    source_ui_db=self.sidecar,
                    destination_root=destination,
                )

        self.assertFalse(destination.exists())
        self.assertEqual(
            list(self.root.glob(".unstable.staging-*")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
