"""SQLite sidecar for mutable human organization state only."""

import hashlib
import sqlite3
import threading
import warnings
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from PIL import Image, UnidentifiedImageError

from apps.paper_library.models import (
    CollectionRecord,
    PaperUserState,
    PaperVisualAnnotation,
    ReadingStatus,
    SidecarStats,
    TranslationPageReview,
    TranslationReviewStatus,
    VisualAnnotationReviewStatus,
)

_SCHEMA_VERSION = 3
_MAX_VISUAL_ANNOTATION_BYTES = 20 * 1024 * 1024
_MAX_VISUAL_ANNOTATION_PIXELS = 40_000_000
_IMAGE_FORMAT_MEDIA_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}

_SCHEMA_SQL = """
CREATE TABLE ui_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE paper_user_state (
    source_revision_id TEXT PRIMARY KEY,
    display_title TEXT,
    reading_status TEXT NOT NULL
        CHECK (reading_status IN ('inbox', 'reading', 'read', 'archived')),
    starred INTEGER NOT NULL CHECK (starred IN (0, 1)),
    personal_memo TEXT NOT NULL,
    last_opened_page INTEGER,
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE tags (
    tag_id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE paper_tags (
    source_revision_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (source_revision_id, tag_id),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
);

CREATE TABLE collections (
    collection_id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    description TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE collection_members (
    collection_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY (collection_id, source_revision_id),
    FOREIGN KEY (collection_id)
        REFERENCES collections(collection_id) ON DELETE CASCADE
);

CREATE TABLE visual_annotations (
    visual_annotation_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    linked_annotation_id TEXT,
    original_filename TEXT NOT NULL,
    media_type TEXT NOT NULL
        CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp')),
    content_hash TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    caption TEXT NOT NULL,
    alt_text TEXT NOT NULL,
    creator TEXT,
    provenance TEXT,
    review_status TEXT NOT NULL
        CHECK (review_status IN ('unreviewed', 'attention', 'verified')),
    review_note TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    image_content BLOB NOT NULL,
    UNIQUE (source_revision_id, content_hash)
);

CREATE INDEX visual_annotations_source_idx
ON visual_annotations (source_revision_id, created_at, visual_annotation_id);

CREATE TABLE IF NOT EXISTS translation_page_reviews (
    translation_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    review_status TEXT NOT NULL
        CHECK (review_status IN ('unreviewed', 'attention', 'verified')),
    review_note TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (translation_id, page_number)
);
"""

_MIGRATION_1_TO_2_SQL = """
CREATE TABLE visual_annotations (
    visual_annotation_id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL,
    linked_annotation_id TEXT,
    original_filename TEXT NOT NULL,
    media_type TEXT NOT NULL
        CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp')),
    content_hash TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    caption TEXT NOT NULL,
    alt_text TEXT NOT NULL,
    creator TEXT,
    provenance TEXT,
    review_status TEXT NOT NULL
        CHECK (review_status IN ('unreviewed', 'attention', 'verified')),
    review_note TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    image_content BLOB NOT NULL,
    UNIQUE (source_revision_id, content_hash)
);

CREATE INDEX visual_annotations_source_idx
ON visual_annotations (source_revision_id, created_at, visual_annotation_id);

UPDATE ui_meta SET value = '2' WHERE key = 'schema_version';
"""

_MIGRATION_2_TO_3_SQL = """
CREATE TABLE IF NOT EXISTS translation_page_reviews (
    translation_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    review_status TEXT NOT NULL
        CHECK (review_status IN ('unreviewed', 'attention', 'verified')),
    review_note TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (translation_id, page_number)
);

UPDATE ui_meta SET value = '3' WHERE key = 'schema_version';
"""


class StateConflictError(RuntimeError):
    """A sidecar row changed after the page loaded it."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaperLibraryStateStore:
    """Own the sidecar DB; never reads or writes the canonical knowledge DB."""

    def __init__(self, path: str | Path) -> None:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            raise ValueError("sidecar database path must be absolute")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.path = resolved
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            resolved,
            isolation_level=None,
            check_same_thread=False,
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            self._db.executescript(
                f"BEGIN IMMEDIATE;\n{_SCHEMA_SQL}\n"
                f"PRAGMA user_version = {_SCHEMA_VERSION};\nCOMMIT;"
            )
            self._db.execute(
                "INSERT INTO ui_meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
        elif version == 1:
            self._db.executescript(
                f"BEGIN IMMEDIATE;\n{_MIGRATION_1_TO_2_SQL}\n"
                "PRAGMA user_version = 2;\nCOMMIT;"
            )
            version = 2
        if version == 2:
            self._db.executescript(
                f"BEGIN IMMEDIATE;\n{_MIGRATION_2_TO_3_SQL}\n"
                f"PRAGMA user_version = {_SCHEMA_VERSION};\nCOMMIT;"
            )
        elif version not in (0, _SCHEMA_VERSION):
            self._db.close()
            raise RuntimeError(
                f"unsupported sidecar schema {version}; expected {_SCHEMA_VERSION}"
            )

    @staticmethod
    def _validate_text(
        value: str | None,
        *,
        name: str,
        maximum: int,
        allow_empty: bool,
    ) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped and not allow_empty:
            raise ValueError(f"{name} must not be blank")
        if len(stripped) > maximum:
            raise ValueError(f"{name} must be at most {maximum} characters")
        return stripped

    @staticmethod
    def _inspect_image(
        content: bytes, claimed_media_type: str
    ) -> tuple[str, int, int]:
        if not content:
            raise ValueError("visual annotation image must not be empty")
        if len(content) > _MAX_VISUAL_ANNOTATION_BYTES:
            raise ValueError("visual annotation image exceeds the 20 MB limit")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    image_format = image.format
                    width, height = image.size
                    if getattr(image, "is_animated", False):
                        raise ValueError(
                            "animated visual annotations are not supported"
                        )
                    image.verify()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
        ) as exc:
            raise ValueError("visual annotation is not a valid image") from exc
        detected_media_type = _IMAGE_FORMAT_MEDIA_TYPES.get(image_format or "")
        if detected_media_type is None:
            raise ValueError("visual annotation must be PNG, JPEG, or WebP")
        if detected_media_type != claimed_media_type:
            raise ValueError(
                "visual annotation media type does not match its bytes"
            )
        if width * height > _MAX_VISUAL_ANNOTATION_PIXELS:
            raise ValueError("visual annotation exceeds the 40 megapixel limit")
        return detected_media_type, width, height

    @staticmethod
    def _visual_annotation_from_row(row: sqlite3.Row) -> PaperVisualAnnotation:
        return PaperVisualAnnotation(
            visual_annotation_id=UUID(str(row["visual_annotation_id"])),
            source_revision_id=UUID(str(row["source_revision_id"])),
            linked_annotation_id=(
                UUID(str(row["linked_annotation_id"]))
                if row["linked_annotation_id"] is not None
                else None
            ),
            original_filename=str(row["original_filename"]),
            media_type=cast(
                Literal["image/png", "image/jpeg", "image/webp"],
                str(row["media_type"]),
            ),
            content_hash=str(row["content_hash"]),
            byte_size=int(row["byte_size"]),
            width=int(row["width"]),
            height=int(row["height"]),
            caption=str(row["caption"]),
            alt_text=str(row["alt_text"]),
            creator=(
                str(row["creator"]) if row["creator"] is not None else None
            ),
            provenance=(
                str(row["provenance"])
                if row["provenance"] is not None
                else None
            ),
            review_status=cast(
                VisualAnnotationReviewStatus, str(row["review_status"])
            ),
            review_note=str(row["review_note"]),
            version=int(row["version"]),
            created_at=datetime.fromtimestamp(
                float(row["created_at"]), timezone.utc
            ),
            updated_at=datetime.fromtimestamp(
                float(row["updated_at"]), timezone.utc
            ),
            image_content=bytes(row["image_content"]),
        )

    def _ensure_state(self, source_revision_id: UUID) -> None:
        now = _now().timestamp()
        self._db.execute(
            """
            INSERT INTO paper_user_state (
                source_revision_id, display_title, reading_status, starred,
                personal_memo, last_opened_page, version, created_at, updated_at
            ) VALUES (?, NULL, 'inbox', 0, '', NULL, 1, ?, ?)
            ON CONFLICT(source_revision_id) DO NOTHING
            """,
            (str(source_revision_id), now, now),
        )

    def get_state(self, source_revision_id: UUID) -> PaperUserState:
        with self._lock:
            self._ensure_state(source_revision_id)
            row = self._db.execute(
                """
                SELECT * FROM paper_user_state WHERE source_revision_id = ?
                """,
                (str(source_revision_id),),
            ).fetchone()
            assert row is not None
            tags = tuple(
                str(tag["name"])
                for tag in self._db.execute(
                    """
                    SELECT t.name FROM tags AS t
                    JOIN paper_tags AS p ON p.tag_id = t.tag_id
                    WHERE p.source_revision_id = ?
                    ORDER BY t.name COLLATE NOCASE
                    """,
                    (str(source_revision_id),),
                ).fetchall()
            )
            collections = tuple(
                str(item["name"])
                for item in self._db.execute(
                    """
                    SELECT c.name FROM collections AS c
                    JOIN collection_members AS m
                      ON m.collection_id = c.collection_id
                    WHERE m.source_revision_id = ?
                    ORDER BY c.name COLLATE NOCASE
                    """,
                    (str(source_revision_id),),
                ).fetchall()
            )
            return PaperUserState(
                source_revision_id=source_revision_id,
                display_title=(
                    str(row["display_title"])
                    if row["display_title"] is not None
                    else None
                ),
                reading_status=cast(ReadingStatus, str(row["reading_status"])),
                starred=bool(row["starred"]),
                personal_memo=str(row["personal_memo"]),
                last_opened_page=(
                    int(row["last_opened_page"])
                    if row["last_opened_page"] is not None
                    else None
                ),
                version=int(row["version"]),
                created_at=datetime.fromtimestamp(
                    float(row["created_at"]), timezone.utc
                ),
                updated_at=datetime.fromtimestamp(
                    float(row["updated_at"]), timezone.utc
                ),
                tags=tags,
                collections=collections,
            )

    def update_state(
        self,
        source_revision_id: UUID,
        *,
        expected_version: int,
        display_title: str | None,
        reading_status: ReadingStatus,
        starred: bool,
        personal_memo: str,
        last_opened_page: int | None,
        page_count: int,
    ) -> PaperUserState:
        display_title = self._validate_text(
            display_title,
            name="display title",
            maximum=200,
            allow_empty=True,
        )
        if display_title == "":
            display_title = None
        memo = self._validate_text(
            personal_memo,
            name="personal memo",
            maximum=20_000,
            allow_empty=True,
        )
        assert memo is not None
        if reading_status not in {"inbox", "reading", "read", "archived"}:
            raise ValueError("invalid reading status")
        if page_count < 1:
            raise ValueError("page_count must be positive")
        if last_opened_page is not None and not (
            1 <= last_opened_page <= page_count
        ):
            raise ValueError("last opened page is outside the paper")
        with self._lock:
            self._ensure_state(source_revision_id)
            cursor = self._db.execute(
                """
                UPDATE paper_user_state
                SET display_title = ?, reading_status = ?, starred = ?,
                    personal_memo = ?, last_opened_page = ?,
                    version = version + 1, updated_at = ?
                WHERE source_revision_id = ? AND version = ?
                """,
                (
                    display_title,
                    reading_status,
                    int(starred),
                    memo,
                    last_opened_page,
                    _now().timestamp(),
                    str(source_revision_id),
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StateConflictError(
                    "reading state changed in another tab; reload before saving"
                )
            return self.get_state(source_revision_id)

    def set_tags(
        self, source_revision_id: UUID, names: tuple[str, ...]
    ) -> PaperUserState:
        normalized_values: list[str] = []
        seen: set[str] = set()
        for name in names:
            normalized_name = self._validate_text(
                name,
                name="tag name",
                maximum=64,
                allow_empty=False,
            )
            assert normalized_name is not None
            key = normalized_name.casefold()
            if key not in seen:
                seen.add(key)
                normalized_values.append(normalized_name)
        normalized = tuple(normalized_values)
        if len(normalized) > 50:
            raise ValueError("a paper may have at most 50 tags")
        now = _now().timestamp()
        with self._lock:
            self._ensure_state(source_revision_id)
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "DELETE FROM paper_tags WHERE source_revision_id = ?",
                    (str(source_revision_id),),
                )
                for name in normalized:
                    assert name is not None
                    tag_id = uuid5(
                        NAMESPACE_URL, f"quantmind-ui:tag:{name.casefold()}"
                    )
                    self._db.execute(
                        """
                        INSERT INTO tags (tag_id, name, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(name) DO NOTHING
                        """,
                        (str(tag_id), name, now),
                    )
                    stored = self._db.execute(
                        "SELECT tag_id FROM tags WHERE name = ? COLLATE NOCASE",
                        (name,),
                    ).fetchone()
                    assert stored is not None
                    self._db.execute(
                        """
                        INSERT INTO paper_tags (source_revision_id, tag_id)
                        VALUES (?, ?)
                        """,
                        (str(source_revision_id), str(stored["tag_id"])),
                    )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
            return self.get_state(source_revision_id)

    def list_tags(self) -> tuple[str, ...]:
        """Return the bounded tag vocabulary for filter controls."""
        with self._lock:
            rows = self._db.execute(
                "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return tuple(str(row["name"]) for row in rows)

    def create_collection(
        self, name: str, description: str = ""
    ) -> CollectionRecord:
        normalized_name = self._validate_text(
            name,
            name="collection name",
            maximum=100,
            allow_empty=False,
        )
        normalized_description = self._validate_text(
            description,
            name="collection description",
            maximum=2_000,
            allow_empty=True,
        )
        assert normalized_name is not None
        assert normalized_description is not None
        collection_id = uuid5(
            NAMESPACE_URL,
            f"quantmind-ui:collection:{normalized_name.casefold()}",
        )
        now = _now()
        with self._lock:
            try:
                self._db.execute(
                    """
                    INSERT INTO collections (
                        collection_id, name, description, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(collection_id),
                        normalized_name,
                        normalized_description,
                        now.timestamp(),
                        now.timestamp(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("collection name already exists") from exc
        return CollectionRecord(
            collection_id=collection_id,
            name=normalized_name,
            description=normalized_description,
            created_at=now,
            updated_at=now,
        )

    def set_collections(
        self,
        source_revision_id: UUID,
        collection_ids: tuple[UUID, ...],
    ) -> PaperUserState:
        now = _now().timestamp()
        with self._lock:
            self._ensure_state(source_revision_id)
            known = {
                UUID(str(row["collection_id"]))
                for row in self._db.execute(
                    "SELECT collection_id FROM collections"
                ).fetchall()
            }
            if not set(collection_ids).issubset(known):
                raise KeyError("unknown collection")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    """
                    DELETE FROM collection_members WHERE source_revision_id = ?
                    """,
                    (str(source_revision_id),),
                )
                self._db.executemany(
                    """
                    INSERT INTO collection_members (
                        collection_id, source_revision_id, added_at
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (str(value), str(source_revision_id), now)
                        for value in collection_ids
                    ],
                )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
            return self.get_state(source_revision_id)

    def list_collections(self) -> tuple[CollectionRecord, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM collections ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return tuple(
            CollectionRecord(
                collection_id=UUID(str(row["collection_id"])),
                name=str(row["name"]),
                description=str(row["description"]),
                created_at=datetime.fromtimestamp(
                    float(row["created_at"]), timezone.utc
                ),
                updated_at=datetime.fromtimestamp(
                    float(row["updated_at"]), timezone.utc
                ),
            )
            for row in rows
        )

    def add_visual_annotation(
        self,
        source_revision_id: UUID,
        *,
        image_content: bytes,
        original_filename: str,
        media_type: str,
        caption: str,
        alt_text: str,
        creator: str | None = None,
        provenance: str | None = None,
        review_status: VisualAnnotationReviewStatus = "unreviewed",
        review_note: str = "",
        linked_annotation_id: UUID | None = None,
    ) -> PaperVisualAnnotation:
        """Store one bounded explanatory image in the UI sidecar."""
        normalized_filename = self._validate_text(
            original_filename,
            name="original filename",
            maximum=255,
            allow_empty=False,
        )
        normalized_caption = self._validate_text(
            caption,
            name="caption",
            maximum=1_000,
            allow_empty=False,
        )
        normalized_alt_text = self._validate_text(
            alt_text,
            name="alt text",
            maximum=1_000,
            allow_empty=False,
        )
        normalized_creator = self._validate_text(
            creator,
            name="creator",
            maximum=200,
            allow_empty=True,
        )
        normalized_provenance = self._validate_text(
            provenance,
            name="provenance",
            maximum=2_000,
            allow_empty=True,
        )
        normalized_review_note = self._validate_text(
            review_note,
            name="review note",
            maximum=2_000,
            allow_empty=True,
        )
        assert normalized_filename is not None
        assert normalized_caption is not None
        assert normalized_alt_text is not None
        assert normalized_review_note is not None
        if "/" in normalized_filename or "\\" in normalized_filename:
            raise ValueError("original filename must not contain a path")
        if review_status not in {"unreviewed", "attention", "verified"}:
            raise ValueError("invalid visual annotation review status")
        detected_media_type, width, height = self._inspect_image(
            image_content, media_type
        )
        content_hash = hashlib.sha256(image_content).hexdigest()
        visual_annotation_id = uuid5(
            NAMESPACE_URL,
            f"quantmind-ui:visual:{source_revision_id}:{content_hash}",
        )
        now = _now()
        with self._lock:
            try:
                self._db.execute(
                    """
                    INSERT INTO visual_annotations (
                        visual_annotation_id, source_revision_id,
                        linked_annotation_id, original_filename, media_type,
                        content_hash, byte_size, width, height, caption,
                        alt_text, creator, provenance, review_status,
                        review_note, version, created_at, updated_at,
                        image_content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                              ?, ?, ?)
                    """,
                    (
                        str(visual_annotation_id),
                        str(source_revision_id),
                        (
                            str(linked_annotation_id)
                            if linked_annotation_id is not None
                            else None
                        ),
                        normalized_filename,
                        detected_media_type,
                        content_hash,
                        len(image_content),
                        width,
                        height,
                        normalized_caption,
                        normalized_alt_text,
                        normalized_creator or None,
                        normalized_provenance or None,
                        review_status,
                        normalized_review_note,
                        now.timestamp(),
                        now.timestamp(),
                        image_content,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "this image is already attached to the paper"
                ) from exc
        stored = self.get_visual_annotation(visual_annotation_id)
        assert stored is not None
        return stored

    def get_visual_annotation(
        self, visual_annotation_id: UUID
    ) -> PaperVisualAnnotation | None:
        """Return one visual annotation, including its bounded image bytes."""
        with self._lock:
            row = self._db.execute(
                """
                SELECT * FROM visual_annotations
                WHERE visual_annotation_id = ?
                """,
                (str(visual_annotation_id),),
            ).fetchone()
        if row is None:
            return None
        return self._visual_annotation_from_row(row)

    def list_visual_annotations(
        self, source_revision_id: UUID
    ) -> tuple[PaperVisualAnnotation, ...]:
        """List explanatory images for one canonical source revision."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT * FROM visual_annotations
                WHERE source_revision_id = ?
                ORDER BY created_at, visual_annotation_id
                """,
                (str(source_revision_id),),
            ).fetchall()
        return tuple(self._visual_annotation_from_row(row) for row in rows)

    def update_visual_annotation_review(
        self,
        visual_annotation_id: UUID,
        *,
        expected_version: int,
        review_status: VisualAnnotationReviewStatus,
        review_note: str,
    ) -> PaperVisualAnnotation:
        """Update review evidence without replacing the attached image."""
        if review_status not in {"unreviewed", "attention", "verified"}:
            raise ValueError("invalid visual annotation review status")
        normalized_review_note = self._validate_text(
            review_note,
            name="review note",
            maximum=2_000,
            allow_empty=True,
        )
        assert normalized_review_note is not None
        with self._lock:
            cursor = self._db.execute(
                """
                UPDATE visual_annotations
                SET review_status = ?, review_note = ?, version = version + 1,
                    updated_at = ?
                WHERE visual_annotation_id = ? AND version = ?
                """,
                (
                    review_status,
                    normalized_review_note,
                    _now().timestamp(),
                    str(visual_annotation_id),
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StateConflictError(
                    "visual annotation changed in another tab; reload before saving"
                )
        stored = self.get_visual_annotation(visual_annotation_id)
        assert stored is not None
        return stored

    @staticmethod
    def _translation_review_from_row(
        row: sqlite3.Row,
    ) -> TranslationPageReview:
        return TranslationPageReview(
            translation_id=UUID(str(row["translation_id"])),
            page_number=int(row["page_number"]),
            review_status=cast(
                TranslationReviewStatus,
                str(row["review_status"]),
            ),
            review_note=str(row["review_note"]),
            version=int(row["version"]),
            created_at=datetime.fromtimestamp(
                float(row["created_at"]), timezone.utc
            ),
            updated_at=datetime.fromtimestamp(
                float(row["updated_at"]), timezone.utc
            ),
        )

    def get_translation_page_review(
        self,
        translation_id: UUID,
        page_number: int,
    ) -> TranslationPageReview:
        """Return a review row, creating the default unreviewed state."""
        if page_number < 1:
            raise ValueError("translation review page must be positive")
        now = _now().timestamp()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO translation_page_reviews (
                    translation_id, page_number, review_status, review_note,
                    version, created_at, updated_at
                ) VALUES (?, ?, 'unreviewed', '', 1, ?, ?)
                ON CONFLICT(translation_id, page_number) DO NOTHING
                """,
                (str(translation_id), page_number, now, now),
            )
            row = self._db.execute(
                """
                SELECT * FROM translation_page_reviews
                WHERE translation_id = ? AND page_number = ?
                """,
                (str(translation_id), page_number),
            ).fetchone()
        assert row is not None
        return self._translation_review_from_row(row)

    def list_translation_page_reviews(
        self,
        translation_id: UUID,
    ) -> tuple[TranslationPageReview, ...]:
        """List already materialized page-review rows for a translation."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT * FROM translation_page_reviews
                WHERE translation_id = ? ORDER BY page_number
                """,
                (str(translation_id),),
            ).fetchall()
        return tuple(self._translation_review_from_row(row) for row in rows)

    def update_translation_page_review(
        self,
        translation_id: UUID,
        page_number: int,
        *,
        expected_version: int,
        review_status: TranslationReviewStatus,
        review_note: str,
    ) -> TranslationPageReview:
        """Update one page's human review state with optimistic locking."""
        if review_status not in {"unreviewed", "attention", "verified"}:
            raise ValueError("invalid translation review status")
        normalized_note = self._validate_text(
            review_note,
            name="translation review note",
            maximum=2_000,
            allow_empty=True,
        )
        assert normalized_note is not None
        self.get_translation_page_review(translation_id, page_number)
        with self._lock:
            cursor = self._db.execute(
                """
                UPDATE translation_page_reviews
                SET review_status = ?, review_note = ?, version = version + 1,
                    updated_at = ?
                WHERE translation_id = ? AND page_number = ? AND version = ?
                """,
                (
                    review_status,
                    normalized_note,
                    _now().timestamp(),
                    str(translation_id),
                    page_number,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StateConflictError(
                    "translation review changed in another tab; reload before "
                    "saving"
                )
        return self.get_translation_page_review(translation_id, page_number)

    def filter_source_ids(
        self,
        *,
        candidate_source_ids: tuple[UUID, ...] = (),
        reading_status: ReadingStatus | None = None,
        starred: bool | None = None,
        tags: tuple[str, ...] = (),
        collection_id: UUID | None = None,
        include_archived: bool = False,
    ) -> tuple[UUID, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT source_revision_id FROM paper_user_state"
            ).fetchall()
            source_ids = {UUID(str(row["source_revision_id"])) for row in rows}
            source_ids.update(candidate_source_ids)
            selected: list[UUID] = []
            for source_id in sorted(source_ids, key=str):
                state = self.get_state(source_id)
                if not include_archived and state.reading_status == "archived":
                    continue
                if (
                    reading_status is not None
                    and state.reading_status != reading_status
                ):
                    continue
                if starred is not None and state.starred != starred:
                    continue
                if tags and not set(tags).issubset(state.tags):
                    continue
                if collection_id is not None:
                    member = self._db.execute(
                        """
                        SELECT 1 FROM collection_members
                        WHERE source_revision_id = ? AND collection_id = ?
                        """,
                        (str(source_id), str(collection_id)),
                    ).fetchone()
                    if member is None:
                        continue
                selected.append(source_id)
            return tuple(selected)

    def orphaned_source_ids(
        self, known_source_ids: set[UUID]
    ) -> tuple[UUID, ...]:
        with self._lock:
            values = {
                UUID(str(row[0]))
                for row in self._db.execute(
                    """
                    SELECT source_revision_id FROM paper_user_state
                    UNION SELECT source_revision_id FROM paper_tags
                    UNION SELECT source_revision_id FROM collection_members
                    UNION SELECT source_revision_id FROM visual_annotations
                    """
                ).fetchall()
            }
        return tuple(sorted(values - known_source_ids, key=str))

    def inspect(self) -> SidecarStats:
        with self._lock:
            counts = {
                status: int(
                    self._db.execute(
                        """
                        SELECT COUNT(*) FROM paper_user_state
                        WHERE reading_status = ?
                        """,
                        (status,),
                    ).fetchone()[0]
                )
                for status in ("inbox", "reading", "read", "archived")
            }
            state_count = sum(counts.values())
            return SidecarStats(
                state_count=state_count,
                inbox_count=counts["inbox"],
                reading_count=counts["reading"],
                read_count=counts["read"],
                archived_count=counts["archived"],
                starred_count=int(
                    self._db.execute(
                        "SELECT COUNT(*) FROM paper_user_state WHERE starred = 1"
                    ).fetchone()[0]
                ),
                tag_count=int(
                    self._db.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
                ),
                collection_count=int(
                    self._db.execute(
                        "SELECT COUNT(*) FROM collections"
                    ).fetchone()[0]
                ),
                visual_annotation_count=int(
                    self._db.execute(
                        "SELECT COUNT(*) FROM visual_annotations"
                    ).fetchone()[0]
                ),
                attention_visual_annotation_count=int(
                    self._db.execute(
                        """
                        SELECT COUNT(*) FROM visual_annotations
                        WHERE review_status = 'attention'
                        """
                    ).fetchone()[0]
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()
