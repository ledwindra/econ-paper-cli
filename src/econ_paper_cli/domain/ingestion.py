"""Domain models for ingestion preflight results and candidate status."""

import re
from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.domain.errors import IngestionValidationError

_SHA256_HEX_PATTERN = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True, slots=True)
class PreflightCandidate:
    """Immutable representation of a discovered PDF candidate for ingestion preflight."""

    source_path: Path
    file_size_bytes: int
    content_checksum: str
    is_stored: bool
    is_batch_duplicate: bool
    duplicate_of_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate candidate fields."""
        if not isinstance(self.source_path, Path):
            raise IngestionValidationError("source_path must be a pathlib.Path.")
        if (
            isinstance(self.file_size_bytes, bool)
            or not isinstance(self.file_size_bytes, int)
            or self.file_size_bytes < 1
        ):
            raise IngestionValidationError(
                "file_size_bytes must be a positive integer (>= 1)."
            )
        if (
            not isinstance(self.content_checksum, str)
            or _SHA256_HEX_PATTERN.fullmatch(self.content_checksum) is None
        ):
            raise IngestionValidationError(
                "content_checksum must be a 64-character lowercase hex string."
            )
        if not isinstance(self.is_stored, bool):
            raise IngestionValidationError("is_stored must be a boolean.")
        if not isinstance(self.is_batch_duplicate, bool):
            raise IngestionValidationError("is_batch_duplicate must be a boolean.")
        if self.duplicate_of_path is not None and not isinstance(
            self.duplicate_of_path, Path
        ):
            raise IngestionValidationError(
                "duplicate_of_path must be a pathlib.Path or None."
            )
        if self.is_batch_duplicate and self.duplicate_of_path is None:
            raise IngestionValidationError(
                "duplicate_of_path must be set when is_batch_duplicate is True."
            )


@dataclass(frozen=True, slots=True)
class IngestionPreflightResult:
    """Immutable result of an ingestion preflight check."""

    target_path: Path
    candidates: tuple[PreflightCandidate, ...]
    new_candidate_count: int
    stored_candidate_count: int
    batch_duplicate_count: int
    total_candidate_count: int

    def __post_init__(self) -> None:
        """Validate preflight result fields."""
        if not isinstance(self.target_path, Path):
            raise IngestionValidationError("target_path must be a pathlib.Path.")
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(c, PreflightCandidate) for c in self.candidates
        ):
            raise IngestionValidationError(
                "candidates must be a tuple of PreflightCandidate instances."
            )
        for field_name, val in (
            ("new_candidate_count", self.new_candidate_count),
            ("stored_candidate_count", self.stored_candidate_count),
            ("batch_duplicate_count", self.batch_duplicate_count),
            ("total_candidate_count", self.total_candidate_count),
        ):
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise IngestionValidationError(
                    f"{field_name} must be a non-negative int (got {val!r})."
                )

        expected_new = sum(
            1 for c in self.candidates if not c.is_stored and not c.is_batch_duplicate
        )
        expected_stored = sum(1 for c in self.candidates if c.is_stored)
        expected_batch_dup = sum(1 for c in self.candidates if c.is_batch_duplicate)
        expected_total = len(self.candidates)

        if self.new_candidate_count != expected_new:
            raise IngestionValidationError(
                f"new_candidate_count ({self.new_candidate_count}) does not match candidates "
                f"flag count ({expected_new})."
            )
        if self.stored_candidate_count != expected_stored:
            raise IngestionValidationError(
                f"stored_candidate_count ({self.stored_candidate_count}) does not match candidates "
                f"flag count ({expected_stored})."
            )
        if self.batch_duplicate_count != expected_batch_dup:
            raise IngestionValidationError(
                f"batch_duplicate_count ({self.batch_duplicate_count}) does not match candidates "
                f"flag count ({expected_batch_dup})."
            )
        if self.total_candidate_count != expected_total:
            raise IngestionValidationError(
                f"total_candidate_count ({self.total_candidate_count}) does not match candidates "
                f"length ({expected_total})."
            )
