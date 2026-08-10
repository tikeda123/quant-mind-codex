"""Public domain types for semantic retrieval and paper management."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from quantmind.knowledge import (
    ArtifactLocator,
    Citation,
    PaperAnnotationSet,
    PaperArtifactKind,
    PaperChunkSet,
    PaperGlobalSummary,
    PaperSourceRevision,
    PaperTranslation,
    SourceRef,
)

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

_TRANSLATION_REGISTRATION_CHECKS = (
    "source_hash",
    "page_sequence",
    "source_page_text",
    "complete_translation",
    "sqlite_constraints",
)


class SemanticQuery(BaseModel):
    """A financial-time-aware semantic query over canonical knowledge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    artifact_kinds: list[PaperArtifactKind] | None = None
    item_types: list[str] | None = None
    source_kinds: (
        list[
            Literal[
                "arxiv",
                "http",
                "doi",
                "local",
                "rss",
                "transcript",
                "manual",
            ]
        ]
        | None
    ) = None
    confidence: Literal["low", "medium", "high"] | None = None
    tags: list[str] | None = None
    tree_id: UUID | None = None
    as_of_before: datetime | None = None
    available_at_before: datetime | None = None
    source_revision_ids: tuple[UUID, ...] | None = None
    top_k: int = Field(default=10, ge=1)

    @field_validator("text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query text must not be blank")
        return stripped

    @field_validator("as_of_before", "available_at_before")
    @classmethod
    def _cutoffs_must_be_timezone_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("financial-time cutoffs must be timezone-aware")
        return value


class SearchProjection(BaseModel):
    """Rebuildable projection details used to rank one semantic hit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["text_embedding"] = "text_embedding"
    version: str
    modality: Literal["text"] = "text"
    model: str
    dimensions: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SemanticHit(BaseModel):
    """Auditable evidence returned by semantic ranking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: ArtifactLocator
    projection: SearchProjection
    item_id: UUID
    node_id: UUID | None
    item_type: str
    score: float
    matched_text: str
    as_of: datetime
    available_at: datetime | None
    source: SourceRef
    citations: list[Citation]


def _registration_content_hash(value: "PaperRegistrationRecord") -> str:
    payload = json.dumps(
        value.model_dump(mode="json", exclude={"canonical_hash"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PaperRegistrationRecord(BaseModel):
    """Immutable audit evidence for one atomic annotated-paper registration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: UUID
    schema_version: Literal["1.0"] = "1.0"
    registered_at: datetime
    source_revision_id: UUID
    chunk_set_id: UUID
    summary_id: UUID
    annotation_set_id: UUID
    pdf_size_bytes: int = Field(gt=0)
    page_count: int = Field(gt=0)
    empty_pages: tuple[int, ...]
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_dimensions: int = Field(gt=0)
    passed_checks: tuple[str, ...]
    canonical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("registered_at")
    @classmethod
    def _registered_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registered_at must be timezone-aware")
        return value

    @field_validator("passed_checks")
    @classmethod
    def _checks_are_complete(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _REGISTRATION_CHECKS:
            raise ValueError("paper registration checks are incomplete")
        return value

    @model_validator(mode="after")
    def _canonical_hash_matches(self) -> "PaperRegistrationRecord":
        if self.canonical_hash != _registration_content_hash(self):
            raise ValueError("paper registration canonical hash mismatch")
        return self


def _translation_registration_content_hash(
    value: "PaperTranslationRegistrationRecord",
) -> str:
    payload = json.dumps(
        value.model_dump(mode="json", exclude={"canonical_hash"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PaperTranslationRegistrationRecord(BaseModel):
    """Immutable audit evidence for one atomic translation registration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: UUID
    schema_version: Literal["1.0"] = "1.0"
    registered_at: datetime
    source_revision_id: UUID
    translation_id: UUID
    page_count: int = Field(gt=0)
    source_language: Literal["en"] = "en"
    target_language: Literal["ja"] = "ja"
    passed_checks: tuple[str, ...]
    canonical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("registered_at")
    @classmethod
    def _registered_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registered_at must be timezone-aware")
        return value

    @field_validator("passed_checks")
    @classmethod
    def _checks_are_complete(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _TRANSLATION_REGISTRATION_CHECKS:
            raise ValueError("translation registration checks are incomplete")
        return value

    @model_validator(mode="after")
    def _canonical_hash_matches(self) -> "PaperTranslationRegistrationRecord":
        if self.canonical_hash != _translation_registration_content_hash(self):
            raise ValueError(
                "paper translation registration canonical hash mismatch"
            )
        return self


class PaperCatalogQuery(BaseModel):
    """Bounded filters and keyset pagination for the paper catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = None
    source_kinds: tuple[str, ...] = ()
    published_from: datetime | None = None
    published_to: datetime | None = None
    health: Literal["ready", "attention", "broken"] | None = None
    sort: Literal["registered_desc", "published_desc", "title_asc"] = (
        "registered_desc"
    )
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None

    @field_validator("text")
    @classmethod
    def _optional_text_is_trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("published_from", "published_to")
    @classmethod
    def _dates_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("catalog date filters must be timezone-aware")
        return value


class PaperCatalogEntry(BaseModel):
    """One source-level management projection with aggregate counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision_id: UUID
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str | None
    authors: tuple[str, ...]
    published_at: datetime | None
    available_at: datetime
    source_kind: str
    source_uri: str
    page_count: int = Field(gt=0)
    empty_page_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    annotation_count: int = Field(ge=0)
    translation_count: int = Field(ge=0)
    registration_count: int = Field(ge=0)
    latest_registered_at: datetime | None
    embedding_model: str | None
    embedding_dimensions: int | None
    health: Literal["ready", "attention", "broken"]
    health_reasons: tuple[str, ...]


class PaperCatalogPage(BaseModel):
    """One bounded keyset page and the total filtered source count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[PaperCatalogEntry, ...]
    next_cursor: str | None
    total_count: int = Field(ge=0)


class PaperDetails(BaseModel):
    """Canonical paper values and audit history for one source revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: PaperSourceRevision
    registrations: tuple[PaperRegistrationRecord, ...]
    chunk_sets: tuple[PaperChunkSet, ...]
    summaries: tuple[PaperGlobalSummary, ...]
    annotation_sets: tuple[PaperAnnotationSet, ...]
    translations: tuple[PaperTranslation, ...]
    translation_registrations: tuple[PaperTranslationRegistrationRecord, ...]
    selected_registration_id: UUID | None
    health: Literal["ready", "attention", "broken"]
    health_reasons: tuple[str, ...]


class PaperLibraryStats(BaseModel):
    """Source-level counts used by the local management dashboard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision_count: int = Field(ge=0)
    search_ready_count: int = Field(ge=0)
    attention_count: int = Field(ge=0)
    broken_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    total_annotations: int = Field(ge=0)
    total_translations: int = Field(ge=0)
    database_size_bytes: int | None = Field(default=None, ge=0)


class PaperAssetPayload(BaseModel):
    """Exact persisted source asset bytes for download or page inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision_id: UUID
    asset_id: UUID
    media_type: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str
    content: bytes
