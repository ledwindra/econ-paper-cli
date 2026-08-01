"""Domain validation tests for single-paper analysis storage records and helpers."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    PDFQualityStatus,
    PDFSectionKind,
    ResearchQuestionKind,
    SinglePaperAnalysisEvidenceRecord,
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSectionRecord,
    SinglePaperAnalysisSectionSpanRecord,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisValidationError,
    compute_analysis_id,
    compute_settings_fingerprint,
)


def test_compute_settings_fingerprint_deterministic() -> None:
    settings1 = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    settings2 = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    fp1 = compute_settings_fingerprint(settings1)
    fp2 = compute_settings_fingerprint(settings2)

    assert len(fp1) == 64
    assert fp1 == fp2


def test_compute_analysis_id_deterministic(tmp_path: Path) -> None:
    pdf_path = (tmp_path / "paper.pdf").resolve()
    chk = "a" * 64
    settings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS

    id1 = compute_analysis_id(chk, settings, pdf_path)
    id2 = compute_analysis_id(chk, settings, pdf_path)

    assert len(id1) == 64
    assert id1 == id2

    # Changed checksum yields different analysis_id
    id3 = compute_analysis_id("b" * 64, settings, pdf_path)
    assert id3 != id1


def test_section_span_and_record_validation() -> None:
    with pytest.raises(
        SinglePaperAnalysisValidationError, match="start_character_offset cannot exceed"
    ):
        SinglePaperAnalysisSectionSpanRecord(
            page_number=1,
            start_character_offset=10,
            end_character_offset=5,
            ordinal_position=0,
        )

    span1 = SinglePaperAnalysisSectionSpanRecord(
        page_number=1,
        start_character_offset=0,
        end_character_offset=100,
        ordinal_position=0,
    )
    span2 = SinglePaperAnalysisSectionSpanRecord(
        page_number=2,
        start_character_offset=0,
        end_character_offset=200,
        ordinal_position=1,
    )

    sec = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.INTRODUCTION,
        heading_text="1. Introduction",
        page_start=1,
        page_end=2,
        spans=(span1, span2),
        ordinal_position=0,
    )
    assert sec.page_start == 1
    assert sec.page_end == 2
    assert len(sec.spans) == 2


def test_evidence_record_validation() -> None:
    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="excerpt_text length .* does not match",
    ):
        SinglePaperAnalysisEvidenceRecord(
            section_kind=PDFSectionKind.ABSTRACT,
            excerpt_text="Short text",  # length 10
            page_number=1,
            start_character_offset=0,
            end_character_offset=20,  # Expected span 20
            ordinal_position=0,
        )


def test_question_record_validation() -> None:
    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="question_text must be None when kind is UNAVAILABLE",
    ):
        SinglePaperAnalysisQuestionRecord(
            kind=ResearchQuestionKind.UNAVAILABLE,
            question_text="Should be None",
            sections_used=(),
        )


def test_record_referential_integrity_and_span_validation(tmp_path: Path) -> None:
    pdf_path = (tmp_path / "paper.pdf").resolve()
    chk = "a" * 64
    settings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS

    span1 = SinglePaperAnalysisSectionSpanRecord(
        page_number=1,
        start_character_offset=0,
        end_character_offset=30,
        ordinal_position=0,
    )
    sec = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_start=1,
        page_end=1,
        spans=(span1,),
        ordinal_position=0,
    )

    # Evidence excerpt falls outside page span [0, 30] -> offset 35 to 55
    ev_out_of_bounds = SinglePaperAnalysisEvidenceRecord(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text="Excerpt out of bounds!",  # length 22
        page_number=1,
        start_character_offset=35,
        end_character_offset=57,
        ordinal_position=0,
    )
    rq = SinglePaperAnalysisQuestionRecord(
        kind=ResearchQuestionKind.EXPLICIT,
        question_text="What is the impact?",
        sections_used=(PDFSectionKind.ABSTRACT,),
    )

    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="does not fall within any section span",
    ):
        SinglePaperAnalysisRecord(
            analysis_id=compute_analysis_id(chk, settings, pdf_path),
            source_path=pdf_path,
            content_checksum=chk,
            status=SinglePaperAnalysisStatus.SUCCESS,
            completed_stages=tuple(SinglePaperAnalysisStage),
            failed_stage=None,
            skipped_stages=(),
            failure_code=None,
            error_message=None,
            quality_status=PDFQualityStatus.USABLE,
            settings=settings,
            settings_fingerprint=compute_settings_fingerprint(settings),
            quality_warnings=(),
            section_warnings=(),
            research_question_warnings=(),
            warnings=(),
            sections=(sec,),
            research_question=rq,
            evidence=(ev_out_of_bounds,),
            created_at="2026-08-01T20:00:00Z",
            updated_at="2026-08-01T20:00:00Z",
        )


def test_record_non_canonical_source_path_rejected(tmp_path: Path) -> None:
    pdf_path = Path("relative/path/paper.pdf")
    chk = "a" * 64
    settings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS

    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="source_path must be canonical/absolute",
    ):
        SinglePaperAnalysisRecord(
            analysis_id="arbitrary_id",
            source_path=pdf_path,
            content_checksum=chk,
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
            error_message="Path not found",
            quality_status=None,
            settings=settings,
            settings_fingerprint=compute_settings_fingerprint(settings),
            quality_warnings=(),
            section_warnings=(),
            research_question_warnings=(),
            warnings=(),
            sections=(),
            research_question=None,
            evidence=(),
            created_at="2026-08-01T20:00:00Z",
            updated_at="2026-08-01T20:00:00Z",
        )
