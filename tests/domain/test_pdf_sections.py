"""Validation tests for immutable PDF section-detection contracts."""

from dataclasses import replace

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_SECTION_SETTINGS,
    PDFSection,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSettings,
    PDFSectionSpan,
    PDFSectionValidationError,
    PDFSectionWarning,
    PDFSectionWarningCode,
)


def test_section_span_validation() -> None:
    span = PDFSectionSpan(
        page_number=1, start_character_offset=10, end_character_offset=50
    )
    assert span.character_count == 40

    with pytest.raises(PDFSectionValidationError, match="page_number"):
        PDFSectionSpan(page_number=0, start_character_offset=0, end_character_offset=10)

    with pytest.raises(PDFSectionValidationError, match="start_character_offset"):
        PDFSectionSpan(
            page_number=1, start_character_offset=-1, end_character_offset=10
        )

    with pytest.raises(PDFSectionValidationError, match="cannot exceed"):
        PDFSectionSpan(
            page_number=1, start_character_offset=20, end_character_offset=10
        )


def test_section_validation() -> None:
    span1 = PDFSectionSpan(
        page_number=1, start_character_offset=10, end_character_offset=50
    )
    span2 = PDFSectionSpan(
        page_number=2, start_character_offset=0, end_character_offset=30
    )
    section = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        start_page_number=1,
        end_page_number=2,
        spans=(span1, span2),
        text="Sample abstract text spanning two pages.",
    )
    assert section.kind is PDFSectionKind.ABSTRACT

    with pytest.raises(PDFSectionValidationError, match="heading_text"):
        replace(section, heading_text="   ")

    with pytest.raises(PDFSectionValidationError, match="cannot exceed"):
        replace(section, start_page_number=3, end_page_number=2)

    with pytest.raises(PDFSectionValidationError, match="first span"):
        replace(section, start_page_number=2)

    with pytest.raises(PDFSectionValidationError, match="last span"):
        replace(section, end_page_number=1)


def test_section_warning_validation() -> None:
    warning = PDFSectionWarning(
        code=PDFSectionWarningCode.MISSING_ABSTRACT,
    )
    assert warning.code is PDFSectionWarningCode.MISSING_ABSTRACT
    assert "Abstract" in warning.message

    with pytest.raises(PDFSectionValidationError, match="page_numbers"):
        PDFSectionWarning(
            code=PDFSectionWarningCode.MISSING_ABSTRACT,
            page_numbers=(2, 1),
        )


def test_settings_canonical_binding() -> None:
    assert DEFAULT_PDF_SECTION_SETTINGS.policy_version == "pdf-section-detection-v1"

    with pytest.raises(
        PDFSectionValidationError, match="not a recognized policy version"
    ):
        PDFSectionSettings(policy_version="unknown-v2")


def test_section_detection_result_validation() -> None:
    span = PDFSectionSpan(
        page_number=1, start_character_offset=10, end_character_offset=50
    )
    section = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text="Sample text",
    )
    result = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(section,),
        warnings=(),
    )
    assert result.sections[0].kind is PDFSectionKind.ABSTRACT

    with pytest.raises(PDFSectionValidationError, match="unique in sections"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(section, section),
            warnings=(),
        )

    w1 = PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT)
    w2 = PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION)

    with pytest.raises(PDFSectionValidationError, match="canonical code order"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            warnings=(w2, w1),
        )
