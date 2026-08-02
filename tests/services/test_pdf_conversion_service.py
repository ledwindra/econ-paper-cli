"""Tests for deterministic early-section Markdown and passage conversion."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
    ExtractedPDFPage,
    PDFConversionSettings,
    PDFConversionStatus,
    PDFConversionValidationError,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSection,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
)
from econ_paper_cli.services import convert_pdf_early_sections

CHECKSUM = "1" * 64


def _extraction(
    *page_texts: str,
    path: str = "paper.pdf",
    title: str | None = "  Synthetic Paper  ",
) -> PDFExtractionResult:
    return PDFExtractionResult(
        source_path=Path.cwd().resolve() / path,
        pages=tuple(
            ExtractedPDFPage(page_number=index, text=text)
            for index, text in enumerate(page_texts, start=1)
        ),
        page_count=len(page_texts),
        metadata=PDFDocumentMetadata(title=title),
        extraction_method="synthetic",
        parser_version="1.0",
    )


def _section(
    kind: PDFSectionKind,
    spans: tuple[PDFSectionSpan, ...],
    text: str,
) -> PDFSection:
    return PDFSection(
        kind=kind,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text=kind.value.title(),
        start_page_number=spans[0].page_number,
        end_page_number=spans[-1].page_number,
        spans=spans,
        text=text,
    )


def _detection(*sections: PDFSection) -> PDFSectionDetectionResult:
    kinds = {section.kind for section in sections}
    warnings = tuple(
        PDFSectionWarning(code)
        for kind, code in (
            (PDFSectionKind.ABSTRACT, PDFSectionWarningCode.MISSING_ABSTRACT),
            (
                PDFSectionKind.INTRODUCTION,
                PDFSectionWarningCode.MISSING_INTRODUCTION,
            ),
        )
        if kind not in kinds
    )
    return PDFSectionDetectionResult(
        # Must agree with DEFAULT_PDF_CONVERSION_SETTINGS.section_policy_version:
        # conversion now rejects a detection produced under a different
        # section policy than the conversion settings claim (see
        # test_rejects_detection_policy_that_disagrees_with_settings).
        policy_version=DEFAULT_PDF_CONVERSION_SETTINGS.section_policy_version,
        sections=sections,
        candidates=(),
        warnings=warnings,
    )


def _convert(
    extraction: PDFExtractionResult,
    detection: PDFSectionDetectionResult,
    *,
    checksum: str = CHECKSUM,
    settings: PDFConversionSettings = DEFAULT_PDF_CONVERSION_SETTINGS,
):
    return convert_pdf_early_sections(
        extraction,
        detection,
        content_checksum=checksum,
        settings=settings,
    )


def test_exact_markdown_section_order_and_page_transition_marker() -> None:
    extraction = _extraction("Abstract body.\n", "Intro first page", "continues.\n")
    abstract = _section(
        PDFSectionKind.ABSTRACT,
        (PDFSectionSpan(1, 0, 15),),
        "Abstract body.\n",
    )
    introduction = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(2, 0, 16), PDFSectionSpan(3, 0, 11)),
        "Intro first pagecontinues.\n",
    )

    result = _convert(extraction, _detection(abstract, introduction))

    assert result.markdown == (
        "# Synthetic Paper\n\n"
        "## Abstract\n\nAbstract body.\n\n"
        "## Introduction\n\nIntro first page\n"
        "<!-- econpapers-page: 3 -->\ncontinues.\n"
    )
    assert result.status is PDFConversionStatus.SUCCESS
    assert tuple(p.section_heading for p in result.passages) == (
        "Abstract",
        "Introduction",
    )


def test_marker_is_only_added_when_page_number_changes() -> None:
    extraction = _extraction("one two")
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 3), PDFSectionSpan(1, 4, 7)),
        "onetwo",
    )
    result = _convert(extraction, _detection(section))
    assert "econpapers-page" not in result.markdown


def test_empty_intermediate_page_span_does_not_emit_a_marker() -> None:
    extraction = _extraction("first", "", "last")
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (
            PDFSectionSpan(1, 0, 5),
            PDFSectionSpan(2, 0, 0),
            PDFSectionSpan(3, 0, 4),
        ),
        "firstlast",
    )
    result = _convert(extraction, _detection(section))
    assert "<!-- econpapers-page: 2 -->" not in result.markdown
    assert "<!-- econpapers-page: 3 -->\nlast" in result.markdown


def test_title_falls_back_to_filename_stem_without_affecting_identities() -> None:
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 10),),
        "Same text.",
    )
    first = _convert(
        _extraction("Same text.", path="old-name.pdf", title=" \t"),
        _detection(section),
    )
    second = _convert(
        _extraction("Same text.", path="renamed.pdf", title=None),
        _detection(section),
    )
    assert first.markdown.startswith("# old-name\n")
    assert second.markdown.startswith("# renamed\n")
    assert first.paper_id == second.paper_id
    assert first.passages == second.passages


def test_checksum_and_settings_have_expected_identity_effects() -> None:
    extraction = _extraction("Same text.")
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 10),),
        "Same text.",
    )
    baseline = _convert(extraction, _detection(section))
    checksum_change = _convert(extraction, _detection(section), checksum="2" * 64)
    settings_change = _convert(
        extraction,
        _detection(section),
        settings=PDFConversionSettings(max_passage_characters=20),
    )
    assert baseline.paper_id != checksum_change.paper_id
    assert baseline.passages[0].passage_id != checksum_change.passages[0].passage_id
    assert baseline.paper_id == settings_change.paper_id
    assert baseline.settings_fingerprint != settings_change.settings_fingerprint
    assert baseline.passages[0].passage_id != settings_change.passages[0].passage_id


@pytest.mark.parametrize("kind", list(PDFSectionKind))
def test_abstract_only_and_introduction_only(kind: PDFSectionKind) -> None:
    extraction = _extraction("Only this section.")
    section = _section(kind, (PDFSectionSpan(1, 0, 18),), "Only this section.")
    result = _convert(extraction, _detection(section))
    assert f"## {kind.value.title()}" in result.markdown
    other = (
        PDFSectionKind.INTRODUCTION
        if kind is PDFSectionKind.ABSTRACT
        else PDFSectionKind.ABSTRACT
    )
    assert f"## {other.value.title()}" not in result.markdown


def test_no_detected_sections_returns_typed_empty_result() -> None:
    result = _convert(_extraction("Later section text."), _detection())
    assert result.status is PDFConversionStatus.NO_USABLE_SECTIONS
    assert result.markdown is None
    assert result.passages == ()
    assert result.passage_provenance == ()


def test_only_detected_spans_are_converted() -> None:
    extraction = _extraction("Introduction text.\n\n2. Data\nLater text.")
    section_text = "Introduction text."
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, len(section_text)),),
        section_text,
    )
    result = _convert(extraction, _detection(section))
    assert "Introduction text." in result.markdown
    assert "2. Data" not in result.markdown
    assert "Later text." not in result.markdown
    assert tuple(p.text for p in result.passages) == (section_text,)


def test_many_page_introduction_has_no_implicit_page_limit() -> None:
    page_texts = tuple(f"Page {number} content.\n" for number in range(1, 21))
    extraction = _extraction(*page_texts)
    spans = tuple(
        PDFSectionSpan(number, 0, len(text))
        for number, text in enumerate(page_texts, start=1)
    )
    section = _section(PDFSectionKind.INTRODUCTION, spans, "".join(page_texts))
    result = _convert(extraction, _detection(section))
    assert "Page 20 content." in result.markdown
    assert result.passages[-1].page_end == 20
    assert result.markdown.count("<!-- econpapers-page:") == 19


def test_passages_pack_paragraphs_and_never_cross_section_boundaries() -> None:
    page = "One.\n\nTwo.\n\nThree."
    extraction = _extraction(page)
    abstract = _section(
        PDFSectionKind.ABSTRACT,
        (PDFSectionSpan(1, 0, 10),),
        "One.\n\nTwo.",
    )
    introduction = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 12, 18),),
        "Three.",
    )
    result = _convert(
        extraction,
        _detection(abstract, introduction),
        settings=PDFConversionSettings(max_passage_characters=20),
    )
    assert tuple(p.text for p in result.passages) == ("One.\n\nTwo.", "Three.")
    assert tuple(p.ordinal_position for p in result.passages) == (0, 1)


def test_oversized_paragraph_splits_at_whitespace_then_hard_boundary() -> None:
    whitespace_text = "alpha beta gamma"
    whitespace_section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, len(whitespace_text)),),
        whitespace_text,
    )
    whitespace_result = _convert(
        _extraction(whitespace_text),
        _detection(whitespace_section),
        settings=PDFConversionSettings(max_passage_characters=10),
    )
    assert tuple(p.text for p in whitespace_result.passages) == (
        "alpha ",
        "beta gamma",
    )

    hard_text = "abcdefghijk"
    hard_section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, len(hard_text)),),
        hard_text,
    )
    hard_result = _convert(
        _extraction(hard_text),
        _detection(hard_section),
        settings=PDFConversionSettings(max_passage_characters=5),
    )
    assert tuple(p.text for p in hard_result.passages) == ("abcde", "fghij", "k")


def test_tiny_budget_never_emits_whitespace_only_passages() -> None:
    text = " a b "
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, len(text)),),
        text,
    )
    result = _convert(
        _extraction(text),
        _detection(section),
        settings=PDFConversionSettings(max_passage_characters=1),
    )
    assert tuple(p.text for p in result.passages) == ("a", "b")
    assert all(len(p.text) <= 1 and p.text.strip() for p in result.passages)


def test_cross_page_passage_provenance_exactly_accounts_for_text() -> None:
    extraction = _extraction("abc", "def")
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 3), PDFSectionSpan(2, 0, 3)),
        "abcdef",
    )
    result = _convert(extraction, _detection(section))
    passage = result.passages[0]
    fragments = result.passage_provenance[0].fragments
    assert (passage.page_start, passage.page_end) == (1, 2)
    assert tuple(
        extraction.pages[f.page_number - 1].text[
            f.start_character_offset : f.end_character_offset
        ]
        for f in fragments
    ) == ("abc", "def")
    assert tuple(
        (f.passage_start_character_offset, f.passage_end_character_offset)
        for f in fragments
    ) == ((0, 3), (3, 6))


def test_rejects_missing_pages_out_of_bounds_and_text_mismatch() -> None:
    extraction = _extraction("abc")
    missing_page = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(2, 0, 1),),
        "x",
    )
    with pytest.raises(PDFConversionValidationError, match="missing page"):
        _convert(extraction, _detection(missing_page))

    out_of_bounds = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 4),),
        "abcd",
    )
    with pytest.raises(PDFConversionValidationError, match="exceeds"):
        _convert(extraction, _detection(out_of_bounds))

    mismatch = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 3),),
        "xyz",
    )
    with pytest.raises(PDFConversionValidationError, match="does not match"):
        _convert(extraction, _detection(mismatch))


def test_rejects_invalid_checksum_and_overlapping_sections() -> None:
    extraction = _extraction("abcdef")
    first = _section(
        PDFSectionKind.ABSTRACT,
        (PDFSectionSpan(1, 3, 6),),
        "def",
    )
    second = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 3),),
        "abc",
    )
    with pytest.raises(PDFConversionValidationError, match="64-character"):
        _convert(extraction, _detection(), checksum="invalid")
    invalid_detection = _detection(second, first)
    object.__setattr__(invalid_detection, "sections", (first, second))
    with pytest.raises(PDFConversionValidationError, match="ordered"):
        _convert(extraction, invalid_detection)


def test_rejects_mutated_out_of_order_spans_at_service_boundary() -> None:
    extraction = _extraction("abcdef")
    section = _section(
        PDFSectionKind.INTRODUCTION,
        (PDFSectionSpan(1, 0, 3), PDFSectionSpan(1, 3, 6)),
        "abcdef",
    )
    detection = _detection(section)
    object.__setattr__(section, "spans", tuple(reversed(section.spans)))
    with pytest.raises(PDFConversionValidationError, match="ordered"):
        _convert(extraction, detection)
