"""Tests for the Paper domain contract."""

from collections.abc import Mapping
from typing import cast

import pytest

from econ_paper_cli.domain import DomainError, Paper, PaperValidationError


def valid_paper_mapping(**overrides: object) -> dict[str, object]:
    """Return a dictionary representing valid paper metadata."""
    data: dict[str, object] = {
        "paper_id": "autor-2003-computerization",
        "title": "The Skill Content of Recent Technological Change",
        "authors": ["David H. Autor", "Frank Levy", "Richard J. Murnane"],
        "year": 2003,
        "abstract": "We apply a task-based framework to analyze skill demand...",
        "source_name": "Quarterly Journal of Economics",
        "source_identifier": "10.1162/003355303322552801",
        "source_url": "https://doi.org/10.1162/003355303322552801",
    }
    data.update(overrides)
    return data


def test_paper_round_trips_canonical_mapping() -> None:
    """Test that Paper serializes and deserializes accurately."""
    data = valid_paper_mapping()
    paper = Paper.from_mapping(data)

    assert paper.paper_id == "autor-2003-computerization"
    assert paper.authors == ("David H. Autor", "Frank Levy", "Richard J. Murnane")
    assert paper.year == 2003
    assert paper.to_mapping() == data


def test_paper_supports_optional_none_fields() -> None:
    """Test that Paper allows None for year, abstract, and source_url."""
    data = valid_paper_mapping(year=None, abstract=None, source_url=None)
    paper = Paper.from_mapping(data)

    assert paper.year is None
    assert paper.abstract is None
    assert paper.source_url is None
    assert paper.to_mapping()["year"] is None


@pytest.mark.parametrize("value", [None, [], "not-a-mapping"])
def test_paper_requires_mapping(value: object) -> None:
    """Test that Paper.from_mapping requires a mapping."""
    with pytest.raises(PaperValidationError, match="mapping"):
        Paper.from_mapping(cast(Mapping[str, object], value))


def test_paper_rejects_missing_and_unknown_fields() -> None:
    """Test that Paper rejects missing required fields and unknown fields."""
    data = valid_paper_mapping()
    del data["title"]
    with pytest.raises(PaperValidationError, match="missing required fields"):
        Paper.from_mapping(data)

    with pytest.raises(PaperValidationError, match="unknown fields"):
        Paper.from_mapping(valid_paper_mapping(unknown_field="extra"))


@pytest.mark.parametrize(
    "paper_id",
    ["", "Uppercase", "two words", "-leading", "trailing-", "double--dash"],
)
def test_paper_id_validation(paper_id: str) -> None:
    """Test that paper_id must match canonical grammar."""
    with pytest.raises(PaperValidationError, match="paper_id"):
        Paper.from_mapping(valid_paper_mapping(paper_id=paper_id))


@pytest.mark.parametrize(
    "field",
    ["title", "source_name", "source_identifier"],
)
@pytest.mark.parametrize("value", ["", "   ", 123, None])
def test_paper_required_text_fields(field: str, value: object) -> None:
    """Test that required text fields must be non-empty strings."""
    with pytest.raises(PaperValidationError, match=field):
        Paper.from_mapping(valid_paper_mapping(**{field: value}))


@pytest.mark.parametrize("authors", [[], ["  "], "Author One", [123]])
def test_paper_authors_validation(authors: object) -> None:
    """Test that authors must be a non-empty list/tuple of non-empty strings."""
    with pytest.raises(PaperValidationError, match="authors|author"):
        Paper.from_mapping(valid_paper_mapping(authors=authors))


@pytest.mark.parametrize("year", [1799, 2101, "2003", True, 2003.5])
def test_paper_year_validation(year: object) -> None:
    """Test that year must be None or an integer between 1800 and 2100."""
    with pytest.raises(PaperValidationError, match="year"):
        Paper.from_mapping(valid_paper_mapping(year=year))


@pytest.mark.parametrize("field", ["abstract", "source_url"])
@pytest.mark.parametrize("value", ["", "   ", 123])
def test_paper_optional_text_fields_reject_empty_sentinels(
    field: str, value: object
) -> None:
    """Test that optional text fields reject empty or non-string sentinels."""
    with pytest.raises(PaperValidationError, match=field):
        Paper.from_mapping(valid_paper_mapping(**{field: value}))


def test_paper_error_inherits_from_domain_error() -> None:
    """Verify that PaperValidationError inherits from DomainError."""
    with pytest.raises(DomainError):
        Paper.from_mapping(valid_paper_mapping(title=""))
