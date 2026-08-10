#!/usr/bin/env python3
"""Migrate and verify one complete Codex Paper Library database pair."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_CANONICAL_NAME = "paper-library.sqlite3"
_SIDECAR_NAME = "paper-library-ui.sqlite3"
_MANIFEST_NAME = "migration-manifest.json"
_ACCEPTANCE_NAME = "migration-acceptance.json"
_MANIFEST_SCHEMA_VERSION = "1.0"
_CANONICAL_TABLES = frozenset(
    {
        "paper_sources",
        "paper_source_assets",
        "paper_artifacts",
        "paper_registration_records",
        "semantic_records",
    }
)
_SIDECAR_TABLES = frozenset(
    {
        "paper_user_state",
        "translation_page_reviews",
        "ui_meta",
        "visual_annotations",
    }
)


class MigrationError(RuntimeError):
    """Raised when a database pair cannot be migrated safely."""


@dataclass(frozen=True)
class DatabaseInventory:
    """Deterministic logical inventory of one SQLite database."""

    path: Path
    user_version: int
    schema_sha256: str
    logical_sha256: str
    table_count: int
    row_count: int
    tables: dict[str, dict[str, Any]]

    def manifest_value(self) -> dict[str, Any]:
        """Return the portable subset written to the migration manifest."""
        return {
            "user_version": self.user_version,
            "schema_sha256": self.schema_sha256,
            "logical_sha256": self.logical_sha256,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "tables": self.tables,
        }


def _hash_parts(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _cell_bytes(value: object) -> bytes:
    if value is None:
        return b"null"
    if isinstance(value, bytes):
        return b"blob:" + value
    if isinstance(value, str):
        return b"text:" + value.encode("utf-8")
    if isinstance(value, int):
        return b"integer:" + str(value).encode("ascii")
    if isinstance(value, float):
        return b"real:" + value.hex().encode("ascii")
    raise MigrationError(f"unsupported SQLite value type: {type(value)!r}")


def _row_hash(row: Sequence[object]) -> str:
    return _hash_parts(_cell_bytes(value) for value in row)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _database_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return tuple(
        (str(row["type"]), str(row["name"]), str(row["tbl_name"]), row["sql"])
        for row in rows
    )


def _table_inventory(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, Any]:
    columns = tuple(
        str(row[1])
        for row in connection.execute(
            f"PRAGMA table_info({_quoted_identifier(table_name)})"
        ).fetchall()
    )
    if not columns:
        raise MigrationError(f"table has no columns: {table_name}")
    projection = ", ".join(_quoted_identifier(column) for column in columns)
    cursor = connection.execute(
        f"SELECT {projection} FROM {_quoted_identifier(table_name)}"
    )
    row_hashes: list[str] = []
    while rows := cursor.fetchmany(1_000):
        row_hashes.extend(_row_hash(tuple(row)) for row in rows)
    row_hashes.sort()
    return {
        "columns": list(columns),
        "row_count": len(row_hashes),
        "content_sha256": _hash_parts(
            value.encode("ascii") for value in row_hashes
        ),
    }


def inspect_database(path: Path) -> DatabaseInventory:
    """Validate SQLite integrity and inventory every schema object and cell."""
    connection = _open_read_only(path)
    try:
        integrity = tuple(
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        )
        if integrity != ("ok",):
            raise MigrationError(
                f"SQLite integrity check failed for {path}: {integrity}"
            )
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise MigrationError(
                f"SQLite foreign-key check failed for {path}: "
                f"{len(foreign_keys)} violation(s)"
            )
        objects = _database_objects(connection)
        schema_sha256 = _hash_parts(
            _hash_parts(part.encode("utf-8") for part in value).encode("ascii")
            for value in objects
        )
        table_names = tuple(
            value[1] for value in objects if value[0] == "table"
        )
        tables = {
            name: _table_inventory(connection, name) for name in table_names
        }
        user_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        logical_sha256 = _hash_parts(
            (
                str(user_version).encode("ascii"),
                schema_sha256.encode("ascii"),
                *(
                    _hash_parts(
                        (
                            name.encode("utf-8"),
                            json.dumps(
                                tables[name],
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode("utf-8"),
                        )
                    ).encode("ascii")
                    for name in sorted(tables)
                ),
            )
        )
        return DatabaseInventory(
            path=path,
            user_version=user_version,
            schema_sha256=schema_sha256,
            logical_sha256=logical_sha256,
            table_count=len(tables),
            row_count=sum(int(value["row_count"]) for value in tables.values()),
            tables=tables,
        )
    except sqlite3.DatabaseError as exc:
        raise MigrationError(f"invalid SQLite database {path}: {exc}") from exc
    finally:
        connection.close()


def _validate_role(
    inventory: DatabaseInventory,
    *,
    role: str,
    required_tables: frozenset[str],
) -> None:
    missing = sorted(required_tables - inventory.tables.keys())
    if missing:
        raise MigrationError(
            f"{role} database is missing required tables: {', '.join(missing)}"
        )


def _source_path(value: Path, name: str) -> Path:
    candidate = value.expanduser()
    if not candidate.is_absolute():
        raise MigrationError(f"{name} must be an absolute path")
    if candidate.is_symlink():
        raise MigrationError(f"{name} must not be a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise MigrationError(f"{name} does not exist: {candidate}") from exc
    if not resolved.is_file():
        raise MigrationError(f"{name} is not a regular file: {resolved}")
    return resolved


def _destination_path(value: Path) -> Path:
    candidate = value.expanduser()
    if not candidate.is_absolute():
        raise MigrationError("destination root must be an absolute path")
    if candidate.is_symlink() or candidate.exists():
        raise MigrationError("destination root must not already exist")
    parent = candidate.parent.resolve(strict=True)
    if parent.is_symlink():
        raise MigrationError("destination parent must not be a symbolic link")
    return parent / candidate.name


def _backup_database(source_path: Path, destination_path: Path) -> None:
    source = _open_read_only(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    except sqlite3.DatabaseError as exc:
        raise MigrationError(
            f"SQLite backup failed for {source_path}: {exc}"
        ) from exc
    finally:
        destination.close()
        source.close()


def _assert_unchanged(
    before: DatabaseInventory,
    after: DatabaseInventory,
    *,
    name: str,
) -> None:
    if before.logical_sha256 != after.logical_sha256:
        raise MigrationError(
            f"{name} changed during migration; stop every writer and retry"
        )


def _assert_equal(
    source: DatabaseInventory,
    destination: DatabaseInventory,
    *,
    name: str,
) -> None:
    if source.manifest_value() != destination.manifest_value():
        raise MigrationError(f"{name} logical inventory mismatch after backup")


def migrate_database_pair(
    *,
    source_library_db: Path,
    source_ui_db: Path,
    destination_root: Path,
) -> tuple[Path, dict[str, Any]]:
    """Migrate a stable canonical/sidecar pair into a new atomic directory."""
    canonical_source = _source_path(source_library_db, "source library DB")
    sidecar_source = _source_path(source_ui_db, "source UI DB")
    if canonical_source == sidecar_source:
        raise MigrationError("source library DB and source UI DB must differ")
    destination = _destination_path(destination_root)

    canonical_before = inspect_database(canonical_source)
    sidecar_before = inspect_database(sidecar_source)
    _validate_role(
        canonical_before,
        role="canonical",
        required_tables=_CANONICAL_TABLES,
    )
    _validate_role(
        sidecar_before,
        role="sidecar",
        required_tables=_SIDECAR_TABLES,
    )

    staging = destination.parent / (
        f".{destination.name}.staging-{uuid4().hex}"
    )
    try:
        staging.mkdir(mode=0o700)
        canonical_destination = staging / _CANONICAL_NAME
        sidecar_destination = staging / _SIDECAR_NAME
        _backup_database(canonical_source, canonical_destination)
        _backup_database(sidecar_source, sidecar_destination)

        canonical_after = inspect_database(canonical_source)
        sidecar_after = inspect_database(sidecar_source)
        _assert_unchanged(
            canonical_before,
            canonical_after,
            name="source library DB",
        )
        _assert_unchanged(
            sidecar_before,
            sidecar_after,
            name="source UI DB",
        )

        canonical_copy = inspect_database(canonical_destination)
        sidecar_copy = inspect_database(sidecar_destination)
        _assert_equal(
            canonical_before,
            canonical_copy,
            name="canonical database",
        )
        _assert_equal(
            sidecar_before,
            sidecar_copy,
            name="sidecar database",
        )

        (staging / "intake").mkdir(mode=0o700)
        manifest: dict[str, Any] = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "verified",
            "source": {
                "library_db": str(canonical_source),
                "ui_db": str(sidecar_source),
            },
            "destination": {
                "root": str(destination),
                "library_db": _CANONICAL_NAME,
                "ui_db": _SIDECAR_NAME,
            },
            "canonical": canonical_copy.manifest_value(),
            "sidecar": sidecar_copy.manifest_value(),
            "checks": [
                "source_integrity",
                "source_foreign_keys",
                "source_stability",
                "destination_integrity",
                "destination_foreign_keys",
                "schema_equality",
                "all_row_and_blob_equality",
            ],
        }
        (staging / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
        return destination, manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _translation_review_rows(
    sidecar_path: Path,
) -> tuple[tuple[str, int], ...]:
    connection = _open_read_only(sidecar_path)
    try:
        return tuple(
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT translation_id, page_number
                FROM translation_page_reviews
                ORDER BY translation_id, page_number
                """
            ).fetchall()
        )
    finally:
        connection.close()


def verify_migrated_library(
    destination_root: Path,
    *,
    model_cache: Path,
    queries: Sequence[str] = (),
) -> dict[str, Any]:
    """Reopen every migrated value through public APIs and verify searches."""
    from apps.paper_library.service import PaperLibraryAppService
    from quantmind.library import SemanticQuery

    root = destination_root.resolve(strict=True)
    cache = model_cache.expanduser().resolve(strict=True)
    service = PaperLibraryAppService(
        knowledge_db_path=root / _CANONICAL_NAME,
        sidecar_db_path=root / _SIDECAR_NAME,
        model_cache_path=cache,
        intake_work_root=root / "intake",
    )
    try:
        stats = service.inspect_library()
        source_ids = service.list_source_ids()
        if stats.source_revision_count != len(source_ids):
            raise MigrationError("public catalog source count mismatch")
        known_sources = set(source_ids)
        known_translation_pages: dict[str, set[int]] = {}
        asset_count = 0
        visual_count = 0
        for source_id in source_ids:
            details = service.get_paper_details(source_id)
            if details.health == "broken":
                raise MigrationError(
                    f"migrated paper is broken: {source_id}: "
                    f"{', '.join(details.health_reasons)}"
                )
            source = details.source
            raw_asset = service.get_paper_asset(source.id, source.raw_asset_id)
            asset_count += 1
            raw_hash = hashlib.sha256(raw_asset.content).hexdigest()
            if (
                raw_hash != source.source.content_hash
                or raw_hash != raw_asset.content_hash
            ):
                raise MigrationError(f"raw PDF hash mismatch: {source.id}")

            expected_pages = tuple(
                page.page_number for page in source.parsed.pages
            )
            annotation_ids = {
                annotation.annotation_id
                for annotation_set in details.annotation_sets
                for annotation in annotation_set.annotations
            }
            for translation in details.translations:
                actual_pages = tuple(
                    page.page_number for page in translation.pages
                )
                if actual_pages != expected_pages:
                    raise MigrationError(
                        f"translation page coverage mismatch: {translation.id}"
                    )
                known_translation_pages[str(translation.id)] = set(actual_pages)

            for visual in service.list_visual_annotations(source.id):
                visual_count += 1
                if hashlib.sha256(visual.image_content).hexdigest() != (
                    visual.content_hash
                ):
                    raise MigrationError(
                        f"visual annotation hash mismatch: "
                        f"{visual.visual_annotation_id}"
                    )
                if len(visual.image_content) != visual.byte_size:
                    raise MigrationError(
                        f"visual annotation size mismatch: "
                        f"{visual.visual_annotation_id}"
                    )
                if (
                    visual.linked_annotation_id is not None
                    and visual.linked_annotation_id not in annotation_ids
                ):
                    raise MigrationError(
                        f"visual annotation link is unresolved: "
                        f"{visual.visual_annotation_id}"
                    )

        orphans = service.state.orphaned_source_ids(known_sources)
        if orphans:
            raise MigrationError(
                "sidecar contains unknown source IDs: "
                + ", ".join(str(value) for value in orphans)
            )
        for translation_id, page_number in _translation_review_rows(
            root / _SIDECAR_NAME
        ):
            if page_number not in known_translation_pages.get(
                translation_id, set()
            ):
                raise MigrationError(
                    "translation review is unresolved: "
                    f"{translation_id} page {page_number}"
                )

        search_results: dict[str, int] = {}
        for query in queries:
            normalized = query.strip()
            if not normalized:
                raise MigrationError("acceptance query must not be blank")
            hits = service.search(SemanticQuery(text=normalized, top_k=5))
            if not hits:
                raise MigrationError(
                    f"semantic acceptance query returned no hits: {normalized}"
                )
            if any(
                hit.locator.source_revision_id not in known_sources
                for hit in hits
            ):
                raise MigrationError(
                    f"semantic query returned an unresolved source: {normalized}"
                )
            search_results[normalized] = len(hits)

        sidecar_stats = service.state.inspect()
        return {
            "status": "passed",
            "source_revision_count": len(source_ids),
            "raw_asset_count": asset_count,
            "translation_count": stats.total_translations,
            "visual_annotation_count": visual_count,
            "sidecar_state_count": sidecar_stats.state_count,
            "search_hit_counts": search_results,
        }
    finally:
        service.close()


def write_acceptance_report(
    destination_root: Path,
    *,
    manifest: dict[str, Any],
    operational: dict[str, Any],
) -> Path:
    """Persist the completed logical and public-API acceptance evidence."""
    root = destination_root.resolve(strict=True)
    report_path = root / _ACCEPTANCE_NAME
    if report_path.exists() or report_path.is_symlink():
        raise MigrationError("migration acceptance report already exists")
    report = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "canonical_logical_sha256": manifest["canonical"]["logical_sha256"],
        "sidecar_logical_sha256": manifest["sidecar"]["logical_sha256"],
        "operational": operational,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate the complete canonical and UI-sidecar SQLite pair into "
            "a new verified runtime directory."
        )
    )
    parser.add_argument("--source-library-db", required=True, type=Path)
    parser.add_argument("--source-ui-db", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument(
        "--model-cache",
        type=Path,
        help="Run public-API acceptance using this fixed local model cache.",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Require a semantic hit after migration; may be repeated.",
    )
    return parser


def main() -> int:
    """Run the non-overwriting migration and optional operational acceptance."""
    args = _parser().parse_args()
    if args.query and args.model_cache is None:
        raise SystemExit("--query requires --model-cache")
    try:
        destination, manifest = migrate_database_pair(
            source_library_db=args.source_library_db,
            source_ui_db=args.source_ui_db,
            destination_root=args.destination_root,
        )
        print(
            "PASS: canonical DB migrated with logical digest "
            f"{manifest['canonical']['logical_sha256']}"
        )
        print(
            "PASS: sidecar DB migrated with logical digest "
            f"{manifest['sidecar']['logical_sha256']}"
        )
        print(f"PASS: verified pair published at {destination}")
        if args.model_cache is not None:
            operational = verify_migrated_library(
                destination,
                model_cache=args.model_cache,
                queries=args.query,
            )
            report_path = write_acceptance_report(
                destination,
                manifest=manifest,
                operational=operational,
            )
            print(
                "PASS: public APIs reopened "
                f"{operational['source_revision_count']} paper(s), "
                f"{operational['translation_count']} translation(s), and "
                f"{operational['visual_annotation_count']} visual annotation(s)"
            )
            for query, count in operational["search_hit_counts"].items():
                print(f"PASS: semantic query returned {count} hit(s): {query}")
            print(f"PASS: acceptance report written to {report_path}")
    except (MigrationError, FileNotFoundError, OSError) as exc:
        raise SystemExit(f"Migration failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
