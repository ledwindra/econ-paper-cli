# Econ Paper CLI

A free, open-source, local-first conversational literature search tool for economists.

The goal is to let users ask research questions in natural language and receive synthesized answers backed by inspectable evidence. After one successful `econpapers setup` and `econpapers analyze`, running bare `econpapers` opens an interactive cited-chat shell over the durable local library:

```text
$ econpapers
=== econpapers interactive shell ===
Database Path: ...
Paper Count: 12
Passage Count: 34
Evidence scope: stored Abstract and Introduction passages only.
Commands: /help, /status, /show, /reset, /exit, /quit
econpapers> Has anyone studied the effect of direct regional elections on infrastructure investment?
Question: Has anyone studied the effect of direct regional elections on infrastructure investment?
Outcome: answered
Answer: ...

--- Citations ---
[e1]
  Paper Title: ...
  ...

econpapers> /show e1
[e1] ...
  Paper ID: ...
  Source Path: ...
  Section Heading: ...
  Page Range: ...
  Passage ID: ...
  Retrieval Rank: ...
  Retrieval Score: ...

  <the full stored passage text>

econpapers> /exit
```

Each question is answered independently against the stored Abstract/Introduction corpus. The shell does resolve **follow-up questions**: a question that refers back to an earlier turn ("what about its effect on housing?") is rewritten into a standalone question first, and the rewrite is always printed as `Interpreted as:` so you can see — and correct — how it was read. Detection is conservative, so a self-contained question is never rewritten. `/reset` forgets earlier turns; only answered turns become context.

`/show ID` prints the full stored passage text behind one citation from the most recent answered turn (safely normalized, not necessarily byte-identical — see "Evidence inspection" below) — so a claim can be checked against the source, not just the citation metadata. Bare `/show` lists the citation IDs currently available. Evidence is scoped to the latest turn only: a turn that does not answer (`no_matches`, `abstained`, `withheld`, or a failure) clears it, and `/reset` clears it along with conversation history. One-shot `econpapers chat QUESTION --show-evidence` prints the same evidence inline under each citation, for scripting or a single lookup without opening the shell.

## Product principles

- **Free by default:** no API keys, subscriptions, or per-token charges.
- **Local-first:** retrieval and generation run on the user's machine.
- **Cross-platform:** Windows, macOS, and Linux are first-class targets.
- **CPU-capable:** a GPU may improve performance but must not be required.
- **Evidence-driven:** substantive claims must be traceable to retrieved sources.
- **Open source:** the codebase should remain auditable and extensible.
- **Legally conservative:** copyrighted paper full text must not be redistributed without permission.

## MVP

The MVP should include:

- a conversational command-line interface;
- a local language model;
- a bundled economics-paper metadata and retrieval index;
- literature synthesis;
- follow-up questions;
- evidence inspection;
- local setup and status commands.

The MVP should not require:

- OpenAI;
- Anthropic;
- any paid cloud model;
- API keys;
- Docker;
- a GPU;
- internet access after required artifacts are installed.

## CLI scaffold

Install the project from the repository using Python 3.10 or newer:

```bash
python -m pip install -e .
```

The package currently exposes these commands:

```bash
econpapers                                                                    # interactive shell
econpapers setup [--model MODEL_ID] [--llama-cpp-path EXECUTABLE_PATH] [--model-path MODEL_PATH --model-id MODEL_ID --model-bytes BYTES --model-checksum SHA256] [--offline] [--threads N] [--timeout SECONDS] [--db-path DB_PATH]
econpapers status
econpapers chat QUESTION [--show-evidence]
econpapers update
econpapers analyze TARGET_PATH [--max-passage-characters 1200]
```

`econpapers update` remains a deterministic placeholder.

Bare `econpapers` (no command) opens the interactive shell described above;
`econpapers --help` still prints normal CLI help and exits.

### Choosing a model

`econpapers setup` provisions a model for you: with no model flags at all it
downloads, checksum-verifies, and installs a pinned GGUF, exactly as it already
does for the `llama.cpp` runtime. A model already installed and matching its
pinned checksum is reused, so re-running `setup` costs seconds, not another
download.

Two sizes are pinned. Both were measured against this tool's prompt on a real
248-paper library:

| Model | Size | Behavior |
| --- | --- | --- |
| Qwen2.5 **1.5B** Instruct Q4_K_M | ~1.0 GB | Runs anywhere. Answers are thin and sometimes repetitive, and it will occasionally reach past the best-matching paper for a weaker one. |
| Qwen2.5 **7B** Instruct Q4_K_M | ~4.4 GB | Noticeably better answers: picks the right paper, states specific findings, and produced no withheld claims on the same questions. Wants roughly 8 GB of free RAM. |

The 1.5B is the default deliberately: it is the smaller download and needs no
special hardware, which is what "CPU-capable, no GPU required" has to mean for
a first run. Move to the 7B when you want answers you would actually quote and
have the disk and memory to spare. Nothing else changes: same command, same
library, same citation and grounding checks.

```bash
econpapers setup                                       # default 1.5B
econpapers setup --model qwen2.5-7b-instruct-q4-k-m    # opt in to the 7B
```

Run `econpapers status` afterwards to confirm which model is active.

To use a GGUF you supplied yourself, pass all four model-identity flags
together — that bypasses provisioning entirely and never downloads anything:

```bash
econpapers setup \
  --model-path /path/to/your-model.gguf \
  --model-id your-model \
  --model-bytes "$(stat -f%z /path/to/your-model.gguf)" \
  --model-checksum "$(shasum -a 256 /path/to/your-model.gguf | cut -d' ' -f1)"
```

Supplying only some of the four is a typed error rather than a partial
configuration. You can also try a model for a single question without changing
your durable configuration by passing the same five flags to `chat` or
`analyze`. `--offline` refuses every download, for the model as well as the
runtime.

Whichever model you pick, an answer that attributes one paper's findings to
another is withheld rather than shown — see
[`docs/generation-contract.md`](docs/generation-contract.md). A smaller model
therefore fails by saying less, not by saying something false.

`econpapers setup` validates a proposed local `llama.cpp` runtime and GGUF
model (path, expected size, expected SHA-256 checksum, and optional thread
count, timeout, and database-path defaults), verifies local readiness, and —
only on success — durably and atomically persists that configuration to a
canonical per-user configuration file. Runtime, model, and database paths are
canonicalized to absolute paths before they become durable, so a relative
path validated in one working directory resolves the same way from any later
directory. On validation or readiness failure, the prior durable configuration
is left unreplaced — though when managed runtime provisioning was involved, a
verified managed runtime install may still remain on disk for reuse by a later
`setup` attempt, since that install is validated independently of whether the
overall command ultimately succeeds.

`--llama-cpp-path` is optional (issue #58): a fresh user can run `econpapers
setup --model-path ... --model-id ... --model-bytes ... --model-checksum
...` with no `--llama-cpp-path` at all, and setup will reuse an
already-verified managed `llama.cpp` install or download, checksum-verify,
safely extract, and atomically install the one pinned release for the
current platform/architecture into an application-managed directory — no
manual `llama.cpp` build, `PATH` edit, or executable discovery required.
Supplying `--llama-cpp-path` always bypasses managed provisioning entirely
(no download is ever triggered) and takes precedence, exactly as before.
`--offline` refuses any download, failing with a typed error unless an
explicit path is given or a verified managed runtime is already installed.
See [`docs/managed-runtime-provisioning.md`](docs/managed-runtime-provisioning.md)
for the manifest/receipt schema, the pinned release's license and
attribution, and recovery instructions. Model acquisition remains manual
and out of scope for this issue.

`econpapers status` is a read-only report of whether durable configuration
exists and is valid, independent runtime-executable and model-artifact
readiness (runtime is further classified as managed/external/unknown origin,
and verified/missing/corrupt-or-mismatched/unsupported-platform/not-checked
state — a missing or corrupt model is never conflated with a corrupt managed
runtime, or vice versa), the resolved database path, schema version, and
stored paper/passage counts. It never creates or modifies the configuration
file or database, and never downloads anything.

`econpapers analyze` and `econpapers chat` accept the same five runtime/model
flags (`--llama-cpp-path`, `--model-path`, `--model-id`, `--model-bytes`,
`--model-checksum`) as optional overrides for that invocation only. Omitting
all five falls back to the durable configuration written by `econpapers
setup`, resolved once per invocation and never mutated; supplying some but
not all five is a typed configuration error, since a partial override can
never reconstruct one coherent, previously verified identity. This
resolution happens from any working directory. `econpapers analyze` is an
offline command for one local PDF or a directory of PDFs. It recursively
processes unique PDF content in deterministic path order, persists structured
research-question evidence and provenance to SQLite, and resumes exact prior
analyses when the checksum and canonical settings match. The command
downloads nothing and never modifies source PDFs. Eligible analyses also
persist deterministic Abstract/Introduction Markdown, passages, and exact
source-fragment provenance. Exact analysis-plus-library reuse and
library-only backfill are decided before the local model adapter is
initialized, so those paths — and the `chat` `EMPTY_LIBRARY`/`NO_MATCHES`
outcomes — do not need runtime/model configuration or accessible model
artifacts.

### Interactive shell (Issue 56 implemented)

Bare `econpapers` opens a plain-text interactive shell instead of printing
help. It resolves configuration and the database path the same way as
`analyze`/`chat` (Issue #54 boundaries), opens the configured SQLite library
read-only exactly once, and builds one session snapshot (strict early-section
records, one validated `Corpus`, one in-memory `BM25Retriever`). The prompt
is `econpapers> `, with standard line-editing, command history (Up/Down
arrows), and word navigation shortcuts (Option/Alt + Left/Right arrows)
enabled in interactive terminals.

Each non-empty, non-command line is one independent cited question — exactly
as `econpapers chat` — normalized, retrieved, and (only if evidence exists)
generated and citation-validated the same way. The local generator is
constructed lazily on the first matched question and reused for later matched
questions in the same process; empty-library and no-match questions never
construct it, and a typed generator failure is rendered for that question
without corrupting the session, so the next question can retry.

Built-in commands: `/help` (session help), `/status` (database path,
paper/passage counts, and generator readiness, read-only), `/show` (list the
citation IDs from the most recent answered turn, or `/show ID` to print that
citation's full stored passage text and provenance — see "Evidence
inspection" below), and `/exit`/`/quit` (terminate successfully). A blank
line just redisplays the prompt.
EOF exits successfully; `Ctrl-C` while waiting for input exits immediately
with code 130 and no traceback. The session is strictly read-only: no
database writes or migrations, no PDF reopening or reanalysis, no
configuration mutation, and no persistence of questions, answers, or
citations. The library is a fixed snapshot for the life of the process —
papers analyzed after the shell opens become visible only after restarting
`econpapers`. Conversation context is deliberately narrow: the shell keeps
the last two *answered* turns in memory only, and uses them solely to rewrite a
follow-up question into a standalone one before retrieval. Nothing is persisted,
an abstained or withheld turn never becomes context, and the rewrite is always
shown as `Interpreted as:`. One-shot `econpapers chat` has no context at all and
answers exactly what it is given.

### Evidence inspection

A citation identifies a paper and passage, but not what the passage actually
says — `/show` in the shell and `--show-evidence` on one-shot `chat` render
the full stored passage text so a claim can be checked against its source
directly, without opening the database. Rendering is safe rather than
byte-identical: CRLF/CR line endings are normalized to LF, terminal control
characters are replaced with a placeholder glyph, and long lines are wrapped
for readability — nothing is truncated or summarized.

`/show` reads only the citations already resolved for the *most recent*
answered turn — it never re-reads storage, so a concurrent `analyze` cannot
change what it shows mid-session. A turn that does not answer
(`no_matches`, `abstained`, `withheld`, or a failure) clears that evidence
rather than leaving the previous turn's passages visible for a question they
no longer correspond to; `/reset` clears it too, together with conversation
history. `econpapers chat --show-evidence` has no such state: it always
prints the evidence for the one question just asked.

```bash
econpapers chat "What is the effect of transit expansion on wages?" --show-evidence
```

Both surfaces call the same renderer, so the output is identical either way.

### Intended future workflow

```bash
econpapers setup --llama-cpp-path EXECUTABLE_PATH --model-path MODEL_PATH --model-id MODEL_ID --model-bytes BYTES --model-checksum SHA256
econpapers ingest /path/to/papers
econpapers
```

The `ingest` example illustrates the intended ordinary-user workflow. Its
command name, syntax, and flags are not yet approved or implemented. The user
should eventually be able to select a legally obtained PDF or directory and
have the application derive checksums, metadata, Markdown, passages,
provenance, database records, and retrieval state locally. Manual conversion,
manifest creation, segmentation, identifier assignment, and database insertion
should not be necessary.

## Local inference adapter

The repository implements a configurable `llama-completion` subprocess adapter
for the existing backend-independent `Generator` protocol. It uses explicit
local paths, offline mode, a versioned evidence-only prompt, a fingerprinted
GBNF constraint derived from the authoritative JSON schema, authoritative
citation resolution, and final response validation. This adapter itself does
not download anything; the only network access anywhere in the application is
the explicit managed-runtime provisioning step inside `econpapers setup`
described above (issue #58) — `analyze`, `chat`, bare `econpapers`, and
`status` remain unconditionally network-free. The `analyze` and `chat`
commands construct this adapter
only from local runtime and model paths — either explicit per-invocation CLI
overrides or durable configuration written by `econpapers setup` — and only
when a local generator is actually required.

`llama.cpp` b10199 is pinned for adapter compatibility testing and the initial
Issue 13 comparison, not as a permanent product runtime. Three model artifacts
are approved for evaluation only:

- SmolLM2 1.7B Instruct Q4_K_M, conditional on completing its immutable
  source-revision and conversion-provenance record;
- official Qwen3 0.6B Q8_0; and
- official Qwen2.5 1.5B Instruct Q4_K_M.

No first-party Qwen3 0.6B Q4 GGUF was identified, correcting the earlier
provisional wording. Issue 13 found that neither eligible Qwen candidate passed
its first mechanical evaluation run, so no default model has been approved. See
[`docs/local-generation-evaluation.md`](docs/local-generation-evaluation.md)
for exact revisions, checksums, licenses, adapter behavior, and the Issue 13
deferral evidence.

## Corpus policy

NBER working papers and many other economics papers are copyrighted. Converting them to Markdown does not remove copyright restrictions.

The project may distribute:

- bibliographic metadata;
- paper identifiers;
- source URLs;
- abstracts where redistribution is permitted;
- topic labels;
- derived indexes that do not reconstruct copyrighted full text;
- code that helps users build a local corpus from authorized sources.

The project must not bundle converted full-text papers unless redistribution permission is established.

## Planned local paper library

The approved future library uses four local layers:

| Layer | Purpose |
| --- | --- |
| Original PDFs | Authoritative user-provided source documents |
| Generated Markdown | Inspectable derived representation |
| SQLite | Structured catalog, retrieval-ready passages, provenance, checksums, ingestion state, and other application state |
| Retrieval index | Rebuildable search accelerator |

Ingestion must never modify or delete a user's source PDFs. Whether the
application later manages private PDF copies or registers files in place
remains undecided. Recovery of derived records therefore depends on the
authoritative PDFs remaining accessible, unless a later managed-copy policy
preserves them.

Generated Markdown, source-derived SQLite records, passage text, provenance,
and retrieval indexes should be rebuildable from accessible PDFs plus versioned
conversion logic and configuration. Future annotations, metadata corrections,
preferences, chat history, or similar unique user state may need separate
backup or export behavior and is not assumed to be reconstructible.

The library location will be configurable and usable outside this repository.
The ignored repository-root `/papers/` directory is only a convenience
location, not a hard-coded application path. PDFs, converted copyrighted text,
Markdown, databases, and indexes remain private user data and must not be
committed or redistributed.

Ordinary ingestion will run locally without network access. Future metadata
enrichment would require separate approval and explicit opt-in. The MVP uses
Python's standard-library `sqlite3` behind a replaceable storage boundary
(`StorageBackend` protocol and `SQLiteStorage` adapter); it does not require
PostgreSQL, a database server, Docker, a cloud database, or a vector database.

### Local storage foundation (Issue 24 implemented)

The storage layer is implemented as a database-independent protocol (`StorageBackend`) with a standard-library `sqlite3` adapter (`SQLiteStorage`):

- **Data persisted:** paper metadata, passages, source provenance, conversion settings, warnings, and completion metadata.
- **Transactions & rollback:** all writes for a paper record execute within a single atomic SQLite transaction (`BEGIN IMMEDIATE`), with full rollback on failure.
- **Deduplication & idempotency:** deterministic replacement and checksum-aware duplicate detection (`content_checksum`), raising `ChecksumConflictError` on conflicting paper identifiers.
- **Versioning & migrations:** schema version tracking with forward migrations (`schema_migrations` table) and transaction rollback on migration failure.
- **Early-section records:** schema version 4 stores generated Abstract/Introduction Markdown, stable passages, conversion identity, and ordered page-local provenance fragments without writing external Markdown files. `econpapers analyze` now populates this representation, reuses exact compatible records, and backfills legacy analysis-only records without rerunning research-question generation; see [`docs/early-section-library-storage.md`](docs/early-section-library-storage.md).
- **Cross-platform paths:** automatic data directory and database path resolution for Windows (`%LOCALAPPDATA%`), macOS (`~/Library/Application Support`), and Linux (`${XDG_DATA_HOME:-~/.local/share}`), with `ECONPAPERS_LIBRARY_DIR` environment variable override.

### Local runtime/model configuration (Issue 54 implemented)

A separate, database-independent configuration boundary makes `analyze` and
`chat` reusable across invocations without repeating runtime/model arguments:

- **Domain contract:** immutable, versioned `LocalRuntimeModelConfig` (schema
  version 1) — runtime executable path, model path, model id, expected model
  size and SHA-256 checksum, and optional thread count, timeout, and
  database-path defaults. Strictly validated; unknown or missing fields and
  invalid values are rejected with actionable errors.
- **Replaceable storage:** `ConfigBackend` protocol with a standard-library
  JSON adapter (`JSONConfigStorage`) that writes atomically (temporary file,
  flush, `os.replace`) with private file permissions where supported. A
  failed write never destroys the previously durable configuration.
- **Cross-platform, independent location:** automatic configuration directory
  resolution for Windows (`%LOCALAPPDATA%\econpapers\config`), macOS
  (`~/Library/Application Support/econpapers/config`), and Linux
  (`${XDG_CONFIG_HOME:-~/.config}/econpapers`), with an
  `ECONPAPERS_CONFIG_DIR` environment variable override independent of
  `ECONPAPERS_LIBRARY_DIR`.
- **Resolution precedence:** explicit CLI value, then durable configuration,
  then a documented default; the five runtime/model identity fields resolve
  as one unit so a partial CLI override can never silently combine with an
  unrelated stored value. A partial override is rejected immediately, before
  `analyze`/`chat` even determine whether a generator will be needed.
- **Working-directory independence:** `econpapers setup` canonicalizes the
  runtime, model, and configured database paths to absolute paths before
  persisting them, so a relative path validated in one directory resolves
  identically from any later invocation directory.
- **Model-independence preserved:** durable configuration is loaded lazily
  and at most once per invocation, and only when actually needed — for a
  database-path fallback, or inside generator construction. Exact
  analysis-plus-library reuse, generator-free library backfill, and the
  `chat` `EMPTY_LIBRARY`/`NO_MATCHES` outcomes need neither configuration nor
  accessible runtime/model artifacts, even when an explicit `--db-path` is
  combined with a fully-specified runtime/model override.

## Repository direction

The repository is growing toward this structure as later scoped issues add the
remaining layers:

```text
econ-paper-cli/
├── README.md
├── AGENTS.md
├── LICENSE
├── pyproject.toml
├── docs/
│   ├── product-requirements.md
│   ├── architecture.md
│   └── roadmap.md
├── src/
│   └── econ_paper_cli/
├── tests/
├── skills/
└── .github/
    └── workflows/
```

## Development workflow

Development is issue-driven:

1. Define one narrowly scoped GitHub issue.
2. Assign explicit acceptance criteria.
3. Implement one issue per pull request.
4. Add or update tests.
5. Update documentation when behavior changes.
6. Run linting and tests before merge.

Install the package and development tools in editable mode from an activated
Python 3.10 or newer virtual environment:

```bash
python -m pip install -e ".[dev]"
```

Run the required checks before opening a pull request:

```bash
ruff check .
ruff format --check .
pytest
```

## Architecture principles

- Keep the CLI thin.
- Put orchestration in application services.
- Represent papers and evidence as typed domain objects.
- Keep retrieval and generation behind replaceable interfaces.
- Isolate filesystem, network, model, and index operations in adapters.
- Avoid unnecessary frameworks.
- Prefer deterministic, offline tests.
- Do not bind the core domain to a specific vector database or model runtime.

## Current status

The CLI works end-to-end over a local library: `econpapers setup` provisions
a local `llama.cpp` runtime and a default GGUF model (or accepts an
explicit, already-installed one); `econpapers analyze` discovers PDFs,
extracts and quality-checks them, detects Abstract/Introduction sections,
converts them to inspectable Markdown/passages, and persists everything to
SQLite transactionally, with exact-reuse and legacy-backfill paths that
never require accessible model artifacts; `econpapers chat QUESTION` and
bare `econpapers` (an interactive multi-question shell with follow-up
resolution) both run retrieval (BM25) and local generation
(`llama-completion`) against that library, validate citations, and detect
and withhold claims that misattribute wording from a paper they do not
cite. `/show` and `--show-evidence` (see "Evidence inspection" above) let a
user check a claim against the exact stored passage behind it. `econpapers
status` reports durable configuration, runtime/model readiness, and library
state read-only.

Underlying building blocks: an installable package with cross-platform CI; a
pure domain contract for validating artifact metadata; filesystem adapters
for loading local manifests and verifying file checksums; pure domain
contracts for papers, passages, retrieval evidence, citations, and corpora; a
synthetic CC0 fixture corpus with a local corpus loader adapter; a
backend-independent retrieval protocol (`Retriever`, `RetrievalRequest`,
`validate_retrieval_results`); a pure-Python BM25 baseline adapter
(`BM25Retriever`) selected as the initial replaceable retrieval backend; a
database-independent local storage protocol (`StorageBackend`) with a
standard-library `sqlite3` adapter (`SQLiteStorage`) supporting schema
versioning, forward migrations, atomic transactions (`BEGIN IMMEDIATE`),
case-insensitive checksum uniqueness, unique passage ordinals, full `Corpus`
reconstruction (`load_corpus`), and cross-platform path resolution; the
replaceable `PDFExtractor` boundary and fully local `PyPDFExtractor`; and a
backend-independent generation protocol with structured requests, responses,
per-claim citations, explicit abstention validation, and cross-paper
grounding checks.

Not yet implemented: `econpapers update` (currently a deterministic
placeholder — see [`MVP-PLAN.md`](MVP-PLAN.md)), OCR, conversion beyond
Abstract/Introduction, a persisted or bundled retrieval index, and
validation of the ingestion pipeline against the six real journal layouts
tracked by issue #59. See [`MVP-PLAN.md`](MVP-PLAN.md) for the current
milestone ladder toward MVP.


## Contributing

Before making changes, read [`AGENTS.md`](AGENTS.md).

Pull requests should be small, issue-linked, tested, and limited to one coherent change.

## License

The source code is released under the [MIT License](LICENSE). Models, corpora,
indexes, and datasets retain their own licenses and terms.
