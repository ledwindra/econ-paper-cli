# Product requirements

## Implementation status

Implementation status and milestone progress are tracked in
[`docs/architecture.md`](architecture.md) and
[`docs/roadmap.md`](roadmap.md).

The automatic full-document ingestion and hybrid local-library requirements
below remain future product requirements. The current CLI does not perform
full-document ingestion or build a persistent retrieval index. It does persist
detected Abstract and Introduction Markdown with stable passages and exact
fragment provenance in SQLite alongside research-question analysis records.

Issue 12 implements a configurable local `llama.cpp` subprocess adapter and a
model-independent synthetic generation benchmark. Issue 13 evaluated the two
eligible candidates from that benchmark and deferred a default at that time.

That deferral no longer describes current behavior: `econpapers setup` now
provisions both a pinned `llama.cpp` runtime and a pinned default GGUF model
(Qwen2.5 1.5B Instruct; a 7B variant is available via `--model`) when their
flags are omitted, checksum-verifying each download before installing it —
see `domain/model_manifest.py`, `services/model_provisioning.py`, and
`services/runtime_provisioning.py`. `analyze` and `chat` still accept
explicit per-invocation runtime/model overrides, but no longer require them:
omitting all five identity flags falls back to this durable, `setup`-written
configuration. Automatic model downloads and the selected default model were
approved by the maintainer on 2026-08-08 — see `AGENTS.md`'s "Approved
decisions" — closing that part of Gate 0 in [`../MVP-PLAN.md`](../MVP-PLAN.md).

The CLI also includes a one-shot cited `chat` command and a bare `econpapers`
interactive shell (with follow-up resolution and `/show` evidence
inspection) over the stored local early-section library — see
[`../README.md`](../README.md) for current command syntax and behavior.

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
## PDF analysis workflow

The `econpapers analyze` command runs offline, local-first research-question
analysis on one PDF or a recursively discovered directory of PDFs:

```text
econpapers analyze TARGET_PATH --llama-cpp-path EXECUTABLE_PATH --model-path MODEL_PATH --model-id MODEL_ID --model-bytes BYTES --model-checksum SHA256 [OPTIONS]
```

For a file target, the command preserves the single-record output contract. For
a directory, it discovers case-insensitive `.pdf` files recursively in
canonical-path order, skips later duplicate checksums, and reuses exact durable
records with matching canonical analysis settings. New unique candidates run
the five-stage workflow (`PREFLIGHT`, `EXTRACTION`, `QUALITY_ASSESSMENT`,
`SECTION_DETECTION`, `QUESTION_EXTRACTION`) and are persisted atomically in
SQLite with strict durable read-back. Directory output includes every discovered
path and a deterministic aggregate summary. Processing remains sequential and
offline, and source PDFs are never modified. Candidate checksum/preflight and
storage failures are isolated per path so successfully completed records and
later candidates remain visible.

Eligible candidates also populate the schema-v4 early-section library. Newly
executed analysis reuses its extraction and section-detection results and writes
both complete records in one transaction. Exact compatible repeats call neither
the extractor nor generator. A legacy analysis-only record, or one whose
conversion fingerprint changed, runs only extraction, quality assessment,
section detection, conversion, and library persistence. Preflight, extraction,
and unusable-quality outcomes remain analysis-only. Conversion uses
`early-section-markdown-v1` and a configurable passage budget (default 1,200).

Process exit code semantics:
- `0`: Every unique analysis succeeds or reuses an exact successful record.
- `1`: No typed or internal failure occurs, but at least one record is halted or unavailable.
- `2`: At least one expected preflight, extraction, input, or configuration failure occurs and no unexpected failure occurs.
- `3`: Any unexpected internal failure occurs.

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
