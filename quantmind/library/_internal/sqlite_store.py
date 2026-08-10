"""Concrete SQLite persistence for canonical knowledge and index records."""

import hashlib
import json
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from quantmind.knowledge import (
    ArtifactLocator,
    BaseKnowledge,
    Citation,
    Earnings,
    Factor,
    LegacyPaper,
    News,
    PaperAnnotatedResult,
    PaperAnnotationSet,
    PaperArtifact,
    PaperChunkSet,
    PaperGlobalSummary,
    PaperSemanticResult,
    PaperSourceRevision,
    PaperStructureTree,
    PaperTranslatedResult,
    PaperTranslation,
    ResolvedPaperArtifact,
    Thesis,
    TreeKnowledge,
)
from quantmind.knowledge.paper import _validate_chunk_set_source
from quantmind.library._internal.llamaindex_retriever import _IndexRecord
from quantmind.library._internal.retrieval_targets import (
    _PROJECTION_SCHEMA_VERSION,
    _RetrievalTarget,
)
from quantmind.library._types import (
    PaperAssetPayload,
    PaperCatalogEntry,
    PaperCatalogPage,
    PaperCatalogQuery,
    PaperDetails,
    PaperLibraryStats,
    PaperRegistrationRecord,
    PaperTranslationRegistrationRecord,
    _registration_content_hash,
    _translation_registration_content_hash,
)

_DATABASE_SCHEMA_VERSION = 7

_REGISTRATION_POLICY_VERSION = "paper-registration-v1"
_REGISTRATION_CHECKS = (
    "source_hash",
    "asset_integrity",
    "page_sequence",
    "chunk_spans",
    "summary_citations",
    "annotation_citations",
    "embedding_vectors",
    "sqlite_constraints",
)

_TRANSLATION_REGISTRATION_POLICY_VERSION = "paper-translation-registration-v1"
_TRANSLATION_REGISTRATION_CHECKS = (
    "source_hash",
    "page_sequence",
    "source_page_text",
    "complete_translation",
    "sqlite_constraints",
)

_KNOWLEDGE_CLASSES: dict[str, type[BaseKnowledge]] = {
    f"{knowledge_type.__module__}:{knowledge_type.__qualname__}": knowledge_type
    for knowledge_type in (
        Earnings,
        Factor,
        News,
        LegacyPaper,
        Thesis,
    )
}
_KNOWLEDGE_CLASSES["quantmind.knowledge.paper:Paper"] = LegacyPaper


@dataclass(frozen=True)
class _CanonicalNode:
    """One normalized canonical tree node."""

    node_id: UUID
    parent_id: UUID | None
    position: int
    payload: str
    content_hash: str


@dataclass(frozen=True)
class _CanonicalDocument:
    """Canonical aggregate root plus separately persisted tree nodes."""

    knowledge_class: str
    item_shape: str
    payload: str
    canonical_hash: str
    nodes: tuple[_CanonicalNode, ...]


@dataclass(frozen=True)
class _StoredEmbedding:
    """Existing vector and invalidation metadata for one target."""

    target_id: str
    embedding_model: str
    dimension: int
    projection_hash: str
    source_content_hash: str | None
    knowledge_schema_version: str
    projection_schema_version: str
    embedding: bytes


@dataclass(frozen=True)
class _PreparedPut:
    """Validated canonical write plus the vectors it may retain."""

    item: BaseKnowledge
    canonical: _CanonicalDocument
    as_of: float
    available_at: float | None
    tags_json: str
    existing_embeddings: dict[str, _StoredEmbedding]


@dataclass(frozen=True)
class _CanonicalPaperMember:
    """One separately addressable paper-artifact member."""

    member_id: UUID
    parent_id: UUID | None
    position: int
    payload: str
    content_hash: str


@dataclass(frozen=True)
class _CanonicalPaperArtifact:
    """One aggregate artifact plus normalized member rows."""

    artifact: PaperArtifact
    payload: str
    canonical_hash: str
    members: tuple[_CanonicalPaperMember, ...]


@dataclass(frozen=True)
class _PreparedPaperPut:
    """Validated source/artifact write plus reusable search projections."""

    result: PaperSemanticResult | PaperAnnotatedResult
    source_payload: str
    source_canonical_hash: str
    artifacts: tuple[_CanonicalPaperArtifact, ...]
    existing_embeddings: dict[str, _StoredEmbedding]


@dataclass(frozen=True)
class _PreparedStructureTreePut:
    """Validated self-contained structure-tree write with no source revision."""

    tree: PaperStructureTree
    canonical: _CanonicalPaperArtifact


@dataclass(frozen=True)
class _PreparedTranslationPut:
    """Validated source/translation write requiring no vector projection."""

    result: PaperTranslatedResult
    source_payload: str
    source_canonical_hash: str
    canonical: _CanonicalPaperArtifact


def _json_payload(value: object) -> str:
    """Encode canonical JSON with stable ordering and separators."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_payload(item: BaseKnowledge) -> _CanonicalDocument:
    """Split a supported canonical aggregate into root and node records."""
    knowledge_class = f"{type(item).__module__}:{type(item).__qualname__}"
    if knowledge_class not in _KNOWLEDGE_CLASSES:
        raise TypeError(
            f"Unsupported knowledge type '{type(item).__name__}'; "
            "only canonical quantmind.knowledge types can be persisted"
        )
    full_payload = _json_payload(item.model_dump(mode="json"))
    canonical_hash = hashlib.sha256(full_payload.encode("utf-8")).hexdigest()
    if not isinstance(item, TreeKnowledge):
        return _CanonicalDocument(
            knowledge_class=knowledge_class,
            item_shape="flat",
            payload=full_payload,
            canonical_hash=canonical_hash,
            nodes=(),
        )

    nodes: list[_CanonicalNode] = []
    for node_id, node in sorted(
        item.nodes.items(), key=lambda pair: str(pair[0])
    ):
        node_payload = _json_payload(node.model_dump(mode="json"))
        nodes.append(
            _CanonicalNode(
                node_id=node_id,
                parent_id=node.parent_id,
                position=node.position,
                payload=node_payload,
                content_hash=hashlib.sha256(
                    node_payload.encode("utf-8")
                ).hexdigest(),
            )
        )
    return _CanonicalDocument(
        knowledge_class=knowledge_class,
        item_shape="tree",
        payload=_json_payload(item.model_dump(mode="json", exclude={"nodes"})),
        canonical_hash=canonical_hash,
        nodes=tuple(nodes),
    )


def _canonical_paper_artifact(
    artifact: PaperArtifact,
) -> _CanonicalPaperArtifact:
    """Normalize a paper artifact without losing its aggregate hash."""
    full_payload = _json_payload(artifact.model_dump(mode="json"))
    canonical_hash = hashlib.sha256(full_payload.encode("utf-8")).hexdigest()
    if isinstance(artifact, PaperGlobalSummary):
        return _CanonicalPaperArtifact(
            artifact=artifact,
            payload=full_payload,
            canonical_hash=canonical_hash,
            members=(),
        )
    if isinstance(artifact, PaperStructureTree):
        members = tuple(
            _CanonicalPaperMember(
                member_id=node.node_id,
                parent_id=node.parent_id,
                position=node.position,
                payload=(
                    payload := _json_payload(node.model_dump(mode="json"))
                ),
                content_hash=hashlib.sha256(
                    payload.encode("utf-8")
                ).hexdigest(),
            )
            for _, node in sorted(
                artifact.nodes.items(), key=lambda pair: str(pair[0])
            )
        )
        return _CanonicalPaperArtifact(
            artifact=artifact,
            payload=_json_payload(
                artifact.model_dump(mode="json", exclude={"nodes"})
            ),
            canonical_hash=canonical_hash,
            members=members,
        )
    if isinstance(artifact, PaperAnnotationSet):
        members = tuple(
            _CanonicalPaperMember(
                member_id=annotation.annotation_id,
                parent_id=None,
                position=annotation.position,
                payload=(
                    payload := _json_payload(annotation.model_dump(mode="json"))
                ),
                content_hash=hashlib.sha256(
                    payload.encode("utf-8")
                ).hexdigest(),
            )
            for annotation in artifact.annotations
        )
        return _CanonicalPaperArtifact(
            artifact=artifact,
            payload=_json_payload(
                artifact.model_dump(mode="json", exclude={"annotations"})
            ),
            canonical_hash=canonical_hash,
            members=members,
        )
    if isinstance(artifact, PaperTranslation):
        members = tuple(
            _CanonicalPaperMember(
                member_id=page.page_id,
                parent_id=None,
                position=page.position,
                payload=(
                    payload := _json_payload(page.model_dump(mode="json"))
                ),
                content_hash=hashlib.sha256(
                    payload.encode("utf-8")
                ).hexdigest(),
            )
            for page in artifact.pages
        )
        return _CanonicalPaperArtifact(
            artifact=artifact,
            payload=_json_payload(
                artifact.model_dump(mode="json", exclude={"pages"})
            ),
            canonical_hash=canonical_hash,
            members=members,
        )
    if not isinstance(artifact, PaperChunkSet):
        raise TypeError(
            f"Unsupported paper artifact '{type(artifact).__name__}'"
        )
    members = tuple(
        _CanonicalPaperMember(
            member_id=chunk.chunk_id,
            parent_id=None,
            position=chunk.position,
            payload=(payload := _json_payload(chunk.model_dump(mode="json"))),
            content_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )
        for chunk in artifact.chunks
    )
    return _CanonicalPaperArtifact(
        artifact=artifact,
        payload=_json_payload(
            artifact.model_dump(mode="json", exclude={"chunks"})
        ),
        canonical_hash=canonical_hash,
        members=members,
    )


def _prepare_paper_source(source: PaperSourceRevision) -> tuple[str, str]:
    """Validate loaded source blobs and return its canonical payload/hash."""
    for asset in source.assets:
        blob = source.blobs.get(asset.content_hash)
        if blob is None:
            raise ValueError(
                f"Paper source is missing blob for asset '{asset.asset_id}'"
            )
        if (
            len(blob) != asset.size_bytes
            or hashlib.sha256(blob).hexdigest() != asset.content_hash
        ):
            raise ValueError(
                f"Paper source blob for asset '{asset.asset_id}' is invalid"
            )
    payload = _json_payload(source.model_dump(mode="json"))
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assemble_canonical_payload(
    *,
    item_id: str,
    item_shape: str,
    item_payload: str,
    expected_node_count: int,
    node_records: Sequence[tuple[str, str | None, int, str, str]],
) -> str:
    """Rehydrate normalized tree nodes into the canonical Pydantic payload."""
    if item_shape == "flat":
        if expected_node_count or node_records:
            raise RuntimeError(
                f"Stale canonical knowledge for item '{item_id}': "
                "flat knowledge unexpectedly has tree nodes"
            )
        return item_payload
    if item_shape != "tree":
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': "
            f"unsupported item shape '{item_shape}'"
        )
    if len(node_records) != expected_node_count:
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': expected "
            f"{expected_node_count} canonical nodes, found {len(node_records)}"
        )
    try:
        root_payload = json.loads(item_payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': invalid root JSON"
        ) from exc
    if not isinstance(root_payload, dict):
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': invalid root payload"
        )
    nodes: dict[str, object] = {}
    for node_id, parent_id, position, node_payload, node_hash in node_records:
        actual_hash = hashlib.sha256(node_payload.encode("utf-8")).hexdigest()
        if actual_hash != node_hash:
            raise RuntimeError(
                f"Stale canonical knowledge for item '{item_id}': "
                f"node '{node_id}' content hash mismatch"
            )
        try:
            parsed_node = json.loads(node_payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Stale canonical knowledge for item '{item_id}': "
                f"node '{node_id}' contains invalid JSON"
            ) from exc
        if (
            not isinstance(parsed_node, dict)
            or parsed_node.get("node_id") != node_id
            or parsed_node.get("parent_id") != parent_id
            or parsed_node.get("position") != position
        ):
            raise RuntimeError(
                f"Stale canonical knowledge for item '{item_id}': "
                f"node '{node_id}' metadata does not match its payload"
            )
        nodes[node_id] = parsed_node
    root_payload["nodes"] = nodes
    return _json_payload(root_payload)


def _load_canonical(
    *,
    item_id: str,
    knowledge_class: str,
    item_type: str,
    schema_version: str,
    payload: str,
    canonical_hash: str,
) -> BaseKnowledge:
    """Validate stored canonical bytes against identity and schema metadata."""
    actual_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if actual_hash != canonical_hash:
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': content hash mismatch"
        )
    model = _KNOWLEDGE_CLASSES.get(knowledge_class)
    if model is None:
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': "
            f"unsupported stored type '{knowledge_class}'"
        )
    try:
        item = model.model_validate_json(payload)
    except ValidationError as exc:
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': "
            "stored payload no longer validates"
        ) from exc
    if (
        str(item.id) != item_id
        or item.item_type != item_type
        or item.schema_version != schema_version
    ):
        raise RuntimeError(
            f"Stale canonical knowledge for item '{item_id}': identity or schema "
            "metadata does not match the payload"
        )
    return item


def _timestamp(value: datetime, field_name: str) -> float:
    """Normalize an aware canonical timestamp for SQLite filtering."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc).timestamp()


_PAPER_TABLES_SQL = """
CREATE TABLE paper_sources (
    source_revision_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    source_content_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    asset_count INTEGER NOT NULL CHECK (asset_count > 0)
);

CREATE TABLE paper_source_assets (
    asset_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    page_number INTEGER,
    media_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    blob BLOB NOT NULL,
    FOREIGN KEY (source_revision_id) REFERENCES paper_sources(source_revision_id)
        ON DELETE CASCADE
);

-- A derived artifact (e.g. a self-contained structure tree) can be stored
-- without its source revision, so ``source_revision_id`` is a metadata pointer
-- with no foreign key to ``paper_sources``. Chunk-set and summary artifacts
-- still write their source first via ``put_paper``.
CREATE TABLE paper_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    producer_config_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    member_count INTEGER NOT NULL CHECK (member_count >= 0),
    target_count INTEGER NOT NULL CHECK (target_count >= 0),
    UNIQUE (source_revision_id, artifact_kind, producer_config_hash)
);

CREATE TABLE paper_artifact_members (
    artifact_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    parent_id TEXT,
    position INTEGER NOT NULL CHECK (position >= 0),
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (artifact_id, member_id),
    FOREIGN KEY (artifact_id) REFERENCES paper_artifacts(artifact_id)
        ON DELETE CASCADE
);

CREATE TABLE paper_artifact_lineage (
    artifact_id TEXT NOT NULL,
    input_artifact_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (artifact_id, input_artifact_id, relation),
    FOREIGN KEY (artifact_id) REFERENCES paper_artifacts(artifact_id)
        ON DELETE CASCADE,
    FOREIGN KEY (input_artifact_id) REFERENCES paper_artifacts(artifact_id)
        ON DELETE RESTRICT
);

CREATE TABLE paper_projections (
    target_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    member_id TEXT,
    artifact_kind TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    as_of REAL NOT NULL,
    available_at REAL NOT NULL,
    source_kind TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    projection_kind TEXT NOT NULL,
    modality TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    projection_hash TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    artifact_schema_version TEXT NOT NULL,
    projection_schema_version TEXT NOT NULL,
    artifact_canonical_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    FOREIGN KEY (source_revision_id) REFERENCES paper_sources(source_revision_id)
        ON DELETE CASCADE,
    FOREIGN KEY (artifact_id) REFERENCES paper_artifacts(artifact_id)
        ON DELETE CASCADE,
    FOREIGN KEY (artifact_id, member_id)
        REFERENCES paper_artifact_members(artifact_id, member_id)
        ON DELETE CASCADE
);

CREATE INDEX paper_artifacts_source_kind
    ON paper_artifacts(source_revision_id, artifact_kind);
CREATE UNIQUE INDEX paper_artifact_members_sibling_position
    ON paper_artifact_members(
        artifact_id, COALESCE(parent_id, ''), position
    );
CREATE INDEX paper_projections_filters
    ON paper_projections(artifact_kind, source_kind, source_revision_id);
"""

_PAPER_V6_TABLES_SQL = """
CREATE TABLE paper_registration_records (
    registration_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    chunk_set_id TEXT NOT NULL,
    summary_id TEXT NOT NULL,
    annotation_set_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    registered_at REAL NOT NULL,
    payload_json TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    FOREIGN KEY (source_revision_id)
        REFERENCES paper_sources(source_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (chunk_set_id)
        REFERENCES paper_artifacts(artifact_id) ON DELETE CASCADE,
    FOREIGN KEY (summary_id)
        REFERENCES paper_artifacts(artifact_id) ON DELETE CASCADE,
    FOREIGN KEY (annotation_set_id)
        REFERENCES paper_artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX paper_registration_records_source_time
    ON paper_registration_records(source_revision_id, registered_at);

CREATE TABLE paper_catalog (
    source_revision_id TEXT PRIMARY KEY,
    source_content_hash TEXT NOT NULL,
    title TEXT,
    authors_text TEXT NOT NULL,
    published_at REAL,
    available_at REAL NOT NULL,
    source_kind TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    page_count INTEGER NOT NULL CHECK (page_count > 0),
    empty_page_count INTEGER NOT NULL CHECK (empty_page_count >= 0),
    FOREIGN KEY (source_revision_id)
        REFERENCES paper_sources(source_revision_id) ON DELETE CASCADE
);

CREATE INDEX paper_catalog_dates
    ON paper_catalog(published_at, available_at, source_revision_id);
CREATE INDEX paper_catalog_source_kind
    ON paper_catalog(source_kind, source_revision_id);
"""

_PAPER_V7_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS paper_translation_registration_records (
    registration_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    translation_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    registered_at REAL NOT NULL,
    payload_json TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    FOREIGN KEY (source_revision_id)
        REFERENCES paper_sources(source_revision_id) ON DELETE CASCADE,
    FOREIGN KEY (translation_id)
        REFERENCES paper_artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS paper_translation_registration_source_time
    ON paper_translation_registration_records(
        source_revision_id, registered_at
    );
"""


def _catalog_row(source: PaperSourceRevision) -> tuple[object, ...]:
    return (
        str(source.id),
        source.source.content_hash,
        source.title,
        _json_payload(list(source.authors)),
        (
            _timestamp(source.published_at, "PaperSourceRevision.published_at")
            if source.published_at is not None
            else None
        ),
        _timestamp(source.available_at, "PaperSourceRevision.available_at"),
        source.source.kind,
        source.source.uri,
        len(source.parsed.pages),
        sum(not page.text.strip() for page in source.parsed.pages),
    )


def _write_catalog_row(
    db: sqlite3.Connection, source: PaperSourceRevision
) -> None:
    db.execute(
        """
        INSERT INTO paper_catalog (
            source_revision_id, source_content_hash, title, authors_text,
            published_at, available_at, source_kind, source_uri,
            page_count, empty_page_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_revision_id) DO UPDATE SET
            source_content_hash = excluded.source_content_hash,
            title = excluded.title,
            authors_text = excluded.authors_text,
            published_at = excluded.published_at,
            available_at = excluded.available_at,
            source_kind = excluded.source_kind,
            source_uri = excluded.source_uri,
            page_count = excluded.page_count,
            empty_page_count = excluded.empty_page_count
        """,
        _catalog_row(source),
    )


def _paper_registration_id(result: PaperAnnotatedResult) -> UUID:
    identity = _json_payload(
        {
            "annotation_set_id": str(result.annotation_set.id),
            "chunk_set_id": str(result.chunk_set.id),
            "policy_version": _REGISTRATION_POLICY_VERSION,
            "source_revision_id": str(result.source_revision.id),
            "summary_id": str(result.global_summary.id),
        }
    )
    return uuid5(NAMESPACE_URL, f"quantmind:paper-registration:{identity}")


def _build_registration_record(
    result: PaperAnnotatedResult,
    *,
    registered_at: datetime,
    embedding_model: str,
    embedding_dimensions: int,
) -> PaperRegistrationRecord:
    source = result.source_revision
    raw_asset = next(
        (
            asset
            for asset in source.assets
            if asset.asset_id == source.raw_asset_id
        ),
        None,
    )
    if raw_asset is None:
        raise ValueError("paper source raw PDF asset is missing")
    values: dict[str, object] = {
        "registration_id": _paper_registration_id(result),
        "schema_version": "1.0",
        "registered_at": registered_at,
        "source_revision_id": source.id,
        "chunk_set_id": result.chunk_set.id,
        "summary_id": result.global_summary.id,
        "annotation_set_id": result.annotation_set.id,
        "pdf_size_bytes": raw_asset.size_bytes,
        "page_count": len(source.parsed.pages),
        "empty_pages": tuple(
            page.page_number
            for page in source.parsed.pages
            if not page.text.strip()
        ),
        "parser_name": source.parsed.parser_name,
        "parser_version": source.parsed.parser_version,
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_dimensions,
        "passed_checks": _REGISTRATION_CHECKS,
    }
    canonical_payload = _json_payload(
        PaperRegistrationRecord.model_construct(
            **cast(dict[str, Any], values),
            canonical_hash="0" * 64,
        ).model_dump(mode="json", exclude={"canonical_hash"})
    )
    values["canonical_hash"] = hashlib.sha256(
        canonical_payload.encode("utf-8")
    ).hexdigest()
    return PaperRegistrationRecord.model_validate(values)


def _load_registration_row(row: sqlite3.Row) -> PaperRegistrationRecord:
    registration_id = str(row["registration_id"])
    try:
        record = PaperRegistrationRecord.model_validate_json(
            str(row["payload_json"])
        )
    except ValidationError as exc:
        raise RuntimeError(
            f"Stale paper registration '{registration_id}': invalid payload"
        ) from exc
    if (
        str(record.registration_id) != registration_id
        or record.schema_version != str(row["schema_version"])
        or _timestamp(record.registered_at, "registered_at")
        != float(row["registered_at"])
        or record.canonical_hash != str(row["canonical_hash"])
        or _registration_content_hash(record) != record.canonical_hash
        or str(record.source_revision_id) != str(row["source_revision_id"])
        or str(record.chunk_set_id) != str(row["chunk_set_id"])
        or str(record.summary_id) != str(row["summary_id"])
        or str(record.annotation_set_id) != str(row["annotation_set_id"])
    ):
        raise RuntimeError(
            f"Stale paper registration '{registration_id}': metadata mismatch"
        )
    return record


def _paper_translation_registration_id(
    result: PaperTranslatedResult,
) -> UUID:
    identity = _json_payload(
        {
            "policy_version": _TRANSLATION_REGISTRATION_POLICY_VERSION,
            "source_revision_id": str(result.source_revision.id),
            "translation_id": str(result.translation.id),
        }
    )
    return uuid5(
        NAMESPACE_URL,
        f"quantmind:paper-translation-registration:{identity}",
    )


def _build_translation_registration_record(
    result: PaperTranslatedResult,
    *,
    registered_at: datetime,
) -> PaperTranslationRegistrationRecord:
    values: dict[str, object] = {
        "registration_id": _paper_translation_registration_id(result),
        "schema_version": "1.0",
        "registered_at": registered_at,
        "source_revision_id": result.source_revision.id,
        "translation_id": result.translation.id,
        "page_count": len(result.translation.pages),
        "source_language": result.translation.producer.source_language,
        "target_language": result.translation.producer.target_language,
        "passed_checks": _TRANSLATION_REGISTRATION_CHECKS,
    }
    provisional = PaperTranslationRegistrationRecord.model_construct(
        **cast(dict[str, Any], values),
        canonical_hash="0" * 64,
    )
    payload = _json_payload(
        provisional.model_dump(mode="json", exclude={"canonical_hash"})
    )
    values["canonical_hash"] = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()
    return PaperTranslationRegistrationRecord.model_validate(values)


def _load_translation_registration_row(
    row: sqlite3.Row,
) -> PaperTranslationRegistrationRecord:
    registration_id = str(row["registration_id"])
    try:
        record = PaperTranslationRegistrationRecord.model_validate_json(
            str(row["payload_json"])
        )
    except ValidationError as exc:
        raise RuntimeError(
            "Stale paper translation registration "
            f"'{registration_id}': invalid payload"
        ) from exc
    if (
        str(record.registration_id) != registration_id
        or record.schema_version != str(row["schema_version"])
        or _timestamp(record.registered_at, "registered_at")
        != float(row["registered_at"])
        or record.canonical_hash != str(row["canonical_hash"])
        or _translation_registration_content_hash(record)
        != record.canonical_hash
        or str(record.source_revision_id) != str(row["source_revision_id"])
        or str(record.translation_id) != str(row["translation_id"])
    ):
        raise RuntimeError(
            "Stale paper translation registration "
            f"'{registration_id}': metadata mismatch"
        )
    return record


def _migrate_schema_v3_to_v4(db: sqlite3.Connection) -> None:
    """Allow vectorless artifacts and hierarchical normalized members."""
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE paper_artifacts_v4 (
                artifact_id TEXT PRIMARY KEY,
                source_revision_id TEXT NOT NULL,
                artifact_kind TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                producer_config_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                canonical_hash TEXT NOT NULL,
                member_count INTEGER NOT NULL CHECK (member_count >= 0),
                target_count INTEGER NOT NULL CHECK (target_count >= 0),
                UNIQUE (source_revision_id, artifact_kind, producer_config_hash),
                FOREIGN KEY (source_revision_id)
                    REFERENCES paper_sources(source_revision_id)
                    ON DELETE CASCADE
            );

            INSERT INTO paper_artifacts_v4
            SELECT * FROM paper_artifacts;

            CREATE TABLE paper_artifact_members_v4 (
                artifact_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                parent_id TEXT,
                position INTEGER NOT NULL CHECK (position >= 0),
                payload_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (artifact_id, member_id),
                FOREIGN KEY (artifact_id)
                    REFERENCES paper_artifacts_v4(artifact_id)
                    ON DELETE CASCADE
            );

            INSERT INTO paper_artifact_members_v4 (
                artifact_id, member_id, parent_id, position,
                payload_json, content_hash
            )
            SELECT artifact_id, member_id, NULL, position,
                   payload_json, content_hash
            FROM paper_artifact_members;

            DROP TABLE paper_artifact_members;
            DROP TABLE paper_artifacts;
            ALTER TABLE paper_artifacts_v4 RENAME TO paper_artifacts;
            ALTER TABLE paper_artifact_members_v4
                RENAME TO paper_artifact_members;

            CREATE INDEX paper_artifacts_source_kind
                ON paper_artifacts(source_revision_id, artifact_kind);
            CREATE UNIQUE INDEX paper_artifact_members_sibling_position
                ON paper_artifact_members(
                    artifact_id, COALESCE(parent_id, ''), position
                );

            PRAGMA user_version = 4;
            COMMIT;
            """
        )
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError(
                "Stale knowledge library schema: v3 migration broke links"
            )
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _migrate_schema_v4_to_v5(db: sqlite3.Connection) -> None:
    """Decouple derived artifacts from a stored source revision.

    A self-contained structure tree is stored without its source, so the
    ``paper_artifacts -> paper_sources`` foreign key is dropped. The
    ``source_revision_id`` column and every other paper relationship are
    preserved; chunk-set and summary writes still persist their source first.
    """
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE paper_artifacts_v5 (
                artifact_id TEXT PRIMARY KEY,
                source_revision_id TEXT NOT NULL,
                artifact_kind TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                producer_config_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                canonical_hash TEXT NOT NULL,
                member_count INTEGER NOT NULL CHECK (member_count >= 0),
                target_count INTEGER NOT NULL CHECK (target_count >= 0),
                UNIQUE (source_revision_id, artifact_kind, producer_config_hash)
            );

            INSERT INTO paper_artifacts_v5 SELECT * FROM paper_artifacts;

            DROP TABLE paper_artifacts;
            ALTER TABLE paper_artifacts_v5 RENAME TO paper_artifacts;

            CREATE INDEX paper_artifacts_source_kind
                ON paper_artifacts(source_revision_id, artifact_kind);

            PRAGMA user_version = 5;
            COMMIT;
            """
        )
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError(
                "Stale knowledge library schema: v4 migration broke links"
            )
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _migrate_schema_v5_to_v6(db: sqlite3.Connection) -> None:
    """Add registration audit and backfill the source management catalog."""
    try:
        db.executescript(f"BEGIN IMMEDIATE;\n{_PAPER_V6_TABLES_SQL}")
        source_columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(paper_sources)").fetchall()
        }
        if "payload_json" not in source_columns:
            source_count = int(
                db.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0]
            )
            if source_count:
                raise RuntimeError(
                    "Stale paper source schema cannot be cataloged during v6 "
                    "migration"
                )
            rows = []
        else:
            rows = db.execute(
                "SELECT source_revision_id, payload_json FROM paper_sources"
            ).fetchall()
        for row in rows:
            try:
                source = PaperSourceRevision.model_validate_json(
                    str(row["payload_json"])
                )
            except ValidationError as exc:
                raise RuntimeError(
                    "Stale paper source during v6 catalog migration: "
                    f"'{row['source_revision_id']}'"
                ) from exc
            if str(source.id) != str(row["source_revision_id"]):
                raise RuntimeError(
                    "Stale paper source during v6 catalog migration: "
                    "identity mismatch"
                )
            _write_catalog_row(db, source)
        db.execute("PRAGMA user_version = 6")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError(
                "Stale knowledge library schema: v5 migration broke links"
            )
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _migrate_schema_v6_to_v7(db: sqlite3.Connection) -> None:
    """Add immutable audit records for page-aligned translations."""
    try:
        db.executescript(
            f"BEGIN IMMEDIATE;\n{_PAPER_V7_TABLES_SQL}\n"
            "PRAGMA user_version = 7;"
        )
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError(
                "Stale knowledge library schema: v6 migration broke links"
            )
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _initialize_schema(db: sqlite3.Connection) -> None:
    """Create the current schema or reject an incompatible local database."""
    version_row = db.execute("PRAGMA user_version").fetchone()
    version = int(version_row[0])
    if version not in (0, 2, 3, 4, 5, 6, _DATABASE_SCHEMA_VERSION):
        raise RuntimeError(
            "Stale knowledge library schema: database version "
            f"{version}, expected {_DATABASE_SCHEMA_VERSION}"
        )
    if version == _DATABASE_SCHEMA_VERSION:
        return
    if version == 2:
        migration_sql = (
            (_PAPER_TABLES_SQL + _PAPER_V6_TABLES_SQL + _PAPER_V7_TABLES_SQL)
            .replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
            .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
        )
        db.executescript(
            f"{migration_sql}\nPRAGMA user_version = {_DATABASE_SCHEMA_VERSION};"
        )
        return
    if version == 3:
        _migrate_schema_v3_to_v4(db)
        version = 4
    if version == 4:
        _migrate_schema_v4_to_v5(db)
        version = 5
    if version == 5:
        _migrate_schema_v5_to_v6(db)
        version = 6
    if version == 6:
        _migrate_schema_v6_to_v7(db)
        return
    db.executescript(
        f"""
        CREATE TABLE knowledge_items (
            item_id TEXT PRIMARY KEY,
            knowledge_class TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_shape TEXT NOT NULL CHECK (item_shape IN ('flat', 'tree')),
            schema_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            canonical_hash TEXT NOT NULL,
            node_count INTEGER NOT NULL CHECK (node_count >= 0),
            target_count INTEGER NOT NULL CHECK (target_count > 0)
        );

        CREATE TABLE knowledge_nodes (
            item_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            parent_id TEXT,
            position INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            PRIMARY KEY (item_id, node_id),
            FOREIGN KEY (item_id) REFERENCES knowledge_items(item_id)
                ON DELETE CASCADE
        );

        CREATE TABLE semantic_records (
            target_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            node_id TEXT,
            item_type TEXT NOT NULL,
            matched_text TEXT NOT NULL,
            as_of REAL NOT NULL,
            available_at REAL,
            source_kind TEXT NOT NULL,
            confidence TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            tree_id TEXT,
            embedding_model TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            projection_hash TEXT NOT NULL,
            source_content_hash TEXT,
            knowledge_schema_version TEXT NOT NULL,
            projection_schema_version TEXT NOT NULL,
            item_canonical_hash TEXT NOT NULL,
            embedding BLOB NOT NULL,
            FOREIGN KEY (item_id) REFERENCES knowledge_items(item_id)
                ON DELETE CASCADE
        );

        CREATE INDEX semantic_records_item_id
            ON semantic_records(item_id);
        CREATE INDEX semantic_records_filters
            ON semantic_records(item_type, source_kind, confidence, tree_id);
        CREATE INDEX knowledge_nodes_parent
            ON knowledge_nodes(item_id, parent_id, position);

        {_PAPER_TABLES_SQL}

        {_PAPER_V6_TABLES_SQL}

        {_PAPER_V7_TABLES_SQL}

        PRAGMA user_version = {_DATABASE_SCHEMA_VERSION};
        """
    )


class _SQLiteStore:
    """Own the concrete SQLite schema, transactions, and record validation."""

    def __init__(
        self,
        db: sqlite3.Connection,
        *,
        database_path: Path | None,
    ) -> None:
        self._db = db
        self._database_path = database_path

    @classmethod
    def open(cls, path: str | Path) -> "_SQLiteStore":
        """Open and initialize a concrete SQLite knowledge store."""
        supplied_path = str(path)
        database_path = (
            supplied_path
            if supplied_path == ":memory:"
            else str(Path(supplied_path).expanduser())
        )
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(database_path, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA busy_timeout = 5000")
            _initialize_schema(db)
        except sqlite3.DatabaseError as exc:
            if db is not None:
                db.close()
            raise RuntimeError(
                f"Corrupt knowledge library database at '{database_path}'"
            ) from exc
        except Exception:
            if db is not None:
                db.close()
            raise
        return cls(
            db,
            database_path=(
                None if database_path == ":memory:" else Path(database_path)
            ),
        )

    def prepare_put(self, item: BaseKnowledge) -> _PreparedPut:
        """Validate a canonical write and load vectors it may retain."""
        rows = self._db.execute(
            "SELECT * FROM semantic_records WHERE item_id = ?",
            (str(item.id),),
        ).fetchall()
        existing = {
            str(row["target_id"]): _StoredEmbedding(
                target_id=str(row["target_id"]),
                embedding_model=str(row["embedding_model"]),
                dimension=int(row["dimension"]),
                projection_hash=str(row["projection_hash"]),
                source_content_hash=(
                    str(row["source_content_hash"])
                    if row["source_content_hash"] is not None
                    else None
                ),
                knowledge_schema_version=str(row["knowledge_schema_version"]),
                projection_schema_version=str(row["projection_schema_version"]),
                embedding=bytes(row["embedding"]),
            )
            for row in rows
        }
        return _PreparedPut(
            item=item,
            canonical=_canonical_payload(item),
            as_of=_timestamp(item.as_of, "BaseKnowledge.as_of"),
            available_at=(
                _timestamp(item.available_at, "BaseKnowledge.available_at")
                if item.available_at is not None
                else None
            ),
            tags_json=json.dumps(
                item.tags,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            existing_embeddings=existing,
        )

    def prepare_put_paper(
        self, result: PaperSemanticResult | PaperAnnotatedResult
    ) -> _PreparedPaperPut:
        """Validate paper blobs and load projections eligible for reuse."""
        source = result.source_revision
        source_payload, source_canonical_hash = _prepare_paper_source(source)
        artifacts = [
            _canonical_paper_artifact(result.chunk_set),
            _canonical_paper_artifact(result.global_summary),
        ]
        if isinstance(result, PaperAnnotatedResult):
            artifacts.append(_canonical_paper_artifact(result.annotation_set))
        artifact_ids = tuple(
            str(canonical.artifact.id) for canonical in artifacts
        )
        placeholders = ", ".join("?" for _ in artifact_ids)
        rows = self._db.execute(
            f"""
            SELECT * FROM paper_projections
            WHERE artifact_id IN ({placeholders})
            """,
            artifact_ids,
        ).fetchall()
        existing = {
            str(row["target_id"]): _StoredEmbedding(
                target_id=str(row["target_id"]),
                embedding_model=str(row["embedding_model"]),
                dimension=int(row["dimension"]),
                projection_hash=str(row["projection_hash"]),
                source_content_hash=str(row["source_content_hash"]),
                knowledge_schema_version=str(row["artifact_schema_version"]),
                projection_schema_version=str(row["projection_schema_version"]),
                embedding=bytes(row["embedding"]),
            )
            for row in rows
        }
        return _PreparedPaperPut(
            result=result,
            source_payload=source_payload,
            source_canonical_hash=source_canonical_hash,
            artifacts=tuple(artifacts),
            existing_embeddings=existing,
        )

    def prepare_put_translation(
        self,
        result: PaperTranslatedResult,
    ) -> _PreparedTranslationPut:
        """Validate exact source bytes and normalize a translation artifact."""
        validated_translation = PaperTranslation.model_validate(
            result.translation.model_dump(mode="json")
        )
        validated_result = PaperTranslatedResult(
            source_revision=result.source_revision,
            translation=validated_translation,
        )
        source_payload, source_canonical_hash = _prepare_paper_source(
            validated_result.source_revision
        )
        return _PreparedTranslationPut(
            result=validated_result,
            source_payload=source_payload,
            source_canonical_hash=source_canonical_hash,
            canonical=_canonical_paper_artifact(validated_translation),
        )

    def _put_paper_source(
        self,
        source: PaperSourceRevision,
        *,
        payload: str,
        canonical_hash: str,
    ) -> None:
        """Write or reuse one exact source inside the active transaction."""
        self._db.execute(
            """
            INSERT INTO paper_sources (
                source_revision_id, schema_version, source_content_hash,
                payload_json, canonical_hash, asset_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_revision_id) DO NOTHING
            """,
            (
                str(source.id),
                source.schema_version,
                source.source.content_hash,
                payload,
                canonical_hash,
                len(source.assets),
            ),
        )
        for asset in source.assets:
            self._db.execute(
                """
                INSERT INTO paper_source_assets (
                    asset_id, source_revision_id, kind, page_number,
                    media_type, content_hash, size_bytes, blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO NOTHING
                """,
                (
                    str(asset.asset_id),
                    str(source.id),
                    asset.kind,
                    asset.page_number,
                    asset.media_type,
                    asset.content_hash,
                    asset.size_bytes,
                    source.blobs[asset.content_hash],
                ),
            )
        _write_catalog_row(self._db, source)

    def _write_paper_bundle(
        self,
        prepared: _PreparedPaperPut,
        targets: Sequence[_RetrievalTarget],
        vectors: dict[str, tuple[bytes, int]],
        *,
        embedding_model: str,
        register: bool,
    ) -> PaperRegistrationRecord | None:
        """Atomically persist one complete paper bundle and optional audit."""
        result = prepared.result
        if register and not isinstance(result, PaperAnnotatedResult):
            raise TypeError("only an annotated paper can be registered")
        source = result.source_revision
        canonical_by_id = {
            artifact.artifact.id: artifact for artifact in prepared.artifacts
        }
        targets_by_artifact: dict[UUID, list[_RetrievalTarget]] = {}
        for target in targets:
            targets_by_artifact.setdefault(target.artifact_id, []).append(
                target
            )
        projected_artifact_ids = {
            result.chunk_set.id,
            result.global_summary.id,
        }
        if set(targets_by_artifact) != projected_artifact_ids:
            raise ValueError("paper artifacts do not have complete projections")
        if set(vectors) != {target.target_id for target in targets}:
            raise ValueError("paper projections do not have complete vectors")
        dimensions = {dimension for _, dimension in vectors.values()}
        if len(dimensions) != 1:
            raise ValueError("paper projection dimensions are inconsistent")
        embedding_dimensions = next(iter(dimensions))
        registration: PaperRegistrationRecord | None = None
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._put_paper_source(
                source,
                payload=prepared.source_payload,
                canonical_hash=prepared.source_canonical_hash,
            )
            for canonical in prepared.artifacts:
                artifact = canonical.artifact
                artifact_targets = targets_by_artifact.get(artifact.id, [])
                self._db.execute(
                    """
                    INSERT INTO paper_artifacts (
                        artifact_id, source_revision_id, artifact_kind,
                        schema_version, producer_config_hash, payload_json,
                        canonical_hash, member_count, target_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(artifact_id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        canonical_hash = excluded.canonical_hash,
                        member_count = excluded.member_count,
                        target_count = excluded.target_count
                    """,
                    (
                        str(artifact.id),
                        str(artifact.source_revision_id),
                        artifact.artifact_kind,
                        artifact.schema_version,
                        artifact.producer_config_hash,
                        canonical.payload,
                        canonical.canonical_hash,
                        len(canonical.members),
                        len(artifact_targets),
                    ),
                )
                self._db.execute(
                    "DELETE FROM paper_projections WHERE artifact_id = ?",
                    (str(artifact.id),),
                )
                self._db.execute(
                    "DELETE FROM paper_artifact_members WHERE artifact_id = ?",
                    (str(artifact.id),),
                )
                self._db.execute(
                    "DELETE FROM paper_artifact_lineage WHERE artifact_id = ?",
                    (str(artifact.id),),
                )
                for member in canonical.members:
                    self._db.execute(
                        """
                        INSERT INTO paper_artifact_members (
                            artifact_id, member_id, parent_id, position,
                            payload_json, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(artifact.id),
                            str(member.member_id),
                            (
                                str(member.parent_id)
                                if member.parent_id is not None
                                else None
                            ),
                            member.position,
                            member.payload,
                            member.content_hash,
                        ),
                    )
            derived_artifacts: list[PaperGlobalSummary | PaperAnnotationSet] = [
                result.global_summary
            ]
            if isinstance(result, PaperAnnotatedResult):
                derived_artifacts.append(result.annotation_set)
            for artifact in derived_artifacts:
                for locator in artifact.derived_from:
                    self._db.execute(
                        """
                        INSERT INTO paper_artifact_lineage (
                            artifact_id, input_artifact_id, relation
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            str(artifact.id),
                            str(locator.artifact_id),
                            "generated_from",
                        ),
                    )
            for target in targets:
                blob, dimension = vectors[target.target_id]
                canonical = canonical_by_id[target.artifact_id]
                self._db.execute(
                    """
                    INSERT INTO paper_projections (
                        target_id, source_revision_id, artifact_id, member_id,
                        artifact_kind, matched_text, as_of, available_at,
                        source_kind, citations_json, projection_kind, modality,
                        embedding_model, dimension, projection_hash,
                        source_content_hash, artifact_schema_version,
                        projection_schema_version, artifact_canonical_hash,
                        embedding
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        target.target_id,
                        str(source.id),
                        str(target.artifact_id),
                        str(target.node_id) if target.node_id else None,
                        target.artifact_kind,
                        target.text,
                        _timestamp(source.as_of, "PaperSourceRevision.as_of"),
                        _timestamp(
                            source.available_at,
                            "PaperSourceRevision.available_at",
                        ),
                        source.source.kind,
                        _json_payload(
                            [
                                citation.model_dump(mode="json")
                                for citation in target.citations
                            ]
                        ),
                        "text_embedding",
                        "text",
                        embedding_model,
                        dimension,
                        target.projection_hash,
                        source.source.content_hash,
                        canonical.artifact.schema_version,
                        _PROJECTION_SCHEMA_VERSION,
                        canonical.canonical_hash,
                        blob,
                    ),
                )
            if register:
                assert isinstance(result, PaperAnnotatedResult)
                registration_id = _paper_registration_id(result)
                existing_row = self._db.execute(
                    """
                    SELECT * FROM paper_registration_records
                    WHERE registration_id = ?
                    """,
                    (str(registration_id),),
                ).fetchone()
                if existing_row is None:
                    registration = _build_registration_record(
                        result,
                        registered_at=datetime.now(timezone.utc),
                        embedding_model=embedding_model,
                        embedding_dimensions=embedding_dimensions,
                    )
                    self._db.execute(
                        """
                        INSERT INTO paper_registration_records (
                            registration_id, source_revision_id, chunk_set_id,
                            summary_id, annotation_set_id, schema_version,
                            registered_at, payload_json, canonical_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(registration.registration_id),
                            str(registration.source_revision_id),
                            str(registration.chunk_set_id),
                            str(registration.summary_id),
                            str(registration.annotation_set_id),
                            registration.schema_version,
                            _timestamp(
                                registration.registered_at,
                                "PaperRegistrationRecord.registered_at",
                            ),
                            _json_payload(registration.model_dump(mode="json")),
                            registration.canonical_hash,
                        ),
                    )
                else:
                    registration = _load_registration_row(existing_row)
                    expected = _build_registration_record(
                        result,
                        registered_at=registration.registered_at,
                        embedding_model=embedding_model,
                        embedding_dimensions=embedding_dimensions,
                    )
                    if registration != expected:
                        raise RuntimeError(
                            "Stored paper registration conflicts with bundle"
                        )
            if (
                self._db.execute("PRAGMA foreign_key_check").fetchone()
                is not None
            ):
                raise RuntimeError("paper bundle violates SQLite constraints")
            self._db.execute("COMMIT")
            return registration
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def put_paper(
        self,
        prepared: _PreparedPaperPut,
        targets: Sequence[_RetrievalTarget],
        vectors: dict[str, tuple[bytes, int]],
        *,
        embedding_model: str,
    ) -> None:
        """Atomically persist one semantic source/chunk/summary result."""
        self._write_paper_bundle(
            prepared,
            targets,
            vectors,
            embedding_model=embedding_model,
            register=False,
        )

    def put_annotated_paper(
        self,
        prepared: _PreparedPaperPut,
        targets: Sequence[_RetrievalTarget],
        vectors: dict[str, tuple[bytes, int]],
        *,
        embedding_model: str,
    ) -> PaperRegistrationRecord:
        """Atomically persist and audit one annotated paper bundle."""
        registration = self._write_paper_bundle(
            prepared,
            targets,
            vectors,
            embedding_model=embedding_model,
            register=True,
        )
        assert registration is not None
        return registration

    def put_translation(
        self,
        prepared: _PreparedTranslationPut,
    ) -> PaperTranslationRegistrationRecord:
        """Atomically persist a source, translation pages, and audit record."""
        result = prepared.result
        source = result.source_revision
        translation = result.translation
        canonical = prepared.canonical
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._put_paper_source(
                source,
                payload=prepared.source_payload,
                canonical_hash=prepared.source_canonical_hash,
            )
            self._db.execute(
                """
                INSERT INTO paper_artifacts (
                    artifact_id, source_revision_id, artifact_kind,
                    schema_version, producer_config_hash, payload_json,
                    canonical_hash, member_count, target_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    canonical_hash = excluded.canonical_hash,
                    member_count = excluded.member_count,
                    target_count = excluded.target_count
                """,
                (
                    str(translation.id),
                    str(translation.source_revision_id),
                    translation.artifact_kind,
                    translation.schema_version,
                    translation.producer_config_hash,
                    canonical.payload,
                    canonical.canonical_hash,
                    len(canonical.members),
                ),
            )
            self._db.execute(
                "DELETE FROM paper_projections WHERE artifact_id = ?",
                (str(translation.id),),
            )
            self._db.execute(
                "DELETE FROM paper_artifact_lineage WHERE artifact_id = ?",
                (str(translation.id),),
            )
            self._db.execute(
                "DELETE FROM paper_artifact_members WHERE artifact_id = ?",
                (str(translation.id),),
            )
            for member in canonical.members:
                self._db.execute(
                    """
                    INSERT INTO paper_artifact_members (
                        artifact_id, member_id, parent_id, position,
                        payload_json, content_hash
                    ) VALUES (?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        str(translation.id),
                        str(member.member_id),
                        member.position,
                        member.payload,
                        member.content_hash,
                    ),
                )

            registration_id = _paper_translation_registration_id(result)
            row = self._db.execute(
                """
                SELECT * FROM paper_translation_registration_records
                WHERE registration_id = ?
                """,
                (str(registration_id),),
            ).fetchone()
            if row is None:
                registration = _build_translation_registration_record(
                    result,
                    registered_at=datetime.now(timezone.utc),
                )
                self._db.execute(
                    """
                    INSERT INTO paper_translation_registration_records (
                        registration_id, source_revision_id, translation_id,
                        schema_version, registered_at, payload_json,
                        canonical_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(registration.registration_id),
                        str(registration.source_revision_id),
                        str(registration.translation_id),
                        registration.schema_version,
                        _timestamp(
                            registration.registered_at,
                            "PaperTranslationRegistrationRecord.registered_at",
                        ),
                        _json_payload(registration.model_dump(mode="json")),
                        registration.canonical_hash,
                    ),
                )
            else:
                registration = _load_translation_registration_row(row)
                expected = _build_translation_registration_record(
                    result,
                    registered_at=registration.registered_at,
                )
                if registration != expected:
                    raise RuntimeError(
                        "Stored translation registration conflicts with artifact"
                    )
            if (
                self._db.execute("PRAGMA foreign_key_check").fetchone()
                is not None
            ):
                raise RuntimeError(
                    "paper translation violates SQLite constraints"
                )
            self._db.execute("COMMIT")
            return registration
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def prepare_structure_tree(
        self,
        tree: PaperStructureTree,
    ) -> _PreparedStructureTreePut:
        """Re-run a self-contained tree's integrity gate before storing it.

        The tree is persisted on its own, reading ``as_of`` / source ref /
        ``source_content_hash`` from its own provenance metadata; no source
        revision or chunk set is required. Re-validating from the tree's own
        dump makes a bypassed (for example ``model_copy``-mutated) tree fail
        closed before any row is written.
        """
        validated = PaperStructureTree.model_validate(
            tree.model_dump(mode="json")
        )
        return _PreparedStructureTreePut(
            tree=validated,
            canonical=_canonical_paper_artifact(validated),
        )

    def put_structure_tree(
        self,
        prepared: _PreparedStructureTreePut,
    ) -> None:
        """Atomically persist one self-contained structure tree with no source."""
        tree = prepared.tree
        canonical = prepared.canonical
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                """
                INSERT INTO paper_artifacts (
                    artifact_id, source_revision_id, artifact_kind,
                    schema_version, producer_config_hash, payload_json,
                    canonical_hash, member_count, target_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    canonical_hash = excluded.canonical_hash,
                    member_count = excluded.member_count,
                    target_count = excluded.target_count
                """,
                (
                    str(tree.id),
                    str(tree.source_revision_id),
                    tree.artifact_kind,
                    tree.schema_version,
                    tree.producer_config_hash,
                    canonical.payload,
                    canonical.canonical_hash,
                    len(canonical.members),
                ),
            )
            self._db.execute(
                "DELETE FROM paper_projections WHERE artifact_id = ?",
                (str(tree.id),),
            )
            self._db.execute(
                "DELETE FROM paper_artifact_members WHERE artifact_id = ?",
                (str(tree.id),),
            )
            self._db.execute(
                "DELETE FROM paper_artifact_lineage WHERE artifact_id = ?",
                (str(tree.id),),
            )
            for member in canonical.members:
                self._db.execute(
                    """
                    INSERT INTO paper_artifact_members (
                        artifact_id, member_id, parent_id, position,
                        payload_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(tree.id),
                        str(member.member_id),
                        (
                            str(member.parent_id)
                            if member.parent_id is not None
                            else None
                        ),
                        member.position,
                        member.payload,
                        member.content_hash,
                    ),
                )
            self._db.execute("COMMIT")
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def put(
        self,
        prepared: _PreparedPut,
        targets: Sequence[_RetrievalTarget],
        vectors: dict[str, tuple[bytes, int]],
        *,
        embedding_model: str,
    ) -> None:
        """Atomically replace canonical and derived records for one item."""
        item = prepared.item
        canonical = prepared.canonical
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                """
                INSERT INTO knowledge_items (
                    item_id, knowledge_class, item_type, item_shape,
                    schema_version, payload_json, canonical_hash,
                    node_count, target_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    knowledge_class = excluded.knowledge_class,
                    item_type = excluded.item_type,
                    item_shape = excluded.item_shape,
                    schema_version = excluded.schema_version,
                    payload_json = excluded.payload_json,
                    canonical_hash = excluded.canonical_hash,
                    node_count = excluded.node_count,
                    target_count = excluded.target_count
                """,
                (
                    str(item.id),
                    canonical.knowledge_class,
                    item.item_type,
                    canonical.item_shape,
                    item.schema_version,
                    canonical.payload,
                    canonical.canonical_hash,
                    len(canonical.nodes),
                    len(targets),
                ),
            )
            self._db.execute(
                "DELETE FROM semantic_records WHERE item_id = ?",
                (str(item.id),),
            )
            self._db.execute(
                "DELETE FROM knowledge_nodes WHERE item_id = ?",
                (str(item.id),),
            )
            for node in canonical.nodes:
                self._db.execute(
                    """
                    INSERT INTO knowledge_nodes (
                        item_id, node_id, parent_id, position,
                        payload_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.id),
                        str(node.node_id),
                        str(node.parent_id) if node.parent_id else None,
                        node.position,
                        node.payload,
                        node.content_hash,
                    ),
                )
            for target in targets:
                blob, dimension = vectors[target.target_id]
                self._db.execute(
                    """
                    INSERT INTO semantic_records (
                        target_id, item_id, node_id, item_type,
                        matched_text, as_of, available_at, source_kind,
                        confidence, tags_json, tree_id, embedding_model,
                        dimension, projection_hash, source_content_hash,
                        knowledge_schema_version,
                        projection_schema_version, item_canonical_hash,
                        embedding
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        target.target_id,
                        str(item.id),
                        str(target.node_id)
                        if target.node_id is not None
                        else None,
                        item.item_type,
                        target.text,
                        prepared.as_of,
                        prepared.available_at,
                        item.source.kind,
                        item.confidence,
                        prepared.tags_json,
                        str(target.tree_id)
                        if target.tree_id is not None
                        else None,
                        embedding_model,
                        dimension,
                        target.projection_hash,
                        item.source.content_hash,
                        item.schema_version,
                        _PROJECTION_SCHEMA_VERSION,
                        canonical.canonical_hash,
                        blob,
                    ),
                )
            self._db.execute("COMMIT")
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def get(self, item_id: UUID) -> BaseKnowledge:
        """Return validated canonical knowledge or report not-found/stale data."""
        row = self._db.execute(
            "SELECT * FROM knowledge_items WHERE item_id = ?",
            (str(item_id),),
        ).fetchone()
        if row is None:
            derived_count = int(
                self._db.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM semantic_records
                         WHERE item_id = ?)
                        +
                        (SELECT COUNT(*) FROM knowledge_nodes
                         WHERE item_id = ?)
                    """,
                    (str(item_id), str(item_id)),
                ).fetchone()[0]
            )
            if derived_count:
                raise RuntimeError(
                    f"Stale data for item '{item_id}': child records exist "
                    "without canonical knowledge"
                )
            raise KeyError(f"Knowledge item '{item_id}' not found")
        item_key = str(row["item_id"])
        node_rows = self._db.execute(
            """
            SELECT node_id, parent_id, position, payload_json, content_hash
            FROM knowledge_nodes
            WHERE item_id = ?
            ORDER BY node_id
            """,
            (item_key,),
        ).fetchall()
        payload = _assemble_canonical_payload(
            item_id=item_key,
            item_shape=str(row["item_shape"]),
            item_payload=str(row["payload_json"]),
            expected_node_count=int(row["node_count"]),
            node_records=[
                (
                    str(node_row["node_id"]),
                    (
                        str(node_row["parent_id"])
                        if node_row["parent_id"] is not None
                        else None
                    ),
                    int(node_row["position"]),
                    str(node_row["payload_json"]),
                    str(node_row["content_hash"]),
                )
                for node_row in node_rows
            ],
        )
        return _load_canonical(
            item_id=item_key,
            knowledge_class=str(row["knowledge_class"]),
            item_type=str(row["item_type"]),
            schema_version=str(row["schema_version"]),
            payload=payload,
            canonical_hash=str(row["canonical_hash"]),
        )

    def get_paper_source(self, source_revision_id: UUID) -> PaperSourceRevision:
        """Rehydrate one exact source revision and all referenced blobs."""
        row = self._db.execute(
            "SELECT * FROM paper_sources WHERE source_revision_id = ?",
            (str(source_revision_id),),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Paper source revision '{source_revision_id}' not found"
            )
        payload = str(row["payload_json"])
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != str(
            row["canonical_hash"]
        ):
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': content hash mismatch"
            )
        asset_rows = self._db.execute(
            """
            SELECT * FROM paper_source_assets
            WHERE source_revision_id = ? ORDER BY asset_id
            """,
            (str(source_revision_id),),
        ).fetchall()
        if len(asset_rows) != int(row["asset_count"]):
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': expected "
                f"{row['asset_count']} assets, found {len(asset_rows)}"
            )
        try:
            parsed_payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': invalid JSON"
            ) from exc
        if not isinstance(parsed_payload, dict):
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': invalid payload"
            )
        blobs: dict[str, bytes] = {}
        for asset_row in asset_rows:
            blob = bytes(asset_row["blob"])
            content_hash = str(asset_row["content_hash"])
            if (
                len(blob) != int(asset_row["size_bytes"])
                or hashlib.sha256(blob).hexdigest() != content_hash
            ):
                raise RuntimeError(
                    f"Corrupt paper asset '{asset_row['asset_id']}'"
                )
            blobs[content_hash] = blob
        parsed_payload["blobs"] = blobs
        try:
            source = PaperSourceRevision.model_validate(parsed_payload)
        except ValidationError as exc:
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': payload no longer "
                "validates"
            ) from exc
        if (
            source.id != source_revision_id
            or source.schema_version != str(row["schema_version"])
            or source.source.content_hash != str(row["source_content_hash"])
        ):
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': identity mismatch"
            )
        try:
            stored_assets = {
                UUID(str(asset_row["asset_id"])): asset_row
                for asset_row in asset_rows
            }
        except ValueError as exc:
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': invalid asset ID"
            ) from exc
        canonical_assets = {asset.asset_id: asset for asset in source.assets}
        if set(stored_assets) != set(canonical_assets):
            raise RuntimeError(
                f"Stale paper source '{source_revision_id}': asset identity "
                "mismatch"
            )
        for asset_id, asset in canonical_assets.items():
            stored = stored_assets[asset_id]
            if any(
                (
                    str(stored["source_revision_id"]) != str(source.id),
                    str(stored["kind"]) != asset.kind,
                    stored["page_number"] != asset.page_number,
                    str(stored["media_type"]) != asset.media_type,
                    str(stored["content_hash"]) != asset.content_hash,
                    int(stored["size_bytes"]) != asset.size_bytes,
                )
            ):
                raise RuntimeError(
                    f"Stale paper asset '{asset_id}': metadata mismatch"
                )
        return source

    def get_paper_artifact(self, artifact_id: UUID) -> PaperArtifact:
        """Rehydrate one validated paper artifact and normalized members."""
        row = self._db.execute(
            "SELECT * FROM paper_artifacts WHERE artifact_id = ?",
            (str(artifact_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"Paper artifact '{artifact_id}' not found")
        member_rows = self._db.execute(
            """
            SELECT * FROM paper_artifact_members
            WHERE artifact_id = ?
            ORDER BY COALESCE(parent_id, ''), position, member_id
            """,
            (str(artifact_id),),
        ).fetchall()
        if len(member_rows) != int(row["member_count"]):
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': expected "
                f"{row['member_count']} members, found {len(member_rows)}"
            )
        try:
            payload_value = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': invalid JSON"
            ) from exc
        if not isinstance(payload_value, dict):
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': invalid payload"
            )
        artifact_kind = str(row["artifact_kind"])
        members: list[object] = []
        for member_row in member_rows:
            member_payload = str(member_row["payload_json"])
            if hashlib.sha256(
                member_payload.encode("utf-8")
            ).hexdigest() != str(member_row["content_hash"]):
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': member content "
                    "hash mismatch"
                )
            try:
                parsed_member = json.loads(member_payload)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': invalid member JSON"
                ) from exc
            if not isinstance(parsed_member, dict):
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': member metadata "
                    "mismatch"
                )
            if artifact_kind == "paper_structure_tree":
                metadata_matches = all(
                    (
                        parsed_member.get("node_id") == member_row["member_id"],
                        parsed_member.get("parent_id")
                        == member_row["parent_id"],
                        parsed_member.get("position") == member_row["position"],
                    )
                )
            elif artifact_kind == "paper_annotation_set":
                metadata_matches = all(
                    (
                        parsed_member.get("annotation_id")
                        == member_row["member_id"],
                        parsed_member.get("annotation_set_id")
                        == str(artifact_id),
                        member_row["parent_id"] is None,
                        parsed_member.get("position") == member_row["position"],
                    )
                )
            elif artifact_kind == "paper_translation":
                metadata_matches = all(
                    (
                        parsed_member.get("page_id") == member_row["member_id"],
                        parsed_member.get("translation_id") == str(artifact_id),
                        member_row["parent_id"] is None,
                        parsed_member.get("position") == member_row["position"],
                    )
                )
            else:
                metadata_matches = all(
                    (
                        parsed_member.get("chunk_id")
                        == member_row["member_id"],
                        member_row["parent_id"] is None,
                        parsed_member.get("position") == member_row["position"],
                    )
                )
            if not metadata_matches:
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': member metadata "
                    "mismatch"
                )
            members.append(parsed_member)
        if artifact_kind == "paper_chunk_set":
            payload_value["chunks"] = members
            model: (
                type[PaperChunkSet]
                | type[PaperGlobalSummary]
                | type[PaperAnnotationSet]
                | type[PaperStructureTree]
                | type[PaperTranslation]
            ) = PaperChunkSet
        elif artifact_kind == "paper_summary":
            if members:
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': summary has members"
                )
            model = PaperGlobalSummary
        elif artifact_kind == "paper_annotation_set":
            payload_value["annotations"] = members
            model = PaperAnnotationSet
        elif artifact_kind == "paper_structure_tree":
            payload_value["nodes"] = {
                str(member["node_id"]): member
                for member in members
                if isinstance(member, dict)
            }
            model = PaperStructureTree
        elif artifact_kind == "paper_translation":
            payload_value["pages"] = members
            model = PaperTranslation
        else:
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': unsupported kind "
                f"'{artifact_kind}'"
            )
        full_payload = _json_payload(payload_value)
        if hashlib.sha256(full_payload.encode("utf-8")).hexdigest() != str(
            row["canonical_hash"]
        ):
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': canonical hash mismatch"
            )
        try:
            artifact = model.model_validate(payload_value)
        except ValidationError as exc:
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': payload no longer "
                "validates"
            ) from exc
        try:
            stored_source_revision_id = UUID(str(row["source_revision_id"]))
        except ValueError as exc:
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': invalid source ID"
            ) from exc
        if (
            artifact.id != artifact_id
            or artifact.source_revision_id != stored_source_revision_id
            or artifact.artifact_kind != artifact_kind
            or artifact.schema_version != str(row["schema_version"])
            or artifact.producer_config_hash != str(row["producer_config_hash"])
        ):
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': identity mismatch"
            )
        expected_target_count = (
            0
            if isinstance(
                artifact,
                (PaperStructureTree, PaperAnnotationSet, PaperTranslation),
            )
            else 1
            if isinstance(artifact, PaperGlobalSummary)
            else len(artifact.chunks)
        )
        if int(row["target_count"]) != expected_target_count:
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': target count mismatch"
            )
        if isinstance(artifact, PaperChunkSet):
            try:
                _validate_chunk_set_source(
                    self.get_paper_source(artifact.source_revision_id),
                    artifact,
                )
            except ValueError as exc:
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': source span "
                    "mismatch"
                ) from exc
        if isinstance(artifact, PaperTranslation):
            try:
                PaperTranslatedResult(
                    source_revision=self.get_paper_source(
                        artifact.source_revision_id
                    ),
                    translation=artifact,
                )
            except (KeyError, ValidationError, ValueError) as exc:
                raise RuntimeError(
                    f"Stale paper artifact '{artifact_id}': translation "
                    "source mismatch"
                ) from exc
        lineage_rows = self._db.execute(
            """
            SELECT input_artifact_id, relation
            FROM paper_artifact_lineage
            WHERE artifact_id = ?
            ORDER BY input_artifact_id, relation
            """,
            (str(artifact_id),),
        ).fetchall()
        expected_lineage = (
            {
                (str(locator.artifact_id), "generated_from")
                for locator in artifact.derived_from
            }
            if isinstance(artifact, (PaperGlobalSummary, PaperAnnotationSet))
            else set()
        )
        stored_lineage = {
            (str(lineage["input_artifact_id"]), str(lineage["relation"]))
            for lineage in lineage_rows
        }
        if stored_lineage != expected_lineage:
            raise RuntimeError(
                f"Stale paper artifact '{artifact_id}': lineage mismatch"
            )
        # A structure tree is self-contained: ``model_validate`` above already
        # ran its full integrity gate (topology, leaf content, citations) from
        # the tree's own value. Loading it never depends on a stored source.
        return artifact

    def get_paper_result(
        self,
        source_revision_id: UUID,
        *,
        chunk_set_id: UUID | None,
        summary_id: UUID | None,
    ) -> PaperSemanticResult:
        """Resolve one unambiguous V1 source/chunk-set/summary combination."""
        source = self.get_paper_source(source_revision_id)

        def select(kind: str, selected_id: UUID | None) -> PaperArtifact:
            if selected_id is not None:
                artifact = self.get_paper_artifact(selected_id)
                if (
                    artifact.artifact_kind != kind
                    or artifact.source_revision_id != source_revision_id
                ):
                    raise KeyError(
                        f"Paper artifact '{selected_id}' does not belong to "
                        f"source '{source_revision_id}'"
                    )
                return artifact
            rows = self._db.execute(
                """
                SELECT artifact_id FROM paper_artifacts
                WHERE source_revision_id = ? AND artifact_kind = ?
                ORDER BY artifact_id
                """,
                (str(source_revision_id), kind),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    f"Paper source '{source_revision_id}' has {len(rows)} "
                    f"'{kind}' artifacts; specify an artifact ID"
                )
            return self.get_paper_artifact(UUID(str(rows[0]["artifact_id"])))

        chunk_set = select("paper_chunk_set", chunk_set_id)
        summary = select("paper_summary", summary_id)
        if not isinstance(chunk_set, PaperChunkSet) or not isinstance(
            summary, PaperGlobalSummary
        ):
            raise RuntimeError("Stored paper artifact types are inconsistent")
        return PaperSemanticResult(
            source_revision=source,
            chunk_set=chunk_set,
            global_summary=summary,
        )

    def get_registration(
        self, registration_id: UUID
    ) -> PaperRegistrationRecord:
        """Return one validated atomic-registration audit record."""
        row = self._db.execute(
            """
            SELECT * FROM paper_registration_records
            WHERE registration_id = ?
            """,
            (str(registration_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"Paper registration '{registration_id}' not found")
        return _load_registration_row(row)

    def get_translation_registration(
        self,
        registration_id: UUID,
    ) -> PaperTranslationRegistrationRecord:
        """Return one validated immutable translation audit record."""
        row = self._db.execute(
            """
            SELECT * FROM paper_translation_registration_records
            WHERE registration_id = ?
            """,
            (str(registration_id),),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Paper translation registration '{registration_id}' not found"
            )
        return _load_translation_registration_row(row)

    def get_translation(
        self,
        translation_id: UUID,
    ) -> PaperTranslatedResult:
        """Load one translation together with its exact stored source."""
        artifact = self.get_paper_artifact(translation_id)
        if not isinstance(artifact, PaperTranslation):
            raise KeyError(
                f"Paper artifact '{translation_id}' is not a translation"
            )
        return PaperTranslatedResult(
            source_revision=self.get_paper_source(artifact.source_revision_id),
            translation=artifact,
        )

    def get_annotated_paper(
        self, registration_id: UUID
    ) -> PaperAnnotatedResult:
        """Rehydrate the exact four-part bundle named by a registration."""
        registration = self.get_registration(registration_id)
        source = self.get_paper_source(registration.source_revision_id)
        chunk_set = self.get_paper_artifact(registration.chunk_set_id)
        summary = self.get_paper_artifact(registration.summary_id)
        annotation_set = self.get_paper_artifact(registration.annotation_set_id)
        if (
            not isinstance(chunk_set, PaperChunkSet)
            or not isinstance(summary, PaperGlobalSummary)
            or not isinstance(annotation_set, PaperAnnotationSet)
        ):
            raise RuntimeError(
                f"Stale paper registration '{registration_id}': "
                "artifact types are inconsistent"
            )
        try:
            return PaperAnnotatedResult(
                source_revision=source,
                chunk_set=chunk_set,
                global_summary=summary,
                annotation_set=annotation_set,
            )
        except ValidationError as exc:
            raise RuntimeError(
                f"Stale paper registration '{registration_id}': "
                "artifact links are inconsistent"
            ) from exc

    def find_paper_source(self, content_hash: str) -> UUID | None:
        """Return the exact source ID for a SHA-256 hash, if present."""
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 value")
        row = self._db.execute(
            """
            SELECT source_revision_id FROM paper_sources
            WHERE source_content_hash = ?
            """,
            (content_hash,),
        ).fetchone()
        return UUID(str(row["source_revision_id"])) if row is not None else None

    def list_registrations(
        self,
        source_revision_id: UUID | None = None,
        *,
        limit: int = 100,
    ) -> tuple[PaperRegistrationRecord, ...]:
        """List newest registration evidence globally or for one source."""
        if not 1 <= limit <= 1_000:
            raise ValueError("registration limit must be between 1 and 1000")
        if source_revision_id is None:
            rows = self._db.execute(
                """
                SELECT * FROM paper_registration_records
                ORDER BY registered_at DESC, registration_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT * FROM paper_registration_records
                WHERE source_revision_id = ?
                ORDER BY registered_at DESC, registration_id DESC
                LIMIT ?
                """,
                (str(source_revision_id), limit),
            ).fetchall()
        return tuple(_load_registration_row(row) for row in rows)

    def list_translation_registrations(
        self,
        source_revision_id: UUID | None = None,
        *,
        limit: int = 100,
    ) -> tuple[PaperTranslationRegistrationRecord, ...]:
        """List newest translation registrations globally or by source."""
        if not 1 <= limit <= 1_000:
            raise ValueError(
                "translation registration limit must be between 1 and 1000"
            )
        if source_revision_id is None:
            rows = self._db.execute(
                """
                SELECT * FROM paper_translation_registration_records
                ORDER BY registered_at DESC, registration_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT * FROM paper_translation_registration_records
                WHERE source_revision_id = ?
                ORDER BY registered_at DESC, registration_id DESC
                LIMIT ?
                """,
                (str(source_revision_id), limit),
            ).fetchall()
        return tuple(_load_translation_registration_row(row) for row in rows)

    def _paper_catalog_entry(self, row: sqlite3.Row) -> PaperCatalogEntry:
        """Build one fast source-level health projection from aggregate rows."""
        source_id = UUID(str(row["source_revision_id"]))
        try:
            authors_value = json.loads(str(row["authors_text"]))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Stale paper catalog '{source_id}': invalid authors"
            ) from exc
        if not isinstance(authors_value, list) or not all(
            isinstance(author, str) for author in authors_value
        ):
            raise RuntimeError(
                f"Stale paper catalog '{source_id}': invalid authors"
            )

        registration_count = int(row["registration_count"])
        latest_registration: PaperRegistrationRecord | None = None
        reasons: list[str] = []
        broken = False
        latest_id = row["latest_registration_id"]
        if registration_count == 0 or latest_id is None:
            broken = True
            reasons.append("missing_registration")
        else:
            try:
                latest_registration = self.get_registration(
                    UUID(str(latest_id))
                )
            except (KeyError, RuntimeError, ValueError):
                broken = True
                reasons.append("invalid_registration")

        if latest_registration is not None:
            if latest_registration.passed_checks != _REGISTRATION_CHECKS:
                broken = True
                reasons.append("incomplete_registration_checks")
            registered_artifacts = self._db.execute(
                """
                SELECT artifact_id, artifact_kind, member_count, target_count
                FROM paper_artifacts
                WHERE artifact_id IN (?, ?, ?)
                """,
                (
                    str(latest_registration.chunk_set_id),
                    str(latest_registration.summary_id),
                    str(latest_registration.annotation_set_id),
                ),
            ).fetchall()
            if len(registered_artifacts) != 3:
                broken = True
                reasons.append("missing_registered_artifact")
            for artifact_row in registered_artifacts:
                artifact_id = str(artifact_row["artifact_id"])
                member_count = int(artifact_row["member_count"])
                target_count = int(artifact_row["target_count"])
                actual_members = int(
                    self._db.execute(
                        """
                        SELECT COUNT(*) FROM paper_artifact_members
                        WHERE artifact_id = ?
                        """,
                        (artifact_id,),
                    ).fetchone()[0]
                )
                projection_rows = self._db.execute(
                    """
                    SELECT embedding_model, dimension
                    FROM paper_projections WHERE artifact_id = ?
                    """,
                    (artifact_id,),
                ).fetchall()
                if (
                    actual_members != member_count
                    or len(projection_rows) != target_count
                ):
                    broken = True
                    reasons.append("artifact_count_mismatch")
                if any(
                    str(projection["embedding_model"])
                    != latest_registration.embedding_model
                    or int(projection["dimension"])
                    != latest_registration.embedding_dimensions
                    for projection in projection_rows
                ):
                    broken = True
                    reasons.append("embedding_identity_mismatch")

        if self._db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            broken = True
            reasons.append("foreign_key_violation")
        if not broken:
            if row["title"] is None:
                reasons.append("missing_title")
            if row["published_at"] is None:
                reasons.append("missing_published_at")
            if int(row["empty_page_count"]):
                reasons.append("empty_pages")
            if int(row["artifact_version_count"]) > 3:
                reasons.append("multiple_artifact_versions")
        health = "broken" if broken else "attention" if reasons else "ready"
        return PaperCatalogEntry(
            source_revision_id=source_id,
            source_content_hash=str(row["source_content_hash"]),
            title=(str(row["title"]) if row["title"] is not None else None),
            authors=tuple(authors_value),
            published_at=(
                datetime.fromtimestamp(float(row["published_at"]), timezone.utc)
                if row["published_at"] is not None
                else None
            ),
            available_at=datetime.fromtimestamp(
                float(row["available_at"]), timezone.utc
            ),
            source_kind=str(row["source_kind"]),
            source_uri=str(row["source_uri"]),
            page_count=int(row["page_count"]),
            empty_page_count=int(row["empty_page_count"]),
            chunk_count=int(row["chunk_count"]),
            annotation_count=int(row["annotation_count"]),
            translation_count=int(row["translation_count"]),
            registration_count=registration_count,
            latest_registered_at=(
                latest_registration.registered_at
                if latest_registration is not None
                else None
            ),
            embedding_model=(
                latest_registration.embedding_model
                if latest_registration is not None
                else None
            ),
            embedding_dimensions=(
                latest_registration.embedding_dimensions
                if latest_registration is not None
                else None
            ),
            health=health,
            health_reasons=tuple(dict.fromkeys(reasons)),
        )

    def _all_catalog_entries(self) -> list[PaperCatalogEntry]:
        rows = self._db.execute(
            """
            SELECT c.*,
                COALESCE((
                    SELECT SUM(a.member_count) FROM paper_artifacts AS a
                    WHERE a.source_revision_id = c.source_revision_id
                      AND a.artifact_kind = 'paper_chunk_set'
                ), 0) AS chunk_count,
                COALESCE((
                    SELECT SUM(a.member_count) FROM paper_artifacts AS a
                    WHERE a.source_revision_id = c.source_revision_id
                      AND a.artifact_kind = 'paper_annotation_set'
                ), 0) AS annotation_count,
                (SELECT COUNT(*) FROM paper_artifacts AS a
                 WHERE a.source_revision_id = c.source_revision_id
                   AND a.artifact_kind = 'paper_translation')
                    AS translation_count,
                (SELECT COUNT(*) FROM paper_artifacts AS a
                 WHERE a.source_revision_id = c.source_revision_id
                   AND a.artifact_kind IN (
                       'paper_chunk_set', 'paper_summary',
                       'paper_annotation_set'
                   ))
                    AS artifact_version_count,
                (SELECT COUNT(*) FROM paper_registration_records AS r
                 WHERE r.source_revision_id = c.source_revision_id)
                    AS registration_count,
                (SELECT r.registration_id
                 FROM paper_registration_records AS r
                 WHERE r.source_revision_id = c.source_revision_id
                 ORDER BY r.registered_at DESC, r.registration_id DESC
                 LIMIT 1) AS latest_registration_id
            FROM paper_catalog AS c
            """
        ).fetchall()
        return [self._paper_catalog_entry(row) for row in rows]

    @staticmethod
    def _catalog_signature(query: PaperCatalogQuery) -> str:
        payload = _json_payload(
            query.model_dump(
                mode="json", exclude={"cursor", "limit"}, exclude_none=False
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _catalog_sort_value(
        entry: PaperCatalogEntry, sort: str
    ) -> str | float | None:
        if sort == "registered_desc":
            return (
                entry.latest_registered_at.timestamp()
                if entry.latest_registered_at is not None
                else None
            )
        if sort == "published_desc":
            return (
                entry.published_at.timestamp()
                if entry.published_at is not None
                else None
            )
        return entry.title.casefold() if entry.title is not None else None

    def list_papers(
        self, query: PaperCatalogQuery | None = None
    ) -> PaperCatalogPage:
        """List filtered paper sources with stable opaque cursor pagination."""
        selected_query = query or PaperCatalogQuery()
        entries = self._all_catalog_entries()
        text = selected_query.text.casefold() if selected_query.text else None
        if text is not None:
            entries = [
                entry
                for entry in entries
                if text
                in " ".join(
                    (
                        entry.title or "",
                        " ".join(entry.authors),
                        entry.source_uri,
                    )
                ).casefold()
            ]
        if selected_query.source_kinds:
            source_kinds = set(selected_query.source_kinds)
            entries = [
                entry for entry in entries if entry.source_kind in source_kinds
            ]
        if selected_query.published_from is not None:
            entries = [
                entry
                for entry in entries
                if entry.published_at is not None
                and entry.published_at >= selected_query.published_from
            ]
        if selected_query.published_to is not None:
            entries = [
                entry
                for entry in entries
                if entry.published_at is not None
                and entry.published_at <= selected_query.published_to
            ]
        if selected_query.health is not None:
            entries = [
                entry
                for entry in entries
                if entry.health == selected_query.health
            ]
        if selected_query.sort == "title_asc":
            entries.sort(
                key=lambda entry: (
                    entry.title is None,
                    (entry.title or "").casefold(),
                    str(entry.source_revision_id),
                )
            )
        elif selected_query.sort == "published_desc":
            entries.sort(
                key=lambda entry: (
                    entry.published_at is None,
                    -(
                        entry.published_at.timestamp()
                        if entry.published_at is not None
                        else 0.0
                    ),
                    str(entry.source_revision_id),
                )
            )
        else:
            entries.sort(
                key=lambda entry: (
                    entry.latest_registered_at is None,
                    -(
                        entry.latest_registered_at.timestamp()
                        if entry.latest_registered_at is not None
                        else 0.0
                    ),
                    str(entry.source_revision_id),
                )
            )
        total_count = len(entries)
        start = 0
        signature = self._catalog_signature(selected_query)
        if selected_query.cursor is not None:
            try:
                cursor_payload = json.loads(
                    urlsafe_b64decode(
                        selected_query.cursor.encode("ascii")
                    ).decode("utf-8")
                )
                last_id = str(cursor_payload["last_id"])
                last_sort = cursor_payload["last_sort"]
                if cursor_payload["signature"] != signature:
                    raise ValueError
            except (
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                raise ValueError("catalog cursor does not match query") from exc
            for position, entry in enumerate(entries):
                if (
                    str(entry.source_revision_id) == last_id
                    and self._catalog_sort_value(entry, selected_query.sort)
                    == last_sort
                ):
                    start = position + 1
                    break
            else:
                raise ValueError("catalog cursor no longer resolves")
        page_entries = entries[start : start + selected_query.limit]
        next_cursor: str | None = None
        if start + len(page_entries) < total_count and page_entries:
            last = page_entries[-1]
            cursor_value = _json_payload(
                {
                    "last_id": str(last.source_revision_id),
                    "last_sort": self._catalog_sort_value(
                        last, selected_query.sort
                    ),
                    "signature": signature,
                }
            )
            next_cursor = urlsafe_b64encode(
                cursor_value.encode("utf-8")
            ).decode("ascii")
        return PaperCatalogPage(
            entries=tuple(page_entries),
            next_cursor=next_cursor,
            total_count=total_count,
        )

    def get_paper_details(
        self,
        source_revision_id: UUID,
        *,
        registration_id: UUID | None,
    ) -> PaperDetails:
        """Return deep-validated canonical values for one catalog source."""
        source = self.get_paper_source(source_revision_id)
        registrations = self.list_registrations(source_revision_id, limit=1_000)
        translation_registrations = self.list_translation_registrations(
            source_revision_id,
            limit=1_000,
        )
        if registration_id is not None and all(
            record.registration_id != registration_id
            for record in registrations
        ):
            raise KeyError(
                f"Paper registration '{registration_id}' does not belong to "
                f"source '{source_revision_id}'"
            )
        rows = self._db.execute(
            """
            SELECT artifact_id FROM paper_artifacts
            WHERE source_revision_id = ?
              AND artifact_kind IN (
                  'paper_chunk_set', 'paper_summary', 'paper_annotation_set',
                  'paper_translation'
              )
            ORDER BY artifact_kind, artifact_id
            """,
            (str(source_revision_id),),
        ).fetchall()
        artifacts = [
            self.get_paper_artifact(UUID(str(row["artifact_id"])))
            for row in rows
        ]
        entry = next(
            (
                value
                for value in self._all_catalog_entries()
                if value.source_revision_id == source_revision_id
            ),
            None,
        )
        if entry is None:
            raise RuntimeError(
                f"Stale paper catalog '{source_revision_id}': row missing"
            )
        return PaperDetails(
            source=source,
            registrations=registrations,
            chunk_sets=tuple(
                artifact
                for artifact in artifacts
                if isinstance(artifact, PaperChunkSet)
            ),
            summaries=tuple(
                artifact
                for artifact in artifacts
                if isinstance(artifact, PaperGlobalSummary)
            ),
            annotation_sets=tuple(
                artifact
                for artifact in artifacts
                if isinstance(artifact, PaperAnnotationSet)
            ),
            translations=tuple(
                artifact
                for artifact in artifacts
                if isinstance(artifact, PaperTranslation)
            ),
            translation_registrations=translation_registrations,
            selected_registration_id=registration_id,
            health=entry.health,
            health_reasons=entry.health_reasons,
        )

    def get_paper_asset(
        self, source_revision_id: UUID, asset_id: UUID
    ) -> PaperAssetPayload:
        """Return exact validated bytes for one persisted source asset."""
        source = self.get_paper_source(source_revision_id)
        asset = next(
            (value for value in source.assets if value.asset_id == asset_id),
            None,
        )
        if asset is None:
            raise KeyError(
                f"Paper asset '{asset_id}' not found for source "
                f"'{source_revision_id}'"
            )
        suffix = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
        }.get(asset.media_type, ".bin")
        filename = (
            "source.pdf"
            if asset.kind == "raw"
            else f"page-{asset.page_number or 0}-{asset.kind}{suffix}"
        )
        return PaperAssetPayload(
            source_revision_id=source_revision_id,
            asset_id=asset.asset_id,
            media_type=asset.media_type,
            content_hash=asset.content_hash,
            filename=filename,
            content=source.blob_for(asset.asset_id),
        )

    def inspect_library(self) -> PaperLibraryStats:
        """Return source-level dashboard statistics without loading models."""
        entries = self._all_catalog_entries()
        database_size = (
            self._database_path.stat().st_size
            if self._database_path is not None and self._database_path.exists()
            else None
        )
        return PaperLibraryStats(
            source_revision_count=len(entries),
            search_ready_count=sum(
                entry.health == "ready" for entry in entries
            ),
            attention_count=sum(
                entry.health == "attention" for entry in entries
            ),
            broken_count=sum(entry.health == "broken" for entry in entries),
            total_pages=sum(entry.page_count for entry in entries),
            total_annotations=sum(entry.annotation_count for entry in entries),
            total_translations=sum(
                entry.translation_count for entry in entries
            ),
            database_size_bytes=database_size,
        )

    def resolve_paper_locator(
        self, locator: ArtifactLocator
    ) -> ResolvedPaperArtifact:
        """Resolve an artifact locator to its canonical aggregate or member."""
        artifact = self.get_paper_artifact(locator.artifact_id)
        if (
            artifact.source_revision_id != locator.source_revision_id
            or artifact.artifact_kind != locator.artifact_kind
        ):
            raise KeyError(
                "Paper artifact locator metadata does not match storage"
            )
        if locator.member_id is None:
            return artifact
        if isinstance(artifact, PaperStructureTree):
            try:
                return artifact.nodes[locator.member_id]
            except KeyError as exc:
                raise KeyError(
                    f"Paper structure node '{locator.member_id}' not found"
                ) from exc
        if isinstance(artifact, PaperAnnotationSet):
            for annotation in artifact.annotations:
                if annotation.annotation_id == locator.member_id:
                    return annotation
            raise KeyError(f"Paper annotation '{locator.member_id}' not found")
        if isinstance(artifact, PaperTranslation):
            for page in artifact.pages:
                if page.page_id == locator.member_id:
                    return page
            raise KeyError(
                f"Paper translation page '{locator.member_id}' not found"
            )
        if not isinstance(artifact, PaperChunkSet):
            raise KeyError("Paper artifact does not have resolvable members")
        for chunk in artifact.chunks:
            if chunk.chunk_id == locator.member_id:
                return chunk
        raise KeyError(f"Paper chunk '{locator.member_id}' not found")

    def delete(self, item_id: UUID) -> None:
        """Transactionally remove canonical knowledge and every child record."""
        exists = self._db.execute(
            "SELECT 1 FROM knowledge_items WHERE item_id = ?",
            (str(item_id),),
        ).fetchone()
        if exists is None:
            derived_count = int(
                self._db.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM semantic_records
                         WHERE item_id = ?)
                        +
                        (SELECT COUNT(*) FROM knowledge_nodes
                         WHERE item_id = ?)
                    """,
                    (str(item_id), str(item_id)),
                ).fetchone()[0]
            )
            if derived_count:
                raise RuntimeError(
                    f"Stale data for item '{item_id}': cannot delete child "
                    "records without canonical knowledge"
                )
            raise KeyError(f"Knowledge item '{item_id}' not found")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                "DELETE FROM semantic_records WHERE item_id = ?",
                (str(item_id),),
            )
            self._db.execute(
                "DELETE FROM knowledge_nodes WHERE item_id = ?",
                (str(item_id),),
            )
            self._db.execute(
                "DELETE FROM knowledge_items WHERE item_id = ?",
                (str(item_id),),
            )
            self._db.execute("COMMIT")
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def load_index_records(
        self,
        *,
        embedding_model: str,
        embedding_dimensions: int | None,
    ) -> list[_IndexRecord]:
        """Validate SQLite relationships and load typed exact-index records."""
        orphan = self._db.execute(
            """
            SELECT r.item_id
            FROM semantic_records AS r
            LEFT JOIN knowledge_items AS i ON i.item_id = r.item_id
            WHERE i.item_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan is not None:
            raise RuntimeError(
                f"Stale index data for item '{orphan['item_id']}': "
                "derived records exist without canonical knowledge"
            )
        orphan_node = self._db.execute(
            """
            SELECT n.item_id
            FROM knowledge_nodes AS n
            LEFT JOIN knowledge_items AS i ON i.item_id = n.item_id
            WHERE i.item_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan_node is not None:
            raise RuntimeError(
                f"Stale canonical knowledge for item '{orphan_node['item_id']}': "
                "tree nodes exist without an aggregate root"
            )
        incomplete_tree = self._db.execute(
            """
            SELECT i.item_id, i.item_shape, i.node_count,
                   COUNT(n.node_id) AS actual_count
            FROM knowledge_items AS i
            LEFT JOIN knowledge_nodes AS n ON n.item_id = i.item_id
            GROUP BY i.item_id, i.item_shape, i.node_count
            HAVING COUNT(n.node_id) != i.node_count
                OR (i.item_shape = 'flat' AND i.node_count != 0)
                OR (i.item_shape = 'tree' AND i.node_count = 0)
            LIMIT 1
            """
        ).fetchone()
        if incomplete_tree is not None:
            raise RuntimeError(
                f"Stale canonical knowledge for item "
                f"'{incomplete_tree['item_id']}': expected "
                f"{incomplete_tree['node_count']} tree nodes, found "
                f"{incomplete_tree['actual_count']}"
            )
        incomplete = self._db.execute(
            """
            SELECT i.item_id, i.target_count, COUNT(r.target_id) AS actual_count
            FROM knowledge_items AS i
            LEFT JOIN semantic_records AS r ON r.item_id = i.item_id
            GROUP BY i.item_id, i.target_count
            HAVING COUNT(r.target_id) != i.target_count
            LIMIT 1
            """
        ).fetchone()
        if incomplete is not None:
            raise RuntimeError(
                f"Stale index data for item '{incomplete['item_id']}': expected "
                f"{incomplete['target_count']} targets, found "
                f"{incomplete['actual_count']}"
            )

        rows = self._db.execute(
            """
            SELECT r.*, i.canonical_hash AS current_canonical_hash,
                   i.schema_version AS current_schema_version
            FROM semantic_records AS r
            JOIN knowledge_items AS i ON i.item_id = r.item_id
            ORDER BY r.target_id
            """
        ).fetchall()
        records: list[_IndexRecord] = []
        for row in rows:
            target_id = str(row["target_id"])
            dimension = int(row["dimension"])
            if str(row["embedding_model"]) != embedding_model:
                raise RuntimeError(
                    f"Stale index data for target '{target_id}': embedding model "
                    "changed; re-put the canonical item"
                )
            if (
                embedding_dimensions is not None
                and dimension != embedding_dimensions
            ):
                raise RuntimeError(
                    f"Stale index data for target '{target_id}': embedding "
                    "dimension changed; re-put the canonical item"
                )
            if (
                str(row["projection_schema_version"])
                != _PROJECTION_SCHEMA_VERSION
                or str(row["knowledge_schema_version"])
                != str(row["current_schema_version"])
                or str(row["item_canonical_hash"])
                != str(row["current_canonical_hash"])
            ):
                raise RuntimeError(
                    f"Stale index data for target '{target_id}': projection or "
                    "canonical metadata changed; re-put the canonical item"
                )
            try:
                parsed_tags = json.loads(str(row["tags_json"]))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Corrupt index data for target '{target_id}': invalid tags"
                ) from exc
            if not isinstance(parsed_tags, list) or not all(
                isinstance(tag, str) for tag in parsed_tags
            ):
                raise RuntimeError(
                    f"Corrupt index data for target '{target_id}': invalid tags"
                )
            node_value = row["node_id"]
            tree_value = row["tree_id"]
            try:
                record = _IndexRecord(
                    target_id=target_id,
                    owner_kind="knowledge",
                    item_id=UUID(str(row["item_id"])),
                    node_id=UUID(str(node_value)) if node_value else None,
                    item_type=str(row["item_type"]),
                    source_revision_id=None,
                    artifact_kind=str(row["item_type"]),
                    projection_kind="text_embedding",
                    projection_version=str(row["projection_schema_version"]),
                    embedding_model=str(row["embedding_model"]),
                    projection_hash=str(row["projection_hash"]),
                    matched_text=str(row["matched_text"]),
                    as_of=float(row["as_of"]),
                    available_at=(
                        float(row["available_at"])
                        if row["available_at"] is not None
                        else None
                    ),
                    source_kind=str(row["source_kind"]),
                    confidence=str(row["confidence"]),
                    tags=frozenset(parsed_tags),
                    tree_id=UUID(str(tree_value)) if tree_value else None,
                    dimension=dimension,
                    embedding=bytes(row["embedding"]),
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Corrupt index data for target '{target_id}': invalid metadata"
                ) from exc
            records.append(record)

        orphan_paper = self._db.execute(
            """
            SELECT p.target_id FROM paper_projections AS p
            LEFT JOIN paper_sources AS s
                ON s.source_revision_id = p.source_revision_id
            LEFT JOIN paper_artifacts AS a ON a.artifact_id = p.artifact_id
            WHERE s.source_revision_id IS NULL OR a.artifact_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan_paper is not None:
            raise RuntimeError(
                f"Stale paper projection '{orphan_paper['target_id']}': "
                "source or artifact is missing"
            )
        orphan_member = self._db.execute(
            """
            SELECT p.target_id FROM paper_projections AS p
            LEFT JOIN paper_artifact_members AS m
                ON m.artifact_id = p.artifact_id
               AND m.member_id = p.member_id
            WHERE p.member_id IS NOT NULL AND m.member_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan_member is not None:
            raise RuntimeError(
                f"Stale paper projection '{orphan_member['target_id']}': "
                "artifact member is missing"
            )
        incomplete_artifact = self._db.execute(
            """
            SELECT a.artifact_id, a.member_count,
                   COUNT(DISTINCT m.member_id) AS actual_members,
                   a.target_count,
                   COUNT(DISTINCT p.target_id) AS actual_targets
            FROM paper_artifacts AS a
            LEFT JOIN paper_artifact_members AS m
                ON m.artifact_id = a.artifact_id
            LEFT JOIN paper_projections AS p
                ON p.artifact_id = a.artifact_id
            GROUP BY a.artifact_id, a.member_count, a.target_count
            HAVING actual_members != a.member_count
                OR actual_targets != a.target_count
            LIMIT 1
            """
        ).fetchone()
        if incomplete_artifact is not None:
            raise RuntimeError(
                f"Stale paper artifact '{incomplete_artifact['artifact_id']}': "
                f"expected {incomplete_artifact['member_count']} members and "
                f"{incomplete_artifact['target_count']} projections"
            )
        paper_rows = self._db.execute(
            """
            SELECT p.*, a.canonical_hash AS current_canonical_hash,
                   a.schema_version AS current_schema_version,
                   s.source_content_hash AS current_source_content_hash
            FROM paper_projections AS p
            JOIN paper_artifacts AS a ON a.artifact_id = p.artifact_id
            JOIN paper_sources AS s
                ON s.source_revision_id = p.source_revision_id
            ORDER BY p.target_id
            """
        ).fetchall()
        paper_artifacts: dict[UUID, PaperArtifact] = {}
        paper_sources: dict[UUID, PaperSourceRevision] = {}
        for row in paper_rows:
            target_id = str(row["target_id"])
            dimension = int(row["dimension"])
            if str(row["embedding_model"]) != embedding_model:
                raise RuntimeError(
                    f"Stale paper projection '{target_id}': embedding model "
                    "changed; re-put the paper result"
                )
            if (
                embedding_dimensions is not None
                and dimension != embedding_dimensions
            ):
                raise RuntimeError(
                    f"Stale paper projection '{target_id}': embedding dimension "
                    "changed; re-put the paper result"
                )
            if (
                str(row["projection_kind"]) != "text_embedding"
                or str(row["modality"]) != "text"
                or str(row["projection_schema_version"])
                != _PROJECTION_SCHEMA_VERSION
                or str(row["artifact_schema_version"])
                != str(row["current_schema_version"])
                or str(row["artifact_canonical_hash"])
                != str(row["current_canonical_hash"])
                or str(row["source_content_hash"])
                != str(row["current_source_content_hash"])
            ):
                raise RuntimeError(
                    f"Stale paper projection '{target_id}': projection, source, "
                    "or artifact metadata changed; re-put the paper result"
                )
            try:
                artifact_id = UUID(str(row["artifact_id"]))
                source_id = UUID(str(row["source_revision_id"]))
                member_id = (
                    UUID(str(row["member_id"]))
                    if row["member_id"] is not None
                    else None
                )
                citations_value = json.loads(str(row["citations_json"]))
                if not isinstance(citations_value, list):
                    raise ValueError
                stored_citations = [
                    Citation.model_validate(citation_value)
                    for citation_value in citations_value
                ]
            except (TypeError, ValueError, ValidationError) as exc:
                raise RuntimeError(
                    f"Corrupt paper projection '{target_id}': invalid metadata"
                ) from exc

            artifact = paper_artifacts.get(artifact_id)
            if artifact is None:
                artifact = self.get_paper_artifact(artifact_id)
                paper_artifacts[artifact_id] = artifact
            source = paper_sources.get(source_id)
            if source is None:
                source = self.get_paper_source(source_id)
                paper_sources[source_id] = source
            artifact_kind = str(row["artifact_kind"])
            if (
                artifact.source_revision_id != source_id
                or artifact.artifact_kind != artifact_kind
                or source.source.kind != str(row["source_kind"])
                or _timestamp(source.as_of, "PaperSourceRevision.as_of")
                != float(row["as_of"])
                or _timestamp(
                    source.available_at,
                    "PaperSourceRevision.available_at",
                )
                != float(row["available_at"])
            ):
                raise RuntimeError(
                    f"Stale paper projection '{target_id}': canonical locator "
                    "or source evidence mismatch"
                )

            if member_id is None:
                if not isinstance(artifact, PaperGlobalSummary):
                    raise RuntimeError(
                        f"Stale paper projection '{target_id}': chunk-set "
                        "aggregate is not searchable"
                    )
                expected_target_id = f"artifact:{artifact.id}"
                expected_text = artifact.summary
                expected_citations = [
                    Citation(
                        source_id=str(source_id),
                        page=citation.page_number,
                        quote=citation.quote,
                    )
                    for citation in artifact.citations
                ]
            else:
                if not isinstance(artifact, PaperChunkSet):
                    raise RuntimeError(
                        f"Stale paper projection '{target_id}': summary has a "
                        "search member"
                    )
                chunk = next(
                    (
                        value
                        for value in artifact.chunks
                        if value.chunk_id == member_id
                    ),
                    None,
                )
                if chunk is None:
                    raise RuntimeError(
                        f"Stale paper projection '{target_id}': canonical "
                        "chunk is missing"
                    )
                expected_target_id = (
                    f"artifact-member:{artifact.id}:{chunk.chunk_id}"
                )
                expected_text = chunk.text
                expected_citations = [
                    Citation(
                        source_id=str(source_id),
                        page=span.page_number,
                        char_offset=span.start_char,
                        quote=chunk.text[:500],
                    )
                    for span in chunk.source_spans
                ]
            expected_projection_hash = hashlib.sha256(
                expected_text.encode("utf-8")
            ).hexdigest()
            if any(
                (
                    target_id != expected_target_id,
                    str(row["matched_text"]) != expected_text,
                    str(row["projection_hash"]) != expected_projection_hash,
                    stored_citations != expected_citations,
                )
            ):
                raise RuntimeError(
                    f"Stale paper projection '{target_id}': canonical text, "
                    "hash, or citations mismatch"
                )
            record = _IndexRecord(
                target_id=target_id,
                owner_kind="paper",
                item_id=artifact_id,
                node_id=member_id,
                item_type=artifact_kind,
                source_revision_id=source_id,
                artifact_kind=artifact_kind,
                projection_kind=str(row["projection_kind"]),
                projection_version=str(row["projection_schema_version"]),
                embedding_model=str(row["embedding_model"]),
                projection_hash=expected_projection_hash,
                matched_text=expected_text,
                as_of=float(row["as_of"]),
                available_at=float(row["available_at"]),
                source_kind=str(row["source_kind"]),
                confidence="high",
                tags=frozenset(),
                tree_id=None,
                dimension=dimension,
                embedding=bytes(row["embedding"]),
            )
            records.append(record)
        return records

    def close(self) -> None:
        """Close the owned SQLite connection."""
        self._db.close()
