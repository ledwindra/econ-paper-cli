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


def test_section_validation_enforces_exact_text_length_and_span_alignment() -> None:
    span1 = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=20
    )
    span2 = PDFSectionSpan(
        page_number=2, start_character_offset=0, end_character_offset=15
    )
    valid_text = "A" * 35

    section = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        start_page_number=1,
        end_page_number=2,
        spans=(span1, span2),
        text=valid_text,
    )
    assert section.kind is PDFSectionKind.ABSTRACT

    with pytest.raises(PDFSectionValidationError, match="text length"):
        replace(section, text="Short text")

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
    text = "Sample text 40 chars long string here..."
    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text)
    )
    section = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text=text,
    )
    intro_span = PDFSectionSpan(
        page_number=2, start_character_offset=0, end_character_offset=len(text)
    )
    intro_section = PDFSection(
        kind=PDFSectionKind.INTRODUCTION,
        heading_text="Introduction",
        start_page_number=2,
        end_page_number=2,
        spans=(intro_span,),
        text=text,
    )

    result = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(section, intro_section),
        warnings=(),
    )
    assert len(result.sections) == 2

    with pytest.raises(
        PDFSectionValidationError, match="not a recognized policy version"
    ):
        PDFSectionDetectionResult(
            policy_version="unrecognized-v2",
            sections=(),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),),
        )

    with pytest.raises(PDFSectionValidationError, match="unique in sections"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(section, section),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),),
        )

    with pytest.raises(
        PDFSectionValidationError, match="contradicts presence of Abstract"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(section, intro_section),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),),
        )

    with pytest.raises(
        PDFSectionValidationError, match="contradicts presence of sections"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(section,),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),),
        )

    with pytest.raises(
        PDFSectionValidationError, match="must include MISSING_ABSTRACT"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(intro_section,),
            warnings=(),
        )

    with pytest.raises(PDFSectionValidationError, match="ordered and non-overlapping"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(intro_section, section),
            warnings=(),
        )
