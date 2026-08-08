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
| M6 | Artifact-metadata alignment (post-MVP) | `ManagedModelArtifact` **and `ManagedRuntimeArtifact`** conform to or reuse `domain.ArtifactManifest`, carrying `redistribution_status`, `update_policy`, and `contains_copyrighted_full_text`. **Outside the MVP-complete gate** — M4 sweep 5 documents these facts in prose first, which is what `AGENTS.md` licensing actually requires. |

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
    actionable message, no wrong answer, and no unhandled exception — and
    compare turn 1's rendered output and `/show` before and after step 3,
    which step 1 now provides as a genuine prior turn rather than an empty
    baseline. An explicit `check_readiness()` call in place of step 1 is
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
  `Disable-NetAdapter -Name <name>` in an elevated shell. Record the exact
  command used and whether elevation was required.
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

CI runs Python 3.11 and 3.14 on ubuntu, macOS, and Windows, and the exact
supported floor 3.10.12 only on ubuntu (`.github/workflows/ci.yml`:61-80). The
in-file rationale (`.github/workflows/ci.yml`:24-32) says the macOS and
Windows runner images offer only 3.10.11, which is below `pyproject.toml`'s
`requires-python = ">=3.10.12"` floor.

That rationale is a point-in-time observation, not a standing fact, and M5
must not restate it as one — `actions/python-versions` publishes a version
manifest that changes over time and includes source-built macOS packages for
some versions. So M5 requires a **contemporaneous availability probe**: query
the `actions/python-versions` manifest for a 3.10.12-or-later 3.10.x build on
`macos-latest` and `windows-latest`, and record the result with its date in
the release checklist.

- If a usable build **exists**, extend the matrix to cover the floor on those
  platforms; the limitation disappears and the stale `ci.yml` comment is
  corrected in the same change.
- If it **does not**, record the named limitation — *floor-version coverage is
  Linux-only; on macOS and Windows the lowest tested interpreter is 3.11* —
  citing the probe result and date, not a static assumption. Building 3.10.12
  from source on those runners remains available at accepted cost and
  flakiness if the maintainer wants the coverage.

Either way, M5 certifies exactly `{ubuntu, macos, windows} × {3.11, 3.14}`
plus `ubuntu × 3.10.12` — stated in those terms, never as "cross-platform
support" in general — and the release report records the interpreter versions
each job actually resolved, not the matrix strings.

### Release checklist artifact

The exit condition below makes a recorded release procedure MVP-blocking, so
"report the commands and versions" is not sufficient. M5 adds
`docs/release-checklist.md`, a named and versioned artifact containing:

- a checklist version, and the **release-candidate commit SHA** the run was
  executed against (see "Which commit was tested" below);
- environment prerequisites and the exact reproducible commands, each with its
  expected outcome and exit code (`ruff check .`, `ruff format --check .`,
  `pytest`, `pytest integration_tests -m model` with the gating variables set
  (and confirmation that the bare `pytest` run did not collect them), the manual
  real-network `setup` smoke run, and the M3 private-PDF harness);
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
- the release tag points at the candidate SHA or at the results-only commit on
  top of it, and the checklist states which convention was used.

### Exit condition

M5 is the last milestone before MVP, but passing it does not by itself make
the MVP complete. Gate 0's decisions must also be *propagated* (M4 sweeps 1–2),
not merely decided.

MVP is complete only when Gate 0 is resolved, M1–M5 acceptance criteria pass,
documentation is current, artifact licenses are recorded, the release procedure
is recorded as `docs/release-checklist.md` with a filled-in run record, and the
final diff contains no unrelated changes. M6 is explicitly **not** part of this
gate.

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
why they stay separate — with the manifest schema bump, migration, and tests
that implies. `ManagedRuntimeArtifact` is in scope explicitly, not optionally:
it lacks the same three fields for the four pinned `llama.cpp` archives that
`setup` downloads.

Not in the MVP gate. M4 sweep 5 documents the licensing facts in prose, which
is what `AGENTS.md`'s corpus-and-licensing section actually requires; M6 is
about the type carrying them structurally so `update`/`status` could read them
rather than a human reading a document.
