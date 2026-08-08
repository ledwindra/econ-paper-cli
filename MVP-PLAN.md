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

### Revision note

An adversarial review of the first draft of this section found four real
gaps, all now folded into the design below rather than listed separately:
(1) classifying an artifact as "managed" from path containment alone is not
sufficient — a user path can lexically live under the managed directory
without being a genuine managed install, and the design needs the same
full identity check `status`'s classifier already does, not just the
containment half of it; (2) the original draft never specified persisting
an updated identity back to durable config, so a version change could
report success while `chat`/`status` kept using the stale path; (3)
`ensure_managed_model` deletes the existing file before staging its
replacement, which is an unnecessary, removable safety gap — `os.replace`
can overwrite an existing file directly, so nothing needs deleting first;
(4) the versioning-transition requirement doesn't actually need the
`update_policy` metadata field this section already excludes — it needs a
structural comparison against the manifest, addressed by narrowing scope
below instead of by adding metadata.

### Design: mostly reuse, with one small upstream fix

`econpapers setup` already implements most of what M2 needs at the
verify/repair/atomic-promote layer:

- `services/runtime_provisioning.ensure_managed_runtime(*, runtime_dir,
  downloader, extractor, allow_download, ...)` — reuse-if-functional via
  `verify_managed_install` (receipt + every declared-member checksum), else
  stage into a sibling temp dir, download, verify, and `os.replace` onto a
  content-addressed path. A lost promotion race adopts the winner instead of
  failing (`_reuse_if_functional` re-check after `OSError`). This *is*
  "concurrent updates converge on one verified artifact" for the runtime
  side — no new code needed there.
- `services/model_provisioning.ensure_managed_model(*, model_dir,
  downloader, model_id, allow_download, ...)` — same shape, single file
  (`model_dir / artifact.filename`), no archive/extraction step. **Needs one
  fix before M2 can honestly claim its own "interrupted updates leave the
  prior valid artifact usable" requirement — see step 0 below.**
- `adapters/runtime_downloader.UrllibDownloader` /
  `adapters/runtime_extractor.SafeArchiveExtractor` — HTTPS-only, bounded
  redirects, incremental size cap, partial-file cleanup on any failure.

0. **Fix `ensure_managed_model`'s premature delete** (`model_provisioning.py`,
   the `final_path.unlink()` call before staging begins). Today: if the
   existing file fails verification, it is deleted *before* the replacement
   is downloaded and verified; if the download or staged verification then
   fails, `final_path` is left with nothing at all, and two concurrent
   repairs race on the `unlink()` call itself. Neither is necessary:
   `os.replace(staged_path, final_path)` — the existing promotion step —
   already atomically overwrites `final_path` whether or not something is
   there, on every platform this project supports. Remove the `unlink()`
   call entirely; the corrupt/missing-vs-present branch collapses to "stage,
   download, verify, then `os.replace` regardless." This is a small,
   self-contained fix to existing shared code (also strictly improves
   `setup`'s safety, not just `update`'s), not new M2-specific logic, and
   should land and be tested on its own before the orchestration below is
   built on top of it. The runtime side's analogous `shutil.rmtree` before
   restaging a corrupt *directory* is not similarly removable — a directory
   generally cannot be atomically replaced onto a non-empty existing
   directory the way a single file can via `os.replace`, especially on
   Windows — so the runtime side keeps a narrower version of this gap:
   "prior valid artifact preserved through a failed repair" holds for the
   *"was already valid, never touched"* case (always true) but not for the
   *"was already corrupt, repair itself then fails"* case (runtime: still
   loses the corrupt directory; model: fixed by step 0 above). Document this
   asymmetry explicitly in the required-behavior list below rather than
   claiming a blanket guarantee the runtime side can't actually provide.

M2's own orchestration work — the part `setup` doesn't need — is deciding
*which* already-configured artifacts are safe to touch, without a fresh
CLI-supplied identity to lean on the way `setup` has:

1. **Load durable config only** (`ConfigBackend.load()` via the same
   `JSONConfigStorage`/`--config-path` pattern as `status`). `update` takes
   no per-invocation runtime/model identity flags — accepting
   `--model-path`/`--model-id`/etc. would mean "bypass managed provisioning
   for this call," which is the opposite of what a repair command does.
   **If no durable config exists, `update` does not create one** — it
   reports both artifacts `NOT_CONFIGURED` and tells the user to run
   `econpapers setup` first, exit code 1. This keeps `update` strictly a
   repair command, never a silent alternate path to initial setup.

2. **Classify runtime as managed or external using the full chain, not
   containment alone.** `locate_managed_install_root` checks lexical path
   containment *only* — its own docstring says so explicitly and requires
   callers to combine it with receipt verification. A user-supplied
   `--llama-cpp-path` that happens to lexically resolve under the managed
   runtime directory must not be treated as repair-eligible just because
   `locate_managed_install_root` returns non-`None`. Reuse the *same full
   chain* `status`'s `_classify_runtime` already implements (as a
   standalone helper both can call, not by duplicating the logic inline):
   locate the candidate root, then `verify_managed_install` against the
   platform's pinned artifact, then confirm the receipt's executable path
   resolves back to `config.executable_path` *and* (when set)
   `config.runtime_id`/`config.runtime_version_marker` agree with the
   receipt. Only if every one of those checks passes is the runtime
   `MANAGED` and eligible for `ensure_managed_runtime(allow_download=not
   offline)`. Any failure in that chain — including "lexically contained
   but receipt doesn't validate" — reports `EXTERNAL_SKIPPED`, the same as
   genuinely-external, and `update` does **not** hand it to
   `ensure_managed_runtime` (which would otherwise "repair" a directory
   that was never actually its install by deleting and replacing it).
   Extract this chain from `status_command._classify_runtime` into a
   shared, testable function (e.g. `runtime_provisioning.classify_runtime_origin`)
   that both `status` and `update` call, rather than inlining a second copy
   of the same five-check sequence.

3. **Classify model the same way — containment *and* identity, not
   either alone.** There is no existing helper for this (unlike runtime,
   `status._classify_model` never distinguishes managed/external at all —
   it only checks the configured path against the configured checksum). Add
   one small, pure, testable function next to `ensure_managed_model` in
   `model_provisioning.py`:

   ```python
   def locate_managed_model_artifact(
       model_path: Path,
       model_dir: Path,
       catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
   ) -> ManagedModelArtifact | None:
       """Return the catalog artifact `model_path` is lexically a managed
       install location for, if any -- filename/containment only, mirroring
       `locate_managed_install_root`. Callers must additionally confirm
       `config.model_id` matches the returned artifact's `model_id` before
       treating the path as genuinely managed; this function alone is not
       sufficient, exactly as `locate_managed_install_root` alone is not
       sufficient for the runtime side (see step 2).
       """
   ```

   The caller (`update`'s orchestration, not this helper) then requires
   **both**: `locate_managed_model_artifact(...)` returns a non-`None`
   artifact, **and** `config.model_id == artifact.model_id`. Only then is
   the model `MANAGED` and eligible for
   `ensure_managed_model(model_id=config.model_id, allow_download=not
   offline)` — repairing whichever `model_id` durable config already names,
   never defaulting to the catalog default, so `update` can never switch a
   user from the 1.5B to the 7B or vice versa. Either check failing reports
   `EXTERNAL_SKIPPED`.

4. **Persist the result when the identity actually changes** — this is the
   piece the first draft omitted entirely. After a `REPAIRED` outcome,
   compare the `ensure_managed_*` result's identity against what durable
   config currently holds:
   - **Runtime:** if `install.executable_path`/`install.runtime_id`/
     `install.version_marker` are unchanged from `config.executable_path`/
     `config.runtime_id`/`config.runtime_version_marker`, this was a
     same-pin repair — no config write needed.
   - **Model:** if `install.model_path`/`install.sha256` are unchanged from
     `config.model_path`/`config.model_checksum`, likewise no write needed.
   - If either differs, this can only happen because the code's own pinned
     manifest (`MANAGED_RUNTIME_MANIFEST`/`MANAGED_MODEL_CATALOG`) now names
     a different pinned identity for that `model_id`/platform than what was
     durably configured — i.e. the *package* was upgraded to a version that
     pins something new, not something `update` itself decided to change.
     **M2 v1 does not auto-adopt this newer pin.** `ensure_managed_runtime`/
     `ensure_managed_model` always operate against the *current* manifest
     (there is no "install exactly this older pin" mode), so detect the
     mismatch *before* calling them — compare the manifest's currently
     pinned identity for `config.model_id`/the platform against what
     `config` already holds — and if they differ, report a new outcome,
     `NEWER_VERSION_AVAILABLE`, without calling `ensure_managed_*` at all
     for that artifact (so the old install is left completely untouched).
     This satisfies "must state whether it is repairing the currently
     pinned version or installing a newer approved version" structurally,
     from the manifest itself, with no `update_policy` metadata field
     needed — and it resolves both the persistence gap (nothing to persist,
     because nothing was installed) and the versioning-vs-out-of-scope
     tension (out-of-scope stays out-of-scope) in one narrower cut. A future
     issue can add an explicit `--apply-newer-version`/similar opt-in once
     config-persistence-on-upgrade is designed; M2 only needs to *detect and
     report* the mismatch, not resolve it.

5. **Outcome enum**, one per artifact:

   ```python
   class UpdateArtifactOutcome(str, Enum):
       REUSED = "reused"  # already valid, no download
       REPAIRED = "repaired"  # was missing/corrupt, now verified, same pin
       NEWER_VERSION_AVAILABLE = (
           "newer_version_available"  # manifest has moved on; not auto-installed
       )
       EXTERNAL_SKIPPED = "external_skipped"  # user-supplied, or managed-looking but fails identity check; not touched
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

6. **Exit codes**: `0` if every configured, managed artifact ends `REUSED` or
   `REPAIRED`; `1` if any is `EXTERNAL_SKIPPED`/`NOT_CONFIGURED`/
   `UNAVAILABLE_OFFLINE`/`NEWER_VERSION_AVAILABLE` and nothing failed
   outright (mirrors `chat`'s `NO_MATCHES`-is-1-not-an-error convention);
   `2` for a typed/config failure before any provisioning was attempted;
   `3` if any artifact ends `FAILED`.

7. **CLI surface**: pull `update` out of the generic `command_definitions`
   tuple in `cli.py` into its own subparser (matching how `setup` and
   `chat` are already defined explicitly), with only `--offline` and
   `--config-path` — deliberately not the five runtime/model identity flags,
   for the reason in step 1.

### Required behavior

- explicit invocation is the only path that may use the network;
- `--offline` refuses a required download with a typed, actionable failure —
  reported per-artifact as `UNAVAILABLE_OFFLINE`, not a hard process failure,
  since one artifact needing a network the user declined is not the same as
  a broken update;
- existing valid artifacts are reused without downloading (`REUSED`);
- downloads are staged, size/checksum verified, and atomically promoted
  (true of both `ensure_managed_runtime`/`ensure_managed_model` after step 0's
  fix);
- **a currently-valid artifact is never touched by a failed repair attempt
  (always true — nothing is deleted until a replacement verifies); an
  already-corrupt artifact whose repair attempt itself then fails may still
  end up with nothing in its place on the runtime side (directory-replace
  constraint, not fixable within M2) but not on the model side (fixed by
  step 0)** — this replaces the original draft's blanket, inaccurate claim
  that interrupted repairs always preserve the prior artifact;
- concurrent updates converge on one verified artifact (true for runtime via
  the race-adoption branch; true for the model once step 0 removes the
  unlink-based race entirely — two concurrent `os.replace` calls with
  checksum-identical staged content are safe in either order);
- a mismatch between durable config and the artifact the code's current
  manifest pins is detected and reported (`NEWER_VERSION_AVAILABLE`) rather
  than silently repaired-to-old or silently upgraded;
- runtime and model status remain independently reportable (one outcome
  per artifact, never conflated); and
- output and exit codes distinguish reused, repaired, newer-available,
  unavailable, external, not-configured, and failed artifacts.

### Files

- `src/econ_paper_cli/services/model_provisioning.py` — remove the
  premature `final_path.unlink()` (step 0); add `locate_managed_model_artifact`
  (step 3).
- `src/econ_paper_cli/services/runtime_provisioning.py` — extract
  `status_command._classify_runtime`'s full managed-vs-external chain into
  a shared, testable function (step 2); `status_command.py` switches to
  calling it too, so the logic has one owner.
- `src/econ_paper_cli/services/update_command.py` (new) — orchestration,
  outcome enum, `execute_update_command`/`run_update_command`, output
  rendering. Mirrors `status_command.py`'s shape (read config, classify,
  report) more than `setup_command.py`'s (which mutates config) — except
  for the conditional config write in step 4.
- `src/econ_paper_cli/services/commands.py` — replace the placeholder
  `run_update` with a thin CLI-args-to-options adapter calling
  `run_update_command`, matching `run_status`/`run_chat`'s existing shape.
- `src/econ_paper_cli/cli.py` — dedicated `update` subparser.
- `tests/services/test_update_command.py` (new).
- `tests/services/test_model_provisioning.py` — tests for the unlink
  removal (step 0) and `locate_managed_model_artifact` (step 3).
- `tests/services/test_runtime_provisioning.py` / `test_status_command.py` —
  tests for the extracted classifier function (step 2).
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
- a path that is lexically under the managed directory but fails receipt/
  identity verification (a user file coincidentally placed there, or a
  managed-shaped filename with a different `model_id`) → `EXTERNAL_SKIPPED`,
  `ensure_managed_*` never called, file left byte-identical — this is the
  regression test for finding 1, and must exist for both runtime and model;
- genuinely externally-supplied runtime/model (`config.executable_path`/
  `model_path` outside the managed directories) → `EXTERNAL_SKIPPED`,
  downloader never invoked, file left byte-identical;
- durable config names a `model_id`/platform whose manifest-pinned identity
  (sha256/version_marker) no longer matches what's configured →
  `NEWER_VERSION_AVAILABLE`, `ensure_managed_*` never called, config
  unchanged, exit code 1 — regression test for finding 2/4;
- `ensure_managed_model` repair-of-corrupt with a downloader that fails
  partway through the *replacement* download: `final_path` still exists
  (not deleted before the replacement verified) and still fails the same
  verification it failed before — never silently absent — regression test
  for finding 3, added to `test_model_provisioning.py` directly against
  `ensure_managed_model`, not just through `update`;
- `--offline` with something needing a download → `UNAVAILABLE_OFFLINE`,
  no network call, prior valid artifact (if any) left untouched;
- concurrent-update race: two `ensure_managed_runtime`/`ensure_managed_model`
  calls against the same target — this property lives in
  `runtime_provisioning.py`/`model_provisioning.py`; confirm via
  `tests/services/test_runtime_provisioning.py` /
  `test_model_provisioning.py`, adding a same-target-race test for the
  model side specifically (post-step-0, since pre-step-0 the race was on
  `unlink()`, not `os.replace()`);
- `locate_managed_model_artifact`: matches only on filename containment
  (identity/`model_id` cross-check is the caller's job, tested separately
  at the orchestration level per the bullet above); a symlink at the
  managed path is not lexically escaped (mirrors the existing
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
records under a fingerprint — and not part of this plan). Adding an
`update_policy` field to the artifact manifests (tracked separately as the
known gap noted under Gate 0 above; not needed for the versioning
distinction M2 requires — see step 4). Actually installing a newer pinned
version once detected (`NEWER_VERSION_AVAILABLE` is report-only in v1;
applying it needs config-persistence-on-upgrade design work deliberately
deferred here).

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
