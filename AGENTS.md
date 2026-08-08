# AGENTS.md

This file defines how coding agents must work in the `econ-paper-cli` repository.

## Mission

Build a free, open-source, local-first conversational literature search tool for economists.

The product should answer research questions with concise synthesis and inspectable evidence while running without paid cloud services.

## Source-of-truth order

When instructions conflict, use this priority:

1. The active GitHub issue and explicit maintainer instructions
2. `docs/product-requirements.md`
3. `docs/architecture.md`
4. `docs/roadmap.md`
5. This file
6. Existing implementation conventions

Do not silently reinterpret product requirements. Ask for clarification when a requested change would alter product scope, architecture, legal posture, or user-facing behavior.

## Commands

Install (editable, with dev tools):

```bash
python -m pip install -e ".[dev]"
```

Before finishing any change, run all three:

```bash
ruff check .
ruff format --check .
pytest
```

Run a single test file or test:

```bash
pytest tests/domain/test_papers.py
pytest tests/domain/test_papers.py::test_paper_rejects_empty_title
```

Model-dependent integration tests are opt-in and require a manually installed
local runtime/model (see `integration_tests/`); the default `pytest` run does
not need them. They are marked `model` (see `pyproject.toml`
`[tool.pytest.ini_options]`).

## Required workflow

For every issue:

1. Read the entire issue and relevant documentation.
2. Inspect existing code and tests before proposing changes.
3. State a compact implementation plan.
4. Implement the smallest complete change that satisfies the issue.
5. Add or update tests.
6. Update documentation when behavior changes.
7. Run all applicable checks.
8. Summarize what changed, why, and any remaining risks.

Work on one issue per pull request.

Do not mix unrelated refactors, formatting changes, or cleanup into feature work.

## Product guardrails

Do not:

- introduce a required paid API;
- require OpenAI, Anthropic, or another hosted model provider;
- require Docker;
- require a GPU;
- redistribute copyrighted paper full text without documented permission;
- weaken citation or evidence traceability;
- collect telemetry by default;
- upload user queries, documents, or indexes without explicit opt-in (**[current]** rule);
- redesign the architecture when a narrow interface extension is sufficient.

A cloud backend may only be added later as an optional adapter. It must never become necessary for the default workflow.

## Architecture rules

- Keep domain types and protocols independent of concrete inference and retrieval libraries.
- Put orchestration in service modules, not in CLI handlers.
- Keep filesystem and network effects behind adapters.
- Prefer dependency injection over global state.
- Preserve deterministic, testable interfaces.
- Keep the CLI thin.
- Represent evidence explicitly; do not pass unstructured strings when source identity and passage boundaries matter.
- Keep retrieval, generation, corpus ingestion, and artifact management separable.
- Do not bind core logic directly to FAISS, a database, `llama.cpp`, or a specific embedding model.
- Model downloads, corpus downloads, and index updates must eventually be resumable or safely restartable. **[planned]** for index updates: there is no on-disk index to update, so this binds nothing today.
- Every downloaded artifact must eventually have a manifest entry and checksum verification.

### Repository map

CLI entry point: `econpapers {setup,status,chat,update,analyze}`, plus bare
`econpapers` (no subcommand) for the interactive shell. `setup` validates and
durably persists local runtime/model config, and auto-provisions both the
pinned `llama.cpp` runtime and a pinned GGUF model when their flags are
omitted (`--model` selects from `domain/model_manifest.py`; the 1.5B is the
default, the 7B is opt-in — see "Approved decisions" below). `status` is a
read-only report of that config, runtime readiness, and library state.
`analyze` ingests one PDF or a directory into the local library. `chat
QUESTION` answers one question one-shot; bare `econpapers` opens a
multi-question shell over the same library, with follow-up resolution
(`domain/conversation.py`) and evidence inspection (`/show`, `--show-evidence`
on `chat`). `analyze` and `chat` take the runtime/model flags
(`--llama-cpp-path`, `--model-path`, `--model-id`, `--model-bytes`,
`--model-checksum`) as *optional* per-invocation overrides — omit all five to
fall back to durable config from `setup`; supplying some but not all five is
a typed error. `update` verifies and repairs managed local runtime and model
artifacts against their pinned manifests and durable configuration. See `--help` on each subcommand for
the full current flag set; `main` is always current.

Layered, adapter-oriented design. Dependency direction:

```text
CLI adapters -> application services -> domain types and protocols
                                      <- infrastructure adapters
```

- **`domain/`** — pure, immutable types and validation (papers, passages,
  evidence, citations, corpora, storage records, PDF conversion/quality/
  sections, research questions, single-paper analysis, early-section
  library, `claim_grounding.py` for cross-paper leakage detection,
  `conversation.py` for follow-up detection, `model_manifest.py` /
  `runtime_manifest.py` for pinned managed-artifact catalogs). No
  filesystem, network, database, or model-runtime dependency.
- **`protocols/`** — replaceable interfaces (`retrieval.Retriever`,
  `generation` request/response + validation, `pdf_extraction.PDFExtractor`,
  `storage.StorageBackend`, `runtime_provisioning.Downloader`/
  `ArchiveExtractor`). Domain and application code depend on these
  protocols, never on a concrete library.
- **`services/`** — orchestration only (ingestion, PDF conversion/
  extraction/quality/section-detection, research-question extraction,
  single-paper analysis + storage, early-section library,
  chat/batch-analysis/setup/status/interactive-shell CLI glue,
  `config_resolution` for CLI-over-durable-config precedence,
  `runtime_provisioning`/`model_provisioning` for managed-artifact
  verify/repair/atomic-install). This is where multi-step workflows and
  reuse/backfill decisions live; CLI handlers stay thin and call into here.
- **`adapters/`** — concrete, swappable implementations: `bm25.py` (pure
  in-memory BM25 retriever, `bm25-v1` tokenizer), `llama_cpp.py`
  (`llama-completion` subprocess generation adapter, prompt `generation-v3`:
  the model emits per-claim citations and the adapter derives the response
  citation list from them), `pypdf_extractor.py`, `sqlite_storage.py` (stdlib
  `sqlite3`, versioned schema + migrations, atomic per-record transactions),
  `storage_paths.py` (cross-platform data/config dir resolution,
  `ECONPAPERS_LIBRARY_DIR`/`ECONPAPERS_CONFIG_DIR` overrides),
  `config_storage.py` (atomic JSON runtime/model config), `corpus.py`,
  `filesystem.py` (checksum/size verification), `runtime_downloader.py` /
  `runtime_extractor.py` (HTTPS-only downloader, safe archive extraction —
  the only network access anywhere in the application, gated to explicit
  `setup`/`update` invocation).
- **`evaluation/`** — frozen benchmarks and structural (not semantic)
  scoring for retrieval and generation; never gates on claims not backed by
  the benchmark.

Specific invariants worth naming explicitly, beyond the general rules above:

- Evidence stays structured end-to-end (paper identity + passage
  boundaries); don't collapse it into unstructured strings.
- Generated citation identifiers must be validated against retrieved
  evidence (`validate_generation_response`) before anything reaches the
  user.
- Retrieval results must pass `validate_retrieval_results` (contiguous
  1-based ranks, non-increasing score order, ascending `passage_id`
  tie-break, near-dup suppression already applied).
- Ingestion/analysis never modifies or deletes source PDFs; SQLite writes
  for one paper record happen inside a single transaction with full
  rollback.
- `econpapers analyze` decides exact-reuse vs. legacy-library-backfill vs.
  fresh-generation *before* constructing the local model adapter — reuse and
  backfill paths must not require accessible model artifacts.

For the historical, issue-by-issue build order and exact behavioral
contracts of each layer, see `docs/architecture.md` (long-form) and the
topic docs it links out to (`docs/retrieval-contract.md`,
`docs/generation-contract.md`, `docs/early-section-library-storage.md`,
`docs/pdf-early-section-conversion.md`, `docs/pdf-quality-assessment.md`,
`docs/local-generation-evaluation.md`, etc.).

### Corpus, models, and papers/ directory

`papers/`, `models/`, `runtimes/`, `artifacts/`, `generation-results/` in a
local checkout hold local, gitignored working data (source PDFs, GGUF
models, the pinned `llama.cpp` runtime, artifact manifests, evaluation
outputs). None of this is committed or redistributed — see the corpus and
licensing requirements above before adding anything under these paths or
touching `.gitignore`.

## Python conventions

- Support Python 3.10 and newer unless the product requirements change.
- Use type hints for public functions and methods.
- Prefer the standard library when it is adequate.
- Add dependencies only when they materially reduce risk or implementation complexity.
- Use `pathlib.Path` for filesystem paths.
- Use explicit exceptions with actionable messages.
- Keep functions focused and side effects visible.
- Avoid broad `except Exception` blocks unless re-raising with useful context.
- Do not suppress type, lint, or test errors without an explanation.
- Keep platform-specific code isolated and tested.

## Retrieval requirements

Retrieval code must:

- return structured evidence objects;
- preserve paper identity;
- preserve passage boundaries;
- expose ranking scores;
- support deterministic tests;
- avoid duplicate or near-duplicate passages;
- allow later replacement of the retrieval backend;
- separate indexing from query-time retrieval (**[current]** rule; `BM25Retriever` builds its index at construction, not per query).

Do not claim that a retrieval method is superior without a benchmark against representative economics questions.

## Generation requirements

Generation code must:

- use only supplied evidence for substantive claims;
- return structured evidence references;
- support abstention when evidence is insufficient;
- report a backend-declared descriptive-versus-causal characterization *when the model supplies one*, without claiming to classify findings itself. **[current]**: a response-level `finding_kinds` label that the model asserts and may legitimately omit, so an answered response may carry no characterization at all. When present, it is structurally validated (legal enum values, no duplicates), and the default `llama.cpp` adapter additionally constrains it by grammar — that constraint belongs to the adapter, not to the generation protocol, and a replacement adapter satisfies the protocol without any grammar. The label is **not** verified against the cited evidence, so nothing here determines whether a finding is causal; semantic validation of that characterization is **[planned]**;
- preserve uncertainty and disagreement;
- avoid inventing paper titles, authors, methods, or results;
- allow the local model backend to be replaced without changing application services.

Generated citation identifiers must be validated against retrieved evidence before an answer is shown to the user.

## Corpus and licensing requirements

Before adding a corpus, model, or index artifact, document (**[current]** rule; no index artifact exists yet, so for indexes it first applies to the **[planned]** persisted index):

- source;
- license;
- redistribution status;
- expected file size;
- checksum;
- update policy;
- whether it contains copyrighted full text.

Do not commit:

- NBER PDFs;
- converted copyrighted papers;
- model weights;
- user-generated indexes (**[current]** rule);
- cached downloads;
- restricted datasets.

When redistribution rights are unclear, stop and ask the maintainer.

## Testing

Changed behavior must have tests.

Prefer:

- unit tests for domain logic;
- adapter contract tests;
- temporary directories for filesystem behavior;
- deterministic fixtures;
- synthetic or redistributable text;
- no network calls in the default test suite.

Before finishing, run:

```bash
ruff check .
ruff format --check .
pytest
```

If a command cannot run, state exactly why.

Tests must not download models, papers, or indexes unless explicitly marked as integration or release tests. **[current]** rule.

## Pre-PR contract self-review

Before opening or updating a pull request, reread the issue's exact scope and behavioral requirements sentence by sentence against the diff. Green CI is necessary, not sufficient — reviews on this repository have repeatedly found contract-level gaps that still pass every test:

- Verify every "once", "fixed", "snapshot", "read-only", or "immutable" claim is literally true: find every later read of that data and confirm none of them re-hit a live source (open connection, filesystem, config backend) instead of the captured state.
- Verify every "even when X" / "regardless of Y" clause has a regression covering exactly that combination, not just the common case.
- Do not collapse distinct outcomes the issue lists (for example, typed vs. internal failure) into one generic result, and do not let exception-handling branches silently drop fields that should still be populated on a failure path.
- Isolate tests from the real machine: inject storage/config backends, or set `ECONPAPERS_CONFIG_DIR`/`ECONPAPERS_LIBRARY_DIR`, rather than relying on defaults that resolve to the developer's actual home directory.
- Do not assert that a literal path string survives unchanged; `Path.resolve()` and other canonicalization can change it, especially on Windows, where a POSIX-style literal is not a real absolute path.

## Git discipline

- Use small, descriptive commits.
- Keep one issue per pull request.
- Do not rewrite unrelated history.
- Do not modify lockfiles unless dependencies changed.
- Never commit secrets, tokens, cookies, or personal paths.
- Do not commit model files, paper PDFs, local indexes, caches, or virtual environments (**[current]** rule).
- Inspect the final diff before completion.
- Keep pull requests narrowly reviewable.

## Documentation

Update documentation when changing:

- CLI commands;
- configuration;
- supported platforms;
- artifact layout;
- corpus behavior;
- model behavior;
- retrieval behavior;
- installation;
- privacy or network behavior.

Documentation should describe current behavior, not merely intended future behavior.

## Definition of done

An issue is complete only when:

- acceptance criteria are satisfied;
- tests cover the new behavior;
- lint and tests pass;
- documentation is updated;
- Windows, macOS, and Linux implications were considered;
- failure modes produce actionable messages;
- no prohibited cloud or corpus dependency was introduced;
- the final diff contains no unrelated changes.

## Decisions requiring maintainer approval

Ask before:

- changing CLI command names;
- changing the default local-model family;
- selecting a corpus redistribution strategy;
- adopting a database or vector-store dependency;
- introducing automatic model downloads;
- changing the package name;
- changing the license;
- adding telemetry;
- adding hosted inference;
- adding web search;
- changing the supported Python versions;
- changing the evidence citation format.

### Approved decisions

- **2026-08-08 — default local-model family and automatic model
  downloads.** The maintainer approved `econpapers setup`'s managed model
  provisioning as final: Qwen2.5 1.5B Instruct Q4_K_M as the default (no
  flags required), Qwen2.5 7B Instruct Q4_K_M available via `--model`, both
  checksum-verified downloads from a pinned manifest
  (`domain/model_manifest.py`), with explicit per-invocation flags
  (`--model-path`/`--model-id`/`--model-bytes`/`--model-checksum`, all four
  together) remaining available to bypass provisioning entirely. This closes
  the "changing the default local-model family" and "introducing automatic
  model downloads" items above for this specific feature; both remain
  standing policy for any future change to either.

## Agent responsibilities

The maintainer defines product direction and approves major decisions.

The coding agent:

- implements scoped issues;
- preserves architecture and constraints;
- adds tests;
- reports uncertainty;
- avoids making product decisions by implication;
- automatically schedules background monitoring for open PR reviews when requested, without requiring explicit user scheduling prompts or slash commands.

When requirements are incomplete, prefer clarification over architectural invention.
