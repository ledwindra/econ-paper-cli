"""Tests for the Citation domain contract."""

from collections.abc import Mapping
from typing import cast

import pytest

from econ_paper_cli.domain import (
    Citation,
    CitationValidationError,
    DomainError,
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


def valid_citation_mapping(**overrides: object) -> dict[str, object]:
    """Return a dictionary representing a valid citation."""
    data: dict[str, object] = {
        "citation_id": "1",
        "paper_id": "synthetic-elections-roads-2024",
        "passage_id": "synthetic-elections-roads-2024:sec1:p3:pos0",
    }
    data.update(overrides)
    return data


def test_citation_round_trips_canonical_mapping() -> None:
    """Test that Citation serializes and deserializes accurately."""
    data = valid_citation_mapping()
    citation = Citation.from_mapping(data)

    assert citation.citation_id == "1"
    assert citation.paper_id == "synthetic-elections-roads-2024"
    assert citation.passage_id == "synthetic-elections-roads-2024:sec1:p3:pos0"
    assert citation.to_mapping() == data


def test_citation_matches_evidence() -> None:
    """Test unambiguous matching against retrieved evidence."""
    citation = Citation.from_mapping(valid_citation_mapping())
    evidence = RetrievalEvidence.from_mapping(valid_evidence_mapping())

    assert citation.matches_evidence(evidence) is True


def test_citation_mismatches_other_evidence() -> None:
    """Test matching returns False when paper_id or passage_id differs."""
    citation = Citation.from_mapping(valid_citation_mapping())

    # Mismatching passage_id
    evidence_diff_passage = RetrievalEvidence.from_mapping(
        valid_evidence_mapping(
            passage={
                "passage_id": "synthetic-other:p1:pos0",
                "paper_id": "synthetic-elections-roads-2024",
                "text": "Different passage text...",
                "section_heading": None,
                "page_start": 1,
                "page_end": None,
                "ordinal_position": 0,
            }
        )
    )
    assert citation.matches_evidence(evidence_diff_passage) is False

    # Mismatching object type
    assert citation.matches_evidence(cast(RetrievalEvidence, "not-evidence")) is False


@pytest.mark.parametrize("value", [None, [], "not-a-mapping"])
def test_citation_requires_mapping(value: object) -> None:
    """Test that Citation.from_mapping requires a mapping."""
    with pytest.raises(CitationValidationError, match="mapping"):
        Citation.from_mapping(cast(Mapping[str, object], value))


def test_citation_rejects_missing_and_unknown_fields() -> None:
    """Test that Citation rejects missing required fields and unknown fields."""
    data = valid_citation_mapping()
    del data["passage_id"]
    with pytest.raises(CitationValidationError, match="missing required fields"):
        Citation.from_mapping(data)

    with pytest.raises(CitationValidationError, match="unknown fields"):
        Citation.from_mapping(valid_citation_mapping(extra="field"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("citation_id", ""),
        ("citation_id", "   "),
        ("paper_id", "Bad Id!"),
        ("passage_id", "Bad Id!"),
    ],
)
def test_citation_fields_validation(field: str, value: str) -> None:
    """Test citation fields validation."""
    with pytest.raises(CitationValidationError, match=field):
        Citation.from_mapping(valid_citation_mapping(**{field: value}))


def test_citation_error_inherits_from_domain_error() -> None:
    """Verify that CitationValidationError inherits from DomainError."""
    with pytest.raises(DomainError):
        Citation.from_mapping(valid_citation_mapping(citation_id=""))
