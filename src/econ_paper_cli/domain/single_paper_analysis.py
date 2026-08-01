"""Immutable domain contracts for single-paper research-question analysis."""

from dataclasses import dataclass, field
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
)
from econ_paper_cli.domain.pdf_sections import (
    DEFAULT_PDF_SECTION_SETTINGS,
    PDFSectionDetectionResult,
    PDFSectionSettings,
)
from econ_paper_cli.domain.research_question import (
    DEFAULT_RESEARCH_QUESTION_SETTINGS,
    ResearchQuestionKind,
    ResearchQuestionResult,
    ResearchQuestionSettings,
)


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
    "single-paper-analysis-v1": {}
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


DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS = SinglePaperAnalysisSettings()
