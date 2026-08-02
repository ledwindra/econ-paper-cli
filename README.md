# Econ Paper CLI

A free, open-source, local-first conversational literature search tool for economists.

The goal is to let users ask research questions in natural language and receive synthesized answers backed by inspectable evidence. The following session is an illustration of the intended product, not current behavior:

```text
$ econpapers chat

> Has anyone studied the effect of direct regional elections on infrastructure investment?

Answer:
...

Evidence:
[1] Author (Year), Paper Title
    Relevant passage: ...
```

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
econpapers setup
econpapers chat
econpapers status
econpapers update
econpapers analyze TARGET_PATH --llama-cpp-path EXECUTABLE_PATH --model-path MODEL_PATH --model-id MODEL_ID --model-bytes BYTES --model-checksum SHA256 [--max-passage-characters 1200]
```

The first four commands are deterministic placeholders. `econpapers analyze`
is an offline command for one local PDF or a directory of PDFs. It recursively
processes unique PDF content in deterministic path order, persists structured
research-question evidence and provenance to SQLite, and resumes exact prior
analyses when the checksum and canonical settings match. The runtime and GGUF
model must already exist at the explicit paths; the command downloads nothing
and never modifies source PDFs. Eligible analyses also persist deterministic
Abstract/Introduction Markdown, passages, and exact source-fragment provenance.
Exact analysis-plus-library reuse and library-only backfill are decided before
the local model adapter is initialized, so those paths do not need accessible
model artifacts or invoke generation.

### Intended future workflow

```bash
econpapers setup
econpapers ingest /path/to/papers
econpapers chat
```

The `ingest` example illustrates the intended ordinary-user workflow. Its
command name, syntax, and flags are not yet approved or implemented. The user
should eventually be able to select a legally obtained PDF or directory and
have the application derive checksums, metadata, Markdown, passages,
provenance, database records, and retrieval state locally. Manual conversion,
manifest creation, segmentation, identifier assignment, and database insertion
should not be necessary.

Inside the chat:

```text
> What is the literature on direct regional elections and public investment?

> :evidence 1

> How credible is the causal identification?

> :quit
```

## Local inference adapter

The repository implements a configurable `llama-completion` subprocess adapter
for the existing backend-independent `Generator` protocol. It uses explicit
local paths, offline mode, a versioned evidence-only prompt, a fingerprinted
GBNF constraint derived from the authoritative JSON schema, authoritative
citation resolution, and final response validation. It does not download a
runtime or model. The `analyze` command constructs this adapter only from the
user's explicit local runtime and model paths.

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

This project has an installable package, placeholder CLI commands, initial
requirements and architecture documents, cross-platform CI configuration, a
pure domain contract for validating artifact metadata, filesystem adapters
for loading local manifests and verifying file checksums, pure domain
contracts for papers, passages, retrieval evidence, citations, and corpora,
a synthetic CC0 fixture corpus with a local corpus loader adapter, a
backend-independent retrieval protocol (`Retriever`, `RetrievalRequest`,
`validate_retrieval_results`), a pure-Python BM25 baseline adapter
(`BM25Retriever`) selected as the initial replaceable retrieval backend, and a
database-independent local storage protocol (`StorageBackend`) with a standard-library
`sqlite3` adapter (`SQLiteStorage`) supporting schema versioning, forward migrations,
atomic transactions (`BEGIN IMMEDIATE`), case-insensitive checksum uniqueness,
unique passage ordinals, full `Corpus` reconstruction (`load_corpus`), and cross-platform
path resolution. Deterministic ingestion preflight discovers PDFs, computes checksums,
deduplicates batch content, and classifies stored checksums without writing records.
The replaceable `PDFExtractor` boundary and fully local `PyPDFExtractor` preserve
ordered page boundaries, optional raw document metadata, parser provenance, and
actionable extraction failures without modifying source PDFs. It also defines a
backend-independent generation protocol with
structured requests, responses, citations, and explicit abstention validation; a
concrete, configurable local `llama-completion` adapter; and a fingerprinted CC0
synthetic generation benchmark with opt-in evaluation tooling. Issue 13 explicitly
deferred the default after both eligible candidates failed the first mechanical
run, and generation is not connected to retrieval or the CLI.
End-to-end ingestion orchestration, OCR, quality assessment, passage segmentation,
Markdown generation, database writes, index refresh, and conversational execution
remain unimplemented.

The immediate priorities are:

1. implement automatic local PDF ingestion; and
2. connect the approved library, retrieval, and generation components through
   end-to-end application services.


## Contributing

Before making changes, read [`AGENTS.md`](AGENTS.md).

Pull requests should be small, issue-linked, tested, and limited to one coherent change.

## License

The source code is released under the [MIT License](LICENSE). Models, corpora,
indexes, and datasets retain their own licenses and terms.
