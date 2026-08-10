# Build a source-first paper result

## Quick Summary

- **Purpose**: Define how one exact PDF revision becomes page-aware cited artifacts or a complete page translation through bounded or deterministic paths.
- **Read when**: Changing `PaperFlow`, paper inputs, cited or translation drafts, summarization limits, citation validation, or paper verification.
- **Status**: Implemented by the config-bound `quantmind.flows.PaperFlow`; cfg type selects semantic, structure, external cited-draft, or translation-draft construction.
- **Core rule**: Preserve and validate the source revision and chunk set before any model-generated summary is accepted.
- **Canonical models**: [Paper source and artifact design](../knowledge/paper.md).

## Contents

- [Contract](#contract)
- [Interactive Cited Draft](#interactive-cited-draft)
- [Interactive Translation Draft](#interactive-translation-draft)
- [Execution Order](#execution-order)
- [Source Revision](#source-revision)
- [Chunk Set](#chunk-set)
- [Cited Global Summary](#cited-global-summary)
- [Bounded Model Calls](#bounded-model-calls)
- [Failure Semantics](#failure-semantics)
- [Persistence and Retrieval](#persistence-and-retrieval)
- [Verification Slice](#verification-slice)
- [Out of Scope](#out-of-scope)

## Contract

`PaperFlow(PaperSemanticCfg(...)).build(input)` returns one validated `PaperSemanticResult`. `PaperFlow` binds the immutable `PaperSemanticCfg` once and its cfg **type** selects this source-first shape, so `batch_run(flow.build, inputs)` runs a batch under one setting. It is the single entry point for the semantic shape; there is no standalone `*_flow` function.

```text
PaperSemanticResult
├── source_revision: PaperSourceRevision
├── chunk_set: PaperChunkSet
└── global_summary: PaperGlobalSummary
```

The result is source-first. It does not return a model-authored paper tree, and V1 does not define `PaperTree`. The source revision is an immutable anchor for independently versioned artifacts. A different splitter configuration may produce another chunk set for the same source; a different model, prompt, input chunk set, or output bound may produce another summary. These versions coexist instead of overwriting each other.

`PaperFlow(PaperCitedDraftCfg()).build(CitedPaperDraftInput(...))` is a third,
deterministic branch. It returns `PaperAnnotatedResult`, containing the same
source/chunk/summary relationship plus one independently versioned
`PaperAnnotationSet`. It does not construct an Agents SDK runner or call any
model provider.

`PaperFlow(PaperTranslationDraftCfg()).build(PaperTranslationDraftInput(...))`
is a fourth deterministic branch. It returns `PaperTranslatedResult`, pairing
the exact source revision with one independently versioned, self-contained
`PaperTranslation`. It does not chunk, embed, construct an Agents SDK runner,
or call a model provider.

V1 accepts:

| Input | Behavior |
|---|---|
| `ArxivIdentifier` | Resolve and fetch the exact PDF revision. The canonical source records a versioned arXiv ID such as `1706.03762v7`. |
| `HttpUrl` | Fetch the URL and require PDF content. |
| `LocalFilePath` | Read the local file and require PDF content. |
| `RawText` | Reject because it has no physical page evidence. |
| `DoiIdentifier` | Reject until an exact open-PDF resolver exists. |

## Interactive Cited Draft

The operator first runs `scripts/prepare_codex_paper.py` against a local PDF or
public HTTPS PDF. Preparation writes an exact `source.pdf` and an extra-forbid
`manifest.json`, including source hash, size, parser identity, ordered physical
page text hashes, and the draft-policy hash. Public URL preparation validates
credentials, scheme, DNS addresses, and every redirect destination, then never
refetches the URL.

Codex may read those files interactively and write only `draft.json` under the
[cited-draft policy](../../usage/codex-paper-draft-v1.md). QuantMind then
re-reads and re-parses `source.pdf`, validates all manifest fields, chunks it
deterministically, and resolves each page/quote pair to exactly one chunk. Zero
or multiple matches fail closed. Drafts contain no canonical UUID, chunk
position, source metadata, or timestamp.

The finalizer creates a `PaperGlobalSummary` and `PaperAnnotationSet` with
producer identities that include the exact draft hash and policy hash. Summary
coverage remains at least three citations across two pages by default. Every
annotation requires an exact citation. The resulting value remains independent
of persistence; callers explicitly use `put_annotated_paper()`.

## Interactive Translation Draft

`scripts/prepare_codex_translation.py` writes `source.pdf` and
`translation_manifest.json`, pinning the exact PDF hash and size, parser
identity, every physical page's English text/hash/empty flag, the fixed `en` →
`ja` pair, and the translation-policy hash. Interactive Codex writes only
`translation_draft.json` under the
[translation policy](../../usage/codex-paper-translation-v1.md). Python and
the UI never call Codex or another LLM API.

Finalization re-reads and re-parses the PDF, rejects parser or page drift, and
requires exactly one ordered translation entry for every physical source page.
Non-empty English pages require non-empty Japanese text; empty pages must stay
empty. Code attaches the exact English page text, mints every ID and hash, and
returns no partial result. `put_translation()` is a separate explicit,
vectorless persistence step.

## Execution Order

The operation has a strict order:

1. Resolve the input to exact bytes and source metadata.
2. Parse the PDF into ordered physical pages, blocks, screenshots, and extracted images.
3. Build and validate `PaperSourceRevision`, including content-addressed asset references and blobs.
4. Chunk each page with LlamaIndex while retaining page and character spans.
5. Build and validate `PaperChunkSet` with code-owned IDs and membership.
6. Tile the chunk set into fixed-size research groups in code, so every chunk is covered exactly once.
7. Fan out one bounded research agent per group (bounded concurrency), each returning typed findings for its own range only.
8. Run one reducer agent over the collected findings to synthesize the summary draft.
9. Resolve model-returned chunk/page coordinates into canonical citations in code.
10. Build and validate `PaperGlobalSummary` and the cross-artifact `PaperSemanticResult`.

Steps 3, 5, 9, and 10 mint no IDs in the flow: the flow calls the knowledge-layer smart constructors `PaperSourceRevision.from_parsed`, `PaperChunkSet.from_parsed_chunks`, and `PaperGlobalSummary.from_draft`, which own every ID, content/producer hash, and citation resolution. The flow only fetches, parses, reads asset bytes, and maps those path-based artifacts into knowledge-native inputs. See [orchestration principles](../operations/orchestration.md).

A summarization failure occurs after source and chunks exist in memory, but the build returns no partial success value. Persistence is a separate explicit operation.

## Source Revision

`PaperSourceRevision` owns the exact fetched bytes and deterministic parser result. Its ID is derived from the source SHA-256 hash. It records:

- typed source metadata, financial `as_of`, `available_at`, publication time, exact arXiv revision, title, and authors;
- parser name, parser version, cleanup version, and every 1-based physical page;
- page text, source blocks, bounding boxes, screenshot references, and extracted-image references;
- content-addressed references for the raw PDF and every retained visual asset;
- exact asset bytes while crossing from the flow to persistence.

The raw asset hash must equal the source hash. Every asset ID is derived from source revision, asset kind, page, and content hash. Page references must resolve to assets on that page. Canonical JSON excludes blob bytes; [`LocalKnowledgeLibrary.put_paper()`](../library/local.md) stores them in linked SQLite rows.

For arXiv, the source must record an exact version suffix. Fetch time is never used as publication time. `available_at` uses exact revision metadata when supplied and otherwise falls back to the earliest reliable observation supported by the input.

## Chunk Set

`PaperChunkSet` is built before summarization. Its producer identity contains the LlamaIndex sentence-splitter version, chunk size, and overlap. The producer configuration hash and source revision deterministically identify the artifact.

Each `PaperChunk` has a stable code-owned ID, contiguous position, exact text hash, and one or more `PaperSourceSpan` values. A span retains its 1-based page number, page-local character offsets, block boxes, and visual asset IDs. The chunk-set content hash covers ordered membership, content hashes, and source spans.

Chunk IDs, artifact IDs, hashes, positions, and membership are not model outputs. Repeating the same source and configuration produces the same source, chunk-set, and chunk IDs.

## Cited Global Summary

The model returns only a draft containing summary prose and citation coordinates: chunk index, page number, and an optional quote. It never chooses canonical UUIDs, artifact lineage, source facts, or timestamps.

Code accepts the draft only when:

- every chunk index exists;
- every cited page is present in that chunk's source spans;
- every supplied quote occurs verbatim in the cited chunk;
- citation count meets `min_summary_citations`;
- distinct cited-page count meets `min_summary_pages`.

`PaperGlobalSummary.from_draft` performs this resolution and coverage check and then mints the artifact; the flow supplies only the draft and the coverage policy. Its producer identity includes model, prompt version, input chunk-set ID, instructions hash, maximum output tokens, and research group size. Its lineage contains the exact input chunk-set locator. Summary citations must resolve through that chunk set to source pages.

## Bounded Model Calls

Summarization is a deterministic map-reduce, not an autonomous coordinator. Code — not a model — decides the decomposition: it tiles the chunk set into `summary_research_group_size` groups and runs one research agent per group, so complete chunk coverage is guaranteed by construction and never needs to be reconciled afterward.

`PaperSemanticCfg` exposes only structural bounds:

- `summary_research_group_size` sets how many consecutive chunks each research agent receives;
- `summary_concurrency` bounds simultaneous research-agent runs;
- `max_summary_output_tokens` caps each agent's output through `ModelSettings.max_tokens`;
- `timeout_seconds` bounds the reducer call via `asyncio.wait_for`;
- `min_summary_citations` and `min_summary_pages` set the accepted-summary coverage policy;
- `summary_prompt_version` and `summary_instructions` version the semantic producer.

Bounding is delegated to the Agents SDK (per-agent `max_tokens`, structured `output_type`) and to `asyncio` (a `Semaphore` for concurrency, `wait_for` for the timeout). There is no hand-rolled token accountant, no coordinator agent, and no `Agent.as_tool()` research tool. The removed manager/worker design paid for an autonomous coordinator and then suppressed its nondeterminism with a concurrency-safe budget — exactly the runtime the SDK already provides. [Orchestration principles](../operations/orchestration.md) records when the agentic pattern is warranted instead.

## Failure Semantics

- Non-PDF input raises `UnsupportedContentTypeError`.
- Unresolved DOI input raises `NotImplementedError`.
- Fetching, parsing, or missing parser assets raise their source error and produce no result.
- Empty chunk output is invalid.
- Invalid or insufficient summary citations raise `PaperCitationValidationError`.
- A research finding that cites outside its assigned group is rejected in code; a reducer timeout raises `PaperSummaryError`.
- Any canonical identity, content hash, membership, lineage, or cross-artifact mismatch fails Pydantic validation.

No failure is converted into a partially valid `PaperSemanticResult`. Callers may retry with the same source and producer settings; stable IDs make successful repeated runs idempotent.

## Persistence and Retrieval

The flow performs no library write. Callers explicitly pass its result to `LocalKnowledgeLibrary.put_paper()`. That method prepares all required summary and chunk embeddings before one SQLite transaction, so provider failure cannot leave a partial paper.

Collection search uses library-owned projections. A summary hit resolves to `PaperGlobalSummary`; a chunk hit resolves to `PaperChunk`. `SemanticHit.locator` carries source revision, artifact, artifact kind, and optional member ID. `SemanticHit.projection` explains the rebuildable embedding projection used for ranking.

## Verification Slice

Offline tests use fixed generated PDFs and fake summary/embedding providers. They verify exact source bytes, page evidence, stable IDs, citation rejection, bounds, atomic writes, reopen behavior, projection reuse, multi-version coexistence, search, and locator resolution.

The bounded live slice is:

```bash
python scripts/verify_pdf_rag_e2e.py
```

It fetches exact arXiv revision `1706.03762v7`, parses at least 15 physical pages, creates multiple page-aware chunks, delegates complete coverage to bounded `gpt-5.6-luna` research subagents, synthesizes a cited global summary, persists with `text-embedding-3-small`, closes and reopens the database, runs summary and chunk searches, resolves every returned locator, and prints useful summary, page, citation, and score diagnostics. The dedicated `paper-flow` job in `.github/workflows/e2e.yml` owns this non-required public-network check.

## Out of Scope

- model-authored canonical IDs, hashes, lineage, or source metadata;
- a V1 paper tree or PageIndex structure;
- HTML, Markdown, raw-text, or scanned-document OCR paper ingestion;
- DOI-to-open-PDF resolution;
- question answering over search results;
- hidden or unbounded model calls;
- implicit persistence from the paper build;
- calling Codex from Python, the UI, a scheduler, or an agent facade;
- fuzzy citation matching, OCR, and HTML-paper ingestion.
