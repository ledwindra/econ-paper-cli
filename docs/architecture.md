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

Future changes should introduce only the narrow interfaces required by their
issue and use dependency injection rather than global state.
