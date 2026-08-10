"""Synchronous Streamlit bridge over one loop-bound local library."""

import asyncio
import ipaddress
import json
import threading
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from apps.paper_library.models import (
    PaperVisualAnnotation,
    TranslationPageReview,
    TranslationReviewStatus,
    VisualAnnotationReviewStatus,
)
from apps.paper_library.state import PaperLibraryStateStore
from quantmind.configs import (
    CitedPaperDraftInput,
    PaperCitedDraftCfg,
    PaperTranslationDraftCfg,
    PaperTranslationDraftInput,
)
from quantmind.flows import PaperFlow
from quantmind.knowledge import PaperAnnotatedResult, PaperTranslatedResult
from quantmind.library import (
    LocalKnowledgeLibrary,
    PaperAssetPayload,
    PaperCatalogPage,
    PaperCatalogQuery,
    PaperDetails,
    PaperLibraryStats,
    PaperRegistrationRecord,
    PaperTranslationRegistrationRecord,
    SemanticHit,
    SemanticQuery,
)
from scripts.prepare_codex_paper import prepare_codex_paper
from scripts.prepare_codex_translation import prepare_codex_translation

_ValueT = TypeVar("_ValueT")
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def validate_loopback_address(address: str) -> str:
    """Reject remote bind addresses before the UI opens local data."""
    normalized = address.strip().lower()
    if normalized == "localhost":
        return "127.0.0.1"
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise ValueError(
            "paper library UI requires a loopback bind address"
        ) from exc
    if not parsed.is_loopback:
        raise ValueError("paper library UI refuses non-loopback binding")
    return normalized


class PaperLibraryAppService:
    """Bind app paths and one dedicated event-loop/library lifecycle."""

    def __init__(
        self,
        *,
        knowledge_db_path: str | Path,
        sidecar_db_path: str | Path,
        model_cache_path: str | Path,
        intake_work_root: str | Path,
        _library_opener: Callable[[], Awaitable[LocalKnowledgeLibrary]]
        | None = None,
    ) -> None:
        self.knowledge_db_path = self._absolute_path(
            knowledge_db_path, "knowledge DB"
        )
        self.sidecar_db_path = self._absolute_path(
            sidecar_db_path, "sidecar DB"
        )
        self.model_cache_path = self._absolute_path(
            model_cache_path, "model cache"
        )
        self.intake_work_root = self._absolute_path(
            intake_work_root, "intake work root"
        )
        if self.knowledge_db_path == self.sidecar_db_path:
            raise ValueError(
                "knowledge DB and sidecar DB must be different files"
            )
        self.intake_work_root.mkdir(parents=True, exist_ok=True)
        self.state = PaperLibraryStateStore(self.sidecar_db_path)
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="quantmind-paper-library-loop",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait(timeout=10)
        if not self._loop_ready.is_set():
            raise RuntimeError("paper library event loop did not start")
        self._closed = False
        self._library_opener = _library_opener
        self._library = self._submit(self._open_library())

    @staticmethod
    def _absolute_path(path: str | Path, name: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise ValueError(f"{name} path must be absolute")
        if name == "intake work root" and candidate.is_symlink():
            raise ValueError("intake work root must not be a symbolic link")
        return candidate.resolve()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()
        self._loop.close()

    async def _open_library(self) -> LocalKnowledgeLibrary:
        if self._library_opener is not None:
            return await self._library_opener()
        return await LocalKnowledgeLibrary.open_local(
            self.knowledge_db_path,
            cache_dir=self.model_cache_path,
        )

    def _submit(self, awaitable: Coroutine[Any, Any, _ValueT]) -> _ValueT:
        if getattr(self, "_closed", False):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise RuntimeError("PaperLibraryAppService is closed")
        future: Future[_ValueT] = asyncio.run_coroutine_threadsafe(
            awaitable, self._loop
        )
        return future.result()

    def list_papers(
        self, query: PaperCatalogQuery | None = None
    ) -> PaperCatalogPage:
        return self._submit(self._library.list_papers(query))

    def inspect_library(self) -> PaperLibraryStats:
        return self._submit(self._library.inspect_library())

    def list_source_ids(self) -> tuple[UUID, ...]:
        """List every canonical source ID through bounded public pages."""
        source_ids: list[UUID] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self.list_papers(PaperCatalogQuery(limit=100, cursor=cursor))
            source_ids.extend(
                entry.source_revision_id for entry in page.entries
            )
            cursor = page.next_cursor
            if cursor is None:
                return tuple(source_ids)
            if cursor in seen_cursors:
                raise RuntimeError("paper catalog returned a repeated cursor")
            seen_cursors.add(cursor)

    def get_paper_details(
        self,
        source_revision_id: UUID,
        *,
        registration_id: UUID | None = None,
    ) -> PaperDetails:
        return self._submit(
            self._library.get_paper_details(
                source_revision_id,
                registration_id=registration_id,
            )
        )

    def get_paper_asset(
        self, source_revision_id: UUID, asset_id: UUID
    ) -> PaperAssetPayload:
        return self._submit(
            self._library.get_paper_asset(source_revision_id, asset_id)
        )

    def search(self, query: SemanticQuery) -> list[SemanticHit]:
        return self._submit(self._library.search(query))

    def list_registrations(
        self,
        source_revision_id: UUID | None = None,
        *,
        limit: int = 100,
    ) -> tuple[PaperRegistrationRecord, ...]:
        return self._submit(
            self._library.list_registrations(
                source_revision_id,
                limit=limit,
            )
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
        """Attach a sidecar image after validating its canonical linkage."""
        details = self.get_paper_details(source_revision_id)
        known_annotation_ids = {
            annotation.annotation_id
            for annotation_set in details.annotation_sets
            for annotation in annotation_set.annotations
        }
        if (
            linked_annotation_id is not None
            and linked_annotation_id not in known_annotation_ids
        ):
            raise ValueError(
                "linked annotation does not belong to this paper revision"
            )
        return self.state.add_visual_annotation(
            source_revision_id,
            image_content=image_content,
            original_filename=original_filename,
            media_type=media_type,
            caption=caption,
            alt_text=alt_text,
            creator=creator,
            provenance=provenance,
            review_status=review_status,
            review_note=review_note,
            linked_annotation_id=linked_annotation_id,
        )

    def list_visual_annotations(
        self, source_revision_id: UUID
    ) -> tuple[PaperVisualAnnotation, ...]:
        """Return sidecar explanatory images for a known paper revision."""
        self.get_paper_details(source_revision_id)
        return self.state.list_visual_annotations(source_revision_id)

    def update_visual_annotation_review(
        self,
        visual_annotation_id: UUID,
        *,
        expected_version: int,
        review_status: VisualAnnotationReviewStatus,
        review_note: str,
    ) -> PaperVisualAnnotation:
        """Update the human review label for a sidecar image."""
        return self.state.update_visual_annotation_review(
            visual_annotation_id,
            expected_version=expected_version,
            review_status=review_status,
            review_note=review_note,
        )

    def create_intake_workdir(self) -> Path:
        workdir = self.intake_work_root / str(uuid4())
        workdir.mkdir(mode=0o700)
        self._assert_in_work_root(workdir)
        return workdir

    def _assert_in_work_root(self, workdir: Path) -> None:
        resolved = workdir.resolve()
        if resolved.parent != self.intake_work_root or workdir.is_symlink():
            raise ValueError("intake path escapes the configured work root")

    def save_uploaded_pdf(self, workdir: Path, content: bytes) -> Path:
        self._assert_in_work_root(workdir)
        if not content.startswith(b"%PDF-"):
            raise ValueError("upload is not a PDF")
        if not content or len(content) > _MAX_UPLOAD_BYTES:
            raise ValueError("PDF upload exceeds the 200 MB limit")
        path = workdir / "upload.pdf"
        if path.exists():
            raise FileExistsError("uploaded PDF already exists")
        path.write_bytes(content)
        return path

    def save_draft(self, workdir: Path, content: bytes) -> Path:
        self._assert_in_work_root(workdir)
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("draft JSON exceeds 5 MB")
        try:
            json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("draft upload is not valid UTF-8 JSON") from exc
        path = workdir / "draft.json"
        if path.exists() or path.is_symlink():
            raise FileExistsError("draft JSON already exists")
        path.write_bytes(content)
        return path

    def prepare_input(
        self, input_value: str, workdir: Path
    ) -> tuple[Path, Path]:
        self._assert_in_work_root(workdir)
        return self._submit(prepare_codex_paper(input_value, workdir))

    def prepare_translation(
        self,
        source_revision_id: UUID,
    ) -> tuple[Path, Path]:
        """Stage a registered source for interactive translation."""
        details = self.get_paper_details(source_revision_id)
        source = details.source
        raw = self.get_paper_asset(source.id, source.raw_asset_id)
        workdir = self.create_intake_workdir()
        input_path = workdir / "registered-source.pdf"
        input_path.write_bytes(raw.content)
        source_path, manifest_path = self._submit(
            prepare_codex_translation(str(input_path), workdir)
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"] = {
            "kind": source.source.kind,
            "requested_uri": source.source.uri or input_path.as_uri(),
            "resolved_uri": source.source.uri or input_path.as_uri(),
            "media_type": "application/pdf",
            "fetched_at": (
                source.source.fetched_at or source.available_at
            ).isoformat(),
            "available_at": source.available_at.isoformat(),
            "published_at": (
                source.published_at.isoformat()
                if source.published_at is not None
                else None
            ),
            "arxiv_id": source.arxiv_id,
            "title": source.title,
            "authors": list(source.authors),
        }
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return source_path, manifest_path

    def finalize_draft(
        self, manifest_path: Path, draft_path: Path
    ) -> PaperAnnotatedResult:
        if manifest_path.resolve().parent != draft_path.resolve().parent:
            raise ValueError("manifest and draft must share one work directory")
        self._assert_in_work_root(manifest_path.resolve().parent)
        flow = PaperFlow(PaperCitedDraftCfg())
        return self._submit(
            flow.build(
                CitedPaperDraftInput(
                    manifest_path=manifest_path,
                    draft_path=draft_path,
                )
            )
        )

    def register(self, result: PaperAnnotatedResult) -> PaperRegistrationRecord:
        return self._submit(self._library.put_annotated_paper(result))

    def finalize_translation(
        self,
        manifest_path: Path,
        draft_path: Path,
    ) -> PaperTranslatedResult:
        """Validate one interactive translation draft without an LLM call."""
        if manifest_path.resolve().parent != draft_path.resolve().parent:
            raise ValueError(
                "translation manifest and draft must share one work directory"
            )
        self._assert_in_work_root(manifest_path.resolve().parent)
        flow = PaperFlow(PaperTranslationDraftCfg())
        return self._submit(
            flow.build(
                PaperTranslationDraftInput(
                    manifest_path=manifest_path,
                    draft_path=draft_path,
                )
            )
        )

    def register_translation(
        self,
        result: PaperTranslatedResult,
    ) -> PaperTranslationRegistrationRecord:
        """Persist a validated translation without creating embeddings."""
        return self._submit(self._library.put_translation(result))

    def update_translation_page_review(
        self,
        translation_id: UUID,
        page_number: int,
        *,
        expected_version: int,
        review_status: TranslationReviewStatus,
        review_note: str,
    ) -> TranslationPageReview:
        """Validate canonical page membership before updating the sidecar."""
        translation = self._submit(
            self._library.open_translation(translation_id)
        )
        if page_number not in {page.page_number for page in translation.pages}:
            raise ValueError("translation page does not exist")
        return self.state.update_translation_page_review(
            translation_id,
            page_number,
            expected_version=expected_version,
            review_status=review_status,
            review_note=review_note,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._submit(self._library.close())
        self._closed = True
        self.state.close()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)
