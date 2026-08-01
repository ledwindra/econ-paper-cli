# Product requirements

## Implementation status

Implementation status and milestone progress are tracked in
[`docs/architecture.md`](architecture.md) and
[`docs/roadmap.md`](roadmap.md).

The automatic ingestion and hybrid local-library requirements below are
approved future product requirements. They are not currently implemented. The
current CLI does not ingest PDFs, create Markdown, use SQLite, or build a
production retrieval index.

Issue 12 implements a configurable local `llama.cpp` subprocess adapter and a
model-independent synthetic generation benchmark. Issue 13 evaluated the two
eligible candidates, but neither passed the first mechanical run; the initial
default model is therefore explicitly deferred. Generation remains
disconnected from the CLI, and the application does not download artifacts.

## Mission

Econ Paper CLI is a free, open-source, local-first conversational literature
search tool for economists. It should answer research questions with concise
synthesis and make the supporting papers and passages inspectable.

## MVP requirements

The MVP must:

- provide an `econpapers` CLI with setup, status, chat, update, follow-up, and
  evidence-inspection workflows;
- run retrieval and language-model inference locally in the default workflow;
- require no paid service, API key, Docker installation, or GPU;
- operate offline after the user has installed the required artifacts;
- support Python 3.10 or newer on Windows, macOS, and Linux;
- preserve paper identity and passage boundaries in evidence;
- ground substantive claims in supplied evidence and abstain when evidence is
  insufficient;
- distinguish descriptive findings from causal findings and retain uncertainty
  or disagreement;
- avoid telemetry and uploads of queries, documents, or indexes by default; and
- distribute only metadata, permitted derived artifacts, and content with
## Single-PDF analysis workflow

The `econpapers analyze` command runs an offline, local-first single-paper research-question analysis on one PDF document:

```text
econpapers analyze PDF_PATH --llama-cpp-path EXECUTABLE_PATH --model-path MODEL_PATH --model-id MODEL_ID --model-bytes BYTES --model-checksum SHA256 [OPTIONS]
```

It executes the five-stage workflow (`PREFLIGHT`, `EXTRACTION`, `QUALITY_ASSESSMENT`, `SECTION_DETECTION`, `QUESTION_EXTRACTION`), persists the result atomically in SQLite, reads back the durable record, and renders structured evidence and provenance to standard output.

Process exit code semantics:
- `0`: Successful research question extraction (`SUCCESS`).
- `1`: Halted or unavailable analysis (`QUALITY_HALTED`, `QUESTION_EXTRACTION_HALTED`).
- `2`: Expected preflight, extraction, input, or configuration failure (`PREFLIGHT_FAILED`, `EXTRACTION_FAILED`, invalid arguments or missing local models).
- `3`: Unexpected internal process failure.

## Artifact requirements

Every corpus, index, or model artifact must have versioned metadata describing
its source, license, redistribution status, expected size, SHA-256 digest,
update policy, copyrighted-full-text status, and portable local path. Manifest
validation does not itself establish legal permission or verify file contents.

Artifact metadata and checksum verification must remain separable from network
downloads and filesystem persistence. Unknown or prohibited redistribution
status must never be interpreted as authorization to download or redistribute.

## Automatic local paper ingestion

For ordinary use, a user should only need to place legally obtained PDF files
in a folder, or select a PDF or folder, and invoke ingestion. The application
must automatically:

1. discover supported PDF files;
2. compute a SHA-256 content checksum;
3. detect duplicate or previously ingested content;
4. register the original PDF as the authoritative input without modifying or
   deleting it;
5. extract text and available bibliographic metadata locally;
6. invoke OCR when it is required and locally supported;
7. assess extraction quality and record warnings;
8. produce inspectable structured Markdown;
9. segment the document into stable passages;
10. preserve paper, page, section, source-file, checksum, extraction-method,
    and conversion-version provenance;
11. write structured records to SQLite transactionally;
12. build or refresh rebuildable retrieval state; and
13. record success, warnings, extraction quality, and actionable failures.

Manual conversion, manifest creation, metadata entry, passage segmentation,
identifier assignment, and database insertion must not be required. A future
command may resemble `econpapers ingest /path/to/papers`, but Issue 11 does not
approve the command name, syntax, or flags.

Ingestion must be safe to repeat. Duplicate detection and re-ingestion must be
deterministic and checksum-aware, and they must not silently duplicate papers.
Corrupted, encrypted, scanned, or unusually formatted PDFs must produce
explicit warnings or actionable failures instead of apparently valid
low-quality content.

## Hybrid local paper library

The future local library has four storage layers:

- Original PDFs are authoritative user-provided source documents. Ingestion
  must never modify or delete them. Whether the application later manages
  private copies or registers files in place remains undecided.
- Generated Markdown is a human-readable, inspectable derived representation.
  It is not the sole structured datastore.
- SQLite stores the structured catalog, retrieval-ready passage text,
  metadata, provenance, checksums, ingestion state, and other application
  state. The MVP must use Python's standard-library `sqlite3` unless a later
  issue demonstrates that another dependency is necessary.
- Retrieval indexes are rebuildable search accelerators. They must not be the
  only copy of paper or passage data.

The actual library location must be configurable and usable outside the source
repository. The repository-root `/papers/` directory remains an ignored
convenience location and must not become a hard-coded application path. Users
must be able to inspect or export derived Markdown so their library is not
trapped in an opaque database.

Domain and application layers must not depend directly on SQLite. Storage
access must sit behind a replaceable repository or storage protocol, with
filesystem, SQLite, and retrieval-index effects implemented by adapters.

## Integrity, identity, and schema evolution

- SQLite schema versions and forward migrations are required.
- Database writes belonging to one ingestion operation must occur in one
  SQLite transaction.
- Filesystem, SQLite, and retrieval-index changes are separate resources.
  Issue 11 does not promise one atomic transaction across all three.
- Re-ingestion and duplicate detection must be deterministic and
  checksum-aware.
- Stable `paper_id` and `passage_id` identities must remain compatible with
  the existing domain contracts.
- Original source location, content checksum, extraction method, page and
  section location, and conversion version must remain traceable.
- Retrieval-ready passage text must be stored in SQLite so Markdown does not
  need to be reparsed on every run.

The exact schema, migration mechanism, identifier derivation rules, file
layout, and cross-resource recovery procedure remain later implementation
decisions.

## Rebuild and recovery

Generated Markdown, source-derived SQLite catalog records, passage text,
provenance, and retrieval indexes must be rebuildable from accessible
authoritative PDFs plus versioned conversion logic and configuration. This
recovery guarantee is conditional on the PDFs remaining accessible, or on a
later approved managed-copy policy preserving them. Registering a PDF stored
outside the library cannot protect it if the user later moves or deletes it.

Not all future application state is necessarily source-derived. User-created
annotations, metadata corrections, preferences, chat history, and similar
unique state may require backup, export, or separate recovery behavior. Issue
11 does not design those features or claim that PDFs can reconstruct them.

Retrieval indexes must be rebuildable from stored passage records and must
never become the only copy of paper or passage content. Failed or interrupted
work must not silently replace a previously valid ingestion or present
incomplete records as successful.

## Privacy and offline operation

- Retrieval, generation, indexing, and corpus inspection must run locally in
  the default workflow.
- Ordinary PDF ingestion, conversion, OCR, passage creation, database updates,
  and index refresh must run locally and must not require network access.
- No user queries, documents, or search history may be uploaded to external
  services by default.
- User PDFs, converted copyrighted full text, generated Markdown, databases,
  and indexes are private user data and must not be committed or redistributed.
- Default operation must remain offline after the required artifacts are
  installed.
- Network access may occur only through explicit setup, manual update, or other
  separately approved opt-in adapters.
- Network access must not be triggered implicitly during retrieval, generation,
  corpus inspection, ingestion, or ordinary CLI use.
- Any future metadata lookup or enrichment service requires separate approval
  and must be an explicit opt-in adapter.
- The MVP must not require PostgreSQL, a database server, Docker, a cloud
  database, or a vector database.

## Evidence and grounding

- Substantive claims in synthesized answers must cite valid retrieved evidence
  passages.
- Citation identifiers in generated answers must be verified against retrieved
  evidence before output is rendered.
- Retrieval results must preserve paper identity and passage boundaries.
- Results must expose deterministic ranks and scores at the protocol boundary.
- Higher scores represent stronger matches at the protocol boundary.
- Retrieval implementations must be replaceable.
- A concrete retrieval implementation must be evaluated on representative
  economics questions before it is selected or described as the default
  retrieval backend.
- When retrieved evidence is insufficient to answer a query, the system must
  explicitly abstain rather than fabricate claims.

## Deferred capabilities

New or changed model, corpus, index, artifact, and citation-format decisions
require explicit design and maintainer approval before implementation. The
following remain out of scope for default execution:

- Paid cloud inference APIs (e.g. OpenAI, Anthropic)
- Mandatory Docker or GPU requirements
- Redistribution of copyrighted paper full text without permission
- Automatic background telemetry or query tracking
- Automatic or implicit online metadata enrichment
