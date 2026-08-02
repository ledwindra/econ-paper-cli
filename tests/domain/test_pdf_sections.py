"""Validation tests for immutable PDF section-detection contracts."""

from dataclasses import replace

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_SECTION_SETTINGS,
    PDFHeadingCandidate,
    PDFSection,
    PDFSectionDetectionMethod,
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


def test_heading_candidate_validation() -> None:
    candidate = PDFHeadingCandidate(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_number=1,
        start_character_offset=0,
        end_character_offset=8,
    )
    assert candidate.kind is PDFSectionKind.ABSTRACT
    assert candidate.start_character_offset == 0
    assert candidate.end_character_offset == 8

    with pytest.raises(PDFSectionValidationError, match="heading_text"):
        PDFHeadingCandidate(
            kind=PDFSectionKind.ABSTRACT,
            heading_text="   ",
            page_number=1,
            start_character_offset=0,
            end_character_offset=8,
        )

    with pytest.raises(PDFSectionValidationError, match="cannot exceed"):
        PDFHeadingCandidate(
            kind=PDFSectionKind.ABSTRACT,
            heading_text="Abstract",
            page_number=1,
            start_character_offset=10,
            end_character_offset=8,
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
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
        start_page_number=1,
        end_page_number=2,
        spans=(span1, span2),
        text=valid_text,
    )
    assert section.kind is PDFSectionKind.ABSTRACT

    with pytest.raises(PDFSectionValidationError, match="text length"):
        replace(section, text="Short text")

    with pytest.raises(PDFSectionValidationError, match="heading_text"):
        replace(section, observed_heading_text="   ")

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


def test_section_detection_result_validation_and_distinct_candidate_grounding() -> None:
    text = "Sample text 40 chars long string here..."
    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text)
    )
    section = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
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
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Introduction",
        start_page_number=2,
        end_page_number=2,
        spans=(intro_span,),
        text=text,
    )
    c1 = PDFHeadingCandidate(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_number=1,
        start_character_offset=0,
        end_character_offset=8,
    )
    c2 = PDFHeadingCandidate(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_number=2,
        start_character_offset=0,
        end_character_offset=8,
    )

    result = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(section, intro_section),
        candidates=(c1,),
        warnings=(),
    )
    assert len(result.sections) == 2

    # Candidates must be unique
    with pytest.raises(PDFSectionValidationError, match="candidates must be unique"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(c1, c1),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
                PDFSectionWarning(
                    PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES,
                    page_numbers=(1,),
                ),
            ),
        )

    # Ambiguity warning must be grounded by at least 2 distinct candidates
    with pytest.raises(
        PDFSectionValidationError, match="must be grounded by at least 2 distinct"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(c1,),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
                PDFSectionWarning(
                    PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES,
                    page_numbers=(1,),
                ),
            ),
        )

    # Warning page_numbers must match candidate page numbers
    with pytest.raises(
        PDFSectionValidationError, match="do not match candidate page numbers"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(c1, c2),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
                PDFSectionWarning(
                    PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES,
                    page_numbers=(1,),
                ),
            ),
        )


def test_result_unresolved_abstract_boundary_and_empty_body_grounding() -> None:
    c_abs = PDFHeadingCandidate(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_number=1,
        start_character_offset=0,
        end_character_offset=8,
    )
    c_intro = PDFHeadingCandidate(
        kind=PDFSectionKind.INTRODUCTION,
        heading_text="1. Introduction",
        page_number=2,
        start_character_offset=0,
        end_character_offset=15,
    )

    # UNRESOLVED_ABSTRACT_BOUNDARY must be grounded by Abstract candidate
    with pytest.raises(
        PDFSectionValidationError,
        match="must be grounded by at least 1 Abstract candidate",
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(),
            warnings=(
                PDFSectionWarning(
                    PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY,
                    page_numbers=(1,),
                ),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
            ),
        )

    # EMPTY_ABSTRACT_BODY must be grounded by Abstract candidate
    with pytest.raises(
        PDFSectionValidationError,
        match="must be grounded by at least 1 Abstract candidate",
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(),
            warnings=(
                PDFSectionWarning(
                    PDFSectionWarningCode.EMPTY_ABSTRACT_BODY, page_numbers=(1,)
                ),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
            ),
        )

    # EMPTY_INTRODUCTION_BODY must be grounded by Introduction candidate
    with pytest.raises(
        PDFSectionValidationError,
        match="must be grounded by at least 1 Introduction candidate",
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(c_abs,),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),
                PDFSectionWarning(
                    PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY, page_numbers=(2,)
                ),
            ),
        )

    # Valid EMPTY_INTRODUCTION_BODY grounding
    res_empty_intro = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(),
        candidates=(c_intro,),
        warnings=(
            PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),
            PDFSectionWarning(
                PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY, page_numbers=(2,)
            ),
        ),
    )
    assert len(res_empty_intro.warnings) == 2


def test_result_rejects_unrecognized_policy_version() -> None:
    with pytest.raises(
        PDFSectionValidationError, match="not a recognized policy version"
    ):
        PDFSectionDetectionResult(
            policy_version="unrecognized-policy-v99",
            sections=(),
            candidates=(),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),),
        )


def test_result_rejects_duplicate_section_kinds() -> None:
    text = "Sample text 40 chars long string here..."
    span1 = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text)
    )
    sec1 = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract 1",
        start_page_number=1,
        end_page_number=1,
        spans=(span1,),
        text=text,
    )
    span2 = PDFSectionSpan(
        page_number=2, start_character_offset=0, end_character_offset=len(text)
    )
    sec2 = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract 2",
        start_page_number=2,
        end_page_number=2,
        spans=(span2,),
        text=text,
    )

    with pytest.raises(PDFSectionValidationError, match="unique in sections"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(sec1, sec2),
            candidates=(),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),),
        )


def test_result_rejects_non_canonical_warning_ordering() -> None:
    with pytest.raises(
        PDFSectionValidationError, match="warnings must use canonical code order"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(),
            candidates=(),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),
            ),
        )


def test_result_rejects_contradictory_no_pages_or_all_empty_with_sections() -> None:
    text = "Sample text 40 chars long string here..."
    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text)
    )
    sec = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text=text,
    )

    with pytest.raises(PDFSectionValidationError, match="NO_PAGES warning contradicts"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(sec,),
            candidates=(),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
            ),
        )

    with pytest.raises(
        PDFSectionValidationError, match="ALL_PAGES_EMPTY warning contradicts"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(sec,),
            candidates=(),
            warnings=(
                PDFSectionWarning(
                    PDFSectionWarningCode.ALL_PAGES_EMPTY, page_numbers=(1,)
                ),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
            ),
        )


def test_result_rejects_missing_section_without_corresponding_warning() -> None:
    text = "Sample text 40 chars long string here..."
    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text)
    )
    sec = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text=text,
    )

    with pytest.raises(PDFSectionValidationError, match="MISSING_INTRODUCTION warning"):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(sec,),
            candidates=(),
            warnings=(),
        )


def test_result_rejects_contradictory_missing_warning_with_section() -> None:
    text = "Sample text 40 chars long string here..."
    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text)
    )
    sec = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text=text,
    )

    with pytest.raises(
        PDFSectionValidationError, match="MISSING_ABSTRACT warning contradicts"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(sec,),
            candidates=(),
            warnings=(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
            ),
        )


def test_result_rejects_overlapping_or_out_of_order_sections() -> None:
    text1 = "Sample abstract text 40 chars string..."
    span1 = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=len(text1)
    )
    sec1 = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span1,),
        text=text1,
    )

    text2 = "Sample intro text 40 chars string here."
    span2 = PDFSectionSpan(
        page_number=1, start_character_offset=10, end_character_offset=10 + len(text2)
    )
    sec2 = PDFSection(
        kind=PDFSectionKind.INTRODUCTION,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Introduction",
        start_page_number=1,
        end_page_number=1,
        spans=(span2,),
        text=text2,
    )

    with pytest.raises(
        PDFSectionValidationError, match="sections on the same page cannot overlap"
    ):
        PDFSectionDetectionResult(
            policy_version="pdf-section-detection-v1",
            sections=(sec1, sec2),
            candidates=(),
            warnings=(),
        )
