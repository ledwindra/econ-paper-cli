"""Immutable domain contracts for single-paper research-question analysis."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from econ_paper_cli.domain.errors import SinglePaperAnalysisValidationError
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
    SECTION_DETECTION_HALTED = "section_detection_halted"
    QUESTION_EXTRACTION_HALTED = "question_extraction_halted"


class SinglePaperAnalysisWarningCode(str, Enum):
    """Stable warning identifiers for single-paper analysis orchestration."""

    QUALITY_HALTED = "quality_halted"
    SECTION_DETECTION_HALTED = "section_detection_halted"
    QUESTION_EXTRACTION_HALTED = "question_extraction_halted"


_WARNING_MESSAGES = {
    SinglePaperAnalysisWarningCode.QUALITY_HALTED: (
        "Extraction quality was poor or unusable. Downstream section detection and generation were skipped."
    ),
    SinglePaperAnalysisWarningCode.SECTION_DETECTION_HALTED: (
        "No usable Abstract or Introduction section was detected. Research question extraction was skipped."
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
    """Immutable composite result of single-paper research-question analysis."""

    policy_version: str
    source_path: Path
    checksum: str | None
    status: SinglePaperAnalysisStatus
    completed_stages: tuple[SinglePaperAnalysisStage, ...]
    skipped_stages: tuple[SinglePaperAnalysisStage, ...]
    preflight_result: IngestionPreflightResult | None
    extraction_result: PDFExtractionResult | None
    quality_assessment: PDFExtractionQualityAssessment | None
    section_result: PDFSectionDetectionResult | None
    research_question_result: ResearchQuestionResult | None
    warnings: tuple[SinglePaperAnalysisWarning, ...]
    error_message: str | None

    def __post_init__(self) -> None:
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
        if not isinstance(self.skipped_stages, tuple) or not all(
            isinstance(s, SinglePaperAnalysisStage) for s in self.skipped_stages
        ):
            raise SinglePaperAnalysisValidationError(
                "skipped_stages must be a tuple of SinglePaperAnalysisStage instances."
            )
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(w, SinglePaperAnalysisWarning) for w in self.warnings
        ):
            raise SinglePaperAnalysisValidationError(
                "warnings must be a tuple of SinglePaperAnalysisWarning instances."
            )
        if self.error_message is not None:
            _validate_nonempty_text("error_message", self.error_message)

        # Stage combination & sequence checks
        all_stages = tuple(SinglePaperAnalysisStage)
        combined = self.completed_stages + self.skipped_stages
        if combined != all_stages:
            raise SinglePaperAnalysisValidationError(
                f"completed_stages + skipped_stages must equal canonical stage sequence {all_stages}."
            )

        # Status-specific state invariants
        if self.status is SinglePaperAnalysisStatus.SUCCESS:
            if self.skipped_stages:
                raise SinglePaperAnalysisValidationError(
                    "SUCCESS status cannot have skipped stages."
                )
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
            if self.completed_stages != (SinglePaperAnalysisStage.PREFLIGHT,):
                raise SinglePaperAnalysisValidationError(
                    "PREFLIGHT_FAILED status must complete only PREFLIGHT stage."
                )
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
            if self.completed_stages != (
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
            ):
                raise SinglePaperAnalysisValidationError(
                    "EXTRACTION_FAILED status must complete PREFLIGHT and EXTRACTION stages."
                )
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
            if self.completed_stages != (
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            ):
                raise SinglePaperAnalysisValidationError(
                    "QUALITY_HALTED status must complete up to QUALITY_ASSESSMENT stage."
                )
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

        elif self.status is SinglePaperAnalysisStatus.SECTION_DETECTION_HALTED:
            if self.completed_stages != (
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
            ):
                raise SinglePaperAnalysisValidationError(
                    "SECTION_DETECTION_HALTED status must complete up to SECTION_DETECTION stage."
                )
            if self.research_question_result is not None:
                raise SinglePaperAnalysisValidationError(
                    "SECTION_DETECTION_HALTED status must have None for research_question_result."
                )

        elif self.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED:
            if self.skipped_stages:
                raise SinglePaperAnalysisValidationError(
                    "QUESTION_EXTRACTION_HALTED completed all 5 stages."
                )
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
