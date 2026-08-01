"""Domain validation tests for single-paper analysis storage records and helpers."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    PDFQualityStatus,
    PDFSectionKind,
    ResearchQuestionKind,
    SinglePaperAnalysisEvidenceRecord,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSectionRecord,
    SinglePaperAnalysisSettings,
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

    # Changed setting policy_version yields a different fingerprint
    custom_settings = SinglePaperAnalysisSettings(
        policy_version="single-paper-analysis-v2"
    )
    custom_fp = compute_settings_fingerprint(custom_settings)
    assert custom_fp != fp1


def test_compute_analysis_id_deterministic() -> None:
    chk = "a" * 64
    settings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS

    id1 = compute_analysis_id(chk, settings)
    id2 = compute_analysis_id(chk, settings)

    assert len(id1) == 64
    assert id1 == id2

    # Changed checksum yields different analysis_id
    id3 = compute_analysis_id("b" * 64, settings)
    assert id3 != id1

    # Changed settings yields different analysis_id
    custom_settings = SinglePaperAnalysisSettings(
        policy_version="single-paper-analysis-v2"
    )
    id4 = compute_analysis_id(chk, custom_settings)
    assert id4 != id1


def test_section_record_validation() -> None:
    with pytest.raises(
        SinglePaperAnalysisValidationError, match="start_character_offset cannot exceed"
    ):
        SinglePaperAnalysisSectionRecord(
            section_kind=PDFSectionKind.ABSTRACT,
            heading_text="Abstract",
            page_start=1,
            page_end=1,
            start_character_offset=10,
            end_character_offset=5,  # Invalid
            ordinal_position=0,
        )


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


def test_record_referential_integrity_mismatch_rejected(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    chk = "a" * 64
    settings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS

    sec = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_start=1,
        page_end=1,
        start_character_offset=0,
        end_character_offset=30,
        ordinal_position=0,
    )
    # Evidence refers to INTRODUCTION, but sections contains only ABSTRACT!
    ev = SinglePaperAnalysisEvidenceRecord(
        section_kind=PDFSectionKind.INTRODUCTION,
        excerpt_text="Introduction text here!!",
        page_number=1,
        start_character_offset=0,
        end_character_offset=24,
        ordinal_position=0,
    )
    rq = SinglePaperAnalysisQuestionRecord(
        kind=ResearchQuestionKind.EXPLICIT,
        question_text="What is the impact?",
        sections_used=(PDFSectionKind.INTRODUCTION,),
    )

    with pytest.raises(
        SinglePaperAnalysisValidationError,
        match="does not match any detected section",
    ):
        SinglePaperAnalysisRecord(
            analysis_id=compute_analysis_id(chk, settings),
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
            warnings=(),
            sections=(sec,),
            research_question=rq,
            evidence=(ev,),
            created_at="2026-08-01T20:00:00Z",
            updated_at="2026-08-01T20:00:00Z",
        )
