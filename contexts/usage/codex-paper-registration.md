# Register a paper with interactive Codex

## Quick Summary

- **Purpose**: Prepare, draft, validate, register, reopen, and search one paper
  without calling Codex from Python or requiring an external LLM API key.
- **Read when**: Operating the cited-paper path from a Codex task or the local
  management UI.
- **Boundary**: Codex writes `draft.json` interactively; Python only performs
  deterministic PDF processing, validation, local embedding, SQLite storage,
  and retrieval.

## Contents

- [Procedure](#procedure)
- [Model Cache and References](#model-cache-and-references)

## Procedure

1. Stage a local PDF or public HTTPS PDF in a new work directory:

   ```bash
   python scripts/prepare_codex_paper.py \
     --input /absolute/path/paper.pdf \
     --workdir /absolute/path/paper-work
   ```

2. Ask Codex in this checkout to read `source.pdf`, `manifest.json`, and
   [`codex-paper-draft-v1.md`](codex-paper-draft-v1.md), then save only
   `draft.json` in the work directory. The Python process does not start or
   invoke Codex.
3. Finalize the files with
   `PaperFlow(PaperCitedDraftCfg()).build(CitedPaperDraftInput(...))`. Fix any
   reported page or exact-quote mismatch in `draft.json` and rerun.
4. Open `LocalKnowledgeLibrary.open_local(...)` using a pre-cached fixed
   `intfloat/multilingual-e5-small` revision and call
   `put_annotated_paper(result)`.
5. Close and reopen the library, read the registration with
   `get_annotated_paper(registration_id)`, and run English and Japanese
   `SemanticQuery` values.

The helper never writes `draft.json` and refuses any work directory that
already contains one. It overwrites `source.pdf` and `manifest.json` only when
`--replace-workdir` is supplied. URL preparation accepts public HTTPS only and
validates every redirect destination before connecting. A validation failure
writes nothing to the knowledge database. Post-commit verification never
auto-deletes evidence on failure.

## Model Cache and References

Cache the exact revision only through an explicit operator action:

```bash
python scripts/cache_local_embedding_model.py \
  --cache-dir /absolute/path/to/quantmind-models
```

Normal open/register/search calls never download weights. The complete Python
finalize/register/reopen example is
[`examples/flows/paper_cited_draft.py`](../../examples/flows/paper_cited_draft.py).
The optional management UI is documented in
[`docs/paper-library-ui.md`](../../docs/paper-library-ui.md).
