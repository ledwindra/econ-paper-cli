"""Immutable domain contracts for single-paper research-question analysis."""

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from econ_paper_cli.domain.errors import (
    IngestionEmptyDirectoryError,
    IngestionInvalidPathError,
    IngestionPathNotFoundError,
    IngestionPermissionError,
    IngestionReadError,
    IngestionUnsupportedFileError,
    PDFEncryptedError,
    PDFMalformedError,
    PDFParserError,
    PDFPermissionError,
    PDFReadError,
    PDFSourceNotFoundError,
    PDFSourceNotRegularFileError,
    SinglePaperAnalysisValidationError,
)
from econ_paper_cli.domain.ingestion import IngestionPreflightResult
from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_quality import (
    DEFAULT_PDF_QUALITY_SETTINGS,
    PDFExtractionQualityAssessment,
    PDFQualitySettings,
    PDFQualityStatus,
    PDFQualityWarning,
)
from econ_paper_cli.domain.pdf_sections import (
    _DISPLAY_LABELS,
    DEFAULT_PDF_SECTION_SETTINGS,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSettings,
    PDFSectionWarning,
)
from econ_paper_cli.domain.research_question import (
    DEFAULT_RESEARCH_QUESTION_SETTINGS,
    ResearchQuestionKind,
    ResearchQuestionResult,
    ResearchQuestionSettings,
    ResearchQuestionWarning,
)

_SHA256_HEX_PATTERN = re.compile(r"[a-f0-9]{64}")


class SinglePaperAnalysisStage(str, Enum):
    """Execution stages of single-paper analysis in canonical order."""

    PREFLIGHT = "preflight"
    EXTRACTION = "extraction"
    QUALITY_ASSESSMENT = "quality_assessment"
    SECTION_DETECTION = "section_detection"
    QUESTION_EXTRACTION = "question_extraction"


class SinglePaperAnalysisStatus(str, Enum):
    """Terminal outcome classification for single-paper analysis."""

    SUCCESS = "success"
    PREFLIGHT_FAILED = "preflight_failed"
    EXTRACTION_FAILED = "extraction_failed"
    QUALITY_HALTED = "quality_halted"
    QUESTION_EXTRACTION_HALTED = "question_extraction_halted"


class SinglePaperAnalysisFailureCode(str, Enum):
    """Stable, typed failure codes for single-paper analysis orchestration.

    Each code maps deterministically to a specific ``IngestionError`` or
    ``PDFExtractionError`` subclass, or to a structural contract violation
    detected by the service (e.g. directory input, multi-candidate batch).
    Codes are set only when ``status`` is ``PREFLIGHT_FAILED`` or
    ``EXTRACTION_FAILED``; all other statuses leave ``failure_code=None``.
    """

    # Preflight failure codes
    PATH_NOT_FOUND = "path_not_found"
    PATH_INVALID = "path_invalid"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    DIRECTORY_INPUT = "directory_input"
    MULTI_CANDIDATE_BATCH = "multi_candidate_batch"
    PREFLIGHT_PERMISSION_DENIED = "preflight_permission_denied"
    PREFLIGHT_READ_ERROR = "preflight_read_error"

    # Extraction failure codes
    PDF_NOT_FOUND = "pdf_not_found"
    PDF_NOT_REGULAR_FILE = "pdf_not_regular_file"
    PDF_PERMISSION_DENIED = "pdf_permission_denied"
    PDF_READ_ERROR = "pdf_read_error"
    PDF_MALFORMED = "pdf_malformed"
    PDF_ENCRYPTED = "pdf_encrypted"
    PDF_PARSER_ERROR = "pdf_parser_error"


class SinglePaperAnalysisWarningCode(str, Enum):
    """Stable warning identifiers for single-paper analysis orchestration."""

    QUALITY_HALTED = "quality_halted"
    QUESTION_EXTRACTION_HALTED = "question_extraction_halted"


_WARNING_MESSAGES = {
    SinglePaperAnalysisWarningCode.QUALITY_HALTED: (
        "Extraction quality was poor or unusable. "
        "Downstream section detection and generation were skipped."
    ),
    SinglePaperAnalysisWarningCode.QUESTION_EXTRACTION_HALTED: (
        "Research question extraction could not yield an evidence-backed question."
    ),
}
_WARNING_ORDER = {
    code: position for position, code in enumerate(SinglePaperAnalysisWarningCode)
}

_CANONICAL_SINGLE_PAPER_SETTINGS: dict[str, dict[str, object]] = {
    "single-paper-analysis-v1": {},
}

# Canonical stage sequence
_ALL_STAGES = tuple(SinglePaperAnalysisStage)

# Expected completed_stages per status (only successfully completed stages).
_STATUS_COMPLETED: dict[
    SinglePaperAnalysisStatus, tuple[SinglePaperAnalysisStage, ...]
] = {
    SinglePaperAnalysisStatus.PREFLIGHT_FAILED: (),
    SinglePaperAnalysisStatus.EXTRACTION_FAILED: (SinglePaperAnalysisStage.PREFLIGHT,),
    SinglePaperAnalysisStatus.QUALITY_HALTED: (
        SinglePaperAnalysisStage.PREFLIGHT,
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
    ),
    SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED: _ALL_STAGES,
    SinglePaperAnalysisStatus.SUCCESS: _ALL_STAGES,
}

# Expected failed_stage per status (None for halt/success statuses).
_STATUS_FAILED_STAGE: dict[
    SinglePaperAnalysisStatus, SinglePaperAnalysisStage | None
] = {
    SinglePaperAnalysisStatus.PREFLIGHT_FAILED: SinglePaperAnalysisStage.PREFLIGHT,
    SinglePaperAnalysisStatus.EXTRACTION_FAILED: SinglePaperAnalysisStage.EXTRACTION,
    SinglePaperAnalysisStatus.QUALITY_HALTED: None,
    SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED: None,
    SinglePaperAnalysisStatus.SUCCESS: None,
}

# Expected skipped_stages per status.
_STATUS_SKIPPED: dict[
    SinglePaperAnalysisStatus, tuple[SinglePaperAnalysisStage, ...]
] = {
    SinglePaperAnalysisStatus.PREFLIGHT_FAILED: (
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    ),
    SinglePaperAnalysisStatus.EXTRACTION_FAILED: (
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    ),
    SinglePaperAnalysisStatus.QUALITY_HALTED: (
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    ),
    SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED: (),
    SinglePaperAnalysisStatus.SUCCESS: (),
}

# Statuses that require a failure_code.
_FAILURE_CODE_STATUSES = frozenset(
    {
        SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
        SinglePaperAnalysisStatus.EXTRACTION_FAILED,
    }
)

# Valid failure_code values per status.
_PREFLIGHT_FAILURE_CODES = frozenset(
    {
        SinglePaperAnalysisFailureCode.PATH_NOT_FOUND,
        SinglePaperAnalysisFailureCode.PATH_INVALID,
        SinglePaperAnalysisFailureCode.UNSUPPORTED_FILE_TYPE,
        SinglePaperAnalysisFailureCode.DIRECTORY_INPUT,
        SinglePaperAnalysisFailureCode.MULTI_CANDIDATE_BATCH,
        SinglePaperAnalysisFailureCode.PREFLIGHT_PERMISSION_DENIED,
        SinglePaperAnalysisFailureCode.PREFLIGHT_READ_ERROR,
    }
)
_EXTRACTION_FAILURE_CODES = frozenset(
    {
        SinglePaperAnalysisFailureCode.PDF_NOT_FOUND,
        SinglePaperAnalysisFailureCode.PDF_NOT_REGULAR_FILE,
        SinglePaperAnalysisFailureCode.PDF_PERMISSION_DENIED,
        SinglePaperAnalysisFailureCode.PDF_READ_ERROR,
        SinglePaperAnalysisFailureCode.PDF_MALFORMED,
        SinglePaperAnalysisFailureCode.PDF_ENCRYPTED,
        SinglePaperAnalysisFailureCode.PDF_PARSER_ERROR,
    }
)

# A multi-candidate result is a structural guard produced by the orchestrator,
# rather than a caught stage exception, so it has no failure_cause.
_CAUSELESS_FAILURE_CODES = frozenset(
    {SinglePaperAnalysisFailureCode.MULTI_CANDIDATE_BATCH}
)

# Mapping each failure code to the exact exception base class its failure_cause
# must be an instance of.  DIRECTORY_INPUT and MULTI_CANDIDATE_BATCH are handled
# separately (DIRECTORY_INPUT may have no cause or IngestionEmptyDirectoryError;
# MULTI_CANDIDATE_BATCH must have no cause).
_FAILURE_CODE_CAUSE_TYPE: dict[SinglePaperAnalysisFailureCode, type[Exception]] = {
    SinglePaperAnalysisFailureCode.PATH_NOT_FOUND: IngestionPathNotFoundError,
    SinglePaperAnalysisFailureCode.PATH_INVALID: IngestionInvalidPathError,
    SinglePaperAnalysisFailureCode.UNSUPPORTED_FILE_TYPE: IngestionUnsupportedFileError,
    SinglePaperAnalysisFailureCode.PREFLIGHT_PERMISSION_DENIED: IngestionPermissionError,
    SinglePaperAnalysisFailureCode.PREFLIGHT_READ_ERROR: IngestionReadError,
    SinglePaperAnalysisFailureCode.PDF_NOT_FOUND: PDFSourceNotFoundError,
    SinglePaperAnalysisFailureCode.PDF_NOT_REGULAR_FILE: PDFSourceNotRegularFileError,
    SinglePaperAnalysisFailureCode.PDF_PERMISSION_DENIED: PDFPermissionError,
    SinglePaperAnalysisFailureCode.PDF_READ_ERROR: PDFReadError,
    SinglePaperAnalysisFailureCode.PDF_MALFORMED: PDFMalformedError,
    SinglePaperAnalysisFailureCode.PDF_ENCRYPTED: PDFEncryptedError,
    SinglePaperAnalysisFailureCode.PDF_PARSER_ERROR: PDFParserError,
}


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisSettings:
    """Versioned configuration for single-paper research-question analysis."""

    policy_version: str = "single-paper-analysis-v1"
    quality_settings: PDFQualitySettings = DEFAULT_PDF_QUALITY_SETTINGS
    section_settings: PDFSectionSettings = DEFAULT_PDF_SECTION_SETTINGS
    research_question_settings: ResearchQuestionSettings = (
        DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        if self.policy_version not in _CANONICAL_SINGLE_PAPER_SETTINGS:
            raise SinglePaperAnalysisValidationError(
                f"policy_version '{self.policy_version}' is not a recognized policy version."
            )
        if not isinstance(self.quality_settings, PDFQualitySettings):
            raise SinglePaperAnalysisValidationError(
                "quality_settings must be a PDFQualitySettings instance."
            )
        if not isinstance(self.section_settings, PDFSectionSettings):
            raise SinglePaperAnalysisValidationError(
                "section_settings must be a PDFSectionSettings instance."
            )
        if not isinstance(self.research_question_settings, ResearchQuestionSettings):
            raise SinglePaperAnalysisValidationError(
                "research_question_settings must be a ResearchQuestionSettings instance."
            )


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisWarning:
    """Actionable warning for single-paper analysis orchestration."""

    code: SinglePaperAnalysisWarningCode
    details: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, SinglePaperAnalysisWarningCode):
            raise SinglePaperAnalysisValidationError(
                "code must be a SinglePaperAnalysisWarningCode instance."
            )
        if self.details is not None:
            if not isinstance(self.details, str) or not self.details.strip():
                raise SinglePaperAnalysisValidationError(
                    "details must be a non-empty string or None."
                )

    @property
    def message(self) -> str:
        """Return the canonical message for this warning code."""
        base = _WARNING_MESSAGES[self.code]
        if self.details:
            return f"{base} Details: {self.details}"
        return base


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisResult:
    """Immutable composite result of single-paper research-question analysis.

    Stage outcome semantics:
    - ``completed_stages``: stages that executed and *succeeded*.
    - ``failed_stage``: the stage that failed (set for ``PREFLIGHT_FAILED`` and
      ``EXTRACTION_FAILED``; ``None`` for halt/success statuses).
    - ``skipped_stages``: stages not attempted because a prior stage failed/halted.
    - ``failure_code``: stable typed code identifying the exact failure reason
      (set for ``PREFLIGHT_FAILED`` and ``EXTRACTION_FAILED``; ``None`` otherwise).
    - ``failure_cause``: the original caught typed exception, where applicable;
      structural guards without an exception have no cause.

    Invariant: ``completed_stages + ((failed_stage,) if failed_stage else ()) +
    skipped_stages`` equals the canonical stage tuple in order.
    """

    policy_version: str
    source_path: Path
    checksum: str | None
    status: SinglePaperAnalysisStatus
    completed_stages: tuple[SinglePaperAnalysisStage, ...]
    failed_stage: SinglePaperAnalysisStage | None
    skipped_stages: tuple[SinglePaperAnalysisStage, ...]
    failure_code: SinglePaperAnalysisFailureCode | None
    preflight_result: IngestionPreflightResult | None
    extraction_result: PDFExtractionResult | None
    quality_assessment: PDFExtractionQualityAssessment | None
    section_result: PDFSectionDetectionResult | None
    research_question_result: ResearchQuestionResult | None
    warnings: tuple[SinglePaperAnalysisWarning, ...]
    error_message: str | None
    # Exception equality is identity-based. Excluding the retained cause keeps
    # repeated equivalent workflow results structurally comparable.
    failure_cause: Exception | None = field(default=None, compare=False)

    def __post_init__(self) -> None:  # noqa: C901
        _validate_nonempty_text("policy_version", self.policy_version)
        if self.policy_version not in _CANONICAL_SINGLE_PAPER_SETTINGS:
            raise SinglePaperAnalysisValidationError(
                f"policy_version '{self.policy_version}' is not a recognized policy version."
            )
        if not isinstance(self.source_path, Path):
            raise SinglePaperAnalysisValidationError(
                "source_path must be a Path instance."
            )
        if self.checksum is not None:
            _validate_nonempty_text("checksum", self.checksum)
        if not isinstance(self.status, SinglePaperAnalysisStatus):
            raise SinglePaperAnalysisValidationError(
                "status must be a SinglePaperAnalysisStatus instance."
            )
        if not isinstance(self.completed_stages, tuple) or not all(
            isinstance(s, SinglePaperAnalysisStage) for s in self.completed_stages
        ):
            raise SinglePaperAnalysisValidationError(
                "completed_stages must be a tuple of SinglePaperAnalysisStage instances."
            )
        if self.failed_stage is not None and not isinstance(
            self.failed_stage, SinglePaperAnalysisStage
        ):
            raise SinglePaperAnalysisValidationError(
                "failed_stage must be a SinglePaperAnalysisStage instance or None."
            )
        if not isinstance(self.skipped_stages, tuple) or not all(
            isinstance(s, SinglePaperAnalysisStage) for s in self.skipped_stages
        ):
            raise SinglePaperAnalysisValidationError(
                "skipped_stages must be a tuple of SinglePaperAnalysisStage instances."
            )
        if self.failure_code is not None and not isinstance(
            self.failure_code, SinglePaperAnalysisFailureCode
        ):
            raise SinglePaperAnalysisValidationError(
                "failure_code must be a SinglePaperAnalysisFailureCode instance or None."
            )
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(w, SinglePaperAnalysisWarning) for w in self.warnings
        ):
            raise SinglePaperAnalysisValidationError(
                "warnings must be a tuple of SinglePaperAnalysisWarning instances."
            )
        if self.error_message is not None:
            _validate_nonempty_text("error_message", self.error_message)
        if self.failure_cause is not None and not isinstance(
            self.failure_cause, Exception
        ):
            raise SinglePaperAnalysisValidationError(
                "failure_cause must be an Exception instance or None."
            )

        # Stage combination & sequence checks
        combined: tuple[SinglePaperAnalysisStage, ...]
        if self.failed_stage is not None:
            combined = (
                self.completed_stages + (self.failed_stage,) + self.skipped_stages
            )
        else:
            combined = self.completed_stages + self.skipped_stages
        if combined != _ALL_STAGES:
            raise SinglePaperAnalysisValidationError(
                f"completed_stages + failed_stage + skipped_stages must equal canonical "
                f"stage sequence {_ALL_STAGES}."
            )

        # Verify failed_stage matches status expectation
        expected_failed = _STATUS_FAILED_STAGE[self.status]
        if self.failed_stage != expected_failed:
            raise SinglePaperAnalysisValidationError(
                f"failed_stage {self.failed_stage!r} does not match expected "
                f"failed_stage {expected_failed!r} for status {self.status.value}."
            )

        # Verify completed_stages matches status expectation
        expected_completed = _STATUS_COMPLETED[self.status]
        if self.completed_stages != expected_completed:
            raise SinglePaperAnalysisValidationError(
                f"completed_stages {self.completed_stages!r} does not match expected "
                f"{expected_completed!r} for status {self.status.value}."
            )

        # failure_code is required for failure statuses, forbidden otherwise
        if self.status in _FAILURE_CODE_STATUSES:
            if self.failure_code is None:
                raise SinglePaperAnalysisValidationError(
                    f"failure_code is required for status {self.status.value}."
                )
            if self.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED:
                if self.failure_code not in _PREFLIGHT_FAILURE_CODES:
                    raise SinglePaperAnalysisValidationError(
                        f"failure_code {self.failure_code!r} is not valid for PREFLIGHT_FAILED."
                    )
            elif self.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED:
                if self.failure_code not in _EXTRACTION_FAILURE_CODES:
                    raise SinglePaperAnalysisValidationError(
                        f"failure_code {self.failure_code!r} is not valid for EXTRACTION_FAILED."
                    )
        else:
            if self.failure_code is not None:
                raise SinglePaperAnalysisValidationError(
                    f"failure_code must be None for status {self.status.value}."
                )

        if self.failure_code in _CAUSELESS_FAILURE_CODES:
            if self.failure_cause is not None:
                raise SinglePaperAnalysisValidationError(
                    f"failure code {self.failure_code.value} must not have a failure_cause."
                )
        elif self.failure_code is SinglePaperAnalysisFailureCode.DIRECTORY_INPUT:
            # DIRECTORY_INPUT may have no cause (structural directory rejection) or
            # an IngestionEmptyDirectoryError (empty-directory preflight error).
            if self.failure_cause is not None:
                if not isinstance(self.failure_cause, IngestionEmptyDirectoryError):
                    raise SinglePaperAnalysisValidationError(
                        f"DIRECTORY_INPUT failure_cause must be IngestionEmptyDirectoryError "
                        f"or None, got {type(self.failure_cause).__name__}."
                    )
                if self.error_message != str(self.failure_cause):
                    raise SinglePaperAnalysisValidationError(
                        "error_message must preserve the failure_cause message."
                    )
        elif self.status in _FAILURE_CODE_STATUSES:
            if self.failure_cause is None:
                raise SinglePaperAnalysisValidationError(
                    f"failure_cause is required for failure code {self.failure_code.value}."
                )
            expected_type = _FAILURE_CODE_CAUSE_TYPE.get(self.failure_code)
            if expected_type is not None and not isinstance(
                self.failure_cause, expected_type
            ):
                raise SinglePaperAnalysisValidationError(
                    f"failure_cause for code {self.failure_code.value} must be an instance of "
                    f"{expected_type.__name__}, got {type(self.failure_cause).__name__}."
                )
            if self.error_message != str(self.failure_cause):
                raise SinglePaperAnalysisValidationError(
                    "error_message must preserve the failure_cause message."
                )
        elif self.failure_cause is not None:
            raise SinglePaperAnalysisValidationError(
                f"failure_cause must be None for status {self.status.value}."
            )

        # Status-specific field invariants
        if self.status is SinglePaperAnalysisStatus.SUCCESS:
            if (
                self.preflight_result is None
                or self.extraction_result is None
                or self.quality_assessment is None
                or self.section_result is None
                or self.research_question_result is None
            ):
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status requires all stage results to be present."
                )
            if self.research_question_result.kind is ResearchQuestionKind.UNAVAILABLE:
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status requires an available research question."
                )
            if self.error_message is not None:
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status cannot have an error_message."
                )

        elif self.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED:
            if (
                self.extraction_result is not None
                or self.quality_assessment is not None
                or self.section_result is not None
                or self.research_question_result is not None
            ):
                raise SinglePaperAnalysisValidationError(
                    "PREFLIGHT_FAILED status must have None for downstream stage results."
                )
            if self.error_message is None:
                raise SinglePaperAnalysisValidationError(
                    "PREFLIGHT_FAILED status requires error_message."
                )

        elif self.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED:
            if (
                self.quality_assessment is not None
                or self.section_result is not None
                or self.research_question_result is not None
            ):
                raise SinglePaperAnalysisValidationError(
                    "EXTRACTION_FAILED status must have None for downstream stage results."
                )
            if self.error_message is None:
                raise SinglePaperAnalysisValidationError(
                    "EXTRACTION_FAILED status requires error_message."
                )

        elif self.status is SinglePaperAnalysisStatus.QUALITY_HALTED:
            if (
                self.section_result is not None
                or self.research_question_result is not None
            ):
                raise SinglePaperAnalysisValidationError(
                    "QUALITY_HALTED status must have None for section_result and research_question_result."
                )
            if (
                self.quality_assessment is None
                or self.quality_assessment.status
                not in (
                    PDFQualityStatus.LIKELY_NEEDS_OCR,
                    PDFQualityStatus.UNUSABLE,
                )
            ):
                raise SinglePaperAnalysisValidationError(
                    "QUALITY_HALTED status requires LIKELY_NEEDS_OCR or UNUSABLE quality assessment."
                )

        elif self.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED:
            if (
                self.research_question_result is None
                or self.research_question_result.kind
                not in (ResearchQuestionKind.UNAVAILABLE,)
            ):
                raise SinglePaperAnalysisValidationError(
                    "QUESTION_EXTRACTION_HALTED requires UNAVAILABLE research_question_result."
                )


def _validate_nonempty_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SinglePaperAnalysisValidationError(
            f"{field_name} must be a non-empty string."
        )


def _validate_checksum(value: object) -> None:
    if not isinstance(value, str) or _SHA256_HEX_PATTERN.fullmatch(value) is None:
        raise SinglePaperAnalysisValidationError(
            "content_checksum must be a 64-character lowercase hex string."
        )


def _validate_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SinglePaperAnalysisValidationError(
            f"{field_name} must be a positive integer (>= 1)."
        )


def _validate_nonnegative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SinglePaperAnalysisValidationError(
            f"{field_name} must be a non-negative integer."
        )


DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS = SinglePaperAnalysisSettings()


def compute_settings_fingerprint(settings: SinglePaperAnalysisSettings) -> str:
    """Compute a deterministic SHA-256 fingerprint of versioned analysis settings."""
    raw = {
        "policy_version": settings.policy_version,
        "quality_settings": dataclasses.asdict(settings.quality_settings),
        "section_settings": dataclasses.asdict(settings.section_settings),
        "research_question_settings": dataclasses.asdict(
            settings.research_question_settings
        ),
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_analysis_id(
    checksum: str | None,
    settings: SinglePaperAnalysisSettings,
    source_path: Path | str | None = None,
) -> str:
    """Compute a deterministic SHA-256 analysis identity hash."""
    settings_fp = compute_settings_fingerprint(settings)
    path_key = ""
    if checksum is None and source_path is not None:
        path_key = str(Path(source_path).resolve())
    raw = {
        "checksum": (checksum or "").lower(),
        "policy_version": settings.policy_version,
        "settings_fingerprint": settings_fp,
        "source_path": path_key,
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisSectionSpanRecord:
    """Persisted page-local character span metadata for a section."""

    page_number: int
    start_character_offset: int
    end_character_offset: int
    ordinal_position: int

    def __post_init__(self) -> None:
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise SinglePaperAnalysisValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )
        _validate_nonnegative_int("ordinal_position", self.ordinal_position)


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisSectionBoundaryEvidenceRecord:
    """Persisted page-local character span metadata for section boundary evidence."""

    page_number: int
    start_character_offset: int
    end_character_offset: int
    evidence_type: str
    description: str
    ordinal_position: int

    def __post_init__(self) -> None:
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise SinglePaperAnalysisValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )
        _validate_nonempty_text("evidence_type", self.evidence_type)
        _validate_nonempty_text("description", self.description)
        _validate_nonnegative_int("ordinal_position", self.ordinal_position)


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisSectionRecord:
    """Persisted section metadata with exact page-local spans.

    ``heading_text`` is always the canonical display label derived from
    ``section_kind`` (for example ``"Abstract"``). ``observed_heading_text``
    is the verbatim source heading when ``detection_method`` is
    ``EXPLICIT_HEADING``, and ``None`` when the section's boundaries were
    inferred without an observed heading (``IMPLICIT_FRONT_MATTER``).
    """

    section_kind: PDFSectionKind
    heading_text: str
    detection_method: PDFSectionDetectionMethod
    observed_heading_text: str | None
    page_start: int
    page_end: int
    spans: tuple[SinglePaperAnalysisSectionSpanRecord, ...]
    ordinal_position: int
    boundary_evidence: tuple[SinglePaperAnalysisSectionBoundaryEvidenceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.section_kind, PDFSectionKind):
            raise SinglePaperAnalysisValidationError(
                "section_kind must be a PDFSectionKind instance."
            )
        _validate_nonempty_text("heading_text", self.heading_text)
        expected_label = _DISPLAY_LABELS[self.section_kind]
        if self.heading_text != expected_label:
            raise SinglePaperAnalysisValidationError(
                f"heading_text '{self.heading_text}' must match canonical label "
                f"'{expected_label}' for section_kind '{self.section_kind.value}'."
            )
        if not isinstance(self.detection_method, PDFSectionDetectionMethod):
            raise SinglePaperAnalysisValidationError(
                "detection_method must be a PDFSectionDetectionMethod instance."
            )
        if not isinstance(self.boundary_evidence, tuple) or not all(
            isinstance(ev, SinglePaperAnalysisSectionBoundaryEvidenceRecord)
            for ev in self.boundary_evidence
        ):
            raise SinglePaperAnalysisValidationError(
                "boundary_evidence must be a tuple of SinglePaperAnalysisSectionBoundaryEvidenceRecord instances."
            )

        if self.detection_method is PDFSectionDetectionMethod.EXPLICIT_HEADING:
            _validate_nonempty_text("observed_heading_text", self.observed_heading_text)
            if self.boundary_evidence:
                raise SinglePaperAnalysisValidationError(
                    "boundary_evidence must be empty when detection_method is EXPLICIT_HEADING."
                )
        elif self.observed_heading_text is not None:
            raise SinglePaperAnalysisValidationError(
                "observed_heading_text must be None when detection_method is "
                "IMPLICIT_FRONT_MATTER."
            )
        else:
            if not self.boundary_evidence:
                raise SinglePaperAnalysisValidationError(
                    "boundary_evidence is required and cannot be empty when detection_method is IMPLICIT_FRONT_MATTER."
                )

        for idx, ev in enumerate(self.boundary_evidence):
            if ev.ordinal_position != idx:
                raise SinglePaperAnalysisValidationError(
                    f"boundary_evidence[{idx}] ordinal_position ({ev.ordinal_position}) does not match index ({idx})."
                )

        _validate_positive_int("page_start", self.page_start)
        _validate_positive_int("page_end", self.page_end)
        if self.page_start > self.page_end:
            raise SinglePaperAnalysisValidationError(
                "page_start cannot exceed page_end."
            )
        if not isinstance(self.spans, tuple) or not self.spans:
            raise SinglePaperAnalysisValidationError(
                "spans must be a non-empty tuple of SinglePaperAnalysisSectionSpanRecord."
            )
        if self.spans[0].page_number != self.page_start:
            raise SinglePaperAnalysisValidationError(
                "page_start must match the first span's page_number."
            )
        if self.spans[-1].page_number != self.page_end:
            raise SinglePaperAnalysisValidationError(
                "page_end must match the last span's page_number."
            )

        previous_page = 0
        previous_end_offset = 0
        for idx, span in enumerate(self.spans):
            if not isinstance(span, SinglePaperAnalysisSectionSpanRecord):
                raise SinglePaperAnalysisValidationError(
                    "spans elements must be SinglePaperAnalysisSectionSpanRecord instances."
                )
            if span.ordinal_position != idx:
                raise SinglePaperAnalysisValidationError(
                    f"span[{idx}] ordinal_position ({span.ordinal_position}) does not match index ({idx})."
                )
            if span.page_number < previous_page:
                raise SinglePaperAnalysisValidationError(
                    "spans must be ordered by page_number and offset."
                )
            if (
                span.page_number == previous_page
                and span.start_character_offset < previous_end_offset
            ):
                raise SinglePaperAnalysisValidationError(
                    "spans on the same page cannot overlap or be out of order."
                )
            previous_page = span.page_number
            previous_end_offset = span.end_character_offset
        _validate_nonnegative_int("ordinal_position", self.ordinal_position)


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisEvidenceRecord:
    """Persisted research-question evidence excerpt with exact provenance."""

    section_kind: PDFSectionKind
    excerpt_text: str
    page_number: int
    start_character_offset: int
    end_character_offset: int
    ordinal_position: int

    def __post_init__(self) -> None:
        if not isinstance(self.section_kind, PDFSectionKind):
            raise SinglePaperAnalysisValidationError(
                "section_kind must be a PDFSectionKind instance."
            )
        _validate_nonempty_text("excerpt_text", self.excerpt_text)
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise SinglePaperAnalysisValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )
        expected_len = self.end_character_offset - self.start_character_offset
        if len(self.excerpt_text) != expected_len:
            raise SinglePaperAnalysisValidationError(
                f"excerpt_text length ({len(self.excerpt_text)}) does not match "
                f"character offset span ({expected_len})."
            )
        _validate_nonnegative_int("ordinal_position", self.ordinal_position)


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisQuestionRecord:
    """Persisted research-question output metadata."""

    kind: ResearchQuestionKind
    question_text: str | None
    sections_used: tuple[PDFSectionKind, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ResearchQuestionKind):
            raise SinglePaperAnalysisValidationError(
                "kind must be a ResearchQuestionKind instance."
            )
        if not isinstance(self.sections_used, tuple) or not all(
            isinstance(sk, PDFSectionKind) for sk in self.sections_used
        ):
            raise SinglePaperAnalysisValidationError(
                "sections_used must be a tuple of PDFSectionKind instances."
            )
        if self.kind is ResearchQuestionKind.UNAVAILABLE:
            if self.question_text is not None:
                raise SinglePaperAnalysisValidationError(
                    "question_text must be None when kind is UNAVAILABLE."
                )
        else:
            if self.question_text is None:
                raise SinglePaperAnalysisValidationError(
                    "question_text is required when kind is available."
                )
            _validate_nonempty_text("question_text", self.question_text)
            if not self.sections_used:
                raise SinglePaperAnalysisValidationError(
                    "sections_used cannot be empty when question is available."
                )


@dataclass(frozen=True, slots=True)
class SinglePaperAnalysisRecord:
    """Immutable domain representation of a persisted single-paper analysis run."""

    analysis_id: str
    source_path: Path
    content_checksum: str | None
    status: SinglePaperAnalysisStatus
    completed_stages: tuple[SinglePaperAnalysisStage, ...]
    failed_stage: SinglePaperAnalysisStage | None
    skipped_stages: tuple[SinglePaperAnalysisStage, ...]
    failure_code: SinglePaperAnalysisFailureCode | None
    error_message: str | None
    quality_status: PDFQualityStatus | None
    settings: SinglePaperAnalysisSettings
    settings_fingerprint: str
    quality_warnings: tuple[PDFQualityWarning, ...]
    section_warnings: tuple[PDFSectionWarning, ...]
    research_question_warnings: tuple[ResearchQuestionWarning, ...]
    warnings: tuple[SinglePaperAnalysisWarning, ...]
    sections: tuple[SinglePaperAnalysisSectionRecord, ...]
    research_question: SinglePaperAnalysisQuestionRecord | None
    evidence: tuple[SinglePaperAnalysisEvidenceRecord, ...]
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:  # noqa: C901
        _validate_nonempty_text("analysis_id", self.analysis_id)
        if not isinstance(self.source_path, Path):
            raise SinglePaperAnalysisValidationError("source_path must be a Path.")
        if self.source_path != self.source_path.resolve():
            raise SinglePaperAnalysisValidationError(
                "source_path must be canonical/absolute."
            )
        if self.content_checksum is not None:
            _validate_checksum(self.content_checksum)
        if not isinstance(self.status, SinglePaperAnalysisStatus):
            raise SinglePaperAnalysisValidationError(
                "status must be a SinglePaperAnalysisStatus instance."
            )
        if not isinstance(self.completed_stages, tuple) or not all(
            isinstance(s, SinglePaperAnalysisStage) for s in self.completed_stages
        ):
            raise SinglePaperAnalysisValidationError(
                "completed_stages must be a tuple of SinglePaperAnalysisStage instances."
            )
        if self.failed_stage is not None and not isinstance(
            self.failed_stage, SinglePaperAnalysisStage
        ):
            raise SinglePaperAnalysisValidationError(
                "failed_stage must be a SinglePaperAnalysisStage instance or None."
            )
        if not isinstance(self.skipped_stages, tuple) or not all(
            isinstance(s, SinglePaperAnalysisStage) for s in self.skipped_stages
        ):
            raise SinglePaperAnalysisValidationError(
                "skipped_stages must be a tuple of SinglePaperAnalysisStage instances."
            )
        if self.failure_code is not None and not isinstance(
            self.failure_code, SinglePaperAnalysisFailureCode
        ):
            raise SinglePaperAnalysisValidationError(
                "failure_code must be a SinglePaperAnalysisFailureCode instance or None."
            )
        if self.error_message is not None:
            _validate_nonempty_text("error_message", self.error_message)
        if self.quality_status is not None and not isinstance(
            self.quality_status, PDFQualityStatus
        ):
            raise SinglePaperAnalysisValidationError(
                "quality_status must be a PDFQualityStatus instance or None."
            )
        if not isinstance(self.settings, SinglePaperAnalysisSettings):
            raise SinglePaperAnalysisValidationError(
                "settings must be a SinglePaperAnalysisSettings instance."
            )
        _validate_nonempty_text("settings_fingerprint", self.settings_fingerprint)
        expected_fp = compute_settings_fingerprint(self.settings)
        if self.settings_fingerprint != expected_fp:
            raise SinglePaperAnalysisValidationError(
                f"settings_fingerprint '{self.settings_fingerprint}' does not match expected '{expected_fp}'."
            )

        expected_id = compute_analysis_id(
            self.content_checksum, self.settings, self.source_path
        )
        if self.analysis_id != expected_id:
            raise SinglePaperAnalysisValidationError(
                f"analysis_id '{self.analysis_id}' does not match expected '{expected_id}'."
            )

        if not isinstance(self.quality_warnings, tuple) or not all(
            isinstance(w, PDFQualityWarning) for w in self.quality_warnings
        ):
            raise SinglePaperAnalysisValidationError(
                "quality_warnings must be a tuple of PDFQualityWarning instances."
            )
        if not isinstance(self.section_warnings, tuple) or not all(
            isinstance(w, PDFSectionWarning) for w in self.section_warnings
        ):
            raise SinglePaperAnalysisValidationError(
                "section_warnings must be a tuple of PDFSectionWarning instances."
            )
        if not isinstance(self.research_question_warnings, tuple) or not all(
            isinstance(w, ResearchQuestionWarning)
            for w in self.research_question_warnings
        ):
            raise SinglePaperAnalysisValidationError(
                "research_question_warnings must be a tuple of ResearchQuestionWarning instances."
            )
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(w, SinglePaperAnalysisWarning) for w in self.warnings
        ):
            raise SinglePaperAnalysisValidationError(
                "warnings must be a tuple of SinglePaperAnalysisWarning instances."
            )
        if not isinstance(self.sections, tuple) or not all(
            isinstance(sec, SinglePaperAnalysisSectionRecord) for sec in self.sections
        ):
            raise SinglePaperAnalysisValidationError(
                "sections must be a tuple of SinglePaperAnalysisSectionRecord instances."
            )
        for idx, sec in enumerate(self.sections):
            if sec.ordinal_position != idx:
                raise SinglePaperAnalysisValidationError(
                    f"sections[{idx}] ordinal_position ({sec.ordinal_position}) "
                    f"does not match index ({idx})."
                )
        if self.research_question is not None and not isinstance(
            self.research_question, SinglePaperAnalysisQuestionRecord
        ):
            raise SinglePaperAnalysisValidationError(
                "research_question must be a SinglePaperAnalysisQuestionRecord instance or None."
            )
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(ev, SinglePaperAnalysisEvidenceRecord) for ev in self.evidence
        ):
            raise SinglePaperAnalysisValidationError(
                "evidence must be a tuple of SinglePaperAnalysisEvidenceRecord instances."
            )
        for idx, ev in enumerate(self.evidence):
            if ev.ordinal_position != idx:
                raise SinglePaperAnalysisValidationError(
                    f"evidence[{idx}] ordinal_position ({ev.ordinal_position}) "
                    f"does not match index ({idx})."
                )
        _validate_nonempty_text("created_at", self.created_at)
        _validate_nonempty_text("updated_at", self.updated_at)

        # Stage tuple sequence invariants
        expected_completed = _STATUS_COMPLETED[self.status]
        if self.completed_stages != expected_completed:
            raise SinglePaperAnalysisValidationError(
                f"completed_stages {self.completed_stages} does not match expected {expected_completed} for status {self.status}."
            )
        expected_failed = _STATUS_FAILED_STAGE[self.status]
        if self.failed_stage != expected_failed:
            raise SinglePaperAnalysisValidationError(
                f"failed_stage '{self.failed_stage}' does not match expected '{expected_failed}' for status {self.status}."
            )
        expected_skipped = _STATUS_SKIPPED[self.status]
        if self.skipped_stages != expected_skipped:
            raise SinglePaperAnalysisValidationError(
                f"skipped_stages {self.skipped_stages} does not match expected {expected_skipped} for status {self.status}."
            )
        if self.status in (
            SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            SinglePaperAnalysisStatus.EXTRACTION_FAILED,
        ):
            if self.failure_code is None:
                raise SinglePaperAnalysisValidationError(
                    f"failure_code is required for failed status {self.status}."
                )
            if (
                self.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
                and self.failure_code not in _PREFLIGHT_FAILURE_CODES
            ):
                raise SinglePaperAnalysisValidationError(
                    f"failure_code '{self.failure_code}' is not a valid preflight failure code."
                )
            if (
                self.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
                and self.failure_code not in _EXTRACTION_FAILURE_CODES
            ):
                raise SinglePaperAnalysisValidationError(
                    f"failure_code '{self.failure_code}' is not a valid extraction failure code."
                )
        else:
            if self.failure_code is not None:
                raise SinglePaperAnalysisValidationError(
                    f"failure_code must be None for non-failure status {self.status}."
                )

        # Status Invariant validations
        if self.status is SinglePaperAnalysisStatus.SUCCESS:
            if not self.sections:
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status requires non-empty detected sections."
                )
            if (
                self.research_question is None
                or self.research_question.kind is ResearchQuestionKind.UNAVAILABLE
            ):
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status requires an available research question."
                )
            if not self.evidence:
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status requires non-empty research-question evidence."
                )

        elif self.status is SinglePaperAnalysisStatus.QUALITY_HALTED:
            if self.sections:
                raise SinglePaperAnalysisValidationError(
                    "QUALITY_HALTED status cannot contain stored sections."
                )
            if self.research_question is not None:
                raise SinglePaperAnalysisValidationError(
                    "QUALITY_HALTED status cannot contain stored research question."
                )
            if self.evidence:
                raise SinglePaperAnalysisValidationError(
                    "QUALITY_HALTED status cannot contain stored evidence."
                )

        elif self.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED:
            if self.research_question is None or self.research_question.kind not in (
                ResearchQuestionKind.UNAVAILABLE,
            ):
                raise SinglePaperAnalysisValidationError(
                    "QUESTION_EXTRACTION_HALTED requires UNAVAILABLE research_question."
                )
            if self.evidence:
                raise SinglePaperAnalysisValidationError(
                    "UNAVAILABLE research question cannot contain evidence."
                )

        elif self.status in (
            SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            SinglePaperAnalysisStatus.EXTRACTION_FAILED,
        ):
            if self.sections or self.research_question is not None or self.evidence:
                raise SinglePaperAnalysisValidationError(
                    "Failed statuses cannot contain sections, research questions, or evidence."
                )

        # Referential integrity checks: Evidence must match a detected section span
        section_map = {sec.section_kind: sec for sec in self.sections}
        for ev in self.evidence:
            if ev.section_kind not in section_map:
                raise SinglePaperAnalysisValidationError(
                    f"Evidence section_kind '{ev.section_kind}' does not match any detected section."
                )
            matching_sec = section_map[ev.section_kind]
            span_found = False
            for sp in matching_sec.spans:
                if sp.page_number == ev.page_number:
                    if (
                        ev.start_character_offset >= sp.start_character_offset
                        and ev.end_character_offset <= sp.end_character_offset
                    ):
                        span_found = True
                        break
            if not span_found:
                raise SinglePaperAnalysisValidationError(
                    f"Evidence excerpt span [{ev.start_character_offset}, {ev.end_character_offset}] on page {ev.page_number} "
                    f"does not fall within any section span of '{ev.section_kind.value}'."
                )

        if (
            self.research_question is not None
            and self.research_question.kind is not ResearchQuestionKind.UNAVAILABLE
        ):
            evidence_section_kinds = {ev.section_kind for ev in self.evidence}
            if set(self.research_question.sections_used) != evidence_section_kinds:
                raise SinglePaperAnalysisValidationError(
                    "research_question.sections_used must match the section_kinds in evidence."
                )

    @classmethod
    def from_result(
        cls,
        result: SinglePaperAnalysisResult,
        settings: SinglePaperAnalysisSettings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> "SinglePaperAnalysisRecord":
        """Construct a SinglePaperAnalysisRecord from a SinglePaperAnalysisResult."""
        if result.policy_version != settings.policy_version:
            raise SinglePaperAnalysisValidationError(
                f"result.policy_version '{result.policy_version}' does not match settings.policy_version '{settings.policy_version}'."
            )
        if (
            result.quality_assessment is not None
            and result.quality_assessment.policy_version
            != settings.quality_settings.policy_version
        ):
            raise SinglePaperAnalysisValidationError(
                f"quality_assessment.policy_version '{result.quality_assessment.policy_version}' "
                f"does not match settings.quality_settings.policy_version '{settings.quality_settings.policy_version}'."
            )
        if (
            result.section_result is not None
            and result.section_result.policy_version
            != settings.section_settings.policy_version
        ):
            raise SinglePaperAnalysisValidationError(
                f"section_result.policy_version '{result.section_result.policy_version}' "
                f"does not match settings.section_settings.policy_version '{settings.section_settings.policy_version}'."
            )
        if (
            result.research_question_result is not None
            and result.research_question_result.policy_version
            != settings.research_question_settings.policy_version
        ):
            raise SinglePaperAnalysisValidationError(
                f"research_question_result.policy_version '{result.research_question_result.policy_version}' "
                f"does not match settings.research_question_settings.policy_version '{settings.research_question_settings.policy_version}'."
            )

        now_str = (
            datetime.now(timezone.utc).isoformat()
            if created_at is None or updated_at is None
            else ""
        )
        c_at = created_at or now_str
        u_at = updated_at or now_str

        settings_fp = compute_settings_fingerprint(settings)
        analysis_id = compute_analysis_id(result.checksum, settings, result.source_path)

        quality_warnings = (
            result.quality_assessment.warnings
            if result.quality_assessment is not None
            else ()
        )
        section_warnings = (
            result.section_result.warnings if result.section_result is not None else ()
        )
        rq_warnings = (
            result.research_question_result.warnings
            if result.research_question_result is not None
            else ()
        )

        sections: list[SinglePaperAnalysisSectionRecord] = []
        if result.section_result is not None:
            for idx, sec in enumerate(result.section_result.sections):
                spans = tuple(
                    SinglePaperAnalysisSectionSpanRecord(
                        page_number=sp.page_number,
                        start_character_offset=sp.start_character_offset,
                        end_character_offset=sp.end_character_offset,
                        ordinal_position=sp_idx,
                    )
                    for sp_idx, sp in enumerate(sec.spans)
                )
                b_evidences = tuple(
                    SinglePaperAnalysisSectionBoundaryEvidenceRecord(
                        page_number=b_ev.page_number,
                        start_character_offset=b_ev.start_character_offset,
                        end_character_offset=b_ev.end_character_offset,
                        evidence_type=b_ev.evidence_type,
                        description=b_ev.description,
                        ordinal_position=b_idx,
                    )
                    for b_idx, b_ev in enumerate(sec.boundary_evidence)
                )
                sections.append(
                    SinglePaperAnalysisSectionRecord(
                        section_kind=sec.kind,
                        heading_text=sec.display_label,
                        detection_method=sec.detection_method,
                        observed_heading_text=sec.observed_heading_text,
                        page_start=sec.start_page_number,
                        page_end=sec.end_page_number,
                        spans=spans,
                        ordinal_position=idx,
                        boundary_evidence=b_evidences,
                    )
                )

        rq_record: SinglePaperAnalysisQuestionRecord | None = None
        evidence_list: list[SinglePaperAnalysisEvidenceRecord] = []
        if result.research_question_result is not None:
            rq_res = result.research_question_result
            rq_record = SinglePaperAnalysisQuestionRecord(
                kind=rq_res.kind,
                question_text=rq_res.question_text,
                sections_used=rq_res.sections_used,
            )
            for idx, ev in enumerate(rq_res.evidence):
                evidence_list.append(
                    SinglePaperAnalysisEvidenceRecord(
                        section_kind=ev.section_kind,
                        excerpt_text=ev.excerpt_text,
                        page_number=ev.page_number,
                        start_character_offset=ev.start_character_offset,
                        end_character_offset=ev.end_character_offset,
                        ordinal_position=idx,
                    )
                )

        quality_status = (
            result.quality_assessment.status
            if result.quality_assessment is not None
            else None
        )

        return cls(
            analysis_id=analysis_id,
            source_path=result.source_path.resolve(),
            content_checksum=result.checksum,
            status=result.status,
            completed_stages=result.completed_stages,
            failed_stage=result.failed_stage,
            skipped_stages=result.skipped_stages,
            failure_code=result.failure_code,
            error_message=result.error_message,
            quality_status=quality_status,
            settings=settings,
            settings_fingerprint=settings_fp,
            quality_warnings=quality_warnings,
            section_warnings=section_warnings,
            research_question_warnings=rq_warnings,
            warnings=result.warnings,
            sections=tuple(sections),
            research_question=rq_record,
            evidence=tuple(evidence_list),
            created_at=c_at,
            updated_at=u_at,
        )
