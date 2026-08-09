"""Seed a deterministic library record for the offline release workflow."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from econ_paper_cli.adapters.filesystem import FileInspectionResult
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    ExtractedPDFPage,
    IngestionPreflightResult,
    PDFDocumentMetadata,
    PDFExtractionQualityAssessment,
    PDFExtractionResult,
    PDFPageQualityObservation,
    PDFQualityMeasurements,
    PDFQualityStatus,
    PDFSection,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
    PreflightCandidate,
    ResearchQuestionEvidence,
    ResearchQuestionKind,
    ResearchQuestionResult,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
)
from econ_paper_cli.services.analysis_library import prepare_analysis_library

ABSTRACT = "This synthetic paper studies local trade policy."
INTRODUCTION = "The introduction describes a reproducible economic question."
TIMESTAMP = "2026-08-08T00:00:00+00:00"


def _quality(text: str) -> PDFExtractionQualityAssessment:
    character_count = len(text)
    printable_count = sum(character.isprintable() for character in text)
    non_whitespace_count = sum(not character.isspace() for character in text)
    observation = PDFPageQualityObservation(
        page_number=1,
        character_count=character_count,
        printable_character_count=printable_count,
        non_whitespace_character_count=non_whitespace_count,
        control_character_count=0,
        replacement_character_count=0,
        repeated_character_count=0,
        is_empty=False,
        is_sparse=False,
    )
    measurements = PDFQualityMeasurements(
        page_count=1,
        total_character_count=character_count,
        printable_character_count=printable_count,
        non_whitespace_character_count=non_whitespace_count,
        empty_page_count=0,
        sparse_page_count=0,
        control_character_count=0,
        replacement_character_count=0,
        repeated_character_count=0,
        minimum_page_non_whitespace_character_count=non_whitespace_count,
        maximum_page_non_whitespace_character_count=non_whitespace_count,
    )
    return PDFExtractionQualityAssessment(
        policy_version=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.quality_settings.policy_version,
        status=PDFQualityStatus.USABLE,
        measurements=measurements,
        pages=(observation,),
        warnings=(),
    )


def _analysis_result(
    pdf_path: Path,
) -> tuple[FileInspectionResult, SinglePaperAnalysisResult]:
    payload = pdf_path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    file_size = len(payload)
    text = f"{ABSTRACT}\n\n{INTRODUCTION}"
    abstract_end = len(ABSTRACT)
    introduction_start = abstract_end + 2

    candidate = PreflightCandidate(
        source_path=pdf_path,
        file_size_bytes=file_size,
        content_checksum=checksum,
        is_stored=False,
        is_batch_duplicate=False,
    )
    preflight = IngestionPreflightResult(
        target_path=pdf_path,
        candidates=(candidate,),
        new_candidate_count=1,
        stored_candidate_count=0,
        batch_duplicate_count=0,
        total_candidate_count=1,
    )
    extraction = PDFExtractionResult(
        source_path=pdf_path,
        pages=(ExtractedPDFPage(page_number=1, text=text),),
        page_count=1,
        metadata=PDFDocumentMetadata(
            title="Synthetic Local Trade Policy Paper",
            author_text="Release Readiness Fixture",
        ),
        extraction_method="release-fixture",
        parser_version="1.0",
    )
    sections = PDFSectionDetectionResult(
        policy_version=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.section_settings.policy_version,
        sections=(
            PDFSection(
                kind=PDFSectionKind.ABSTRACT,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Abstract",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 0, abstract_end),),
                text=ABSTRACT,
            ),
            PDFSection(
                kind=PDFSectionKind.INTRODUCTION,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Introduction",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, introduction_start, len(text)),),
                text=INTRODUCTION,
            ),
        ),
        candidates=(),
        warnings=(),
    )
    question = "How does local trade policy affect economic outcomes?"
    research_question = ResearchQuestionResult(
        policy_version=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.research_question_settings.policy_version,
        question_text=question,
        kind=ResearchQuestionKind.INFERRED,
        sections_used=(PDFSectionKind.ABSTRACT,),
        evidence=(
            ResearchQuestionEvidence(
                section_kind=PDFSectionKind.ABSTRACT,
                excerpt_text=ABSTRACT,
                page_number=1,
                start_character_offset=0,
                end_character_offset=abstract_end,
            ),
        ),
        warnings=(),
    )
    inspection = FileInspectionResult(pdf_path, file_size, checksum)
    return inspection, SinglePaperAnalysisResult(
        policy_version=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.policy_version,
        source_path=pdf_path,
        checksum=checksum,
        status=SinglePaperAnalysisStatus.SUCCESS,
        completed_stages=tuple(SinglePaperAnalysisStage),
        failed_stage=None,
        skipped_stages=(),
        failure_code=None,
        preflight_result=preflight,
        extraction_result=extraction,
        quality_assessment=_quality(text),
        section_result=sections,
        research_question_result=research_question,
        warnings=(),
        error_message=None,
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_release_library.py PDF_PATH")
    pdf_path = Path(sys.argv[1]).resolve(strict=True)
    inspection, result = _analysis_result(pdf_path)
    prepared = prepare_analysis_library(
        inspection,
        result,
        DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        DEFAULT_PDF_CONVERSION_SETTINGS,
        timestamp=TIMESTAMP,
    )
    if prepared.library_record is None:
        raise RuntimeError("Release fixture did not produce a library record.")

    storage = SQLiteStorage(get_default_db_path())
    storage.initialize()
    try:
        storage.save_analysis_and_early_section(
            prepared.analysis_record, prepared.library_record
        )
    finally:
        storage.close()
    print(f"Seeded release library: {get_default_db_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
