"""Tests for the RetrievalEvidence domain contract."""

from collections.abc import Mapping
from typing import cast

import pytest

from econ_paper_cli.domain import (
    DomainError,
    EvidenceValidationError,
    Passage,
    RetrievalEvidence,
)


def valid_passage_mapping(**overrides: object) -> dict[str, object]:
    """Return a dictionary representing valid passage metadata."""
    data: dict[str, object] = {
        "passage_id": "autor-2003:p1279:pos0",
        "paper_id": "autor-2003-computerization",
        "text": "Computerization alters job tasks rather than simply replacing workers...",
        "section_heading": "II. A Model of Task Substitution",
        "page_start": 1279,
        "page_end": 1280,
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

    assert evidence.passage.passage_id == "autor-2003:p1279:pos0"
    assert evidence.score == 14.82
    assert evidence.rank == 1
    assert evidence.retrieval_method == "bm25"
    assert evidence.to_mapping() == data


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
def test_evidence_rejects_invalid_passage(invalid_passage: object) -> None:
    """Test passage field validation."""
    with pytest.raises(EvidenceValidationError, match="passage"):
        RetrievalEvidence.from_mapping(valid_evidence_mapping(passage=invalid_passage))


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
