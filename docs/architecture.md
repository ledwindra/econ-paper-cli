# Architecture

## Design goals

The system uses a layered, adapter-oriented design so local inference,
retrieval, storage, and corpus implementations can change without rewriting
the product workflow.

The intended dependency direction is:

```text
CLI adapters -> application services -> domain types and protocols
                                      <- infrastructure adapters
```

The domain and application layers must not depend directly on a database,
vector store, model runtime, embedding model, or network service.

## Layers

- **Domain:** typed papers, passages, evidence, scores, citations, and errors.
- **Application services:** orchestration for retrieval, generation, evidence
  validation, abstention, ingestion, and artifact management.
- **Protocols:** replaceable interfaces for retrieval, inference, storage, and
  artifact sources.
- **Adapters:** CLI, filesystem, network, model runtime, index, and corpus
  implementations. Effects remain visible and injectable.

Retrieval, generation, corpus ingestion, and artifact management remain
separate concerns. Evidence identity and passage boundaries remain structured
throughout the pipeline, and generated citation identifiers must be validated
against retrieved evidence before display.

## Approved hybrid local-library architecture

Issue 11 approves the following future architecture. It is documentation and
design only. No storage protocol, SQLite schema, ingestion service, PDF
processing, or production index is currently implemented.

```text
User-selected PDF or directory
              |
              v
      Ingestion application service
      - discovery and checksums
      - duplicate and re-ingestion decisions
      - extraction and supported OCR
      - metadata and quality assessment
      - Markdown conversion
      - stable passage segmentation
              |
              +--> generated Markdown
              |
              +--> storage protocol --> SQLite adapter
              |
              +--> retrieval protocol/index adapter
```

The application service owns orchestration. PDF readers, OCR tools,
filesystems, SQLite, and retrieval indexes remain infrastructure adapters.
Domain and application modules must depend on a replaceable storage protocol,
not on `sqlite3` or a concrete schema. The SQLite adapter should use Python's
standard-library `sqlite3` unless a later implementation issue demonstrates a
need for another dependency.

### Storage-layer roles

| Layer | Role | Authority and recovery |
| --- | --- | --- |
| Original PDFs | User-provided source documents | Authoritative source input. Ingestion never modifies or deletes them. Recovery depends on the files remaining accessible or on a later managed-copy policy preserving them. |
| Generated Markdown | Human-readable converted document | Derived and inspectable. It can be regenerated from accessible PDFs with the recorded conversion version and configuration. |
| SQLite | Catalog, retrieval-ready passage text, metadata, provenance, checksums, ingestion state, and other application state | Source-derived records are rebuildable from accessible PDFs and versioned conversion logic. Unique user state is not assumed to be reconstructible. |
| Retrieval index | Query-time search optimization | Derived accelerator. It is rebuildable from stored passages and is never the only copy of paper or passage data. |

Whether the application manages private copies of PDFs or registers source
files in place remains deferred. Registration cannot preserve an externally
stored PDF after a user moves or deletes it.

SQLite is the structured operational store for the future application, but
Markdown remains available for inspection and export. Markdown is not reparsed
on every run, and SQLite is not exposed as the only means of accessing
converted text.

### Rebuildable records and unique user state

Generated Markdown, source-derived catalog records, passage text, provenance,
and retrieval indexes must be reproducible from accessible PDFs plus versioned
conversion logic and configuration. Retrieval state should be rebuildable from
SQLite passage records without repeating PDF extraction.

Future user-created annotations, metadata corrections, preferences, chat
history, or similar unique state may require backup, export, or separate
recovery behavior. Those features are not designed by Issue 11, and the
architecture does not classify all application state as rebuildable.

### Intended ingestion flow

For ordinary use, a user selects a PDF or directory and invokes ingestion. A
future application service will:

1. discover supported PDF files;
2. calculate content checksums and identify duplicate or previously ingested
   content;
3. register the authoritative source without modifying or deleting it;
4. extract text and available metadata locally;
5. use locally supported OCR when required;
6. assess extraction quality and report actionable warnings or failures;
7. create structured, inspectable Markdown;
8. create deterministic passages with paper, page, section, file, extraction,
   checksum, and conversion provenance;
9. validate stable identities and cross-record integrity;
10. commit the database writes for the ingestion in one SQLite transaction;
    and
11. build or refresh rebuildable retrieval state and record its freshness.

The workflow requires no manual conversion, manifest creation, metadata entry,
segmentation, identifier assignment, or database insertion. It performs no
network access. Any future metadata-enrichment service must be separately
approved and explicitly enabled.

### Transactions, re-ingestion, and recovery

Content checksums, rather than paths or filenames, provide the primary
duplicate-detection input. Repeating ingestion with the same content and
conversion configuration must be deterministic and idempotent. Stable
`paper_id` and `passage_id` values must remain compatible with the existing
domain contracts when their identity inputs have not changed.

All SQLite writes belonging to one ingestion operation must use one database
transaction. The PDF filesystem, SQLite database, generated Markdown, and
retrieval index are separate resources, so Issue 11 does not promise atomic
commit across them. A later implementation issue must define staging, rollback,
cleanup, index-freshness, and restart behavior without presenting partial work
as successful.

SQLite schemas require explicit versions and forward migrations. Migration
failures must preserve existing user data or stop with an actionable error.
The exact tables, migration machinery, identifier algorithm, and concurrency
policy remain deferred.

### Privacy, licensing, and portability

PDFs, generated copyrighted text, databases, indexes, and OCR output are
private user data. They must not be committed or redistributed. The ignored
repository-root `/papers/` directory is a convenience input location only and
must not be hard-coded. The actual library location must be configurable and
suitable for use outside a source checkout on Windows, macOS, and Linux.

The MVP requires no PostgreSQL, database server, Docker service, cloud
database, or vector database. Ordinary ingestion remains local and offline.

### Deferred implementation decisions

Later storage or ingestion issues must decide:

- whether PDFs are copied into a managed library or registered in place;
- the default library location and configuration precedence;
- SQLite tables, indexes, connection policy, migration machinery, and locking;
- exact `paper_id` and `passage_id` derivation rules;
- extraction, OCR, metadata-precedence, quality, and segmentation algorithms;
- encrypted-PDF and password handling;
- conversion-version and reprocessing rules;
- filesystem staging, cleanup, and recovery around the SQLite transaction;
- persisted retrieval-index format and refresh behavior;
- Markdown storage and export layout; and
- ingestion command syntax and options.

These choices must preserve the approved local, offline, licensing, privacy,
portability, evidence, and failure-reporting requirements.

## Current scaffold

Issue 1 contains a standard-library CLI adapter and side-effect-free placeholder
application services. The CLI parses commands, delegates once, prints the
result, and returns an exit code.

Issue 2 adds the first domain object: an immutable artifact manifest with pure
mapping conversion and validation.

Issue 3 implements local filesystem adapters in `econ_paper_cli.adapters.filesystem` to load artifact manifests from JSON files and verify local file sizes and SHA-256 digests using chunked streams.

Issue 4 adds pure, immutable domain contracts for `Paper`, `Passage`, `RetrievalEvidence`, and `Citation` with strict validation rules and JSON-compatible mapping conversion.

Issue 5 introduces the pure immutable `Corpus` domain object (`econ_paper_cli.domain.corpora.Corpus`), a 100% synthetic, legally redistributable CC0-1.0 economics fixture corpus (`tests/fixtures/corpus/synthetic-economics-v1.json`), and a local corpus loader adapter (`econ_paper_cli.adapters.corpus.load_corpus_from_file`). Artifact verification and corpus parsing remain strictly separate concerns: filesystem adapters verify artifact digests, while `Corpus.from_mapping` enforces corpus-level schema and cross-record integrity.

Issue 6 defines the backend-independent retrieval boundary in `econ_paper_cli.protocols.retrieval`. It introduces the immutable `RetrievalRequest` domain object, the replaceable `Retriever` protocol, and pure result contract validation via `validate_retrieval_results` (enforcing contiguous 1-based ranks, non-increasing score order, ascending `passage_id` tie-breaking, uniform non-empty `retrieval_method` labels, and exact duplicate passage rejection). Near-duplicate passage suppression is deferred to adapter evaluation. For full specification details, see [`docs/retrieval-contract.md`](retrieval-contract.md).

Issue 7 implements the first concrete `Retriever` adapter: `econ_paper_cli.adapters.bm25.BM25Retriever`. It is a pure-Python, standard-library-only BM25 baseline constructed over an in-memory `Corpus`. It computes corpus-wide term statistics once during initialization and indexes `Passage.text` only. It employs the `bm25-v1` tokenizer (`char.isalnum()` character scan with NFKC normalization and casefolding), positive IDF, score-descending sorting with `passage_id` ascending tie-breaking, and normalized lexical duplicate suppression. Returned tuples pass `validate_retrieval_results` with `retrieval_method="bm25-v1"`. Query-time retrieval performs zero filesystem or network I/O. Persisted index artifacts, CLI integration, PDF parsing, Markdown conversion, and local model inference remain unimplemented. `BM25Retriever` is not selected as the default retrieval backend.

Issue 8 adds `econ_paper_cli.evaluation.retrieval`, a pure evaluation boundary
over the existing `Retriever` protocol. A frozen CC0 benchmark supplies 25
synthetic economics queries, binary passage-level relevance judgments, ranked
metrics at `k=1,3,5`, and conservative CI regression gates. The benchmark is
pinned to canonical retrieval-relevant corpus content with a SHA-256 fingerprint
validated before retrieval. The untuned `BM25Retriever` is measured by this
benchmark but is not selected as the permanent default or claimed to outperform
another adapter. See [`docs/retrieval-evaluation.md`](retrieval-evaluation.md).

Issue 9 selects `BM25Retriever` as the initial, replaceable retrieval backend.
The decision uses the frozen benchmark together with installation, artifact,
CPU, portability, licensing, privacy, and maintenance evidence; it does not
claim BM25 is permanently optimal. `econ_paper_cli.evaluation.resources` adds a
backend-independent, standard-library observation boundary for stable result
digests, initialization and query timings, Python heap, available process RSS,
and machine metadata. Correctness remains CI-gated while timing and memory are
non-gating observations. No second adapter, production dependency, model
artifact, persisted index, CLI wiring, or generation integration is added. See
[`docs/retrieval-selection.md`](retrieval-selection.md).

Issue 10 defines the backend-independent local-generation boundary in
`econ_paper_cli.protocols.generation`. Immutable request and response objects
carry the user's question, ranked retrieval evidence, answer text, structured
citations, generation-method identity, answer-level finding kinds, and explicit
abstention state. `validate_generation_response` verifies rank-derived citation
identity, supplied-evidence membership, canonical citation ordering, and
abstention consistency. These structural checks do not prove factual grounding
or sentence-level citation support. No model adapter, runtime dependency,
artifact, retrieval orchestration, CLI integration, or PDF ingestion is added.
See [`docs/generation-contract.md`](generation-contract.md).

Issue 11 documents the approved automatic local PDF-ingestion workflow and
hybrid paper-library architecture. Original PDFs are authoritative inputs,
Markdown is an inspectable derived artifact, SQLite is the future structured
operational store, and retrieval indexes are rebuildable accelerators. Issue 11
does not implement any of these components and does not change the domain,
retrieval, generation, BM25, artifact, corpus, or evaluation contracts created
by Issues 1 through 10. The frozen Issue 8 benchmark, fixture fingerprint,
relevance judgments, and regression gates remain unchanged.

Issue 12 implements `econ_paper_cli.adapters.llama_cpp.LlamaCppGenerator`, a
concrete adapter for the existing backend-independent generation protocol. The
adapter invokes an explicit local `llama-completion` path with `shell=False`, an
explicit verified GGUF path, offline mode, a permission-restricted temporary
prompt file, the packaged `generation-v1` prompt, and a fingerprinted GBNF
constraint deterministically derived from the authoritative JSON schema. The
model returns only answer text, citation IDs, abstention state, and answer-level
finding kinds. The adapter resolves citation IDs into authoritative `Citation`
objects, supplies its path-independent generation-method identity, constructs
`GenerationResponse`, and calls `validate_generation_response`.

Questions and evidence are not command-line arguments. Temporary prompt and
grammar files are cleaned on normal and exceptional paths. Runtime logs are
redirected away from captured output. Stdout and stderr are captured through
separately bounded pipes; exceeding either live bound terminates the process
group. The adapter removes only the pinned runtime's exact completion footer
before strict single-object JSON parsing. Normal errors omit captured content,
and timeouts, cancellation, runtime exits, artifact failures, and invalid model
output remain exceptions rather than abstentions. The adapter does not
download artifacts, use hosted inference, or connect to retrieval or the CLI.

`econ_paper_cli.evaluation.generation` adds a separate, fingerprinted CC0
synthetic benchmark and structural evaluation boundary. Semantic grounding,
causal characterization, uncertainty, disagreement, and substantive
claim-to-response-citation support require blinded human review. The frozen
Issue 8 retrieval benchmark remains unchanged. Exact runtime and candidate
artifact metadata, benchmark design, review procedure, and the Issue 13
decision gate are documented in
[`docs/local-generation-evaluation.md`](local-generation-evaluation.md).

Issue 13 found that both eligible Qwen candidates failed their first mechanical
run under the common configuration. The evaluator preserved sanitized failures
and marked the remaining scheduled runs `not_run`; no semantic scoring was
applicable. No default generation adapter configuration is approved. The
backend-independent generation boundary and replaceable adapter architecture
remain unchanged.

Issue 24 implements the local SQLite storage foundation behind a
database-independent storage protocol. `econ_paper_cli.protocols.storage.StorageBackend`
defines the replaceable interface for persistent library data, including full paper
storage records, passages, provenance, conversion settings, warnings, and completion
metadata. `econ_paper_cli.domain.storage` defines the immutable `PaperRecord`,
`SourceProvenance` (including `source_file_size` and `markdown_path`), `ConversionSettings`,
`IngestionWarning`, and `IngestionCompletion` domain objects with full JSON-compatible
mapping conversion and validation.

`econ_paper_cli.adapters.sqlite_storage.SQLiteStorage` is the first concrete
adapter implementation. It uses Python's standard-library `sqlite3` and enforces
foreign-key constraints (`PRAGMA foreign_keys = ON;`), schema versioning, and forward
migrations (`schema_migrations` table). Opening a database with a schema version newer
than supported raises `StorageIncompatibleSchemaError` without modifying the database.
All writes for a paper record execute within an explicit atomic SQLite transaction
(`BEGIN IMMEDIATE` / `COMMIT`), ensuring complete rollback on failure. Duplicate
detection and re-ingestion are deterministic and checksum-aware (`content_checksum` with
`COLLATE NOCASE`), with `ChecksumConflictError` raised if a checksum belongs to another
`paper_id`. Passages enforce unique ordinal positions per paper (`UNIQUE(paper_id, ordinal_position)`),
and the backend supports reconstructing a validated `Corpus` domain object (`load_corpus()`).
Cascading deletes (`ON DELETE CASCADE`) maintain referential integrity across paper,
passage, provenance, settings, warnings, and completion tables.

`econ_paper_cli.adapters.storage_paths` provides cross-platform user data directory
and database path resolution for Windows (`%LOCALAPPDATA%` / `%APPDATA%`), macOS
(`~/Library/Application Support`), and Linux (`${XDG_DATA_HOME:-~/.local/share}`),
with explicit `ECONPAPERS_LIBRARY_DIR` environment variable override. End-to-end PDF
ingestion, OCR, retrieval-index persistence, and cloud storage remain unimplemented.

Issue 28 implements deterministic PDF discovery and ingestion preflight through
`econ_paper_cli.services.ingestion.run_ingestion_preflight`. It accepts an explicit PDF
or directory, discovers case-insensitive `.pdf` paths in deterministic order, delegates
canonical path, size, and SHA-256 inspection to the filesystem adapter, marks duplicate
content within the batch, and classifies existing checksums through the replaceable
`StorageBackend`. It performs no extraction, conversion, writes, or network access.

Issue 30 implements the replaceable `PDFExtractor` protocol, immutable structured
extraction models, and the fully local `PyPDFExtractor` adapter. Extraction preserves
one ordered record per source page, including empty-text pages, plus optional raw PDF
document-information strings and parser provenance. The adapter normalizes only line
endings, translates filesystem, encryption, malformed-file, and parser failures into
typed protocol errors, and never modifies the source. The application-facing
`extract_pdf` service requires explicit extractor injection. OCR, bibliographic
normalization, Markdown conversion, passage creation, database writes, index updates,
CLI syntax, and network enrichment remain unimplemented.

Issue 32 implements immutable extraction-quality settings, measurements, page
observations, warning contracts, and document statuses in the domain layer. The pure
`assess_pdf_extraction_quality` service accepts only a validated `PDFExtractionResult`
and explicit versioned settings. It measures empty and sparse pages, text volume,
control, replacement, and repeated-character anomalies, and page-text imbalance. The
default thresholds and deterministic status precedence are specified in
[`docs/pdf-quality-assessment.md`](pdf-quality-assessment.md). Assessment performs no
OCR, parser retry, filesystem access, persistence, conversion, or text mutation.

Issue 44 extends the existing offline `econpapers analyze` orchestration from a
single PDF to either a PDF or directory target. Directory mode composes the
existing ingestion preflight, single-paper analysis, and SQLite persistence
services without binding core logic to concrete storage or inference
implementations. It initializes shared adapters once, separates deterministic
path discovery from per-candidate checksum inspection, reuses each successful
preflight checksum rather than inspecting a candidate twice, skips later
duplicate bytes, and resumes exact analysis identities derived from checksum
plus canonical settings. Candidate inspection, identity lookup, analysis,
persistence, and strict read-back share one per-file isolation boundary. Ordered
immutable batch outcomes therefore preserve durable typed preflight failures,
duplicate paths, and unexpected storage failures without concealing later
papers. The feature does not add concurrency, downloads, OCR, Markdown
generation, indexing, or full-document ingestion.

Issue 46 adds a pure early-section conversion boundary. Immutable versioned
settings, deterministic checksum-derived paper identity, settings and passage
fingerprints, Markdown, ordered passages, and exact page-local passage
provenance remain independent of filesystem and storage adapters. The service
grounds detected Abstract and Introduction spans against structured extraction
pages, renders canonical headings and invisible page-transition markers, and
segments each section independently with a deterministic character budget.
It does not change the existing `Passage` contract, infer source-path equality,
convert later sections, write derived artifacts, populate SQLite, or build a
retrieval index. See
[`docs/pdf-early-section-conversion.md`](pdf-early-section-conversion.md).

Issue 48 adds the immutable `EarlySectionLibraryRecord`, a pure projection
service, explicit storage-protocol operations, and SQLite schema version 4.
Successful early-section Markdown, passages, parser/conversion identity, and
ordered page-local provenance fragments now survive database restart. Fragment
source text is retained so strict read-back can validate each stored fragment
against its passage slice without claiming that full extraction pages are
stored. Atomic replacement preserves the original durable creation timestamp,
uses the injected projection timestamp as the update timestamp, and removes
stale passages and fragments in the same transaction. Stored passages remain
available through `load_corpus()` and the existing in-memory BM25 adapter.
Issue 50 connects this boundary to `econpapers analyze`. A preflight-first
application state machine decides exact reuse and legacy library backfill before
constructing the local generator. Newly produced eligible analysis and library
records share one injected timestamp and one coordinated SQLite transaction;
non-eligible terminal analyses retain their analysis-only transaction. Existing
analysis records can be paired with a missing or differently configured library
record through extraction and deterministic conversion without generation.
Conversion, projection, or storage failures roll back and surface as internal
failures. The workflow does not write Markdown files or persist a retrieval
index. See
[`docs/early-section-library-storage.md`](early-section-library-storage.md).

Issue 54 adds a durable, versioned local runtime/model configuration boundary
so `analyze` and `chat` can be run from any working directory without
repeating explicit runtime/model arguments after one successful setup. The
immutable `econ_paper_cli.domain.local_config.LocalRuntimeModelConfig`
(schema version 1) captures exactly the reusable identity: runtime executable
path, model path, model id, expected model size and SHA-256 checksum, and
optional threads, timeout, and database-path defaults. It excludes fixed
reproducibility constants that remain owned by the concrete generation
adapter. `econ_paper_cli.protocols.config.ConfigBackend` is the replaceable
storage protocol; `econ_paper_cli.adapters.config_storage.JSONConfigStorage`
is the standard-library JSON adapter, writing atomically (temporary file,
flush, `os.replace`) to a canonical per-user configuration location
independent of the SQLite data directory
(`econ_paper_cli.adapters.storage_paths.get_default_config_dir`, with an
`ECONPAPERS_CONFIG_DIR` override), using private file permissions where
supported. A failed write never destroys previously durable configuration.
Runtime, model, and configured database paths are canonicalized to absolute
paths (`Path.resolve()`) at the point they become durable, in
`run_setup_command`, so a relative path validated in one working directory
resolves identically from any later invocation directory; the domain
constructor itself performs no resolution, preserving pure validation there.
`ConfigBackend.exists()` reports whether a configuration file is present,
independent of `load()`'s parse/validation outcome, so a malformed file is
distinguishable from a missing one.

`econpapers setup` (`econ_paper_cli.services.setup_command`) validates a
proposed configuration and verifies local runtime/model readiness through the
existing `LlamaCppGenerator.check_readiness()` boundary before persisting;
nothing is written on validation or readiness failure. It accepts an optional
`--db-path` default alongside the runtime/model arguments. `econpapers status`
(`econ_paper_cli.services.status_command`) is a strictly read-only report of
configuration presence, validity, runtime/model readiness, resolved database
path, schema version, and durable paper/passage counts; it never creates or
migrates a database or configuration file.

`econ_paper_cli.services.config_resolution.resolve_runtime_model_config`
implements the CLI-over-durable-configuration precedence: an explicit CLI
value wins, otherwise durable configuration, otherwise a documented default
(a 300-second generation timeout) or a typed `ConfigResolutionError`. The five
runtime/model identity fields (executable path, model path, model id,
expected size, expected checksum) are resolved as one unit — a CLI invocation
supplies all five together or none of them, since a durable configuration is
already one coherent, previously verified identity that partial CLI mixing
could silently break. `validate_identity_override_shape` performs this
partial-override check independent of whether durable configuration exists or
a generator will ever be constructed, and `analyze`/`chat` call it eagerly, so
a malformed partial override is rejected immediately rather than only on a
path that happens to build a generator. `analyze` and `chat` now accept these
five arguments as optional.

Durable configuration is read through `config_resolution.LazyConfigLoader`,
which loads a `ConfigBackend` at most once per invocation and caches either
the resulting value or the raised `ConfigError`, so a later access never
re-triggers a load. `analyze` and `chat` only force a load (`.get()`) when
actually needed — for a database-path fallback (skipped entirely when
`--db-path` is explicit), or inside generator construction when the CLI
identity is incomplete. Existing lazy-model-resolution guarantees are
preserved exactly: exact analysis-plus-library reuse, generator-free library
backfill, and the `EMPTY_LIBRARY`/`NO_MATCHES` chat outcomes still require
neither configuration nor accessible runtime/model artifacts even when
combined with an explicit `--db-path` and a fully-specified runtime/model
override, because no code path they can take ever calls
`LazyConfigLoader.get()`.

When the CLI identity is fully specified, generator construction still calls
`LazyConfigLoader.peek()` — never `.get()` — so a config load that already
happened for an unrelated reason (resolving `--db-path`) still supplies its
optional `threads`/`timeout_seconds` defaults per the documented CLI > config
> default precedence, without that same identity-complete path ever forcing
a load merely to check them. `peek()` returns `None` when nothing has been
loaded yet, so a fully-specified identity combined with an explicit
`--db-path` (which never triggers `.get()`) correctly falls back to the
command's own documented defaults rather than silently reading durable
configuration it was never supposed to touch.

Issue 56 adds a bare interactive cited-chat shell
(`econ_paper_cli.services.interactive_shell`), reusing the Issue 54
configuration boundary and the retrieval/generation/citation logic already
validated by one-shot `chat`. `open_shell_session` resolves configuration
and the database path exactly like `execute_chat_command` (eager
`validate_identity_override_shape`, lazy `LazyConfigLoader`), then opens the
configured SQLite library read-only exactly once and builds one immutable
`SessionSnapshot` (database path, paper/passage counts, one validated
`Corpus` — or `None` for a genuinely empty library, since `Corpus` requires
at least one paper — and every early-section record loaded at open time into
an immutable mapping). `InteractiveShellSession` constructs one retriever
from that snapshot and reuses it for every question; its `ask()` method is a
per-question, config-independent adaptation of `execute_chat_command`'s
retrieval/generation/citation-validation body, sharing a
`_resolve_citations(record_lookup, ...)` (parameterized by a lookup callable
rather than bound to live storage, so both callers stay byte-for-byte
identical while the shell never re-reads storage per turn) and
`_render_citation_lines` from `chat_command`. Every turn resolves citations
from the snapshot's fixed mapping, never a live re-read, so a concurrent
`analyze`/`update` cannot change or break a citation mid-session; the
session's storage connection closes deterministically
(`InteractiveShellSession.close()`) when the REPL loop exits on every path.
`ShellTurnOutcome` distinguishes `TYPED_FAILURE` from `INTERNAL_FAILURE`
(mirroring one-shot chat's exit-code-2-vs-3 exception grouping, including the
same ValueError-subclass shadowing chat already relies on for citation/
generation validation errors), and every failure branch preserves
`generator_action` ("constructed"/"reused"/`None`) instead of discarding it.
The local generator is constructed lazily on the first question with
retrieval evidence and cached on the session for reuse; a failed
construction attempt leaves the cache untouched so the next matched question
retries from scratch, and empty-library/no-match questions never reach
construction at all.

`run_interactive_shell` is the read-eval-print loop: it reads lines via
`stdin.readline()` rather than the builtin `input()`, so it has no direct
dependency on terminal globals and is fully driven by injectable
`stdin`/`stdout`/`stderr` streams in tests. An empty `readline()` result
(EOF) and `/exit`/`/quit` exit with code 0; `KeyboardInterrupt` while
blocked on `readline()` exits with code 130 and no traceback; a per-question
failure is rendered to `stderr` and the loop continues. `econ_paper_cli.cli`
dispatches bare `econpapers` (no subcommand) to this shell — `arguments.command
is None` is the only signal used, so `econpapers --help` and every other
subcommand are unaffected. The session never writes to configuration or the
database, never reopens or reanalyzes a PDF, and never persists questions,
answers, or citations; the library snapshot is fixed for the life of the
process.

Issue 58 adds managed `llama.cpp` runtime provisioning to `econpapers setup`
so a fresh user no longer has to build `llama.cpp`, edit `PATH`, or locate an
executable manually — see
[`docs/managed-runtime-provisioning.md`](managed-runtime-provisioning.md) for
the full contract. In brief: `econ_paper_cli.domain.runtime_manifest`/
`runtime_manifest_data` pin one `llama.cpp` release per supported
platform/architecture as a plain Python module (not JSON, so it is always
packaged); `econ_paper_cli.services.platform_detection` maps the current
machine onto that vocabulary without raising for unsupported combinations;
`econ_paper_cli.protocols.runtime_provisioning` defines narrow
`Downloader`/`ArchiveExtractor` protocols, backed by stdlib-only real
adapters (`adapters.runtime_downloader.UrllibDownloader`,
`adapters.runtime_extractor.SafeArchiveExtractor`) — this is the only network
access anywhere in the application; `analyze`, `chat`, bare `econpapers`, and
`status` remain unconditionally network-free.
`econ_paper_cli.services.runtime_provisioning.ensure_managed_runtime`
orchestrates stage-into-a-sibling-directory, verify-every-member-checksum-
and-executable-readiness-while-still-staged, then promote via `os.replace`
onto a content-addressed final path that does not already exist — this
makes concurrent installs of the same pinned artifact race-safe by
construction (colliding installs are byte-identical, so losing the promotion
race means adopting the winner rather than failing) and means a corrupt
existing directory at that path is evicted, never overwritten in place.
`econ_paper_cli.domain.runtime_receipt.InstallReceipt` (schema version 1) is
written once per install and records a checksum for every extracted bundle
member, not just the top-level executable, so `econpapers status` and the
setup reuse-check can classify an install as managed only via a validated
receipt — never merely by directory location. `SetupCommandOptions.
executable_path` is now optional: an explicit `--llama-cpp-path` still always
bypasses provisioning entirely (never a download, unchanged CLI-override
precedence), while omitting it triggers reuse-or-download-or-typed-offline-
failure. `econ_paper_cli.services.status_command` reports runtime origin
(`managed`/`external`/`unknown`) and state
(`verified`/`missing`/`corrupt_or_mismatched`/`unsupported_platform`/
`not_checked`) independently from model state
(`verified`/`missing`/`corrupt_or_mismatched`/`not_configured`), so a missing
or corrupt model is never conflated with a corrupt managed runtime.

Future changes should introduce only the narrow interfaces required by their
issue and use dependency injection rather than global state.
