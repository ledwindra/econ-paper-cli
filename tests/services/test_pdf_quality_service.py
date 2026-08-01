"""Unit tests for deterministic PDF extraction-quality assessment."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_QUALITY_SETTINGS,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFQualitySettings,
    PDFQualityStatus,
    PDFQualityWarningCode,
)
from econ_paper_cli.services import assess_pdf_extraction_quality


def _text(non_whitespace_count: int) -> str:
    return "".join("ab"[index % 2] for index in range(non_whitespace_count))


def _normal_text(repetitions: int) -> str:
    return (
        "Local institutions affect household investment and regional productivity. "
        * repetitions
    )


def _extraction(*page_texts: str) -> PDFExtractionResult:
    return PDFExtractionResult(
        source_path=Path.cwd().resolve() / "synthetic-quality.pdf",
        pages=tuple(
            ExtractedPDFPage(page_number=index, text=text)
            for index, text in enumerate(page_texts, start=1)
        ),
        page_count=len(page_texts),
        metadata=PDFDocumentMetadata(title="Synthetic quality fixture"),
        extraction_method="fake-pdf",
        parser_version="1.0",
    )


def _assess(*page_texts: str, settings: PDFQualitySettings | None = None):
    return assess_pdf_extraction_quality(
        _extraction(*page_texts),
        settings=settings or DEFAULT_PDF_QUALITY_SETTINGS,
    )


def _codes(assessment) -> tuple[PDFQualityWarningCode, ...]:
    return tuple(warning.code for warning in assessment.warnings)


def test_normal_single_page_extraction_is_usable() -> None:
    assessment = _assess(_normal_text(10))

    assert assessment.status is PDFQualityStatus.USABLE
    assert assessment.warnings == ()
    assert assessment.measurements.page_count == 1
    assert assessment.measurements.non_whitespace_character_count > 200
    assert assessment.policy_version == "pdf-extraction-quality-v1"


def test_normal_multipage_extraction_is_usable_and_preserves_distribution() -> None:
    assessment = _assess(_normal_text(8), _normal_text(9), _normal_text(10))

    assert assessment.status is PDFQualityStatus.USABLE
    assert tuple(page.page_number for page in assessment.pages) == (1, 2, 3)
    page_counts = tuple(
        page.non_whitespace_character_count for page in assessment.pages
    )
    assert assessment.measurements.minimum_page_non_whitespace_character_count == min(
        page_counts
    )
    assert assessment.measurements.maximum_page_non_whitespace_character_count == max(
        page_counts
    )
    assert assessment.measurements.maximum_page_text_ratio == pytest.approx(
        max(page_counts) / sum(page_counts)
    )


def test_zero_page_result_is_unusable_without_implying_parser_failure() -> None:
    assessment = _assess()

    assert assessment.status is PDFQualityStatus.UNUSABLE
    assert _codes(assessment) == (PDFQualityWarningCode.NO_PAGES,)
    assert assessment.measurements.page_count == 0


def test_all_empty_pages_likely_need_ocr_with_traceable_warning() -> None:
    assessment = _assess("", " \n\t", "\r\n")

    assert assessment.status is PDFQualityStatus.LIKELY_NEEDS_OCR
    assert _codes(assessment) == (PDFQualityWarningCode.ALL_PAGES_EMPTY,)
    assert assessment.warnings[0].page_numbers == (1, 2, 3)
    assert assessment.measurements.empty_page_count == 3
    assert assessment.measurements.empty_page_ratio == 1.0


def test_limited_empty_pages_remain_usable_with_warnings() -> None:
    assessment = _assess("", _text(500), _text(500))

    assert assessment.status is PDFQualityStatus.USABLE_WITH_WARNINGS
    assert _codes(assessment) == (PDFQualityWarningCode.EMPTY_PAGES,)
    assert assessment.warnings[0].page_numbers == (1,)


def test_high_non_total_empty_ratio_likely_needs_ocr_at_default_boundary() -> None:
    assessment = _assess("", _text(500))

    assert assessment.measurements.empty_page_ratio == 0.5
    assert assessment.status is PDFQualityStatus.LIKELY_NEEDS_OCR
    assert _codes(assessment) == (
        PDFQualityWarningCode.EMPTY_PAGES,
        PDFQualityWarningCode.HIGH_EMPTY_PAGE_RATIO,
    )


def test_empty_ratio_below_default_boundary_only_warns() -> None:
    assessment = _assess("", _text(500), _text(500))

    assert assessment.measurements.empty_page_ratio < 0.5
    assert PDFQualityWarningCode.HIGH_EMPTY_PAGE_RATIO not in _codes(assessment)
    assert assessment.status is PDFQualityStatus.USABLE_WITH_WARNINGS


@pytest.mark.parametrize(
    ("character_count", "expected_sparse"),
    [(79, True), (80, False)],
)
def test_sparse_page_default_threshold_boundary(
    character_count: int, expected_sparse: bool
) -> None:
    assessment = _assess(_text(character_count))

    assert assessment.pages[0].is_sparse is expected_sparse
    assert (PDFQualityWarningCode.SPARSE_PAGES in _codes(assessment)) is expected_sparse


@pytest.mark.parametrize(
    ("character_count", "expected_warning"),
    [(199, True), (200, False)],
)
def test_very_low_text_default_threshold_boundary(
    character_count: int, expected_warning: bool
) -> None:
    assessment = _assess(_text(character_count))

    assert (
        PDFQualityWarningCode.VERY_LOW_TEXT_VOLUME in _codes(assessment)
    ) is expected_warning


def test_control_character_warning_boundary_and_page_traceability() -> None:
    below = _assess("\x00" + _text(100))
    exact = _assess("\x00" + _text(99))

    assert below.measurements.control_character_ratio < 0.01
    assert PDFQualityWarningCode.CONTROL_CHARACTERS not in _codes(below)
    assert exact.pages[0].control_character_count == 1
    assert exact.pages[0].printable_character_count == 99
    warning = next(
        warning
        for warning in exact.warnings
        if warning.code is PDFQualityWarningCode.CONTROL_CHARACTERS
    )
    assert exact.measurements.control_character_ratio == 0.01
    assert warning.page_numbers == (1,)


@pytest.mark.parametrize("anomaly", ["\x00", "\ufffd"])
def test_anomaly_unusable_default_threshold_boundary(anomaly: str) -> None:
    below = _assess(anomaly * 9 + _text(91))
    at_boundary = _assess(anomaly * 10 + _text(90))

    assert below.status is PDFQualityStatus.USABLE_WITH_WARNINGS
    assert PDFQualityWarningCode.EXTRACTION_GARBAGE not in _codes(below)
    assert at_boundary.status is PDFQualityStatus.UNUSABLE
    assert PDFQualityWarningCode.EXTRACTION_GARBAGE in _codes(at_boundary)


def test_replacement_character_warning_default_boundary() -> None:
    below = _assess("\ufffd" + _text(100))
    at_boundary = _assess("\ufffd" + _text(99))

    assert PDFQualityWarningCode.REPLACEMENT_CHARACTERS not in _codes(below)
    assert PDFQualityWarningCode.REPLACEMENT_CHARACTERS in _codes(at_boundary)
    assert at_boundary.pages[0].replacement_character_count == 1


def test_repeated_character_run_default_threshold_boundary() -> None:
    below = _assess("z" * 11 + _text(189))
    at_boundary = _assess("z" * 12 + _text(188))

    assert below.measurements.repeated_character_count == 0
    assert PDFQualityWarningCode.REPEATED_CHARACTERS not in _codes(below)
    assert at_boundary.measurements.repeated_character_count == 12
    assert PDFQualityWarningCode.REPEATED_CHARACTERS in _codes(at_boundary)


def test_repeated_character_garbage_default_ratio_boundary() -> None:
    below = _assess("z" * 39 + _text(161))
    at_boundary = _assess("z" * 40 + _text(160))

    assert below.measurements.repeated_character_ratio == 0.195
    assert below.status is PDFQualityStatus.USABLE_WITH_WARNINGS
    assert at_boundary.measurements.repeated_character_ratio == 0.2
    assert at_boundary.status is PDFQualityStatus.UNUSABLE


def test_page_imbalance_default_ratio_boundary() -> None:
    below = _assess(_text(799), _text(101), _text(50), _text(50))
    at_boundary = _assess(_text(800), _text(100), _text(50), _text(50))

    assert below.measurements.maximum_page_text_ratio == 0.799
    assert PDFQualityWarningCode.SEVERE_PAGE_IMBALANCE not in _codes(below)
    assert at_boundary.measurements.maximum_page_text_ratio == 0.8
    assert PDFQualityWarningCode.SEVERE_PAGE_IMBALANCE in _codes(at_boundary)


def test_page_imbalance_minimum_page_boundary() -> None:
    below = _assess(_text(900), _text(50), _text(50))
    at_boundary = _assess(_text(900), _text(50), _text(25), _text(25))

    assert PDFQualityWarningCode.SEVERE_PAGE_IMBALANCE not in _codes(below)
    assert PDFQualityWarningCode.SEVERE_PAGE_IMBALANCE in _codes(at_boundary)


def test_warning_order_is_canonical_and_page_ordered() -> None:
    assessment = _assess(
        "",
        "\x00" * 10 + "\ufffd" * 10 + "z" * 40 + _text(40),
        _text(5),
        _text(5),
    )

    assert _codes(assessment) == tuple(
        sorted(_codes(assessment), key=list(PDFQualityWarningCode).index)
    )
    for warning in assessment.warnings:
        assert warning.page_numbers == tuple(sorted(warning.page_numbers))


def test_repeated_assessment_is_equivalent() -> None:
    extraction = _extraction("", _text(500), "\ufffd" + _text(99))
    settings = DEFAULT_PDF_QUALITY_SETTINGS

    first = assess_pdf_extraction_quality(extraction, settings=settings)
    second = assess_pdf_extraction_quality(extraction, settings=settings)

    assert first == second


def test_explicit_policy_version_is_preserved() -> None:
    settings = PDFQualitySettings(policy_version="quality-policy-test-v2")

    assessment = _assess(_text(500), settings=settings)

    assert assessment.policy_version == "quality-policy-test-v2"


def test_service_rejects_non_contract_inputs() -> None:
    extraction = _extraction(_text(500))
    with pytest.raises(TypeError, match="extraction"):
        assess_pdf_extraction_quality(object(), settings=DEFAULT_PDF_QUALITY_SETTINGS)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="settings"):
        assess_pdf_extraction_quality(extraction, settings=object())  # type: ignore[arg-type]
