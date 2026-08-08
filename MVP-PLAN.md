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

A second adversarial review of that revision found three more gaps, all
verified against the actual source before being folded in below: (5) the
"full identity chain" for runtime classification still only checked
`config.runtime_id`/`config.runtime_version_marker` *when set*
(`status_command.py`'s own soft-check pattern, confirmed by reading it) —
so an explicitly-supplied `--llama-cpp-path` that happens to point at (or
under) a byte-identical, receipt-valid managed install would still pass
every check and be treated as repair-eligible, and the model side has no
comparable signal to check *at all*, soft or hard, since `model_id`/
`model_bytes`/`model_checksum` are populated identically whether `setup`'s
managed path or its explicit-four-flags path wrote them; (6) confirmed by
reading `runtime_provisioning.py`: the `shutil.rmtree(final_install_dir)`
before restaging a corrupt install wraps *any* `OSError` — including
`FileNotFoundError` from a second process racing to remove what a first
process already removed — into a hard `RuntimeInstallIOError`, so two
concurrent repairs of the *same already-corrupt* install can have one fail
outright rather than converge (the existing race-adoption logic lives only
around the later `os.replace` promotion step, never around this earlier
cleanup step); (7) the manual verification sequence corrupts the GGUF,
repairs it, and then immediately re-runs `update --offline` still expecting
`UNAVAILABLE_OFFLINE` — but the artifact is valid again after the repair,
so that step would actually report `REUSED`.

A third adversarial review of that revision found one more gap in 0b
specifically, verified against the actual `ensure_managed_runtime` control
flow: (8) catching `FileNotFoundError` around `shutil.rmtree` only protects
against a second process finding *nothing* there (a benign lost race on the
delete itself). It does not protect against a worse ordering: process A
finds the install corrupt, is pre-empted; process B also finds it corrupt,
removes it, downloads, verifies, and successfully promotes a *valid* fresh
install to `final_install_dir`; process A then resumes and calls
`shutil.rmtree(final_install_dir)` — which now finds B's good install
present, not absent, so it deletes it without raising anything. 0b's fix
does not touch this path at all, so the plan's claim that "concurrent
repairs of an already-corrupt install converge" was still false for this
specific ordering. Folded into 0b below as an added mitigation, plus a
narrowed, honest statement of what the runtime side can actually guarantee
without a directory-level atomic-replace primitive (which POSIX/Windows
rename semantics do not provide for a non-empty destination, and building
one — e.g. a symlink-indirection layer — is out of scope for M2 v1).

### Design: mostly reuse, with two small upstream fixes and one schema addition

`econpapers setup` already implements most of what M2 needs at the
verify/repair/atomic-promote layer:

- `services/runtime_provisioning.ensure_managed_runtime(*, runtime_dir,
  downloader, extractor, allow_download, ...)` — reuse-if-functional via
  `verify_managed_install` (receipt + every declared-member checksum), else
  stage into a sibling temp dir, download, verify, and `os.replace` onto a
  content-addressed path. A lost promotion race at the final `os.replace`
  adopts the winner instead of failing (`_reuse_if_functional` re-check
  after `OSError`) — but a race on the earlier `shutil.rmtree` cleanup step
  does not have the same protection; **see step 0 below.**
- `services/model_provisioning.ensure_managed_model(*, model_dir,
  downloader, model_id, allow_download, ...)` — same shape, single file
  (`model_dir / artifact.filename`), no archive/extraction step. **Needs one
  fix before M2 can honestly claim its own "interrupted updates leave the
  prior valid artifact usable" requirement — see step 0 below.**
- `adapters/runtime_downloader.UrllibDownloader` /
  `adapters/runtime_extractor.SafeArchiveExtractor` — HTTPS-only, bounded
  redirects, incremental size cap, partial-file cleanup on any failure.

0. **Two small fixes to existing shared code, landed and tested on their own
   before the orchestration below is built on top of them:**

   **0a — `ensure_managed_model`'s premature delete**
   (`model_provisioning.py`, the `final_path.unlink()` call before staging
   begins). Today: if the existing file fails verification, it is deleted
   *before* the replacement is downloaded and verified; if the download or
   staged verification then fails, `final_path` is left with nothing at
   all, and two concurrent repairs race on the `unlink()` call itself.
   Neither is necessary: `os.replace(staged_path, final_path)` — the
   existing promotion step — already atomically overwrites `final_path`
   whether or not something is there, on every platform this project
   supports. Remove the `unlink()` call entirely; the
   corrupt/missing-vs-present branch collapses to "stage, download, verify,
   then `os.replace` regardless." Also strictly improves `setup`'s safety,
   not just `update`'s.

   **0b — `ensure_managed_runtime`'s non-race-tolerant cleanup**
   (`runtime_provisioning.py`, the `shutil.rmtree(final_install_dir)` call
   before restaging a corrupt install). Confirmed by reading it: the
   `try/except OSError` around this call converts *any* removal failure —
   including `FileNotFoundError` from a second process racing to remove
   what a first process already removed — into a hard
   `RuntimeInstallIOError`, well before reaching the `os.replace`-based
   race-adoption logic that protects the later promotion step. Fix: catch
   `FileNotFoundError` specifically and treat it as success (someone else
   already removed it; proceed to staging as this process would have after
   its own successful `rmtree`), re-raising only for other `OSError`
   subtypes (permission errors, etc., which are genuine failures). This
   does **not** make directory replacement itself atomic the way a single
   file's `os.replace` is — a directory generally cannot be atomically
   replaced onto a non-empty existing directory, especially on Windows —
   it only removes the specific hard-failure-on-benign-race defect, so two
   concurrent repairs of the same corrupt install both reach staging and
   let the existing final-`os.replace` adoption logic decide the winner,
   instead of one of them erroring out first.

   **Added mitigation (finding 8): re-check immediately before deleting.**
   `FileNotFoundError`-tolerance alone does not stop this rmtree from
   deleting a directory that is *present but now valid* — a second process
   that already repaired it while this process was pre-empted between its
   own `_reuse_if_functional` check and this `rmtree` call. Immediately
   before calling `rmtree`, re-run `_reuse_if_functional` one more time; if
   it now reports a valid receipt (someone else already fixed it), skip the
   delete entirely and return that receipt instead of destroying it. This
   shrinks the exposed window from "the entire prior download/verify
   duration" down to the handful of instructions between this re-check and
   the `rmtree` call — several orders of magnitude smaller — but it is a
   mitigation, not a proof: a promotion landing in that residual sliver is
   still destroyed. Closing that residual sliver completely would require a
   directory-level atomic replace (e.g. a symlink pointing at an immutable,
   uniquely-named target directory, repointed via one atomic `os.replace`
   on the symlink itself), which is a real architectural change and is
   explicitly **out of scope for M2 v1** — see "Out of scope" below.

   Even after 0b and this mitigation, "prior valid artifact preserved
   through a failed repair" is not a single-call guarantee for the runtime
   side the way it now is for the model side (0a). What M2 v1 actually
   guarantees on the runtime side is **convergence within at most one
   additional invocation, not within a single racing call**:
   `ensure_managed_runtime` is fully idempotent and safe to re-run, so if
   one process's `rmtree` destroys another process's just-promoted valid
   install (the residual finding-8 sliver) or a *solo* (non-concurrent)
   failure lands between a successful `rmtree` and a successful
   `os.replace` (finding 3's runtime analogue — e.g. the download itself
   fails, no race involved), the affected process's own download simply
   needs to complete; if it does, the single call still ends in a valid
   install despite the near-miss. Only if that process's *own* download
   also fails does `final_install_dir` end up empty — recoverable by
   re-running `update`/`setup`, not by anything internal to this call. This
   replaces any claim of single-call, race-proof convergence on the
   runtime side with the guarantee M2 v1 can actually build and test:
   eventual convergence via retry, with the retry step being "run `update`
   again," stated explicitly in the outcome text for a `FAILED` runtime
   artifact. The model side (0a) has no equivalent residual gap: deleting
   nothing before promotion means there is no destructive step for a race
   to land in.

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

2. **Classify runtime as managed or external, requiring declared identity
   for `update` even though `status` doesn't.** `locate_managed_install_root`
   checks lexical path containment *only* — its own docstring says so
   explicitly. `status._classify_runtime` goes further (receipt
   verification, executable-path match) but still treats
   `config.runtime_id`/`config.runtime_version_marker` agreement as
   *conditional* — `if config.runtime_id is not None and ... != receipt...`
   (confirmed by reading it) — skipped entirely when those fields are
   `None`. That soft check is the right call for `status`, which is
   read-only and has nothing to lose from a generous classification. It is
   the wrong call for `update`: an explicitly-supplied `--llama-cpp-path`
   that happens to point at a byte-identical, receipt-valid managed
   install (contrived, but possible — e.g. a user manually copied a
   managed install and pointed `--llama-cpp-path` at the copy) has
   `runtime_id`/`runtime_version_marker` left `None` by `setup`'s explicit-path
   branch, and would pass every one of `status`'s checks anyway, since none
   of the *other* checks depend on those two fields being set.
   `runtime_id`/`runtime_version_marker` are the only signal anywhere in
   durable config that specifically means "this was populated by
   `ensure_managed_runtime`, not typed in by a user" (see
   `local_config.py`'s own docstring) — so `update` must not skip them.

   Extract `status`'s chain into a shared function taking a
   `require_declared_identity: bool` parameter (e.g.
   `runtime_provisioning.classify_runtime_origin(config, ...,
   require_declared_identity=False)`), rather than duplicating the
   five-check sequence a second time. `status` calls it with the parameter
   `False` (current, unchanged behavior). `update` calls it with `True`:
   when `True`, `config.runtime_id is None` or
   `config.runtime_version_marker is None` unconditionally means "not
   confidently managed," full stop, regardless of what containment/receipt
   verification would otherwise conclude — downgrading straight to
   `EXTERNAL_SKIPPED` without even running the receipt check. A real
   managed install whose config predates schema 2 (so these fields were
   never populated) is therefore treated conservatively as external by
   `update` until the user re-runs `setup` once (idempotent, reuse-if-valid,
   and upgrades the config to schema 2 with these fields populated) — an
   acceptable cost for never risking a destructive repair against
   unconfirmed identity. Only when every check passes, including the two
   hard identity fields, is the runtime `MANAGED` and eligible for
   `ensure_managed_runtime(allow_download=not offline)`.

3. **Classify model: containment and identity are not enough on their own —
   add an explicit origin marker to durable config.** Unlike runtime, there
   is *no* existing field anywhere in `LocalRuntimeModelConfig` that
   distinguishes "populated by managed provisioning" from "populated
   because the user explicitly typed `--model-id`/`--model-bytes`/
   `--model-checksum`": `setup`'s explicit-four-flags path requires the
   user to supply exactly the same three fields the managed path derives
   from the catalog, so a user who separately downloaded the identical
   catalog file themselves (same size, same checksum, same chosen
   `model_id` string) and pointed `--model-path`/`--model-id`/etc. at it
   produces a config byte-for-byte indistinguishable from a genuinely
   managed install — filename containment and `model_id` matching both
   pass, with nothing left to check. This is not a contrived edge case the
   way the runtime one is; it is the *default* shape of every explicit
   model config, managed or not. Path/identity checks alone cannot close
   this gap; only an explicit, durable, `setup`-recorded fact can.

   Add one new optional field to `LocalRuntimeModelConfig`
   (`domain/local_config.py`), bumping `LOCAL_CONFIG_SCHEMA_VERSION` from 2
   to 3 following the exact precedent already set for `runtime_id`/
   `runtime_version_marker` (new *serialized* content, so schema-2 files
   must not silently claim schema 3; schema 1 and 2 files remain fully
   readable and are transparently upgraded to 3 on next write):

   ```python
   managed_model_provisioning: bool = False
   ```

   Set to `True` only inside `setup_command.py`'s managed-model branch
   (never by the explicit-four-flags branch); defaults to `False` for
   every schema-1/2 file, including a genuinely-managed install configured
   before this field existed — same conservative "re-run `setup` once to
   upgrade" tradeoff as the runtime side's schema-2 fields.

   Add one small, pure, testable function next to `ensure_managed_model` in
   `model_provisioning.py` for the containment half:

   ```python
   def locate_managed_model_artifact(
       model_path: Path,
       model_dir: Path,
       catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
   ) -> ManagedModelArtifact | None:
       """Return the catalog artifact `model_path` is lexically a managed
       install location for, if any -- filename/containment only, mirroring
       `locate_managed_install_root`. Containment and `model_id` agreement
       are necessary but *not sufficient* to classify a model as managed --
       callers must additionally require `config.managed_model_provisioning
       is True`, the only durable signal that actually distinguishes a
       managed install from an explicit one with coincidentally identical
       identity (see step 3's design note).
       """
   ```

   `update`'s orchestration then requires **all three**:
   `locate_managed_model_artifact(...)` returns a non-`None` artifact,
   **and** `config.model_id == artifact.model_id`, **and**
   `config.managed_model_provisioning is True`. Only then is the model
   `MANAGED` and eligible for `ensure_managed_model(model_id=config.model_id,
   allow_download=not offline)` — repairing whichever `model_id` durable
   config already names, never defaulting to the catalog default, so
   `update` can never switch a user from the 1.5B to the 7B or vice versa.
   Any of the three checks failing reports `EXTERNAL_SKIPPED`.

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
  fixes);
- **a currently-valid artifact is never touched by a failed repair attempt
  against *that same target*, on either side (nothing is deleted until a
  replacement verifies).** This is *not* the same claim as "a currently-valid
  artifact is never deleted, period" — on the runtime side, finding 8 means a
  process that started its own check while the target was still corrupt can
  still delete a valid install a *different* process promoted in the
  meantime, even though neither individual repair attempt "failed" in the
  usual sense; see the retry-based convergence guarantee above. An
  already-corrupt artifact whose *solo* (non-concurrent) repair attempt
  then fails may still end up with nothing in its place on the runtime
  side (directory-replace constraint, not fixable within M2) but not on
  the model side (fixed by step 0a) — this replaces the original draft's
  blanket, inaccurate claim that interrupted repairs always preserve the
  prior artifact;
- concurrent updates converge on one verified artifact. **On the model
  side this holds within a single racing call** once step 0a removes the
  unlink-based race entirely — two concurrent `os.replace` calls with
  checksum-identical staged content are safe in either order, and nothing
  is ever deleted before a replacement is verified, so there is no window
  for one racer to destroy another's valid install. **On the runtime side
  this is a weaker, retry-based guarantee, not single-call atomicity**:
  0b plus its finding-8 mitigation (re-checking immediately before the
  pre-restage `rmtree`) shrinks the window in which one process's cleanup
  can delete a second process's just-promoted valid install, but does not
  close it, because a directory cannot be atomically replaced onto a
  non-empty existing directory the way a single file can. What holds
  instead: `ensure_managed_runtime` is idempotent, so the affected process
  converges either within its own call (if its own download then succeeds)
  or after one further `update` invocation (if it does not) — never
  requiring more than one retry, and never leaving corrupted-but-plausible
  state behind, only an honestly empty slot that the next call fills;
- **an artifact is only ever treated as managed-and-repair-eligible when
  durable config explicitly records that it was provisioned through
  managed provisioning** — `runtime_id`/`runtime_version_marker` both
  non-`None` and matching for runtime; the new `managed_model_provisioning
  is True` flag for models — never reconstructed from path containment,
  filename, or checksum/identity agreement alone, even when those happen
  to agree with a real managed install (steps 2 and 3);
- a mismatch between durable config and the artifact the code's current
  manifest pins is detected and reported (`NEWER_VERSION_AVAILABLE`) rather
  than silently repaired-to-old or silently upgraded;
- runtime and model status remain independently reportable (one outcome
  per artifact, never conflated); and
- output and exit codes distinguish reused, repaired, newer-available,
  unavailable, external, not-configured, and failed artifacts.

### Files

- `src/econ_paper_cli/services/model_provisioning.py` — remove the
  premature `final_path.unlink()` (step 0a); add `locate_managed_model_artifact`
  (step 3).
- `src/econ_paper_cli/services/runtime_provisioning.py` — make the
  pre-restage `shutil.rmtree` race-tolerant (step 0b); extract
  `status_command._classify_runtime`'s chain into a shared, testable
  function taking `require_declared_identity: bool` (step 2).
- `src/econ_paper_cli/services/status_command.py` — switch to calling the
  extracted classifier with `require_declared_identity=False` (unchanged
  behavior), so the logic has one owner instead of two copies.
- `src/econ_paper_cli/domain/local_config.py` — bump
  `LOCAL_CONFIG_SCHEMA_VERSION` to 3; add `managed_model_provisioning: bool
  = False` as a schema-3-only serialized field, following the exact
  precedent of the schema-1-to-2 `runtime_id`/`runtime_version_marker`
  addition (step 3).
- `src/econ_paper_cli/adapters/config_storage.py` — schema 3
  serialize/deserialize round-trip for the new field; confirm schema 1/2
  files still load and transparently upgrade on next write.
- `src/econ_paper_cli/services/setup_command.py` — set
  `managed_model_provisioning=True` only on the managed-model branch, never
  on the explicit-four-flags branch.
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
  removal (step 0a) and `locate_managed_model_artifact` (step 3).
- `tests/services/test_runtime_provisioning.py` — tests for the rmtree
  race fix (step 0b) and the extracted classifier's
  `require_declared_identity` parameter (step 2).
- `tests/services/test_status_command.py` — confirm `status` still behaves
  identically after switching to the shared classifier
  (`require_declared_identity=False`).
- `tests/domain/test_local_config.py` — schema 3 field validation and the
  schema 1/2 → 3 upgrade-on-write path.
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
- a path that is lexically under the managed directory and even fully
  receipt-verifies, but `config.runtime_id`/`config.runtime_version_marker`
  are `None` (an explicit `--llama-cpp-path` pointed at a byte-identical
  managed install, or a genuinely-managed pre-schema-2 config) →
  `EXTERNAL_SKIPPED`, `ensure_managed_runtime` never called, directory left
  byte-identical — the regression test for finding 5's runtime half, and
  the reason `require_declared_identity=True` must be asserted to change
  the outcome relative to `status`'s own (unchanged) classification of the
  identical config;
- a model path that lexically matches a catalog filename *and* whose
  `config.model_id` matches that artifact's `model_id`, but
  `config.managed_model_provisioning` is `False` or absent (schema 1/2) →
  `EXTERNAL_SKIPPED`, `ensure_managed_model` never called, file left
  byte-identical — the regression test for finding 5's model half; also
  test the inverse (flag `True`, everything else matching) actually
  reaches `REUSED`/`REPAIRED`, so the flag is a necessary *and* sufficient
  gate together with the other two checks, not an accidentally-always-off
  one;
- genuinely externally-supplied runtime/model (`config.executable_path`/
  `model_path` outside the managed directories entirely) → `EXTERNAL_SKIPPED`,
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
- `ensure_managed_runtime` repair-of-corrupt where `shutil.rmtree` raises
  `FileNotFoundError` (simulating a second process having already removed
  the directory): the call proceeds to staging rather than raising
  `RuntimeInstallIOError` — regression test for finding 6's fix (0b),
  added directly against `ensure_managed_runtime`;
- `ensure_managed_runtime` repair-of-corrupt where, between the initial
  `_reuse_if_functional` check and the pre-restage `rmtree`, a fake
  filesystem hook installs a *valid* directory at `final_install_dir` (not
  merely removes it): the finding-8 re-check must detect this and return
  that install without calling `rmtree` or downloading anything —
  regression test for the finding-8 mitigation, distinct from the plain
  `FileNotFoundError` case above;
- `--offline` with something needing a download → `UNAVAILABLE_OFFLINE`,
  no network call, prior valid artifact (if any) left untouched;
- concurrent repair of the *same already-corrupt* target where the
  finding-8 re-check itself loses the race (a fake hook installs a valid
  directory *after* the re-check reports corrupt but *before* `rmtree`
  runs, so the mitigation cannot see it): assert `rmtree` still deletes the
  now-valid directory without raising, `final_install_dir` is empty
  afterward, and — this is the actual guarantee, not single-call atomicity
  — either (a) letting that same `ensure_managed_runtime` call's own
  download/verify/promote complete normally leaves a valid install by the
  end of the *same* call, or (b), if that call's own download is made to
  fail too, a second, independent `ensure_managed_runtime` call against the
  same `runtime_dir` immediately afterward reports a fresh `REPAIRED`
  rather than repeating the failure — proving retry-convergence rather
  than asserting the false single-call convergence claim the second review
  round already flagged; the equivalent model-side test only needs the
  simpler "both start with nothing present" case, since step 0a removes
  the destructive step that makes the runtime case possible at all — both
  in `tests/services/test_runtime_provisioning.py` /
  `test_model_provisioning.py`;
- `locate_managed_model_artifact`: matches only on filename containment
  (identity/`model_id`/`managed_model_provisioning` checks are the
  caller's job, tested separately at the orchestration level per the
  bullets above); a symlink at the managed path is not lexically escaped
  (mirrors the existing `locate_managed_install_root` symlink test on the
  runtime side);
- schema 1 and schema 2 config files both still load correctly with
  `managed_model_provisioning` defaulting to `False`, and are transparently
  upgraded to schema 3 on the next `setup`-triggered write.

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
**re-corrupt the GGUF again** (the repair just made it valid, so this step
is required — the first draft of this sequence skipped it and expected
`UNAVAILABLE_OFFLINE` against an artifact that was, at that point, actually
valid) → `econpapers update --offline` (expect `UNAVAILABLE_OFFLINE`, no
download, clear message, artifact still corrupt afterward since offline
mode never touches it).

### Out of scope

Re-analyzing the library against a newer section-detection/generation
policy version (that's a different concern — reusing existing analysis
records under a fingerprint — and not part of this plan). Adding an
`update_policy` field to the artifact manifests (tracked separately as the
known gap noted under Gate 0 above; not needed for the versioning
distinction M2 requires — see step 4). Actually installing a newer pinned
version once detected (`NEWER_VERSION_AVAILABLE` is report-only in v1;
applying it needs config-persistence-on-upgrade design work deliberately
deferred here). A directory-level atomic-replace primitive for the managed
runtime install (e.g. a symlink-indirection layer: install each verified
runtime under an immutable, uniquely-named directory and repoint a
`current` symlink at it via one atomic `os.replace` on the symlink) — this
is the only way to close finding 8's residual race window completely
rather than shrink it; it is a real architectural change to how the
runtime install path is laid out on disk, touching `locate_managed_install_root`,
every `status`/`update` classification check, and existing installed
layouts, so it is deliberately deferred past M2 v1 rather than folded into
what was scoped as "two small upstream fixes."

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
