"""Private validation boundary for interactive paper-translation drafts."""

import hashlib
import json
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

from quantmind.configs import PaperTranslationDraftCfg
from quantmind.knowledge import PaperSourceFacts
from quantmind.preprocess import ParsedDocument
from quantmind.preprocess.format import parse_pdf


class _TranslationManifestSource(BaseModel):
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


class _TranslationManifestPdf(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("translation manifest PDF path must be absolute")
        return value


class _TranslationManifestParser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cleanup_policy: str = Field(min_length=1)


class _TranslationManifestPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    text: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_empty: bool

    @model_validator(mode="after")
    def _content_metadata_matches(self) -> "_TranslationManifestPage":
        expected_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.text_sha256 != expected_hash:
            raise ValueError("translation manifest page text hash mismatch")
        if self.is_empty != (not self.text.strip()):
            raise ValueError("translation manifest page empty flag mismatch")
        return self


class _TranslationManifestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["paper-translation-draft-v1"] = (
        "paper-translation-draft-v1"
    )
    instructions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _PaperTranslationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    source: _TranslationManifestSource
    pdf: _TranslationManifestPdf
    parser: _TranslationManifestParser
    pages: tuple[_TranslationManifestPage, ...] = Field(min_length=1)
    source_language: Literal["en"] = "en"
    target_language: Literal["ja"] = "ja"
    translation_policy: _TranslationManifestPolicy

    @model_validator(mode="after")
    def _pages_are_contiguous(self) -> "_PaperTranslationManifest":
        if tuple(page.page_number for page in self.pages) != tuple(
            range(1, len(self.pages) + 1)
        ):
            raise ValueError(
                "translation manifest pages must be contiguous and 1-based"
            )
        return self


class _TranslationDraftGenerator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["codex-interactive", "external-interactive"]
    model_label: str | None = None
    draft_policy_version: Literal["paper-translation-draft-v1"] = (
        "paper-translation-draft-v1"
    )
    instructions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _TranslationDraftPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    translated_text: str


class _PaperTranslationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_language: Literal["en"] = "en"
    target_language: Literal["ja"] = "ja"
    generator: _TranslationDraftGenerator
    pages: tuple[_TranslationDraftPage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _pages_are_contiguous(self) -> "_PaperTranslationDraft":
        if tuple(page.page_number for page in self.pages) != tuple(
            range(1, len(self.pages) + 1)
        ):
            raise ValueError(
                "translation draft pages must be contiguous and 1-based"
            )
        return self


@dataclass(frozen=True)
class _ValidatedPaperTranslationDraft:
    """Revalidated local inputs ready for canonical artifact construction."""

    manifest: _PaperTranslationManifest
    draft: _PaperTranslationDraft
    draft_content_hash: str
    facts: PaperSourceFacts
    parsed: ParsedDocument


def _canonical_translation_draft_hash(draft: _PaperTranslationDraft) -> str:
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


async def _validate_paper_translation_draft(
    manifest_path: Path,
    draft_path: Path,
    *,
    cfg: PaperTranslationDraftCfg,
) -> _ValidatedPaperTranslationDraft:
    """Re-read PDF and page text, rejecting partial coverage or drift."""
    manifest = _load_json_model(manifest_path, _PaperTranslationManifest)
    draft = _load_json_model(draft_path, _PaperTranslationDraft)

    try:
        raw_bytes = manifest.pdf.path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"Unable to read staged PDF: {manifest.pdf.path}"
        ) from exc
    if not raw_bytes.startswith(b"%PDF-"):
        raise ValueError("staged translation source is not a PDF")
    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    if source_hash != manifest.pdf.sha256:
        raise ValueError("staged PDF hash does not match translation manifest")
    if len(raw_bytes) != manifest.pdf.size_bytes:
        raise ValueError("staged PDF size does not match translation manifest")
    if draft.source_content_hash != source_hash:
        raise ValueError("translation draft source hash does not match PDF")
    if manifest.translation_policy.version != cfg.draft_policy_version:
        raise ValueError("translation manifest policy does not match config")
    if draft.generator.draft_policy_version != cfg.draft_policy_version:
        raise ValueError("translation draft policy does not match config")
    if (
        draft.generator.instructions_sha256
        != manifest.translation_policy.instructions_sha256
    ):
        raise ValueError(
            "translation instructions hash does not match manifest"
        )
    if (
        manifest.source_language != cfg.source_language
        or draft.source_language != cfg.source_language
        or manifest.target_language != cfg.target_language
        or draft.target_language != cfg.target_language
    ):
        raise ValueError("translation language pair does not match config")

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
        raise ValueError(
            "parsed page count does not match translation manifest"
        )
    if len(draft.pages) != len(manifest.pages):
        raise ValueError("translation draft must cover every source page")
    for parsed_page, manifest_page, draft_page in zip(
        parsed.pages,
        manifest.pages,
        draft.pages,
        strict=True,
    ):
        if (
            parsed_page.page_number != manifest_page.page_number
            or draft_page.page_number != manifest_page.page_number
        ):
            raise ValueError("translation page number does not match manifest")
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
        if parsed_page.text.strip() and not draft_page.translated_text.strip():
            raise ValueError(
                f"translation page {draft_page.page_number} is blank"
            )
        if not parsed_page.text.strip() and draft_page.translated_text.strip():
            raise ValueError(
                f"empty source page {draft_page.page_number} must stay empty"
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
    return _ValidatedPaperTranslationDraft(
        manifest=manifest,
        draft=draft,
        draft_content_hash=_canonical_translation_draft_hash(draft),
        facts=facts,
        parsed=parsed,
    )
