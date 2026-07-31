# Local generation adapter and evaluation

## Decision status

Issue 12 implements a concrete, replaceable local-generation adapter and a
model-independent evaluation framework. It does not approve a default model.
Issue 13 is the empirical gate for real-model evaluation and either a
documented default-model decision or an explicit deferral.

`llama.cpp` release `b10199` is pinned only for Issue 12 compatibility work and
the initial Issue 13 comparison. Executable and model paths remain
configurable. This pin is not a permanent product-runtime commitment. A runtime
upgrade requires new checksums, compatibility testing, and renewed evaluation.

## Implemented adapter

`LlamaCppGenerator` implements the existing `Generator` protocol by invoking a
configured `llama-cli` executable once per request. It has no Python inference
dependency and performs no artifact download.

The adapter:

- requires explicit executable and model paths;
- verifies the configured model size and SHA-256 before first use;
- checks that `llama-cli --version` contains the configured runtime marker;
- passes the question and evidence through a permission-restricted temporary
  prompt file rather than command-line text;
- uses the packaged `generation-v1` prompt and JSON schema;
- assigns evidence identifiers from supplied ranks (`e1`, `e2`, and so on);
- runs with `shell=False`, `--offline`, disabled prompt display and logging, and
  no Hugging Face repository or model-URL flags;
- uses explicit context, output, seed, sampler, and thread settings;
- uses the GGUF's embedded Jinja chat template with reasoning disabled so the
  candidates share a non-thinking structured-output task;
- disables context shifting so oversized input fails instead of silently
  discarding evidence;
- rejects empty, malformed, partial, trailing, or extra output;
- bounds captured stdout and stderr;
- supports timeouts and an injected cancellation check;
- resolves returned citation IDs to authoritative existing `Citation` objects;
- supplies a path-independent `generation_method`;
- constructs `GenerationResponse`; and
- calls `validate_generation_response()` before returning.

The model-facing JSON contains only `answer_text`, `citation_ids`, `abstained`,
`abstention_reason`, and answer-level `finding_kinds`. It does not contain
claim-level citations. Structural citation validation cannot prove that a
particular sentence is supported by a particular citation.

Operational problems such as missing files, checksum mismatches, incompatible
artifacts, process failures, timeouts, cancellation, invalid UTF-8, and invalid
model output remain typed exceptions. They are never converted into
insufficient-evidence abstentions.

### Privacy boundary

Questions and evidence are written to a new temporary directory and files
created with mode `0600` where POSIX permissions apply. Prompt, schema, stdout,
and stderr files are cleaned by context managers after success or failure,
including timeout and cancellation paths. Normal exception messages never
include captured model output.

The adapter removes common Hugging Face token and repository variables from the
child environment and sets `LLAMA_ARG_OFFLINE=1`. Tests verify command
construction and the absence of download-enabled flags. Those tests cannot
prove that arbitrary native code will never attempt network access; the pinned
runtime, explicit local paths, and runtime offline option form the operational
control.

The current one-shot adapter reloads the model for each request. Issue 13 must
measure the resulting total latency. A persistent `llama-server` process is a
possible later adapter optimization, not part of Issue 12.

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
asset and verify its compressed archive checksum, or build `llama-cli` locally
from the exact source commit. The archive digest is not the digest of the
extracted executable. Readiness therefore verifies the runtime version marker
and model artifact checksum; it does not misapply the archive digest to the
extracted binary.

Runtime and model installation is manual. The adapter never uses `-hf`, model
URLs, automatic updates, Docker, or a package-managed download.

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
Compatibility is still subject to Issue 13 readiness and smoke testing. The
nominal model contexts are 8,192 tokens for SmolLM2 and 32,768 for the Qwen
models; the controlled comparison uses a common 4,096-token context and the
chat template embedded in each GGUF. Installation is manual from the immutable
manifest URL followed by size and SHA-256 verification.

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
  --executable /absolute/path/to/llama-cli \
  --model /absolute/path/to/model.gguf \
  --model-id candidate-id \
  --model-size-bytes EXPECTED_BYTES \
  --model-sha256 EXPECTED_SHA256 \
  --candidate-code candidate-a \
  --output generation-results/candidate-a.json \
  --review-output generation-results/candidate-a-review.json
```

The technical report records the benchmark and configuration fingerprints,
model digest, runtime identity, machine profile, thread configuration,
readiness-validation time, and per-run total latency. It marks initialization
time, time to first token, token throughput, peak child RSS, and complete
installation footprint unavailable when the portable pinned subprocess path
cannot measure them reliably. These resource observations are not correctness
gates.

The separate review file omits model/runtime identity, randomizes cases with a
recorded seed, and contains blank scores, vetoes, notes, reviewer count, and
procedure fields. The person coordinating Issue 13 must keep the mapping from
candidate code to artifact away from reviewers until scoring is complete.

No hardware requirement may be derived from this synthetic benchmark or one
development machine. Any later user guidance must name the measured hardware,
OS, CPU, RAM, runtime, artifact digest, threads, prompt, and inference settings.
