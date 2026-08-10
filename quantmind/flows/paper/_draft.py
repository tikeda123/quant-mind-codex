"""Private validation boundary for externally authored cited paper drafts."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from quantmind.configs import PaperCitedDraftCfg
from quantmind.knowledge import (
    PaperAnnotationDraft,
    PaperAnnotationKind,
    PaperChunkSet,
    PaperCitationDraft,
    PaperSourceFacts,
)
from quantmind.preprocess import ParsedDocument
from quantmind.preprocess.format import parse_pdf


class _DraftManifestSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["arxiv", "http", "local"]
    requested_uri: str = Field(min_length=1)
    resolved_uri: str = Field(min_length=1)
    media_type: Literal["application/pdf"] = "application/pdf"
    fetched_at: datetime
    available_at: datetime
    published_at: datetime | None = None
    arxiv_id: str | None = None
    title: str | None = None
    authors: tuple[str, ...] = ()


class _DraftManifestPdf(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("manifest PDF path must be absolute")
        return value


class _DraftManifestParser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cleanup_policy: str = Field(min_length=1)


class _DraftManifestPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    text: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_empty: bool

    @model_validator(mode="after")
    def _content_metadata_matches(self) -> "_DraftManifestPage":
        expected_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.text_sha256 != expected_hash:
            raise ValueError("manifest page text hash mismatch")
        if self.is_empty != (not self.text.strip()):
            raise ValueError("manifest page empty flag mismatch")
        return self


class _DraftManifestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cited-paper-draft-v1"] = "cited-paper-draft-v1"
    instructions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CitedPaperManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    source: _DraftManifestSource
    pdf: _DraftManifestPdf
    parser: _DraftManifestParser
    pages: tuple[_DraftManifestPage, ...] = Field(min_length=1)
    draft_policy: _DraftManifestPolicy

    @model_validator(mode="after")
    def _pages_are_contiguous(self) -> "_CitedPaperManifest":
        if tuple(page.page_number for page in self.pages) != tuple(
            range(1, len(self.pages) + 1)
        ):
            raise ValueError("manifest pages must be contiguous and 1-based")
        return self


class _DraftGenerator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["codex-interactive", "external-interactive"]
    model_label: str | None = None
    draft_policy_version: Literal["cited-paper-draft-v1"] = (
        "cited-paper-draft-v1"
    )
    instructions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _DraftCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    quote: str = Field(min_length=8, max_length=500)

    @field_validator("quote")
    @classmethod
    def _quote_is_trimmed_and_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 8:
            raise ValueError("draft citation quote must contain 8 characters")
        return stripped


class _DraftSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    citations: tuple[_DraftCitation, ...] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("draft summary must not be blank")
        return stripped


class _DraftAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PaperAnnotationKind
    text: str = Field(min_length=1)
    citations: tuple[_DraftCitation, ...] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("draft annotation must not be blank")
        return stripped


class _CitedPaperDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator: _DraftGenerator
    summary: _DraftSummary
    annotations: tuple[_DraftAnnotation, ...] = Field(min_length=1)


@dataclass(frozen=True)
class _ValidatedCitedPaperDraft:
    """Revalidated local inputs ready for canonical artifact construction."""

    manifest: _CitedPaperManifest
    draft: _CitedPaperDraft
    draft_content_hash: str
    facts: PaperSourceFacts
    parsed: ParsedDocument


def _canonical_draft_hash(draft: _CitedPaperDraft) -> str:
    payload = json.dumps(
        draft.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _load_json_model(path: Path, model: type[_ModelT]) -> _ModelT:
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Unable to read staged JSON file: {path}") from exc
    try:
        return model.model_validate_json(payload)
    except ValueError as exc:
        raise ValueError(f"Invalid staged JSON file '{path}': {exc}") from exc


async def _validate_cited_paper_draft(
    manifest_path: Path,
    draft_path: Path,
    *,
    cfg: PaperCitedDraftCfg,
) -> _ValidatedCitedPaperDraft:
    """Re-read all staged evidence and reject any drift before construction."""
    manifest = _load_json_model(manifest_path, _CitedPaperManifest)
    draft = _load_json_model(draft_path, _CitedPaperDraft)

    try:
        raw_bytes = manifest.pdf.path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"Unable to read staged PDF: {manifest.pdf.path}"
        ) from exc
    if not raw_bytes.startswith(b"%PDF-"):
        raise ValueError("staged source is not a PDF")
    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    if source_hash != manifest.pdf.sha256:
        raise ValueError("staged PDF hash does not match manifest")
    if len(raw_bytes) != manifest.pdf.size_bytes:
        raise ValueError("staged PDF size does not match manifest")
    if draft.source_content_hash != source_hash:
        raise ValueError("draft source hash does not match staged PDF")
    if manifest.draft_policy.version != cfg.draft_policy_version:
        raise ValueError("manifest draft policy does not match flow config")
    if draft.generator.draft_policy_version != cfg.draft_policy_version:
        raise ValueError("draft policy does not match flow config")
    if (
        draft.generator.instructions_sha256
        != manifest.draft_policy.instructions_sha256
    ):
        raise ValueError("draft instructions hash does not match manifest")

    parsed = await parse_pdf(raw_bytes, artifact_dir=cfg.output_dir)
    if parsed.source_hash != source_hash:
        raise ValueError("parser source hash does not match staged PDF")
    if (
        parsed.parser_name != manifest.parser.name
        or parsed.parser_version != manifest.parser.version
        or parsed.cleanup_version != manifest.parser.cleanup_policy
    ):
        raise ValueError("current parser identity does not match manifest")
    if len(parsed.pages) != len(manifest.pages):
        raise ValueError("parsed page count does not match manifest")
    for parsed_page, manifest_page in zip(
        parsed.pages, manifest.pages, strict=True
    ):
        if parsed_page.page_number != manifest_page.page_number:
            raise ValueError("parsed page number does not match manifest")
        parsed_text_hash = hashlib.sha256(
            parsed_page.text.encode("utf-8")
        ).hexdigest()
        if (
            parsed_page.text != manifest_page.text
            or parsed_text_hash != manifest_page.text_sha256
            or (not parsed_page.text.strip()) != manifest_page.is_empty
        ):
            raise ValueError(
                f"parsed page {parsed_page.page_number} does not match manifest"
            )

    source = manifest.source
    facts = PaperSourceFacts(
        kind=source.kind,
        uri=source.resolved_uri,
        media_type=source.media_type,
        raw_bytes=raw_bytes,
        fetched_at=source.fetched_at,
        available_at=source.available_at,
        published_at=source.published_at,
        arxiv_id=source.arxiv_id,
        title=source.title,
        authors=source.authors,
    )
    return _ValidatedCitedPaperDraft(
        manifest=manifest,
        draft=draft,
        draft_content_hash=_canonical_draft_hash(draft),
        facts=facts,
        parsed=parsed,
    )


def _resolve_draft_citations(
    chunk_set: PaperChunkSet,
    citations: Sequence[_DraftCitation],
) -> tuple[PaperCitationDraft, ...]:
    """Resolve page + exact quote coordinates to one and only one chunk."""
    resolved: list[PaperCitationDraft] = []
    for citation in citations:
        matches = [
            chunk
            for chunk in chunk_set.chunks
            if citation.quote in chunk.text
            and citation.page_number
            in {span.page_number for span in chunk.source_spans}
        ]
        if not matches:
            raise ValueError(
                "draft citation quote does not match a chunk on page "
                f"{citation.page_number}"
            )
        if len(matches) > 1:
            raise ValueError(
                "draft citation quote is ambiguous on page "
                f"{citation.page_number}"
            )
        chunk = matches[0]
        resolved.append(
            PaperCitationDraft(
                chunk_index=chunk.position,
                page_number=citation.page_number,
                quote=citation.quote,
            )
        )
    return tuple(resolved)


def _resolve_draft_annotations(
    chunk_set: PaperChunkSet,
    annotations: Sequence[_DraftAnnotation],
) -> tuple[PaperAnnotationDraft, ...]:
    return tuple(
        PaperAnnotationDraft(
            kind=annotation.kind,
            text=annotation.text,
            citations=_resolve_draft_citations(chunk_set, annotation.citations),
        )
        for annotation in annotations
    )
