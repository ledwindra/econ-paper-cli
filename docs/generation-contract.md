# Generation Contract Specification

## Purpose and boundary

The generation contract defines the narrow, backend-independent boundary by
which application services can invoke a local language-model adapter. The
contract does not select a model, runtime, prompt, artifact, or CLI workflow.
Issue 12 implements one concrete adapter behind this unchanged boundary, while
Issue 13 evaluated the eligible candidates and deferred default selection.

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
- `abstention_reason`: `insufficient_evidence` or `null`;
- `finding_kinds`: a duplicate-free tuple containing zero or more of
  `descriptive` and `causal` — optional metadata, empty whenever the backend
  asserts none; and
- `claims`: an immutable tuple of `GeneratedClaim`, each a single
  self-contained sentence with the `citation_ids` supporting that sentence
  alone. Empty for backends that emit only flat prose.

Every identifier a claim cites must also appear in `citations`, so the
per-claim attribution and the response citation list can never disagree about
which passages back the answer.

`FindingKind` is a two-value enum — `descriptive` and `causal` — declared by
the backend at the response level. It is optional metadata, not a guarantee
the tool provides. Nothing derives it from the evidence or checks it against
the evidence: the model asserts it, and validation is purely structural. A
response therefore never establishes that a finding *is* causal; it records
only what the backend claimed. Semantic validation of that characterization
is **[planned]** and does not exist today.
[`docs/local-generation-evaluation.md`](local-generation-evaluation.md)
scores causal-versus-descriptive characterization through blinded human
review, which is a manual evaluation procedure, not a runtime check.

"Optional" is literal, in three separate ways:

- an abstaining response must carry no finding kinds at all, and
  `validate_generation_response` rejects one that does;
- an answered response is not required to carry any — the empty tuple is
  legal, and `chat`/the interactive shell then render `Finding Kinds: N/A`;
  and
- `chat`/the shell clear the label whenever any claim was withheld (see
  below).

The serialized field is always required: `from_mapping` rejects a mapping with
no `finding_kinds` key. What is optional is the label itself, expressed as an
empty list. Nothing in the contract makes a response "carry" a
descriptive-or-causal characterization.

Structural validation of the tuple itself checks that every element is a legal
`FindingKind` and that there are no duplicates; the abstention-state check
noted above adds the further constraint that an abstaining response carry
none. Those are the only checks the label receives. The default
`llama.cpp` adapter narrows the space further by enumerating the five legal
arrays in its `generation-v3` grammar, so a degenerate repeat is not even
representable. That grammar constraint belongs to the adapter, not to the
generation protocol — a replacement adapter satisfies the protocol with no
grammar at all.

Because `finding_kinds` is answer-level rather than per-claim, it cannot
survive claim-level withholding intact: if cross-paper grounding withholds
one claim from a multi-claim response, the surviving answer may no longer
match the original `finding_kinds` label (the withheld claim, not the
surviving ones, could have been the causal one). `chat`/the interactive
shell handle this conservatively — see `services/chat_command.py` and
`services/interactive_shell.py` — by reporting no finding kind at all
whenever any claim was withheld, rather than risk mislabeling the answer
that is actually shown. An answered response with partial withholding
therefore prints `Finding Kinds: N/A`; a response whose claims were *all*
withheld prints no finding-kind line at all, because it shows no answer to
characterize. Attributing `finding_kinds` per claim instead of per response
would require a schema change and is not implemented.

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

## Claim grounding

`check_response_grounding()` returns one `ClaimGroundingResult` per claim, in
claim order. A claim is *ungrounded* when it uses a term distinctive to a paper
it does not cite — see `domain/claim_grounding.py` for the scoping rules
(paper-level, not passage-level; terms shared across several papers are field
vocabulary and are ignored; question wording is exempt).

The check is structural, never semantic: it does not ask whether a claim is
true, only whether the words it uses could have come from the paper it points
at. Responses without claims yield no verdicts, and an empty result must never
be read as "verified".

`chat` and the interactive shell both withhold ungrounded claims from the
answer. When every claim is withheld the outcome is `withheld`, which is
deliberately distinct from `abstained`: the generator did produce an answer,
and reporting an abstention would tell the user the library had nothing to say.

## Concrete adapter

`LlamaCppGenerator` preserves this contract exactly. Its model-facing schema
(`generation-v3`) contains only `claims`, `abstained`, `abstention_reason`, and
answer-level `finding_kinds`. The model emits no separate citation list: the
adapter derives `citations` from what the claims cite, in ascending evidence
rank, so a claim/citation disagreement is unrepresentable rather than merely
invalid. It resolves rank-derived citation IDs to existing authoritative
`Citation` objects, joins the claim texts into `answer_text`, supplies
`generation_method`, creates `GenerationResponse`, and invokes
`validate_generation_response()`.

v2 carried a single flat `answer_text` and a separate bag of citation IDs. That
shape could not express which sentence came from which paper, and a small local
model routinely welded two studies into one fluent description that passed
every structural check. Claim-level binding is what makes that misattribution
both representable and detectable.

The adapter uses explicit local executable and model paths and performs no
downloads. Runtime, prompt, schema, privacy, failure, benchmark, artifact, and
evaluation details are in
[`docs/local-generation-evaluation.md`](local-generation-evaluation.md).

## Status note

At the time this contract was first written, Issue 13 had evaluated two
eligible candidates and explicitly deferred the initial replaceable default
because neither passed the first mechanical run; model download, retrieval
orchestration, CLI integration, conversational state, PDF ingestion,
conversion, storage, segmentation, and indexing were all unimplemented
(**[historical]**). That is no longer current: retrieval orchestration, CLI
integration (`chat`, bare `econpapers`), conversational state
(`domain/conversation.py`), PDF
ingestion/conversion/storage/segmentation (`analyze`, the early-section
library), indexing (see below), and model/runtime download (`econpapers
setup`, see
[`docs/managed-runtime-provisioning.md`](managed-runtime-provisioning.md)
and `domain/model_manifest.py`) are all implemented — see
[`docs/roadmap.md`](roadmap.md) for current status. "Indexing" in that list
means **[current]** in-memory BM25 index construction only, over the
passages `analyze` has persisted: `chat` builds one per invocation, after
finding at least one stored early-section record; the interactive shell
builds one when its session opens, for a paper library that is non-empty
and so has a loadable corpus. Those conditions differ — a library holding
only legacy paper records opens a shell corpus but still sends `chat` to
its empty-library outcome. When a command's respective gate fails, that
command does not build one. `analyze` itself constructs no retriever and
builds no index, and a
persisted or bundled retrieval index is **[planned]** and does not ship. OCR
and full-document conversion (beyond Abstract/Introduction) remain
unimplemented. The
`LlamaCppGenerator` adapter described above still performs no downloads
itself; downloading is a separate, adapter-independent provisioning step.

The repository-root `papers/` directory is private future ingestion input. It
is ignored by Git and is not a public corpus, test fixture, package resource, or
hard-coded application path.
