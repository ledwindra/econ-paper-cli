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
  local early-section library — **resolved 2026-08-08**: out of MVP. MVP scope
  is user-provided local PDFs plus the in-memory BM25 retriever rebuilt per
  run; no persisted or bundled index is built or shipped. `README.md`'s
  "bundled economics-paper metadata and retrieval index" line is *retained and
  relabeled* as post-MVP scope, not deleted;
- whether descriptive-versus-causal classification is an MVP guarantee —
  **resolved 2026-08-08**: narrowed to what is actually implemented.
  `finding_kinds` (`FindingKind`, `protocols/generation.py`) is *optional*
  response-level metadata, not a per-claim or always-present label. An
  abstaining response must carry none (`_validate_abstention_state`); an
  answered response is not required to carry any; and `chat`/the shell
  deliberately report none once any claim has been withheld
  (`chat_command.py`), because a whole-response label cannot be trusted to
  describe a surviving subset of claims — displayed answers then read
  `Finding Kinds: N/A`. The guarantee is therefore: **when present**, the
  default adapter's response-level label is model-asserted and structurally
  constrained — validated for legal values and uniqueness, and additionally
  grammar-constrained in the default `llama.cpp` adapter (a property of that
  adapter, not of every replaceable generator). Nothing verifies the label
  against the cited evidence. Semantic causal validation is post-MVP;
- whether setup may automatically download model artifacts — **resolved
  2026-08-08**: yes, approved by the maintainer; see `AGENTS.md`'s "Approved
  decisions"; and
- whether the default model is selected or still deferred — **resolved
  2026-08-08**: Qwen2.5 1.5B Instruct (default) / 7B Instruct (opt-in),
  approved alongside the download decision above (the two were the same
  maintainer decision). `docs/roadmap.md` §7's "deferred" language describes
  the earlier, separate Issue 13 benchmark and is explicitly marked
  historical, not current status.

The model-download and default-model decisions are already reflected in
`README.md`, `docs/product-requirements.md`,
`docs/managed-runtime-provisioning.md`, `docs/generation-contract.md`,
`docs/local-generation-evaluation.md`, and `docs/roadmap.md`.

The index-scope and causal-classification decisions above were **decided
before the documents reflected them** — propagating them was M4 sweep 1 and
sweep 2 respectively, including the `README.md` relabel and the
`docs/product-requirements.md`:55 / `AGENTS.md` rewording. Both sweeps have
landed (`e4b6146`, `783d6a0` and their corrections), so those documents now
state the post-decision wording and Gate 0 is closed.

With all four resolved, the MVP product scope is: local user-provided PDFs,
stored Abstract/Introduction passages, in-memory BM25 retrieval rebuilt per
run, local generation with a managed default model, inspectable evidence,
structural claim-grounding checks, and a model-asserted (unverified)
descriptive/causal label.

**Known, non-blocking gap from the approval above:** `ManagedModelArtifact`
(`domain/model_manifest.py`) does not conform to, or reuse,
`domain.ArtifactManifest` (schema version 1 — see
[`artifact-manifest.md`](artifact-manifest.md)), the artifact-metadata
contract this project already built for exactly this purpose. It has no
`redistribution_status`, `update_policy`, or `contains_copyrighted_full_text`
field, even though it does carry `license_name`, `sha256`,
`size_bytes`/`expected_size_bytes`, and a source URL.
`ManagedRuntimeArtifact` (`domain/runtime_manifest.py`) has the same three
omissions for the pinned `llama.cpp` archives. The maintainer chose to
approve download/default-model behavior as implemented rather than gate that
approval on adding these fields first; this is recorded as a real,
un-actioned gap rather than silently dropped. M2 shipped without addressing
it (see its "Out of scope" note). It is now split and owned: M4 sweep 5
documents the underlying licensing facts in prose (blocking M4), and **M6**
aligns the type itself (post-MVP, outside the completion gate).

## Milestone ladder

| # | Milestone | Exit condition |
| --- | --- | --- |
| Gate 0 | Reconcile MVP contract ✅ closed | All 4 items decided 2026-08-08. Propagation of the latter two into the documents was M4's job and landed with M4's five sweeps, so Gate 0 is now closed. |
| M1 | Evidence inspection ✅ done | A user can inspect the full stored passage for a citation in both CLI surfaces. |
| M2 | Real `econpapers update` ✅ done | Explicit update repairs approved managed artifacts without touching user data or silently changing versions. |
| M3 | Real-PDF acceptance corpus ✅ done | All six approved issue #59 cases pass their section-boundary and contamination assertions. |
| M4 | Documentation truth pass ✅ done | All five sweeps landed and passed their acceptance criteria; every audited claim carries an explicit classification. See M4's run record below. |
| M5 | Release-readiness verification | Offline, restart, privacy, artifact, and cross-platform checks pass in reproducible environments. M5 is the last milestone before MVP, **not** the sole MVP-complete gate — see M5's exit condition. |
| M6 | Artifact-metadata alignment ✅ done (post-MVP) | `ManagedModelArtifact` **and `ManagedRuntimeArtifact`** carry `redistribution_status`, `update_policy`, and `contains_copyrighted_full_text` as validated fields sharing `domain.artifacts`' vocabulary, and `docs/artifact-licensing.md` carries a generated declarations block bound to them by test. Conformance to, and reuse of, `domain.ArtifactManifest` were both **considered and rejected** with reasons recorded in M6's section — an earlier version of this row required one of them. **Outside the MVP-complete gate** — M4 sweep 5 documents these facts in prose first, which is what `AGENTS.md` licensing actually requires. |

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

## M4 — Documentation truth pass ✅ done

Partly done as a side effect of the M1 review cycle: **setup/model/runtime
download behavior**, **default-model status**, **follow-up behavior and
`/reset` semantics**, and **evidence-inspection syntax and output** were
already current across README/AGENTS.md/docs/product-requirements.md/
docs/roadmap.md/docs/generation-contract.md/
docs/managed-runtime-provisioning.md/docs/local-generation-evaluation.md
before the sweeps began (see commits `583eb1b`, `8f2f90c`, `9b2e0fa`). The
rest — the stale statements enumerated in the sweeps below — was the sweeps'
own work.

The two examples this section used to cite in the present tense are now
**[historical]** as descriptions of the plan's starting state:
`docs/local-generation-evaluation.md`'s "runtime and model installation is
manual" was scoped to Issue 13 evaluation by Sweep 3 (`56d50f2`), and
`docs/roadmap.md`'s "no update policy is documented" was retired by Sweep 5
(`72e7dd2`). Both documents are current.

### Run record

Executed 2026-08-08. Five sweeps plus seven review-driven corrections to the
sweeps, then the closeout below — all local, none pushed. Every commit after
the first in each row was raised by adversarial review, not self-caught:

| Sweep | Initial | Corrections |
| --- | --- | ---: |
| 1 — retrieval-index scope | `e4b6146` | `878cdf6`, `720aa84`, `b86e88c` (3) |
| 2 — descriptive-vs-causal | `783d6a0` | `ad4ac02`, `f933c67` (2) |
| 3 — status reconciliation | `56d50f2` | `d623e84` (1) |
| 4 — grounding scope | `0fea1f6` | none (0) |
| 5 — corpus scope and licensing | `72e7dd2` | `62f8e00` (1) |

Seven in total. `62f8e00` counts once here although it fixed three separate
P1s at the same time.

Closing out M4 and Gate 0 in this plan took further review-driven commits of
its own, starting with `1d2679e`. Those are not sweep work and are not
counted above; `git log` on this file is the complete record.

Documentation only: one source file changed and only its docstring
(`domain/runtime_manifest_data.py`). `ruff check`, `ruff format --check`, and
`pytest` (1450 passed, 1 skipped) were green on every commit — which proves
nothing about the claims themselves, since no test covers them. Each sweep's
assertions were checked by reading the code they describe.

Gate 0's propagation condition is satisfied by these sweeps, so Gate 0 is
closed.

Offline, privacy, restart, and cross-platform guarantees are M5's job, not a
doc-pass item. A reproducible quickstart landed in `200abdd`.

### Classification requirement (applies to every sweep)

Documentation must describe current behavior. Where a document states
something that is *not* current behavior, the sweep does not simply delete
it — it labels it as exactly one of:

- **`[current]`** — implemented and true of `main` today;
- **`[planned]`** — approved future scope, explicitly not implemented;
- **`[historical]`** — a record of what a past Issue found or decided, true
  as of that Issue and not a status claim about today.

A sweep is done when every claim it touched carries one of these three
classifications and no claim silently changes category. Deleting a `[planned]`
requirement is a scope change and needs maintainer approval, not a doc edit.

**Every sweep specification below is [historical].** Each describes the state
M4 found and the work it prescribed, in its original wording — including
present-tense assertions such as "`docs/roadmap.md`:26 states outright that an
update policy is not separately documented". Those described the pre-sweep
repository, not `main` today; all five sweeps have since landed. For the
resulting state, read the documents themselves.

### Sweep 1 — Retrieval index scope

Propagates Gate 0's index decision. Current behavior: in-memory BM25
(`adapters/bm25.py`) rebuilt per run; nothing persisted or bundled.

- `README.md`:60 — "a bundled economics-paper metadata and retrieval index"
  is retained under MVP but relabeled `[planned]`, post-MVP. Do **not** delete
  it and do **not** silently reword it to "rebuildable".
- `README.md`:384, :393 — "Retrieval index | Rebuildable search accelerator"
  and the rebuildable-artifact paragraph describe a design invariant that
  holds for a *future* persisted index; mark which parts are `[current]`
  (BM25 rebuild) versus `[planned]` (persisted index layer).
- `README.md`:555 — the "not yet implemented ... persisted or bundled
  retrieval index" line stays and becomes the canonical `[planned]` statement.
- `docs/product-requirements.md`:11, :152, :188, :199 — reconcile against the
  same split.

- `docs/architecture.md` and `docs/roadmap.md` carry the same
  current/historical mixture and are in scope too.

Acceptance: **repository-wide across all tracked Markdown** — every
occurrence of "index" in a retrieval sense is classified. Historical Issue
narratives may keep their original wording, but must be explicitly labeled
`[historical]` rather than left to be inferred from surrounding context. A
reader cannot conclude a bundled index ships today, nor that the idea was
dropped.

### Sweep 2 — Descriptive-versus-causal classification

Propagates Gate 0's narrowing decision.

- `docs/product-requirements.md`:55 — reword to: *when present*, the
  response-level label is model-asserted and structurally validated (legal
  enum values, no duplicates), additionally grammar-constrained in the
  default `llama.cpp` adapter, and **not** verified against the cited
  evidence.
- `AGENTS.md` "Generation requirements" — same narrowing, same wording.
- `docs/generation-contract.md` — document `FindingKind` explicitly, and say
  plainly that it is optional metadata: absent on abstention, not required on
  an answered response, and cleared by `chat`/the shell after any claim is
  withheld, surfacing as `Finding Kinds: N/A`.

Two things the wording must not do: describe the label as something every
response "carries", or attribute the grammar constraint to the generation
protocol rather than to the default adapter. A replacement adapter satisfies
the protocol without any grammar.

Acceptance: no document claims the tool *determines* whether a finding is
causal, or that the label is always present; semantic causal validation
appears only as `[planned]`.

### Sweep 3 — Status reconciliation

Stale status claims contradicted by shipped work. Not exhaustive — the sweep
must re-derive the list, but these are confirmed:

- `README.md`:220 — "Model acquisition remains manual and out of scope for
  this issue" contradicts the approved managed model provisioning and the
  new quickstart. This sentence is `[historical]` (scoped to the runtime
  issue) and must either say so or be replaced with the `[current]` behavior.
- `README.md`:554 — "validation of the ingestion pipeline against the six
  real journal layouts tracked by issue #59" is listed as not implemented;
  M3 completed it (`200abdd`, run record above).
- `docs/architecture.md`:263 — "No default generation adapter configuration
  is approved" contradicts the 2026-08-08 approval. The surrounding Issue 13
  narrative is legitimately `[historical]`; this sentence reads as a
  `[current]` status claim and must be relabeled or corrected.
- `docs/local-generation-evaluation.md`:119 — "Runtime and model installation
  is manual" contradicts managed provisioning for both artifacts. The
  document's opening historical note covers *deferred default selection*, not
  this sentence, so the sentence is not currently scoped by it. Either
  relabel it explicitly as Issue-13-evaluation-only behavior (which it
  accurately describes — evaluators did install by hand) or correct it to the
  `[current]` managed behavior. Do not leave it relying on the opening note.

Acceptance: each of the four is fixed with an explicit classification, and
`README.md`'s "Not yet implemented" list plus `docs/architecture.md`'s
per-Issue narrative are read end-to-end for the same failure mode — a
historical finding phrased as present-tense status.

### Sweep 4 — Grounding: structural versus semantic

- `docs/generation-contract.md`:108–111 — "Claim-level citation association,
  inline citation rendering, and semantic grounding evaluation remain later
  work" is stale: claim-level citations and inline rendering shipped
  (prompt `generation-v3`; the adapter derives the response citation list
  from per-claim citations). Only semantic grounding evaluation is still
  `[planned]`. Split the sentence accordingly.

The sweep must state, in one place, what grounding **does** establish:

- claim-to-citation identity: every `(citation_id, paper_id, passage_id)`
  matches a passage actually supplied to the generator
  (`validate_generation_response`);
- citation ordering follows supplied-evidence rank;
- for responses that carry per-claim attribution, on the `chat`/shell path
  only: a token-level *distinctive-term heuristic*
  (`domain/claim_grounding.py`) that flags a claim when it uses a term
  appearing in the evidence of a paper the claim does not cite, after
  excluding terms supported by the cited evidence or the question itself and
  an ignore list — and withholds that claim from the answer.

and what it explicitly **does not** establish:

- factual correctness of the claim;
- semantic entailment of the claim by the cited passage;
- causal validity of any relationship asserted;
- sufficiency of the evidence for the question asked;
- **any** leakage verdict for responses without per-claim attribution — v1/v2
  backends and injected test generators produce no verdicts and pass through
  unchanged (`chat_command.py`);
- detection of cross-paper leakage in general — paraphrase that shares no
  distinctive term is invisible to the heuristic, even under
  `generation-v3`.

Acceptance: that does/does-not pair exists verbatim in
`docs/generation-contract.md` and is linked (not restated divergently) from
`README.md` and `docs/architecture.md`; the grounding claim is scoped to
claim-bearing responses on the chat/shell path and names the heuristic; and
no document implies leakage detection is complete or applies to every
adapter.

### Sweep 5 — Corpus scope and artifact licenses

- Supported corpus scope: Abstract/Introduction versus full-document
  ingestion — accurate in places, never audited end-to-end.
- Artifact licenses beyond the model/runtime manifests already documented.
- The Gate 0 known gap (`ManagedModelArtifact` lacking
  `redistribution_status` / `update_policy` /
  `contains_copyrighted_full_text`) splits in two, because `AGENTS.md`'s
  corpus-and-licensing section requires those facts be **documented** for
  every model artifact, while conforming the *dataclass* to
  `domain.ArtifactManifest` is a schema change:
  - **the documentation half is M4 work and blocks this sweep.** Both pinned
    Qwen2.5 GGUF artifacts get source, license, redistribution status,
    expected size, checksum, update policy, and copyrighted-full-text status
    written out in prose — satisfying the `AGENTS.md` requirement even though
    the type does not carry the fields.
  - **the schema half is M6**, named below and outside the MVP-complete gate,
    matching the 2026-08-08 decision to approve download behavior rather than
    gate it on these fields.
- `ManagedRuntimeArtifact` (`domain/runtime_manifest.py`) has the identical
  gap and is in scope for the same split. `setup` downloads **four** pinned
  `llama.cpp` archives (one per supported platform/architecture —
  `services/setup_command.py`), not just the two GGUFs. Existing runtime
  documentation gives source, license, expected size, and checksum, but not
  redistribution status, update policy, or copyrighted-full-text status; the
  roadmap (`docs/roadmap.md`:26) states outright that an update policy for
  these artifacts is not separately documented. All four archives therefore
  need the three missing facts written out in prose in this sweep, and the
  roadmap's "not yet separately documented" sentence must be updated once
  they are. The schema half for this type is M6 as well.

Acceptance: no document implies full-document ingestion is supported; every
artifact the application downloads — both Qwen2.5 GGUFs and all four pinned
`llama.cpp` archives — has all seven `AGENTS.md` licensing facts documented in
prose; `docs/roadmap.md`'s "an update policy for these artifacts is not yet
separately documented" no longer holds; and the residual schema gap points at
M6 by name rather than at "later".

---

## M5 — Release-readiness verification

This milestone is a verification matrix, not a single local smoke test.

### Required scenarios

- clean setup using temporary configuration and library directories, under the
  no-network fixture strategy below;
- chat, shell, analyze, and status with network access unavailable after
  artifacts are installed — same socket-guard harness as the no-upload
  criterion below, asserting each path still succeeds;
- restart after analysis and confirmation that stored passages and citations
  are identical;
- corrupt or partially present runtime/model artifacts;
- interrupted update/download — split by artifact kind, see "Interruption
  criterion" below;
- concurrent read-only shell use while analysis/update is attempted — see
  "Concurrency criterion" below for the pass conditions;
- no query, PDF, answer, citation, or index upload by default — scope and
  test shape defined under "No-upload criterion" below; and
- Windows, macOS, and Linux path, subprocess, line-ending, and exit-code
  checks, within the coverage actually achieved — see "Cross-platform
  coverage" below.

Two rules apply across every scenario below. Dependency injection happens at
the service layer, because the public CLI entry points do not expose those
seams; a test that cannot inject must either drop to the service layer or move
to the opt-in `integration_tests/` tier, and the plan says which for each
scenario.
Platform coverage comes from the CI matrix, within the limits recorded under
"Cross-platform coverage".

### Fixture strategy for "clean setup"

No test in the default `pytest` run may download anything, and a literal
first-run `setup` fetches multi-GB artifacts. Injection is *not* available at
the public CLI boundary: `run_setup` and `run_update`
(`services/commands.py`:51, 131) inject only a config backend and otherwise
always construct the real provisioners, downloader, and extractor. Every
injection point lives one layer down, in `run_setup_command`
(`services/setup_command.py`:135-144) and `run_update_command`
(`services/update_command.py`:331-345). M5 must therefore split the scenario
into two tiers and must not describe the fake-backed tier as `econpapers
setup` coverage.

- **Tier 1 — service level, default suite, offline.** Call
  `run_setup_command`/`run_update_command` directly with injected fake
  `Downloader`/`ArchiveExtractor` (`protocols/runtime_provisioning`), an
  injected `readiness_checker`/`runtime_readiness_checker`, and temporary
  `runtime_dir`/`model_dir`/config backend. Artifacts come from *synthetic*
  manifests built inside the test: the fixture writes a few hundred bytes and
  computes their real size and SHA-256 into a `ManagedModelArtifact` /
  `ManagedRuntimeArtifact`. The real pinned manifests
  (`domain/model_manifest.py`, `domain/runtime_manifest.py`) are never used as
  download targets; they are asserted as *data* — fields present, well-formed,
  and consistent with `docs/artifact-licensing.md`. This tier covers
  everything below argparse and nothing above it.
- **Tier 1b — the argparse boundary.** A thin unit test asserts `run_setup` /
  `run_update` map a `Namespace` onto the right `SetupCommandOptions` /
  `UpdateCommandOptions` and config backend. That leaves exactly one untested
  seam — the real provisioner/downloader wiring those functions hard-code —
  and tier 2 is the only thing that covers it.
- **Tier 2 — opt-in integration, real artifacts, release time.** Real
  `econpapers setup`/`update`/`status` invocations against real local
  artifacts with `ECONPAPERS_CONFIG_DIR`/`ECONPAPERS_LIBRARY_DIR` pointed at
  temporary directories, executed as a recorded step in the release
  checklist. This is the only tier permitted to touch the network, and only
  for the artifact downloads `setup`/`update` are explicitly allowed to
  perform.

#### What "opt-in" requires mechanically

The `model` marker alone does **not** make a test opt-in. `addopts` is
`["--strict-config", "--strict-markers"]` with no marker deselection, so a
`model`-marked test placed under `tests/` runs in the default suite
(`pyproject.toml`:31-38). The existing opt-in test is skipped by two
independent mechanisms, and every tier 2 test M5 adds must reproduce both:

- **Location outside `testpaths`.** `testpaths = ["tests"]`, so
  `integration_tests/` is never collected by a bare `pytest`
  (`integration_tests/test_llama_cpp_model.py`).
- **An explicit environment gate.** The test declares
  `pytestmark = pytest.mark.model` and calls `pytest.skip(...)` when its
  required environment variables are unset, so it also skips when someone
  points pytest at the directory directly
  (`integration_tests/test_llama_cpp_model.py`:18, 24-31).

Tier 2 tests therefore live in `integration_tests/`, carry the `model` mark,
and gate on their own required variables — the artifact/config variables above,
plus `ECONPAPERS_TEST_ACCEPTANCE_DIR`/`ECONPAPERS_ACCEPTANCE_PAPER_DIR` where a
real corpus is involved. The release checklist invokes them explicitly as
`pytest integration_tests -m model` with those variables set, and records that
the bare `pytest` run in the same session did not collect them.

Tests in every tier set `ECONPAPERS_CONFIG_DIR`/`ECONPAPERS_LIBRARY_DIR`
rather than relying on defaults that resolve to the developer's real home
directory.

### Interruption criterion

The blanket "preservation of the previous valid install" wording does not hold
for both artifact kinds, and must not be asserted as if it did.

- **Model file — three distinct outcomes, not one.** The final path is
  *filename*-based, `model_dir / artifact.filename`
  (`services/model_provisioning.py`:81), not content-addressed, and a
  verified existing file returns before any staging happens (lines 83-85). So
  "the previous install survives an interrupted download" is only meaningful
  for a target that was already corrupt. Test all three:
  - **Valid installed model.** `ensure_managed_model` must return
    `downloaded=False` with **zero downloader calls** — assert the injected
    downloader was never invoked and the file is byte-identical. No interrupt
    is reachable here, because no download is attempted.
  - **Corrupt installed model, staging fails.** With a downloader that raises
    mid-transfer, and separately with staged bytes that fail
    `_verify_local_model` (`StagedModelVerificationError`), assert the
    pre-existing **corrupt** bytes at `final_path` are still present and
    unmodified, nothing was promoted, and the `.staging-` directory is gone.
    The guarantee is "no partial promotion", not "a good install survives" —
    there was no good install.
  - **Corrupt installed model, staging succeeds.** After `os.replace`
    (line 123), `final_path` must hold the **new verified** bytes; asserting
    the old bytes or the old mtime survive would be asserting a bug.

  Note for whoever implements this: the module docstring
  (`services/model_provisioning.py`:8) claims promotion onto "a
  content-addressed final path", which the code does not do — that is the
  runtime module's scheme. Correcting that docstring is a one-line fix, but
  it is a separate change from M5 and must not ride along in the release
  verification diff.
- **Runtime directory — preservation is required only for a valid install.**
  `ensure_managed_runtime` returns early via `_reuse_if_functional` when the
  existing install verifies (`services/runtime_provisioning.py`:174-177), so
  an interrupted update over a *valid* runtime must never download or delete
  anything. Assert exactly that: the injected downloader is never called and
  the install tree is unchanged.
- **Runtime directory — repair of a corrupt install has no preservation
  guarantee, and M5 must report that rather than test around it.** Repair
  `shutil.rmtree`s the corrupt target before restaging
  (`services/runtime_provisioning.py`:190-198), which is the accepted
  limitation already recorded under "Known limitation carried forward"
  (M2 above): a directory cannot be atomically replaced onto a non-empty
  destination the way `os.replace` handles a single file. M5 tests and
  documents the honest recovery behavior instead: after an interrupt during
  corrupt-install repair, (a) no partially extracted tree is ever promoted to
  the final content-addressed path, (b) the target is absent or still
  detectably corrupt — never a tree that passes verification while being
  incomplete, and (c) re-running `setup`/`update` with network access
  converges to a verified install. This limitation is named in the release
  checklist; M5 does not claim atomic runtime replacement.

### Concurrency criterion

`SessionSnapshot` promises an immutable, restart-safe snapshot, and the shell
touches storage exactly twice — once to build the snapshot at session open and
once to `close()` (`services/interactive_shell.py`:145-162, 260). M5 must prove
that with a real two-process test, not assert it.

The writer cannot be a real `econpapers analyze` subprocess in the default
suite: analyzing a *new* paper constructs a real `LlamaCppGenerator` and calls
`check_readiness()` (`services/single_paper_analysis_cli.py`:964-985), and the
CLI has no seam for injecting a fake one. The same is true of `econpapers
update` (`services/commands.py`:131-143). So the scenario splits, and neither
tier may be described as the other:

- **Tier 1 — default suite, cross-process write against an idle reader, not
  the real CLI.** The reader is the in-process shell session with an injected
  `generator_provider` and a temporary `--db-path`. The writer is a real
  `subprocess` running a small driver that writes a paper record through
  `SQLiteStorage` — the same adapter `analyze` uses — while skipping PDF
  extraction and generation. Name what this establishes precisely: the shell
  holds an open connection but **no active read transaction** between turns,
  so the writer meets no lock to contend for. The test certifies
  *cross-process compatibility with an idle read-only shell*, which is the
  state the product is actually ever in; it does not certify behavior under
  lock contention, and must not be labelled as doing so.
- **Lock contention is deliberately out of scope.** Exercising it would mean
  holding a read transaction open across turns — something the shell never
  does, since it touches storage exactly twice per session. Manufacturing that
  state would certify a configuration the product cannot reach. If the shell
  ever gains a long-lived read transaction, this scenario must be upgraded to
  a real contention test with a stated expected outcome; that dependency is
  recorded here rather than left implicit.
- **Tier 2 — opt-in integration, real CLI.** Real `econpapers analyze` and
  real `econpapers update` subprocesses against real artifacts and a real
  shell session, asserting the identical pass conditions end to end. Located
  and gated per "What 'opt-in' requires mechanically" above, and run as a
  recorded release-checklist step.

Pass conditions, required in both tiers:

- **Existing turns are byte-identical.** Capture the rendered turn output and
  the `/show` evidence output before the writer runs and again after it
  completes; both must compare equal string-for-string.
- **No mixed snapshot.** The banner's `paper_count`/`passage_count` are
  unchanged for the life of the session; a paper the writer adds is never
  retrievable or citable in that session; and every citation the session
  renders resolves from `snapshot.early_section_records`, never from a live
  storage read.
- **A specified outcome for each writer, not "either is fine".**
  - The storage write must **succeed** — the tier 1 driver exits 0, and in
    tier 2 `econpapers analyze` exits 0 — while the shell is open. The
    shell's connection is idle between turns and `sqlite3` opens no read
    transaction for a completed `SELECT`, so the writer's `BEGIN IMMEDIATE`
    acquires. If it instead times out with a lock error, that is a defect to
    fix, not an accepted outcome.
  - `update` must **complete successfully, and a successful repair must leave
    the next turn working.** Tier 1 asserts this at service level
    (`run_update_command` with injected provisioners against a synthetic
    managed install) while the shell session is open; tier 2 runs the real
    `econpapers update`. `UpdateArtifactOutcome.REPAIRED` is reported only
    after `ensure_managed_runtime` has downloaded, verified, readiness-checked
    and promoted the install (`services/update_command.py`:207-221), and
    `update` does not rewrite durable config — so the executable path the
    shell resolves still points into a verified install. A shell turn that
    constructs its generator for the first time after a successful update must
    therefore **succeed** (`ANSWERED`, or a withheld/no-answer outcome on the
    merits) and must not be a `TYPED_FAILURE`. Accepting a typed failure here
    would be accepting a regression with no mechanism behind it. A generator
    already constructed this session is cached and unaffected either way.
  - **Typed failure is required only where something is actually broken:** a
    deliberately failed repair (`UpdateArtifactOutcome.FAILED` via an injected
    downloader or extractor error) or corruption injected into the install
    *after* `update` returns. Then the next unconstructed turn must fail as
    `ShellTurnOutcome.TYPED_FAILURE` through `LlamaCppConfigurationError` or
    `LlamaCppReadinessError` (`services/interactive_shell.py`:333-341) — never
    a wrong answer, an unhandled exception, or damage to earlier turns.
  - **Mid-flight repair: `INTERNAL_FAILURE` is the current behavior, and M5
    records it rather than promising typed failure.** "It fails typed" is
    false once a generator already exists. If the executable disappears
    between construction and launch, `SubprocessRunner._start_process`
    converts the `OSError` into `LlamaCppProcessError`
    (`adapters/llama_cpp.py`:307-311), and the shell maps that to
    `ShellTurnOutcome.INTERNAL_FAILURE`, not `TYPED_FAILURE`
    (`services/interactive_shell.py`:476-490). Only the *unconstructed* case
    is typed, because it fails at `check_readiness()` with
    `LlamaCppReadinessError`/`LlamaCppConfigurationError`.

    M5 does not change that classification. Reclassifying a user-visible
    failure outcome is a behavior change with its own exit-code and
    documentation consequences; it needs its own issue, and folding it into a
    release-verification milestone would violate the "no unrelated changes"
    rule this plan closes on. It is recorded as a known limitation in the
    release checklist, next to runtime-repair non-atomicity, and flagged there
    as a candidate follow-up.

    What M5 *does* require is that the state be tested deterministically
    rather than left as an unschedulable race. The race cannot be scheduled
    reliably, but the state it produces can — provided the generator is
    actually *ready*, which construction alone does not achieve.
    `_build_llama_cpp_generator` returns an unverified generator
    (`services/chat_command.py`:712-726); readiness is lazy, performed inside
    `generate()` only when `not self._ready`, and `_ready` is set only after a
    successful check (`adapters/llama_cpp.py`:434-441). "Construct, delete the
    executable, ask a question" would therefore fail at `check_readiness()`
    with `LlamaCppReadinessError` and report `TYPED_FAILURE` — the wrong path,
    proving nothing about the one being pinned.

    The sequence must be: **(1)** ask a question that completes successfully,
    which sets `_ready = True` on the cached generator; **(2)** remove the
    managed executable; **(3)** ask a second question. Only then does
    `generate()` skip readiness, reach `_start_process`, and produce
    `LlamaCppProcessError` → `INTERNAL_FAILURE`. Assert that outcome, an
    actionable message, no wrong answer, and no unhandled exception, with
    turn 1's own rendered output still intact — step 1 provides that as a
    genuine prior turn rather than an empty baseline.

    `/show` is compared across step 3 too, but **not** for byte-identity, and
    this is where an earlier draft of this plan was wrong. `/show` reads
    `last_turn_citations`, which is scoped to the most recent turn and
    deliberately cleared by any non-answered turn so evidence is never left
    visible as though it belonged to the question just asked
    (`services/interactive_shell.py`, `last_turn_citations`). After the failed
    turn, `/show` therefore reports that no evidence is available — assert
    *that*, since asserting byte-identity here would assert against the
    product's intended behavior. Byte-identical `/show` is required in the
    concurrency scenario, where the session is untouched and only an outside
    writer changed; it is not required here, where the session itself moved on
    to a turn that produced no evidence. An explicit `check_readiness()` call
    in place of step 1 is
    acceptable only for a unit-level variant; the shell-level test wants the
    real first turn. That pins today's behavior so the follow-up issue — if
    taken — changes a test on purpose instead of discovering the mapping by
    accident. The interruption criterion above covers the on-disk half of the
    same window.
- **The writer actually wrote.** Restarting the shell after the writer
  finishes must show the new paper — otherwise the test proves nothing.

### No-upload criterion

"No query, PDF, answer, citation, or index upload by default" is currently a
slogan with no test behind it. M5 must make it a checkable claim, which means
first stating its boundary:

- **In scope — application-initiated network traffic on the four application
  paths.** `chat`, the interactive shell, `analyze`, and `status` must make no
  outbound network call at all, for any reason. There is no allowed-endpoint
  list here; the assertion is zero.
- **Explicitly out of scope — `setup` and `update` artifact downloads.** These
  are the only code paths holding a `Downloader`/`ArchiveExtractor`
  (`adapters/runtime_downloader.py`, `adapters/runtime_extractor.py`), they
  are gated to explicit invocation, and their traffic is HTTPS fetches of
  pinned, checksum-verified artifacts — downloads, never uploads. The
  no-upload claim is not weakened by them and must not be written as though
  they are exceptions to it.
- **Explicitly out of scope — the user-supplied executable.** Generation runs
  a `llama-completion` subprocess. Nothing in this project can constrain what
  an arbitrary user-supplied binary does with a socket, and M5 must say so
  rather than imply the guarantee extends into it. The claim covers what the
  application itself initiates.

Test shape, tier 1 — a `socket` guard fixture patching `socket.socket`,
`socket.create_connection`, and `socket.getaddrinfo` to raise, installed
around the four **service** entry points, which are the deepest layer that
accepts fakes. Per the injection rule above, this is not `econpapers chat`
coverage and must not be written as such:

- `run_chat_command` (`services/chat_command.py`:444) — injected `storage`,
  `config_backend`, `retriever_factory`, `generator_provider`;
- `run_interactive_shell` (`services/interactive_shell.py`:774) — the same
  four seams plus injected `stdin`/`stdout`;
- `run_single_paper_analysis_command`
  (`services/single_paper_analysis_cli.py`:885) — injected `extractor`,
  `generator`, `storage`, `config_backend`;
- `run_status_command` (`services/status_command.py`:325) — injected
  `config_backend`, `storage`, and both readiness checkers.

Each must produce its normal successful result with the guard installed. A
negative control — one test asserting the guard actually fires on a deliberate
connection attempt — is required, otherwise a guard that silently does nothing
passes everything.

Test shape, tier 2 — the real `econpapers chat`, bare `econpapers`, `analyze`,
and `status` commands, which have no injection seams at all
(`services/commands.py`:88 and its neighbours). The guard cannot be installed
inside a subprocess, so tier 2's claim is only that each command completes
successfully with the machine's network unavailable.

**That claim is worthless without proof the machine was actually offline** — a
command also succeeds when the isolation step silently failed. Tier 1 has a
negative control; tier 2 needs one too, in the same session, or it is not
evidence. The release checklist therefore prescribes, per platform:

- **Isolation.** Linux: run the commands inside `unshare -rn` (no network
  namespace). macOS: disable every interface, e.g. `networksetup
  -setairportpower <device> off` plus any wired service set to off. Windows:
  for a manual run, use `Disable-NetAdapter -Name <name>` in an elevated
  shell; on a hosted runner, keep the runner connected and install outbound
  firewall rules for `python.exe`, `econpapers.exe`, and the configured
  `llama-completion.exe`. Record the exact command used and whether elevation
  was required. The hosted form proves process-tree isolation rather than
  whole-machine isolation; all three executable rules and the guarded Python
  negative control are required.
- **Three-point control, starting with a preflight that must succeed.** Two
  post-isolation failures prove nothing on their own: they look identical
  whether the isolation command worked, the host was already offline, a
  corporate policy blocks egress, or the probe endpoint is simply
  unreachable. The probe is a raw-IP TCP connection that bypasses DNS —
  `python -c "import socket; socket.create_connection(('1.1.1.1', 443),
  timeout=5)"` — run three times, all recorded:
  1. **Before isolation: must succeed.** This establishes that the probe can
     detect connectivity on this host and that the endpoint is reachable. Any
     other endpoint may be substituted if `1.1.1.1:443` is unreachable here;
     record which was used.
  2. **After isolation, before the command suite: must fail.** The
     before/after transition — not the failure alone — is what demonstrates
     the isolation step did the work.
  3. **After the command suite: must fail.** Catches an interface that came
     back up mid-run.

  A DNS-only failure does not count as isolation, which is why the probe uses
  a literal address.
- **Downgrade rule.** If the preflight does not succeed, if either
  post-isolation attempt does not fail, or if the control is skipped, the
  tier 2 result is recorded as an observational manual check and **may not be
  cited as evidence for the no-upload gate**. The gate then rests on tier 1's
  guarded service-level tests alone, and the checklist says so explicitly.

Even at its strongest, tier 2 shows the commands work offline; it does not
instrument what they attempt. The no-upload assertion itself is tier 1's. That
division, and the generation-subprocess boundary above, are recorded in the
checklist rather than blurred into a combined claim neither tier supports.

### Cross-platform coverage

CI is *configured* to run Python 3.11 and 3.14 on ubuntu, macOS, and Windows,
plus the exact supported floor 3.10.12 on ubuntu and `macos-15-intel`
(`.github/workflows/ci.yml`). Two corrections to an earlier version of this
paragraph: it said "CI runs", which asserts execution — see "CI status, and
the rule for reading it" below, which is the authority for what has actually
run; and it said the floor was covered "only on ubuntu", which stopped being
true when `7945e9a` made `floor-check` a two-platform matrix. The line
references it carried are dropped, because line numbers in a file this
milestone edits go stale on the next change.

The in-file rationale says the macOS and Windows runner images offer only
3.10.11, which is below `pyproject.toml`'s current
`requires-python = ">=3.10.12"` floor.

That rationale was a point-in-time observation, not a standing fact, so M5
requires a **contemporaneous availability probe** rather than restating it.
The probe (and the command that reproduces it) is recorded in
`docs/release-checklist.md` § 5 and must be re-run each release.

**Probe result, 2026-08-08.** Every 3.10.12–3.10.20 entry in the
`actions/python-versions` manifest publishes `linux/x64`, `linux/arm64`, and
`darwin/x64` — and **no `win32` build at all**. 3.10.11 is the last 3.10 with
Windows installers, and it is below the floor. `darwin/arm64` also stops at
3.10.11. So the two platforms resolve differently, which the original blanket
assumption obscured:

Both bullets below mix a durable configuration fact with a dated observation,
and both observations expire — one on the next CI run, the other on the next
availability probe. They are scoped accordingly; "CI status, and the rule for
reading it" below is the authority for anything about what has run.

- **macOS floor coverage is configured; as of 2026-08-08 it had not executed.**
  *Durable:* it needs an x64 runner label, because `macos-latest` is arm64 and
  no `darwin/arm64` build exists at or above the floor, so
  `.github/workflows/ci.yml`'s `floor-check` job is a matrix over
  `ubuntu-latest` and `macos-15-intel`; the stale comment was corrected in the
  same change. *Dated:* the matrix entry was committed in `7945e9a` and
  pushed, and no workflow run had executed it as of that date — see the status
  subsection below, which this bullet does not restate. An earlier version
  said the coverage "exists and has been added"; that overstated it, and the
  overstatement is withdrawn.
- **Windows floor coverage does not exist, per the 2026-08-08 availability
  probe.** *Durable:* whether it exists is decided by upstream, not by this
  project, so the probe is re-run every release rather than trusted from this
  file. *Dated:* that probe found no `win32` build published at or above the
  3.10.12 floor, so on Windows the lowest **configured** matrix interpreter is
  3.11, recorded as a named limitation. Configured, not tested: no Windows
  coverage *from the current CI configuration* has been exercised, so calling
  3.11 "the lowest tested interpreter" would contradict the status subsection
  below. Windows was genuinely tested under the superseded workflow — see
  that subsection for why those passes do not carry over. If a later probe
  shows a `win32` build, the job gains a third matrix entry and the limitation
  is dropped.

Those two gaps are **not** of the same kind, and the release checklist records
them as separate limitations for that reason. Windows depends on
`actions/python-versions`: while no build is published there is nothing to
run, and no amount of compute changes that. Intel macOS is a resource
constraint: the build exists, the job exists, and a single successful CI run
clears it with no code or configuration change at all.

#### CI status, and the rule for reading it

Investigating the macOS floor job surfaced something larger. An earlier
version of this subsection got it wrong in the maintainer's favor by treating
the last green run as partial validation of today's matrix; that reading is
withdrawn, and the correction is the point of this subsection.

Two things below, and they age differently. The **rule** is durable. The
**status** is a dated observation that a single successful run will falsify,
and it is written so that such a run obviously supersedes it rather than
leaving the document quietly wrong.

**The rule (durable).** A green run validates the workflow *that ran*, not
the workflow now in the tree. When the matrix, the interpreter list, or the
job set changes, prior passes stop being evidence for the new configuration
and the coverage claim resets to nothing until the new configuration runs.
This holds whatever CI's current state is, and it is the reason the status
below matters at all.

**The status (as of 2026-08-08).** The last successful CI run was
`977c7e26` (2026-08-02) — but **it ran a different workflow, against a
different floor.** At that commit the matrix was
`{ubuntu, macos, windows} × {3.10, 3.14}`, with a *floating* `3.10`, and
there was **no `floor-check` job at all**. That was legitimate then:
`requires-python` was `>=3.10`, which floating `3.10` satisfies on every
platform. Windows and macOS really were tested there.

`945341e` changed both halves at once. It raised the floor to `>=3.10.12` —
for PEP 706 `tarfile` extraction, needed by managed runtime provisioning —
and in the same commit switched the matrix to `{3.11, 3.14}` and added
`floor-check`, precisely *because* floating `3.10` resolves to 3.10.11 on
macOS and Windows, which the new floor excludes. So `977c7e26`'s passes are
superseded rather than wrong, and by the rule above they are not evidence for
the configuration in the tree today.

`945341e` is also the first run that failed with zero steps. So,
**observed 2026-08-08** — the table is a snapshot, and a reader arriving at it
directly should re-run the commands in the release checklist's § 5 rather than
trust it:

| SHA | CI | Workflow |
| --- | --- | --- |
| `977c7e26` (2026-08-02) | last success | old: `× {3.10, 3.14}`, no floor job |
| `945341e` (PR #60 merge, in `main`) | failed, 0 steps | current: `× {3.11, 3.14}` + `floor-check` |
| `682178be`, `a58418e7` | failed, 0 steps | current |
| `50d18e3` (2026-08-08) | failed, 0 steps, all seven jobs | current |
| `7945e9a`, `bfc7996`, `3e050cb` | **no run exists** | current |

**As of 2026-08-08, no job of the current CI configuration has completed a
step at any SHA.** Not the `{3.11, 3.14}` matrix, and not `floor-check` in
either its ubuntu-only or its two-platform form. Applying the rule above,
`977c7e26`'s green tick attests to a workflow that no longer exists — and one
whose floating `3.10` does not satisfy the **current** `>=3.10.12` floor on
two of three platforms. That is a statement about today's floor, not a defect
in that run.

Every commit from `945341e` onward is therefore CI-unvalidated as of that
date: all of M4, all of M5 including its own tier-1 suite, and all of M6.
**One successful run at a candidate SHA supersedes this paragraph**; re-read
the rule, not this status, when that happens.

Why the jobs failed is **not established**. Zero steps with a two- to
ten-second duration shows only that no step ran; runner-allocation failure,
cancellation, a billing or minutes limit, or another service fault would all
look the same from the run metadata. Determining the cause needs the
repository's Actions billing or settings page, which is outside what this
document can assert.

Consequences for what M5 may claim: nothing about the current matrix is
certified, at any SHA. A run record may attest only to jobs the operator
watched pass at the candidate SHA; where CI did not run, the entry is "not
run", never a blank and never a pass inherited from another SHA or another
workflow. Local `pytest` on one machine is real evidence and is recorded as
such — single-platform, on the interpreter and OS named in the record.

### Release checklist artifact

The exit condition below makes a recorded release procedure MVP-blocking, so
"report the commands and versions" is not sufficient. M5 adds
`docs/release-checklist.md`, a named and versioned artifact containing:

- a checklist version, and the **release-candidate commit SHA** the run was
  executed against (see "Which commit was tested" below);
- environment prerequisites and the exact reproducible commands, each with its
  expected outcome and exit code (`ruff check .`, `ruff format --check .`,
  `pytest`, `pytest integration_tests -m model` with the gating variables set
  (and confirmation that the bare `pytest` run did not collect them), the
  manually triggered three-OS offline CLI workflow or its manual fallback,
  the manual real-network `setup` smoke run, and the M3 private-PDF harness);
- artifact identities: every pinned model and runtime artifact by
  `model_id`/`runtime_id`, version marker, expected size, and SHA-256,
  cross-referenced to `docs/artifact-licensing.md`;
- private-corpus handling — **the contract, not the corpus.** `AGENTS.md`'s
  git discipline forbids committing personal paths, and M3 deliberately keeps
  exact filenames, sizes, and checksums in the gitignored
  `papers/ACCEPTANCE_CORPUS_RECORD.md` (M3 above). The committed checklist
  therefore records only the environment-variable contract
  (`ECONPAPERS_TEST_ACCEPTANCE_DIR`, `ECONPAPERS_ACCEPTANCE_PAPER_DIR`), the
  per-case `case_a`–`case_f` pass/fail result, and a pointer to the gitignored
  maintainer record. The actual directory path, filenames, and corpus
  identities never enter the committed file;
- the verification matrix results table (OS × Python × scenario); and
- the network-isolation procedure actually used per platform, the probe
  endpoint, all three negative-control results (preflight success plus both
  post-isolation failures), and whether the downgrade rule was triggered;
- known limitations, including at minimum the three named above:
  runtime-repair non-atomicity; floor coverage as resolved by the availability
  probe; and a shell turn hitting a removed executable after its generator was
  constructed reporting `INTERNAL_FAILURE` rather than a typed failure, listed
  as a candidate follow-up issue and not fixed inside M5.

A filled-in run record for the release being cut is committed alongside the
checklist; the blank checklist alone does not satisfy the exit condition.

#### Which commit was tested

Committing the run record necessarily moves `HEAD` past the commit the run
tested, so "tested at `HEAD`" is never true and must not be written. The
checklist resolves this explicitly:

- the run record names the **release-candidate SHA** it attests to — the
  commit that was actually checked out when the commands ran;
- the follow-up commit that adds the record is **results-only**: it may touch
  the checklist and run-record files and nothing else. Any source, test, or
  configuration change invalidates the run and requires a fresh candidate SHA
  and a fresh run;
- **the release tag points at the candidate SHA**, never at the results-only
  commit on top of it. An earlier version left this open ("states which
  convention was used"); the maintainer settled it on 2026-08-08 and it is
  pinned here rather than deferred to the run record, so the convention is
  fixed before any record is produced. The reason: the tag must name the
  commit whose behavior was tested, and the results-only commit is by
  definition a commit the run did not test.

### Exit condition

M5 is the last milestone before MVP, but passing it does not by itself make
the MVP complete. Gate 0's decisions must also be *propagated* (M4 sweeps 1–2),
not merely decided.

MVP is complete only when Gate 0 is resolved, M1–M5 acceptance criteria pass,
documentation is current, artifact licenses are recorded, the release procedure
is recorded as `docs/release-checklist.md` with a filled-in run record, and the
final diff contains no unrelated changes. M6 is explicitly **not** part of this
gate.

**CI remains a blocker. M5 is blocked, and the way out is a maintainer
decision, not a documentation edit.**

An earlier version of this section removed green CI from the gate and allowed
a local-only release. That was a release-policy change made by implication,
which `AGENTS.md` forbids the coding agent from doing, and it is **withdrawn**.
Recording an outage is documentation; deciding that a release may ship without
the cross-platform evidence the gate was built to require is a product
decision.

The factual position, from the subsection above: as of 2026-08-08 no job of
the current CI configuration had completed a step, so M5 could not be passed
as written. That is recorded here as a **blocked state**, not resolved into a
softer gate — and it lifts on evidence, not on an edit: the first successful
run at a candidate SHA ends the block.

**Maintainer decision, 2026-08-08.** An earlier version of this paragraph
recorded the outcome as "option 1" alone, which was ambiguous: the option list
mixes a *policy* choice with an *action*, and the action currently being taken
is option 3's. The three parts are separated here so they cannot be conflated
again:

| | |
| --- | --- |
| **Decision (policy)** | CI remains a blocker. Option 2 — accepting local, single-platform evidence — was put to the maintainer and **declined**. Revisiting it needs its own explicit approval, recorded here with a date. |
| **Current state (action)** | Hold M5. No run record is produced, nothing claims MVP completion, and no attempt is made to diagnose or run CI. This is option 3's behavior, adopted because compute is unavailable — not because the gate was softened. |
| **Future remediation** | On the maintainer's signal that compute is available, pursue **option 1**: diagnose why jobs are not starting, fix it, and run CI once at the candidate SHA. That clears the block and the Intel-macOS floor limitation together. |

The distinction that matters: holding is a *consequence* of the resource
constraint, not the decision itself. The decision is that the gate keeps its
meaning, so M5 stays unpassable rather than becoming passable on weaker
evidence.

The three options as put to the maintainer:

1. **Diagnose why the jobs are not starting, fix that, and run CI once** at
   the candidate SHA. The cause is not established — see above — so this
   begins with the repository's Actions billing and settings pages, not with
   an assumption. One successful run clears the block completely, plus the
   Intel-macOS floor limitation, with no change to code, configuration, or
   policy. This is the only option that leaves the gate's meaning intact.
2. **Amend the gate** to accept recorded local, single-platform evidence plus
   the tier-2 manual checks, with every CI row marked "not run" and the gap
   carried into the release notes. This genuinely weakens the release
   evidence and needs explicit approval.
3. **Hold M5 open** until option 1 is possible, and ship nothing that claims
   MVP completion.

M5's status is therefore *blocked on CI*, and no run record is to be produced
— a record written now would either overstate its evidence or attest to a
gate that has not been agreed. The next action on this milestone is the
maintainer's signal that compute is available, not a further documentation
change.

---

## M6 — Artifact-metadata alignment (post-MVP)

`ManagedModelArtifact` (`domain/model_manifest.py`) and
`ManagedRuntimeArtifact` (`domain/runtime_manifest.py`) neither conform to nor
reuse `domain.ArtifactManifest` (schema version 1), the artifact-metadata
contract this project already built for this purpose. Both carry
`license_name`, `sha256`, `size_bytes`/`expected_size_bytes`, and a source
URL, but neither carries `redistribution_status`, `update_policy`, or
`contains_copyrighted_full_text`.

Scope: reconcile all three types — conform, reuse, or deliberately document
why they stay separate. `ManagedRuntimeArtifact` is in scope explicitly, not
optionally: it lacks the same three fields for the four pinned `llama.cpp`
archives that `setup` downloads.

Not in the MVP gate. M4 sweep 5 documents the licensing facts in prose, which
is what `AGENTS.md`'s corpus-and-licensing section actually requires; M6 is
about the type carrying them structurally so `update`/`status` could read them
rather than a human reading a document.

### Premise corrections found while planning

Four assumptions in the paragraphs above do not survive contact with the
code. They are recorded here rather than silently designed around. Item 1 is
a correction to an earlier draft of this section, which stated the usage
audit wrongly; the corrected audit changes the design.

1. **`ArtifactManifest` is not unused, and it has committed instance data.**
   An earlier draft said its two entry points were called from exactly one
   place, `tests/adapters/test_filesystem.py`. That was false.
   `load_manifest_from_file` is also called by `tests/adapters/test_corpus.py`,
   `tests/evaluation/test_generation_evaluation.py`, and
   `integration_tests/test_llama_cpp_model.py`; `verify_artifact` is also
   called by `tests/adapters/test_corpus.py`. The integration-test call reads
   a manifest path supplied by the user through `ECONPAPERS_MODEL_MANIFEST`,
   which is the closest thing in the repository to production use.

   More consequentially, **four `ArtifactManifest` JSON instances are
   committed**: `tests/fixtures/corpus/synthetic-economics-v1.manifest.json`
   and three `artifacts/models/*.manifest.json` Issue-13 evaluation
   candidates. One of those three,
   `artifacts/models/qwen2.5-1.5b-instruct-q4-k-m.manifest.json`, describes
   **the same file as the managed catalog's default model** — identical
   `sha256` and `expected_size_bytes` — under a divergent source URL (see
   correction 4). The schema is therefore live, exercised, and populated; the
   two representations of the default model are a duplication M6 has to
   reconcile, and that reconciliation is now in scope.

   Only the narrow form of the original claim survives: **no module under
   `src/` consumes `ArtifactManifest`.** It is a serialization contract used
   by tests, by the opt-in integration test, and by committed records — not
   by application code.
2. **No schema bump or migration is required.** An earlier draft of this
   section said the work implies "the manifest schema bump, migration, and
   tests that implies"; that clause has been removed above because it binds
   nothing. Neither managed catalog is ever serialized — both are Python
   literals.

   An earlier draft justified this by saying `InstallReceipt`
   (`domain/runtime_receipt.py`) is the only on-disk artifact derived from
   the runtime manifest. That was false. `setup` also persists
   manifest-derived identity into `LocalRuntimeModelConfig`
   (`domain/local_config.py`): `runtime_id`, `runtime_version_marker`, and
   the provisioned `executable_path`, plus `model_id`, `model_bytes`, and
   `model_checksum` from the model install. The accurate premise is narrower:
   **`InstallReceipt` is the only on-disk artifact that serializes the
   runtime bundle's member metadata**, and *neither* on-disk form carries a
   licensing field.

   The conclusion survives the correction. Both serialized shapes have fixed,
   explicitly enumerated field sets that this milestone does not touch, so
   adding fields to the catalogs changes no byte of `receipt.json` or of the
   durable configuration file. There is nothing to migrate, and
   `ManagedRuntimeManifest.schema_version` therefore stays `1`: bumping it
   would advertise a serialized-format change that does not exist.
3. **Licensing fields with no runtime reader are the existing precedent.**
   `license_name` and `attribution_text` already exist on both managed types
   and are read by nothing under `src/` — only by documentation and by
   humans. The three new fields joining them is consistent with the current
   design rather than a new gap.
4. **The two records of the default model disagree on its source URL.**
   `domain/model_manifest.py` pins
   `…/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf`,
   a *mutable* Hugging Face ref, and `docs/artifact-licensing.md` records the
   same URL. The committed
   `artifacts/models/qwen2.5-1.5b-instruct-q4-k-m.manifest.json` pins
   `…/resolve/dd26da440ef0330c47919d1ecae0966d24022222/…`, the immutable
   revision, for a file with the identical `sha256` and size. The checksum
   pin means users are never served different bytes silently — a changed
   upstream file fails verification — but it does mean a legitimate upstream
   update breaks provisioning for every user until a maintainer refreshes the
   pin, and it makes "pinned" less true of the URL than of the bytes. The 7B
   entry has the same `resolve/main` shape. **Reported, not fixed here**:
   changing a pinned source URL is a maintainer action under
   `docs/artifact-licensing.md`'s own update policy and belongs in its own
   issue, not folded into a metadata milestone.

### Chosen shape — reuse the vocabulary, not the dataclass

Add the three fields to both live types, importing `RedistributionStatus`
from `domain/artifacts.py` so the enum is literally shared rather than
duplicated. Full conformance to `ArtifactManifest` is **rejected**, for three
reasons that are each checkable in the code as it stands:

1. **`local_path` is required and cannot be pinned for the runtime.** The
   runtime's install directory is content-addressed and computed at
   provisioning time, and `runtime_manifest.py` has no path field at all.
   This reason is weaker for models than an earlier draft claimed: the
   committed `artifacts/models/*.manifest.json` instances show a model
   `local_path` is perfectly expressible (`models/qwen2.5-1.5b-instruct-q4_k_m.gguf`).
   It disqualifies conformance for `ManagedRuntimeArtifact` specifically, and
   the milestone requires the two types be treated together.
2. **`ArtifactKind` has no `runtime` member, and adding one forces an
   unanswered design question.** An earlier draft claimed adding the member
   would itself mean a schema bump and a migration of the four committed
   JSON files; that is too categorical and is withdrawn. Widening an enum is
   backward-compatible, and every existing record stays valid. The real cost
   is a decision this milestone is not the place to make: whether runtime
   bundles belong in the serialized `ArtifactManifest` contract at all, and
   if so what `local_path` means for a content-addressed install directory
   and where `bundle_member_checksums`, `archive_format`, and
   `executable_relative_path` live. Answering that is a schema design task
   with its own issue; guessing at it here is how a narrow interface
   extension turns into an architecture change.
3. **The managed types carry fields `ArtifactManifest` has no slot for**:
   `archive_format`, `bundle_member_checksums`, `executable_relative_path`,
   `platform`, `architecture`, `minimum_free_ram_bytes`, `display_name`,
   `summary`, `attribution_text`. Conformance either drops them or forces a
   parallel type anyway.

A fourth reason in an earlier draft — that binding the catalogs to a contract
with no reader buys nothing — is **withdrawn**: correction 1 shows the
contract has readers and committed data. It was the weakest of the four and
the design does not depend on it.

No projection function (`ManagedModelArtifact` → `ArtifactManifest`) is added
either; it would be more code with no caller under `src/`.

### Reconciling the duplicate record of the default model

Correction 1 puts a second scope item on this milestone that the earlier
draft missed. `artifacts/models/qwen2.5-1.5b-instruct-q4-k-m.manifest.json`
and `QWEN2_5_1_5B_INSTRUCT_Q4_K_M` pin the same bytes with different URLs and
different update-policy text, and nothing in the repository says how they
relate. The milestone's own wording — "reconcile all three types" — covers
this; a fourth representation simply went unnoticed when it was written.

The two records exist for different reasons: the JSON files are Issue-13
*evaluation candidates* describing a local install (`local_path`, and an
`update_policy` that says so), while the catalog describes a *download
source*. That is a defensible split, so the recommendation is **document the
relationship rather than delete either record** — a short subsection in
`docs/artifact-licensing.md` naming which record governs provisioning (the
catalog) and which is an evaluation artifact (the JSON), plus the URL
divergence from correction 4. Deleting the overlapping JSON is the
alternative; it is a maintainer decision, listed for sign-off below.

Documentation alone would not earn the word "reconcile": nothing would stop
the two records from drifting apart tomorrow. So this item also carries a
test, in `tests/test_artifact_licensing_doc.py`, over every committed
`artifacts/models/*.manifest.json` whose `artifact_id` matches a
`model_id` in `MANAGED_MODEL_CATALOG` — today exactly one, and the test
asserts that count is 1 so that a new overlap cannot appear unnoticed. For
each match it asserts:

- `sha256` and `expected_size_bytes` are **equal** across the two records,
  because they describe the same bytes and a divergence is a defect;
- the two `source` URLs are **both** pinned to their currently recorded
  values, so the known divergence is frozen rather than merely described. A
  change to either URL fails the test and forces a maintainer decision, which
  is also how the correction-4 fix will be picked up when its own issue lands.

**What the test deliberately does not compare, and why.** "Reconcile" here
means *the two records agree about the bytes*, not *the two records are the
same record*. `license`, `redistribution_status`,
`contains_copyrighted_full_text`, and `update_policy` are allowed to diverge,
and the documentation subsection will say so explicitly rather than leaving
"same file" to imply a stronger identity than the test enforces. The reason
is that the two records answer different questions. The catalog's
`update_policy` describes how a *download pin* is maintained; the JSON's says
`Pinned Issue 13 evaluation candidate; upgrades require new provenance,
checksum, compatibility testing, and semantic evaluation`, which describes
how an *evaluation candidate* is retired or replaced. Both are true at once,
and forcing them equal would destroy real information. The same holds for the
licensing classifications: the sibling
`smollm2-1.7b-instruct-q4-k-m.manifest.json` records
`redistribution_status: unknown` and a license string flagging that
conversion provenance needs maintainer review, which is a judgment made for
evaluation purposes and has no counterpart in a catalog of artifacts approved
for download.

Only `sha256` and `expected_size_bytes` identify the bytes, so only those are
required to agree. If either diverges, one record is simply wrong.

The maintainer has chosen to document the relationship and retain the
evaluation manifest, so this test and the documentation subsection are both
in scope.

### Defining `contains_copyrighted_full_text` before using it

An earlier draft set this field to `False` for both GGUFs and called the
values "taken verbatim" from `docs/artifact-licensing.md`. They are not
verbatim, and the gap matters. The document says the model contains "no paper
text from this project's corpus" — a claim M4 deliberately narrowed. A typed
`contains_copyrighted_full_text = False` reads as the much broader claim that
the artifact contains no copyrighted full text at all, which nothing here
establishes: a GGUF's training data is not characterized in this repository.

Two facts constrain the fix. First, the field is **not new and not
unclassified**: all four committed `ArtifactManifest` instances already
assert `false`, including
`artifacts/models/qwen2.5-1.5b-instruct-q4-k-m.manifest.json`, which
describes the very model the managed catalog defaults to. M6 would propagate
an existing typed classification, not invent one. Second, the field's scope
is genuinely undefined: `docs/artifact-manifest.md` documents it only as
"Boolean disclosure".

**Recommended resolution — define the scope, do not rename.** Add to
`docs/artifact-manifest.md` an explicit definition covering both
`ArtifactManifest` and the managed catalogs:

> `contains_copyrighted_full_text` discloses whether the artifact's
> distributed bytes contain copyrighted full text of research papers. It is a
> corpus-content disclosure, which is why `AGENTS.md`'s corpus-and-licensing
> section requires it. It is **not** a claim about a model's training data,
> which this project does not characterize.

Under that definition `False` is correct for all six artifacts, the M4 prose
and the typed value stop disagreeing, and the four committed instances stay
valid unchanged.

Renaming the field to something like `contains_copyrighted_paper_text` is the
honest alternative, and it is more expensive than it looks: the name is part
of `ArtifactManifest`'s serialized schema, so renaming means a real schema
bump plus migration of four committed JSON files — reinstating exactly the
clause correction 2 removed. That trade-off is why the recommendation is to
define rather than rename, but the choice is the maintainer's and is listed
for sign-off below. **No `contains_copyrighted_full_text` value is written
into either catalog until this is settled.**

### Code changes

A shared constant is added to `domain/artifacts.py`, next to
`RedistributionStatus`:

```python
PINNED_UPDATE_POLICY = (
    "Pinned to an exact URL, size, and SHA-256 in version-controlled data. "
    "The application never tracks upstream releases or upgrades on its own; "
    "changing a pin is a maintainer action verified against a real download."
)
```

Placing it there makes the "one policy governs all six artifacts" claim an
invariant of the code. On its own that does **not** bind the document, which
could later describe a different policy while every test still passed; the
declarations block described under "Tests" is what closes that gap.

Both `ManagedModelArtifact` and `ManagedRuntimeArtifact` gain:

| Field | Type | Validation |
| --- | --- | --- |
| `redistribution_status` | `RedistributionStatus` | must be an enum member |
| `update_policy` | `str` | nonempty after `strip()` |
| `contains_copyrighted_full_text` | `bool` | exact `isinstance(..., bool)`, so `1`/`0` are rejected |

Three deliberate choices:

- **`str`, not an enum, for `update_policy`**, matching
  `ArtifactManifest.update_policy`'s type exactly, so "shared vocabulary" is
  true rather than approximate. Drift is prevented by the shared constant,
  not by the type.
- **No defaults.** Adding a new pinned artifact must force its author to
  state all three facts. That is the licensing guardrail working, and it is
  worth the churn.
- **Each module raises its own error type** — `ManagedModelManifestError` /
  `ManagedRuntimeManifestError`, never `ArtifactManifestError` — preserving
  each module's existing contract. One test per module pins this.

Both module docstrings gain a note that these three fields are
maintainer-supplied *classifications*, unlike `sha256` and the size fields,
which are computed from a real download. This mirrors the safety boundary
`docs/artifact-manifest.md` already states for `redistribution_status`.

Churn: 20 construction sites — 6 under `src/` (2 model catalog entries, 4
runtime artifacts in `domain/runtime_manifest_data.py`) and 14 in tests
across **seven** files (an earlier draft said six):
`tests/domain/test_runtime_manifest.py`,
`tests/services/test_model_provisioning.py`,
`tests/services/test_runtime_provisioning.py`,
`tests/services/test_setup_command.py`,
`tests/services/test_status_command.py`,
`tests/services/test_update_command.py`, and
`tests/services/_release_fixtures.py`. Two of those are shared factories
(`test_runtime_manifest.py`, `test_runtime_provisioning.py`), leaving twelve
literal sites needing three keyword arguments each.

All six production artifacts take `RedistributionStatus.PERMITTED` and
`PINNED_UPDATE_POLICY`. The `permitted` classification is genuinely already
recorded — in the document for all six, and in typed committed data for the
default model — but it is **not** transcribed verbatim from prose, and an
earlier draft's claim that all three values were is withdrawn:
`PINNED_UPDATE_POLICY` is new wording written for this milestone, and
`contains_copyrighted_full_text` is blocked on the definition above.

### Tests

An earlier draft proposed asserting that each artifact's `attribution_text`
appears in `docs/artifact-licensing.md` after whitespace normalization. That
would have failed on first run for all four runtime artifacts, and the reason
generalizes. `_LLAMA_CPP_ATTRIBUTION` reads `Copyright (c) 2023-2026 The ggml
authors. Licensed under the MIT License; see the LICENSE file bundled in the
downloaded archive for the full text.`, while the document reads `Copyright
(c) 2023–2026 The ggml authors, licensed under the MIT License.` — different
dash, different punctuation, different wording, and one clause absent.
Whitespace normalization cannot repair any of that, and it should not: the
document is *paraphrasing* the notice, which is a legitimate thing for prose
to do.

The lesson is that **containment is the wrong mechanism for free-text
values.** It only ever worked for the two model attributions by luck. The
same weakness is why an earlier draft could not bind `PINNED_UPDATE_POLICY`
or the shared classifications to the document at all.

**Mechanism: a generated declarations block, compared by equality.**
`docs/artifact-licensing.md` gains one appendix section, "Typed declarations
(generated — do not edit by hand)", holding a fenced block that renders every
typed licensing fact for all six artifacts. A single helper renders that text
from the two catalogs; the test asserts **byte-exact equality** between the
committed block and the rendered text.

#### Canonical form of the block

An earlier draft said "exact equality after whitespace normalization", which
promises two incompatible things. The block is machine-written, so there is
no reason to tolerate any difference in it: comparison is **byte-exact**, and
all normalization happens inside the renderer, where it is deterministic.
Whitespace normalization survives only for the *narrative* containment
guards, where a human line-wrapping a sentence is legitimate.

The renderer emits, and the test compares:

- UTF-8, `\n` line endings, no trailing whitespace on any line, exactly one
  newline before the closing fence;
- one record per artifact, records sorted by artifact identifier ascending
  (`model_id` for models, `runtime_id` plus platform and architecture for the
  four runtime archives, which share a `runtime_id`), models before runtimes;
- one `key: value` per line in a fixed field order, records separated by a
  single blank line;
- sizes as plain decimal integers with no separators — the comma-grouped form
  belongs to the narrative, not here; booleans as lowercase `true`/`false`;
  enums as their `.value`;
- free-text values — attribution and update policy — collapsed to a single
  line, runs of whitespace reduced to one space. This is where whitespace
  normalization actually belongs: in producing the canonical text, not in
  comparing it.

**Line endings are the script's responsibility, not git's.** An earlier draft
justified byte-exact comparison by citing `.gitattributes` (`* text=auto
eol=lf`). That is necessary and not sufficient, and the justification is
withdrawn: `.gitattributes` governs what git writes at checkout, while
Python's text mode independently translates `\n` to `\r\n` on write under
Windows and collapses CRLF to LF on read. A `--write` using ordinary text I/O
would therefore emit a CRLF document on Windows, which git would then
renormalize, producing a file that differs from what the tool just wrote.

So the script does **binary I/O throughout**: `Path.read_bytes()` then
`.decode("utf-8")`, and `.encode("utf-8")` then `Path.write_bytes()`, with no
text-mode `open()` anywhere. Equivalently explicit `newline="\n"` on both
sides would do, but binary is harder to get wrong by accident. The script
tests assert on the **bytes** written, including that the output contains no
`\r`, so a regression to text mode fails on Windows CI rather than silently
producing a file that only that platform sees.

#### Authority hierarchy

The document today opens by calling itself "the single authoritative record
of every artifact the application downloads". After M6 that is no longer
true, and leaving it would leave two things claiming authority over the same
facts. The hierarchy M6 establishes, and writes into the document's opening
section:

1. **The catalogs** (`domain/model_manifest.py`,
   `domain/runtime_manifest_data.py`) are the source of truth for every typed
   licensing fact. They are what `setup` and `update` actually act on.
2. **The generated declarations block** is a mechanically faithful projection
   of the catalogs, and is authoritative *within the document* — including
   for the four fields the narrative only paraphrases. It is generated, never
   hand-edited, and a test fails if it drifts from the catalogs.
3. **The human narrative** explains and contextualizes: why these assets and
   not GPU builds, what `permitted` does and does not mean legally, how the
   corpus rules differ. It is authoritative for nothing the block covers, and
   where the two ever disagree the block wins.

`AGENTS.md`'s requirement is unaffected: the seven licensing facts are still
documented for all six artifacts, now in a form that cannot silently drift
from the code.

#### Regenerating the block

The helper is `scripts/render_artifact_declarations.py`, alongside the
existing `scripts/evaluate_generation.py` and `scripts/measure_retrieval.py`.
It exposes a pure `render_declarations() -> str` over the two catalogs plus a
`main()` with two modes:

```bash
# --check exits 1 on drift and prints the diff; --write rewrites in place.
# --document defaults to docs/artifact-licensing.md; tests pass a tmp copy.
python scripts/render_artifact_declarations.py --check
python scripts/render_artifact_declarations.py --write
python scripts/render_artifact_declarations.py --check --document PATH
```

The block is delimited in the document by explicit markers rather than by a
heading, so both extraction and rewriting are unambiguous and survive
editing of the surrounding prose:

```text
<!-- BEGIN GENERATED: artifact-declarations -->
...fenced block...
<!-- END GENERATED: artifact-declarations -->
```

`--write` replaces only the text between those markers and fails loudly,
without modifying the file, if either marker is missing, either is
duplicated, or the closing marker precedes the opening one. That is five
failure branches, and the script tests below cover exactly those five.

**The consistency test never regenerates.** It loads the script module with
`importlib.util.spec_from_file_location`, following the precedent in
`tests/evaluation/test_generation_script.py`, calls `render_declarations()`,
and compares against the committed document. It never invokes `--write`,
never imports anything that writes, and never touches `docs/` — a test that
silently rewrote the file it checks would pass unconditionally, which is the
same failure mode as a guard with no teeth.

For that separation to be testable, the path is explicit at both layers.
`render_declarations() -> str` is pure; `check_document(path)` and
`write_document(path)` take the file to operate on; and `main()` accepts
**`--document PATH`**, defaulting to `docs/artifact-licensing.md`. An earlier
draft specified only `--check`/`--write`, which left the tests no way to
exercise the CLI without writing to the real document — they would have had
to bypass `main()` and lose coverage of argument parsing and exit codes.

The script tests therefore drive `main()` with `--document` pointed at a
`tmp_path` copy, so exit codes and output are part of what is covered. The
only test that touches the real document runs `--check` against it, which
reads.

#### Tests for the script itself

The renderer is the state-changing part of this milestone and an earlier
draft left it entirely unverified — specified in prose, exercised by nothing.
`tests/test_artifact_declarations_script.py` covers both modes and every
failure branch:

- `--check` against the committed document exits `0`;
- `--check` against a copy whose block has been altered exits `1` and prints
  a diff naming the changed line;
- `--write` against that altered copy restores the block, and the bytes
  **before the opening marker and after the closing marker are unchanged** —
  asserted by comparing those two slices to the original, which is what
  "replaces only the interior" has to mean to be worth claiming;
- `--write` against an already-correct copy leaves the file byte-identical
  and reports that nothing changed;
- the bytes `--write` produces contain no `\r`, so a regression from binary
  to text-mode I/O fails on Windows CI instead of silently emitting a CRLF
  document that only that platform sees;
- **five** marker-failure cases, each exiting non-zero **and leaving the file
  byte-identical**, asserted by hashing before and after. The contract says
  *either* marker missing or duplicated, which is two markers times two
  failure modes, plus the ordering case — an earlier draft listed only three
  and so contradicted its own "every failure branch" claim while omitting the
  likelier delimiter bugs:
  - opening marker absent;
  - **closing marker absent**;
  - opening marker duplicated;
  - **closing marker duplicated**;
  - markers in reverse order (`END` before `BEGIN`).

  A rewriter that corrupts a document while failing is worse than one that
  refuses, which is why byte-identity is asserted on all five rather than
  just the exit code.

The byte-identity assertions are the point: without them a `--write` that
rewrote the whole file, or a marker check that failed after truncating,
would pass.

This is what makes the three new fields document-bound rather than
code-only: every typed value fails the suite if the code and the block
disagree, in either direction, including the shared policy text and the
shared classifications.

A new `tests/test_artifact_licensing_doc.py` holds the check, anchored by a
`REPO_ROOT` computed from `Path(__file__).resolve().parents[1]`, following
the `parents[2]` precedent in `tests/adapters/test_corpus.py`.

#### What binds the human-written narrative, and what does not

An earlier draft claimed the guards below mean "the narrative cannot quietly
contradict the generated block". That was an overclaim: the guards it listed
covered only source URL, license, checksum, and size, so the prose could have
said redistribution was prohibited while the block said `permitted` and every
test would still have passed. The block binds **code → block**; binding
**block → narrative** needs its own guards, and for one field it is not
achievable at all. The honest split:

*Guards on the narrative body — the document with the generated block
removed, so the block cannot satisfy a check about the prose. Only the first
is genuinely per-artifact; the rest are whole-document consistency checks,
and the plan calls them that:*

- **per-artifact**: each artifact's `source_url`, artifact-level `sha256`,
  and comma-grouped size (`f"{n:,}"`) appears in the narrative. These values
  are distinct per artifact, so containment binds each one individually;
- **whole-document**: the set of `license_name` values and the set of
  `redistribution_status` values appearing in the catalogs each appear in the
  narrative. Because all six artifacts currently share `permitted`, and five
  of six share a license, a single occurrence satisfies the check for every
  artifact — this detects a wholesale contradiction, not a single divergent
  row;
- **whole-document**: the narrative contains no affirmative form of the
  copyrighted-full-text disclosure — no `yes` following a `contains
  copyrighted full text` phrase in the normalized text;
- **whole-document**: every 64-character hexadecimal token anywhere in the
  document is one of the six artifact-level checksums, catching a stale pin
  left behind after a code change. `bundle_member_checksums` are deliberately
  not in the document.

The copyrighted-full-text guard **does not invert automatically**, and an
earlier draft claiming it would is withdrawn. The narrative has no
per-artifact machine-readable disclosure for this field — the model tables
say `Contains copyrighted full text | No.` and the runtime section makes one
statement covering all four archives — so there is no structure from which a
future `True` could be matched to the artifact it belongs to. Inventing one
would mean an undocumented parsing convention, which is the brittleness this
whole mechanism avoids.

Instead the guard states its own precondition. It first asserts that all six
artifacts are `False`, and if that ever stops holding it fails with a message
naming the reason: the narrative guard assumes a uniform all-false
disclosure, and a `True` value requires either per-artifact machine-readable
disclosure in the document or dropping the prose guard and relying on the
generated block alone. That is a loud, self-explaining failure rather than a
guard that silently stops meaning anything — and the generated block, which
*is* per-artifact and exact, keeps binding the field correctly either way.

*Not bound, with the claim narrowed accordingly:*

- **The narrative's "## Update policy" section is a paraphrase and is not
  checked.** It cannot be: the same paraphrasing that broke attribution
  containment applies to a multi-paragraph prose policy. The generated block
  is authoritative for the exact `PINNED_UPDATE_POLICY` text, and the
  document will say so in a pointer sentence at that section, so a human
  reader knows which one governs.
- **Attribution is not checked against the prose**, only against the block,
  for the same reason.
- **No reverse URL check**, because the document legitimately contains the
  `llama.cpp` repository URL as attribution, which is not an artifact source,
  and an allowlist would be brittle.

*Residual weakness, stated rather than papered over:* the whole-document
guards are containment, and all six artifacts currently share the same status
and classification. If one of six entries were edited to disagree while the
other five stayed correct, containment would still be satisfied. Catching
that needs per-artifact prose parsing, which reintroduces exactly the
brittleness the generated block exists to avoid. **The generated block is
authoritative for redistribution status, update policy, the
copyrighted-full-text disclosure, and attribution**; the narrative guards
catch wholesale contradiction, not a single divergent row, and the acceptance
criteria below say so in those terms.

Validation tests go in the existing `tests/domain/test_model_manifest.py` and
`tests/domain/test_runtime_manifest.py`: each new field rejected for the
wrong type, each raising its own module's error type, and
`contains_copyrighted_full_text=1` rejected specifically.

**Mutation check before the work is called done**, following the M5 precedent
that a checker with no teeth passes everything. Five separate mutations, each
applied and reverted one at a time, each of which must fail the suite:

1. corrupt one pinned checksum in code (exercises both the equality assertion
   and the prose containment guard);
2. corrupt one pinned size in code;
3. change one artifact's `redistribution_status` to `UNKNOWN` in code
   (exercises the declarations block on a field the prose cannot bind);
4. change a **value** inside the generated block in the document — flip one
   `permitted` to `prohibited` there, not a whitespace edit — exercising the
   check in the document-drift direction, which is the direction the earlier
   draft left entirely uncovered. A whitespace-only edit would prove nothing
   about semantic drift, which is the failure this guard exists to catch;
5. change every `permitted` in the *narrative* to `prohibited`, leaving the
   generated block correct (exercises the narrative guard specifically — the
   scenario that passed silently in the earlier draft).

Reported in the summary, never committed.

### Documentation

| File | Change |
| --- | --- |
| `docs/artifact-licensing.md` | Replace "Residual schema gap — M6" with a "Typed representation" section; flip the "Until M6 lands, this document is the authoritative source" sentence; **replace the opening "single authoritative record" claim with the three-level hierarchy above**; update the Classification block, which currently marks that section **[planned]**; add a pointer at "## Update policy" naming the generated block as authoritative for the exact policy text. Add the generated "Typed declarations" appendix between its markers, and the subsection reconciling the duplicate default-model record. |
| `scripts/render_artifact_declarations.py` | New maintainer helper: `render_declarations()` plus `--check`/`--write`. |
| `docs/artifact-manifest.md` | New "Relationship to the managed catalogs" section: the three non-conformance reasons, and the accurate usage picture — no `src/` consumer, but four committed instances and four test/integration call sites. Add the `contains_copyrighted_full_text` scope definition, which governs both the schema and the catalogs. |
| `AGENTS.md` | The "do not yet carry … tracked as M6" paragraph becomes a statement that they carry them, with the shared-vocabulary note. Also fix the false `artifacts/` claim in the repository map — see below. |
| `MVP-PLAN.md` | Ladder row for M6 marked done; this section gains a run record. M4's historical sweep text stays as written — it records what was true then. |
| `README.md` | Verified: makes no claim M6 falsifies. No edit. |
| `docs/roadmap.md` | Verified: the prose facts remain documented. No edit. |

### Two defects found while planning

**In scope: the false `artifacts/` claim in `AGENTS.md`.** An earlier draft
deferred this to its own issue. That was the wrong call and it is withdrawn:
M6 already edits `AGENTS.md`, the false sentence sits in the same repository
map, and leaving a known-wrong repository instruction in place changes how
future agents behave and what a fresh clone is assumed to contain. The
no-unrelated-changes rule protects reviewability, and a correction inside a
file the milestone already touches, about the very artifact records the
milestone is reconciling, does not threaten it.

The fix is larger than the one word it looks like. `AGENTS.md`'s
"Corpus, models, and papers/ directory" section lists `papers/`, `models/`,
`runtimes/`, `artifacts/`, `generation-results/` as "local, gitignored
working data", glosses them as including "artifact manifests", and concludes
"None of this is committed or redistributed". Three claims are false for
`artifacts/`: `.gitignore` has no such entry, the manifests under it *are*
tracked, and they must be — `tests/evaluation/test_generation_evaluation.py`
asserts on the exact set of files there, so a fresh clone fails without them.
The other four directories are correctly ignored. So the sentence needs
`artifacts/` removed from the list, the "artifact manifests" gloss removed,
and a short clause added recording that `artifacts/models/*.manifest.json`
are committed `ArtifactManifest` records. That change is itself covered by
the reconciliation test above, which fails if those files stop existing.

**Out of scope: the mutable `resolve/main` source URLs**, described in
correction 4. Changing a pinned source URL is a maintainer action under the
document's own update policy, needs a fresh download to confirm the pin, and
belongs in its own issue. The reconciliation test freezes the current values
so the divergence cannot widen unnoticed in the meantime.

### Decisions requiring maintainer sign-off

The first two were blocking. **Both were answered by the maintainer on
2026-08-08 and are now settled**; the rest are standing choices recorded so
they are not made by implication.

1. **SETTLED — the scope of `contains_copyrighted_full_text`.** Adopt the
   corpus-content definition proposed above: a disclosure about whether the
   artifact's distributed bytes contain copyrighted *paper* text, explicitly
   not a claim about a model's training data. **The serialized field is not
   renamed**, so no schema bump and no migration of the four committed JSON
   records follows, and correction 2's conclusion stands unconditionally.
2. **SETTLED — the duplicate record of the default model.** Document the
   relationship and **retain** the evaluation manifest; deleting it would
   remove Issue-13 evaluation coverage for no gain. The identity test and the
   documentation subsection above are both in scope, and
   `tests/evaluation/test_generation_evaluation.py` needs no change.
3. **No runtime consumer is added.** The rationale above says the point is
   that `update`/`status` *could* read these fields. No CLI surface is added:
   that is user-facing scope this milestone does not ask for, and
   `license_name`/`attribution_text` set the precedent of licensing data with
   no reader. Surfacing them in `status` is a one-line scope change if the
   maintainer wants it.
4. **No `schema_version` bump**, justified by correction 2. Decision 1
   settling on "define, do not rename" removes the one condition that would
   have forced one.
5. **`attribution_text` is included in the generated declarations block**
   even though it is not one of the seven `AGENTS.md` licensing facts — same
   mechanism, no extra machinery, and it is the only way the exact notice
   text stays checked now that prose containment has been dropped for it.
6. **`ArtifactManifest` is left in place unchanged.** M6 documents its
   accurate usage picture; deleting it, wiring it into `src/`, or migrating
   the managed catalogs onto it are separate decisions, not ones to make by
   implication here.

### Sequencing constraint

M6 must start from a clean working tree. At planning time the tree held
seven modified files of unrelated in-progress work, including
`domain/runtime_manifest_data.py`, which M6 also edits. `AGENTS.md` requires
one issue per pull request and a final diff with no unrelated changes, so
that work is committed or stashed first.

### Acceptance

`ManagedModelArtifact` and `ManagedRuntimeArtifact` each carry
`redistribution_status`, `update_policy`, and `contains_copyrighted_full_text`
as validated fields sharing `domain/artifacts.py`'s vocabulary, and all six
pinned artifacts declare all three.

`contains_copyrighted_full_text` has a written scope definition — the
corpus-content reading settled in decision 1 — that a reader of either the
schema or the catalogs will find, and the serialized field is not renamed.

The generated declarations block matches the catalogs exactly and is the
**authoritative** record of redistribution status, update policy, the
copyrighted-full-text disclosure, and attribution. The document states the
catalogs → block → narrative hierarchy in place of its former "single
authoritative record" claim. `scripts/render_artifact_declarations.py`
regenerates the block via `--write` and reports drift via `--check`, with
`--document` selecting the target file; the script uses binary UTF-8 I/O
throughout, so its output is LF on every platform; both modes and all five
marker-failure branches — each marker absent, each marker duplicated, and the
reversed pair — are driven through `main()` against `tmp_path` copies, each
asserted to leave the target byte-identical; and
the consistency test compares the committed block without ever regenerating
it. The
milestone-ladder row for M6 states the design that was actually chosen rather
than the conformance it rejected. The narrative guards are
weaker by design and the document says which is which: per-artifact for
source URL, checksum, and size; whole-document consistency for license and
redistribution status; and a whole-document check on the copyrighted-full-text
disclosure that asserts its own all-false precondition and fails with a
directive message if that precondition ever breaks. All five mutations fail
the suite.

The duplicate default-model record is documented, covered by the identity
test, and accompanied by an explicit statement of which fields may legitimately
diverge between the catalog and the evaluation manifest. `AGENTS.md`'s
repository map no longer claims `artifacts/` is gitignored. The reason full
conformance was rejected is written down where a reader of `ArtifactManifest`
will find it. `ruff check .`, `ruff format --check .`, and `pytest` are
clean, and the diff contains no part of the unrelated in-progress work.

Out of scope: the M5 remainder (tier 2 run, run record, tag),
`ArtifactKind.RUNTIME` and the serialized-schema design question behind it,
any projection function, any change to `receipt.json` or durable
configuration, and the mutable `resolve/main` source URLs.

### Run record — 2026-08-08

Implemented as planned. `ruff check .` and `ruff format --check .` clean;
**1518 passed, 1 skipped** (baseline before the change: 1467 passed,
1 skipped — 51 new tests).

All five mutations were applied one at a time and reverted, each required to
fail the two new test files:

| # | Mutation | Caught by |
| --- | --- | --- |
| 1 | Pinned checksum corrupted in code | 7 tests, including the block equality, the per-artifact narrative guard, both checksum sweeps, and the overlap identity test |
| 2 | Pinned size corrupted in code | 5 tests |
| 3 | `redistribution_status` → `UNKNOWN` in code | 5 tests, including `test_all_artifacts_are_permitted_and_carry_no_paper_text` |
| 4 | A value changed *inside* the generated block | 3 tests — the document-drift direction |
| 5 | Narrative says `prohibited` while the block stays correct | `test_narrative_uses_the_declared_licenses_and_statuses` |

Mutation 5 is the one worth noting: it is the scenario that would have passed
silently under the earlier draft of this plan, and it is caught by exactly the
guard added in response to that finding, and by no other test.

A sixth mutation was added during review, after a finding that the
artifact-coverage test only asserted a record *count* and the model
identifiers: a renderer that omitted one runtime while duplicating another
would keep the count right, and regenerating the document would satisfy byte
equality. Applied — the renderer patched to drop the first runtime and emit
the second twice, then `--write` run so the document matched — the whole suite
produced exactly one failure,
`test_generated_block_covers_exactly_the_downloadable_artifacts`. That
confirms both that the hole was real and that the strengthened test, which
pins the exact set of six `(kind, identifier)` pairs and rejects duplicates,
is the only thing standing in front of it.

Four deviations from the plan as written, all small:

1. The plan said validation tests would go in "the existing
   `tests/domain/test_model_manifest.py`". That file did not exist — model
   manifest validation was tested indirectly from
   `tests/services/test_model_provisioning.py`. It was created, mirroring the
   sibling `test_runtime_manifest.py`.
2. The plan did not specify exit codes beyond "non-zero" for marker failures.
   The script uses `1` for drift and `2` for a marker failure, with a test
   pinning the distinction: collapsing them would tell a caller to run
   `--write` when `--write` cannot help.
3. `test_generated_block_covers_exactly_the_downloadable_artifacts` was added
   beyond the planned set. Byte-exact equality alone cannot catch a renderer
   that silently omits an artifact, because the document would be regenerated
   to match the omission. It asserts the exact set of six
   `(kind, identifier)` pairs — platform and architecture included, since all
   four runtimes share one `runtime_id` — and rejects duplicates, because a
   count alone is satisfied by omitting one record and duplicating another.
4. `test_write_changes_only_the_marker_delimited_interior` compares raw
   `bytes` slices rather than decoded text. The two are equivalent for valid
   UTF-8, but the guarantee being claimed is about what lands on disk, and
   only a byte comparison states it literally.

**Process note.** The first attempt at the mutation run used
`git checkout -- src docs` to revert each mutation. Heredoc redirection meant
the mutation commands never ran, so the checkout reverted every `src/` and
`docs/` change in the working tree instead — the implementation had to be
redone from scratch. Nothing was lost permanently (tests, scripts, and the
plan were outside the checkout paths), but the lesson is recorded here: a
mutation harness must restore from a copy it made itself, never from git,
which cannot distinguish the mutation from the work under test. The harness
that produced the table above backs up each target file and restores from that
backup, and asserts the restore is byte-identical.
