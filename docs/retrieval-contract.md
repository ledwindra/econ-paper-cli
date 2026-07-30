# Retrieval Contract Specification

This document defines the backend-independent contract that all future retrieval adapters in `econ-paper-cli` must satisfy.

## Overview

The retrieval contract isolates literature search logic from concrete search algorithms, vector stores, embedding models, and index structures. Higher-level application services interact with retrieval implementations exclusively through pure domain requests, protocols, and result validation functions.

## Architectural Boundary

```text
Application Services -> Retriever Protocol -> Concrete Retrieval Adapter
                            |
                     RetrievalRequest
                            |
                 validate_retrieval_results
                            |
                 tuple[RetrievalEvidence, ...]
```

Retrievers are configured with their target corpus or index during construction. The corpus or index is **not** passed into individual `retrieve(...)` invocations.

`BM25Retriever` (`econ_paper_cli.adapters.bm25.BM25Retriever`) is the first concrete adapter conforming to this protocol. The protocol remains strictly backend-independent, and implementing `BM25Retriever` does not select it as the permanent default backend.

## Components

### 1. `RetrievalRequest`

An immutable dataclass (`econ_paper_cli.protocols.retrieval.RetrievalRequest`) representing a search query:

- `query` (`str`): Non-empty search string. Leading and trailing whitespace are stripped consistently upon construction. Internal whitespace, newlines, and punctuation are preserved.
- `top_k` (`int`): Maximum number of evidence passages to return. Direct Python construction `RetrievalRequest(query="...")` defaults `top_k` to `10`. Must be an integer `>= 1`. Boolean values, floats, and strings are rejected.

Mapping conversion via `RetrievalRequest.from_mapping` requires **both** `query` and `top_k` as exact required fields. Mappings omitting `top_k` (or `query`) or providing unknown fields are rejected with `RetrievalRequestValidationError`. Canonical serialization (`to_mapping()`) always emits both `query` and `top_k`.

### 2. `Retriever` Protocol

A `@runtime_checkable` Python `Protocol` (`econ_paper_cli.protocols.retrieval.Retriever`):

```python
class Retriever(Protocol):
    def retrieve(
        self,
        request: RetrievalRequest,
    ) -> tuple[RetrievalEvidence, ...]: ...
```

> **Note on `@runtime_checkable`:** Runtime protocol checking verifies structural presence of the required `retrieve` attribute but does not validate the full method signature, argument types, return type, or retrieval semantics. Application correctness must not rely on `isinstance(obj, Retriever)`.

### 3. Result Validation (`validate_retrieval_results`)

A pure validation function (`econ_paper_cli.protocols.retrieval.validate_retrieval_results`) that enforces output invariants on returned result tuples:

- **Type Safety:** The returned result set must be a `tuple` containing only `RetrievalEvidence` instances.
- **Result Count:** `len(results)` must be `<= request.top_k`. An empty tuple `()` is valid when no matches are found.
- **Contiguous Ranks:** Ranks must start at `1` and be strictly contiguous (`rank == index + 1` for 0-indexed position `index`).
- **Score Direction:** Results must be ordered by non-increasing score (`results[i].score >= results[i + 1].score`). Higher scores represent stronger matches.
- **Deterministic Tie-Breaking:** When two results have equal scores (`results[i].score == results[i + 1].score`), they must be ordered by ascending `passage_id` string order (`results[i].passage.passage_id < results[i + 1].passage.passage_id`).
- **Exact Duplicate Policy:** `passage_id` values across the returned tuple must be strictly unique. Submitting duplicate passage IDs raises `RetrievalResultValidationError`.
- **Near-Duplicate Suppression:** Near-duplicate or semantic passage suppression is an adapter/evaluation responsibility and is deferred to concrete retrieval implementation testing.
- **Uniform Non-Empty Method:** Every returned `RetrievalEvidence` object must have a non-empty `retrieval_method` string (not `None` or whitespace-only), and all items in a single returned tuple must share the exact same `retrieval_method` string label.

## Determinism

For the same:
- `RetrievalRequest`;
- configured corpus or index;
- adapter implementation; and
- adapter configuration;

a conforming `Retriever` must return identical result identities, ordering, ranks, scores, and `retrieval_method` values.

## Backend-independent evaluation

`econ_paper_cli.evaluation.retrieval` evaluates any conforming `Retriever`
against a validated `RetrievalBenchmark`. The evaluator uses only
`RetrievalRequest`, returned `RetrievalEvidence`, `validate_retrieval_results`,
and stable passage identities; it does not depend on BM25 scores or internals.

The first frozen benchmark contains 25 synthetic economics questions with
binary relevance judgments and calculates Hit Rate, macro Recall, and MRR at
`k=1,3,5`. Before evaluation, it verifies the exact canonical passage identity
and text fingerprint of the corpus. Full fixture provenance, formulas,
thresholds, baseline results, and limitations are documented in
[`docs/retrieval-evaluation.md`](retrieval-evaluation.md).

Passing this benchmark is a regression signal for its small synthetic corpus.
It is not evidence that an adapter is optimal, and Issue 8 does not select a
default retrieval backend.

## Exception Hierarchy

All protocol validation errors inherit from `RetrievalContractError`, preserving compatibility with standard Python `ValueError`:

```text
ValueError
└── RetrievalContractError
    ├── RetrievalRequestValidationError
    └── RetrievalResultValidationError
```
