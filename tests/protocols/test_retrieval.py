"""Tests for the backend-independent retrieval protocol and contract validation."""

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from econ_paper_cli.domain import Passage, RetrievalEvidence
from econ_paper_cli.protocols import (
    RetrievalContractError,
    RetrievalRequest,
    RetrievalRequestValidationError,
    RetrievalResultValidationError,
    Retriever,
    validate_retrieval_results,
)


def make_synthetic_passage(passage_id: str, paper_id: str, text: str) -> Passage:
    """Return a valid synthetic Passage fixture."""
    return Passage.from_mapping(
        {
            "passage_id": passage_id,
            "paper_id": paper_id,
            "text": text,
            "section_heading": "I. Introduction",
            "page_start": 1,
            "page_end": 2,
            "ordinal_position": 0,
        }
    )


def make_synthetic_evidence(
    passage: Passage, score: float, rank: int, method: str = "bm25"
) -> RetrievalEvidence:
    """Return a valid synthetic RetrievalEvidence fixture."""
    return RetrievalEvidence(
        passage=passage,
        score=score,
        rank=rank,
        retrieval_method=method,
    )


class DeterministicTestRetriever:
    """Deterministic test double conforming to the Retriever protocol."""

    def __init__(self, corpus_evidence: Sequence[RetrievalEvidence]) -> None:
        self.corpus_evidence = tuple(corpus_evidence)

    def retrieve(
        self,
        request: RetrievalRequest,
    ) -> tuple[RetrievalEvidence, ...]:
        query_lower = request.query.lower()
        matches = [
            ev for ev in self.corpus_evidence if query_lower in ev.passage.text.lower()
        ]
        # Sort by score descending, then passage_id ascending for tie-break
        matches.sort(key=lambda ev: (-ev.score, ev.passage.passage_id))
        top_matches = matches[: request.top_k]

        ranked = [
            RetrievalEvidence(
                passage=ev.passage,
                score=ev.score,
                rank=i + 1,
                retrieval_method=ev.retrieval_method or "bm25",
            )
            for i, ev in enumerate(top_matches)
        ]
        return validate_retrieval_results(request, tuple(ranked))


class NonConformingObject:
    """Object without a retrieve method for testing runtime_checkable failure."""

    pass


# --- RetrievalRequest Tests ---


def test_retrieval_request_valid_direct_construction() -> None:
    """Test valid direct construction of RetrievalRequest."""
    req = RetrievalRequest(query="infrastructure investment", top_k=5)
    assert req.query == "infrastructure investment"
    assert req.top_k == 5


def test_retrieval_request_valid_mapping_construction() -> None:
    """Test valid construction from a mapping requiring query and top_k."""
    data = {"query": "road connectivity", "top_k": 20}
    req = RetrievalRequest.from_mapping(data)
    assert req.query == "road connectivity"
    assert req.top_k == 20


def test_retrieval_request_rejects_missing_top_k() -> None:
    """Test that from_mapping rejects mappings missing top_k."""
    with pytest.raises(
        RetrievalRequestValidationError, match="missing required fields: top_k"
    ):
        RetrievalRequest.from_mapping({"query": "tax reform"})


def test_retrieval_request_to_mapping_always_emits_query_and_top_k() -> None:
    """Test that to_mapping always includes both query and top_k."""
    req_default = RetrievalRequest(query="default test")
    assert req_default.to_mapping() == {"query": "default test", "top_k": 10}


def test_retrieval_request_canonical_mapping_round_trip() -> None:
    """Test round-trip serialization to and from mapping."""
    req = RetrievalRequest(query="elections & road spending", top_k=15)
    mapping = req.to_mapping()
    assert mapping == {"query": "elections & road spending", "top_k": 15}
    restored = RetrievalRequest.from_mapping(mapping)
    assert restored == req


def test_retrieval_request_immutability() -> None:
    """Test that RetrievalRequest instances are frozen and immutable."""
    req = RetrievalRequest(query="test query", top_k=10)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        req.top_k = 20  # type: ignore[misc]


def test_retrieval_request_whitespace_normalization() -> None:
    """Test that leading and trailing whitespace is stripped."""
    req = RetrievalRequest(query="   municipal elections   \n\t")
    assert req.query == "municipal elections"


def test_retrieval_request_preserves_internal_whitespace_and_punctuation() -> None:
    """Test that internal whitespace, tabs, newlines, and punctuation are preserved."""
    raw_query = "elections  and\t public\n investment (2024)! & road-spending?"
    req = RetrievalRequest(query=f"  {raw_query}  ")
    assert req.query == raw_query


def test_retrieval_request_rejects_empty_query() -> None:
    """Test that empty query strings are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="non-empty string"):
        RetrievalRequest(query="")


def test_retrieval_request_rejects_whitespace_only_query() -> None:
    """Test that whitespace-only query strings are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="non-empty string"):
        RetrievalRequest(query="   \t\n  ")


def test_retrieval_request_rejects_non_string_query() -> None:
    """Test that non-string queries are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="string"):
        RetrievalRequest(query=cast(str, 12345))


def test_retrieval_request_rejects_top_k_zero() -> None:
    """Test that top_k=0 is rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="positive integer"):
        RetrievalRequest(query="valid query", top_k=0)


def test_retrieval_request_rejects_negative_top_k() -> None:
    """Test that negative top_k values are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="positive integer"):
        RetrievalRequest(query="valid query", top_k=-5)


def test_retrieval_request_rejects_bool_top_k() -> None:
    """Test that boolean top_k values are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="positive integer"):
        RetrievalRequest(query="valid query", top_k=cast(int, True))


def test_retrieval_request_rejects_float_top_k() -> None:
    """Test that float top_k values are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="positive integer"):
        RetrievalRequest(query="valid query", top_k=cast(int, 5.5))


def test_retrieval_request_rejects_string_top_k() -> None:
    """Test that string top_k values are rejected."""
    with pytest.raises(RetrievalRequestValidationError, match="positive integer"):
        RetrievalRequest(query="valid query", top_k=cast(int, "10"))


def test_retrieval_request_rejects_missing_query() -> None:
    """Test that from_mapping rejects missing query key."""
    with pytest.raises(
        RetrievalRequestValidationError, match="missing required fields: query"
    ):
        RetrievalRequest.from_mapping({"top_k": 5})


def test_retrieval_request_rejects_unknown_mapping_fields() -> None:
    """Test that from_mapping rejects unknown fields."""
    with pytest.raises(RetrievalRequestValidationError, match="unknown fields: extra"):
        RetrievalRequest.from_mapping(
            {"query": "valid query", "top_k": 5, "extra": 123}
        )


def test_retrieval_request_rejects_non_string_mapping_keys() -> None:
    """Test that from_mapping rejects non-string mapping keys."""
    bad_mapping = {123: "query"}
    with pytest.raises(RetrievalRequestValidationError, match="keys must be strings"):
        RetrievalRequest.from_mapping(cast(dict[str, object], bad_mapping))


def test_retrieval_request_exception_hierarchy() -> None:
    """Test that protocol exceptions inherit from RetrievalContractError and ValueError."""
    err = RetrievalRequestValidationError("Query error")
    assert isinstance(err, RetrievalContractError)
    assert isinstance(err, ValueError)


# --- Retriever Protocol Tests ---


def test_retriever_protocol_conformance_with_test_double() -> None:
    """Test that DeterministicTestRetriever structurally conforms to Retriever."""
    retriever = DeterministicTestRetriever([])
    assert isinstance(retriever, Retriever)


def test_retriever_protocol_runtime_checkable_non_conformance() -> None:
    """Test that NonConformingObject does not conform to Retriever."""
    obj = NonConformingObject()
    assert not isinstance(obj, Retriever)


def test_retriever_protocol_documentation_note_on_runtime_checkable() -> None:
    """Verify runtime_checkable behavior: checks structural presence of attribute, not full signature/types."""

    class StructurallyConformingDummy:
        def retrieve(self, request: object) -> object:
            return None

    dummy = StructurallyConformingDummy()
    # @runtime_checkable verifies structural presence of the required 'retrieve' attribute
    assert isinstance(dummy, Retriever)


def test_retriever_protocol_retrieve_signature_accepts_request_returns_evidence_tuple() -> (
    None
):
    """Test that a conforming retriever accepts RetrievalRequest and returns evidence tuple."""
    p1 = make_synthetic_passage(
        "synthetic-paper-1:p1:pos0",
        "synthetic-paper-1",
        "Local road infrastructure spending.",
    )
    ev1 = make_synthetic_evidence(p1, score=12.5, rank=1, method="bm25")
    retriever = DeterministicTestRetriever([ev1])

    req = RetrievalRequest(query="road", top_k=5)
    results = retriever.retrieve(req)

    assert isinstance(results, tuple)
    assert len(results) == 1
    assert results[0].passage.passage_id == "synthetic-paper-1:p1:pos0"


# --- Result Validation Tests ---


def test_validate_results_valid_ordered_results() -> None:
    """Test validation of valid, properly ranked and ordered results."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p2, score=10.0, rank=2, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    results = (ev1, ev2)
    validated = validate_retrieval_results(req, results)

    assert validated == results


def test_validate_results_valid_empty_results() -> None:
    """Test that an empty tuple is valid when no matches occur."""
    req = RetrievalRequest(query="nonexistent", top_k=5)
    validated = validate_retrieval_results(req, ())
    assert validated == ()


def test_validate_results_fewer_than_top_k_results() -> None:
    """Test that returning fewer than top_k results is valid."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    ev1 = make_synthetic_evidence(p1, score=8.0, rank=1, method="bm25")

    req = RetrievalRequest(query="text", top_k=10)
    validated = validate_retrieval_results(req, (ev1,))
    assert len(validated) == 1


def test_validate_results_exactly_top_k_results() -> None:
    """Test that returning exactly top_k results is valid."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p2, score=10.0, rank=2, method="bm25")

    req = RetrievalRequest(query="text", top_k=2)
    validated = validate_retrieval_results(req, (ev1, ev2))
    assert len(validated) == 2


def test_validate_results_rejects_non_tuple_results() -> None:
    """Test that non-tuple result structures are rejected."""
    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(RetrievalResultValidationError, match="must be a tuple"):
        validate_retrieval_results(req, [1, 2, 3])


def test_validate_results_rejects_non_evidence_item() -> None:
    """Test that items that are not RetrievalEvidence are rejected."""
    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="not a RetrievalEvidence instance"
    ):
        validate_retrieval_results(req, ("invalid_item",))


def test_validate_results_rejects_too_many_results() -> None:
    """Test that returning more than top_k results is rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p2, score=10.0, rank=2, method="bm25")

    req = RetrievalRequest(query="text", top_k=1)
    with pytest.raises(RetrievalResultValidationError, match="exceeds request.top_k"):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_rejects_rank_starting_at_zero() -> None:
    """Test that ranks starting at 0 are rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    # Rank set to 0 directly
    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    object.__setattr__(ev1, "rank", 0)

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="expected contiguous rank 1"
    ):
        validate_retrieval_results(req, (ev1,))


def test_validate_results_rejects_rank_gap() -> None:
    """Test that non-contiguous rank gaps are rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(
        p2, score=10.0, rank=3, method="bm25"
    )  # Gap: 3 instead of 2

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="expected contiguous rank 2"
    ):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_rejects_rank_order_mismatch() -> None:
    """Test that mismatched rank order is rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(
        p1, score=15.0, rank=2, method="bm25"
    )  # Rank 2 at position 0
    ev2 = make_synthetic_evidence(p2, score=10.0, rank=1, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="expected contiguous rank 1"
    ):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_rejects_duplicate_passage_id() -> None:
    """Test that duplicate passage_id values are rejected."""
    p1 = make_synthetic_passage("dup_passage", "paper1", "text 1")
    p2 = make_synthetic_passage("dup_passage", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p2, score=10.0, rank=2, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="Duplicate passage_id 'dup_passage'"
    ):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_rejects_increasing_score_order() -> None:
    """Test that results with increasing scores are rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=10.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(
        p2, score=15.0, rank=2, method="bm25"
    )  # Increasing score

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="not ordered by non-increasing score"
    ):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_accepts_deterministic_equal_score_passage_id_tie_break() -> (
    None
):
    """Test that equal scores ordered by ascending passage_id tie-breaker are accepted."""
    p_alpha = make_synthetic_passage("alpha_passage", "paper1", "text 1")
    p_beta = make_synthetic_passage("beta_passage", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p_alpha, score=10.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p_beta, score=10.0, rank=2, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    validated = validate_retrieval_results(req, (ev1, ev2))
    assert len(validated) == 2


def test_validate_results_rejects_reversed_equal_score_tie_break() -> None:
    """Test that equal scores with non-ascending passage_id order are rejected."""
    p_alpha = make_synthetic_passage("alpha_passage", "paper1", "text 1")
    p_beta = make_synthetic_passage("beta_passage", "paper1", "text 2")

    # Reversed: beta before alpha for equal score 10.0
    ev1 = make_synthetic_evidence(p_beta, score=10.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p_alpha, score=10.0, rank=2, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="must be ordered by ascending passage_id"
    ):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_rejects_retrieval_method_none() -> None:
    """Test that retrieval_method=None is rejected at the retrieval protocol boundary."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    ev1 = make_synthetic_evidence(p1, score=10.0, rank=1, method="bm25")
    object.__setattr__(ev1, "retrieval_method", None)

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="non-empty retrieval_method string"
    ):
        validate_retrieval_results(req, (ev1,))


def test_validate_results_rejects_empty_retrieval_method() -> None:
    """Test that empty or whitespace-only retrieval_method is rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    ev1 = make_synthetic_evidence(p1, score=10.0, rank=1, method="bm25")
    object.__setattr__(ev1, "retrieval_method", "   ")

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError, match="non-empty retrieval_method string"
    ):
        validate_retrieval_results(req, (ev1,))


def test_validate_results_rejects_inconsistent_retrieval_method_labels() -> None:
    """Test that mixing different retrieval_method labels in one result set is rejected."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    p2 = make_synthetic_passage("p2", "paper1", "text 2")

    ev1 = make_synthetic_evidence(p1, score=15.0, rank=1, method="bm25")
    ev2 = make_synthetic_evidence(p2, score=10.0, rank=2, method="dense_vector")

    req = RetrievalRequest(query="text", top_k=5)
    with pytest.raises(
        RetrievalResultValidationError,
        match="Inconsistent retrieval_method 'dense_vector'",
    ):
        validate_retrieval_results(req, (ev1, ev2))


def test_validate_results_same_input_yields_identical_output() -> None:
    """Test that validating the same input yields identical results."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    ev1 = make_synthetic_evidence(p1, score=10.0, rank=1, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    res1 = validate_retrieval_results(req, (ev1,))
    res2 = validate_retrieval_results(req, (ev1,))
    assert res1 == res2


def test_validate_results_does_not_mutate_input_tuple_or_objects() -> None:
    """Test that validation returns the tuple without mutating any objects."""
    p1 = make_synthetic_passage("p1", "paper1", "text 1")
    ev1 = make_synthetic_evidence(p1, score=10.0, rank=1, method="bm25")

    req = RetrievalRequest(query="text", top_k=5)
    input_tuple = (ev1,)
    validated = validate_retrieval_results(req, input_tuple)
    assert validated is input_tuple
    assert validated[0].rank == 1
    assert validated[0].score == 10.0


def test_retrieval_contract_error_catches_all_protocol_failures() -> None:
    """Test that RetrievalContractError serves as the top-level catch-all exception."""
    req = RetrievalRequest(query="test", top_k=5)

    with pytest.raises(RetrievalContractError):
        RetrievalRequest(query="")

    with pytest.raises(RetrievalContractError):
        validate_retrieval_results(req, [1, 2])
