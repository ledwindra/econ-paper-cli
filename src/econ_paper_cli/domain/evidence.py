"""Pure domain contract for retrieved passage evidence."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from econ_paper_cli.domain.errors import EvidenceValidationError
from econ_paper_cli.domain.passages import Passage

_EVIDENCE_FIELDS = frozenset({"passage", "score", "rank", "retrieval_method"})


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    """Immutable domain representation of a retrieved passage with ranking information."""

    passage: Passage
    score: float
    rank: int
    retrieval_method: str | None

    def __post_init__(self) -> None:
        """Validate and normalize direct construction.

        Normalizes integer scores to float values.
        """
        _validate_passage(self.passage)
        _validate_score(self.score)
        if isinstance(self.score, int) and not isinstance(self.score, bool):
            object.__setattr__(self, "score", float(self.score))
        _validate_rank(self.rank)
        _validate_optional_text("retrieval_method", self.retrieval_method)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "RetrievalEvidence":
        """Validate and construct RetrievalEvidence from a JSON-compatible mapping.

        Note on nested validation errors:
        If data['passage'] is a mapping and fails passage validation,
        PassageValidationError is propagated directly without being wrapped.
        DomainError serves as the top-level catch-all exception for any domain
        parsing failure.
        """
        if not isinstance(data, Mapping):
            raise EvidenceValidationError(
                "RetrievalEvidence metadata must be a mapping."
            )
        if any(not isinstance(field, str) for field in data):
            raise EvidenceValidationError(
                "RetrievalEvidence field names must be strings."
            )

        provided_fields = set(data)
        missing_fields = sorted(_EVIDENCE_FIELDS - provided_fields)
        if missing_fields:
            raise EvidenceValidationError(
                "RetrievalEvidence is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided_fields - _EVIDENCE_FIELDS)
        if unknown_fields:
            raise EvidenceValidationError(
                "RetrievalEvidence contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        raw_passage = data["passage"]
        if isinstance(raw_passage, Mapping):
            passage = Passage.from_mapping(cast(Mapping[str, object], raw_passage))
        elif isinstance(raw_passage, Passage):
            passage = raw_passage
        else:
            raise EvidenceValidationError(
                "passage must be a Passage object or mapping."
            )

        raw_score = data["score"]
        _validate_score(raw_score)
        score = float(cast(int | float, raw_score))

        return cls(
            passage=passage,
            score=score,
            rank=cast(int, data["rank"]),
            retrieval_method=cast(str | None, data["retrieval_method"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "passage": self.passage.to_mapping(),
            "score": self.score,
            "rank": self.rank,
            "retrieval_method": self.retrieval_method,
        }


def _validate_passage(value: object) -> None:
    if not isinstance(value, Passage):
        raise EvidenceValidationError("passage must be an instance of Passage.")


def _validate_score(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceValidationError("score must be a finite float.")
    float_value = float(value)
    if not math.isfinite(float_value):
        raise EvidenceValidationError(
            "score must be a finite number (not NaN or Infinity)."
        )


def _validate_rank(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EvidenceValidationError("rank must be a positive integer (>= 1).")


def _validate_optional_text(field: str, value: object) -> None:
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise EvidenceValidationError(
                f"{field} must be a non-empty string or None."
            )
