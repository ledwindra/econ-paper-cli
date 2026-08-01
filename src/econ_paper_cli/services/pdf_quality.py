"""Pure deterministic assessment of successful PDF extraction results."""

from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_quality import (
    PDFExtractionQualityAssessment,
    PDFPageQualityObservation,
    PDFQualityMeasurements,
    PDFQualitySettings,
    PDFQualityStatus,
    PDFQualityWarning,
    PDFQualityWarningCode,
)

_STRUCTURAL_WHITESPACE = frozenset("\t\n\r\f")


def assess_pdf_extraction_quality(
    extraction: PDFExtractionResult,
    *,
    settings: PDFQualitySettings,
) -> PDFExtractionQualityAssessment:
    """Assess extracted page text without parser, filesystem, or other effects."""
    if not isinstance(extraction, PDFExtractionResult):
        raise TypeError("extraction must be a PDFExtractionResult instance.")
    if not isinstance(settings, PDFQualitySettings):
        raise TypeError("settings must be a PDFQualitySettings instance.")

    pages = tuple(
        _observe_page(page.page_number, page.text, settings)
        for page in extraction.pages
    )
    measurements = _measure_document(pages)
    warnings = _build_warnings(pages, measurements, settings)
    status = _classify(warnings)
    return PDFExtractionQualityAssessment(
        policy_version=settings.policy_version,
        status=status,
        measurements=measurements,
        pages=pages,
        warnings=warnings,
    )


def _observe_page(
    page_number: int,
    text: str,
    settings: PDFQualitySettings,
) -> PDFPageQualityObservation:
    non_whitespace_count = sum(not character.isspace() for character in text)
    control_count = sum(_is_suspicious_control(character) for character in text)
    replacement_count = text.count("\ufffd")
    repeated_count = _count_repeated_characters(
        text, settings.repeated_character_run_threshold
    )
    is_empty = non_whitespace_count == 0
    return PDFPageQualityObservation(
        page_number=page_number,
        character_count=len(text),
        printable_character_count=sum(
            _is_assessment_printable(character) for character in text
        ),
        non_whitespace_character_count=non_whitespace_count,
        control_character_count=control_count,
        replacement_character_count=replacement_count,
        repeated_character_count=repeated_count,
        is_empty=is_empty,
        is_sparse=(
            not is_empty
            and non_whitespace_count < settings.sparse_page_non_whitespace_threshold
        ),
    )


def _measure_document(
    pages: tuple[PDFPageQualityObservation, ...],
) -> PDFQualityMeasurements:
    page_text_counts = tuple(page.non_whitespace_character_count for page in pages)
    return PDFQualityMeasurements(
        page_count=len(pages),
        total_character_count=sum(page.character_count for page in pages),
        printable_character_count=sum(page.printable_character_count for page in pages),
        non_whitespace_character_count=sum(page_text_counts),
        empty_page_count=sum(page.is_empty for page in pages),
        sparse_page_count=sum(page.is_sparse for page in pages),
        control_character_count=sum(page.control_character_count for page in pages),
        replacement_character_count=sum(
            page.replacement_character_count for page in pages
        ),
        repeated_character_count=sum(page.repeated_character_count for page in pages),
        minimum_page_non_whitespace_character_count=min(page_text_counts, default=0),
        maximum_page_non_whitespace_character_count=max(page_text_counts, default=0),
    )


def _build_warnings(
    pages: tuple[PDFPageQualityObservation, ...],
    measurements: PDFQualityMeasurements,
    settings: PDFQualitySettings,
) -> tuple[PDFQualityWarning, ...]:
    if measurements.page_count == 0:
        return (PDFQualityWarning(PDFQualityWarningCode.NO_PAGES),)

    warnings: list[PDFQualityWarning] = []
    empty_pages = _page_numbers(pages, "is_empty")
    if measurements.empty_page_count == measurements.page_count:
        warnings.append(
            PDFQualityWarning(PDFQualityWarningCode.ALL_PAGES_EMPTY, empty_pages)
        )
    elif empty_pages:
        warnings.append(
            PDFQualityWarning(PDFQualityWarningCode.EMPTY_PAGES, empty_pages)
        )
        if measurements.empty_page_ratio >= settings.high_empty_page_ratio_threshold:
            warnings.append(
                PDFQualityWarning(
                    PDFQualityWarningCode.HIGH_EMPTY_PAGE_RATIO, empty_pages
                )
            )

    if (
        measurements.non_whitespace_character_count
        < settings.very_low_text_non_whitespace_threshold
        and measurements.empty_page_count != measurements.page_count
    ):
        warnings.append(PDFQualityWarning(PDFQualityWarningCode.VERY_LOW_TEXT_VOLUME))

    sparse_pages = _page_numbers(pages, "is_sparse")
    if sparse_pages:
        warnings.append(
            PDFQualityWarning(PDFQualityWarningCode.SPARSE_PAGES, sparse_pages)
        )

    control_pages = tuple(
        page.page_number for page in pages if page.control_character_count
    )
    if measurements.control_character_ratio >= settings.anomaly_ratio_warning_threshold:
        warnings.append(
            PDFQualityWarning(PDFQualityWarningCode.CONTROL_CHARACTERS, control_pages)
        )

    replacement_pages = tuple(
        page.page_number for page in pages if page.replacement_character_count
    )
    if (
        measurements.replacement_character_ratio
        >= settings.anomaly_ratio_warning_threshold
    ):
        warnings.append(
            PDFQualityWarning(
                PDFQualityWarningCode.REPLACEMENT_CHARACTERS, replacement_pages
            )
        )

    repeated_pages = tuple(
        page.page_number for page in pages if page.repeated_character_count
    )
    if repeated_pages:
        warnings.append(
            PDFQualityWarning(PDFQualityWarningCode.REPEATED_CHARACTERS, repeated_pages)
        )

    if (
        measurements.page_count >= settings.minimum_pages_for_imbalance
        and measurements.maximum_page_text_ratio
        >= settings.severe_page_imbalance_ratio_threshold
    ):
        dominant_pages = tuple(
            page.page_number
            for page in pages
            if page.non_whitespace_character_count
            == measurements.maximum_page_non_whitespace_character_count
        )
        warnings.append(
            PDFQualityWarning(
                PDFQualityWarningCode.SEVERE_PAGE_IMBALANCE, dominant_pages
            )
        )

    if (
        measurements.control_character_ratio
        >= settings.anomaly_ratio_unusable_threshold
        or measurements.replacement_character_ratio
        >= settings.anomaly_ratio_unusable_threshold
        or measurements.repeated_character_ratio
        >= settings.repeated_character_ratio_unusable_threshold
    ):
        garbage_pages = tuple(
            page.page_number
            for page in pages
            if page.control_character_count
            or page.replacement_character_count
            or page.repeated_character_count
        )
        warnings.append(
            PDFQualityWarning(PDFQualityWarningCode.EXTRACTION_GARBAGE, garbage_pages)
        )

    return tuple(warnings)


def _classify(warnings: tuple[PDFQualityWarning, ...]) -> PDFQualityStatus:
    codes = {warning.code for warning in warnings}
    if codes & {
        PDFQualityWarningCode.NO_PAGES,
        PDFQualityWarningCode.EXTRACTION_GARBAGE,
    }:
        return PDFQualityStatus.UNUSABLE
    if codes & {
        PDFQualityWarningCode.ALL_PAGES_EMPTY,
        PDFQualityWarningCode.HIGH_EMPTY_PAGE_RATIO,
    }:
        return PDFQualityStatus.LIKELY_NEEDS_OCR
    if warnings:
        return PDFQualityStatus.USABLE_WITH_WARNINGS
    return PDFQualityStatus.USABLE


def _page_numbers(
    pages: tuple[PDFPageQualityObservation, ...], attribute: str
) -> tuple[int, ...]:
    return tuple(page.page_number for page in pages if getattr(page, attribute))


def _is_suspicious_control(character: str) -> bool:
    code_point = ord(character)
    return character not in _STRUCTURAL_WHITESPACE and (
        code_point < 0x20 or 0x7F <= code_point <= 0x9F
    )


def _is_assessment_printable(character: str) -> bool:
    return character not in _STRUCTURAL_WHITESPACE and not _is_suspicious_control(
        character
    )


def _count_repeated_characters(text: str, minimum_run: int) -> int:
    repeated_count = 0
    run_character = ""
    run_length = 0
    for character in text:
        if character == run_character:
            run_length += 1
            continue
        if run_character and not run_character.isspace() and run_length >= minimum_run:
            repeated_count += run_length
        run_character = character
        run_length = 1
    if run_character and not run_character.isspace() and run_length >= minimum_run:
        repeated_count += run_length
    return repeated_count
