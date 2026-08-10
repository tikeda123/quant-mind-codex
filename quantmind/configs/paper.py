"""Paper-flow configuration + input discriminated union.

`PaperInput` describes supported and reserved source variants. Paper Flow V1
accepts PDF-backed arXiv, HTTP, and local inputs. It rejects non-PDF HTTP/local
content and raw text, and reserves DOI input until an exact PDF resolver exists.
"""

from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from quantmind.configs.base import BaseFlowCfg, BaseInput


class ArxivIdentifier(BaseInput):
    """Arxiv id (e.g. ``2604.12345``) or full arxiv URL."""

    type: Literal["arxiv"] = "arxiv"
    id: str


class HttpUrl(BaseInput):
    """A web URL that must resolve to a PDF in Paper Flow V1."""

    type: Literal["http"] = "http"
    url: str


class LocalFilePath(BaseInput):
    """Filesystem path to a PDF for Paper Flow V1."""

    type: Literal["local"] = "local"
    path: Path


class RawText(BaseInput):
    """Reserved inline text input rejected by page-aware Paper Flow V1."""

    type: Literal["text"] = "text"
    text: str


class DoiIdentifier(BaseInput):
    """A DOI to be resolved by ``preprocess.fetch.doi``."""

    type: Literal["doi"] = "doi"
    doi: str


PaperInput = Annotated[
    Union[ArxivIdentifier, HttpUrl, LocalFilePath, RawText, DoiIdentifier],
    Field(discriminator="type"),
]


class CitedPaperDraftInput(BaseInput):
    """Staged source manifest and cited draft authored outside QuantMind.

    Both files are local JSON. The manifest pins the exact PDF bytes and parser
    output; the draft contains prose plus page-and-quote evidence coordinates.
    ``PaperFlow`` validates and resolves those coordinates without calling an
    LLM or refetching a URL.
    """

    type: Literal["cited-paper-draft"] = "cited-paper-draft"
    manifest_path: Path
    draft_path: Path


class PaperTranslationDraftInput(BaseInput):
    """Staged source manifest and page translation authored outside QuantMind."""

    type: Literal["paper-translation-draft"] = "paper-translation-draft"
    manifest_path: Path
    draft_path: Path


class PaperCitedDraftCfg(BaseFlowCfg):
    """Deterministic policy for importing one externally authored draft."""

    model: Literal["external-cited-draft"] = "external-cited-draft"
    chunk_size: int = Field(default=384, gt=0)
    chunk_overlap: int = Field(default=48, ge=0)
    min_summary_citations: int = Field(default=3, ge=1)
    min_summary_pages: int = Field(default=2, ge=1)
    require_annotation_citations: Literal[True] = True
    draft_policy_version: Literal["cited-paper-draft-v1"] = (
        "cited-paper-draft-v1"
    )

    @model_validator(mode="after")
    def _validate_paper_bounds(self) -> "PaperCitedDraftCfg":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.min_summary_pages > self.min_summary_citations:
            raise ValueError(
                "min_summary_pages cannot exceed min_summary_citations"
            )
        return self


class PaperTranslationDraftCfg(BaseFlowCfg):
    """Deterministic policy for importing one complete English-to-Japanese draft."""

    model: Literal["external-translation-draft"] = "external-translation-draft"
    source_language: Literal["en"] = "en"
    target_language: Literal["ja"] = "ja"
    draft_policy_version: Literal["paper-translation-draft-v1"] = (
        "paper-translation-draft-v1"
    )


class PaperSemanticCfg(BaseFlowCfg):
    """Chunking and summarization controls for the semantic paper build.

    Selects the source-first chunk/summary shape when bound to
    ``PaperFlow``: ``PaperFlow(PaperSemanticCfg(...)).build(input)`` returns a
    ``PaperSemanticResult``.

    Summarization is a deterministic map-reduce: code tiles the chunk set into
    ``summary_research_group_size`` groups (so coverage is guaranteed by
    construction), fans out one research agent per group with
    ``summary_concurrency`` parallelism, then runs one reducer. Per-agent output
    is bounded by ``max_summary_output_tokens`` through ``ModelSettings``; there
    is no hand-rolled token accountant.

    This cfg carries no embedding setting: the build produces only source, chunk,
    and summary text. Embeddings are minted downstream at persistence time, using
    the ``embedding_model`` bound at ``LocalKnowledgeLibrary.open(...)`` (the
    examples use ``text-embedding-3-small``).
    """

    model: str = "gpt-5.6-luna"
    max_turns: int = Field(default=16, ge=1)
    chunk_size: int = Field(default=512, gt=0)
    chunk_overlap: int = Field(default=64, ge=0)
    summary_prompt_version: str = "paper-summary-v3"
    summary_instructions: str | None = None
    summary_research_group_size: int = Field(default=8, ge=1)
    summary_concurrency: int = Field(default=4, ge=1)
    max_summary_output_tokens: int = Field(default=4_096, gt=0)
    min_summary_citations: int = Field(default=3, ge=1)
    min_summary_pages: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def _validate_paper_bounds(self) -> "PaperSemanticCfg":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.min_summary_pages > self.min_summary_citations:
            raise ValueError(
                "min_summary_pages cannot exceed min_summary_citations"
            )
        return self
