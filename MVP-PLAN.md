# MVP plan

## Purpose and current baseline

The working tree has an end-to-end path over a private local corpus: `setup`
provisions local runtime/model artifacts, `analyze` stores PDF-derived
Abstract/Introduction records in SQLite, and `chat` plus the interactive shell
produce locally generated answers with claim-level citations and cross-paper
grounding checks.

The current bottleneck is operational trust: a non-technical economist must be
able to inspect evidence, repair local artifacts, understand the supported
corpus scope, and verify that the workflow is offline and restart-safe.

This document is a plan for the current working tree, not a claim that a clean
checkout already satisfies the plan. The test baseline, private-corpus
location, model artifacts, and configuration paths must be recorded when each
milestone is accepted.

## Gate 0 — Reconcile the MVP contract

This gate must be resolved before calling M1–M5 “MVP complete.” The repository
currently contains conflicting statements about:

- whether MVP includes a bundled metadata/retrieval index or a user-provided
  local early-section library;
- whether descriptive-versus-causal classification is an MVP guarantee;
- whether setup may automatically download model artifacts; and
- whether the default model is selected or still deferred.

The maintainer must choose one coherent contract and update
`README.md`, `docs/product-requirements.md`, `docs/architecture.md`, and
`docs/roadmap.md` together. The decision must preserve the project guardrails:
local inference, no paid service, no required GPU, no telemetry, and no
redistribution of copyrighted full text.

Until this gate is resolved, the plan treats the implemented product scope as:
local user-provided PDFs, stored Abstract/Introduction passages, local BM25
retrieval, local generation, inspectable evidence, and no persisted retrieval
index. It does not silently redefine the README MVP requirements.

## Milestone ladder

| # | Milestone | Exit condition |
| --- | --- | --- |
| Gate 0 | Reconcile MVP contract | Requirements and status documentation agree on corpus, model, index, and finding-kind scope. |
| M1 | Evidence inspection ✅ | A user can inspect the exact stored passage for a citation in both CLI surfaces. Implemented: `/show` in the shell, `--show-evidence` on one-shot `chat`, shared `format_evidence_detail` renderer, control-character/CRLF sanitization, reviewed adversarially (codex) and fixed. |
| M2 | Real `econpapers update` | Explicit update repairs approved managed artifacts without touching user data or silently changing versions. |
| M3 | Real-PDF acceptance corpus | All six approved issue #59 cases pass their section-boundary and contamination assertions. |
| M4 | Documentation truth pass | Current behavior, limitations, licenses, and commands are accurately documented. |
| M5 | Release-readiness verification | Offline, restart, privacy, artifact, and cross-platform checks pass in reproducible environments. |

OCR, conversion beyond Abstract/Introduction, persisted retrieval indexes,
semantic causal classification, and other features excluded by Gate 0 remain
out of scope unless the maintainer explicitly changes the MVP contract.

---

## M1 — Evidence inspection

### Deliverable

A reader can go from a claim to the exact stored passage behind it, without
knowing a passage ID or opening SQLite.

- Interactive shell: `/show e1` prints that citation's stored passage and
  provenance. Bare `/show` lists the citation IDs available for the current
  evidence state.
- One-shot chat: `econpapers chat "..." --show-evidence` prints each cited
  passage beneath the citation block.
- Default output is byte-for-byte unchanged when `--show-evidence` is absent.

“Exact” means the stored passage text and its existing provenance are shown. It
does not claim that the passage semantically proves the model’s claim.

### State semantics

Evidence state is explicitly defined as follows:

- after an `answered` turn, it contains that turn’s surviving citations;
- after `withheld`, `abstained`, `no_matches`, empty-library, typed-failure, or
  internal-failure turns, it is empty;
- `/reset` clears it as well as conversation history;
- a citation ID is valid only for the current evidence state and is not a
  durable identifier across turns.

This “latest turn” rule avoids showing stale evidence after a failed or
non-answering question. The shell must never reload passages from storage at
`/show` time; it renders the immutable citation details already resolved by
`ask()`.

### Design

The passage text is already available and validated during citation resolution.
`_resolve_citations` looks up the durable passage and verifies equality with the
retrieved passage before building each `ChatCitationDetail`.

1. Add `passage_text: str` to `ChatCitationDetail`, populated from the
   validated stored passage. Do not add a new storage protocol method or a
   `/show`-time storage read.

2. Add one shared renderer,
   `format_evidence_detail(citations, *, citation_id=None)`, beside
   `_render_citation_lines`. The renderer must:

   - render the same evidence block for the shell and one-shot chat;
   - preserve passage content, including meaningful line breaks and blank lines;
   - wrap long lines without truncating text;
   - work for passages longer than the default 1,200-character budget;
   - render section, page, paper, source, rank, and score metadata; and
   - define a safe policy for terminal control characters in source text.

3. Shell state: store the latest turn’s citations on
   `InteractiveShellSession`, replacing them after every `ask()` outcome and
   clearing them on `/reset`. Dispatch `/show` beside the existing commands.
   Handle empty state, malformed requests, unknown IDs, and IDs unavailable
   because the latest turn was not answered with plain messages and keep the
   loop alive.

4. Chat: add `--show-evidence` to the chat parser, pass it through
   `ChatCommandOptions` and `run_chat`, and consume it only in output
   rendering. No retrieval, generation, prompt, or citation-validation logic
   changes.

### Files

- `src/econ_paper_cli/services/chat_command.py`
- `src/econ_paper_cli/services/interactive_shell.py`
- `src/econ_paper_cli/cli.py`
- `src/econ_paper_cli/services/commands.py`
- `tests/services/test_chat_cli_service.py`
- `tests/services/test_interactive_shell.py`
- `tests/test_cli.py`
- `README.md`, `docs/architecture.md`, and any citation-shape documentation

### Tests

- `passage_text` equals the validated stored passage text.
- Default chat output is unchanged; opt-in output contains the full passage.
- Shell and one-shot chat produce identical evidence blocks for identical
  citation details.
- `/show` and `/show e1` work after an answered turn.
- `/show` behaves correctly before any turn and after every non-answered
  outcome, including typed and internal failures.
- A later answered turn replaces earlier evidence; `/reset` clears it.
- Storage is not read after session open, including when `/show` is invoked.
- Long, multiline, Unicode, blank-line, and control-character-containing text
  follows the documented rendering policy.
- Citation IDs from a previous turn are not accepted after the state is cleared
  or replaced.

### Verification

Run the standard checks from an activated development environment:

```bash
ruff check .
ruff format --check .
pytest
```

Then run a deterministic smoke test against a temporary SQLite library and
explicit local configuration/model fixtures. The smoke test must not depend on
the developer’s home directory, an undocumented private corpus, or an
unrecorded model installation. Manually confirm `/show e1`, `/show`, an
unknown ID, `/reset`, and evidence output for a multiline passage.

### Out of scope

M2–M5. No new storage protocol method, persisted retrieval index, prompt
version, or semantic claim-verification mechanism.

---

## M2 — Real `econpapers update`

### Scope

`update` operates only on approved managed runtime/model artifacts described by
versioned manifests. It may verify and repair missing or corrupt managed
artifacts, but it must not overwrite explicitly user-supplied runtime/model
paths, source PDFs, the SQLite library, or generated user data.

The command must state whether it is repairing the currently pinned version or
installing a newer approved version. It must not use “update” to conceal a
version change.

### Required behavior

- explicit invocation is the only path that may use the network;
- `--offline` refuses a required download with a typed, actionable failure;
- existing valid artifacts are reused without downloading;
- downloads are staged, size/checksum verified, and atomically promoted;
- interrupted or failed updates leave the prior valid artifact usable;
- concurrent updates converge on one verified artifact;
- runtime and model status remain independently reportable; and
- output and exit codes distinguish reused, repaired, unavailable, and failed
  artifacts.

### Tests and documentation

Add service, adapter, CLI, corruption, interruption, offline, and concurrency
tests using injected downloaders and temporary directories. Document every
downloaded artifact’s source, license, redistribution status, expected size,
checksum, update policy, and whether it contains copyrighted full text.

Acceptance requires a clean temporary install in which `status` reports the
repaired artifact and a second `update` performs no network operation.

---

## M3 — Real-PDF acceptance corpus

The issue #59 harness currently defines six exact private PDF cases (`case_a`
through `case_f`), not five. The cases cover the approved JUE, JEG, AER, Wiley,
and Taylor & Francis layouts plus the additional approved case recorded in the
harness.

Acceptance requires:

- resolving all six exact filenames, with no globbing or duplicate substitution;
- passing every expected Abstract and Introduction detection method;
- passing expected headings and boundary evidence;
- excluding every listed metadata, furniture, affiliation, and footnote string;
- preserving the existing source PDFs and private-corpus policy; and
- reporting every case failure rather than stopping at the first one.

Run the opt-in harness with its documented private-corpus environment variables
and record the exact corpus revision/checksums privately. Do not commit or
redistribute the PDFs. Keep ordinary CI on synthetic fixtures and unit tests;
the private acceptance run must be an explicit release check.

---

## M4 — Documentation truth pass

Reconcile the status and requirements documents after Gate 0 and the completed
milestones. At minimum, audit:

- supported corpus scope: Abstract/Introduction versus full-document ingestion;
- whether a persisted or bundled retrieval index exists;
- setup, model, and runtime download behavior;
- default-model status and artifact licenses;
- follow-up behavior and `/reset` semantics;
- evidence-inspection syntax and output;
- offline, privacy, restart, and cross-platform guarantees; and
- the distinction between structural grounding checks and semantic truth.

Documentation must describe current behavior, not planned behavior. Add a
reproducible quickstart that identifies the required local artifacts and
temporary/configurable library paths.

---

## M5 — Release-readiness verification

This milestone is a verification matrix, not a single local smoke test.

### Required scenarios

- clean setup using temporary configuration and library directories;
- chat, shell, analyze, and status with network access unavailable after
  artifacts are installed;
- restart after analysis and confirmation that stored passages and citations
  are identical;
- corrupt or partially present runtime/model artifacts;
- interrupted update/download with preservation of the previous valid install;
- concurrent read-only shell use while analysis/update is attempted;
- no query, PDF, answer, citation, or index upload by default; and
- Windows, macOS, and Linux path, subprocess, line-ending, and exit-code checks.

Use injected storage/config/download backends in unit tests and a CI matrix for
platform coverage. The final release check must report exact Python versions,
artifact manifests/checksums, test commands, private-corpus status, and any
known platform limitations.

### Exit condition

MVP is complete only when Gate 0 is resolved, M1–M5 acceptance criteria pass,
documentation is current, artifact licenses and release procedure are recorded,
and the final diff contains no unrelated changes.
