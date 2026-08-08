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

This last bullet was this section's original policy statement, before §10's
managed runtime/model provisioning existed. Both now perform real downloads
(checksum-verified, with pinned sources and sizes); the maintainer approved
automatic model downloads and the default model on 2026-08-08 (see
`AGENTS.md`'s "Approved decisions"), which is why §10 below documents them
as implemented rather than blocked on approval. Sources, licenses,
redistribution status, sizes, checksums, the update policy, and
copyrighted-full-text status are now documented for all six downloadable
artifacts — both GGUF models and all four pinned `llama.cpp` archives — in
[`artifact-licensing.md`](artifact-licensing.md).

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
- Persisted retrieval index or index artifact — **[planned]**, post-MVP.
  `BM25Retriever` is built in memory from `load_corpus()` over the passages
  `analyze` has persisted. `chat` builds one per invocation, after finding
  at least one stored early-section record; the interactive shell builds one
  when its session opens, for a paper library that is non-empty and so has a
  loadable corpus. The two conditions are not identical: a library holding
  only legacy paper records opens a shell corpus but still sends `chat` to
  its empty-library outcome. When a command's respective gate fails, that
  command does not build a retriever: `chat` returns its empty-library
  outcome first, and a shell session opens without one and short-circuits
  questions. `analyze` itself constructs no retriever either, and there is
  still no on-disk index. The
  in-memory rebuild is the **[current]** behavior, not a stopgap that has
  replaced the bundled-index goal

CLI integration is implemented: `chat` and the interactive shell both
construct `BM25Retriever` from the durable library and retrieve against it
— see §10.

## 5. Hybrid local-library requirements and architecture (Issue 11 documented)

Approved design:

- Automatic ordinary-user ingestion from a selected PDF or directory
- Original PDFs as authoritative source inputs that ingestion never modifies
  or deletes
- Inspectable generated Markdown as a derived representation
- Standard-library SQLite as the future structured operational store behind a
  replaceable storage protocol
- Rebuildable retrieval indexes separate from authoritative and structured data
  — **[current]** as an invariant the in-memory BM25 index already satisfies;
  **[planned]** as a constraint on a persisted index
- Checksum-aware deduplication, deterministic re-ingestion, schema migrations,
  and transactional database writes
- Configurable library location, private user data, and offline ingestion
- Separate recovery treatment for source-derived records and unique user state

Not implemented:

- Storage protocol, SQLite schema, migrations, or database files
- PDF discovery, extraction, OCR, conversion, segmentation, or ingestion
- Markdown export or retrieval-index persistence — retrieval-index persistence
  is **[planned]**
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
- Real-model semantic and resource evaluation of the Issue 13 candidate set
  specifically (the mechanical benchmark below); a default model has since
  been selected outside that benchmark — see §10

Claim-level citation association/rendering, artifact download, and artifact update (`econpapers update`) are now
implemented; see §10.

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
- At the time of this benchmark, explicitly deferred the initial default
  because neither candidate passed the mechanical gate; no semantic scoring
  was applicable. A default (Qwen2.5 1.5B Instruct, 7B opt-in) was later
  selected outside this benchmark and is now provisioned by `econpapers
  setup` — see §10. This section is kept as the historical record of the
  Issue 13 benchmark run, not as a statement of current default-model status.

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
- Project successful early-section conversion into an immutable library record
  and persist Markdown, passages, parser/conversion identity, and exact fragment
  provenance atomically in SQLite schema version 4
- Populate that library during single-file and directory analysis, with exact
  dual-record reuse, generator-free legacy backfill, configurable deterministic
  passage sizing, and coordinated analysis/library writes
- Reconstruct persisted passages after restart through `load_corpus()` for use
  by the existing in-memory BM25 adapter

Not yet implemented:

- Add supported OCR
- Extend conversion beyond the currently supported Abstract and Introduction
- Refresh rebuildable retrieval state after library changes — a shell session
  builds its retriever once at startup, so an `analyze` run during that
  session is not reflected until it is reopened
- Verify offline, restart-safe, and cross-platform behavior systematically
  (M5 in [`../MVP-PLAN.md`](../MVP-PLAN.md))

Two items previously on this list have been removed as complete: preflight
eligibility decisions and extraction-quality outcomes are both connected to
`analyze` orchestration — preflight failure, extraction failure,
`LIKELY_NEEDS_OCR`, and `UNUSABLE` all resolve to `NOT_ELIGIBLE` without
reaching conversion. OCR itself remains genuinely unimplemented.

## 10. End-to-end MVP orchestration

Completed:

- One-shot cited chat over the local early-section library
- Durable, versioned local runtime/model configuration; `econpapers setup` and
  `econpapers status`; optional `analyze`/`chat` runtime/model arguments
  resolved from configuration when omitted (Issue 54)
- Bare `econpapers` interactive cited-chat shell reusing the durable library,
  retrieval, generation, citation, and configuration boundaries, with a
  lazily constructed and reused generator (Issue 56)
- Managed `llama.cpp` runtime provisioning during `econpapers setup` — pinned
  per-platform manifest, checksum-verified download, safe extraction, atomic
  content-addressed install, and an install receipt independent of model
  acquisition (Issue 58)
- Managed default GGUF model provisioning during `econpapers setup`,
  independent of runtime acquisition — a pinned model manifest
  (`domain/model_manifest.py`), checksum-verified download, and a default
  model (Qwen2.5 1.5B Instruct, with a 7B variant opt-in via `--model`)
  selected for the analyze/chat/shell path (approved by the maintainer
  2026-08-08, see `AGENTS.md`'s "Approved decisions" — not yet written up in
  this roadmap as its own numbered issue section)
- Claim-level citation association and per-source answer rendering: the
  generator emits per-claim citations, and claims whose wording is
  distinctive to a paper they do not cite are detected and withheld rather
  than shown misattributed (`domain/claim_grounding.py`)
- Follow-up question resolution in the interactive shell: a question
  referring to an earlier turn is rewritten into a standalone question
  before retrieval, shown to the user as `Interpreted as:`
  (`domain/conversation.py`)
- Evidence inspection: `/show` in the shell and `--show-evidence` on
  one-shot `chat` render the full stored passage text behind a citation
- Managed artifact update command (`econpapers update` verifies and repairs
  managed runtime and model artifacts against their pinned manifests and
  durable configuration, including the renamed-pin exception for the model
  side — see M2 in [`../MVP-PLAN.md`](../MVP-PLAN.md))

Not yet implemented:

- Verify offline operation, privacy, restart safety, and cross-platform
  behavior systematically — the pieces exist (`--offline`, network confined
  to `setup`/`update`, SQLite close/reopen round-trips, cross-platform CI),
  but the consolidated release-readiness pass is M5 in
  [`../MVP-PLAN.md`](../MVP-PLAN.md)
- Document artifact licenses and release procedures — artifact licenses are
  recorded in [`artifact-licensing.md`](artifact-licensing.md) (M4 sweep 5)
  and the release procedure in
  [`release-checklist.md`](release-checklist.md) (M5); what remains is
  executing the checklist and committing its run record

"Connect the approved library, ingestion, retrieval, and generation adapters"
was removed from this list as complete: the Completed items directly above
are that connection.
