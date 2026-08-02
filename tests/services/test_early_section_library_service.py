"""Tests for projection of early-section conversion into durable records."""

import dataclasses
import hashlib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
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
    PDFSectionSettings,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
    PreflightCandidate,
    SinglePaperAnalysisSettings,
    StoredPassageProvenance,
    StoredPassageSourceFragment,
)
from econ_paper_cli.services import (
    convert_pdf_early_sections,
    project_early_section_library_record,
)
from econ_paper_cli.services.analysis_library import LibraryPopulationStatus
from econ_paper_cli.services.single_paper_analysis_cli import _process_candidate

CHECKSUM = "a" * 64
TIMESTAMP = "2026-08-01T12:00:00+00:00"


def _inputs(
    *,
    title: str | None = "  Stored Paper  ",
    author_text: str | None = "Ada Economist; Ben Scholar",
    abstract: bool = True,
    section_policy_version: str = DEFAULT_PDF_CONVERSION_SETTINGS.section_policy_version,
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
        policy_version=section_policy_version,
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
    # Detection policy and conversion settings must agree, so a record
    # built for a given section policy really is what that pipeline
    # would have produced (passage identities included).
    extraction, detection = _inputs(
        title=title,
        author_text=author_text,
        abstract=abstract,
        section_policy_version=settings.section_policy_version,
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


class _FixedExtractor:
    """Returns one prebuilt extraction for whatever path it is handed."""

    def __init__(self, extraction: PDFExtractionResult) -> None:
        self._extraction = extraction

    def extract(self, source_path: Path) -> PDFExtractionResult:
        return dataclasses.replace(self._extraction, source_path=source_path.resolve())


class _UnusedGenerator:
    def generate(self, request: object) -> object:  # pragma: no cover - never called
        raise AssertionError("generator must not be needed for this path")


def test_stale_v1_record_is_rejected_and_replaced_through_production_reuse_path(
    tmp_path: Path,
) -> None:
    """Issue #59 review: the *production* reuse path (``_process_candidate``,
    what ``econpapers analyze`` actually runs) must reject a stored
    early-section record produced under section policy v1 once v2 is
    active, and replace it — verified across a real close/reopen, not by
    calling the SQLite adapter's query helper directly.
    """
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 synthetic")
    checksum = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    # Page text the *real* detector can actually parse: this path runs
    # detect_pdf_sections itself, so a hand-built detection result would
    # not be exercised at all.
    page_text = (
        "Synthetic Working Paper On Regional Markets\n"
        "By A Researcher\n"
        "\n"
        "Abstract\n"
        "This paper studies how synthetic regional markets allocate scarce "
        "resources across competing firms under uncertainty.\n"
        "\n"
        "1. Introduction\n"
        "We examine the allocation question in detail, building a tractable "
        "model of firm entry and comparing it against observed outcomes.\n"
        "\n"
        "2. Model\n"
        "This content belongs to the next section and must not be retained.\n"
    )
    extractor = _FixedExtractor(
        PDFExtractionResult(
            source_path=pdf_path.resolve(),
            pages=(ExtractedPDFPage(1, page_text),),
            page_count=1,
            metadata=PDFDocumentMetadata(title="Synthetic", author_text="A Researcher"),
            extraction_method="synthetic-parser",
            parser_version="1.2.3",
        )
    )
    candidate = PreflightCandidate(
        source_path=pdf_path.resolve(),
        file_size_bytes=pdf_path.stat().st_size,
        content_checksum=checksum,
        is_stored=False,
        is_batch_duplicate=False,
    )

    def _run(section_policy_version: str, storage: SQLiteStorage):
        return _process_candidate(
            pdf_path=pdf_path.resolve(),
            candidate=candidate,
            extractor=extractor,
            generator_provider=lambda: _UnusedGenerator(),
            storage=storage,
            analysis_settings=SinglePaperAnalysisSettings(
                section_settings=PDFSectionSettings(
                    policy_version=section_policy_version
                )
            ),
            conversion_settings=PDFConversionSettings(
                section_policy_version=section_policy_version
            ),
            timestamp_provider=lambda: TIMESTAMP,
        )

    db_file = tmp_path / "library.sqlite3"
    storage = SQLiteStorage(db_file)
    storage.initialize()
    first = _run("pdf-section-detection-v1", storage)
    assert first.library_result is not None
    v1_fingerprint = first.library_result.conversion_fingerprint
    assert v1_fingerprint is not None
    storage.close()

    # Restart, then re-run the same candidate under section policy v2.
    reopened = SQLiteStorage(db_file)
    reopened.initialize()
    second = _run("pdf-section-detection-v2", reopened)
    assert second.library_result is not None
    # Not REUSED: the stale v1 record must not satisfy a v2 request.
    assert second.library_result.status is not LibraryPopulationStatus.REUSED
    v2_fingerprint = second.library_result.conversion_fingerprint
    assert v2_fingerprint is not None
    assert v2_fingerprint != v1_fingerprint
    reopened.close()

    # Restart again: the replacement persisted, and the record now reads
    # back only under v2 settings — never under the old v1 identity.
    reopened2 = SQLiteStorage(db_file)
    reopened2.initialize()
    paper_id = f"paper-{checksum}"
    persisted = reopened2.get_early_section_record(
        paper_id,
        settings=PDFConversionSettings(
            section_policy_version="pdf-section-detection-v2"
        ),
    )
    assert persisted is not None
    assert persisted.conversion_settings.section_policy_version == (
        "pdf-section-detection-v2"
    )
    assert persisted.settings_fingerprint == v2_fingerprint
    assert (
        reopened2.get_early_section_record(
            paper_id,
            settings=PDFConversionSettings(
                section_policy_version="pdf-section-detection-v1"
            ),
        )
        is None
    )
    reopened2.close()


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
