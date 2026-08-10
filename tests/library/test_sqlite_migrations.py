"""Schema migrations for paper audit and catalog tables."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from quantmind.library import LocalKnowledgeLibrary
from tests.library.test_paper import _FakeEmbeddingProvider
from tests.paper_helpers import build_paper_result


class SQLiteV6MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_v5_database_backfills_catalog_without_changing_paper(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v5.db"
            result = build_paper_result()
            library = await LocalKnowledgeLibrary.open(
                path,
                embedding_model="fake-2d",
                embedding_dimensions=2,
                _embedding_provider=_FakeEmbeddingProvider(),
            )
            await library.put_paper(result)
            await library.close()

            with sqlite3.connect(path) as db:
                source_hash = db.execute(
                    """
                    SELECT canonical_hash FROM paper_sources
                    WHERE source_revision_id = ?
                    """,
                    (str(result.source_revision.id),),
                ).fetchone()[0]
                db.executescript(
                    """
                    DROP TABLE paper_catalog;
                    DROP TABLE paper_registration_records;
                    PRAGMA user_version = 5;
                    """
                )

            library = await LocalKnowledgeLibrary.open(
                path,
                embedding_model="fake-2d",
                embedding_dimensions=2,
                _embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                restored = await library.get_paper(result.source_revision.id)
                page = await library.list_papers()
            finally:
                await library.close()

            self.assertEqual(restored, result)
            self.assertEqual(page.total_count, 1)
            self.assertEqual(
                page.entries[0].source_revision_id,
                result.source_revision.id,
            )
            self.assertEqual(page.entries[0].health, "broken")
            with sqlite3.connect(path) as db:
                self.assertEqual(
                    db.execute("PRAGMA user_version").fetchone()[0], 7
                )
                self.assertEqual(
                    db.execute(
                        """
                        SELECT canonical_hash FROM paper_sources
                        WHERE source_revision_id = ?
                        """,
                        (str(result.source_revision.id),),
                    ).fetchone()[0],
                    source_hash,
                )
                self.assertIsNone(
                    db.execute("PRAGMA foreign_key_check").fetchone()
                )


if __name__ == "__main__":
    unittest.main()
