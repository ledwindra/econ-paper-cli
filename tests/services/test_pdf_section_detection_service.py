"""Unit tests for deterministic PDF section detection service."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_SECTION_SETTINGS,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSectionKind,
    PDFSectionWarningCode,
)
from econ_paper_cli.services import detect_pdf_sections


def _extraction(*page_texts: str) -> PDFExtractionResult:
    return PDFExtractionResult(
        source_path=Path.cwd().resolve() / "synthetic-paper.pdf",
        pages=tuple(
            ExtractedPDFPage(page_number=index, text=text)
            for index, text in enumerate(page_texts, start=1)
        ),
        page_count=len(page_texts),
        metadata=PDFDocumentMetadata(title="Synthetic Section Detection Fixture"),
        extraction_method="fake-pdf",
        parser_version="1.0",
    )


def test_abstract_and_introduction_separate_pages() -> None:
    p1 = "Abstract\nThis paper examines regional productivity growth.\n"
    p2 = "1. Introduction\nLocal institutions significantly influence investment dynamics.\n\n2. Data\nWe collect panel data from local municipalities.\n"
    result = detect_pdf_sections(
        _extraction(p1, p2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    assert len(result.sections) == 2
    abstract = result.sections[0]
    intro = result.sections[1]

    assert abstract.kind is PDFSectionKind.ABSTRACT
    assert abstract.heading_text == "Abstract"
    assert "regional productivity growth" in abstract.text
    assert abstract.start_page_number == 1
    assert abstract.end_page_number == 1

    assert intro.kind is PDFSectionKind.INTRODUCTION
    assert intro.heading_text == "1. Introduction"
    assert "influence investment dynamics" in intro.text
    assert intro.start_page_number == 2
    assert intro.end_page_number == 2
    assert "2. Data" not in intro.text


def test_abstract_and_introduction_same_page() -> None:
    text = (
        "Abstract\n"
        "We study public policy impact on labor mobility.\n\n"
        "Introduction\n"
        "Labor mobility is a key factor in economic growth.\n\n"
        "2. Empirical Framework\n"
        "We specify a structural estimation model.\n"
    )
    result = detect_pdf_sections(
        _extraction(text), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    assert len(result.sections) == 2
    abs_sec = result.sections[0]
    intro_sec = result.sections[1]

    assert abs_sec.start_page_number == 1 and abs_sec.end_page_number == 1
    assert intro_sec.start_page_number == 1 and intro_sec.end_page_number == 1
    assert "public policy impact" in abs_sec.text
    assert "Labor mobility is a key factor" in intro_sec.text
    assert "2. Empirical Framework" not in intro_sec.text

    # Verify spans on same page do not overlap
    assert (
        abs_sec.spans[0].end_character_offset
        <= intro_sec.spans[0].start_character_offset
    )


def test_introduction_after_several_pages_front_matter() -> None:
    p1 = "Working Paper Series 2026-08\nDepartment of Economics\n"
    p2 = "Acknowledgements\nWe thank seminar participants for helpful feedback.\n"
    p3 = "Abstract\nSynthetic paper on trade flows.\n"
    p4 = "Table of Contents\nAbstract ...... 3\n1. Introduction ...... 5\n"
    p5 = "I. INTRODUCTION\nTrade flows react dynamically to tariff adjustments.\n\nII. METHODOLOGY\nWe construct a multi-country trade model.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2, p3, p4, p5), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    assert len(result.sections) == 2
    abs_sec = result.sections[0]
    intro_sec = result.sections[1]

    assert abs_sec.start_page_number == 3
    assert intro_sec.start_page_number == 5
    assert intro_sec.heading_text == "I. INTRODUCTION"
    assert "tariff adjustments" in intro_sec.text


@pytest.mark.parametrize(
    "heading_text",
    [
        "Introduction",
        "1 Introduction",
        "1. Introduction",
        "1.0 Introduction",
        "I. Introduction",
        "I. INTRODUCTION",
        "1. INTRODUCTION.",
    ],
)
def test_introduction_heading_variations(heading_text: str) -> None:
    p1 = f"Abstract\nShort abstract text.\n\n{heading_text}\nThis is the introduction text.\n\n2 Data\nData text.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 2
    intro = result.sections[1]
    assert intro.heading_text == heading_text
    assert "introduction text" in intro.text


def test_multipage_section_content_preserves_page_boundaries_and_offsets() -> None:
    p1 = "1. Introduction\nPage 1 intro content.\n"
    p2 = "Page 2 intro content continues here.\n"
    p3 = "Page 3 intro content concludes.\n\n2. Literature Review\nReview content.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2, p3), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    intro = result.sections[0]
    assert intro.start_page_number == 1
    assert intro.end_page_number == 3
    assert len(intro.spans) == 3
    assert tuple(s.page_number for s in intro.spans) == (1, 2, 3)
    assert "Page 1 intro content" in intro.text
    assert "Page 2 intro content" in intro.text
    assert "Page 3 intro content" in intro.text


def test_missing_abstract_and_missing_next_section_warnings() -> None:
    p1 = "1. Introduction\nOnly introduction exists in this document.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 1
    assert result.sections[0].kind is PDFSectionKind.INTRODUCTION
    codes = tuple(w.code for w in result.warnings)
    assert PDFSectionWarningCode.MISSING_ABSTRACT in codes
    assert PDFSectionWarningCode.MISSING_NEXT_SECTION_BOUNDARY in codes


def test_missing_introduction_warning() -> None:
    p1 = "Abstract\nOnly abstract exists in this document.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 1
    assert result.sections[0].kind is PDFSectionKind.ABSTRACT
    codes = tuple(w.code for w in result.warnings)
    assert PDFSectionWarningCode.MISSING_INTRODUCTION in codes


def test_heading_words_embedded_in_prose_are_ignored() -> None:
    p1 = "Abstract\nIn this paper we present an introduction to regional growth models.\n\n1. Introduction\nThis is the real introduction heading.\n\n2. Model\nModel text.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 2
    abs_sec = result.sections[0]
    intro_sec = result.sections[1]

    assert "an introduction to regional growth models" in abs_sec.text
    assert intro_sec.heading_text == "1. Introduction"
    assert "real introduction heading" in intro_sec.text


def test_toc_lines_ignored_as_heading_candidates() -> None:
    p1 = "Table of Contents\nAbstract ............................ 2\n1. Introduction .................... 3\n\nAbstract\nActual abstract text.\n\n1. Introduction\nActual intro text.\n\n2. Data\nData text.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 2
    assert result.sections[0].heading_text == "Abstract"
    assert "Actual abstract text" in result.sections[0].text
    assert result.sections[1].heading_text == "1. Introduction"
    assert "Actual intro text" in result.sections[1].text


def test_duplicate_heading_candidates_emit_warning() -> None:
    p1 = "Abstract\nFirst abstract text.\n"
    p2 = "Abstract\nSecond abstract text.\n\n1. Introduction\nIntro text.\n\n2. Data\nData text.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    codes = tuple(w.code for w in result.warnings)
    assert PDFSectionWarningCode.DUPLICATE_ABSTRACT_CANDIDATES in codes


def test_empty_pages_and_zero_page_results() -> None:
    empty_res = detect_pdf_sections(
        _extraction(), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert empty_res.warnings[0].code is PDFSectionWarningCode.NO_PAGES

    blank_res = detect_pdf_sections(
        _extraction("", "  \n\t"), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    codes = tuple(w.code for w in blank_res.warnings)
    assert PDFSectionWarningCode.ALL_PAGES_EMPTY in codes


def test_repeated_run_equivalence() -> None:
    extraction = _extraction(
        "Abstract\nAbstract text.\n\n1. Introduction\nIntro text.\n\n2. Data\nData text.\n"
    )
    res1 = detect_pdf_sections(extraction, settings=DEFAULT_PDF_SECTION_SETTINGS)
    res2 = detect_pdf_sections(extraction, settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert res1 == res2


def test_service_rejects_invalid_inputs() -> None:
    with pytest.raises(TypeError, match="extraction"):
        detect_pdf_sections(object(), settings=DEFAULT_PDF_SECTION_SETTINGS)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="settings"):
        detect_pdf_sections(_extraction("Abstract\nText"), settings=object())  # type: ignore[arg-type]
