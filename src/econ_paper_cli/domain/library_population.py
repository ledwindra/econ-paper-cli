"""Typed outcomes for analysis-driven early-section library population."""

from dataclasses import dataclass
from enum import Enum


class LibraryPopulationStatus(str, Enum):
    """Terminal outcome for one paper's early-section library workflow."""

    STORED = "stored"
    REUSED = "reused"
    NO_USABLE_SECTIONS = "no_usable_sections"
    NOT_ELIGIBLE = "not_eligible"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LibraryPopulationResult:
    """Deterministic, inspectable library outcome exposed to CLI adapters."""

    status: LibraryPopulationStatus
    paper_id: str | None = None
    conversion_fingerprint: str | None = None
    passage_count: int = 0
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LibraryPopulationStatus):
            raise TypeError("status must be a LibraryPopulationStatus.")
        for name, value in (
            ("paper_id", self.paper_id),
            ("conversion_fingerprint", self.conversion_fingerprint),
            ("error_message", self.error_message),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None.")
        if (
            isinstance(self.passage_count, bool)
            or not isinstance(self.passage_count, int)
            or self.passage_count < 0
        ):
            raise ValueError("passage_count must be a non-negative integer.")
        if self.status in (
            LibraryPopulationStatus.STORED,
            LibraryPopulationStatus.REUSED,
        ):
            if self.paper_id is None or self.conversion_fingerprint is None:
                raise ValueError("stored/reused outcomes require identity fields.")
            if self.passage_count < 1:
                raise ValueError("stored/reused outcomes require passages.")
        elif self.passage_count != 0:
            raise ValueError("non-stored outcomes cannot report passages.")
        if (self.status is LibraryPopulationStatus.FAILED) != (
            self.error_message is not None
        ):
            raise ValueError("only failed outcomes require error_message.")
