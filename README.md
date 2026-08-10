
<p align="center">
  <img src="assets/quantmind-wordmark.png" width="240">
</p>

<p align="center">
  <img src="assets/quant-mind.png" width="400">
</p>

<p align="center">
  <b>Transform Financial Knowledge into Actionable Intelligence</b>
</p>
<p align="center">
  <b>This fork: a local, Codex-operated paper library with page evidence, Japanese reading aids, and API-key-free semantic search</b>
</p>
<p align="center">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  </a>
  <a href="https://python.org">
    <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python Version">
  </a>
  <img src="https://img.shields.io/badge/Codex-interactive-412991.svg" alt="Codex interactive">
  <img src="https://img.shields.io/badge/Paper_Library_LLM_API_key-not_required-success.svg" alt="Paper Library requires no external LLM API key">
</p>
<p align="center">
  <a href="#-codex-paper-library-this-fork">Codex Paper Library</a> •
  <a href="docs/paper-library-setup.md">Build Database</a> •
  <a href="#-knowledge-engineering">Knowledge Engineering</a> •
  <a href="#-the-vision">The Vision</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-evaluation-in-design">Evaluation</a> •
  <a href="#-in-production-llmquant-data">In Production</a> •
  <a href="#%EF%B8%8F-roadmap">Roadmap</a> •
  <a href="#-contributing">Contributing</a>
</p>


This repository is the **Codex-focused fork** [`tikeda123/quant-mind-codex`](https://github.com/tikeda123/quant-mind-codex). It keeps the upstream [QuantMind](https://github.com/LLMQuant/quant-mind) knowledge library and adds a human-operated, local Paper Library in which Codex prepares cited research artifacts through explicit files rather than through an application API.

QuantMind is an information processor for quantitative finance: it refines raw financial information — papers, news, filings — into structured financial knowledge that downstream retrieval and reasoning can trust. Every piece of knowledge is typed, keeps its citation, and knows its timestamp, so it persists and time-queries standalone.

The people who build these refinement flows are increasingly agents — coding agents, with humans behind them. So the repository is designed agent-oriented: you open the checkout, describe the pipeline you want, and an agent builds it here against the repo's contracts, skills, and deterministic verification. It is also a perfectly good importable Python library.

### 📚 Codex Paper Library (This Fork)

> **日本語概要:** このforkは、PDFまたは公開HTTPS URLから論文を取り込み、Codexとの対話でページ根拠付き要約・注釈と全ページ日本語訳を作成し、原典・生成物・ローカル埋め込みをSQLiteへ保存するローカル論文DBです。PythonやStreamlitからCodexをAPIとして呼び出すことはありません。

The main fork-specific surface is [`apps/paper_library/`](apps/paper_library/): a loopback-only Streamlit workbench for building and using a source-backed paper collection. Codex is the interactive operator; Python is limited to deterministic preparation, validation, local embedding, persistence, and retrieval.

```mermaid
flowchart LR
    A["Text PDF or public HTTPS PDF"] --> B["Prepare source.pdf + manifest.json"]
    B --> C["Interactive Codex writes cited draft + Japanese translation"]
    C --> D["Validate PDF hash, pages, and exact quotes"]
    D --> E["Atomically register canonical artifacts in SQLite"]
    E --> F["Search locally and manage papers in the loopback UI"]
```

| Area | Implemented behavior |
|---|---|
| Source preservation | Stores the exact PDF bytes, page-aware extracted text, hashes, source metadata, and registration history. |
| Codex research handoff | Uses `source.pdf`, `manifest.json`, and strictly validated JSON drafts. Codex creates cited summaries and typed `source_fact` / `codex_interpretation` annotations without inventing IDs, pages, chunks, or quotes. |
| Japanese reading | Stores an immutable Japanese translation for every physical English page, with per-page human review state kept separately. The English source remains the citation authority. |
| Explanatory images | Attaches PNG, JPEG, or WebP visual annotations with Japanese caption and alternative-text explanations, provenance, optional links to text annotations, and review status. Images are reading aids, not source evidence. |
| Local semantic search | Uses one fixed revision of `intfloat/multilingual-e5-small` on local CPU. Registration embeds document projections; search embeds one query and performs local similarity ranking. |
| Human management | Provides Dashboard, Library, Paper Detail, Search, Intake, and Audit views, plus reading state, stars, tags, collections, notes, reviewed title/author/publication labels, source downloads, and page previews. |
| Storage boundaries | Keeps canonical source/artifact/vector data in one SQLite database and mutable personal/UI state in a separate sidecar database. Sidecar edits never rewrite canonical evidence. |

The operational boundary is deliberate:

- Python and Streamlit **do not invoke Codex, OpenAI, or another external LLM API**. A human asks Codex to read the staged files and write the required draft file.
- Model weights are downloaded only by an explicit cache command. Normal registration and search use `local_files_only=True`; no Hugging Face API key is required.
- Intake is interactive. There is no unattended batch scheduler, background Codex polling, provider registry, model-selection UI, hybrid search, reranker, or autonomous repair loop.
- Scanned PDFs require OCR before intake. Translation is page-aligned reading assistance, and explanatory images are not included in semantic search or accepted as paper evidence.
- Runtime PDFs, model weights, intake files, and SQLite databases are operator data outside Git history. Migrate or back up the canonical and sidecar databases as one verified pair.
- The API-key-free guarantee applies to this local Paper Library path. Other optional QuantMind Agents SDK flows remain available and may require their configured model provider.

Start with the [current database construction guide](docs/paper-library-setup.md). It covers a new persistent installation, first-paper registration, Japanese translation, explanatory images, verification, migration from a temporary database, and paired SQLite backup. The [Paper Library UI guide](docs/paper-library-ui.md), [interactive registration procedure](contexts/usage/codex-paper-registration.md), [cited draft contract](contexts/usage/codex-paper-draft-v1.md), and [translation contract](contexts/usage/codex-paper-translation-v1.md) define the detailed operating rules.

### 📰 News
| 🗞️ News        | 📝 Description                                                                 |
|----------------|-------------------------------------------------------------------------------|
| 📚 2026-08 | Added the Codex-operated local Paper Library: cited PDF/URL intake, canonical SQLite storage, full-page Japanese translations, explanatory image annotations, fixed local multilingual search, and a six-view management UI. |
| 🛠️ 2026-07 | Rebuilding the repo **agent-native** — contexts, skills, hooks — so a coding agent can do QuantMind-quality work inside the checkout. |
| 🎉 Accepted at NeurIPS 2025 Workshop | Our paper **[Quant-Mind](#)** has been accepted to the **[NeurIPS 2025 GenAI in Finance Workshop](https://sites.google.com/view/neurips-25-gen-ai-in-finance/home)** !🚀 |
| 📢 First Release on GitHub  | **Quant-Mind** is now live on GitHub — please check it out and join us! 🤗 |


### 🧩 Knowledge Engineering

**Any source → typed knowledge.**

<p align="center"><img src="assets/v1-context-engineering.png" width="920" alt="any source through preprocess and flows into typed knowledge and applications"></p>

*The target surface — shipping today: `PaperFlow` · `collect_news`; see Roadmap.*

- **Deterministic preprocess** — `fetch` / parse / `format` + `clean` produce source-faithful values with no model in the loop, so provenance is exact and replayable.
- **Config-driven operations** — `PaperFlow(cfg).build(input)` binds an immutable build config once and applies it per input; `collect_news` collects a replayable source window; `batch_run` fans any operation across a list of inputs. You never write `asyncio.gather` boilerplate.
- **Typed knowledge shapes** — a `Paper` structure tree for whole documents, and flat cards for `News` / `Earnings` / `Factor` / `Thesis`. Every artifact is self-contained: it carries its own text, an `as_of` timestamp, and a light source ref, so it persists and time-queries standalone.
- **Retrieval over that knowledge** — `rag/` (chunking + BM25 / similarity), `library/` (local persistence + meaning-based search), and `mind/` (agentic, reasoning-based retrieval). Together they serve RAG and Agentic RAG, deep research, and data-MCP serving.

This is the substance shipped as the NeurIPS 2025 GenAI-in-Finance workshop paper (**arXiv:2509.21507**). The always-current statement lives in [`contexts/design/positioning.md`](contexts/design/positioning.md).

### 🧠 The Vision

**Harness engineering — any agent → domain specialist.**

<p align="center"><img src="assets/v2-harness-engineering.png" width="920" alt="the quant-mind harness: context layer, code layer, workspace, deterministic verify, deliverables"></p>

> **Don't import it. Open it.**

The repository itself is the product surface — we call this **harness engineering**. Its `AGENTS.md` / `CLAUDE.md` contracts, progressive-disclosure `contexts/`, portable skills, and Claude + Codex hooks, all gated by a deterministic verify, upgrade a general coding agent into one that reliably does QuantMind-quality work. The bet: **a weak model in a good harness beats a strong model running bare.**

- **Repo-level contracts** — `AGENTS.md` / `CLAUDE.md` state the always-on rules once, in one source, for every agent that opens the repo.
- **Progressive-disclosure `contexts/`** — agent-facing pages with a Quick Summary / Contents preview, so an agent loads only the one page a task needs.
- **Portable skills** — `quantmind-dev` ships today (contributor setup / commit / PR / component workflow), mirrored for Claude and Codex.
- **Claude + Codex hooks** — shared hook scripts give both agents identical hard guarantees without maintaining two copies of a rule.
- **Deterministic verify** — `scripts/verify.sh` runs lint + types + import boundaries + tests, fast-failing in a fixed order; CI runs the exact same script.

See [`contexts/dev/harness-engineering.md`](contexts/dev/harness-engineering.md) for the enforcement mechanics.


### 🚀 Quick Start

#### The agent path (recommended)

QuantMind is meant to be opened, not imported. Point a coding agent at the checkout and describe the pipeline you want:

```bash
git clone https://github.com/tikeda123/quant-mind-codex.git
cd quant-mind-codex && codex
```

Then, in the agent session:

> "Build me a source-first paper artifact for arXiv 1706.03762, then persist it and search the summary."

The agent reads the repo's contracts (`AGENTS.md`), loads the relevant `contexts/` pages, writes the pipeline, and runs `scripts/verify.sh` before it hands the change back.

#### The library path

QuantMind is still a normal Python package. We use [uv](https://github.com/astral-sh/uv) for package management.

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
```

`PaperFlow` refines one arXiv PDF into a self-contained paper artifact. The cfg **type** selects the knowledge shape (`PaperStructureCfg` → `PaperStructureTree`, `PaperSemanticCfg` → `PaperSemanticResult`). Bind a `PaperStructureCfg` to build a source-native **structure tree** — a hierarchy of page-cited nodes:

```python
import asyncio

from quantmind.configs import PaperStructureCfg
from quantmind.configs.paper import ArxivIdentifier
from quantmind.flows import PaperFlow


async def main() -> None:
    flow = PaperFlow(PaperStructureCfg(model="gpt-5.6-luna"))
    tree = await flow.build(ArxivIdentifier(id="1706.03762v7"))
    print(tree.id, len(tree.nodes))


asyncio.run(main())
```

Prefer the **semantic** shape — a page-aware chunk set plus one cited global summary you can embed and retrieve over? Bind a `PaperSemanticCfg` instead — same class, different cfg:

```python
import asyncio

from quantmind.configs import PaperSemanticCfg
from quantmind.configs.paper import ArxivIdentifier
from quantmind.flows import PaperFlow


async def main() -> None:
    flow = PaperFlow(PaperSemanticCfg(model="gpt-5.6-luna", chunk_size=512))
    result = await flow.build(ArxivIdentifier(id="1706.03762v7"))
    print(result.global_summary.summary)
    print(result.source_revision.id, result.chunk_set.id)


asyncio.run(main())
```

#### Fan out a batch with `batch_run`

```python
import asyncio
from datetime import datetime, timedelta, timezone

from quantmind.configs import NewsCollectionCfg, NewsWindow
from quantmind.flows import batch_run, collect_news


async def main() -> None:
    end = datetime.now(timezone.utc)
    windows = [
        NewsWindow(
            source="pr-newswire",
            start=end - timedelta(days=day + 1),
            end=end - timedelta(days=day),
        )
        for day in range(3)
    ]
    result = await batch_run(
        collect_news,
        windows,
        cfg=NewsCollectionCfg(retain_raw_html=False),
        concurrency=3,
        on_error="skip",
        on_progress=lambda done, total: print(f"{done}/{total}"),
    )
    print(f"ok={result.success_count} failed={result.failure_count}")


asyncio.run(main())
```

#### Resolve free-form intent with `magic`

```python
import asyncio

from quantmind.flows import collect_news
from quantmind.magic import resolve_magic_input


async def main() -> None:
    inp, cfg = await resolve_magic_input(
        "Collect the last day of PR Newswire company news.",
        target_flow=collect_news,
    )
    batch = await collect_news(inp, cfg=cfg)
    print(f"documents={batch.success_count} complete={batch.complete}")


asyncio.run(main())
```

More examples live under [`examples/`](examples/); design contracts live under [`contexts/design/`](contexts/design/).

#### Run the API-key-free Codex Paper Library

Install the library, UI, and fixed local embedding dependencies. The one-time
cache command uses public network access but requires no Hugging Face API key;
normal operation does not download models.

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[full,ui]"

export QUANTMIND_RUNTIME_ROOT="/absolute/path/to/quantmind-codex-data"
mkdir -p "$QUANTMIND_RUNTIME_ROOT/models" "$QUANTMIND_RUNTIME_ROOT/intake"
python scripts/cache_local_embedding_model.py \
  --cache-dir "$QUANTMIND_RUNTIME_ROOT/models"

export QUANTMIND_LIBRARY_DB="$QUANTMIND_RUNTIME_ROOT/paper-library.sqlite3"
export QUANTMIND_UI_DB="$QUANTMIND_RUNTIME_ROOT/paper-library-ui.sqlite3"
export QUANTMIND_MODEL_CACHE="$QUANTMIND_RUNTIME_ROOT/models"
export QUANTMIND_INTAKE_ROOT="$QUANTMIND_RUNTIME_ROOT/intake"

streamlit run apps/paper_library/app.py \
  --server.address 127.0.0.1
```

Set `QUANTMIND_RUNTIME_ROOT` to an absolute directory outside the Git checkout
so paper data, model weights, and personal annotations remain local runtime
assets rather than repository files.

Open [`http://127.0.0.1:8501`](http://127.0.0.1:8501), then use the explicit
operator loop:

1. In **Intake**, upload a text PDF or enter a public HTTPS PDF URL and select
   **Prepare**.
2. Ask Codex in this checkout to read the displayed `source.pdf`,
   `manifest.json`, and cited-draft policy, then write only `draft.json` in the
   prepared work directory.
3. Return to the UI, reload and validate the draft, inspect the preview, and
   explicitly confirm canonical registration.
4. In **Paper Detail**, prepare the registered source for translation, ask
   Codex to write the page-complete `translation_draft.json`, validate it, and
   register it as a separate immutable artifact.
5. Add explanatory images and Japanese descriptions as visual annotations,
   manage review/reading state, and use **Search** for Japanese or English
   semantic queries.

For command-line operation and exact JSON contracts, follow the
[interactive registration procedure](contexts/usage/codex-paper-registration.md).
The [database construction guide](docs/paper-library-setup.md) is the complete
new-install, full-pair migration, verification, and backup runbook. Its
`scripts/migrate_paper_library.py` command verifies every table row and BLOB,
then reopens papers, translations, visual annotations, and semantic search
through the public application API. The
[local library guide](docs/library.md#api-key-free-interactive-paper-registration)
and [Paper Library UI guide](docs/paper-library-ui.md) document the lower-level
persistence contract, security boundaries, and known limitations.

### 🔬 Evaluation (In Design)

> [!NOTE]
> Evaluation is in the **design phase** — no results are claimed yet. Our framing follows Anthropic's [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

**quantmind-bench** measures the harness bet directly. Following the SWE-bench model, it runs **paired trials on the same model and the same task set**: once against a bare checkout, once against the QuantMind repo mounted with its contracts, contexts, skills, and hooks. The reported deltas are **cost-to-green, pass@1, pass^k across seeds, and wall-clock** — how much a good harness moves a fixed model. Run instrumentation (tokens / cost / duration / a verify oracle) already ships; the protocol that consumes it is being designed, and no numbers are published.

A separate **llmquant-data-bench** will score knowledge quality (correctness, citation precision/recall, point-in-time correctness); it is likewise in design.

### 🏭 In Production: LLMQuant Data

LLMQuant Data is QuantMind in production. The hosted data platform runs extraction pipelines powered by QuantMind: QuantMind is the open engine, LLMQuant Data is the operated product on top of it. The dependency direction is one-way — `llmquant-data` imports `quantmind`, never the reverse.

<p align="center"><img src="assets/llmquant-data-cards.png" width="860"></p>


### 🗺️ Roadmap

Directions we are actively pushing on (not yet shipped):

- **More agent-native** — a `quantmind-best-practice` skill alongside the shipped `quantmind-dev`, and an agent-first contributing path.
- **Broader coverage** — a SEC / filings collection flow and a prediction-market knowledge type, beyond today's papers and news.
- **Evaluation** — land the `quantmind-bench` protocol and publish its first paired runs.

Development is moving fast. If you need a source, a knowledge type, or a flow we do not have yet, [open an issue](https://github.com/tikeda123/quant-mind-codex/issues) — we welcome the request.


### 🤝 Contributing

Prefer manual steps? See [`.claude/skills/quantmind-dev/references/setup.md`](.claude/skills/quantmind-dev/references/setup.md).

The fastest path is to let a coding agent drive. Inside the checkout, tell Claude Code:

```text
/quantmind-dev set me up as a contributor
/quantmind-dev file an issue: <what you need>
/quantmind-dev I want to contribute <your change>
```

Codex users say the same thing in words — the skill is mirrored under `.agents/skills/quantmind-dev/`, so both agents follow one workflow: contributor setup, filing an issue, and developing a change with tests, verification, commit, and PR.

> [!IMPORTANT]
> **For Contributors**: [CONTRIBUTING.md](CONTRIBUTING.md) covers the same setup for humans — environment, pre-commit hooks, coding standards, and testing. `scripts/verify.sh` is the single deterministic check; CI runs the exact same script.

We welcome contributions of all forms, from bug reports to feature development. Open an [issue](https://github.com/tikeda123/quant-mind-codex/issues) to discuss significant changes before you start, and make sure `bash scripts/verify.sh` is green before you open a PR.

### License

QuantMind is released under the MIT License—see `LICENSE` for details.

### ❤️ Acknowledgements

- **arXiv** for providing open access to a world of research.
- The **open-source community** for the tools and libraries that make this project possible.
</content>
</invoke>
