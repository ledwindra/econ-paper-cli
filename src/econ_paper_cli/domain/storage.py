"""Domain contract for storage paper records, provenance, and ingestion metadata."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from econ_paper_cli.domain.errors import StorageRecordValidationError
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage

_SHA256_HEX_PATTERN = re.compile(r"[a-f0-9]{64}")
_VALID_COMPLETION_STATUSES = frozenset({"completed", "failed", "partial"})


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Immutable domain representation of source document provenance."""

    source_path: str
    source_format: str
    source_file_size: int
    content_checksum: str
    markdown_path: str
    extraction_method: str
    created_at: str

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_nonempty_text("source_path", self.source_path)
        _validate_nonempty_text("source_format", self.source_format)
        _validate_positive_int("source_file_size", self.source_file_size)
        _validate_checksum(self.content_checksum)
        _validate_nonempty_text("markdown_path", self.markdown_path)
        _validate_nonempty_text("extraction_method", self.extraction_method)
        _validate_nonempty_text("created_at", self.created_at)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "SourceProvenance":
        """Validate and construct SourceProvenance from a mapping."""
        if not isinstance(data, Mapping):
            raise StorageRecordValidationError(
                "SourceProvenance metadata must be a mapping."
            )
        expected_fields = {
            "source_path",
            "source_format",
            "source_file_size",
            "content_checksum",
            "markdown_path",
            "extraction_method",
            "created_at",
        }
        provided_fields = set(data)
        missing = sorted(expected_fields - provided_fields)
        if missing:
            raise StorageRecordValidationError(
                f"SourceProvenance missing fields: {', '.join(missing)}."
            )
        unknown = sorted(provided_fields - expected_fields)
        if unknown:
            raise StorageRecordValidationError(
                f"SourceProvenance contains unknown fields: {', '.join(unknown)}."
            )

        return cls(
            source_path=cast(str, data["source_path"]),
            source_format=cast(str, data["source_format"]),
            source_file_size=cast(int, data["source_file_size"]),
            content_checksum=cast(str, data["content_checksum"]),
            markdown_path=cast(str, data["markdown_path"]),
            extraction_method=cast(str, data["extraction_method"]),
            created_at=cast(str, data["created_at"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "source_file_size": self.source_file_size,
            "content_checksum": self.content_checksum,
            "markdown_path": self.markdown_path,
            "extraction_method": self.extraction_method,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ConversionSettings:
    """Immutable domain representation of document conversion settings."""

    conversion_version: str
    ocr_enabled: bool
    parameters: dict[str, object]

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_nonempty_text("conversion_version", self.conversion_version)
        if not isinstance(self.ocr_enabled, bool):
            raise StorageRecordValidationError("ocr_enabled must be a boolean.")
        if not isinstance(self.parameters, dict):
            raise StorageRecordValidationError("parameters must be a dictionary.")

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ConversionSettings":
        """Validate and construct ConversionSettings from a mapping."""
        if not isinstance(data, Mapping):
            raise StorageRecordValidationError(
                "ConversionSettings metadata must be a mapping."
            )
        expected_fields = {"conversion_version", "ocr_enabled", "parameters"}
        provided_fields = set(data)
        missing = sorted(expected_fields - provided_fields)
        if missing:
            raise StorageRecordValidationError(
                f"ConversionSettings missing fields: {', '.join(missing)}."
            )
        unknown = sorted(provided_fields - expected_fields)
        if unknown:
            raise StorageRecordValidationError(
                f"ConversionSettings contains unknown fields: {', '.join(unknown)}."
            )

        raw_params = data["parameters"]
        if not isinstance(raw_params, dict):
            raise StorageRecordValidationError("parameters must be a dictionary.")

        return cls(
            conversion_version=cast(str, data["conversion_version"]),
            ocr_enabled=cast(bool, data["ocr_enabled"]),
            parameters=cast(dict[str, object], dict(raw_params)),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "conversion_version": self.conversion_version,
            "ocr_enabled": self.ocr_enabled,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True, slots=True)
class IngestionWarning:
    """Immutable domain representation of an ingestion warning."""

    warning_code: str
    message: str
    created_at: str | None = None

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_nonempty_text("warning_code", self.warning_code)
        _validate_nonempty_text("message", self.message)
        if self.created_at is not None:
            _validate_nonempty_text("created_at", self.created_at)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "IngestionWarning":
        """Validate and construct IngestionWarning from a mapping."""
        if not isinstance(data, Mapping):
            raise StorageRecordValidationError(
                "IngestionWarning metadata must be a mapping."
            )
        if "warning_code" not in data or "message" not in data:
            raise StorageRecordValidationError(
                "IngestionWarning requires 'warning_code' and 'message'."
            )
        created_at = data.get("created_at")
        return cls(
            warning_code=cast(str, data["warning_code"]),
            message=cast(str, data["message"]),
            created_at=cast(str | None, created_at),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        res: dict[str, object] = {
            "warning_code": self.warning_code,
            "message": self.message,
        }
        if self.created_at is not None:
            res["created_at"] = self.created_at
        return res


@dataclass(frozen=True, slots=True)
class IngestionCompletion:
    """Immutable domain representation of ingestion completion metadata."""

    status: str
    completed_at: str
    passage_count: int
    warning_count: int
    error_message: str | None = None

    def __post_init__(self) -> None:
        """Validate direct construction."""
        if self.status not in _VALID_COMPLETION_STATUSES:
            raise StorageRecordValidationError(
                f"status must be one of {sorted(_VALID_COMPLETION_STATUSES)}."
            )
        _validate_nonempty_text("completed_at", self.completed_at)
        _validate_nonnegative_int("passage_count", self.passage_count)
        _validate_nonnegative_int("warning_count", self.warning_count)
        if self.error_message is not None:
            _validate_nonempty_text("error_message", self.error_message)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "IngestionCompletion":
        """Validate and construct IngestionCompletion from a mapping."""
        if not isinstance(data, Mapping):
            raise StorageRecordValidationError(
                "IngestionCompletion metadata must be a mapping."
            )
        expected_fields = {
            "status",
            "completed_at",
            "passage_count",
            "warning_count",
            "error_message",
        }
        provided_fields = set(data)
        missing = sorted(expected_fields - provided_fields)
        if missing:
            raise StorageRecordValidationError(
                f"IngestionCompletion missing fields: {', '.join(missing)}."
            )
        unknown = sorted(provided_fields - expected_fields)
        if unknown:
            raise StorageRecordValidationError(
                f"IngestionCompletion contains unknown fields: {', '.join(unknown)}."
            )

        return cls(
            status=cast(str, data["status"]),
            completed_at=cast(str, data["completed_at"]),
            passage_count=cast(int, data["passage_count"]),
            warning_count=cast(int, data["warning_count"]),
            error_message=cast(str | None, data["error_message"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "status": self.status,
            "completed_at": self.completed_at,
            "passage_count": self.passage_count,
            "warning_count": self.warning_count,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class PaperRecord:
    """Immutable domain representation of a paper storage record."""

    paper: Paper
    passages: tuple[Passage, ...]
    source_provenance: SourceProvenance
    conversion_settings: ConversionSettings
    warnings: tuple[IngestionWarning, ...]
    completion: IngestionCompletion

    def __post_init__(self) -> None:
        """Validate structural and referential integrity."""
        if not isinstance(self.paper, Paper):
            raise StorageRecordValidationError("paper must be a Paper instance.")
        if not isinstance(self.passages, tuple):
            raise StorageRecordValidationError(
                "passages must be a tuple of Passage instances."
            )
        for idx, passage in enumerate(self.passages):
            if not isinstance(passage, Passage):
                raise StorageRecordValidationError(
                    f"passages[{idx}] must be a Passage instance."
                )
            if passage.paper_id != self.paper.paper_id:
                raise StorageRecordValidationError(
                    f"Passage '{passage.passage_id}' paper_id '{passage.paper_id}' "
                    f"does not match paper paper_id '{self.paper.paper_id}'."
                )
        if not isinstance(self.source_provenance, SourceProvenance):
            raise StorageRecordValidationError(
                "source_provenance must be a SourceProvenance instance."
            )
        if not isinstance(self.conversion_settings, ConversionSettings):
            raise StorageRecordValidationError(
                "conversion_settings must be a ConversionSettings instance."
            )
        if not isinstance(self.warnings, tuple):
            raise StorageRecordValidationError(
                "warnings must be a tuple of IngestionWarning instances."
            )
        for idx, warning in enumerate(self.warnings):
            if not isinstance(warning, IngestionWarning):
                raise StorageRecordValidationError(
                    f"warnings[{idx}] must be an IngestionWarning instance."
                )
        if not isinstance(self.completion, IngestionCompletion):
            raise StorageRecordValidationError(
                "completion must be an IngestionCompletion instance."
            )
        if self.completion.passage_count != len(self.passages):
            raise StorageRecordValidationError(
                f"completion.passage_count ({self.completion.passage_count}) does "
                f"not match number of passages ({len(self.passages)})."
            )
        if self.completion.warning_count != len(self.warnings):
            raise StorageRecordValidationError(
                f"completion.warning_count ({self.completion.warning_count}) does "
                f"not match number of warnings ({len(self.warnings)})."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "PaperRecord":
        """Validate and construct PaperRecord from a mapping."""
        if not isinstance(data, Mapping):
            raise StorageRecordValidationError(
                "PaperRecord metadata must be a mapping."
            )
        expected_fields = {
            "paper",
            "passages",
            "source_provenance",
            "conversion_settings",
            "warnings",
            "completion",
        }
        provided_fields = set(data)
        missing = sorted(expected_fields - provided_fields)
        if missing:
            raise StorageRecordValidationError(
                f"PaperRecord missing fields: {', '.join(missing)}."
            )
        unknown = sorted(provided_fields - expected_fields)
        if unknown:
            raise StorageRecordValidationError(
                f"PaperRecord contains unknown fields: {', '.join(unknown)}."
            )

        raw_paper = data["paper"]
        if not isinstance(raw_paper, Mapping):
            raise StorageRecordValidationError("paper must be a mapping.")
        paper = Paper.from_mapping(cast(Mapping[str, object], raw_paper))

        raw_passages = data["passages"]
        if not isinstance(raw_passages, (list, tuple)):
            raise StorageRecordValidationError(
                "passages must be a sequence of mappings."
            )
        passages = tuple(
            Passage.from_mapping(cast(Mapping[str, object], item))
            for item in cast(Sequence[object], raw_passages)
        )

        raw_prov = data["source_provenance"]
        if not isinstance(raw_prov, Mapping):
            raise StorageRecordValidationError("source_provenance must be a mapping.")
        source_provenance = SourceProvenance.from_mapping(
            cast(Mapping[str, object], raw_prov)
        )

        raw_sett = data["conversion_settings"]
        if not isinstance(raw_sett, Mapping):
            raise StorageRecordValidationError("conversion_settings must be a mapping.")
        conversion_settings = ConversionSettings.from_mapping(
            cast(Mapping[str, object], raw_sett)
        )

        raw_warns = data["warnings"]
        if not isinstance(raw_warns, (list, tuple)):
            raise StorageRecordValidationError(
                "warnings must be a sequence of mappings."
            )
        warnings = tuple(
            IngestionWarning.from_mapping(cast(Mapping[str, object], item))
            for item in cast(Sequence[object], raw_warns)
        )

        raw_comp = data["completion"]
        if not isinstance(raw_comp, Mapping):
            raise StorageRecordValidationError("completion must be a mapping.")
        completion = IngestionCompletion.from_mapping(
            cast(Mapping[str, object], raw_comp)
        )

        return cls(
            paper=paper,
            passages=passages,
            source_provenance=source_provenance,
            conversion_settings=conversion_settings,
            warnings=warnings,
            completion=completion,
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "paper": self.paper.to_mapping(),
            "passages": [p.to_mapping() for p in self.passages],
            "source_provenance": self.source_provenance.to_mapping(),
            "conversion_settings": self.conversion_settings.to_mapping(),
            "warnings": [w.to_mapping() for w in self.warnings],
            "completion": self.completion.to_mapping(),
        }


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StorageRecordValidationError(f"{field} must be a non-empty string.")


def _validate_checksum(value: object) -> None:
    if not isinstance(value, str) or _SHA256_HEX_PATTERN.fullmatch(value) is None:
        raise StorageRecordValidationError(
            "content_checksum must be a 64-character lowercase hex string."
        )


def _validate_positive_int(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StorageRecordValidationError(
            f"{field} must be a positive integer (>= 1)."
        )


def _validate_nonnegative_int(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StorageRecordValidationError(f"{field} must be a non-negative integer.")
