"""Validation tests for immutable single-paper analysis domain contracts."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
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
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
    PreflightCandidate,
    ResearchQuestionEvidence,
    ResearchQuestionKind,
    ResearchQuestionResult,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisValidationError,
    SinglePaperAnalysisWarning,
    SinglePaperAnalysisWarningCode,
)


def _make_preflight(path: Path) -> IngestionPreflightResult:
    resolved = path.resolve()
    cand = PreflightCandidate(
        source_path=resolved,
        file_size_bytes=1000,
        content_checksum="a" * 64,
        is_stored=False,
        is_batch_duplicate=False,
    )
    return IngestionPreflightResult(
        target_path=resolved,
        candidates=(cand,),
        new_candidate_count=1,
        stored_candidate_count=0,
        batch_duplicate_count=0,
        total_candidate_count=1,
    )


def _make_extraction(path: Path) -> PDFExtractionResult:
    meta = PDFDocumentMetadata(title="Test Paper")
    page = ExtractedPDFPage(page_number=1, text="Abstract\nWe study tariffs.")
    return PDFExtractionResult(
        source_path=path.resolve(),
        pages=(page,),
        page_count=1,
        metadata=meta,
        extraction_method="test",
        parser_version="1.0.0",
    )


def _make_quality(
    status: PDFQualityStatus = PDFQualityStatus.USABLE,
) -> PDFExtractionQualityAssessment:
    obs = PDFPageQualityObservation(
        page_number=1,
        character_count=23,
        printable_character_count=23,
        non_whitespace_character_count=18,
        control_character_count=0,
        replacement_character_count=0,
        repeated_character_count=0,
        is_empty=False,
        is_sparse=False,
    )
    meas = PDFQualityMeasurements(
        page_count=1,
        total_character_count=23,
        printable_character_count=23,
        non_whitespace_character_count=18,
        empty_page_count=0,
        sparse_page_count=0,
        control_character_count=0,
        replacement_character_count=0,
        repeated_character_count=0,
        minimum_page_non_whitespace_character_count=18,
        maximum_page_non_whitespace_character_count=18,
    )
    return PDFExtractionQualityAssessment(
        policy_version="pdf-quality-assessment-v1",
        status=status,
        measurements=meas,
        pages=(obs,),
        warnings=(),
    )


def test_single_paper_analysis_settings_validation() -> None:
    assert (
        DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.policy_version
        == "single-paper-analysis-v1"
    )

    with pytest.raises(
        SinglePaperAnalysisValidationError, match="not a recognized policy version"
    ):
        SinglePaperAnalysisSettings(policy_version="unknown-v99")


def test_single_paper_analysis_warning_validation() -> None:
    w1 = SinglePaperAnalysisWarning(code=SinglePaperAnalysisWarningCode.QUALITY_HALTED)
    assert w1.code is SinglePaperAnalysisWarningCode.QUALITY_HALTED
    assert "skipped" in w1.message

    w2 = SinglePaperAnalysisWarning(
        code=SinglePaperAnalysisWarningCode.QUALITY_HALTED,
        details="Extremely low character count.",
    )
    assert "Details: Extremely low character count." in w2.message

    with pytest.raises(SinglePaperAnalysisValidationError, match="details"):
        SinglePaperAnalysisWarning(
            code=SinglePaperAnalysisWarningCode.QUALITY_HALTED,
            details="   ",
        )


def test_single_paper_analysis_result_success_validation(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    preflight = _make_preflight(pdf_path)
    extraction = _make_extraction(pdf_path)
    quality = _make_quality()

    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=26
    )
    sec = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text="Abstract\nWe study tariffs.",
    )
    sec_res = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(sec,),
        candidates=(),
        warnings=(PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),),
    )
    ev = ResearchQuestionEvidence(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text="We study tariffs.",
        page_number=1,
        start_character_offset=9,
        end_character_offset=26,
    )
    rq_res = ResearchQuestionResult(
        policy_version="research-question-extraction-v1",
        question_text="What is the impact of tariffs?",
        kind=ResearchQuestionKind.EXPLICIT,
        sections_used=(PDFSectionKind.ABSTRACT,),
        evidence=(ev,),
        warnings=(),
    )

    res = SinglePaperAnalysisResult(
        policy_version="single-paper-analysis-v1",
        source_path=pdf_path,
        checksum="a" * 64,
        status=SinglePaperAnalysisStatus.SUCCESS,
        completed_stages=tuple(SinglePaperAnalysisStage),
        skipped_stages=(),
        preflight_result=preflight,
        extraction_result=extraction,
        quality_assessment=quality,
        section_result=sec_res,
        research_question_result=rq_res,
        warnings=(),
        error_message=None,
    )

    assert res.status is SinglePaperAnalysisStatus.SUCCESS
    assert res.completed_stages == tuple(SinglePaperAnalysisStage)
    assert res.skipped_stages == ()


def test_invalid_stage_combinations_rejected(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    preflight = _make_preflight(pdf_path)

    with pytest.raises(
        SinglePaperAnalysisValidationError, match="must equal canonical stage sequence"
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum="a" * 64,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(SinglePaperAnalysisStage.PREFLIGHT,),
            skipped_stages=(),  # Invalid: missing skipped stages!
            preflight_result=preflight,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message="Error",
        )
