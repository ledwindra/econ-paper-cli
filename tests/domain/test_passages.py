"""Tests for the Passage domain contract."""

from collections.abc import Mapping
from typing import cast

import pytest

from econ_paper_cli.domain import DomainError, Passage, PassageValidationError


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


def test_passage_round_trips_canonical_mapping() -> None:
    """Test that Passage serializes and deserializes accurately."""
    data = valid_passage_mapping()
    passage = Passage.from_mapping(data)

    assert passage.passage_id == "autor-2003:p1279:pos0"
    assert passage.paper_id == "autor-2003-computerization"
    assert passage.page_start == 1279
    assert passage.page_end == 1280
    assert passage.ordinal_position == 0
    assert passage.to_mapping() == data


def test_passage_supports_optional_none_fields() -> None:
    """Test that Passage allows None for section_heading and page range."""
    data = valid_passage_mapping(
        section_heading=None,
        page_start=None,
        page_end=None,
    )
    passage = Passage.from_mapping(data)

    assert passage.section_heading is None
    assert passage.page_start is None
    assert passage.page_end is None


@pytest.mark.parametrize("value", [None, [], "not-a-mapping"])
def test_passage_requires_mapping(value: object) -> None:
    """Test that Passage.from_mapping requires a mapping."""
    with pytest.raises(PassageValidationError, match="mapping"):
        Passage.from_mapping(cast(Mapping[str, object], value))


def test_passage_rejects_missing_and_unknown_fields() -> None:
    """Test that Passage rejects missing required fields and unknown fields."""
    data = valid_passage_mapping()
    del data["text"]
    with pytest.raises(PassageValidationError, match="missing required fields"):
        Passage.from_mapping(data)

    with pytest.raises(PassageValidationError, match="unknown fields"):
        Passage.from_mapping(valid_passage_mapping(extra="field"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("passage_id", ""),
        ("passage_id", "Bad Id!"),
        ("paper_id", ""),
        ("paper_id", "Bad Id!"),
    ],
)
def test_passage_identifier_grammar(field: str, value: str) -> None:
    """Test identifier grammar validation."""
    with pytest.raises(PassageValidationError, match=field):
        Passage.from_mapping(valid_passage_mapping(**{field: value}))


def test_passage_text_must_be_nonempty() -> None:
    """Test text validation."""
    with pytest.raises(PassageValidationError, match="text"):
        Passage.from_mapping(valid_passage_mapping(text="   "))


@pytest.mark.parametrize(
    ("page_start", "page_end", "match"),
    [
        (0, 10, "page_start"),
        (-1, 10, "page_start"),
        ("1", 10, "page_start"),
        (True, 10, "page_start"),
        (10, 0, "page_end"),
        (10, 9, "page_end"),
        (None, 10, "page_end"),
    ],
)
def test_passage_page_range_validation(
    page_start: object, page_end: object, match: str
) -> None:
    """Test page start and page end range constraints."""
    with pytest.raises(PassageValidationError, match=match):
        Passage.from_mapping(
            valid_passage_mapping(page_start=page_start, page_end=page_end)
        )


@pytest.mark.parametrize("ordinal", [-1, "0", True, 1.5])
def test_passage_ordinal_position_validation(ordinal: object) -> None:
    """Test ordinal position constraints."""
    with pytest.raises(PassageValidationError, match="ordinal_position"):
        Passage.from_mapping(valid_passage_mapping(ordinal_position=ordinal))


def test_passage_error_inherits_from_domain_error() -> None:
    """Verify that PassageValidationError inherits from DomainError."""
    with pytest.raises(DomainError):
        Passage.from_mapping(valid_passage_mapping(text=""))
