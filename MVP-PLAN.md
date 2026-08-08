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

This gate must be resolved before calling M1–M5 "MVP complete." The repository
contained conflicting statements about:

- whether MVP includes a bundled metadata/retrieval index or a user-provided
  local early-section library — **still open**;
- whether descriptive-versus-causal classification is an MVP guarantee —
  **still open**;
- whether setup may automatically download model artifacts — **resolved
  2026-08-08**: yes, approved by the maintainer; see `AGENTS.md`'s "Approved
  decisions"; and
- whether the default model is selected or still deferred — **resolved
  2026-08-08**: Qwen2.5 1.5B Instruct (default) / 7B Instruct (opt-in),
  approved alongside the download decision above (the two were the same
  maintainer decision). `docs/roadmap.md` §7's "deferred" language describes
  the earlier, separate Issue 13 benchmark and is explicitly marked
  historical, not current status.

The two resolved items are reflected in `README.md`,
`docs/product-requirements.md`, `docs/managed-runtime-provisioning.md`,
`docs/generation-contract.md`, `docs/local-generation-evaluation.md`, and
`docs/roadmap.md`. The two still-open items remain the maintainer's call;
until resolved, the plan treats the implemented product scope as: local
user-provided PDFs, stored Abstract/Introduction passages, local BM25
retrieval, local generation with a managed default model, inspectable
evidence, and no persisted retrieval index. It does not silently redefine
the README MVP requirements beyond what's now approved.

**Known, non-blocking gap from the approval above:** `ManagedModelArtifact`
(`domain/model_manifest.py`) does not conform to, or reuse,
`domain.ArtifactManifest` (schema version 1 — see
[`artifact-manifest.md`](artifact-manifest.md)), the artifact-metadata
contract this project already built for exactly this purpose. It has no
`redistribution_status`, `update_policy`, or `contains_copyrighted_full_text`
field, even though it does carry `license_name`, `sha256`,
`size_bytes`/`expected_size_bytes`, and a source URL. The maintainer chose to
approve download/default-model behavior as implemented rather than gate that
approval on adding these fields first; this is recorded as a real,
un-actioned gap rather than silently dropped. M2 shipped without addressing
it (see its "Out of scope" note) — it remains reasonable follow-up scope for
M4.

## Milestone ladder

| # | Milestone | Exit condition |
| --- | --- | --- |
| Gate 0 | Reconcile MVP contract | 2 of 4 items resolved (model download/default, 2026-08-08); index-scope and causal-classification-guarantee questions remain open — maintainer's call. |
| M1 | Evidence inspection ✅ done | A user can inspect the full stored passage for a citation in both CLI surfaces. |
| M2 | Real `econpapers update` ✅ done | Explicit update repairs approved managed artifacts without touching user data or silently changing versions. |
| M3 | Real-PDF acceptance corpus ✅ done | All six approved issue #59 cases pass their section-boundary and contamination assertions. |
| M4 | Documentation truth pass | Mostly done as a side effect of M1's review cycle; a few items remain — see M4 section. **Up next — see below.** |
| M5 | Release-readiness verification | Offline, restart, privacy, artifact, and cross-platform checks pass in reproducible environments. |

OCR, conversion beyond Abstract/Introduction, persisted retrieval indexes,
semantic causal classification, and other features excluded by Gate 0 remain
out of scope unless the maintainer explicitly changes the MVP contract.

---

## M1 — Evidence inspection ✅ done

Shipped: `/show`/`/show ID` in the shell, `--show-evidence` on one-shot
`chat`, one shared renderer (`format_evidence_detail` in
`chat_command.py`), reusing the passage text `_resolve_citations` already
validates rather than a new storage read. Evidence state tracks only the
latest turn — cleared on any non-`answered` outcome and on `/reset` — and
`/show` never re-reads storage. Passage text is shown in full but *safely
normalized*, not byte-identical (CRLF→LF, control characters replaced,
long lines wrapped).

Landed in `b71ae00`–`9b2e0fa`, reviewed adversarially three times (codex,
then two independent passes) — findings included a blocking CRLF-rendering
bug, unsanitized default citation output, and stale `finding_kinds` after
claim withholding, all fixed with regression tests. Also folded in and
reconciled docs for claim-level grounding, follow-up resolution, and
default-model provisioning — pre-existing work from an earlier session that
had never been committed. See commit messages for full detail; the
implementation-planning detail that used to live in this section (file
lists, step-by-step design, verification checklist) is gone now that the
code and its tests are the source of truth.

---

## M2 — Real `econpapers update` ✅ done

### Scope

`update` operates only on approved managed runtime/model artifacts described by
versioned manifests. It may verify and repair missing or corrupt managed
artifacts, but it must not overwrite explicitly user-supplied runtime/model
paths, source PDFs, the SQLite library, or generated user data. The command
states whether it is repairing the currently pinned version or reports a newer
approved version without installing it — never using "update" to conceal a
version change.

### Shipped

Landed in `328d25f`, with three follow-up review rounds (`9ed33fb`, `ab62e8d`,
`50d18e3`) closing gaps found after implementation. Design went through three
adversarial reviews *before* implementation as well; see git history on this
file for that record if the reasoning behind a specific guarantee below is
ever in question — it is not repeated here now that the code and its 12
tests in `tests/services/test_update_command.py` (plus the runtime/model
provisioning regression tests alongside them) are the source of truth.

Final shape:

- `services/update_command.py` — `execute_update_command`/`run_update_command`,
  the `UpdateArtifactOutcome` enum (`REUSED`, `REPAIRED`,
  `NEWER_VERSION_AVAILABLE`, `EXTERNAL_SKIPPED`, `NOT_CONFIGURED`,
  `UNAVAILABLE_OFFLINE`, `FAILED`), and report rendering. `update` is its own
  `cli.py` subparser with only `--offline`/`--config-path` — never the
  runtime/model identity flags, since accepting them would mean "bypass
  managed provisioning for this call."
- Runtime classification reuses `runtime_provisioning.classify_runtime_origin(...,
  require_declared_identity=True)`, the same chain `status` uses
  (`require_declared_identity=False`) with one extra hard gate: `update`
  treats missing `runtime_id`/`runtime_version_marker` as conclusively
  external, never falling back to path/receipt agreement alone.
- Model classification uses the schema-3 `managed_model_provisioning: bool`
  field (`domain/local_config.py`, `LOCAL_CONFIG_SCHEMA_VERSION = 3`, schema
  1/2 files transparently upgrade on next `setup` write) plus lexical
  containment (`model_provisioning.is_model_path_contained_in_dir`) as the
  MANAGED/EXTERNAL gate. Catalog filename/`model_id`/size/checksum agreement
  is deferred to the identity-comparison step, not the gate itself — the
  "renamed-pin exception" this document records so a future catalog filename
  rename reports `NEWER_VERSION_AVAILABLE` rather than misclassifying a
  still-genuinely-managed install as external.
- A version mismatch against the artifact the code's *current* pinned
  manifest names is detected and reported as `NEWER_VERSION_AVAILABLE`
  without calling `ensure_managed_*` at all — nothing is installed, so
  there is nothing to persist back to config. M2 v1 does not auto-adopt a
  newer pin.
- Two upstream safety fixes landed underneath this, both reused by `setup`
  too: `ensure_managed_model` no longer deletes the existing file before
  staging its replacement (`os.replace` overwrites atomically either way);
  `ensure_managed_runtime`'s pre-restage `shutil.rmtree` tolerates a peer
  process having already removed the same corrupt install, and re-checks for
  a peer's *valid* install immediately before deleting.

### Known limitation carried forward

Runtime-side concurrent repair has no single-call or bounded-retry
convergence guarantee — a directory cannot be atomically replaced onto a
non-empty destination the way a single file can via `os.replace`. The
model side has no equivalent gap (fixed by the unlink removal above). A
full fix (per-target interprocess lock, rename-to-quarantine, or a
symlink-indirection install layout) is a real cross-platform design change,
deliberately out of scope for M2 v1, and would touch
`locate_managed_install_root` and every `status`/`update` classification
check if ever taken up.

Also out of scope, tracked separately: adding `redistribution_status`/
`update_policy`/`contains_copyrighted_full_text` to `ManagedModelArtifact`
(the Gate 0 known gap above); actually installing a newer pinned version
once `NEWER_VERSION_AVAILABLE` is detected (report-only in v1); re-analyzing
the library against a newer section-detection/generation policy version (a
different, unrelated concern).

---

## M3 — Real-PDF acceptance corpus ✅ done

The issue #59 harness defines six exact private PDF cases (`case_a` through
`case_f`), not five. The cases cover the approved JUE, JEG, AER, Wiley, and
Taylor & Francis layouts plus the additional approved case recorded in the
harness (`tests/evaluation/test_pdf_acceptance_harness.py`).

Acceptance required:

- resolving all six exact filenames, with no globbing or duplicate substitution;
- passing every expected Abstract and Introduction detection method;
- passing expected headings and boundary evidence;
- excluding every listed metadata, furniture, affiliation, and footnote string;
- preserving the existing source PDFs and private-corpus policy; and
- reporting every case failure rather than stopping at the first one.

### Run record

Executed 2026-08-08 against the local private corpus:

```bash
ECONPAPERS_TEST_ACCEPTANCE_DIR="$(pwd)" \
ECONPAPERS_ACCEPTANCE_PAPER_DIR="$(pwd)/papers" \
python -m pytest tests/evaluation/test_pdf_acceptance_harness.py -m real_pdf -v
```

Result: `test_pdf_acceptance_harness_opt_in` passed — all six cases matched
their expected detection method, heading text, boundary evidence, disjoint
span reconstruction, Introduction termination point, and forbidden-substring
exclusions, then round-tripped through SQLite close/reopen, BM25 retrieval,
grounded generation/citation validation, and production candidate reuse.
Exact filenames, sha256 checksums, and file sizes are recorded privately in
`papers/ACCEPTANCE_CORPUS_RECORD.md` (gitignored, not committed or
redistributed — matches the existing `/papers/` private-corpus policy).
Ordinary CI is unaffected: it still runs only
`test_acceptance_harness_orchestration_runs_in_ordinary_ci` and the other
synthetic-fixture contract tests in the same file; the real-corpus case
remains gated on `ECONPAPERS_TEST_ACCEPTANCE_DIR` and skips deterministically
without it.

---

## M4 — Documentation truth pass

Largely done as a side effect of the M1 review cycle, not as a standalone
pass: **setup/model/runtime download behavior**, **default-model status**,
**follow-up behavior and `/reset` semantics**, and **evidence-inspection
syntax and output** are all now current across README/AGENTS.md/
docs/product-requirements.md/docs/roadmap.md/docs/generation-contract.md/
docs/managed-runtime-provisioning.md/docs/local-generation-evaluation.md
(see commits `583eb1b`, `8f2f90c`, `9b2e0fa`).

Still outstanding:

- supported corpus scope: Abstract/Introduction versus full-document
  ingestion — accurate in places, not audited end-to-end;
- whether a persisted or bundled retrieval index exists — mentioned
  correctly in a few places, not swept for every stale reference the way
  the default-model status was;
- artifact licenses beyond the model/runtime manifests already documented;
- the distinction between structural grounding checks and semantic truth —
  partially covered by the `finding_kinds`/withholding notes added during
  M1, not a dedicated pass;
- offline, privacy, restart, and cross-platform guarantees — this is M5's
  job, not a doc-pass item;
- a reproducible quickstart identifying required local artifacts and
  temporary/configurable library paths — not started.

Documentation must describe current behavior, not planned behavior.

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
