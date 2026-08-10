# Local Paper Library UI

The optional Streamlit application makes the local paper library usable by a
human without turning QuantMind into a web framework. It binds only to
loopback, uses public `quantmind.library` APIs, and stores mutable reading
organization in a separate SQLite sidecar. It does not call Codex, OpenAI, or
Hugging Face during normal operation.

## Install and Start

Install the UI and local-model extras, cache the fixed model once, and assign
four explicit absolute paths:

```bash
uv pip install -e ".[full,ui]"
python scripts/cache_local_embedding_model.py \
  --cache-dir /absolute/path/to/quantmind-models

export QUANTMIND_LIBRARY_DB=/absolute/path/to/paper-library.sqlite3
export QUANTMIND_UI_DB=/absolute/path/to/paper-library-ui.sqlite3
export QUANTMIND_MODEL_CACHE=/absolute/path/to/quantmind-models
export QUANTMIND_INTAKE_ROOT=/absolute/path/to/paper-intake

streamlit run apps/paper_library/app.py \
  --server.address 127.0.0.1
```

The knowledge and UI paths must be different. Non-loopback addresses are
rejected in both configuration and application startup. Authentication and
remote hosting are intentionally unsupported.

## Views

| View | Human task |
|---|---|
| Dashboard | Understand saved, search-ready, attention, broken, unread, annotated, currently reading, and starred papers. |
| Library | Filter title/author/URI, source, health, reading state, star, tag, and collection; sort and page through sources. |
| Paper detail | Read summaries, typed annotations, and page-aligned Japanese translations; compare English/Japanese pages; review translation pages; open exact citations; add separately labeled explanatory images; render one highlighted PDF page; download the hash-checked PDF/JSON; inspect versions; and manage personal state and reviewed bibliographic display labels. |
| Search | Embed one Japanese or English query locally, pre-filter sidecar candidates, inspect matched projection text and similarity, and return to source evidence. |
| Intake | Explicitly prepare a PDF/URL, hand files to interactive Codex, reload and validate `draft.json`, preview, and separately confirm registration. |
| Audit | Inspect counts, registrations, model identity, fast health, optional read-only deep validation, JSON exports, and sidecar orphans. |

`source_fact` means a draft claim cites source text;
`codex_interpretation` means an explicitly separated interpretation; personal
memo means unverified human state. None of these labels is a probability or an
automatic truth judgment. Semantic similarity scores are ranking values, not
confidence scores.

## Data Ownership and Safety

- The canonical database owns exact PDF bytes, sources, artifacts, vectors,
  registration audit, and catalog projections.
- The sidecar owns display title, reviewed author labels, a publication label,
  reading state, star, personal memo, last page, tags, collections, and
  explanatory image annotations. Sidecar writes never touch canonical SQLite
  bytes. Author labels use one author per line. The publication label preserves
  the source's available precision, such as `Mar. 1952`, instead of inventing a
  day for a month-only citation.
- A visual annotation stores the original PNG, JPEG, or WebP bytes (up to 20
  MB and 40 megapixels), caption, alternative text, creator/provenance, an
  optional link to a canonical text annotation, and a human review label. It
  is never treated as original-paper evidence or added to semantic search.
- Use `unreviewed` until a human checks an image. Use `attention` when a date,
  claim, chart, or provenance needs correction. Use `verified` only after
  comparison with the original paper. The review label records workflow state;
  it is not an automated truth score.
- A Japanese translation is an immutable canonical artifact aligned to every
  physical English page. It is a reading aid, never citation evidence and not
  a semantic-search target. Per-page review status and notes stay in the
  sidecar so human verification never mutates canonical translation text.
- Intake work directories are UUID children of one configured root. Symlinks,
  root escape, silent source/draft overwrite, non-PDF uploads, files over 200
  MB, non-HTTPS URLs, credentials, localhost/private addresses, and unsafe
  redirects are rejected.
- Browsing, catalog, details, and audit do not load embeddings. Registration
  creates document embeddings; search creates only a query embedding.
- The UI contains no canonical edit, delete, repair, re-embed, VACUUM, Codex
  start, polling, API-key, or background scheduler control.

Back up the canonical and sidecar databases separately. The sidecar can be
recreated without affecting knowledge, but doing so loses personal reading
state and attached explanatory images. The UI therefore offers no
sidecar-delete button.

## Verification

Run the network-free shell smoke after installing the `ui` extra:

```bash
python scripts/verify_paper_library_ui.py
```

For a real cached local-model registration/search slice:

```bash
python scripts/verify_local_paper_e2e.py \
  --cache-dir /absolute/path/to/quantmind-models
```

Known limitations are the same as the library MVP: text PDFs only, no built-in
OCR, page-level rather than sentence-level translation alignment, single fixed
E5 model, exact citations only, brute-force local ranking, and no translation
embedding, hybrid search, reranking, optimization, autonomous batches,
authentication, or remote deployment.
