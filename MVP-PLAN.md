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
un-actioned gap rather than silently dropped, and is reasonable follow-up
scope for M2 or M4.

## Milestone ladder

| # | Milestone | Exit condition |
| --- | --- | --- |
| Gate 0 | Reconcile MVP contract | 2 of 4 items resolved (model download/default, 2026-08-08); index-scope and causal-classification-guarantee questions remain open — maintainer's call. |
| M1 | Evidence inspection ✅ done | A user can inspect the full stored passage for a citation in both CLI surfaces. |
| M2 | Real `econpapers update` | Explicit update repairs approved managed artifacts without touching user data or silently changing versions. **In progress — see design below.** |
| M3 | Real-PDF acceptance corpus | All six approved issue #59 cases pass their section-boundary and contamination assertions. |
| M4 | Documentation truth pass | Mostly done as a side effect of M1's review cycle; a few items remain — see M4 section. |
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

## M2 — Real `econpapers update`

### Scope

`update` operates only on approved managed runtime/model artifacts described by
versioned manifests. It may verify and repair missing or corrupt managed
artifacts, but it must not overwrite explicitly user-supplied runtime/model
paths, source PDFs, the SQLite library, or generated user data.

The command must state whether it is repairing the currently pinned version or
installing a newer approved version. It must not use "update" to conceal a
version change.

### Design: almost entirely reuse, not new machinery

`econpapers setup` already implements everything M2 needs at the
verify/repair/atomic-promote layer:

- `services/runtime_provisioning.ensure_managed_runtime(*, runtime_dir,
  downloader, extractor, allow_download, ...)` — reuse-if-functional via
  `verify_managed_install` (receipt + every declared-member checksum), else
  stage into a sibling temp dir, download, verify, and `os.replace` onto a
  content-addressed path. A lost promotion race adopts the winner instead of
  failing (`runtime_provisioning.py:265`-ish `_reuse_if_functional` re-check
  after `OSError`). This *is* "interrupted/failed updates leave the prior
  valid artifact usable" and "concurrent updates converge on one verified
  artifact" — no new code needed for either requirement.
- `services/model_provisioning.ensure_managed_model(*, model_dir,
  downloader, model_id, allow_download, ...)` — same shape, single file
  (`model_dir / artifact.filename`), no archive/extraction step.
- `adapters/runtime_downloader.UrllibDownloader` /
  `adapters/runtime_extractor.SafeArchiveExtractor` — HTTPS-only, bounded
  redirects, incremental size cap, partial-file cleanup on any failure.

M2's real work is orchestration `setup` doesn't need: deciding *which*
already-configured artifacts are safe to touch, without a fresh CLI-supplied
identity to lean on the way `setup` has.

1. **Load durable config only** (`ConfigBackend.load()` via the same
   `JSONConfigStorage`/`--config-path` pattern as `status`). `update` takes
   no per-invocation runtime/model identity flags — accepting
   `--model-path`/`--model-id`/etc. would mean "bypass managed provisioning
   for this call," which is the opposite of what a repair command does.
   **If no durable config exists, `update` does not create one** — it
   reports both artifacts `NOT_CONFIGURED` and tells the user to run
   `econpapers setup` first, exit code 1. This keeps `update` strictly a
   repair command, never a silent alternate path to initial setup.

2. **Classify runtime as managed or external before touching it**, reusing
   `runtime_provisioning.locate_managed_install_root(config.executable_path,
   get_default_runtime_dir())` (public, lexical containment — same function
   `status`'s `_classify_runtime` uses). `None` → external → skip, report
   `EXTERNAL_SKIPPED`, never call `ensure_managed_runtime`. Non-`None` →
   call `ensure_managed_runtime(allow_download=not offline)` for the
   platform's pinned artifact (there is no user-facing runtime *version*
   choice the way there is for models, so no version-selection logic is
   needed here).

3. **Classify model as managed or external the same way**, but there is no
   existing helper for this (unlike runtime, `status._classify_model` never
   distinguishes managed/external — it only checks the configured path
   against the configured checksum). Add one small, pure, testable function
   next to `ensure_managed_model` in `model_provisioning.py`:

   ```python
   def locate_managed_model_artifact(
       model_path: Path,
       model_dir: Path,
       catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
   ) -> ManagedModelArtifact | None:
       """Return the catalog artifact `model_path` is a managed install of, if any.

       Mirrors `runtime_provisioning.locate_managed_install_root`: lexical
       parent-directory containment (never resolving symlinks), so a managed
       model replaced by a symlink to an outside file is not silently
       reclassified as external and skipped.
       """
   ```

   A path counts as managed only if it is lexically `model_dir /
   <some catalog artifact's filename>` **and** `config.model_id` matches
   that same artifact's `model_id` (mirroring `_classify_runtime`'s
   belt-and-suspenders receipt-*and*-config-identity check) — this is what
   stops `update` from ever switching a user from the 1.5B to the 7B or vice
   versa: it repairs whichever `model_id` durable config already names, it
   never defaults to the catalog default. If either check fails, treat as
   external → skip, report `EXTERNAL_SKIPPED`. Otherwise call
   `ensure_managed_model(model_id=config.model_id, allow_download=not offline)`.

4. **Outcome enum**, one per artifact (matches the plan's "reused, repaired,
   unavailable, and failed" requirement plus the not-configured/external
   cases above):

   ```python
   class UpdateArtifactOutcome(str, Enum):
       REUSED = "reused"  # already valid, no download
       REPAIRED = "repaired"  # was missing/corrupt, now verified
       EXTERNAL_SKIPPED = "external_skipped"  # user-supplied, not touched
       NOT_CONFIGURED = "not_configured"  # no durable config at all
       UNAVAILABLE_OFFLINE = "unavailable_offline"  # needed download, --offline set
       FAILED = "failed"  # download/verification/IO error
   ```

   `ensure_managed_runtime`/`ensure_managed_model` don't currently report
   "did this call actually download or reuse" as a return field for the
   *runtime* side (the model side already has `ManagedModelInstall.downloaded:
   bool` — reuse that directly). For runtime, distinguish reused vs. repaired
   by checking whether `verify_managed_install` already succeeded *before*
   calling `ensure_managed_runtime` (i.e., `update` does its own cheap
   pre-check with `verify_managed_install`, catching `CorruptManagedInstallError`,
   purely to classify the outcome — `ensure_managed_runtime` itself remains
   the single source of truth for the actual repair).

5. **Exit codes**: `0` if every configured, managed artifact ends `REUSED` or
   `REPAIRED`; `1` if any is `EXTERNAL_SKIPPED`/`NOT_CONFIGURED`/
   `UNAVAILABLE_OFFLINE` and nothing failed outright (mirrors `chat`'s
   `NO_MATCHES`-is-1-not-an-error convention); `2` for a typed/config
   failure before any provisioning was attempted; `3` if any artifact
   ends `FAILED`.

6. **CLI surface**: pull `update` out of the generic `command_definitions`
   tuple in `cli.py` into its own subparser (matching how `setup` and
   `chat` are already defined explicitly), with only `--offline` and
   `--config-path` — deliberately not the five runtime/model identity flags,
   for the reason in step 1.

### Required behavior (unchanged from original scope)

- explicit invocation is the only path that may use the network;
- `--offline` refuses a required download with a typed, actionable failure —
  reported per-artifact as `UNAVAILABLE_OFFLINE`, not a hard process failure,
  since one artifact needing a network the user declined is not the same as
  a broken update;
- existing valid artifacts are reused without downloading (`REUSED`);
- downloads are staged, size/checksum verified, and atomically promoted
  (already true of `ensure_managed_runtime`/`ensure_managed_model`, reused
  as-is);
- interrupted or failed updates leave the prior valid artifact usable
  (already true, reused as-is — sibling staging dir never touches the
  existing install until full verification passes);
- concurrent updates converge on one verified artifact (already true for
  runtime via the race-adoption branch; for the model, `os.replace` onto a
  non-preexisting target is safe because colliding installs are
  checksum-identical by construction);
- runtime and model status remain independently reportable (one outcome
  per artifact, never conflated); and
- output and exit codes distinguish reused, repaired, unavailable, and failed
  artifacts (§4/§5 above; also distinguishes external-skipped and
  not-configured, which the original scope note didn't anticipate but the
  design requires to satisfy "must not overwrite explicitly user-supplied
  runtime/model paths").

### Files

- `src/econ_paper_cli/services/update_command.py` (new) — orchestration,
  outcome enum, `execute_update_command`/`run_update_command`, output
  rendering. Mirrors `status_command.py`'s shape (read config, classify,
  report) more than `setup_command.py`'s (which mutates config).
- `src/econ_paper_cli/services/model_provisioning.py` — add
  `locate_managed_model_artifact` (pure, no I/O beyond what callers already
  do).
- `src/econ_paper_cli/services/commands.py` — replace the placeholder
  `run_update` with a thin CLI-args-to-options adapter calling
  `run_update_command`, matching `run_status`/`run_chat`'s existing shape.
- `src/econ_paper_cli/cli.py` — dedicated `update` subparser.
- `tests/services/test_update_command.py` (new).
- `tests/services/test_model_provisioning.py` — tests for
  `locate_managed_model_artifact`.
- `tests/test_cli.py` — parser/dispatch tests for the new flags.
- `README.md`, `docs/roadmap.md`, `docs/managed-runtime-provisioning.md` —
  replace "`update` remains a deterministic placeholder" language.

### Tests

Reuse the exact patterns already in `tests/services/test_setup_command.py`
(fake `Downloader`/`ArchiveExtractor` implementing the Protocols directly,
`_fake_install`-style fixture builders) rather than inventing new doubles:

- no durable config → both artifacts `NOT_CONFIGURED`, exit code 1, no
  network call attempted;
- valid managed runtime + valid managed model already installed → both
  `REUSED`, zero downloader calls, exit code 0;
- corrupt/missing managed runtime or model → `REPAIRED` after a real
  `ensure_managed_runtime`/`ensure_managed_model` call with a fake
  downloader, exit code 0, and a second `update` immediately after reports
  `REUSED` with zero further downloader calls (this is the plan's stated
  acceptance test);
- externally-supplied runtime/model (`config.executable_path`/`model_path`
  outside the managed directories, or `model_id` not in the catalog) →
  `EXTERNAL_SKIPPED`, downloader never invoked for that artifact, file left
  byte-identical (hash the file before/after);
- `--offline` with something needing a download → `UNAVAILABLE_OFFLINE`,
  no network call, prior valid artifact (if any) left untouched;
- download interrupted (fake downloader raises partway) → prior valid
  install, if one existed, still verifies afterward; if none existed,
  `FAILED` and no partial file left in the managed directory;
- concurrent-update race: two `ensure_managed_runtime`/`ensure_managed_model`
  calls against the same target — this property lives in
  `runtime_provisioning.py`/`model_provisioning.py` already; confirm (don't
  re-derive) via `tests/services/test_runtime_provisioning.py` /
  `test_model_provisioning.py`, adding a same-target-race test there only if
  one doesn't already exist;
- `locate_managed_model_artifact`: matches only when both filename
  containment and `model_id` agree; a symlink at the managed path is not
  lexically escaped (mirrors the existing
  `locate_managed_install_root` symlink test on the runtime side).

### Verification

```bash
ruff check .
ruff format --check .
pytest
```

Then a manual pass against a real (throwaway) config/library directory:
`econpapers setup` (fresh install) → `econpapers update` (expect all
`REUSED`, zero network) → deliberately corrupt the installed GGUF (truncate
a byte) → `econpapers update` (expect `REPAIRED`, one download) →
`econpapers update --offline` against a corrupted install (expect
`UNAVAILABLE_OFFLINE`, no download, clear message).

### Out of scope

Re-analyzing the library against a newer section-detection/generation
policy version (that's a different concern — reusing existing analysis
records under a fingerprint — and not part of this plan). Adding an update
*policy* field to the artifact manifests (tracked separately as the known
gap noted under Gate 0 above).

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
