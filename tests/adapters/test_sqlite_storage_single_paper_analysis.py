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
    PDFSectionKind,
    ResearchQuestionKind,
    SinglePaperAnalysisEvidenceRecord,
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSectionRecord,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisWarning,
    SinglePaperAnalysisWarningCode,
    compute_analysis_id,
    compute_settings_fingerprint,
)
from econ_paper_cli.protocols.storage import (
    StorageTransactionError,
)

CHECKSUM_1 = "a" * 64
CHECKSUM_2 = "b" * 64


def _make_success_record(
    pdf_path: Path,
    checksum: str = CHECKSUM_1,
    settings: SinglePaperAnalysisSettings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
) -> SinglePaperAnalysisRecord:
    sec_abs = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.ABSTRACT,
        heading_text="Abstract",
        page_start=1,
        page_end=1,
        start_character_offset=0,
        end_character_offset=30,
        ordinal_position=0,
    )
    sec_intro = SinglePaperAnalysisSectionRecord(
        section_kind=PDFSectionKind.INTRODUCTION,
        heading_text="1. Introduction",
        page_start=1,
        page_end=2,
        start_character_offset=32,
        end_character_offset=100,
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
        analysis_id=compute_analysis_id(checksum, settings, pdf_path),
        source_path=pdf_path,
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
        warnings=(),
        sections=(sec_abs, sec_intro),
        research_question=rq,
        evidence=(ev_abs,),
        created_at="2026-08-01T20:00:00Z",
        updated_at="2026-08-01T20:00:00Z",
    )


def test_schema_migration_v2_to_v3(tmp_path: Path) -> None:
    """Migration 3 creates single_paper_analysis tables in a v2 database."""
    db_file = tmp_path / "v2_db.db"
    storage = SQLiteStorage(db_file)
    storage.initialize()

    assert storage.get_schema_version() == CURRENT_SCHEMA_VERSION

    # Verify new tables exist in sqlite_master
    conn = sqlite3.connect(str(db_file))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'single_paper_analys%'"
    )
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    expected_tables = {
        "single_paper_analyses",
        "single_paper_analysis_warnings",
        "single_paper_analysis_sections",
        "single_paper_analysis_questions",
        "single_paper_analysis_evidence",
    }
    assert tables == expected_tables
    storage.close()


def test_save_and_get_success_analysis_record(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    record = _make_success_record(pdf_path)
    storage.save_single_paper_analysis(record)

    retrieved = storage.get_single_paper_analysis(record.analysis_id)
    assert retrieved is not None
    assert retrieved == record
    assert retrieved.status is SinglePaperAnalysisStatus.SUCCESS
    assert len(retrieved.sections) == 2
    assert retrieved.sections[0].heading_text == "Abstract"
    assert retrieved.research_question is not None
    assert (
        retrieved.research_question.question_text
        == "What is the effect of trade policy?"
    )
    assert len(retrieved.evidence) == 1
    assert retrieved.evidence[0].excerpt_text == "Abstract excerpt for question"
    storage.close()


def test_idempotent_repeated_writes(tmp_path: Path) -> None:
    """Writing the exact same analysis_id repeatedly leaves equivalent record and no duplicate rows."""
    pdf_path = tmp_path / "paper.pdf"
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    record = _make_success_record(pdf_path)
    storage.save_single_paper_analysis(record)
    storage.save_single_paper_analysis(record)
    storage.save_single_paper_analysis(record)

    retrieved = storage.get_single_paper_analysis(record.analysis_id)
    assert retrieved is not None
    assert len(retrieved.sections) == 2
    assert len(retrieved.evidence) == 1

    conn = storage._ensure_initialized()
    cur = conn.execute(
        "SELECT COUNT(*) FROM single_paper_analyses WHERE analysis_id = ?",
        (record.analysis_id,),
    )
    assert cur.fetchone()[0] == 1

    cur_sec = conn.execute(
        "SELECT COUNT(*) FROM single_paper_analysis_sections WHERE analysis_id = ?",
        (record.analysis_id,),
    )
    assert cur_sec.fetchone()[0] == 2

    storage.close()


def test_changed_checksum_or_settings_creates_distinct_records(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    rec1 = _make_success_record(pdf_path, checksum=CHECKSUM_1)
    rec2 = _make_success_record(pdf_path, checksum=CHECKSUM_2)

    custom_settings = SinglePaperAnalysisSettings(
        policy_version="single-paper-analysis-v2"
    )
    rec3 = _make_success_record(pdf_path, checksum=CHECKSUM_1, settings=custom_settings)

    assert rec1.analysis_id != rec2.analysis_id
    assert rec1.analysis_id != rec3.analysis_id

    storage.save_single_paper_analysis(rec1)
    storage.save_single_paper_analysis(rec2)
    storage.save_single_paper_analysis(rec3)

    all_recs = storage.list_single_paper_analyses()
    assert len(all_recs) == 3

    by_ck1 = storage.get_single_paper_analysis_by_checksum(CHECKSUM_1)
    assert by_ck1 is not None

    by_ck1_custom = storage.get_single_paper_analysis_by_checksum(
        CHECKSUM_1, settings_fingerprint=rec3.settings_fingerprint
    )
    assert by_ck1_custom is not None
    assert by_ck1_custom.analysis_id == rec3.analysis_id

    storage.close()


def test_save_and_get_failed_and_halted_outcomes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    settings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS

    # 1. PREFLIGHT_FAILED record
    preflight_rec = SinglePaperAnalysisRecord(
        analysis_id=compute_analysis_id(None, settings, pdf_path),
        source_path=pdf_path,
        content_checksum=None,
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
        error_message=f"File not found: {pdf_path}",
        quality_status=None,
        settings=settings,
        settings_fingerprint=compute_settings_fingerprint(settings),
        warnings=(),
        sections=(),
        research_question=None,
        evidence=(),
        created_at="2026-08-01T20:00:00Z",
        updated_at="2026-08-01T20:00:00Z",
    )
    storage.save_single_paper_analysis(preflight_rec)
    ret_pf = storage.get_single_paper_analysis(preflight_rec.analysis_id)
    assert ret_pf == preflight_rec

    # 2. QUALITY_HALTED record
    quality_rec = SinglePaperAnalysisRecord(
        analysis_id=compute_analysis_id(CHECKSUM_1, settings, pdf_path),
        source_path=pdf_path,
        content_checksum=CHECKSUM_1,
        status=SinglePaperAnalysisStatus.QUALITY_HALTED,
        completed_stages=(
            SinglePaperAnalysisStage.PREFLIGHT,
            SinglePaperAnalysisStage.EXTRACTION,
            SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        ),
        failed_stage=None,
        skipped_stages=(
            SinglePaperAnalysisStage.SECTION_DETECTION,
            SinglePaperAnalysisStage.QUESTION_EXTRACTION,
        ),
        failure_code=None,
        error_message=None,
        quality_status=PDFQualityStatus.UNUSABLE,
        settings=settings,
        settings_fingerprint=compute_settings_fingerprint(settings),
        warnings=(
            SinglePaperAnalysisWarning(
                code=SinglePaperAnalysisWarningCode.QUALITY_HALTED,
                details="Extraction garbage.",
            ),
        ),
        sections=(),
        research_question=None,
        evidence=(),
        created_at="2026-08-01T20:00:00Z",
        updated_at="2026-08-01T20:00:00Z",
    )
    storage.save_single_paper_analysis(quality_rec)
    ret_q = storage.get_single_paper_analysis(quality_rec.analysis_id)
    assert ret_q == quality_rec

    storage.close()


def test_cascade_delete_analysis_record(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
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
        "single_paper_analysis_questions",
        "single_paper_analysis_evidence",
    ):
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE analysis_id = ?",
            (record.analysis_id,),
        )
        assert cur.fetchone()[0] == 0

    storage.close()


def test_transaction_rollback_leaves_no_partial_data(tmp_path: Path) -> None:
    """An error during write rolls back entire transaction cleanly."""
    pdf_path = tmp_path / "paper.pdf"
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    real_conn = storage._ensure_initialized()

    class ProxyConn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO single_paper_analysis_evidence" in sql:
                raise sqlite3.OperationalError("Simulated database error")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    storage._conn = ProxyConn(real_conn)
    record = _make_success_record(pdf_path)

    with pytest.raises(StorageTransactionError, match="Simulated database error"):
        storage.save_single_paper_analysis(record)

    storage._conn = real_conn

    # Verify no parent or child rows were created
    cur = real_conn.execute(
        "SELECT COUNT(*) FROM single_paper_analyses WHERE analysis_id = ?",
        (record.analysis_id,),
    )
    assert cur.fetchone()[0] == 0

    storage.close()
