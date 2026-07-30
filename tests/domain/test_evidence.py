"""Tests for the RetrievalEvidence domain contract."""

from collections.abc import Mapping
from typing import cast

import pytest

from econ_paper_cli.domain import (
    DomainError,
    EvidenceValidationError,
    Passage,
    PassageValidationError,
    RetrievalEvidence,
)


def valid_passage_mapping(**overrides: object) -> dict[str, object]:
    """Return a dictionary representing valid synthetic passage metadata."""
    data: dict[str, object] = {
        "passage_id": "synthetic-elections-roads-2024:sec1:p3:pos0",
        "paper_id": "synthetic-elections-roads-2024",
        "text": "Direct democratic elections in local municipal jurisdictions may enhance responsiveness...",
        "section_heading": "I. Introduction and Motivation",
        "page_start": 3,
        "page_end": 4,
        "ordinal_position": 0,
    }
    data.update(overrides)
    return data


def valid_evidence_mapping(**overrides: object) -> dict[str, object]:
    """Return a dictionary representing valid retrieval evidence."""
    data: dict[str, object] = {
        "passage": valid_passage_mapping(),
        "score": 14.82,
        "rank": 1,
        "retrieval_method": "bm25",
    }
    data.update(overrides)
    return data


def test_evidence_round_trips_canonical_mapping() -> None:
    """Test that RetrievalEvidence serializes and deserializes accurately."""
    data = valid_evidence_mapping()
    evidence = RetrievalEvidence.from_mapping(data)

    assert evidence.passage.passage_id == "synthetic-elections-roads-2024:sec1:p3:pos0"
    assert evidence.score == 14.82
    assert evidence.rank == 1
    assert evidence.retrieval_method == "bm25"
    assert evidence.to_mapping() == data


def test_evidence_normalizes_integer_score_to_float() -> None:
    """Test that integer score input is normalized to float for direct and mapping construction."""
    passage = Passage.from_mapping(valid_passage_mapping())

    # Direct construction with integer score
    evidence_direct = RetrievalEvidence(
        passage=passage,
        score=14,
        rank=1,
        retrieval_method="bm25",
    )
    assert isinstance(evidence_direct.score, float)
    assert evidence_direct.score == 14.0

    # from_mapping construction with integer score
    evidence_mapping = RetrievalEvidence.from_mapping(valid_evidence_mapping(score=14))
    assert isinstance(evidence_mapping.score, float)
    assert evidence_mapping.score == 14.0


def test_evidence_supports_optional_retrieval_method_none() -> None:
    """Test that retrieval_method can be None."""
    data = valid_evidence_mapping(retrieval_method=None)
    evidence = RetrievalEvidence.from_mapping(data)

    assert evidence.retrieval_method is None


@pytest.mark.parametrize("value", [None, [], "not-a-mapping"])
def test_evidence_requires_mapping(value: object) -> None:
    """Test that RetrievalEvidence.from_mapping requires a mapping."""
    with pytest.raises(EvidenceValidationError, match="mapping"):
        RetrievalEvidence.from_mapping(cast(Mapping[str, object], value))


def test_evidence_rejects_missing_and_unknown_fields() -> None:
    """Test that RetrievalEvidence rejects missing required fields and unknown fields."""
    data = valid_evidence_mapping()
    del data["rank"]
    with pytest.raises(EvidenceValidationError, match="missing required fields"):
        RetrievalEvidence.from_mapping(data)

    with pytest.raises(EvidenceValidationError, match="unknown fields"):
        RetrievalEvidence.from_mapping(valid_evidence_mapping(extra="field"))


def test_evidence_accepts_passage_instance_or_mapping() -> None:
    """Test that evidence accepts Passage instance directly in construction."""
    passage = Passage.from_mapping(valid_passage_mapping())
    evidence = RetrievalEvidence(
        passage=passage,
        score=1.0,
        rank=1,
        retrieval_method="dense",
    )
    assert evidence.passage == passage


@pytest.mark.parametrize("invalid_passage", ["not-a-passage", 123, None])
def test_evidence_rejects_invalid_passage_type(invalid_passage: object) -> None:
    """Test non-mapping and non-Passage passage field validation."""
    with pytest.raises(EvidenceValidationError, match="passage"):
        RetrievalEvidence.from_mapping(valid_evidence_mapping(passage=invalid_passage))


def test_evidence_propagates_passage_validation_error_unwrapped() -> None:
    """Verify that Passage.from_mapping errors inside RetrievalEvidence are not wrapped."""
    invalid_passage_data = valid_passage_mapping(page_start=0)
    invalid_evidence_data = valid_evidence_mapping(passage=invalid_passage_data)

    # Should raise PassageValidationError directly (not wrapped into EvidenceValidationError)
    with pytest.raises(PassageValidationError):
        RetrievalEvidence.from_mapping(invalid_evidence_data)


def test_domain_error_catches_nested_passage_validation_error() -> None:
    """Verify that DomainError catches PassageValidationError during RetrievalEvidence parsing."""
    invalid_passage_data = valid_passage_mapping(page_start=0)
    invalid_evidence_data = valid_evidence_mapping(passage=invalid_passage_data)

    with pytest.raises(DomainError) as exc_info:
        RetrievalEvidence.from_mapping(invalid_evidence_data)

    assert isinstance(exc_info.value, PassageValidationError)


@pytest.mark.parametrize(
    "score", [float("nan"), float("inf"), float("-inf"), "14.82", True]
)
def test_evidence_score_validation(score: object) -> None:
    """Test that score must be a finite number."""
    with pytest.raises(EvidenceValidationError, match="score"):
        RetrievalEvidence.from_mapping(valid_evidence_mapping(score=score))


@pytest.mark.parametrize("rank", [0, -1, 1.5, "1", True])
def test_evidence_rank_validation(rank: object) -> None:
    """Test rank validation."""
    with pytest.raises(EvidenceValidationError, match="rank"):
        RetrievalEvidence.from_mapping(valid_evidence_mapping(rank=rank))


def test_evidence_error_inherits_from_domain_error() -> None:
    """Verify that EvidenceValidationError inherits from DomainError."""
    with pytest.raises(DomainError):
        RetrievalEvidence.from_mapping(valid_evidence_mapping(rank=0))
