"""Unit tests for deterministic PDF section detection service."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_SECTION_SETTINGS,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSectionBoundaryEvidenceType,
    PDFSectionDetectionMethod,
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
    assert abstract.observed_heading_text == "Abstract"
    assert "regional productivity growth" in abstract.text
    assert abstract.start_page_number == 1
    assert abstract.end_page_number == 1

    assert intro.kind is PDFSectionKind.INTRODUCTION
    assert intro.observed_heading_text == "1. Introduction"
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
    assert intro_sec.observed_heading_text == "I. INTRODUCTION"
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
    assert intro.observed_heading_text == heading_text
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

    assert len(result.sections) == 0
    codes = tuple(w.code for w in result.warnings)
    assert PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY in codes
    assert PDFSectionWarningCode.MISSING_INTRODUCTION in codes


def test_heading_words_embedded_in_prose_are_ignored() -> None:
    p1 = "Abstract\nIn this paper we present an introduction to regional growth models.\n\n1. Introduction\nThis is the real introduction heading.\n\n2. Model\nModel text.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    assert len(result.sections) == 2
    abs_sec = result.sections[0]
    intro_sec = result.sections[1]

    assert "an introduction to regional growth models" in abs_sec.text
    assert intro_sec.observed_heading_text == "1. Introduction"
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

    assert abs_sec.observed_heading_text == "Abstract"
    assert "Actual abstract text" in abs_sec.text
    assert intro_sec.observed_heading_text == "1. Introduction"
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
    assert PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY in codes
    assert PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES in codes
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


def test_empty_section_bodies_emit_empty_body_warnings() -> None:
    p1 = "Abstract\n   \n\t  \n1. Introduction\n   \n2. Data\nData text.\n"
    result = detect_pdf_sections(_extraction(p1), settings=DEFAULT_PDF_SECTION_SETTINGS)

    codes = [w.code for w in result.warnings]
    assert PDFSectionWarningCode.EMPTY_ABSTRACT_BODY in codes
    assert PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY in codes
    assert len(result.sections) == 0


def test_introduction_preceding_abstract_emits_unresolved_abstract_boundary() -> None:
    p1 = "Department Series\n1. Introduction\nSeries overview text.\n"
    p2 = "Abstract\nReal abstract content.\n\n2. Data\nData text.\n"

    result = detect_pdf_sections(
        _extraction(p1, p2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    codes = [w.code for w in result.warnings]
    assert PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY in codes
    assert PDFSectionWarningCode.MISSING_INTRODUCTION in codes
    assert len(result.sections) == 0


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


def test_heading_grammar_variants_and_case_insensitivity() -> None:
    # 1. Pipe-separated headings (Case D style)
    text1 = "ABSTRACT\nAbstract text.\n\n1 | Introduction\nIntro text.\n\n2 | Related Literature\nRelated literature text.\n"
    res1 = detect_pdf_sections(
        _extraction(text1), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res1.sections) == 2
    assert res1.sections[0].observed_heading_text == "ABSTRACT"
    assert res1.sections[1].observed_heading_text == "1 | Introduction"
    assert "Related literature text" not in res1.sections[1].text

    # 2. Roman numerals (Case B style)
    text2 = "Abstract\nAbstract text.\n\nI. Introduction\nIntro text.\n\nII. Spatial Equilibrium\nSpatial text.\n"
    res2 = detect_pdf_sections(
        _extraction(text2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res2.sections) == 2
    assert res2.sections[1].observed_heading_text == "I. Introduction"
    assert "Spatial text" not in res2.sections[1].text

    # 3. Punctuation-free numbered section 2 (Case A style)
    text3 = "Abstract\nAbstract text.\n\n1. Introduction\nIntro text.\n\n2 Gravity estimation framework\nFramework text.\n"
    res3 = detect_pdf_sections(
        _extraction(text3), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res3.sections) == 2
    assert "Framework text" not in res3.sections[1].text

    # 4. Explicit 'Section 1: Introduction' prefix
    text4 = "Abstract\nAbstract text.\n\nSection 1: Introduction\nIntro text.\n\n2. Data\nData text.\n"
    res4 = detect_pdf_sections(
        _extraction(text4), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res4.sections) == 2
    assert res4.sections[1].observed_heading_text == "Section 1: Introduction"


def test_structured_false_positive_rejections_and_legitimate_headings() -> None:
    # Test equations, citations, cross-references, declarative prose starts, and legitimate headings
    text = (
        "Abstract\nAbstract text.\n\n"
        "1. Introduction\nIntro prose.\n\n"
        "2 Utility = income + leisure\n"
        "2 Smith (2020) shows effects\n"
        "2 Section 3 reports results\n"
        "2 Table 4 reports estimates\n"
        "2 We thank the editor\n\n"
        "2. Our Results\nReal section 2 text.\n"
    )
    res = detect_pdf_sections(_extraction(text), settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res.sections) == 2
    intro = res.sections[1]
    assert intro.observed_heading_text == "1. Introduction"
    assert "2 Utility = income + leisure" in intro.text
    assert "2 Smith (2020) shows effects" in intro.text
    assert "2 Section 3 reports results" in intro.text
    assert "2 Table 4 reports estimates" in intro.text
    assert "2 We thank the editor" in intro.text
    assert "Real section 2 text" not in intro.text


def test_legitimate_headings_with_results_or_test_words() -> None:
    text = "Abstract\nAbstract text.\n\n1. Introduction\nIntro text.\n\n2. What We Test\nTest section text.\n"
    res = detect_pdf_sections(_extraction(text), settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res.sections) == 2
    assert "Test section text" not in res.sections[1].text


def test_roman_i_implicit_vs_explicit_context_split() -> None:
    from econ_paper_cli.services.pdf_section_detection import (
        _extract_lines,
        _find_next_section_candidate,
    )

    ext = _extraction(
        "I. Introduction\nIntro prose.\n\nI. Theoretical Framework\nFramework prose.\n\nII. Spatial Equilibrium\n"
    )
    lines = _extract_lines(ext)

    # 1. Explicit Intro context (is_implicit_intro=False):
    # Starting search from line 1 (after I. Introduction):
    cand_explicit = _find_next_section_candidate(
        lines, start_index=1, running_headers=set(), is_implicit_intro=False
    )
    assert cand_explicit is not None
    # Must skip 'I. Theoretical Framework' and find 'II. Spatial Equilibrium'
    assert cand_explicit.line.trimmed == "II. Spatial Equilibrium"

    # 2. Implicit Intro context (is_implicit_intro=True):
    # Starting search from line 2 (I. Theoretical Framework):
    cand_implicit = _find_next_section_candidate(
        lines, start_index=2, running_headers=set(), is_implicit_intro=True
    )
    assert cand_implicit is not None
    # Must accept 'I. Theoretical Framework'
    assert cand_implicit.line.trimmed == "I. Theoretical Framework"

    # 3. Implicit Intro context (is_implicit_intro=True):
    # Starting search from line 0 (I. Introduction):
    cand_implicit_intro = _find_next_section_candidate(
        lines, start_index=0, running_headers=set(), is_implicit_intro=True
    )
    assert cand_implicit_intro is not None
    # Must NOT select 'I. Introduction' as next boundary, but advance to 'I. Theoretical Framework'
    assert cand_implicit_intro.line.trimmed == "I. Theoretical Framework"


def test_generic_numbered_prose_and_embedded_cross_refs_rejected() -> None:
    text = (
        "Abstract\nAbstract text.\n\n"
        "1. Introduction\nIntro prose.\n\n"
        "2 This paper studies urban growth\n"
        "2 The model predicts higher wages\n"
        "2 These findings imply convergence\n"
        "2 Results in Table 4 show significant gains\n\n"
        "2. Our Results\nReal section 2 text.\n"
    )
    res = detect_pdf_sections(_extraction(text), settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res.sections) == 2
    intro = res.sections[1]
    assert intro.observed_heading_text == "1. Introduction"
    assert "2 This paper studies urban growth" in intro.text
    assert "2 The model predicts higher wages" in intro.text
    assert "2 These findings imply convergence" in intro.text
    assert "2 Results in Table 4 show significant gains" in intro.text
    assert "Real section 2 text" not in intro.text


def test_case_c_unheaded_abstract_and_unheaded_introduction_inference() -> None:
    from econ_paper_cli.domain import PDFSectionBoundaryEvidenceType

    text = (
        "Optimal Monetary Policy in Frictionless Credit Markets\n"
        "Alice Smith and Bob Jones\n"
        "This paper examines optimal central bank policy when credit markets are frictionless.\n"
        "We show that inflation targeting maximizes welfare.\n"
        "JEL Classification: E52, E44\n\n"
        "Unheaded introduction prose explaining the macroeconomic model setup and literature.\n"
        "We build upon classic search-theoretic models of money.\n\n"
        "I. Theoretical Framework\n"
        "Model specifications and equations follow here.\n"
    )
    res = detect_pdf_sections(_extraction(text), settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res.sections) == 2
    abs_sec = res.sections[0]
    intro_sec = res.sections[1]

    assert abs_sec.kind is PDFSectionKind.ABSTRACT
    assert abs_sec.detection_method is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    assert abs_sec.observed_heading_text is None
    assert any(
        e.evidence_type is PDFSectionBoundaryEvidenceType.JEL_CLASSIFICATION_TERMINATOR
        for e in abs_sec.boundary_evidence
    )

    assert intro_sec.kind is PDFSectionKind.INTRODUCTION
    assert intro_sec.detection_method is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    assert intro_sec.observed_heading_text is None
    assert "I. Theoretical Framework" not in intro_sec.text

    tf_offset = text.index("I. Theoretical Framework")
    assert intro_sec.spans[-1].end_character_offset == tf_offset
    assert any(
        e.evidence_type is PDFSectionBoundaryEvidenceType.FIRST_SECTION_HEADING
        and "I. Theoretical Framework" in e.description
        for e in intro_sec.boundary_evidence
    )


def test_case_f_unheaded_abstract_with_explicit_roman_introduction() -> None:
    text = (
        "Dynamic Fiscal Policy in Open Economies\n"
        "Carol Vance\n"
        "We analyze capital taxation when international asset markets are incomplete.\n"
        "JEL Classification: F38, H21\n\n"
        "I. Introduction\n"
        "Fiscal policy choice is a primary determinant of sovereign spread dynamics.\n\n"
        "II. Model Setup\n"
        "Consider a small open economy.\n"
    )
    res = detect_pdf_sections(_extraction(text), settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res.sections) == 2
    abs_sec = res.sections[0]
    intro_sec = res.sections[1]

    assert abs_sec.kind is PDFSectionKind.ABSTRACT
    assert abs_sec.detection_method is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    assert abs_sec.observed_heading_text is None

    assert intro_sec.kind is PDFSectionKind.INTRODUCTION
    assert intro_sec.detection_method is PDFSectionDetectionMethod.EXPLICIT_HEADING
    assert intro_sec.observed_heading_text == "I. Introduction"
    assert "II. Model Setup" not in intro_sec.text


def test_case_e_publisher_cover_sheet_rejection() -> None:
    p1 = "This content downloaded from 192.168.1.1 on Sun, 02 Aug 2026. JSTOR is a not-for-profit service.\n"
    p2 = "Abstract\nAbstract text on page 2.\n\n1. Introduction\nIntro text on page 2.\n\n2. Data\nData text.\n"
    res = detect_pdf_sections(
        _extraction(p1, p2), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res.sections) == 2
    assert res.sections[0].start_page_number == 2
    assert res.sections[1].start_page_number == 2


def test_running_furniture_excluded_and_exact_source_spans_preserved() -> None:
    p1 = "Journal of Economic Literature, Vol 60\nAbstract\nAbstract text on page 1.\n\n1. Introduction\nIntro start on page 1.\n"
    p2 = "Journal of Economic Literature, Vol 60\nIntro continuation on page 2.\nMore intro text.\n\n2. Data\nData section.\n"
    p3 = "Journal of Economic Literature, Vol 60\nData text on page 3.\n"
    ext = _extraction(p1, p2, p3)
    res = detect_pdf_sections(ext, settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res.sections) == 2
    intro = res.sections[1]
    assert intro.observed_heading_text == "1. Introduction"
    # Verify running header 'Journal of Economic Literature, Vol 60' is excluded from page 2 span
    assert "Journal of Economic Literature, Vol 60" not in intro.text
    concat_text = "".join(
        ext.pages[span.page_number - 1].text[
            span.start_character_offset : span.end_character_offset
        ]
        for span in intro.spans
    )
    assert intro.text == concat_text


def test_synthetic_layout_cases_b_c_d_e_f() -> None:
    # Case B: Keywords and JEL after Abstract prose
    b_text = (
        "Macroeconomic Risk and Asset Prices\n"
        "Abstract\n"
        "We develop a DSGE model with recursive preferences.\n"
        "Keywords: Asset Pricing, Risk, DSGE\n"
        "JEL Classification: G12, E44\n\n"
        "1. Introduction\n"
        "Asset pricing puzzles remain central to modern macroeconomics.\n\n"
        "2. Literature\n"
        "Prior literature includes...\n"
    )
    res_b = detect_pdf_sections(
        _extraction(b_text), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res_b.sections) == 2
    assert "Keywords: Asset Pricing" not in res_b.sections[0].text
    assert "JEL Classification: G12" not in res_b.sections[0].text

    # Case C: Parenthetical (JEL ...) terminator plus interleaved * affiliation footnote
    c_text = (
        "Spatial Competition in Retail Markets\n"
        "Jane Doe and John Smith\n"
        "* Department of Economics, Harvard University, Cambridge, MA\n"
        "We study spatial price differentiation in grocery markets.\n"
        "(JEL L11, R12)\n\n"
        "Unheaded introduction prose describing empirical methodology.\n\n"
        "I. Theoretical Framework\n"
        "The model assumes location choice along a circle.\n"
    )
    res_c = detect_pdf_sections(
        _extraction(c_text), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res_c.sections) == 2
    assert "Department of Economics" not in res_c.sections[0].text
    assert "(JEL L11, R12)" not in res_c.sections[0].text
    assert (
        res_c.sections[1].detection_method
        is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    )
    assert "I. Theoretical Framework" not in res_c.sections[1].text

    # Case D: Keywords before ABSTRACT, JEL after prose
    d_text = (
        "Trade Elasticities and Exchange Rates\n"
        "Keywords: Trade, Exchange Rates\n\n"
        "ABSTRACT\n"
        "This paper estimates trade elasticities using micro tariff data.\n"
        "JEL Classification: F14, F31\n\n"
        "1 | Introduction\n"
        "Understanding trade response is critical for policy.\n\n"
        "2 | Empirical Model\n"
        "We specify a gravity equation.\n"
    )
    res_d = detect_pdf_sections(
        _extraction(d_text), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res_d.sections) == 2
    assert res_d.sections[0].observed_heading_text == "ABSTRACT"
    assert res_d.sections[1].observed_heading_text == "1 | Introduction"
    assert "JEL Classification" not in res_d.sections[0].text

    # Case E: ARTICLE HISTORY / KEYWORDS / JEL after Abstract
    e_text = (
        "Fiscal Multipliers in Recessions\n"
        "Abstract\n"
        "We estimate government spending multipliers during deep economic downturns.\n"
        "ARTICLE HISTORY: Received 10 Jan 2025; Accepted 15 May 2026\n"
        "Keywords: Fiscal Policy, Multipliers\n"
        "JEL Classification: E62, H30\n\n"
        "1. Introduction\n"
        "The size of fiscal multipliers is widely debated.\n\n"
        "2. Data\n"
        "Quarterly VAR data are collected.\n"
    )
    res_e = detect_pdf_sections(
        _extraction(e_text), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res_e.sections) == 2
    assert "ARTICLE HISTORY" not in res_e.sections[0].text
    assert "Keywords:" not in res_e.sections[0].text

    # Case F: Unheaded Abstract terminated by same-page We thank..., Page 2 header and explicit I. Introduction
    f_p1 = (
        "Copyright 2026 American Economic Association\n"
        "Optimal Tax Progressivity\n"
        "We quantify optimal income tax schedules with heterogeneous skills.\n"
        "We thank the editor and anonymous referees for helpful comments.\n"
    )
    f_p2 = (
        "123 JOURNAL OF POLITICAL ECONOMY\n"
        "I. Introduction\n"
        "Progressive taxation balances efficiency and equity.\n"
        "In this paper (1) we find high top rates; (2) we find broad base.\n\n"
        "II. Model\n"
        "Model equations here.\n"
    )
    ext_f = _extraction(f_p1, f_p2)
    res_f = detect_pdf_sections(ext_f, settings=DEFAULT_PDF_SECTION_SETTINGS)
    assert len(res_f.sections) == 2
    assert res_f.sections[0].kind is PDFSectionKind.ABSTRACT
    assert (
        res_f.sections[0].detection_method
        is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    )
    ev_types_f = {e.evidence_type for e in res_f.sections[0].boundary_evidence}
    assert PDFSectionBoundaryEvidenceType.TITLE_BLOCK in ev_types_f
    assert PDFSectionBoundaryEvidenceType.ACKNOWLEDGMENTS_START in ev_types_f
    assert "We thank the editor" not in res_f.sections[0].text
    assert "We quantify optimal" in res_f.sections[0].text

    assert res_f.sections[1].kind is PDFSectionKind.INTRODUCTION
    assert (
        res_f.sections[1].detection_method is PDFSectionDetectionMethod.EXPLICIT_HEADING
    )
    assert res_f.sections[1].observed_heading_text == "I. Introduction"
    assert "JOURNAL OF POLITICAL ECONOMY" not in res_f.sections[1].text
    assert "(1) we find high top rates" in res_f.sections[1].text


def test_metadata_words_in_body_prose_not_excluded() -> None:
    prose_text = (
        "Macroeconomic Risk and Asset Prices\n"
        "ABSTRACT\n"
        "This paper analyzes macroeconomic risk.\n\n"
        "1. Introduction\n"
        "The keywords used in this literature are reviewed extensively.\n"
        "In addition, the JEL classification scheme was updated in recent years.\n"
        "We thank the authors who pioneered this literature.\n\n"
        "2. Literature\n"
        "Prior work includes...\n"
    )
    res = detect_pdf_sections(
        _extraction(prose_text), settings=DEFAULT_PDF_SECTION_SETTINGS
    )
    assert len(res.sections) == 2
    intro = res.sections[1]
    assert "keywords used in this literature" in intro.text
    assert "JEL classification scheme was updated" in intro.text
    assert "We thank the authors who pioneered" in intro.text


# --- Paragraph-break heading recognition (issue #59 review) ----------------
#
# Real journal layouts emit the next top-level heading with no blank line and
# no page edge around it (issue #59 cases A, B, D, E). The rule that admits
# those must not also swallow numbered prose.


def test_next_section_heading_without_blank_line_terminates_introduction() -> None:
    """The real structural pattern: hard-wrapped body text, a sentence-final
    line, then a short heading, with no blank separator anywhere."""
    page = (
        "Abstract\n"
        "We study how regional labour markets absorb migration inflows here.\n"
        "\n"
        "1. Introduction\n"
        "The literature has long debated how local labour markets adjust to a\n"
        "sudden inflow of workers, and whether wages or employment respond more\n"
        "strongly over the medium run in affected metropolitan regions today.\n"
        "2 Theoretical framework\n"
        "We now set out a spatial equilibrium model of local labour demand and\n"
        "supply that we take to the data in the following empirical section.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    intro = next(s for s in result.sections if s.kind is PDFSectionKind.INTRODUCTION)
    assert "sudden inflow of workers" in intro.text
    assert "2 Theoretical framework" not in intro.text
    assert "spatial equilibrium model" not in intro.text


def test_numbered_prose_continuation_is_not_a_section_boundary() -> None:
    """The review's false-positive case: a numbered fragment mid-paragraph
    whose next line continues the sentence in lowercase is prose, not a
    Section 2 heading."""
    page = (
        "Abstract\n"
        "We study how regional labour markets absorb migration inflows here.\n"
        "\n"
        "1. Introduction\n"
        "A completed introductory sentence.\n"
        "2 Higher prices\n"
        "continue to reduce demand in the model.\n"
        "More body prose follows in this paragraph and should be retained.\n"
        "\n"
        "3. Real Next Section\n"
        "This belongs to the following section entirely.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    intro = next(s for s in result.sections if s.kind is PDFSectionKind.INTRODUCTION)
    assert "2 Higher prices" in intro.text
    assert "continue to reduce demand in the model." in intro.text
    assert "This belongs to the following section entirely." not in intro.text


def test_heading_shaped_line_without_sentence_end_before_it_is_not_a_boundary() -> None:
    """Without a completed sentence immediately before it, a short numbered
    line is a wrapped fragment, not a heading."""
    page = (
        "Abstract\n"
        "We study how regional labour markets absorb migration inflows here.\n"
        "\n"
        "1. Introduction\n"
        "The estimated elasticity is bounded above by the value reported in\n"
        "2 Alternative specifications\n"
        "Robustness checks confirm the same qualitative pattern in all cases.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    intro = next(s for s in result.sections if s.kind is PDFSectionKind.INTRODUCTION)
    assert "2 Alternative specifications" in intro.text


# --- Metadata-block termination (issue #59 review) -------------------------


def test_metadata_block_ends_at_body_prose_using_an_unlisted_verb() -> None:
    """A verb allowlist cannot generalize: "derives" is not enumerated, but
    the sentence is plainly body prose and must be retained."""
    page = (
        "Abstract\n"
        "We summarize the result briefly here for interested readers today.\n"
        "Keywords: trade, cities\n"
        "Our framework derives bilateral migration flows.\n"
        "\n"
        "1. Introduction\n"
        "We examine the allocation question across many competing firms here.\n"
        "\n"
        "2. Model\n"
        "Next section body text.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    abstract = next(s for s in result.sections if s.kind is PDFSectionKind.ABSTRACT)
    assert "Keywords: trade, cities" not in abstract.text
    assert "Our framework derives bilateral migration flows." in abstract.text


def test_body_sentence_opening_with_financial_support_is_retained() -> None:
    """ "Financial support ..." is both a funding-footnote opener and an
    ordinary sentence opener; sentence-shaped prose must not be excluded."""
    page = (
        "Abstract\n"
        "We summarize the result briefly here for interested readers today.\n"
        "\n"
        "1. Introduction\n"
        "Financial support for the program declined sharply after 2015.\n"
        "The decline reduced participation across all regions we studied.\n"
        "\n"
        "2. Model\n"
        "Next section body text.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    intro = next(s for s in result.sections if s.kind is PDFSectionKind.INTRODUCTION)
    assert (
        "Financial support for the program declined sharply after 2015." in intro.text
    )
    assert "The decline reduced participation" in intro.text


def test_genuine_funding_footnote_block_is_still_excluded() -> None:
    """The safeguard above must not disable the real exclusion: a funding
    footnote written as fragments (no sentence shape) is still removed."""
    page = (
        "Abstract\n"
        "We summarize the result briefly here for interested readers today.\n"
        "Financial support: NSF grant 1745302; ESRC grant ES/T000001/1\n"
        "and the Leverhulme Trust, whose assistance we note\n"
        "\n"
        "1. Introduction\n"
        "We examine the allocation question across many competing firms here.\n"
        "\n"
        "2. Model\n"
        "Next section body text.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    abstract = next(s for s in result.sections if s.kind is PDFSectionKind.ABSTRACT)
    assert "NSF grant 1745302" not in abstract.text
    assert "Leverhulme Trust" not in abstract.text


def test_interleaved_author_footnote_inside_body_prose_is_excluded() -> None:
    """Issue #59 case C: the author-affiliation/acknowledgments footnote is
    extracted interleaved into page-1 body text, not cleanly before it."""
    page = (
        "A Synthetic Title About Markets\n"
        "By A Researcher and B Researcher*\n"
        "We summarize the finding compactly for readers of the journal here.\n"
        "(JEL D44, Q24)\n"
        "Land use change contributes a large share of global emissions today,\n"
        "and market mechanisms aim to combat this degradation at a low cost.\n"
        "* Researcher: Yale School of the Environment (email: a@example.edu);\n"
        "B Researcher: Harvard University (email: b@example.edu). We thank the\n"
        "coeditor and three anonymous referees for their generous feedback,\n"
        "\n"
        "I. Theoretical Framework\n"
        "There exists a continuum of landowners indexed by i in the model.\n"
    )
    result = detect_pdf_sections(
        _extraction(page), settings=DEFAULT_PDF_SECTION_SETTINGS
    )

    retained = "".join(s.text for s in result.sections)
    assert "Land use change contributes" in retained
    assert "Yale School" not in retained
    assert "We thank the" not in retained
    assert "@example.edu" not in retained
    assert "There exists a continuum" not in retained
