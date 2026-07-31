# Generation Contract Specification

## Purpose and boundary

The generation contract defines the narrow, backend-independent boundary by
which application services can invoke a local language-model adapter. The
contract does not select a model, runtime, prompt, artifact, or CLI workflow.
Issue 12 implements one concrete adapter behind this unchanged boundary, while
Issue 13 remains responsible for real-model evaluation and default selection.

```text
Application Services -> Generator Protocol -> Replaceable Local Adapter
                            |
                     GenerationRequest
                            |
                validate_generation_response
                            |
                    GenerationResponse
```

The contract and validator are pure in-memory operations. They perform no
network or filesystem I/O and have no dependency on `llama.cpp`, GGUF, or any
model package.

## Request contract

`GenerationRequest` contains:

- `question`: a non-empty string, normalized by stripping only outer
  whitespace; and
- `evidence`: an immutable tuple of `RetrievalEvidence` objects.

Empty evidence is valid input so the absence of retrieval results can flow to
an explicit abstention. Non-empty evidence must satisfy the existing retrieval
result contract, including contiguous one-based ranks, unique passage identity,
score ordering, deterministic tie-breaking, and a uniform non-empty retrieval
method. This preserves each paper ID, passage ID, rank, score, retrieval method,
and passage boundary supplied to generation.

Direct construction requires a tuple. `from_mapping()` accepts JSON-array
containers (`list` or `tuple`), validates each item, and normalizes the sequence
to a tuple. `to_mapping()` emits a JSON-native list.

## Response contract

`GenerationResponse` contains:

- `answer_text`: non-empty answer or abstention prose;
- `citations`: an immutable tuple of existing `Citation` domain objects;
- `generation_method`: a non-empty adapter-defined identity;
- `abstained`: an explicit boolean state;
- `abstention_reason`: `insufficient_evidence` or `null`; and
- `finding_kinds`: a duplicate-free tuple containing zero or more of
  `descriptive` and `causal`.

`FindingKind` is backend-declared answer-level metadata. Structural validation
can verify only that the values are well formed. It cannot prove that prose is
correctly characterized as descriptive or causal.

Direct construction requires tuple fields and enum objects. Mapping parsing
accepts JSON lists and enum strings, then normalizes them to immutable tuples
and enums. Canonical mapping output emits lists and enum string values, so the
following round trip is supported:

```text
object -> to_mapping() -> json.dumps() -> json.loads() -> from_mapping()
```

## Citation identity, membership, and order

Citation IDs are derived deterministically from supplied retrieval rank:

```text
rank 1 -> e1
rank 2 -> e2
...
```

For a non-abstaining response, `validate_generation_response()` builds the
allowed citation triples from the request evidence. Every returned
`(citation_id, paper_id, passage_id)` must exactly match its corresponding
supplied passage. Unknown IDs, mismatched paper or passage IDs, duplicate
citation IDs, and duplicate `(paper_id, passage_id)` references are rejected.

Response citations must appear in ascending supplied-evidence rank order.
Relevant ranks may be omitted, but remaining citations cannot be reordered.
The validator rejects malformed ordering with an actionable error and never
silently sorts adapter output.

These checks prove only that cited passages were supplied to the generator and
that their structured identities match. Response-level citations do not prove
that a particular sentence is supported by a particular citation. Claim-level
citation association, inline citation rendering, and semantic grounding
evaluation remain later work.

## Abstention

An answered response must:

- set `abstained` to `false`;
- set `abstention_reason` to `null`;
- receive non-empty request evidence; and
- cite at least one supplied passage.

An abstaining response must:

- set `abstained` to `true`;
- use `abstention_reason="insufficient_evidence"`;
- contain non-empty user-facing abstention text;
- contain no citations; and
- contain no finding kinds.

Empty request evidence requires abstention. A generator may also abstain when
evidence exists but it judges that evidence insufficient. Runtime failures,
missing artifacts, and adapter errors remain exceptions rather than being
misrepresented as insufficient-evidence abstentions.

## Mechanical and semantic responsibilities

The validator mechanically enforces schema correctness, evidence structure,
citation membership and order, and abstention-state consistency. These checks
do not establish factual correctness, claim-to-citation support, evidence
sufficiency, causal validity, or faithful treatment of uncertainty,
limitations, and disagreement.

Model adapters must receive instructions for those semantic duties, and
generation evaluation must measure them with representative synthetic cases.
Issue 12 adds that model-independent benchmark and a human-review procedure;
structurally valid output can still be substantively wrong.

## Protocol

All replaceable adapters implement:

```python
class Generator(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResponse: ...
```

`Generator` is runtime-checkable for structural discovery only. Runtime
protocol membership does not validate the method signature, return type, or
generation semantics. Callers must pass the returned object through
`validate_generation_response()` before display or further orchestration.

## Errors

All generation contract failures inherit from `ValueError`:

```text
ValueError
└── GenerationContractError
    ├── GenerationRequestValidationError
    └── GenerationResponseValidationError
```

Errors identify the malformed field, evidence position, citation ID, expected
paper or passage identity, rank-order violation, or inconsistent abstention
state.

## Concrete Issue 12 adapter

`LlamaCppGenerator` preserves this contract exactly. Its model-facing schema
contains only `answer_text`, `citation_ids`, `abstained`,
`abstention_reason`, and answer-level `finding_kinds`. The adapter resolves
rank-derived citation IDs to existing authoritative `Citation` objects,
supplies `generation_method`, creates `GenerationResponse`, and invokes
`validate_generation_response()`.

This does not create claim-level citation associations. Human semantic review
may judge whether each substantive claim is supported by at least one returned
response-level citation, but the contract does not mechanically associate
citations with sentences.

The adapter uses explicit local executable and model paths and performs no
downloads. Runtime, prompt, schema, privacy, failure, benchmark, artifact, and
evaluation details are in
[`docs/local-generation-evaluation.md`](local-generation-evaluation.md).

## Deferred work

Issue 13 must run the approved candidates and either select an initial
replaceable default or explicitly defer that decision. Model download,
retrieval orchestration, CLI integration, conversational state, PDF ingestion,
OCR, conversion, storage, segmentation, and indexing remain unimplemented.

The repository-root `papers/` directory is private future ingestion input. It
is ignored by Git and is not a public corpus, test fixture, package resource, or
hard-coded application path.
