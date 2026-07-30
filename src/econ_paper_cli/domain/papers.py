"""Pure domain contract for economic literature paper metadata."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse

from econ_paper_cli.domain.errors import PaperValidationError

_PAPER_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_PAPER_FIELDS = frozenset(
    {
        "paper_id",
        "title",
        "authors",
        "year",
        "abstract",
        "source_name",
        "source_identifier",
        "source_url",
    }
)


@dataclass(frozen=True, slots=True)
class Paper:
    """Immutable domain representation of a paper's bibliographic metadata."""

    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    abstract: str | None
    source_name: str
    source_identifier: str
    source_url: str | None

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_paper_id(self.paper_id)
        _validate_nonempty_text("title", self.title)
        _validate_authors(self.authors)
        _validate_year(self.year)
        _validate_optional_text("abstract", self.abstract)
        _validate_nonempty_text("source_name", self.source_name)
        _validate_nonempty_text("source_identifier", self.source_identifier)
        _validate_source_url(self.source_url)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Paper":
        """Validate and construct a Paper from a JSON-compatible mapping."""
        if not isinstance(data, Mapping):
            raise PaperValidationError("Paper metadata must be a mapping.")
        if any(not isinstance(field, str) for field in data):
            raise PaperValidationError("Paper field names must be strings.")

        provided_fields = set(data)
        missing_fields = sorted(_PAPER_FIELDS - provided_fields)
        if missing_fields:
            raise PaperValidationError(
                "Paper metadata is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided_fields - _PAPER_FIELDS)
        if unknown_fields:
            raise PaperValidationError(
                "Paper metadata contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        raw_authors = data["authors"]
        if not isinstance(raw_authors, (list, tuple)):
            raise PaperValidationError(
                "authors must be a non-empty sequence of strings."
            )
        authors = tuple(cast(Sequence[object], raw_authors))

        return cls(
            paper_id=cast(str, data["paper_id"]),
            title=cast(str, data["title"]),
            authors=cast(tuple[str, ...], authors),
            year=cast(int | None, data["year"]),
            abstract=cast(str | None, data["abstract"]),
            source_name=cast(str, data["source_name"]),
            source_identifier=cast(str, data["source_identifier"]),
            source_url=cast(str | None, data["source_url"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "abstract": self.abstract,
            "source_name": self.source_name,
            "source_identifier": self.source_identifier,
            "source_url": self.source_url,
        }


def _validate_paper_id(value: object) -> None:
    if not isinstance(value, str) or _PAPER_ID_PATTERN.fullmatch(value) is None:
        raise PaperValidationError("paper_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*.")


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PaperValidationError(f"{field} must be a non-empty string.")


def _validate_optional_text(field: str, value: object) -> None:
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise PaperValidationError(f"{field} must be a non-empty string or None.")


def _validate_source_url(value: object) -> None:
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise PaperValidationError("source_url must be a non-empty string or None.")
        try:
            parsed = urlparse(value.strip())
        except Exception as error:
            raise PaperValidationError(
                f"source_url must be a valid absolute HTTP or HTTPS URL: {error}."
            ) from error
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise PaperValidationError(
                "source_url must be a valid absolute HTTP or HTTPS URL."
            )


def _validate_authors(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise PaperValidationError(
            "authors must be a non-empty tuple of non-empty strings."
        )
    for author in value:
        if not isinstance(author, str) or not author.strip():
            raise PaperValidationError("Each author must be a non-empty string.")


def _validate_year(value: object) -> None:
    if value is not None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not (1800 <= value <= 2100)
        ):
            raise PaperValidationError(
                "year must be an integer between 1800 and 2100, or None."
            )
