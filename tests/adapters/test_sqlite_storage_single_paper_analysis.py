"""SQLite storage adapter unit and integration tests for single-paper analysis persistence."""

import sqlite3
from pathlib import Path

import pytest

from econ_paper_cli.adapters.sqlite_storage import (
    CURRENT_SCHEMA_VERSION,
    SQLiteStorage,
)
from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    PDFQualityStatus,
    PDFQualityWarning,
    PDFQualityWarningCode,
    PDFSectionDetectionMethod,
    PDFSectionKind,
    PDFSectionWarning,
    PDFSectionWarningCode,
    ResearchQuestionKind,
    ResearchQuestionWarning,
    ResearchQuestionWarningCode,
    SinglePaperAnalysisEvidenceRecord,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSectionRecord,
    SinglePaperAnalysisSectionSpanRecord,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    compute_analysis_id,
    compute_settings_fingerprint,
)

CHECKSUM_1 = "a" * 64
CHECKSUM_2 = "b" * 64


def _make_success_record(
    pdf_path: Path,
    checksum: str = CHECKSUM_1,
    settings: SinglePaperAnalysisSettings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
) -> SinglePaperAnalysisRecord:
    canon_path = pdf_path.resolve()

    span_abs = SinglePaperAnalysisSectionSpanRecord(
        page_number=1,
        start_character_offset=0,
        end_character_offset=30,
        ordinal_position=0,
    )
    sec_abs = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="Abstract",
        page_start=1,
        page_end=1,
        spans=(span_abs,),
        ordinal_position=0,
    )

    span_intro_p1 = SinglePaperAnalysisSectionSpanRecord(
        page_number=1,
        start_character_offset=32,
        end_character_offset=100,
        ordinal_position=0,
    )
    span_intro_p2 = SinglePaperAnalysisSectionSpanRecord(
        page_number=2,
        start_character_offset=0,
        end_character_offset=250,
        ordinal_position=1,
    )
    sec_intro = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.INTRODUCTION,
        heading_text="Introduction",
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text="1. Introduction",
        page_start=1,
        page_end=2,
        spans=(span_intro_p1, span_intro_p2),
        ordinal_position=1,
    )

    ev_abs = SinglePaperAnalysisEvidenceRecord(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text="Abstract excerpt for question",
        page_number=1,
        start_character_offset=0,
        end_character_offset=29,
        ordinal_position=0,
    )
    rq = SinglePaperAnalysisQuestionRecord(
        kind=ResearchQuestionKind.EXPLICIT,
        question_text="What is the effect of trade policy?",
        sections_used=(PDFSectionKind.ABSTRACT,),
    )
    return SinglePaperAnalysisRecord(
        analysis_id=compute_analysis_id(checksum, settings, canon_path),
        source_path=canon_path,
        content_checksum=checksum,
        status=SinglePaperAnalysisStatus.SUCCESS,
        completed_stages=tuple(SinglePaperAnalysisStage),
        failed_stage=None,
        skipped_stages=(),
        failure_code=None,
        error_message=None,
        quality_status=PDFQualityStatus.USABLE,
        settings=settings,
        settings_fingerprint=compute_settings_fingerprint(settings),
        quality_warnings=(
            PDFQualityWarning(
                code=PDFQualityWarningCode.SPARSE_PAGES, page_numbers=(2,)
            ),
        ),
        section_warnings=(
            PDFSectionWarning(
                code=PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY,
                page_numbers=(1, 2),
            ),
        ),
        research_question_warnings=(
            ResearchQuestionWarning(
                code=ResearchQuestionWarningCode.MISSING_SECTION,
                details="Only Abstract section was available.",
            ),
        ),
        warnings=(),
        sections=(sec_abs, sec_intro),
        research_question=rq,
        evidence=(ev_abs,),
        created_at="2026-08-01T20:00:00Z",
        updated_at="2026-08-01T20:00:00Z",
    )


def test_schema_migration_v2_populated_to_v3(tmp_path: Path) -> None:
    """Migration 3 creates single_paper_analysis tables and preserves populated v2 data."""
    db_file = tmp_path / "v2_populated.db"
    conn = sqlite3.connect(str(db_file))

    # Create exact schema version 2
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            year INTEGER,
            abstract TEXT,
            source_name TEXT NOT NULL,
            source_identifier TEXT NOT NULL,
            source_url TEXT,
            content_checksum TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_papers_checksum ON papers(content_checksum);
        CREATE TABLE source_provenance (
            paper_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            source_format TEXT NOT NULL,
            source_file_size INTEGER NOT NULL,
            content_checksum TEXT NOT NULL COLLATE NOCASE,
            markdown_path TEXT NOT NULL,
            extraction_method TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
        );
        CREATE TABLE conversion_settings (
            paper_id TEXT PRIMARY KEY,
            conversion_version TEXT NOT NULL,
            ocr_enabled INTEGER NOT NULL,
            parameters_json TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
        );
        CREATE TABLE passages (
            passage_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            text TEXT NOT NULL,
            section_heading TEXT,
            page_start INTEGER,
            page_end INTEGER,
            ordinal_position INTEGER NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE,
            CONSTRAINT uq_paper_ordinal UNIQUE(paper_id, ordinal_position)
        );
        CREATE TABLE ingestion_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            warning_code TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
        );
        CREATE TABLE ingestion_completions (
            paper_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            passage_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            error_message TEXT,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
        );
        """
    )
    ck = "a" * 64
    conn.execute(
        "INSERT INTO schema_migrations VALUES (2, '2026-07-31T20:00:00Z', 'Update paper content_checksum collation');"
    )
    conn.execute(
        "INSERT INTO papers VALUES ('paper.v2', 'V2 Paper Title', '[\"Author\"]', 2024, 'Abstract', 'NBER', '123', NULL, ?, '2026-07-31T20:00:00Z', '2026-07-31T20:00:00Z');",
        (ck,),
    )
    conn.execute(
        "INSERT INTO source_provenance VALUES ('paper.v2', '/path/paper.pdf', 'pdf', 1024, ?, '/path/paper.md', 'pdfplumber', '2026-07-31T20:00:00Z');",
        (ck,),
    )
    conn.execute(
        "INSERT INTO conversion_settings VALUES ('paper.v2', '1.0.0', 0, '{}');"
    )
    conn.execute(
        "INSERT INTO ingestion_completions VALUES ('paper.v2', 'completed', '2026-07-31T20:00:00Z', 0, 0, NULL);"
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db_file)
    storage.initialize()

    assert storage.get_schema_version() == CURRENT_SCHEMA_VERSION

    # Verify existing V2 paper survived
    paper = storage.get_paper_record("paper.v2")
    assert paper is not None
    assert paper.paper.title == "V2 Paper Title"
    storage.close()


def test_save_and_get_multi_page_section_and_typed_warnings(tmp_path: Path) -> None:
    pdf_path = (tmp_path / "paper.pdf").resolve()
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    record = _make_success_record(pdf_path)
    storage.save_single_paper_analysis(record)

    retrieved = storage.get_single_paper_analysis(record.analysis_id)
    assert retrieved is not None
    assert retrieved == record

    # Verify multi-page section span round-trip
    assert len(retrieved.sections) == 2
    intro = retrieved.sections[1]
    assert intro.observed_heading_text == "1. Introduction"
    assert intro.heading_text == "Introduction"
    assert len(intro.spans) == 2
    assert intro.spans[0].page_number == 1
    assert intro.spans[0].start_character_offset == 32
    assert intro.spans[0].end_character_offset == 100
    assert intro.spans[1].page_number == 2
    assert intro.spans[1].start_character_offset == 0
    assert intro.spans[1].end_character_offset == 250

    # Verify typed stage warnings round-trip
    assert len(retrieved.quality_warnings) == 1
    assert retrieved.quality_warnings[0].code is PDFQualityWarningCode.SPARSE_PAGES
    assert retrieved.quality_warnings[0].page_numbers == (2,)

    assert len(retrieved.section_warnings) == 1
    assert (
        retrieved.section_warnings[0].code
        is PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY
    )
    assert retrieved.section_warnings[0].page_numbers == (1, 2)

    assert len(retrieved.research_question_warnings) == 1
    assert (
        retrieved.research_question_warnings[0].code
        is ResearchQuestionWarningCode.MISSING_SECTION
    )
    assert (
        retrieved.research_question_warnings[0].details
        == "Only Abstract section was available."
    )

    storage.close()


def test_database_level_foreign_key_evidence_integrity(tmp_path: Path) -> None:
    """DB-level foreign key constraint prevents evidence referencing an unpersisted section."""
    db_file = tmp_path / "fk_test.db"
    storage = SQLiteStorage(db_file)
    storage.initialize()

    conn = storage._ensure_initialized()
    conn.execute("PRAGMA foreign_keys = ON;")

    # Insert parent analysis
    conn.execute(
        """INSERT INTO single_paper_analyses (
            analysis_id, content_checksum, source_path, policy_version,
            status, failed_stage, failure_code, error_message,
            completed_stages_json, skipped_stages_json, quality_status,
            quality_settings_json, section_settings_json, research_question_settings_json,
            settings_fingerprint, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "anal_1",
            "a" * 64,
            "/path/paper.pdf",
            "v1",
            "success",
            None,
            None,
            None,
            "[]",
            "[]",
            "usable",
            "{}",
            "{}",
            "{}",
            "fp1",
            "2026-08-01T20:00:00Z",
            "2026-08-01T20:00:00Z",
        ),
    )

    # Attempt to insert evidence referencing section_kind 'abstract' when no section exists!
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO single_paper_analysis_evidence (
                analysis_id, section_kind, excerpt_text, page_number,
                start_character_offset, end_character_offset, ordinal_position
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("anal_1", "abstract", "Excerpt", 1, 0, 7, 0),
        )

    storage.close()


def test_cascade_delete_analysis_record(tmp_path: Path) -> None:
    pdf_path = (tmp_path / "paper.pdf").resolve()
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    record = _make_success_record(pdf_path)
    storage.save_single_paper_analysis(record)

    deleted = storage.delete_single_paper_analysis(record.analysis_id)
    assert deleted is True

    assert storage.get_single_paper_analysis(record.analysis_id) is None

    conn = storage._ensure_initialized()
    for tbl in (
        "single_paper_analysis_warnings",
        "single_paper_analysis_sections",
        "single_paper_analysis_section_spans",
        "single_paper_analysis_questions",
        "single_paper_analysis_evidence",
    ):
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE analysis_id = ?",
            (record.analysis_id,),
        )
        assert cur.fetchone()[0] == 0

    storage.close()


def test_implicit_section_roundtrip_and_restart(tmp_path: Path) -> None:
    """An implicit section survives SinglePaperAnalysisRecord.from_result, SQLite save/read, and restart without fabricating an observed heading."""
    from econ_paper_cli.domain import (
        ExtractedPDFPage,
        IngestionPreflightResult,
        PDFDocumentMetadata,
        PDFExtractionQualityAssessment,
        PDFExtractionResult,
        PDFPageQualityObservation,
        PDFQualityMeasurements,
        PDFSection,
        PDFSectionBoundaryEvidence,
        PDFSectionDetectionResult,
        PDFSectionSpan,
        PreflightCandidate,
        ResearchQuestionEvidence,
        ResearchQuestionResult,
        SinglePaperAnalysisResult,
    )

    pdf_path = (tmp_path / "implicit_paper.pdf").resolve()

    preflight = IngestionPreflightResult(
        target_path=pdf_path,
        candidates=(
            PreflightCandidate(
                source_path=pdf_path,
                file_size_bytes=500,
                content_checksum="c" * 64,
                is_stored=False,
                is_batch_duplicate=False,
            ),
        ),
        new_candidate_count=1,
        stored_candidate_count=0,
        batch_duplicate_count=0,
        total_candidate_count=1,
    )
    extraction = PDFExtractionResult(
        source_path=pdf_path,
        pages=(
            ExtractedPDFPage(page_number=1, text="Unheaded front matter text here..."),
        ),
        page_count=1,
        metadata=PDFDocumentMetadata(title="Implicit Paper"),
        extraction_method="test",
        parser_version="1.0.0",
    )
    quality = PDFExtractionQualityAssessment(
        policy_version="pdf-extraction-quality-v1",
        status=PDFQualityStatus.USABLE,
        measurements=PDFQualityMeasurements(
            page_count=1,
            total_character_count=34,
            printable_character_count=34,
            non_whitespace_character_count=30,
            empty_page_count=0,
            sparse_page_count=0,
            control_character_count=0,
            replacement_character_count=0,
            repeated_character_count=0,
            minimum_page_non_whitespace_character_count=30,
            maximum_page_non_whitespace_character_count=30,
        ),
        pages=(
            PDFPageQualityObservation(
                page_number=1,
                character_count=34,
                printable_character_count=34,
                non_whitespace_character_count=30,
                control_character_count=0,
                replacement_character_count=0,
                repeated_character_count=0,
                is_empty=False,
                is_sparse=False,
            ),
        ),
        warnings=(),
    )

    span = PDFSectionSpan(
        page_number=1, start_character_offset=0, end_character_offset=34
    )
    b_ev = PDFSectionBoundaryEvidence(
        page_number=1,
        start_character_offset=0,
        end_character_offset=34,
        evidence_type="title_block",
        description="Implicit front-matter inferred from title block boundaries",
    )

    implicit_section = PDFSection(
        kind=PDFSectionKind.ABSTRACT,
        detection_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        observed_heading_text=None,
        start_page_number=1,
        end_page_number=1,
        spans=(span,),
        text="Unheaded front matter text here...",
        boundary_evidence=(b_ev,),
    )

    section_res = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(implicit_section,),
        candidates=(),
        warnings=(PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),),
    )

    rq_ev = ResearchQuestionEvidence(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text="Unheaded front matter text here...",
        page_number=1,
        start_character_offset=0,
        end_character_offset=34,
    )
    rq_res = ResearchQuestionResult(
        policy_version="research-question-extraction-v1",
        question_text="What is the implicit question?",
        kind=ResearchQuestionKind.INFERRED,
        sections_used=(PDFSectionKind.ABSTRACT,),
        evidence=(rq_ev,),
        warnings=(),
    )

    analysis_result = SinglePaperAnalysisResult(
        policy_version="single-paper-analysis-v1",
        source_path=pdf_path,
        checksum="c" * 64,
        status=SinglePaperAnalysisStatus.SUCCESS,
        completed_stages=tuple(SinglePaperAnalysisStage),
        failed_stage=None,
        skipped_stages=(),
        failure_code=None,
        preflight_result=preflight,
        extraction_result=extraction,
        quality_assessment=quality,
        section_result=section_res,
        research_question_result=rq_res,
        warnings=(),
        error_message=None,
    )

    # 1. Verify SinglePaperAnalysisRecord.from_result converts implicit section correctly
    record = SinglePaperAnalysisRecord.from_result(analysis_result)
    assert len(record.sections) == 1
    sec_rec = record.sections[0]
    assert sec_rec.section_kind is PDFSectionKind.ABSTRACT
    assert sec_rec.heading_text == "Abstract"
    assert sec_rec.detection_method is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    assert sec_rec.observed_heading_text is None
    assert len(sec_rec.boundary_evidence) == 1
    assert sec_rec.boundary_evidence[0].evidence_type == "title_block"

    # 2. Save to SQLite database file and close
    db_file = tmp_path / "implicit_restart.db"
    storage1 = SQLiteStorage(db_file)
    storage1.initialize()
    storage1.save_single_paper_analysis(record)
    storage1.close()

    # 3. Reopen database (simulating restart) and fetch record
    storage2 = SQLiteStorage(db_file)
    storage2.initialize()
    restarted_record = storage2.get_single_paper_analysis(record.analysis_id)
    assert restarted_record is not None
    assert len(restarted_record.sections) == 1

    r_sec = restarted_record.sections[0]
    assert r_sec.section_kind is PDFSectionKind.ABSTRACT
    assert r_sec.heading_text == "Abstract"
    assert r_sec.detection_method is PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER
    assert r_sec.observed_heading_text is None
    assert len(r_sec.boundary_evidence) == 1
    assert r_sec.boundary_evidence[0].page_number == 1
    assert r_sec.boundary_evidence[0].start_character_offset == 0
    assert r_sec.boundary_evidence[0].end_character_offset == 34
    assert r_sec.boundary_evidence[0].evidence_type == "title_block"
    assert (
        r_sec.boundary_evidence[0].description
        == "Implicit front-matter inferred from title block boundaries"
    )
    storage2.close()
