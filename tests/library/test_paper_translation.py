"""Persistence and audit tests for page-aligned paper translations."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from quantmind.knowledge import ArtifactLocator, PaperTranslationPage
from quantmind.library import LocalKnowledgeLibrary
from tests.library.test_paper import _FakeEmbeddingProvider
from tests.paper_helpers import (
    build_annotated_paper_result,
    build_paper_translation_result,
)


class PaperTranslationLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.path = Path(self._directory.name) / "translation.sqlite3"
        self.provider = _FakeEmbeddingProvider()

    def tearDown(self) -> None:
        self._directory.cleanup()

    async def _open(self) -> LocalKnowledgeLibrary:
        return await LocalKnowledgeLibrary.open(
            self.path,
            embedding_model="fake-2d",
            embedding_dimensions=2,
            _embedding_provider=self.provider,
        )

    async def test_atomic_round_trip_uses_no_translation_embeddings(
        self,
    ) -> None:
        annotated = build_annotated_paper_result()
        translated = build_paper_translation_result()
        library = await self._open()
        await library.put_annotated_paper(annotated)
        calls_before = len(self.provider.calls)
        first = await library.put_translation(translated)
        second = await library.put_translation(translated)
        self.assertEqual(first, second)
        self.assertEqual(len(self.provider.calls), calls_before)

        restored = await library.open_translation(translated.translation.id)
        details = await library.get_paper_details(translated.source_revision.id)
        page = await library.resolve(
            ArtifactLocator(
                source_revision_id=translated.source_revision.id,
                artifact_id=translated.translation.id,
                artifact_kind=translated.translation.artifact_kind,
                member_id=translated.translation.pages[0].page_id,
            )
        )
        catalog = await library.list_papers()
        stats = await library.inspect_library()
        await library.close()

        self.assertEqual(restored, translated.translation)
        self.assertEqual(details.translations, (translated.translation,))
        self.assertEqual(details.translation_registrations, (first,))
        self.assertIsInstance(page, PaperTranslationPage)
        self.assertEqual(catalog.entries[0].translation_count, 1)
        self.assertEqual(stats.total_translations, 1)
        with sqlite3.connect(self.path) as db:
            artifact = db.execute(
                """
                SELECT member_count, target_count FROM paper_artifacts
                WHERE artifact_id = ?
                """,
                (str(translated.translation.id),),
            ).fetchone()
            self.assertEqual(artifact, (2, 0))
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM paper_translation_registration_records"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    """
                    SELECT COUNT(*) FROM paper_projections
                    WHERE artifact_id = ?
                    """,
                    (str(translated.translation.id),),
                ).fetchone()[0],
                0,
            )

    async def test_changed_draft_adds_version_without_replacing_source(
        self,
    ) -> None:
        first = build_paper_translation_result(draft_marker="first")
        changed = build_paper_translation_result(draft_marker="changed")
        library = await self._open()
        first_record = await library.put_translation(first)
        changed_record = await library.put_translation(changed)
        registrations = await library.list_translation_registrations(
            first.source_revision.id
        )
        await library.close()

        self.assertNotEqual(
            first_record.registration_id,
            changed_record.registration_id,
        )
        self.assertEqual(len(registrations), 2)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    """
                    SELECT COUNT(*) FROM paper_artifacts
                    WHERE artifact_kind = 'paper_translation'
                    """
                ).fetchone()[0],
                2,
            )


if __name__ == "__main__":
    unittest.main()
