# Local Semantic Knowledge Library

`LocalKnowledgeLibrary` persists canonical QuantMind values in SQLite and ranks rebuildable text-embedding projections with a private LlamaIndex retriever. The canonical storage, transaction, financial-time, migration, and PageIndex boundaries live in the [local library design](../contexts/design/library/local.md).

## API-key-free Interactive Paper Registration

The cited-draft path separates interactive authorship from deterministic
library work. Python never calls Codex: an operator prepares exact files, asks
Codex in the repository task to write `draft.json`, and then validates and
registers that file.

Install the local-model dependencies and explicitly cache the one pinned model
revision while network access is available:

```bash
uv pip install -e ".[full]"
python scripts/cache_local_embedding_model.py \
  --cache-dir /absolute/path/to/quantmind-models
```

Normal library and UI execution uses `local_files_only=True`; a cache miss
fails closed and never falls back to a network download. The fixed identity is
`intfloat/multilingual-e5-small@fd1525a9fd15316a2d503bf26ab031a61d056e98#e5-query-passage-v1`
with 384 normalized dimensions. Documents receive the E5 `passage:` prefix and
queries receive `query:`. There is no model selector, provider registry,
multiple backend, hybrid search, reranker, or scheduler.

Prepare one local or public HTTPS PDF, then follow the printed path to the
[interactive draft contract](../contexts/usage/codex-paper-draft-v1.md):

```bash
python scripts/prepare_codex_paper.py \
  --input /absolute/path/paper.pdf \
  --workdir /absolute/path/paper-work
```

After Codex has saved `/absolute/path/paper-work/draft.json`, run the focused
example. It finalizes without an LLM call, atomically registers source bytes,
summary, annotations, vectors, and audit evidence, then closes, reopens, and
searches in Japanese:

```bash
python examples/flows/paper_cited_draft.py \
  --manifest /absolute/path/paper-work/manifest.json \
  --draft /absolute/path/paper-work/draft.json \
  --database /absolute/path/paper-library.sqlite3 \
  --cache-dir /absolute/path/to/quantmind-models
```

See the complete [operator procedure](../contexts/usage/codex-paper-registration.md).

## API-key-free Interactive Paper Translation

Translation uses a second deterministic handoff. Preparation pins the exact
PDF, parser identity, ordered page text, language pair, and policy hash. Codex
is used only in the active repository conversation to write the JSON; Python
and the UI never invoke Codex or another LLM.

```bash
python scripts/prepare_codex_translation.py \
  --input /absolute/path/paper.pdf \
  --workdir /absolute/path/translation-work
```

After `translation_draft.json` exists, validate and register all pages:

```bash
python examples/flows/paper_translation_draft.py \
  --manifest /absolute/path/translation-work/translation_manifest.json \
  --draft /absolute/path/translation-work/translation_draft.json \
  --database /absolute/path/paper-library.sqlite3
```

`put_translation()` writes the exact source, one immutable
`PaperTranslation`, normalized `PaperTranslationPage` members, and an
idempotent audit record in one transaction. It creates no document embedding.
`open_translation()` restores the self-contained artifact, whose members hold
both exact English source text and Japanese reading text. Original English
pages remain the only citation evidence. `get_paper_details()` and the catalog
expose translation versions and counts; page review status belongs to the UI
sidecar, not canonical knowledge.

## Store a Paper Flow Result

Run `PaperFlow(PaperSemanticCfg(...)).build()` first, then explicitly store its complete result:

```python
result = await PaperFlow(
    PaperSemanticCfg(model="gpt-5.6-luna"),
).build(ArxivIdentifier(id="1706.03762v7"))

library = await LocalKnowledgeLibrary.open(
    ".quantmind/library.db",
    embedding_model="text-embedding-3-small",
)
try:
    await library.put_paper(result)
finally:
    await library.close()
```

`put_paper()` persists the exact source PDF and retained parser assets, one page-aware chunk-set artifact, one cited global-summary artifact, explicit lineage, and required summary/chunk projections. It obtains all affected embeddings before opening the SQLite transaction, so an embedding failure leaves no partial paper.

Putting the same result again is safe and reuses valid vectors. A changed splitter or summary producer creates another independently addressable artifact version for the same source.

## Store and Resolve a Structure Tree

After building a self-contained `PaperStructureTree` with `PaperFlow`, persist just the tree — no source, chunk, summary, or embedding projections — then reopen it by id:

```python
tree_flow = PaperFlow(PaperStructureCfg(model="gpt-5.6-luna"))
structure = await tree_flow.build(ArxivIdentifier(id="1706.03762v7"))

await library.put(structure)                        # standalone; no source needed
tree = await library.open_structure(structure.id)   # identical self-contained value
```

The structure tree is derived only from the exact source pages and structuring producer configuration. Splitter settings and chunk-set versions do not affect its identity. The tree is **self-contained**: its leaf nodes carry their own page text and it carries its own provenance metadata (`as_of` + a light source ref), so it round-trips through `put()` / `open_structure()` to an identical value and can be persisted and retrieved from with no source or chunk set present. A node `ArtifactLocator` passed to `resolve()` returns a `TreeNode` with its stored `content` (no query-time refill). Building node projections and semantic hybrid seeding are deferred to P2.

## Reopen, Search, and Resolve

Opening a library performs no embedding or network request. Search embeds only the query when stored projections are reusable:

```python
from quantmind.knowledge import PaperArtifactKind

library = await LocalKnowledgeLibrary.open(
    ".quantmind/library.db",
    embedding_model="text-embedding-3-small",
)
try:
    summary_hits = await library.search(
        SemanticQuery(
            text="What is the paper's central contribution?",
            artifact_kinds=[PaperArtifactKind.GLOBAL_SUMMARY],
            top_k=3,
        )
    )
    chunk_hits = await library.search(
        SemanticQuery(
            text="How does multi-head attention work?",
            artifact_kinds=[PaperArtifactKind.CHUNK_SET],
            top_k=5,
        )
    )
    evidence = [
        await library.resolve(hit.locator)
        for hit in (*summary_hits, *chunk_hits)
    ]
finally:
    await library.close()
```

A `paper_summary` hit resolves to `PaperGlobalSummary`. A `paper_chunk_set` hit has a member ID and resolves to the exact `PaperChunk`, including source-page spans. Structure trees are retrieved by reasoning over titles and summaries through `AgenticRetriever(RetrievalCfg(...)).retrieve()` in `quantmind.mind` — an LLM agent traverses the structure — not by semantic search in the vectorless MVP. Every `SemanticHit` also includes:

- `matched_text`, the exact library-owned projection used for ranking;
- `projection`, the projection version, model, dimensions, and content hash;
- source metadata, financial time, and canonical citations;
- compatibility fields `item_id`, `node_id`, and `item_type`.

Use `get_artifact(artifact_id)` when the aggregate ID is already known. Use `get_paper(source_revision_id, chunk_set_id=..., summary_id=...)` to reconstruct a compatible result. Artifact IDs may be omitted only when one unambiguous linked chunk-set/summary pair exists.

For registered interactive results, use `get_annotated_paper(registration_id)`
to reconstruct the exact source/chunk/summary/annotation bundle. The following
read-only management APIs do not load the local model:

- `list_papers(PaperCatalogQuery(...))` for bounded filters, sort, health, and
  cursor pagination;
- `get_paper_details(...)` for deep canonical and registration validation;
- `get_paper_asset(...)` for hash-checked raw PDF or retained evidence bytes;
- `list_registrations(...)` and `get_registration(...)` for immutable audit;
- `inspect_library()` for fast source, search-ready, attention, broken, page,
  annotation, and database-size counts.

`SemanticQuery.source_revision_ids` is applied before ranking. The management
UI uses it only after resolving human tags, collections, reading state, and
stars in a separate sidecar database.

The complete runnable path is [examples/flows/paper.py](../examples/flows/paper.py).

## Conventional Knowledge

`put(item)` and `get(item_id)` remain available for supported `BaseKnowledge` values such as `News`, `Earnings`, `Factor`, `Thesis`, and generic trees. Canonical models do not implement `embedding_text()`; library-owned projection rules select searchable text.

`SemanticQuery` supports item type, source kind, confidence, tag, tree, `as_of`, and `available_at` filters. Use `available_at_before` to prevent look-ahead: an `as_of` cutoff alone does not prove that the source was observable at that time.

## Bundled Compatibility Example

The bundled AI-infrastructure scenario contains primary-source-backed `News`, `Earnings`, and one pre-V1 `LegacyPaper` tree. Its canonical JSON is precompiled into a SQLite database with six `text-embedding-3-small` targets, so the example embeds only the query:

```bash
python examples/library/semantic_search.py
```

`LegacyPaper` exists only so older databases and this auditable example remain readable. New paper ingestion uses `PaperSemanticResult` and `put_paper()`.

Maintainers can regenerate the bundle after changing source data, projection rules, or the storage schema:

```bash
python scripts/examples/build_ai_infrastructure_bundle.py
```

The bundle's facts and short citations come directly from the [Compute Trends Across Three Eras of Machine Learning paper](https://arxiv.org/abs/2202.05924), [Microsoft's FY2025 AI-datacenter investment announcement](https://blogs.microsoft.com/on-the-issues/2025/01/03/the-golden-opportunity-for-american-ai/), and [NVIDIA's FY2026 Q1 results](https://investor.nvidia.com/news/press-release-details/2025/NVIDIA-Announces-Financial-Results-for-First-Quarter-Fiscal-2026/default.aspx).

Missing IDs raise `KeyError`. Canonical payloads, linked rows, or asset metadata that no longer agree raise `RuntimeError` with stale-data context. Invalid vector bytes and inconsistent dimensions raise a corrupt-index `RuntimeError`; provider or query dimension mismatches raise `ValueError`.

## Schema 7 Migration and Limits

Opening a schema-5 or schema-6 database migrates it through schema 7. The 5→6
step backfills the paper catalog; 6→7 adds translation registration audit
without rewriting canonical payloads. Before
opening an important database with new code, stop all writers, checkpoint or
copy the SQLite database and side files, hash the backup, and rehearse the
migration on the copy. There is no automatic downgrade, repair, delete, or
re-embed operation.

The initial local scope supports text PDFs with physical page evidence. It does
not support OCR, HTML papers, DOI resolution, autonomous jobs, annotation
embeddings, multiple local models, hybrid search, reranking, or ANN tuning.
The management UI and its limitations are documented in
[Paper Library UI](paper-library-ui.md).
