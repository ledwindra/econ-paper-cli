"""Tests for the Corpus domain contract."""

from collections.abc import Mapping
from typing import cast

import pytest

from econ_paper_cli.domain import (
    Corpus,
    CorpusValidationError,
    DomainError,
    Paper,
    PaperValidationError,
    Passage,
    PassageValidationError,
)


def valid_paper_mapping(**overrides: object) -> dict[str, object]:
    """Return a valid synthetic paper mapping."""
    data: dict[str, object] = {
        "paper_id": "synthetic-elections-roads-2024",
        "title": "Direct Municipal Elections and Local Road Infrastructure: Evidence from Fictional District Reforms in Valdonia",
        "authors": ["Elena Rostova", "Marcus Vance"],
        "year": 2024,
        "abstract": "We examine the introduction of direct mayoral elections across 140 fictional municipalities in Valdonia between 2012 and 2020. Using a difference-in-differences design with municipal fixed effects, we find that direct electoral accountability increases local road construction spending by 18.4% relative to administrative appointment regimes.",
        "source_name": "Econ Paper CLI Synthetic Fixture Series",
        "source_identifier": "synthetic-fixture-paper-001",
        "source_url": "https://example.invalid/synthetic-fixture-paper-001",
    }
    data.update(overrides)
    return data


def valid_passage_mapping(**overrides: object) -> dict[str, object]:
    """Return a valid synthetic passage mapping."""
    data: dict[str, object] = {
        "passage_id": "synthetic-elections-roads-2024:sec1:p3:pos0",
        "paper_id": "synthetic-elections-roads-2024",
        "text": "Direct democratic elections in local municipal jurisdictions may enhance responsiveness to voter demands for basic infrastructure services. In developing regional economies, local road connectivity represents a highly visible public good.",
        "section_heading": "I. Introduction and Motivation",
        "page_start": 3,
        "page_end": 4,
        "ordinal_position": 0,
    }
    data.update(overrides)
    return data


def valid_corpus_mapping(**overrides: object) -> dict[str, object]:
    """Return a valid corpus mapping."""
    data: dict[str, object] = {
        "schema_version": 1,
        "corpus_id": "synthetic-economics-v1",
        "papers": [valid_paper_mapping()],
        "passages": [valid_passage_mapping()],
    }
    data.update(overrides)
    return data


def test_corpus_valid_construction() -> None:
    """Test valid construction of Corpus from Paper and Passage domain instances."""
    paper = Paper.from_mapping(valid_paper_mapping())
    passage = Passage.from_mapping(valid_passage_mapping())
    corpus = Corpus(
        schema_version=1,
        corpus_id="synthetic-economics-v1",
        papers=(paper,),
        passages=(passage,),
    )

    assert corpus.schema_version == 1
    assert corpus.corpus_id == "synthetic-economics-v1"
    assert corpus.papers == (paper,)
    assert corpus.passages == (passage,)


def test_corpus_canonical_mapping_round_trip() -> None:
    """Test that Corpus deserializes and serializes accurately."""
    data = valid_corpus_mapping()
    corpus = Corpus.from_mapping(data)

    assert corpus.schema_version == 1
    assert corpus.corpus_id == "synthetic-economics-v1"
    assert len(corpus.papers) == 1
    assert len(corpus.passages) == 1
    assert corpus.to_mapping() == data


def test_corpus_immutability() -> None:
    """Test that Corpus attributes cannot be mutated."""
    corpus = Corpus.from_mapping(valid_corpus_mapping())
    with pytest.raises(AttributeError):
        corpus.corpus_id = "new-id"  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, [], "not-a-mapping"])
def test_corpus_requires_mapping(value: object) -> None:
    """Test that Corpus.from_mapping requires a mapping."""
    with pytest.raises(CorpusValidationError, match="mapping"):
        Corpus.from_mapping(cast(Mapping[str, object], value))


def test_corpus_rejects_non_string_keys() -> None:
    """Test that Corpus rejects non-string mapping keys."""
    data = {123: "val"}
    with pytest.raises(CorpusValidationError, match="field names must be strings"):
        Corpus.from_mapping(cast(Mapping[str, object], data))


def test_corpus_rejects_missing_required_fields() -> None:
    """Test that Corpus rejects missing required fields."""
    data = valid_corpus_mapping()
    del data["papers"]
    with pytest.raises(CorpusValidationError, match="missing required fields"):
        Corpus.from_mapping(data)


def test_corpus_rejects_unknown_fields() -> None:
    """Test that Corpus rejects unknown fields."""
    data = valid_corpus_mapping(description="Extra field")
    with pytest.raises(CorpusValidationError, match="unknown fields"):
        Corpus.from_mapping(data)


@pytest.mark.parametrize("schema_version", [0, 2, "1", True, 1.0])
def test_corpus_unsupported_schema_version(schema_version: object) -> None:
    """Test that schema_version must be integer 1."""
    with pytest.raises(CorpusValidationError, match="schema_version"):
        Corpus.from_mapping(valid_corpus_mapping(schema_version=schema_version))


@pytest.mark.parametrize(
    "corpus_id", ["", "Uppercase", "spaces in id", "-leading", "trailing-"]
)
def test_corpus_invalid_corpus_id(corpus_id: str) -> None:
    """Test corpus_id grammar validation."""
    with pytest.raises(CorpusValidationError, match="corpus_id"):
        Corpus.from_mapping(valid_corpus_mapping(corpus_id=corpus_id))


@pytest.mark.parametrize("papers", [[], "not-a-sequence", None])
def test_corpus_rejects_empty_or_invalid_papers(papers: object) -> None:
    """Test papers validation."""
    with pytest.raises(CorpusValidationError, match="papers"):
        Corpus.from_mapping(valid_corpus_mapping(papers=papers))


@pytest.mark.parametrize("passages", [[], "not-a-sequence", None])
def test_corpus_rejects_empty_or_invalid_passages(passages: object) -> None:
    """Test passages validation."""
    with pytest.raises(CorpusValidationError, match="passages"):
        Corpus.from_mapping(valid_corpus_mapping(passages=passages))


def test_corpus_propagates_nested_paper_validation_error() -> None:
    """Test that malformed paper mappings propagate PaperValidationError directly."""
    invalid_paper = valid_paper_mapping(title="")
    data = valid_corpus_mapping(papers=[invalid_paper])

    with pytest.raises(PaperValidationError):
        Corpus.from_mapping(data)


def test_corpus_propagates_nested_passage_validation_error() -> None:
    """Test that malformed passage mappings propagate PassageValidationError directly."""
    invalid_passage = valid_passage_mapping(page_start=0)
    data = valid_corpus_mapping(passages=[invalid_passage])

    with pytest.raises(PassageValidationError):
        Corpus.from_mapping(data)


def test_corpus_rejects_duplicate_paper_id() -> None:
    """Test that duplicate paper_ids raise CorpusValidationError."""
    paper1 = valid_paper_mapping(paper_id="paper-1")
    paper2 = valid_paper_mapping(paper_id="paper-1")
    data = valid_corpus_mapping(papers=[paper1, paper2])

    with pytest.raises(CorpusValidationError, match="Duplicate paper_id"):
        Corpus.from_mapping(data)


def test_corpus_rejects_duplicate_passage_id() -> None:
    """Test that duplicate passage_ids raise CorpusValidationError."""
    passage1 = valid_passage_mapping(passage_id="p1", ordinal_position=0)
    passage2 = valid_passage_mapping(passage_id="p1", ordinal_position=1)
    data = valid_corpus_mapping(passages=[passage1, passage2])

    with pytest.raises(CorpusValidationError, match="Duplicate passage_id"):
        Corpus.from_mapping(data)


def test_corpus_rejects_dangling_paper_reference() -> None:
    """Test that a passage referencing an unknown paper_id raises CorpusValidationError."""
    passage = valid_passage_mapping(paper_id="unknown-paper")
    data = valid_corpus_mapping(passages=[passage])

    with pytest.raises(CorpusValidationError, match="unknown paper_id"):
        Corpus.from_mapping(data)


def test_corpus_rejects_duplicate_ordinal_position_within_same_paper() -> None:
    """Test that duplicate ordinal_position within the same paper raises CorpusValidationError."""
    passage1 = valid_passage_mapping(passage_id="p1", ordinal_position=0)
    passage2 = valid_passage_mapping(passage_id="p2", ordinal_position=0)
    data = valid_corpus_mapping(passages=[passage1, passage2])

    with pytest.raises(CorpusValidationError, match="Duplicate ordinal_position"):
        Corpus.from_mapping(data)


def test_corpus_allows_same_ordinal_position_across_different_papers() -> None:
    """Test that the same ordinal_position across different papers is valid."""
    paper1 = valid_paper_mapping(paper_id="paper-1")
    paper2 = valid_paper_mapping(paper_id="paper-2")
    passage1 = valid_passage_mapping(
        passage_id="paper-1:sec1:pos0", paper_id="paper-1", ordinal_position=0
    )
    passage2 = valid_passage_mapping(
        passage_id="paper-2:sec1:pos0", paper_id="paper-2", ordinal_position=0
    )

    data = valid_corpus_mapping(papers=[paper1, paper2], passages=[passage1, passage2])
    corpus = Corpus.from_mapping(data)

    assert len(corpus.papers) == 2
    assert len(corpus.passages) == 2


def test_corpus_validation_error_inherits_from_domain_error() -> None:
    """Verify that CorpusValidationError inherits from DomainError."""
    assert issubclass(CorpusValidationError, DomainError)
    with pytest.raises(DomainError):
        Corpus.from_mapping(valid_corpus_mapping(schema_version=99))
