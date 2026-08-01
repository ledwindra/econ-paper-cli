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


def test_page_breaks_treated_as_structural_heading_boundaries() -> None:
    p1 = "1. Introduction\nIntroduction prose line ending at the bottom of page 1."
    p2 = "2 Data and empirical strategy\nData content starting directly at the top of page 2.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    assert len(result.sections) == 1
    intro = result.sections[0]
    assert intro.kind is PDFSectionKind.INTRODUCTION
    assert "Introduction prose line ending" in intro.text
    assert "2 Data and empirical strategy" not in intro.text


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


def test_noun_led_numbered_prose_and_list_items_do_not_truncate_introduction() -> None:
    p1 = (
        "1. Introduction\n"
        "2 million households received the transfer under the new social policy.\n"
        "2. We next estimate the empirical model across municipalities.\n"
        "2 Results are shown in Table 1.\n"
        "2 Robustness checks confirm the result.\n"
        "2 Households respond strongly to the policy.\n"
        "Further introduction prose continues here.\n\n"
        "2. Data\n"
        "We describe the data sources.\n"
    )
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    intro = next(s for s in result.sections if s.kind is PDFSectionKind.INTRODUCTION)
    assert "2 million households received" in intro.text
    assert "2. We next estimate" in intro.text
    assert "2 Results are shown in Table 1." in intro.text
    assert "2 Robustness checks confirm the result." in intro.text
    assert "2 Households respond strongly" in intro.text
    assert "2. Data" not in intro.text


def test_candidate_traceability_on_same_page_ambiguity() -> None:
    p1 = "Abstract\nFirst abstract text.\n\nAbstract\nSecond abstract text.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 0
    assert PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES in [
        w.code for w in result.warnings
    ]

    # Verify candidates preserve exact offset provenance on the same page
    abstract_candidates = tuple(
        c for c in result.candidates if c.kind is PDFSectionKind.ABSTRACT
    )
    assert len(abstract_candidates) == 2
    c1, c2 = abstract_candidates
    assert c1.page_number == 1 and c2.page_number == 1
    assert c1.start_character_offset == 0
    assert c2.start_character_offset > c1.end_character_offset
    assert c1.heading_text == "Abstract" and c2.heading_text == "Abstract"


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


def test_toc_lines_and_running_headers_ignored_for_body_heading_selection() -> None:
    p1 = "Table of Contents\nAbstract ............................ 2\n1. Introduction .................... 3\n\nAbstract\nActual abstract text.\n"
    p2 = "Abstract\n1. Introduction\nActual intro text.\n\n2. Data\nData text.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    assert len(result.sections) == 2
    abs_sec = result.sections[0]
    intro_sec = result.sections[1]

    assert abs_sec.heading_text == "Abstract"
    assert "Actual abstract text" in abs_sec.text
    assert intro_sec.heading_text == "1. Introduction"
    assert "Actual intro text" in intro_sec.text


def test_sentence_case_multiword_next_section_headings() -> None:
    p1 = (
        "1. Introduction\n"
        "Intro text starts here.\n\n"
        "2 Data and empirical strategy\n"
        "Data and strategy text.\n"
    )
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    intro = next(s for s in result.sections if s.kind is PDFSectionKind.INTRODUCTION)
    assert "Intro text starts here." in intro.text
    assert "2 Data and empirical strategy" not in intro.text


def test_abstract_omitted_when_introduction_is_ambiguous() -> None:
    p1 = "Abstract\nAbstract content here.\n"
    p2 = "\n1. Introduction\nFirst intro candidate.\n"
    p3 = "\n1. Introduction\nSecond intro candidate.\n\n2. Data\nData text.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2, p3), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    codes = [w.code for w in result.warnings]
    assert PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES in codes
    assert PDFSectionWarningCode.MISSING_ABSTRACT in codes
    assert len(result.sections) == 0


def test_ambiguous_abstract_and_introduction_candidates_emit_warning_and_omit_section() -> (
    None
):
    p1 = "\nAbstract\nFirst abstract text.\n"
    p2 = "\nAbstract\nSecond abstract text.\n"
    p3 = "\n1. Introduction\nFirst intro text.\n"
    p4 = "\n1. Introduction\nSecond intro text.\n\n2. Data\nData text.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2, p3, p4), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    codes = tuple(w.code for w in result.warnings)
    assert PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES in codes
    assert PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES in codes
    assert len(result.sections) == 0
    assert len(result.candidates) == 4


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
