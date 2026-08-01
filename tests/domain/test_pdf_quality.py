"""Validation tests for immutable PDF extraction-quality contracts."""

from dataclasses import replace
from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_QUALITY_SETTINGS,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionQualityAssessment,
    PDFExtractionResult,
    PDFPageQualityObservation,
    PDFQualitySettings,
    PDFQualityStatus,
    PDFQualityValidationError,
    PDFQualityWarning,
    PDFQualityWarningCode,
)
from econ_paper_cli.services import assess_pdf_extraction_quality


def _assessment(*texts: str) -> PDFExtractionQualityAssessment:
    extraction = PDFExtractionResult(
        source_path=Path.cwd().resolve() / "quality-contract.pdf",
        pages=tuple(
            ExtractedPDFPage(page_number=index, text=text)
            for index, text in enumerate(texts, start=1)
        ),
        page_count=len(texts),
        metadata=PDFDocumentMetadata(),
        extraction_method="fake",
        parser_version="1",
    )
    return assess_pdf_extraction_quality(
        extraction, settings=DEFAULT_PDF_QUALITY_SETTINGS
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "sparse_page_non_whitespace_threshold",
        "very_low_text_non_whitespace_threshold",
        "repeated_character_run_threshold",
        "minimum_pages_for_imbalance",
    ],
)
@pytest.mark.parametrize("value", [True, 1.5, -1])
def test_settings_reject_invalid_integer_thresholds(
    field_name: str, value: object
) -> None:
    with pytest.raises(PDFQualityValidationError, match=field_name):
        PDFQualitySettings(**{field_name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name",
    [
        "high_empty_page_ratio_threshold",
        "anomaly_ratio_warning_threshold",
        "anomaly_ratio_unusable_threshold",
        "repeated_character_ratio_unusable_threshold",
        "severe_page_imbalance_ratio_threshold",
    ],
)
@pytest.mark.parametrize("value", [True, 0, -0.1, 1.1, float("inf"), float("nan")])
def test_settings_reject_invalid_ratio_thresholds(
    field_name: str, value: object
) -> None:
    with pytest.raises(PDFQualityValidationError, match=field_name):
        PDFQualitySettings(**{field_name: value})  # type: ignore[arg-type]


def test_settings_reject_blank_version_and_contradictory_thresholds() -> None:
    with pytest.raises(PDFQualityValidationError, match="policy_version"):
        PDFQualitySettings(policy_version=" ")
    with pytest.raises(PDFQualityValidationError, match="must be less"):
        PDFQualitySettings(
            anomaly_ratio_warning_threshold=0.2,
            anomaly_ratio_unusable_threshold=0.1,
        )


@pytest.mark.parametrize("value", [True, 1.5, -1])
def test_page_observation_rejects_invalid_counts(value: object) -> None:
    page = _assessment("ab" * 150).pages[0]
    with pytest.raises(PDFQualityValidationError, match="character_count"):
        replace(page, character_count=value)  # type: ignore[arg-type]


def test_page_observation_rejects_inconsistent_counts_and_flags() -> None:
    page = _assessment("ab" * 150).pages[0]
    with pytest.raises(PDFQualityValidationError, match="cannot exceed"):
        replace(page, control_character_count=page.character_count + 1)
    with pytest.raises(PDFQualityValidationError, match="is_empty"):
        replace(page, is_empty=True)

    empty = PDFPageQualityObservation(
        page_number=1,
        character_count=0,
        printable_character_count=0,
        non_whitespace_character_count=0,
        control_character_count=0,
        replacement_character_count=0,
        repeated_character_count=0,
        is_empty=True,
        is_sparse=False,
    )
    with pytest.raises(PDFQualityValidationError, match="cannot also be sparse"):
        replace(empty, is_sparse=True)


def test_assessment_rejects_inconsistent_document_counts() -> None:
    assessment = _assessment("ab" * 150)
    inconsistent = replace(
        assessment.measurements,
        total_character_count=assessment.measurements.total_character_count + 1,
    )
    with pytest.raises(PDFQualityValidationError, match="does not match"):
        replace(assessment, measurements=inconsistent)


def test_assessment_rejects_invalid_page_ordering() -> None:
    assessment = _assessment("ab" * 150, "cd" * 150)
    with pytest.raises(PDFQualityValidationError, match="contiguous 1-based"):
        replace(assessment, pages=tuple(reversed(assessment.pages)))


def test_assessment_rejects_noncanonical_or_duplicate_warnings() -> None:
    assessment = _assessment("", "x" * 40, "ab" * 150, "cd" * 150)
    assert len(assessment.warnings) > 1

    with pytest.raises(PDFQualityValidationError, match="canonical"):
        replace(assessment, warnings=tuple(reversed(assessment.warnings)))
    with pytest.raises(PDFQualityValidationError, match="unique"):
        replace(
            assessment,
            warnings=(assessment.warnings[0], assessment.warnings[0]),
        )


def test_assessment_rejects_unknown_warning_page() -> None:
    assessment = _assessment("x")
    warning = replace(assessment.warnings[-1], page_numbers=(2,))
    with pytest.raises(PDFQualityValidationError, match="observed source pages"):
        replace(assessment, warnings=assessment.warnings[:-1] + (warning,))


@pytest.mark.parametrize(
    "status",
    [
        PDFQualityStatus.USABLE_WITH_WARNINGS,
        PDFQualityStatus.LIKELY_NEEDS_OCR,
        PDFQualityStatus.UNUSABLE,
    ],
)
def test_assessment_rejects_contradictory_status_without_warnings(
    status: PDFQualityStatus,
) -> None:
    assessment = _assessment("ab" * 150)
    with pytest.raises(PDFQualityValidationError, match="contradicts"):
        replace(assessment, status=status)


def test_warning_contract_rejects_invalid_pages_and_exposes_message() -> None:
    warning = PDFQualityWarning(
        code=PDFQualityWarningCode.EMPTY_PAGES,
        page_numbers=(1, 3),
    )
    assert "Inspect" in warning.message

    with pytest.raises(PDFQualityValidationError, match="ascending"):
        PDFQualityWarning(
            code=PDFQualityWarningCode.EMPTY_PAGES,
            page_numbers=(2, 1),
        )
    with pytest.raises(PDFQualityValidationError, match="positive integer"):
        PDFQualityWarning(
            code=PDFQualityWarningCode.EMPTY_PAGES,
            page_numbers=(True,),
        )
