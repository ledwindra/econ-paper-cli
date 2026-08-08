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

The index-scope and causal-classification decisions above are **decided but
not yet reflected in the documents** — propagating them is M4 sweep 1 and
sweep 2 respectively, including the `README.md` relabel and the
`docs/product-requirements.md`:55 / `AGENTS.md` rewording. Until M4 lands,
those documents still state the pre-decision wording.

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
`size_bytes`/`expected_size_bytes`, and a source URL. The maintainer chose to
approve download/default-model behavior as implemented rather than gate that
approval on adding these fields first; this is recorded as a real,
un-actioned gap rather than silently dropped. M2 shipped without addressing
it (see its "Out of scope" note). It is now split and owned: M4 sweep 5
documents the underlying licensing facts in prose (blocking M4), and **M6**
aligns the type itself (post-MVP, outside the completion gate).

## Milestone ladder

| # | Milestone | Exit condition |
| --- | --- | --- |
| Gate 0 | Reconcile MVP contract ✅ decided | All 4 items decided 2026-08-08. Decisions recorded above; **propagating the latter two into the documents is M4's job**, so Gate 0 is not "closed" until M4 lands. |
| M1 | Evidence inspection ✅ done | A user can inspect the full stored passage for a citation in both CLI surfaces. |
| M2 | Real `econpapers update` ✅ done | Explicit update repairs approved managed artifacts without touching user data or silently changing versions. |
| M3 | Real-PDF acceptance corpus ✅ done | All six approved issue #59 cases pass their section-boundary and contamination assertions. |
| M4 | Documentation truth pass | Five sweeps pass their acceptance criteria; every audited claim carries an explicit classification. **Up next — see below.** |
| M5 | Release-readiness verification | Offline, restart, privacy, artifact, and cross-platform checks pass in reproducible environments. M5 is the last milestone before MVP, **not** the sole MVP-complete gate — see M5's exit condition. |
| M6 | Artifact-metadata alignment (post-MVP) | `ManagedModelArtifact` conforms to or reuses `domain.ArtifactManifest`, carrying `redistribution_status`, `update_policy`, and `contains_copyrighted_full_text`. **Outside the MVP-complete gate** — M4 sweep 5 documents these facts in prose first, which is what `AGENTS.md` licensing actually requires. |

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

Acceptance: no document implies full-document ingestion is supported; every
artifact the application downloads has all seven `AGENTS.md` licensing facts
documented in prose; and the residual schema gap points at M6 by name rather
than at "later".

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

M5 is the last milestone before MVP, but passing it does not by itself make
the MVP complete. Gate 0's decisions must also be *propagated* (M4 sweeps 1–2),
not merely decided.

MVP is complete only when Gate 0 is resolved, M1–M5 acceptance criteria pass,
documentation is current, artifact licenses and release procedure are recorded,
and the final diff contains no unrelated changes. M6 is explicitly **not** part
of this gate.

---

## M6 — Artifact-metadata alignment (post-MVP)

`ManagedModelArtifact` (`domain/model_manifest.py`) neither conforms to nor
reuses `domain.ArtifactManifest` (schema version 1), the artifact-metadata
contract this project already built for this purpose. It carries
`license_name`, `sha256`, `size_bytes`/`expected_size_bytes`, and a source
URL, but not `redistribution_status`, `update_policy`, or
`contains_copyrighted_full_text`.

Scope: reconcile the two types — conform, reuse, or deliberately document why
they stay separate — with the manifest schema bump, migration, and tests that
implies. Consider `runtime_manifest.py` at the same time; it has the same
question.

Not in the MVP gate. M4 sweep 5 documents the licensing facts in prose, which
is what `AGENTS.md`'s corpus-and-licensing section actually requires; M6 is
about the type carrying them structurally so `update`/`status` could read them
rather than a human reading a document.
