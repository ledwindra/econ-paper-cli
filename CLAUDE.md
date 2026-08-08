# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read AGENTS.md first

`AGENTS.md` is the binding policy file for this repository and takes priority
over the general guidance below. It defines the source-of-truth order for
conflicting instructions, product guardrails (no required paid API/cloud
model, no Docker, no GPU requirement, no copyrighted full-text
redistribution, no telemetry), architecture rules, decisions that require
maintainer approval, and the definition of done. Read it in full before
making non-trivial changes.

Source-of-truth order when instructions conflict: active GitHub issue/maintainer
instructions > `docs/product-requirements.md` > `docs/architecture.md` >
`docs/roadmap.md` > `AGENTS.md` > existing implementation conventions.

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

CLI entry point after install: `econpapers {setup,status,chat,update,analyze}`.
`update` remains a deterministic placeholder. `setup` validates and durably
persists local runtime/model config, and auto-provisions **both** the pinned
`llama.cpp` runtime and a pinned GGUF model when their flags are omitted
(`--model` selects from `domain/model_manifest.py`; the 1.5B is the default,
the 7B is opt-in); `status` is a read-only report of that
config, runtime readiness, and library state; `analyze` ingests one PDF or a
directory into the local library; `chat QUESTION` answers one question
one-shot. `analyze` and `chat` take the runtime/model flags
(`--llama-cpp-path`, `--model-path`, `--model-id`, `--model-bytes`,
`--model-checksum`) as *optional* per-invocation overrides — omit all five
to fall back to durable config from `setup`; supplying some but not all
five is a typed error. See `--help` on each subcommand for the full flag
set. **On `main`, all of the above is merged and current.**

Bare `econpapers` (no subcommand) opening an interactive multi-question
shell over the same library (each question answered independently, but
follow-ups referring to an earlier turn are rewritten into standalone
questions first — see `domain/conversation.py`; `/reset` clears that context) is issue #56, implemented on branch
`feature/issue-56-interactive-shell` (PR #57) — **not yet merged to `main`**
as of this writing, and the PR has two open review blockers (session
snapshot must hold immutable citation/provenance state instead of
re-reading live storage per turn; `ShellTurnOutcome` needs distinct
typed/internal-failure outcomes and must preserve `generator_action` on
failure paths). Check `gh pr view 57` before assuming this command exists
on whatever branch is currently checked out.

## Architecture

Layered, adapter-oriented design. Dependency direction:

```text
CLI adapters -> application services -> domain types and protocols
                                      <- infrastructure adapters
```

- **`domain/`** — pure, immutable types and validation (papers, passages,
  evidence, citations, corpora, storage records, PDF conversion/quality/
  sections, research questions, single-paper analysis, early-section
  library). No filesystem, network, database, or model-runtime dependency.
- **`protocols/`** — replaceable interfaces (`retrieval.Retriever`,
  `generation` request/response + validation, `pdf_extraction.PDFExtractor`,
  `storage.StorageBackend`). Domain and application code depend on these
  protocols, never on a concrete library.
- **`services/`** — orchestration only (ingestion, PDF conversion/
  extraction/quality/section-detection, research-question extraction,
  single-paper analysis + storage, early-section library,
  chat/batch-analysis/setup/status CLI glue, `config_resolution` for
  CLI-over-durable-config precedence, and `interactive_shell` — the last of
  these is on branch `feature/issue-56-interactive-shell` / PR #57, not yet
  merged to `main`, see the PR-status note above). This is where multi-step
  workflows and reuse/backfill decisions live; CLI handlers stay thin and
  call into here.
- **`domain/claim_grounding.py`** — pure, structural detection of cross-paper
  leakage: a generated claim using a term distinctive to a paper it does not
  cite is misattributed. `chat` and the interactive shell withhold such claims
  and report outcome `withheld` (distinct from `abstained`) when none survive.
- **`adapters/`** — concrete, swappable implementations: `bm25.py` (pure
  in-memory BM25 retriever, `bm25-v1` tokenizer), `llama_cpp.py`
  (`llama-completion` subprocess generation adapter, prompt `generation-v3`:
  the model emits per-claim citations and the adapter derives the response
  citation list from them), `pypdf_extractor.py`,
  `sqlite_storage.py` (stdlib `sqlite3`, versioned schema + migrations,
  atomic per-record transactions), `storage_paths.py` (cross-platform data
  and config dir resolution, `ECONPAPERS_LIBRARY_DIR`/`ECONPAPERS_CONFIG_DIR`
  overrides), `config_storage.py` (atomic JSON runtime/model config), `corpus.py`,
  `filesystem.py` (checksum/size verification).
- **`evaluation/`** — frozen benchmarks and structural (not semantic)
  scoring for retrieval and generation; never gates on claims not backed by
  the benchmark.

Key invariants to preserve when touching this code:

- Never bind domain/service code directly to FAISS, a specific database,
  `llama.cpp`, or a specific embedding model — go through the protocol.
- Evidence stays structured end-to-end (paper identity + passage boundaries);
  don't collapse it into unstructured strings.
- Generated citation identifiers must be validated against retrieved evidence
  (`validate_generation_response`) before anything reaches the user.
- Retrieval results must pass `validate_retrieval_results` (contiguous
  1-based ranks, non-increasing score order, ascending `passage_id`
  tie-break, near-dup suppression already applied).
- Ingestion/analysis never modifies or deletes source PDFs; SQLite writes for
  one paper record happen inside a single transaction with full rollback.
- `econpapers analyze` decides exact-reuse vs. legacy-library-backfill vs.
  fresh-generation *before* constructing the local model adapter — reuse and
  backfill paths must not require accessible model artifacts.

For the historical, issue-by-issue build order and exact behavioral contracts
of each layer, see `docs/architecture.md` (long-form) and the topic docs it
links out to (`docs/retrieval-contract.md`, `docs/generation-contract.md`,
`docs/early-section-library-storage.md`, `docs/pdf-early-section-conversion.md`,
`docs/pdf-quality-assessment.md`, `docs/local-generation-evaluation.md`, etc.).

## Before opening or updating a PR

AGENTS.md's "Pre-PR contract self-review" section is the checklist to run
before every PR here — CI passing is not sufficient. This repo's reviews
(PRs #55 and #57 both, so far) have caught real contract gaps that still
passed every test: a "snapshot"/"fixed"/"once" claim that turned out to
still re-read a live source somewhere, an "even when X" clause with no
regression for that exact combination, and a result enum that silently
collapsed distinct required outcomes (or dropped a field on an error path
that should still have been populated). Also recurring: tests that
accidentally hit the real default config/db path instead of an injected one
or `ECONPAPERS_CONFIG_DIR`/`ECONPAPERS_LIBRARY_DIR`, and assertions that a
literal POSIX path string survives `Path.resolve()` unchanged (it won't on
Windows). Re-read the issue's exact scope sentence by sentence against the
diff before calling a PR done.

## Corpus, models, and papers/ directory

`papers/`, `models/`, `runtimes/`, `artifacts/`, `generation-results/` in
this checkout hold local, gitignored working data (source PDFs, GGUF models,
the pinned `llama.cpp` b10199 runtime, artifact manifests, evaluation
outputs). None of this is committed or redistributed — see AGENTS.md's
corpus/licensing rules before adding anything under these paths or touching
`.gitignore`.
