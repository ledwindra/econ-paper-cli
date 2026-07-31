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

## 6. Local inference and synthesis (Issue 12 next)

Completed:
- Replaceable backend-independent generation protocol
- Immutable JSON-compatible generation request and response contracts
- Structural citation membership and canonical-order validation
- Explicit insufficient-evidence abstention contract

Not yet implemented:
- Concrete local model adapter or default model selection
- Semantic grounded-generation evaluation for factual support, causal
  characterization, uncertainty, disagreement, and evidence sufficiency
- Claim-level citation association or inline rendering
- Approve a default model separately before adding download behavior

## 7. Local library storage foundation

- Define the narrow, database-independent storage or repository protocol
- Design and implement the SQLite schema using standard-library `sqlite3`
- Add schema versions, forward migrations, transactional writes, and
  actionable recovery behavior
- Configure a portable library location outside the source repository
- Preserve stable compatibility with existing paper and passage identities
- Keep source-derived records distinct from unique user state

## 8. Automatic PDF ingestion

- Discover selected PDFs and directories without assuming `/papers/`
- Compute checksums and implement deterministic duplicate and re-ingestion
  behavior
- Extract text and available metadata locally
- Add supported OCR, extraction-quality reporting, and actionable failures
- Generate inspectable Markdown and stable passages with complete provenance
- Populate SQLite through the storage protocol and refresh rebuildable
  retrieval state
- Verify offline, restart-safe, and cross-platform behavior

## 9. End-to-end MVP orchestration

- Connect setup, status, update, chat, follow-up, and evidence inspection
- Connect the approved library, ingestion, retrieval, and generation adapters
- Verify offline operation, privacy, restart safety, and cross-platform behavior
- Document artifact licenses and release procedures
