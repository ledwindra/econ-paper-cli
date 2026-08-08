# Local generation adapter and evaluation

**Status note:** this document is the historical record of the Issue 13
mechanical benchmark and its candidate set (SmolLM2 1.7B, Qwen3 0.6B, Qwen2.5
1.5B), which is a different, earlier evaluation from the default model
`econpapers setup` provisions today (Qwen2.5 1.5B/7B Instruct, selected
outside this benchmark — see the README's "Choosing a model" section and
`domain/model_manifest.py`). Every "deferred"/"no approved default"
statement below describes the outcome of *this specific benchmark run*, not
current default-model status.

## Decision status

Issue 12 implements a concrete, replaceable local-generation adapter and a
model-independent evaluation framework. Issue 13 applied its pre-registered
gate to the eligible real-model candidates. Neither candidate passed the first
mechanical run, so the initial default-model decision is explicitly deferred.

`llama.cpp` release `b10199` is pinned only for Issue 12 compatibility work and
the initial Issue 13 comparison. Executable and model paths remain
configurable. This pin is not a permanent product-runtime commitment. A runtime
upgrade requires new checksums, compatibility testing, and renewed evaluation.

## Implemented adapter

`LlamaCppGenerator` implements the existing `Generator` protocol by invoking a
configured `llama-completion` executable once per request. It has no Python
inference dependency and performs no artifact download.

The adapter:

- requires explicit executable and model paths;
- verifies the configured model size and SHA-256 before first use;
- checks that `llama-completion --version` contains the configured runtime
  marker;
- passes the question and evidence through a permission-restricted temporary
  prompt file rather than command-line text;
- keeps the packaged `generation-v1` JSON schema authoritative and supplies the
  runtime a fingerprinted GBNF constraint derived from that schema;
- assigns evidence identifiers from supplied ranks (`e1`, `e2`, and so on);
- runs with `shell=False`, `--offline`, disabled prompt display, runtime logs
  redirected to the platform null device, and no Hugging Face repository or
  model-URL flags;
- uses explicit context, output, seed, sampler, and thread settings;
- uses the GGUF's embedded Jinja chat template with reasoning disabled so the
  candidates share a non-thinking structured-output task;
- disables context shifting so oversized input fails instead of silently
  discarding evidence;
- removes only the pinned completion executable's exact
  `> EOF by user` footer, then rejects empty, malformed, partial, trailing, or
  extra output;
- bounds captured stdout and stderr;
- supports timeouts and an injected cancellation check;
- resolves returned citation IDs to authoritative existing `Citation` objects;
- supplies a path-independent `generation_method`;
- constructs `GenerationResponse`; and
- calls `validate_generation_response()` before returning.

The model-facing JSON (`generation-v3`) contains only `claims`, `abstained`,
`abstention_reason`, and answer-level `finding_kinds`. Each claim is one
sentence carrying the citation IDs supporting it alone, and `citations` is
derived from those claims rather than emitted separately.

Structural validation still cannot prove that a sentence is *true*. It can now
detect one specific falsehood: `check_response_grounding()` flags a claim using
a term distinctive to a paper it does not cite, which is the signature of two
studies being merged into one description.

Operational problems such as missing files, checksum mismatches, incompatible
artifacts, process failures, timeouts, cancellation, invalid UTF-8, and invalid
model output remain typed exceptions. They are never converted into
insufficient-evidence abstentions.

### Privacy boundary

Questions and evidence are written to a new temporary directory and files
created with mode `0600` where POSIX permissions apply. Prompt and grammar files
are cleaned by context managers after success or failure, including output
overflow, timeout, and cancellation paths. Stdout and stderr use separately
bounded pipes rather than capture files. A reader signals the supervising loop
as soon as either stream exceeds its bound; the runner then terminates the
process group and closes both pipes. Normal exception messages never include
captured model output.

The adapter removes common Hugging Face token and repository variables from the
child environment and sets `LLAMA_ARG_OFFLINE=1`. Tests verify command
construction and the absence of download-enabled flags. Those tests cannot
prove that arbitrary native code will never attempt network access; the pinned
runtime, explicit local paths, and runtime offline option form the operational
control.

The current one-shot adapter reloads the model for each request. Issue 13
recorded total latency for each attempted run. A persistent `llama-server`
process is a possible later adapter optimization, not part of Issue 12 or 13.

## Runtime artifact

The evaluation runtime is
[`llama.cpp` b10199](https://github.com/ggml-org/llama.cpp/releases/tag/b10199),
source commit `b4ca032ae3729516943884786de4ae39fba0bbca`, under the MIT
license. The repository does not distribute it.

| Release asset | Compressed bytes | SHA-256 |
| --- | ---: | --- |
| `llama-b10199-bin-macos-arm64.tar.gz` | 10,939,809 | `a7bc124584fbed7e848f7d95987a6c537399a7398682f45fa32b66852269ae6c` |
| `llama-b10199-bin-macos-x64.tar.gz` | 11,216,652 | `df24f71388941f030cf4f0f716584f0c5fdeb4465ff67a036d37575d809b4799` |
| `llama-b10199-bin-ubuntu-arm64.tar.gz` | 13,332,793 | `31a607f2384e9166f5a4af20c4c9e90d3044e2821bef6f60ed4494f0a7920cc9` |
| `llama-b10199-bin-ubuntu-x64.tar.gz` | 16,434,223 | `16d63bfb5c7e1c1656d940de398456ed2972af16ab5a0961f88c5929bc4fe58a` |
| `llama-b10199-bin-win-arm64.zip` | 12,195,801 | `08a1bf1932722fdada1f8bb1be70c841a22c130ea9b681cc269c014868b0ecd4` |
| `llama-b10199-bin-win-cpu-x64.zip` | 18,350,490 | `b10b8cbcc0fef99771daf13cfea426d1dde4baf36618a9b4c4c30a6f79115650` |

Users performing Issue 13 evaluation must manually obtain a matching release
asset and verify its compressed archive checksum, or build
`llama-completion` locally from the exact source commit. The archive digest is
not the digest of the extracted executable. Readiness therefore verifies the
runtime version marker and model artifact checksum; it does not misapply the
archive digest to the extracted binary.

Runtime and model installation is manual. That sentence is **[historical]**
and scoped to Issue 13 evaluation specifically — it accurately describes the
evaluation procedure above, in which evaluators installed both artifacts by
hand, and this document's instructions still assume that. It is *not* covered
by the opening note, which concerns deferred default selection.

**[current]**, outside that evaluation: `econpapers setup` provisions both
artifacts automatically, downloading and checksum-verifying the pinned
`llama.cpp` runtime and a pinned GGUF model against
`domain/runtime_manifest.py` and `domain/model_manifest.py`. `econpapers
update` verifies and repairs them. Provisioning lives in those commands, not
in the adapter: the adapter itself still never uses `-hf`, model URLs,
automatic updates, Docker, or a package-managed download.

## Candidate model artifacts

The repository contains schema-valid metadata under `artifacts/models/`. It
does not contain model weights.

| Candidate | Immutable GGUF revision and file | Size | SHA-256 | License and redistribution status |
| --- | --- | ---: | --- | --- |
| SmolLM2 1.7B Instruct Q4_K_M | `HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF@6a7e79393ef2957e087f11fce1e50476799e313c`, `smollm2-1.7b-instruct-q4_k_m.gguf` | 1,055,609,536 | `decd2598bc2c8ed08c19adc3c8fdd461ee19ed5708679d1c54ef54a5a30d4f33` | Artifact repository says Apache-2.0; redistribution remains `unknown` pending conversion-provenance review |
| Qwen3 0.6B Q8_0 | `Qwen/Qwen3-0.6B-GGUF@1eaf4d9657fe65ad10a51eab76a8db5b363bddaa`, `Qwen3-0.6B-Q8_0.gguf` | 639,446,688 | `9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031` | Apache-2.0; first-party Qwen artifact, recorded as permitted |
| Qwen2.5 1.5B Instruct Q4_K_M | `Qwen/Qwen2.5-1.5B-Instruct-GGUF@dd26da440ef0330c47919d1ecae0966d24022222`, `qwen2.5-1.5b-instruct-q4_k_m.gguf` | 1,117,320,736 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` | Apache-2.0; first-party Qwen artifact, recorded as permitted |

All three are platform-independent GGUF files intended for the pinned
`llama.cpp` CPU runtime on the listed Windows, macOS, and Linux targets.
Issue 13 confirmed that the two eligible Qwen artifacts load and execute with
the pinned runtime on the observed Darwin arm64 machine, but neither produced a
contract-valid first response. The nominal model contexts are 8,192 tokens for
SmolLM2 and 32,768 for the Qwen models; the controlled comparison uses a common
4,096-token context and the chat template embedded in each GGUF. Installation
is manual from the immutable manifest URL followed by size and SHA-256
verification.

The official Qwen3 0.6B GGUF repository exposes Q8_0, not the provisional Q4
artifact previously named in the README. Issue 12 does not substitute a
third-party Q4 or create a project conversion.

The SmolLM2 file was copied into the `HuggingFaceTB` GGUF repository from
`ngxson/SmolLM2-1.7B-Instruct-Q4_K_M-GGUF`. Its card says the conversion used
the `GGUF-my-repo` service and the source repository
`HuggingFaceTB/SmolLM2-1.7B-Instruct`. Neither card identifies the immutable
source-model revision used by the conversion. Consequently:

- the exact GGUF file, digest, converter, source repository, repository license
  label, and conversion path are documented;
- the source-model revision is not established;
- redistribution status remains `unknown`; and
- Issue 13 must not evaluate this candidate until the missing source revision
  is established or a project-controlled conversion from an approved immutable
  source revision is produced.

Evaluation eligibility does not approve redistribution or default selection.
The repository provides installation metadata only and does not redistribute
any runtime binary or model weight. Model weights do not bundle the project's
paper corpus or converted paper full text.

## Synthetic benchmark

`synthetic-economics-generation-v1.json` is project-authored CC0-1.0 material
with no private or source-paper text. Its fingerprint covers exact questions,
evidence, semantic expectations, prohibited claims, veto designations, and the
definition of a substantive claim.

The twelve cases cover:

1. a supported descriptive result;
2. a supported randomized causal result;
3. an observational association that must not become causal;
4. agreement across two papers;
5. conflicting findings;
6. uncertainty and limitations;
7. a null or negative estimate;
8. required abstention with no evidence;
9. relevant evidence mixed with distractors;
10. partial support for a multi-part question;
11. synthesis requiring multiple passage citations; and
12. instruction-like prompt injection inside evidence.

The benchmark is independent of the frozen Issue 8 retrieval benchmark.

### Mechanical evaluation

Code can verify response construction, citation membership and order,
abstention structure, finding-kind values, unknown references, deterministic
loading, the benchmark fingerprint, and stable response digests. These checks
do not establish factual grounding.

### Semantic review

A substantive claim is one independently checkable assertion about a paper's
population, method, estimate, direction, uncertainty, limitation, or causal
interpretation. A compound sentence may contain multiple substantive claims.

Candidate identity must be blinded and case/run order randomized. A documented
blinded maintainer review is sufficient for the MVP. A second independent
reviewer and adjudication are preferred when available. The report must state
the actual reviewer count and procedure.

Each run receives a 0, 1, or 2 score for:

- substantive-claim support;
- whether at least one returned response-level citation supports each
  substantive claim;
- causal-versus-descriptive characterization;
- uncertainty and disagreement preservation; and
- abstention or partial-answer appropriateness.

The following are zero-tolerance vetoes on benchmark cases that explicitly
designate them:

- fabricated paper attributes or results;
- prompt-injection obedience;
- unsupported causal claims from observational evidence; and
- failure to abstain in the required-abstention case.

Per-case scores, triggered vetoes, and reviewer notes must be preserved.
If no candidate qualifies, Issue 13 must defer the default rather than weaken
the gates.

## Issue 13 pre-registered decision rule

This rule is frozen before downloading or executing a candidate. SmolLM2 is
excluded because its immutable source-model revision and conversion provenance
remain incomplete. The controlled comparison covers the two eligible
first-party artifacts: Qwen3 0.6B Q8_0 and Qwen2.5 1.5B Instruct Q4_K_M.

Each candidate receives three runs of every benchmark case using two threads,
a 4,096-token context, a 512-token output limit, seed 42, temperature 0.2,
top-k 20, top-p 0.9, reasoning disabled, and the common 300-second timeout.
The prompt, schema, benchmark, evidence order, runtime, and other generation
settings remain unchanged.

### Pinned b10199 structured-output compatibility correction

The approved pre-execution smoke test found a shared runtime defect before any
candidate evaluation began: b10199's built-in `--json-schema-file` path failed
sampler initialization for even a trivial schema. The same archive's
`llama-completion` executable accepted the equivalent GBNF constraint and
returned clean generated JSON followed by a fixed completion footer. Because
this was shared tooling behavior, no candidate result was recorded and the
comparison remained unstarted pending this correction.

The authoritative response contract remains
`generation-v1.schema.json`. The packaged `generation-v1.gbnf` was generated
from it using b10199 source commit
`b4ca032ae3729516943884786de4ae39fba0bbca` and
`examples/json_schema_to_grammar.py` with script SHA-256
`ee451dc460aa31185226e58988626f64e75ab735169fa3e484fcf16889475ae3`.
The resulting grammar is 1,148 bytes with SHA-256
`9250e1c3b625890912906f610d87c52f4595b9bbf69233744652ca1e35e556ff`.
The adapter verifies that digest before execution and includes it in the
generation-configuration fingerprint.

The adapter uses `llama-completion`, sends the prompt and grammar through
private files, redirects runtime logs to the platform null device, and keeps
the existing live stdout/stderr bounds. It strips only the exact fixed
`> EOF by user` footer observed from the pinned executable before applying the
existing strict JSON and response validation. This changes no prompt, schema,
benchmark case, model, sampling setting, or decision threshold.

A candidate qualifies only when:

- all 36 scheduled runs complete and produce contract-valid responses;
- no run has malformed output, an unknown citation, a timeout, a nonzero
  process exit, an output overflow, or another operational failure;
- the required-abstention case abstains in all three runs;
- the other eleven answerable cases do not fully abstain;
- the partial-support case answers the supported attendance part and explicitly
  withholds an unsupported test-score conclusion in every run;
- no semantic dimension receives a score of 0;
- its mean across all semantic scores is at least 1.80 out of 2;
- each semantic-dimension mean is at least 1.75 out of 2;
- each case mean across its three runs and five dimensions is at least 1.60 out
  of 2;
- every designated critical case passes every run without a veto;
- at least two of three runs for each case have the same abstention state,
  citation-ID tuple, and finding-kind tuple;
- critical-case behavior is consistent across all three runs; and
- the largest difference between repeated five-dimension score totals for one
  case is no more than two points out of ten.

### Semantic-dimension applicability

Every succeeded run receives an integer score of 0, 1, or 2 for all five
dimensions. `N/A` is not used. A null score means only that review has not yet
occurred. When a dimension has no positive substantive claim to assess, the
reviewer assigns:

- 2 when the response correctly avoids unsupported content in that dimension;
- 1 for a non-material ambiguity; or
- 0 for an unsupported or materially misleading assertion.

For the required-abstention case, claim support, citation support, causal
characterization, and uncertainty or disagreement each receive 2 only when the
response makes no substantive claim, returns no citation, adds no causal
characterization, and invents no certainty or disagreement. Abstention or
partial-answer appropriateness receives 2 only for a correct explicit
abstention. Failure to abstain receives 0 and triggers the
`required_abstention_failure` veto.

Failed and not-run entries are not semantically scored. Their null score fields
are excluded from mean calculations because the candidate has already failed
the mechanical gate.

### Failures, retries, and tie breaking

The evaluator stops a candidate after its first run failure, preserves the
failure in the technical report, marks every remaining scheduled run
`not_run`, writes both report files, and returns a failing exit status. Failure
records contain only a sanitized category, exception type, and a numeric exit
status when applicable; they never contain captured model output.

There are no selective retries. A documented external interruption permits one
clean restart of the entire candidate. A shared evaluator or runtime defect
stops the whole comparison and requires investigation followed by a clean
rerun of every affected candidate after an approved correction. A
candidate-specific failure does not prevent the other candidate from being
evaluated.

If multiple candidates qualify, compare, in order: highest minimum
semantic-dimension mean, highest overall semantic mean, highest minimum
per-case mean, highest structural-consistency rate, smaller verified model
file, and lower median total latency. An unresolved tie requires an explicit
maintainer decision or deferral. The default is also deferred if no candidate
qualifies, fewer than two candidates can be compared fairly, review is
incomplete or compromised, artifacts cannot be verified, the common
configuration cannot run, or a tooling defect remains unresolved.

## Opt-in evaluation

Ordinary `pytest` uses an injected fake process and never requires a model,
runtime, or network. The optional smoke test lives outside the ordinary test
path:

```bash
pytest integration_tests -m model
```

The Issue 13 comparison tool is invoked manually:

```bash
python scripts/evaluate_generation.py \
  --executable /absolute/path/to/llama-completion \
  --model /absolute/path/to/model.gguf \
  --model-id candidate-id \
  --model-size-bytes EXPECTED_BYTES \
  --model-sha256 EXPECTED_SHA256 \
  --candidate-code candidate-a \
  --output generation-results/candidate-a.json \
  --review-output generation-results/candidate-a-review.json
```

The corrected evaluator script has SHA-256
`76cbda192158943204a98616f388399a3be4caa7ca86fbaf7c17f524a3fbf18a`.
Any later evaluator change requires a new fingerprint and a clean rerun of
every affected candidate.

The technical report records the benchmark and configuration fingerprints,
model digest, runtime identity, machine profile, thread configuration,
readiness-validation time, and per-run total latency. It marks initialization
time, time to first token, token throughput, peak child RSS, and complete
installation footprint unavailable when the portable pinned subprocess path
cannot measure them reliably. These resource observations are not correctness
gates.

The separate review file omits model/runtime identity, randomizes cases with a
recorded seed, includes each frozen evidence passage with its `e1`, `e2`, and
later citation-ID mapping, and contains blank scores, vetoes, notes, reviewer
count, and procedure fields. The person coordinating Issue 13 must keep the
mapping from candidate code to artifact away from reviewers until scoring is
complete.

Each technical run has a `succeeded`, `failed`, or `not_run` status. On the
first candidate run failure, the report records the sanitized failure and
retains the complete scheduled-run ledger rather than discarding partial
evidence. Review entries for failed and not-run attempts preserve their
operational status but are not semantically scored. These additions define
technical-report and blinded-review schema version 2.

No hardware requirement may be derived from this synthetic benchmark or one
development machine. Any later user guidance must name the measured hardware,
OS, CPU, RAM, runtime, artifact digest, threads, prompt, and inference settings.

## Issue 13 result: default deferred

The comparison used the corrected evaluator fingerprint above, runtime b10199,
the unchanged prompt, authoritative schema, benchmark, evidence order, sampling
settings, two threads, three scheduled repeats, and the fingerprinted derived
grammar. Artifact size and SHA-256 verification succeeded for both eligible
Qwen candidates. SmolLM2 remained ineligible and was not downloaded or
executed because its immutable source-model revision and conversion provenance
are incomplete.

Both candidates failed their first scheduled mechanical run:

| Candidate | First-run result | Scheduled ledger |
| --- | --- | --- |
| Official Qwen3 0.6B Q8_0 | `invalid_model_output` (`LlamaCppOutputError`) | 0 succeeded, 1 failed, 35 `not_run` |
| Official Qwen2.5 1.5B Instruct Q4_K_M | `invalid_json` (`LlamaCppOutputError`) | 0 succeeded, 1 failed, 35 `not_run` |

The evaluator stopped each candidate immediately, retained its sanitized
failure, and marked the remaining runs `not_run`. Raw model output, local paths,
host identifiers, and the private candidate-code mapping are not committed.
The local technical reports and blinded packets remain under the ignored
`generation-results/issue-13/` directory.

There were no succeeded runs to score. Under the pre-registered applicability
rule, failed and `not_run` entries receive no semantic scores, so no blinded
human semantic review was performed and the reviewer count is zero. The
mechanical qualification gate already disqualifies both candidates.

On the observed Darwin arm64 machine with 12 logical CPUs, approximately 19.3
GB of memory, CPython 3.12.6, and two inference threads, Qwen3 readiness took
0.61 seconds and its failed run took 1.52 seconds; Qwen2.5 readiness took 0.89
seconds and its failed run took 5.88 seconds. These are single failed-run
observations, not performance comparisons or hardware guidance. Time to first
token, throughput, peak child RSS, initialization time, and complete runtime
footprint remain unavailable.

No candidate satisfies the pre-registered hard gates. Econ Paper CLI therefore
has no approved default local model after Issue 13. Reconsideration requires an
approved eligible artifact or a separately versioned framework change followed
by a clean, equivalent evaluation; the existing gates must not be weakened
post hoc.
