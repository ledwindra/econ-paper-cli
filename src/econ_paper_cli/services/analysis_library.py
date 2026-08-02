"""Prepare analysis and early-section records for coordinated persistence."""

from dataclasses import dataclass

from econ_paper_cli.adapters.filesystem import FileInspectionResult
from econ_paper_cli.domain.early_section_library import EarlySectionLibraryRecord
from econ_paper_cli.domain.errors import EarlySectionLibraryValidationError
from econ_paper_cli.domain.library_population import (
    LibraryPopulationResult,
    LibraryPopulationStatus,
)
from econ_paper_cli.domain.pdf_conversion import (
    PDFConversionSettings,
    PDFConversionStatus,
    compute_conversion_paper_id,
    compute_conversion_settings_fingerprint,
)
from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_quality import PDFQualityStatus
from econ_paper_cli.domain.pdf_sections import PDFSectionDetectionResult
from econ_paper_cli.domain.single_paper_analysis import (
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
)
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections


@dataclass(frozen=True, slots=True)
class PreparedAnalysisLibrary:
    """Prepared immutable records and their terminal library outcome."""

    analysis_record: SinglePaperAnalysisRecord
    library_record: EarlySectionLibraryRecord | None
    library_result: LibraryPopulationResult


@dataclass(frozen=True, slots=True)
class PreparedEarlySectionLibrary:
    """Prepared early-section record and its terminal outcome."""

    library_record: EarlySectionLibraryRecord | None
    library_result: LibraryPopulationResult


def prepare_analysis_record(
    result: SinglePaperAnalysisResult,
    settings: SinglePaperAnalysisSettings,
    *,
    timestamp: str,
    existing: SinglePaperAnalysisRecord | None = None,
) -> SinglePaperAnalysisRecord:
    """Project transient analysis with an explicit orchestration timestamp."""
    created_at = existing.created_at if existing is not None else timestamp
    return SinglePaperAnalysisRecord.from_result(
        result,
        settings=settings,
        created_at=created_at,
        updated_at=timestamp,
    )


def prepare_analysis_library(
    inspection: FileInspectionResult,
    result: SinglePaperAnalysisResult,
    analysis_settings: SinglePaperAnalysisSettings,
    conversion_settings: PDFConversionSettings,
    *,
    timestamp: str,
    existing_analysis: SinglePaperAnalysisRecord | None = None,
) -> PreparedAnalysisLibrary:
    """Prepare eligible analysis/library records without persistence effects."""
    analysis_record = prepare_analysis_record(
        result, analysis_settings, timestamp=timestamp, existing=existing_analysis
    )
    if (
        result.checksum is None
        or result.extraction_result is None
        or result.quality_assessment is None
        or result.section_result is None
        or result.quality_assessment.status
        in (PDFQualityStatus.LIKELY_NEEDS_OCR, PDFQualityStatus.UNUSABLE)
    ):
        return PreparedAnalysisLibrary(
            analysis_record,
            None,
            LibraryPopulationResult(LibraryPopulationStatus.NOT_ELIGIBLE),
        )
    canonical_path = inspection.file_path.resolve()
    if canonical_path != result.source_path.resolve():
        raise EarlySectionLibraryValidationError(
            "inspected path does not match analysis source path."
        )
    if inspection.sha256.lower() != result.checksum.lower():
        raise EarlySectionLibraryValidationError(
            "inspected checksum does not match analysis checksum."
        )
    if result.extraction_result.source_path.resolve() != canonical_path:
        raise EarlySectionLibraryValidationError(
            "extraction source path does not match inspected path."
        )
    prepared_library = prepare_early_section_library(
        inspection,
        result.extraction_result,
        result.section_result,
        conversion_settings,
        timestamp=timestamp,
    )
    return PreparedAnalysisLibrary(
        analysis_record,
        prepared_library.library_record,
        prepared_library.library_result,
    )


def prepare_early_section_library(
    inspection: FileInspectionResult,
    extraction: PDFExtractionResult,
    sections: PDFSectionDetectionResult,
    settings: PDFConversionSettings,
    *,
    timestamp: str,
) -> PreparedEarlySectionLibrary:
    """Prepare a library record from already-produced deterministic inputs."""
    canonical_path = inspection.file_path.resolve()
    if extraction.source_path.resolve() != canonical_path:
        raise EarlySectionLibraryValidationError(
            "extraction source path does not match inspected path."
        )
    conversion = convert_pdf_early_sections(
        extraction,
        sections,
        content_checksum=inspection.sha256.lower(),
        settings=settings,
    )
    if conversion.status is PDFConversionStatus.NO_USABLE_SECTIONS:
        return PreparedEarlySectionLibrary(
            None,
            LibraryPopulationResult(
                LibraryPopulationStatus.NO_USABLE_SECTIONS,
                paper_id=compute_conversion_paper_id(inspection.sha256.lower()),
                conversion_fingerprint=compute_conversion_settings_fingerprint(
                    settings
                ),
            ),
        )
    library_record = project_early_section_library_record(
        extraction,
        sections,
        conversion,
        source_file_size=inspection.size_bytes,
        timestamp=timestamp,
    )
    return PreparedEarlySectionLibrary(
        library_record,
        LibraryPopulationResult(
            LibraryPopulationStatus.STORED,
            paper_id=library_record.paper.paper_id,
            conversion_fingerprint=library_record.settings_fingerprint,
            passage_count=len(library_record.passages),
        ),
    )
