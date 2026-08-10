"""Atomic registration and catalog tests for annotated paper bundles."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from quantmind.knowledge import PaperAnnotation
from quantmind.library import (
    LocalKnowledgeLibrary,
    PaperCatalogQuery,
    SemanticQuery,
)
from tests.library.test_paper import (
    _FailingEmbeddingProvider,
    _FakeEmbeddingProvider,
)
from tests.paper_helpers import build_annotated_paper_result


class AnnotatedPaperLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "annotated.db"

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def _open(self, provider=None) -> LocalKnowledgeLibrary:
        return await LocalKnowledgeLibrary.open(
            self.db_path,
            embedding_model="fake-2d",
            embedding_dimensions=2,
            _embedding_provider=provider or _FakeEmbeddingProvider(),
        )

    async def test_atomic_registration_round_trips_all_layers(self) -> None:
        result = build_annotated_paper_result()
        provider = _FakeEmbeddingProvider()
        library = await self._open(provider)
        registration = await library.put_annotated_paper(result)
        await library.close()

        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(provider.calls[0]), 4)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM paper_artifacts").fetchone()[
                    0
                ],
                3,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM paper_artifact_members"
                ).fetchone()[0],
                5,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM paper_projections").fetchone()[
                    0
                ],
                4,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM paper_registration_records"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM paper_catalog").fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    """
                    SELECT target_count FROM paper_artifacts
                    WHERE artifact_kind = 'paper_annotation_set'
                    """
                ).fetchone()[0],
                0,
            )

        library = await self._open()
        try:
            restored = await library.get_annotated_paper(
                registration.registration_id
            )
            self.assertEqual(restored, result)
            self.assertEqual(
                await library.get_registration(registration.registration_id),
                registration,
            )
            annotation = await library.resolve(
                result.annotation_set.derived_from[0].model_copy(
                    update={
                        "artifact_id": result.annotation_set.id,
                        "artifact_kind": result.annotation_set.artifact_kind,
                        "member_id": result.annotation_set.annotations[
                            0
                        ].annotation_id,
                    }
                )
            )
            self.assertIsInstance(annotation, PaperAnnotation)
        finally:
            await library.close()

    async def test_same_bundle_is_idempotent_and_reuses_vectors(self) -> None:
        result = build_annotated_paper_result()
        provider = _FakeEmbeddingProvider()
        library = await self._open(provider)
        try:
            first = await library.put_annotated_paper(result)
            second = await library.put_annotated_paper(result)
        finally:
            await library.close()

        self.assertEqual(first, second)
        self.assertEqual(len(provider.calls), 1)

    async def test_changed_draft_reuses_source_chunks_and_adds_audit(
        self,
    ) -> None:
        first = build_annotated_paper_result(draft_marker="first")
        second = build_annotated_paper_result(draft_marker="second")
        library = await self._open()
        try:
            first_record = await library.put_annotated_paper(first)
            second_record = await library.put_annotated_paper(second)
        finally:
            await library.close()

        self.assertNotEqual(
            first_record.registration_id, second_record.registration_id
        )
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    """
                    SELECT COUNT(*) FROM paper_artifacts
                    WHERE artifact_kind = 'paper_chunk_set'
                    """
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM paper_registration_records"
                ).fetchone()[0],
                2,
            )

    async def test_embedding_failure_leaves_every_bundle_table_empty(
        self,
    ) -> None:
        library = await self._open(_FailingEmbeddingProvider())
        try:
            with self.assertRaisesRegex(RuntimeError, "embedding unavailable"):
                await library.put_annotated_paper(
                    build_annotated_paper_result()
                )
        finally:
            await library.close()

        with sqlite3.connect(self.db_path) as db:
            for table in (
                "paper_sources",
                "paper_source_assets",
                "paper_artifacts",
                "paper_artifact_members",
                "paper_projections",
                "paper_registration_records",
                "paper_catalog",
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                            0
                        ],
                        0,
                    )

    async def test_catalog_details_assets_stats_and_source_filter(self) -> None:
        result = build_annotated_paper_result()
        provider = _FakeEmbeddingProvider()
        library = await self._open(provider)
        try:
            registration = await library.put_annotated_paper(result)
            page = await library.list_papers(
                PaperCatalogQuery(text="Attention")
            )
            self.assertEqual(page.total_count, 1)
            entry = page.entries[0]
            self.assertEqual(entry.health, "ready")
            self.assertEqual(entry.annotation_count, 2)
            self.assertEqual(entry.registration_count, 1)
            self.assertEqual(entry.embedding_model, "fake-2d")

            details = await library.get_paper_details(
                result.source_revision.id,
                registration_id=registration.registration_id,
            )
            self.assertEqual(details.source, result.source_revision)
            self.assertEqual(details.annotation_sets, (result.annotation_set,))
            asset = await library.get_paper_asset(
                result.source_revision.id,
                result.source_revision.raw_asset_id,
            )
            self.assertEqual(
                asset.content,
                result.source_revision.blob_for(
                    result.source_revision.raw_asset_id
                ),
            )
            stats = await library.inspect_library()
            self.assertEqual(stats.source_revision_count, 1)
            self.assertEqual(stats.search_ready_count, 1)

            self.assertEqual(
                await library.find_paper_source(
                    result.source_revision.source.content_hash or ""
                ),
                result.source_revision.id,
            )
            no_hits = await library.search(
                SemanticQuery(
                    text="attention",
                    source_revision_ids=(),
                )
            )
            self.assertEqual(no_hits, [])
            self.assertEqual(provider.calls[-1], provider.calls[0])
        finally:
            await library.close()

    async def test_catalog_cursor_is_query_bound(self) -> None:
        first = build_annotated_paper_result(draft_marker="first")
        library = await self._open()
        try:
            await library.put_annotated_paper(first)
            page = await library.list_papers(PaperCatalogQuery(limit=1))
            self.assertIsNone(page.next_cursor)
            with self.assertRaisesRegex(ValueError, "cursor"):
                await library.list_papers(
                    PaperCatalogQuery(
                        text="changed",
                        cursor="not-a-cursor",
                    )
                )
        finally:
            await library.close()


if __name__ == "__main__":
    unittest.main()
