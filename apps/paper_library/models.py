"""App-local immutable values for human organization and intake state."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ReadingStatus = Literal["inbox", "reading", "read", "archived"]
VisualAnnotationReviewStatus = Literal["unreviewed", "attention", "verified"]
TranslationReviewStatus = Literal["unreviewed", "attention", "verified"]


class PaperUserState(BaseModel):
    """Mutable-in-sidecar reading state returned as an immutable value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_revision_id: UUID
    display_title: str | None = None
    reading_status: ReadingStatus = "inbox"
    starred: bool = False
    personal_memo: str = ""
    last_opened_page: int | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    tags: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()


class CollectionRecord(BaseModel):
    """One personal paper collection stored only in the sidecar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collection_id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class SidecarStats(BaseModel):
    """Counts used by dashboard and audit views."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state_count: int = Field(ge=0)
    inbox_count: int = Field(ge=0)
    reading_count: int = Field(ge=0)
    read_count: int = Field(ge=0)
    archived_count: int = Field(ge=0)
    starred_count: int = Field(ge=0)
    tag_count: int = Field(ge=0)
    collection_count: int = Field(ge=0)
    visual_annotation_count: int = Field(ge=0)
    attention_visual_annotation_count: int = Field(ge=0)


class PaperVisualAnnotation(BaseModel):
    """One explanatory image kept separate from canonical paper evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visual_annotation_id: UUID
    source_revision_id: UUID
    linked_annotation_id: UUID | None = None
    original_filename: str
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    caption: str
    alt_text: str
    creator: str | None = None
    provenance: str | None = None
    review_status: VisualAnnotationReviewStatus
    review_note: str = ""
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    image_content: bytes


class TranslationPageReview(BaseModel):
    """Human review state for one immutable canonical translation page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    translation_id: UUID
    page_number: int = Field(ge=1)
    review_status: TranslationReviewStatus
    review_note: str = ""
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class IntakeSnapshot(BaseModel):
    """Session-local explicit intake state machine snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal[
        "unprepared",
        "prepared",
        "draft_waiting",
        "validation_failed",
        "validated",
        "registered",
    ]
    workdir: str | None = None
    manifest_path: str | None = None
    draft_path: str | None = None
    error: str | None = None
    registration_id: UUID | None = None
