"""Pure projection of successful early-section conversion into library storage."""

from econ_paper_cli.domain.early_section_library import (
    EarlySectionLibraryRecord,
    StoredPassageProvenance,
    StoredPassageSourceFragment,
)
from econ_paper_cli.domain.errors import EarlySectionLibraryValidationError
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.pdf_conversion import (
    PDFConversionResult,
    PDFConversionStatus,
)
from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_sections import (
    PDFSectionDetectionResult,
    PDFSectionKind,
)
from econ_paper_cli.domain.storage import SourceProvenance
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections


def project_early_section_library_record(
    extraction: PDFExtractionResult,
    detection: PDFSectionDetectionResult,
    conversion: PDFConversionResult,
    *,
    source_file_size: int,
    timestamp: str,
) -> EarlySectionLibraryRecord:
    """Validate and project successful conversion output into a durable record."""
    if not isinstance(extraction, PDFExtractionResult):
        raise TypeError("extraction must be a PDFExtractionResult instance.")
    if not isinstance(detection, PDFSectionDetectionResult):
        raise TypeError("detection must be a PDFSectionDetectionResult instance.")
    if not isinstance(conversion, PDFConversionResult):
        raise TypeError("conversion must be a PDFConversionResult instance.")
    if conversion.status is not PDFConversionStatus.SUCCESS:
        raise EarlySectionLibraryValidationError(
            "conversion must be a successful PDFConversionResult."
        )
    if (
        isinstance(source_file_size, bool)
        or not isinstance(source_file_size, int)
        or source_file_size < 1
    ):
        raise EarlySectionLibraryValidationError(
            "source_file_size must be a positive integer."
        )
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise EarlySectionLibraryValidationError(
            "timestamp must be a non-empty canonical timestamp string."
        )

    try:
        reproduced = convert_pdf_early_sections(
            extraction,
            detection,
            content_checksum=conversion.content_checksum,
            settings=conversion.settings,
        )
    except ValueError as error:
        raise EarlySectionLibraryValidationError(
            f"extraction and detection inputs are inconsistent: {error}"
        ) from error
    if reproduced != conversion:
        raise EarlySectionLibraryValidationError(
            "conversion does not exactly match the supplied extraction and detection inputs."
        )

    pages = {page.page_number: page.text for page in extraction.pages}
    stored_provenance = tuple(
        StoredPassageProvenance(
            passage_id=provenance.passage_id,
            section_kind=provenance.section_kind,
            fragments=tuple(
                StoredPassageSourceFragment(
                    page_number=fragment.page_number,
                    start_character_offset=fragment.start_character_offset,
                    end_character_offset=fragment.end_character_offset,
                    passage_start_character_offset=(
                        fragment.passage_start_character_offset
                    ),
                    passage_end_character_offset=(
                        fragment.passage_end_character_offset
                    ),
                    source_text=pages[fragment.page_number][
                        fragment.start_character_offset : fragment.end_character_offset
                    ],
                )
                for fragment in provenance.fragments
            ),
        )
        for provenance in conversion.passage_provenance
    )

    metadata_title = extraction.metadata.title
    title = (
        metadata_title.strip()
        if metadata_title is not None and metadata_title.strip()
        else extraction.source_path.stem
    )
    author_text = extraction.metadata.author_text
    authors = (author_text,) if author_text is not None and author_text.strip() else ()
    abstract = next(
        (
            section.text
            for section in detection.sections
            if section.kind is PDFSectionKind.ABSTRACT
        ),
        None,
    )
    paper = Paper(
        paper_id=conversion.paper_id,
        title=title,
        authors=authors,
        year=None,
        abstract=abstract,
        source_name="local-pdf",
        source_identifier=conversion.content_checksum,
        source_url=None,
    )
    source_provenance = SourceProvenance(
        source_path=str(extraction.source_path),
        source_format="pdf",
        source_file_size=source_file_size,
        content_checksum=conversion.content_checksum,
        markdown_path=None,
        extraction_method=extraction.extraction_method,
        created_at=timestamp,
    )
    assert conversion.markdown is not None
    return EarlySectionLibraryRecord(
        paper=paper,
        source_provenance=source_provenance,
        parser_version=extraction.parser_version,
        conversion_settings=conversion.settings,
        settings_fingerprint=conversion.settings_fingerprint,
        markdown=conversion.markdown,
        passages=conversion.passages,
        passage_provenance=stored_provenance,
        created_at=timestamp,
        updated_at=timestamp,
    )
