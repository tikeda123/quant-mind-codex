"""Deterministic paper artifacts shared by offline tests.

The fixture builds through the same knowledge-layer smart constructors that
``PaperFlow.build`` uses, so tests exercise the real identity path instead of
re-deriving IDs and hashes with the private helpers.
"""

import hashlib
from datetime import datetime, timezone
from typing import Literal

from quantmind.knowledge import (
    PaperAnnotatedResult,
    PaperAnnotationDraft,
    PaperAnnotationKind,
    PaperAnnotationSet,
    PaperChunkingConfig,
    PaperChunkInput,
    PaperChunkSet,
    PaperCitationDraft,
    PaperCitedDraftProducer,
    PaperGlobalSummary,
    PaperPageInput,
    PaperSemanticResult,
    PaperSourceFacts,
    PaperSourceRevision,
    PaperStructureNodeDraft,
    PaperStructureProducer,
    PaperStructureTree,
    PaperStructureTreeDraft,
    PaperSummaryProducer,
    PaperTranslatedResult,
    PaperTranslation,
    PaperTranslationProducer,
)

_RAW_BYTES = b"deterministic paper source revision"
_WHEN = datetime(2017, 12, 6, tzinfo=timezone.utc)
_PAGE_ONE = (
    "The Transformer removes recurrence and convolution "
    "from sequence transduction."
)
_PAGE_TWO = (
    "Multi-head attention uses parallel projections. "
    "The model improves translation and training efficiency."
)


def build_paper_result(
    *,
    chunk_size: int = 128,
    summary_model: str = "fake-summary",
    summary_text: str = (
        "The Transformer replaces recurrence and convolution with an "
        "encoder-decoder attention architecture, uses multi-head attention, "
        "and improves translation quality with efficient training."
    ),
    when: datetime = _WHEN,
) -> PaperSemanticResult:
    """Build one valid two-page result without parsing, network, or models.

    ``when`` drives the source revision timestamps (and therefore the derived
    structure tree's ``as_of`` provenance) without changing the source bytes, so
    tests can rebuild the same artifact at a different wall-clock time.
    """
    source_hash = hashlib.sha256(_RAW_BYTES).hexdigest()
    source = PaperSourceRevision.from_parsed(
        facts=PaperSourceFacts(
            kind="arxiv",
            uri="https://arxiv.org/pdf/1706.03762v7.pdf",
            media_type="application/pdf",
            raw_bytes=_RAW_BYTES,
            fetched_at=when,
            available_at=when,
            published_at=when,
            arxiv_id="1706.03762v7",
            title="Attention Is All You Need",
            authors=("Ashish Vaswani",),
        ),
        source_hash=source_hash,
        parser_name="fake-parser",
        parser_version="1",
        cleanup_version="1",
        pages=(
            PaperPageInput(
                page_number=1, width=612, height=792, text=_PAGE_ONE
            ),
            PaperPageInput(
                page_number=2, width=612, height=792, text=_PAGE_TWO
            ),
        ),
    )

    chunk_values = (
        (1, "The Transformer removes recurrence and convolution."),
        (2, "Multi-head attention uses parallel learned projections."),
        (2, "The model improves translation and training efficiency."),
    )
    chunk_inputs = tuple(
        PaperChunkInput(
            page_number=page,
            start_char=position * 10,
            end_char=position * 10 + len(text),
            block_boxes=(),
            text=text,
        )
        for position, (page, text) in enumerate(chunk_values)
    )
    chunk_set = PaperChunkSet.from_parsed_chunks(
        source,
        chunk_inputs,
        producer=PaperChunkingConfig(
            splitter_version="fake-llama-index",
            chunk_size=chunk_size,
            chunk_overlap=min(16, chunk_size - 1),
        ),
    )

    summary = PaperGlobalSummary.from_draft(
        chunk_set,
        producer=PaperSummaryProducer(
            model=summary_model,
            prompt_version="test-v1",
            input_chunk_set_id=chunk_set.id,
            instructions_hash=hashlib.sha256(b"test instructions").hexdigest(),
            max_output_tokens=512,
            research_group_size=8,
        ),
        summary=summary_text,
        citations=tuple(
            PaperCitationDraft(
                chunk_index=index,
                page_number=chunk.source_spans[0].page_number,
            )
            for index, chunk in enumerate(chunk_set.chunks)
        ),
        min_citations=1,
        min_pages=1,
    )
    return PaperSemanticResult(
        source_revision=source,
        chunk_set=chunk_set,
        global_summary=summary,
    )


def build_annotated_paper_result(
    *,
    draft_marker: str = "original",
    when: datetime = _WHEN,
) -> PaperAnnotatedResult:
    """Build one cited summary/annotation result without IO or model calls."""
    semantic = build_paper_result(when=when)
    chunk_set = semantic.chunk_set
    producer = PaperCitedDraftProducer(
        input_chunk_set_id=chunk_set.id,
        generator="codex-interactive",
        model_label=None,
        instructions_hash=hashlib.sha256(
            b"cited paper instructions"
        ).hexdigest(),
        draft_content_hash=hashlib.sha256(
            draft_marker.encode("utf-8")
        ).hexdigest(),
    )
    citations = tuple(
        PaperCitationDraft(
            chunk_index=chunk.position,
            page_number=chunk.source_spans[0].page_number,
            quote=chunk.text,
        )
        for chunk in chunk_set.chunks
    )
    summary = PaperGlobalSummary.from_draft(
        chunk_set,
        producer=producer,
        summary="A cited external summary of the Transformer paper.",
        citations=citations,
        min_citations=1,
        min_pages=1,
    )
    annotation_set = PaperAnnotationSet.from_draft(
        chunk_set,
        producer=producer,
        annotations=(
            PaperAnnotationDraft(
                kind=PaperAnnotationKind.SOURCE_FACT,
                text="The source describes a recurrence-free architecture.",
                citations=(citations[0],),
            ),
            PaperAnnotationDraft(
                kind=PaperAnnotationKind.CODEX_INTERPRETATION,
                text="Parallel attention is the central design shift.",
                citations=(citations[1],),
            ),
        ),
    )
    return PaperAnnotatedResult(
        source_revision=semantic.source_revision,
        chunk_set=chunk_set,
        global_summary=summary,
        annotation_set=annotation_set,
    )


def build_paper_structure_tree(
    *,
    model: str = "fake-structure",
    quality: Literal["low", "medium", "high"] = "high",
    when: datetime = _WHEN,
) -> PaperStructureTree:
    """Build one valid cited structure tree through its smart constructor.

    The tree carries its own provenance metadata (``as_of`` copied from the
    source's ``when``, a source ref, and the source content hash), so downstream
    fixtures get a self-contained, standalone-storable value.
    """
    result = build_paper_result(when=when)
    producer = PaperStructureProducer(
        model=model,
        prompt_version="test-v1",
        instructions_hash=hashlib.sha256(
            b"test structure instructions"
        ).hexdigest(),
        page_text_chars=1_200,
        max_output_tokens=512,
        max_depth=4,
        max_nodes=16,
    )
    draft = PaperStructureTreeDraft(
        quality=quality,
        root=PaperStructureNodeDraft(
            title="Attention Is All You Need",
            summary="The complete paper structure.",
            start_page=1,
            end_page=2,
            children=(
                PaperStructureNodeDraft(
                    title="Architecture",
                    summary="The recurrence-free architecture.",
                    start_page=1,
                    end_page=1,
                ),
                PaperStructureNodeDraft(
                    title="Attention and results",
                    summary="Multi-head attention and reported results.",
                    start_page=2,
                    end_page=2,
                ),
            ),
        ),
    )
    return PaperStructureTree.from_draft(
        result.source_revision,
        producer=producer,
        draft=draft,
    )


def build_paper_translation_result(
    *,
    draft_marker: str = "original",
    when: datetime = _WHEN,
) -> PaperTranslatedResult:
    """Build one complete page-aligned Japanese translation without IO."""
    semantic = build_paper_result(when=when)
    producer = PaperTranslationProducer(
        generator="codex-interactive",
        model_label=None,
        instructions_hash=hashlib.sha256(
            b"paper translation instructions"
        ).hexdigest(),
        draft_content_hash=hashlib.sha256(
            draft_marker.encode("utf-8")
        ).hexdigest(),
    )
    translation = PaperTranslation.from_draft(
        semantic.source_revision,
        producer=producer,
        translated_pages=(
            "Transformerは系列変換から再帰と畳み込みを取り除く。",
            "マルチヘッド注意は並列射影を用い、翻訳と学習効率を改善する。",
        ),
    )
    return PaperTranslatedResult(
        source_revision=semantic.source_revision,
        translation=translation,
    )
