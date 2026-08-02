"""Tests for projection of early-section conversion into durable records."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    EarlySectionLibraryRecord,
    EarlySectionLibraryValidationError,
    ExtractedPDFPage,
    PDFConversionSettings,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSection,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
    StoredPassageProvenance,
    StoredPassageSourceFragment,
)
from econ_paper_cli.services import (
    convert_pdf_early_sections,
    project_early_section_library_record,
)

CHECKSUM = "a" * 64
TIMESTAMP = "2026-08-01T12:00:00+00:00"


def _inputs(
    *,
    title: str | None = "  Stored Paper  ",
    author_text: str | None = "Ada Economist; Ben Scholar",
    abstract: bool = True,
) -> tuple[PDFExtractionResult, PDFSectionDetectionResult]:
    page_text = "Abstract evidence.\n\nIntroduction evidence."
    extraction = PDFExtractionResult(
        source_path=Path.cwd().resolve() / "local-paper.pdf",
        pages=(ExtractedPDFPage(1, page_text),),
        page_count=1,
        metadata=PDFDocumentMetadata(title=title, author_text=author_text),
        extraction_method="synthetic-parser",
        parser_version="1.2.3",
    )
    sections: list[PDFSection] = []
    if abstract:
        sections.append(
            PDFSection(
                kind=PDFSectionKind.ABSTRACT,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Abstract",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 0, 20),),
                text="Abstract evidence.\n\n",
            )
        )
    sections.append(
        PDFSection(
            kind=PDFSectionKind.INTRODUCTION,
            detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
            observed_heading_text="Introduction",
            start_page_number=1,
            end_page_number=1,
            spans=(PDFSectionSpan(1, 20, len(page_text)),),
            text="Introduction evidence.",
        )
    )
    warnings = (
        () if abstract else (PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),)
    )
    return extraction, PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=tuple(sections),
        candidates=(),
        warnings=warnings,
    )


def _record(
    *,
    title: str | None = "  Stored Paper  ",
    author_text: str | None = "Ada Economist; Ben Scholar",
    abstract: bool = True,
    settings: PDFConversionSettings = PDFConversionSettings(),
) -> EarlySectionLibraryRecord:
    extraction, detection = _inputs(
        title=title, author_text=author_text, abstract=abstract
    )
    conversion = convert_pdf_early_sections(
        extraction, detection, content_checksum=CHECKSUM, settings=settings
    )
    return project_early_section_library_record(
        extraction,
        detection,
        conversion,
        source_file_size=4096,
        timestamp=TIMESTAMP,
    )


def test_projection_metadata_and_exact_mapping_round_trip() -> None:
    record = _record()

    assert record.paper.title == "Stored Paper"
    assert record.paper.authors == ("Ada Economist; Ben Scholar",)
    assert record.paper.abstract == "Abstract evidence.\n\n"
    assert record.paper.source_identifier == CHECKSUM
    assert record.source_provenance.markdown_path is None
    assert record.parser_version == "1.2.3"
    assert record.created_at == record.updated_at == TIMESTAMP
    assert EarlySectionLibraryRecord.from_mapping(record.to_mapping()) == record
    assert record.passage_provenance[0].fragments[0].source_text == (
        record.passages[0].text
    )


def test_projection_fallback_title_empty_authors_and_introduction_only() -> None:
    record = _record(title=" \t", author_text=None, abstract=False)

    assert record.paper.title == "local-paper"
    assert record.paper.authors == ()
    assert record.paper.abstract is None
    assert tuple(passage.section_heading for passage in record.passages) == (
        "Introduction",
    )


def test_record_is_immutable() -> None:
    record = _record()
    with pytest.raises(FrozenInstanceError):
        record.markdown = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("source_file_size", [0, -1, True])
def test_projection_rejects_invalid_source_file_size(source_file_size: object) -> None:
    extraction, detection = _inputs()
    conversion = convert_pdf_early_sections(
        extraction,
        detection,
        content_checksum=CHECKSUM,
        settings=PDFConversionSettings(),
    )
    with pytest.raises(EarlySectionLibraryValidationError, match="source_file_size"):
        project_early_section_library_record(
            extraction,
            detection,
            conversion,
            source_file_size=source_file_size,  # type: ignore[arg-type]
            timestamp=TIMESTAMP,
        )


def test_projection_rejects_conversion_not_matching_inputs() -> None:
    extraction, detection = _inputs()
    conversion = convert_pdf_early_sections(
        extraction,
        detection,
        content_checksum=CHECKSUM,
        settings=PDFConversionSettings(),
    )
    mismatched = replace(conversion, markdown=conversion.markdown + "\n")

    with pytest.raises(EarlySectionLibraryValidationError, match="exactly match"):
        project_early_section_library_record(
            extraction,
            detection,
            mismatched,
            source_file_size=4096,
            timestamp=TIMESTAMP,
        )


def test_record_rejects_source_text_not_matching_passage_slice() -> None:
    record = _record()
    provenance = record.passage_provenance[0]
    fragment = provenance.fragments[0]
    corrupt_fragment = replace(
        fragment,
        source_text="X" + fragment.source_text[1:],
    )
    corrupt_provenance = replace(provenance, fragments=(corrupt_fragment,))

    with pytest.raises(EarlySectionLibraryValidationError, match="passage slice"):
        replace(
            record,
            passage_provenance=(
                corrupt_provenance,
                *record.passage_provenance[1:],
            ),
        )


def test_stored_fragment_and_provenance_mapping_round_trip() -> None:
    record = _record()
    provenance = record.passage_provenance[0]
    fragment = provenance.fragments[0]
    assert StoredPassageSourceFragment.from_mapping(fragment.to_mapping()) == fragment
    assert StoredPassageProvenance.from_mapping(provenance.to_mapping()) == provenance
