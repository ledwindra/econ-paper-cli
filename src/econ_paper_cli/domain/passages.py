"""Pure domain contract for a passage extracted from a paper."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from econ_paper_cli.domain.errors import PassageValidationError

_PASSAGE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._:-][a-z0-9]+)*")
_PAPER_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_PASSAGE_FIELDS = frozenset(
    {
        "passage_id",
        "paper_id",
        "text",
        "section_heading",
        "page_start",
        "page_end",
        "ordinal_position",
    }
)


@dataclass(frozen=True, slots=True)
class Passage:
    """Immutable domain representation of a bounded text passage from a paper."""

    passage_id: str
    paper_id: str
    text: str
    section_heading: str | None
    page_start: int | None
    page_end: int | None
    ordinal_position: int

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_passage_id(self.passage_id)
        _validate_paper_id(self.paper_id)
        _validate_nonempty_text("text", self.text)
        _validate_optional_text("section_heading", self.section_heading)
        _validate_page_range(self.page_start, self.page_end)
        _validate_ordinal_position(self.ordinal_position)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Passage":
        """Validate and construct a Passage from a JSON-compatible mapping."""
        if not isinstance(data, Mapping):
            raise PassageValidationError("Passage metadata must be a mapping.")
        if any(not isinstance(field, str) for field in data):
            raise PassageValidationError("Passage field names must be strings.")

        provided_fields = set(data)
        missing_fields = sorted(_PASSAGE_FIELDS - provided_fields)
        if missing_fields:
            raise PassageValidationError(
                "Passage metadata is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided_fields - _PASSAGE_FIELDS)
        if unknown_fields:
            raise PassageValidationError(
                "Passage metadata contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        return cls(
            passage_id=cast(str, data["passage_id"]),
            paper_id=cast(str, data["paper_id"]),
            text=cast(str, data["text"]),
            section_heading=cast(str | None, data["section_heading"]),
            page_start=cast(int | None, data["page_start"]),
            page_end=cast(int | None, data["page_end"]),
            ordinal_position=cast(int, data["ordinal_position"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "passage_id": self.passage_id,
            "paper_id": self.paper_id,
            "text": self.text,
            "section_heading": self.section_heading,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "ordinal_position": self.ordinal_position,
        }


def _validate_passage_id(value: object) -> None:
    if not isinstance(value, str) or _PASSAGE_ID_PATTERN.fullmatch(value) is None:
        raise PassageValidationError(
            "passage_id must match [a-z0-9]+(?:[._:-][a-z0-9]+)*."
        )


def _validate_paper_id(value: object) -> None:
    if not isinstance(value, str) or _PAPER_ID_PATTERN.fullmatch(value) is None:
        raise PassageValidationError(
            "paper_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PassageValidationError(f"{field} must be a non-empty string.")


def _validate_optional_text(field: str, value: object) -> None:
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise PassageValidationError(f"{field} must be a non-empty string or None.")


def _validate_page_range(start: object, end: object) -> None:
    if start is not None:
        if isinstance(start, bool) or not isinstance(start, int) or start < 1:
            raise PassageValidationError(
                "page_start must be a positive integer (>= 1) or None."
            )

    if end is not None:
        if isinstance(end, bool) or not isinstance(end, int) or end < 1:
            raise PassageValidationError(
                "page_end must be a positive integer (>= 1) or None."
            )
        if start is None:
            raise PassageValidationError(
                "page_end cannot be specified without page_start."
            )
        if end < start:
            raise PassageValidationError(
                "page_end must be greater than or equal to page_start."
            )


def _validate_ordinal_position(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PassageValidationError(
            "ordinal_position must be a non-negative integer (>= 0)."
        )
