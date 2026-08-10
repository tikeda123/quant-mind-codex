# Build and Persist the Codex Paper Library

This guide builds the current local Paper Library from an empty directory,
registers the first source-backed paper, adds Japanese reading material, and
keeps the resulting databases durable. It also explains how to move an
existing temporary or demo database into a persistent location.

For screen-by-screen daily operation, Codex request examples, bibliographic
metadata handling, and troubleshooting in Japanese, start with the
[日本語運用マニュアル](paper-library-manual-ja.md). The original upstream
repository is [`LLMQuant/quant-mind`](https://github.com/LLMQuant/quant-mind),
and this local application is maintained in the
[`tikeda123/quant-mind-codex`](https://github.com/tikeda123/quant-mind-codex)
fork.

The Paper Library path does not require an external LLM API key. Codex is used
interactively in the checkout and communicates with the deterministic Python
application only through explicit files.

## Resulting Layout

Choose one absolute runtime directory outside the Git checkout:

```text
/absolute/path/to/quantmind-codex-data/
├── paper-library.sqlite3       # canonical PDF, artifacts, vectors, audit
├── paper-library-ui.sqlite3    # personal state and explanatory images
├── migration-manifest.json     # full logical migration inventory (if migrated)
├── migration-acceptance.json   # public-API acceptance (if requested)
├── models/                     # fixed local E5 model cache
└── intake/                     # UUID-scoped preparation work directories
```

SQLite may create `-wal` and `-shm` side files while the application is open.
Do not commit this runtime directory to Git. Back up the two databases as a
pair if personal annotations and reviewed display metadata must be preserved.

## 1. Prerequisites

- Python 3.10 or newer and `uv`.
- Git and an interactive Codex session in this checkout.
- A text-layer PDF, or a public HTTPS URL that resolves to one. The application
  does not perform OCR. OCR a scanned PDF first and visually check equations,
  symbols, tables, and page order before registration.
- Public network access for the initial clone, dependency installation, and
  one explicit model-cache command. Normal registration and search use only
  the cached model.
- The `sqlite3` command is optional but recommended for integrity checks and
  safe backups.

No OpenAI, Hugging Face, or other external LLM API key is required for the
workflow in this guide. Other optional QuantMind Agents SDK flows are separate
and may require their configured provider.

## 2. Clone and Install

```bash
git clone https://github.com/tikeda123/quant-mind-codex.git
cd quant-mind-codex

uv venv
source .venv/bin/activate
uv pip install -e ".[full,ui]"
```

Run the deterministic repository verification before building important data:

```bash
bash scripts/verify.sh
```

## 3. Create the Runtime Root and Cache the Local Model

Set `QUANTMIND_RUNTIME_ROOT` to a durable absolute directory outside the
checkout:

```bash
export QUANTMIND_RUNTIME_ROOT="/absolute/path/to/quantmind-codex-data"
mkdir -p "$QUANTMIND_RUNTIME_ROOT/models" "$QUANTMIND_RUNTIME_ROOT/intake"

python scripts/cache_local_embedding_model.py \
  --cache-dir "$QUANTMIND_RUNTIME_ROOT/models"
```

The cache command downloads the fixed
`intfloat/multilingual-e5-small@fd1525a9fd15316a2d503bf26ab031a61d056e98`
revision. The application then loads it on local CPU with
`local_files_only=True`. There is no model-selection UI or automatic fallback
to another provider.

## 4. Configure and Initialize Both Databases

```bash
export QUANTMIND_LIBRARY_DB="$QUANTMIND_RUNTIME_ROOT/paper-library.sqlite3"
export QUANTMIND_UI_DB="$QUANTMIND_RUNTIME_ROOT/paper-library-ui.sqlite3"
export QUANTMIND_MODEL_CACHE="$QUANTMIND_RUNTIME_ROOT/models"
export QUANTMIND_INTAKE_ROOT="$QUANTMIND_RUNTIME_ROOT/intake"

streamlit run apps/paper_library/app.py \
  --server.address 127.0.0.1 \
  --server.port 8501
```

Open [`http://127.0.0.1:8501`](http://127.0.0.1:8501). The first application
open creates empty databases when the configured files do not exist and
migrates supported older schemas before use. The current canonical database
schema is version 7; the mutable UI sidecar schema is version 4. The two paths
must identify different files.

The initial Dashboard should show zero saved papers. Browsing an empty library
does not load the embedding model or call an external service.

If `sqlite3` is available, inspect the new files after stopping Streamlit:

```bash
sqlite3 "$QUANTMIND_LIBRARY_DB" \
  "PRAGMA user_version; PRAGMA integrity_check;"
sqlite3 "$QUANTMIND_UI_DB" \
  "PRAGMA user_version; PRAGMA integrity_check;"
```

The expected versions are `7` and `4`, and each integrity result must be `ok`.

## 5. Register the First Paper

1. Open **取り込み (Intake)**.
2. Select **PDF upload** for a local file, or **公開HTTPS URL** for a remote
   PDF. Select **Prepare**.
3. Preparation creates one UUID-named intake directory containing immutable
   `source.pdf` and `manifest.json`. The manifest fixes the PDF hash, physical
   page order, extracted page text, parser identity, and draft-policy hash.
4. Ask Codex in this checkout to read the paths displayed by the UI and follow
   [`codex-paper-draft-v1.md`](../contexts/usage/codex-paper-draft-v1.md).
   Codex must write only `draft.json` in that prepared directory. It must not
   invent UUIDs, chunk numbers, page numbers, or quotations.
5. Return to **取り込み** and select `再読込して検証`. Validation checks
   the source hash, page references, exact quotations, citation coverage, and
   resolvable chunks without writing to SQLite.
6. Review the finalization preview, select
   `検証済みbundleをcanonical DBへ登録します`, and select `登録する`.

Registration is atomic. It stores the exact PDF, source revision, cited
summary, typed annotations, chunk set, document vectors, catalog projection,
and registration audit. A validation error writes nothing. Re-registering the
same validated bundle is idempotent.

Local PDFs may not provide machine-readable title, author, or publication
metadata. After checking the first page and journal citation, open **論文詳細 →
読書管理** and enter `個人表示名`, `表示用著者（1行1名）`, and
`表示用公開情報` such as `Mar. 1952`. These labels remain in the sidecar and
do not rewrite canonical evidence.

## 6. Add a Full-Page Japanese Translation

1. Open the registered paper in **論文詳細 → 日本語訳**.
2. Expand `Codex対話で日本語訳を作成・登録`, then select
   `翻訳用ファイルを準備`.
3. Ask Codex to read `source.pdf`, `translation_manifest.json`, and
   [`codex-paper-translation-v1.md`](../contexts/usage/codex-paper-translation-v1.md),
   then write `translation_draft.json` in the displayed directory.
4. Select `translation_draft.jsonを検証`. Every physical page must appear
   exactly once, in order, and the source hash must match the registered paper.
5. Select `確認して日本語訳を登録`.

The Japanese translation is a separate immutable canonical artifact and does
not create embeddings. It is a reading aid, not citation evidence. Per-page
review labels and notes are mutable sidecar state, so a human can record source
comparison without changing the translation text.

## 7. Add Explanatory Images and Reading Metadata

Use **論文詳細 → 画像注釈** to attach a PNG, JPEG, or WebP image.
Record a Japanese `見出し`, `代替テキスト`, `作成者・ツール`, `入手元・作成条件`,
optional linked text annotation, and `確認状態`. The image bytes and review
workflow are stored in the sidecar. They are not paper evidence and are not
included in semantic search.

Use **読書管理** for stars, reading state, notes, tags, collections,
last page, and reviewed bibliographic display labels. Losing the sidecar loses
these values and attached images but does not remove canonical papers.

## 8. Verify the Installation and Registered Data

The UI shell verifier creates isolated temporary databases and never loads the
embedding model:

```bash
python scripts/verify_paper_library_ui.py
```

The local paper verifier also uses a temporary database, but exercises
prepare, deterministic finalization, atomic registration, reopen, PDF-hash
verification, and English/Japanese search with the cached model:

```bash
python scripts/verify_local_paper_e2e.py \
  --cache-dir "$QUANTMIND_MODEL_CACHE"
```

In the running application, use **監査 (Audit)** for counts, registration
history, model identity, fast health, optional read-only deep validation, and
JSON exports. Use **検索 (Search)** with one English and one Japanese query and
open each result back to its cited source page.

## 9. Migrate Every Value from an Existing Database Pair

Do not rely on a database under `/tmp` or `/private/tmp`; the operating system
may remove it. Stop Streamlit and every other writer before migration. The
migration command treats the canonical database and UI sidecar as one unit,
uses SQLite's online-backup API, and refuses to overwrite an existing
destination directory.

```bash
export QUANTMIND_SOURCE_LIBRARY_DB="/absolute/path/to/current-paper-library.sqlite3"
export QUANTMIND_SOURCE_UI_DB="/absolute/path/to/current-paper-library-ui.sqlite3"
export QUANTMIND_RUNTIME_ROOT="/absolute/path/to/quantmind-codex-data"
mkdir -p "$(dirname "$QUANTMIND_RUNTIME_ROOT")"

python scripts/migrate_paper_library.py \
  --source-library-db "$QUANTMIND_SOURCE_LIBRARY_DB" \
  --source-ui-db "$QUANTMIND_SOURCE_UI_DB" \
  --destination-root "$QUANTMIND_RUNTIME_ROOT" \
  --model-cache "/absolute/path/to/existing-fixed-model-cache" \
  --query "portfolio selection covariance" \
  --query "分散投資と共分散"
```

The command inventories every SQLite schema object, table row, and BLOB with
type-aware SHA-256 digests before and after backup. It checks SQLite integrity
and foreign keys, rejects a source that changed during migration, compares the
complete logical inventories, and only then publishes the pair by renaming one
staging directory. This covers source PDFs, page text, cited artifacts,
embeddings, registration history, Japanese translations, personal metadata,
tags, collections, translation reviews, and explanatory image bytes.

When `--model-cache` is supplied, the command also opens the migrated pair
through `PaperLibraryAppService`, reopens every canonical paper and raw PDF,
checks PDF and image hashes, translation page coverage, sidecar references,
and each repeated `--query`. It writes two durable reports:

- `migration-manifest.json`: complete table counts and pre-operation logical
  digests for both databases.
- `migration-acceptance.json`: migrated object counts and semantic-query hit
  counts after public-API verification.

The destination must not already exist. A failed migration never replaces the
source, and the command removes only its private staging directory. Keep the
existing model cache or explicitly cache the fixed revision into a `models/`
directory. Copy old `intake/` work directories only when operator history is
needed; registered source PDF bytes and canonical artifacts already live in
the canonical database.

Point the four runtime variables from step 4 at the durable location, restart
the UI, and compare its paper, annotation, translation, and search counts with
`migration-acceptance.json` in **監査**. Never migrate only one database or
treat a raw file copy made during active writes as a verified migration.

## 10. Routine Backup and Restore

Stop writers and use the same non-overwriting command to back up both
databases to a new dated directory. Omit `--model-cache` for a logical backup,
or include it when the backup itself must pass the operational acceptance:

```bash
export QUANTMIND_BACKUP_ROOT="/absolute/path/to/quantmind-backups/YYYY-MM-DD"
mkdir -p "$(dirname "$QUANTMIND_BACKUP_ROOT")"

python scripts/migrate_paper_library.py \
  --source-library-db "$QUANTMIND_LIBRARY_DB" \
  --source-ui-db "$QUANTMIND_UI_DB" \
  --destination-root "$QUANTMIND_BACKUP_ROOT"
```

Before restoring, stop all writers and preserve the current pair. Point the
four runtime variables at the verified backup directory or migrate that pair
again into a new runtime directory. Start the UI only after the migration
manifest reports `status: verified` for both databases.

## Acceptance Checklist

- The UI binds to `127.0.0.1`, not a network interface.
- Canonical and sidecar database paths are absolute and different.
- Canonical schema is 7, sidecar schema is 4, and both integrity checks are
  `ok`.
- The fixed local model is cached, and normal use performs no download.
- One paper can be reopened with the exact stored PDF and page citations.
- English and Japanese semantic queries return resolvable source evidence.
- A registered Japanese translation covers every physical page.
- Explanatory images and personal metadata survive a sidecar reopen.
- The **監査** view reports no unexpected broken records.
- The migration manifest reports equal full logical digests for both source
  and destination databases.
- The acceptance report records successful public-API reopen and both English
  and Japanese semantic queries.
- Both SQLite databases have a tested paired backup outside the runtime
  directory.

For UI behavior and security limits, continue with the
[Paper Library UI guide](paper-library-ui.md). For the exact interactive
handoff, use the [registration procedure](../contexts/usage/codex-paper-registration.md).
