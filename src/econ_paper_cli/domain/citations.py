"""Pure domain contract for evidence citations."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from econ_paper_cli.domain.errors import CitationValidationError
from econ_paper_cli.domain.evidence import RetrievalEvidence

_PAPER_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_PASSAGE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._:-][a-z0-9]+)*")
_CITATION_FIELDS = frozenset({"citation_id", "paper_id", "passage_id"})


@dataclass(frozen=True, slots=True)
class Citation:
    """Immutable domain representation of an evidence citation reference."""

    citation_id: str
    paper_id: str
    passage_id: str

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_nonempty_text("citation_id", self.citation_id)
        _validate_paper_id(self.paper_id)
        _validate_passage_id(self.passage_id)

    def matches_evidence(self, evidence: RetrievalEvidence) -> bool:
        """Check if this citation unambiguously matches the given retrieved evidence."""
        if not isinstance(evidence, RetrievalEvidence):
            return False
        return (
            self.paper_id == evidence.passage.paper_id
            and self.passage_id == evidence.passage.passage_id
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Citation":
        """Validate and construct a Citation from a JSON-compatible mapping."""
        if not isinstance(data, Mapping):
            raise CitationValidationError("Citation metadata must be a mapping.")
        if any(not isinstance(field, str) for field in data):
            raise CitationValidationError("Citation field names must be strings.")

        provided_fields = set(data)
        missing_fields = sorted(_CITATION_FIELDS - provided_fields)
        if missing_fields:
            raise CitationValidationError(
                "Citation is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided_fields - _CITATION_FIELDS)
        if unknown_fields:
            raise CitationValidationError(
                "Citation contains unknown fields: " + ", ".join(unknown_fields) + "."
            )

        return cls(
            citation_id=cast(str, data["citation_id"]),
            paper_id=cast(str, data["paper_id"]),
            passage_id=cast(str, data["passage_id"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "citation_id": self.citation_id,
            "paper_id": self.paper_id,
            "passage_id": self.passage_id,
        }


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CitationValidationError(f"{field} must be a non-empty string.")


def _validate_paper_id(value: object) -> None:
    if not isinstance(value, str) or _PAPER_ID_PATTERN.fullmatch(value) is None:
        raise CitationValidationError(
            "paper_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_passage_id(value: object) -> None:
    if not isinstance(value, str) or _PASSAGE_ID_PATTERN.fullmatch(value) is None:
        raise CitationValidationError(
            "passage_id must match [a-z0-9]+(?:[._:-][a-z0-9]+)*."
        )
