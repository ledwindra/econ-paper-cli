# Roadmap

This roadmap orders small, reviewable slices. It does not imply that unbuilt
features are available.

## 1. Repository foundation (implemented)

- Installable Python 3.10+ package using a `src` layout
- Thin `econpapers` CLI with setup, status, chat, and update placeholders
- Cross-platform lint and test automation
- Initial product and architecture constraints

## 2. Artifact contracts (implemented)

- Define manifests, checksum syntax, local paths, and actionable validation
  errors (schema-version-1 domain contract implemented)
- Add filesystem loading, size checking, and checksum calculation behind adapters (implemented)
- Keep downloads absent until sources, licenses, sizes, and update policies are
  approved

## 3. Corpus contracts and fixtures (implemented)

- Define pure, immutable `Corpus` domain contract with cross-record invariant validation
- Add 100% synthetic, legally redistributable CC0-1.0 fixture corpus (`synthetic-economics-v1.json`)
- Add filesystem loader adapter (`load_corpus_from_file`) returning `Corpus` domain objects
- Verify manifest size and SHA-256 digests relative to repo root
- Preserve clean separation between artifact digest verification and domain parsing

## 4. Evidence and retrieval contracts

Completed:
- `Paper`, `Passage`, `RetrievalEvidence`, and `Citation` domain contracts
- Synthetic CC0 corpus suitable for deterministic retrieval evaluation
- Replaceable retrieval protocol (`Retriever`)
- Immutable retrieval request (`RetrievalRequest`)
- Deterministic retrieval-result contract validation (`validate_retrieval_results`)
- Protocol and contract unit tests
- Deterministic pure-Python BM25 sparse retrieval baseline (`BM25Retriever`)
- In-memory corpus statistics for BM25 query-time retrieval
- Normalized lexical duplicate suppression
- Adapter contract and unit tests
- Representative synthetic economics retrieval benchmark
- Binary passage-level relevance judgments and deterministic retrieval quality metrics
- Corpus-fingerprint-pinned CI regression gates for the untuned BM25 baseline
- Evidence-based selection of BM25 as the initial, replaceable retrieval backend
- Backend-independent offline resource-observation helper and script
- Stable cross-platform ranked-result digest excluding raw floating-point scores

Not yet implemented:
- Additional retrieval-adapter implementation or broader comparison benchmark
- Persisted retrieval index or index artifact
- CLI integration

## 5. Hybrid local-library requirements and architecture (Issue 11 documented)

Approved design:

- Automatic ordinary-user ingestion from a selected PDF or directory
- Original PDFs as authoritative source inputs that ingestion never modifies
  or deletes
- Inspectable generated Markdown as a derived representation
- Standard-library SQLite as the future structured operational store behind a
  replaceable storage protocol
- Rebuildable retrieval indexes separate from authoritative and structured data
- Checksum-aware deduplication, deterministic re-ingestion, schema migrations,
  and transactional database writes
- Configurable library location, private user data, and offline ingestion
- Separate recovery treatment for source-derived records and unique user state

Not implemented:

- Storage protocol, SQLite schema, migrations, or database files
- PDF discovery, extraction, OCR, conversion, segmentation, or ingestion
- Markdown export or retrieval-index persistence
- Ingestion CLI syntax

## 6. Local inference adapter and evaluation framework (Issue 12)

Completed:
- Replaceable backend-independent generation protocol
- Immutable JSON-compatible generation request and response contracts
- Structural citation membership and canonical-order validation
- Explicit insufficient-evidence abstention contract
- Configurable local `llama-completion` subprocess adapter with explicit model
  paths
- Versioned evidence-only prompt, authoritative JSON schema, and fingerprinted
  derived GBNF constraint
- Adapter-side authoritative citation resolution and response validation
- Model-free fake-process, privacy, failure, and cross-platform tests
- Fingerprinted twelve-case CC0 synthetic generation benchmark
- Blinded semantic-review rubric and opt-in real-model evaluation tooling
- Exact metadata for three Issue 13 evaluation candidates

Not yet implemented:
- Real-model semantic and resource evaluation
- Initial default-model selection
- Claim-level citation association or inline rendering
- Artifact download or update behavior

## 7. Real-model evaluation and default decision (Issue 13)

Completed:

- Excluded SmolLM2 because its immutable source-model revision and conversion
  provenance remain incomplete
- Verified and executed the two eligible official Qwen artifacts with the
  pinned runtime and common pre-registered configuration
- Preserved each first-run mechanical failure and marked the remaining 35
  scheduled runs `not_run`
- Recorded the limited observational resource measurements available from the
  portable runner
- Explicitly deferred the initial default because neither candidate passed the
  mechanical gate; no semantic scoring was applicable

## 8. Local library storage foundation (Issue 24 implemented)

- Define the narrow, database-independent storage protocol (`StorageBackend`) (implemented)
- Design and implement the SQLite schema using standard-library `sqlite3` (`SQLiteStorage`) (implemented)
- Add schema versions, forward migrations, transactional writes, and actionable recovery behavior (implemented)
- Configure portable library location outside the source repository for Windows, macOS, and Linux (implemented)
- Preserve stable compatibility with existing paper and passage identities (implemented)
- Keep source-derived records distinct from unique user state (implemented)


## 9. Automatic PDF ingestion

Completed:

- Discover selected PDFs and directories without assuming `/papers/`
- Compute checksums, deduplicate content within a discovery batch, and classify
  previously stored checksums through the storage protocol
- Extract ordered page text and available raw document metadata locally through
  a replaceable parser protocol
- Preserve canonical source paths, page boundaries, parser provenance, and typed
  failures for missing, unreadable, malformed, and password-encrypted PDFs
- Assess successful extraction results through immutable versioned settings,
  deterministic measurements and statuses, and stable actionable warnings
- Analyze a selected PDF or directory sequentially through the existing
  early-section research-question workflow, with deterministic ordering,
  duplicate-byte suppression, exact durable-record reuse, and isolated
  per-file outcomes
- Convert detected Abstract and Introduction spans into deterministic,
  inspectable Markdown and stable passages with exact page-local provenance

Not yet implemented:

- Connect preflight eligibility decisions to extraction orchestration
- Add supported OCR and connect extraction-quality outcomes to orchestration
- Extend conversion beyond the currently supported Abstract and Introduction
- Populate SQLite through the storage protocol and refresh rebuildable
  retrieval state
- Verify offline, restart-safe, and cross-platform behavior

## 10. End-to-end MVP orchestration

- Connect setup, status, update, chat, follow-up, and evidence inspection
- Connect the approved library, ingestion, retrieval, and generation adapters
- Verify offline operation, privacy, restart safety, and cross-platform behavior
- Document artifact licenses and release procedures
