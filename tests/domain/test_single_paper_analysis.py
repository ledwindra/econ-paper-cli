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
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisValidationError,
    SinglePaperAnalysisWarning,
    SinglePaperAnalysisWarningCode,
)
from econ_paper_cli.domain.errors import (
    IngestionPathNotFoundError,
    PDFMalformedError,
    PDFReadError,
    PDFSourceNotFoundError,
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
        failed_stage=None,
        skipped_stages=(),
        failure_code=None,
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
    assert res.failed_stage is None
    assert res.failure_code is None
    assert res.skipped_stages == ()


def test_preflight_failed_result_uses_failed_stage_and_failure_code(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "paper.pdf"

    res = SinglePaperAnalysisResult(
        policy_version="single-paper-analysis-v1",
        source_path=pdf_path,
        checksum=None,
        status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
        completed_stages=(),
        failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
        skipped_stages=(
            SinglePaperAnalysisStage.EXTRACTION,
            SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            SinglePaperAnalysisStage.SECTION_DETECTION,
            SinglePaperAnalysisStage.QUESTION_EXTRACTION,
        ),
        failure_code=SinglePaperAnalysisFailureCode.PATH_NOT_FOUND,
        preflight_result=None,
        extraction_result=None,
        quality_assessment=None,
        section_result=None,
        research_question_result=None,
        warnings=(),
        error_message="File not found.",
        failure_cause=IngestionPathNotFoundError("File not found."),
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.completed_stages == ()
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert res.failure_code is SinglePaperAnalysisFailureCode.PATH_NOT_FOUND
    assert SinglePaperAnalysisStage.EXTRACTION in res.skipped_stages


def test_extraction_failed_result_uses_failed_stage_and_failure_code(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "paper.pdf"
    preflight = _make_preflight(pdf_path)

    cause = PDFMalformedError(pdf_path, ValueError("Corrupted PDF"))
    res = SinglePaperAnalysisResult(
        policy_version="single-paper-analysis-v1",
        source_path=pdf_path,
        checksum="a" * 64,
        status=SinglePaperAnalysisStatus.EXTRACTION_FAILED,
        completed_stages=(SinglePaperAnalysisStage.PREFLIGHT,),
        failed_stage=SinglePaperAnalysisStage.EXTRACTION,
        skipped_stages=(
            SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            SinglePaperAnalysisStage.SECTION_DETECTION,
            SinglePaperAnalysisStage.QUESTION_EXTRACTION,
        ),
        failure_code=SinglePaperAnalysisFailureCode.PDF_MALFORMED,
        preflight_result=preflight,
        extraction_result=None,
        quality_assessment=None,
        section_result=None,
        research_question_result=None,
        warnings=(),
        error_message=str(cause),
        failure_cause=cause,
    )

    assert res.completed_stages == (SinglePaperAnalysisStage.PREFLIGHT,)
    assert res.failed_stage is SinglePaperAnalysisStage.EXTRACTION
    assert res.failure_code is SinglePaperAnalysisFailureCode.PDF_MALFORMED
    assert SinglePaperAnalysisStage.QUALITY_ASSESSMENT in res.skipped_stages


def test_wrong_failure_code_for_status_rejected(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"

    # PDF extraction code is not valid for PREFLIGHT_FAILED
    with pytest.raises(SinglePaperAnalysisValidationError, match="failure_code"):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=(
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            failure_code=SinglePaperAnalysisFailureCode.PDF_MALFORMED,  # Wrong!
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message="Error",
            failure_cause=IngestionPathNotFoundError("Error"),
        )


def test_failure_code_required_for_failure_statuses(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"

    with pytest.raises(
        SinglePaperAnalysisValidationError, match="failure_code is required"
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=(
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            failure_code=None,  # Missing!
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message="Error",
        )


def test_failure_code_forbidden_for_success(tmp_path: Path) -> None:
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

    with pytest.raises(
        SinglePaperAnalysisValidationError, match="failure_code must be None"
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum="a" * 64,
            status=SinglePaperAnalysisStatus.SUCCESS,
            completed_stages=tuple(SinglePaperAnalysisStage),
            failed_stage=None,
            skipped_stages=(),
            failure_code=SinglePaperAnalysisFailureCode.PDF_ENCRYPTED,  # Forbidden!
            preflight_result=preflight,
            extraction_result=extraction,
            quality_assessment=quality,
            section_result=sec_res,
            research_question_result=rq_res,
            warnings=(),
            error_message=None,
        )


def test_invalid_stage_sequence_rejected(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"

    with pytest.raises(
        SinglePaperAnalysisValidationError, match="canonical stage sequence"
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=(),  # Missing 4 stages!
            failure_code=SinglePaperAnalysisFailureCode.PATH_NOT_FOUND,
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message="Error",
            failure_cause=IngestionPathNotFoundError("Error"),
        )


# ---------------------------------------------------------------------------
# failure_cause type mismatch regressions (Blocker 1)
# ---------------------------------------------------------------------------


def _skipped_after_preflight() -> tuple:
    return (
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )


def test_path_not_found_code_with_pdf_malformed_cause_rejected(
    tmp_path: Path,
) -> None:
    """PATH_NOT_FOUND paired with PDFMalformedError must be rejected."""
    pdf_path = tmp_path / "paper.pdf"
    wrong_cause = PDFMalformedError(pdf_path, ValueError("truncated"))

    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="failure_cause for code path_not_found",
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=_skipped_after_preflight(),
            failure_code=SinglePaperAnalysisFailureCode.PATH_NOT_FOUND,
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=str(wrong_cause),
            failure_cause=wrong_cause,
        )


def test_pdf_encrypted_code_with_pdf_read_error_cause_rejected(
    tmp_path: Path,
) -> None:
    """PDF_ENCRYPTED paired with PDFReadError must be rejected."""
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    wrong_cause = PDFReadError(pdf_path, OSError("read failed"))

    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="failure_cause for code pdf_encrypted",
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum="a" * 64,
            status=SinglePaperAnalysisStatus.EXTRACTION_FAILED,
            completed_stages=(SinglePaperAnalysisStage.PREFLIGHT,),
            failed_stage=SinglePaperAnalysisStage.EXTRACTION,
            skipped_stages=(
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            failure_code=SinglePaperAnalysisFailureCode.PDF_ENCRYPTED,
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=str(wrong_cause),
            failure_cause=wrong_cause,
        )


def test_directory_input_code_with_non_empty_directory_error_rejected(
    tmp_path: Path,
) -> None:
    """DIRECTORY_INPUT failure_cause must be IngestionEmptyDirectoryError or None."""
    from econ_paper_cli.domain.errors import IngestionError

    pdf_path = tmp_path / "paper.pdf"
    # Use a plain IngestionError (not IngestionEmptyDirectoryError) as the cause
    wrong_cause = IngestionError("some other ingestion error")

    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="DIRECTORY_INPUT failure_cause must be IngestionEmptyDirectoryError",
    ):
        SinglePaperAnalysisResult(
            policy_version="single-paper-analysis-v1",
            source_path=pdf_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=_skipped_after_preflight(),
            failure_code=SinglePaperAnalysisFailureCode.DIRECTORY_INPUT,
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=str(wrong_cause),
            failure_cause=wrong_cause,
        )


def test_pdf_not_found_code_with_correct_cause_accepted(tmp_path: Path) -> None:
    """PDF_NOT_FOUND paired with PDFSourceNotFoundError must be accepted."""
    pdf_path = tmp_path / "paper.pdf"
    cause = PDFSourceNotFoundError(pdf_path)

    result = SinglePaperAnalysisResult(
        policy_version="single-paper-analysis-v1",
        source_path=pdf_path,
        checksum="a" * 64,
        status=SinglePaperAnalysisStatus.EXTRACTION_FAILED,
        completed_stages=(SinglePaperAnalysisStage.PREFLIGHT,),
        failed_stage=SinglePaperAnalysisStage.EXTRACTION,
        skipped_stages=(
            SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            SinglePaperAnalysisStage.SECTION_DETECTION,
            SinglePaperAnalysisStage.QUESTION_EXTRACTION,
        ),
        failure_code=SinglePaperAnalysisFailureCode.PDF_NOT_FOUND,
        preflight_result=None,
        extraction_result=None,
        quality_assessment=None,
        section_result=None,
        research_question_result=None,
        warnings=(),
        error_message=str(cause),
        failure_cause=cause,
    )
    assert result.failure_code is SinglePaperAnalysisFailureCode.PDF_NOT_FOUND
    assert isinstance(result.failure_cause, PDFSourceNotFoundError)
