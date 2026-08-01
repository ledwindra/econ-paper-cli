"""Unit tests for SQLite storage adapter."""

import sqlite3
from pathlib import Path

import pytest

from econ_paper_cli.adapters.sqlite_storage import (
    CURRENT_SCHEMA_VERSION,
    SQLiteStorage,
)
from econ_paper_cli.domain import Corpus, Paper, Passage
from econ_paper_cli.domain.storage import (
    ConversionSettings,
    IngestionCompletion,
    IngestionWarning,
    PaperRecord,
    SourceProvenance,
)
from econ_paper_cli.protocols.storage import (
    ChecksumConflictError,
    StorageBackend,
    StorageIncompatibleSchemaError,
    StorageMigrationError,
    StorageTransactionError,
)

CHECKSUM_1 = "1" * 64
CHECKSUM_2 = "2" * 64


@pytest.fixture
def sample_paper_record() -> PaperRecord:
    paper = Paper(
        paper_id="paper.2024.v1",
        title="Economic Analysis of Local Libraries",
        authors=("Alice Smith", "Bob Jones"),
        year=2024,
        abstract="This paper analyzes local library economies.",
        source_name="NBER",
        source_identifier="w12345",
        source_url="https://example.org/w12345.pdf",
    )
    passage = Passage(
        passage_id="paper.2024.v1:p1",
        paper_id="paper.2024.v1",
        text="Local libraries provide public goods with high returns.",
        section_heading="1. Introduction",
        page_start=1,
        page_end=2,
        ordinal_position=0,
    )
    provenance = SourceProvenance(
        source_path="/papers/2024/w12345.pdf",
        source_format="pdf",
        source_file_size=1024567,
        content_checksum=CHECKSUM_1,
        markdown_path="/papers/2024/w12345.md",
        extraction_method="pdfplumber-v1",
        created_at="2026-07-31T20:00:00Z",
    )
    conversion = ConversionSettings(
        conversion_version="1.0.0",
        ocr_enabled=False,
        parameters={"max_passage_tokens": 512},
    )
    warning = IngestionWarning(
        warning_code="LOW_RESOLUTION_PAGE",
        message="Page 3 has low resolution image.",
        created_at="2026-07-31T20:00:00Z",
    )
    completion = IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T20:01:00Z",
        passage_count=1,
        warning_count=1,
        error_message=None,
    )
    return PaperRecord(
        paper=paper,
        passages=(passage,),
        source_provenance=provenance,
        conversion_settings=conversion,
        warnings=(warning,),
        completion=completion,
    )


def test_protocol_conformance() -> None:
    storage = SQLiteStorage(":memory:")
    assert isinstance(storage, StorageBackend)


def test_initialization_and_schema_version() -> None:
    storage = SQLiteStorage(":memory:")
    assert storage.get_schema_version() == CURRENT_SCHEMA_VERSION
    storage.close()


def test_save_and_retrieve_round_trip(sample_paper_record: PaperRecord) -> None:
    storage = SQLiteStorage(":memory:")
    storage.save_paper_record(sample_paper_record)

    # Count checks
    assert storage.count_papers() == 1
    assert storage.count_passages() == 1
    assert storage.list_paper_ids() == ("paper.2024.v1",)

    # Get by paper_id
    retrieved = storage.get_paper_record("paper.2024.v1")
    assert retrieved is not None
    assert retrieved.paper == sample_paper_record.paper
    assert retrieved.passages == sample_paper_record.passages
    assert retrieved.source_provenance == sample_paper_record.source_provenance
    assert retrieved.conversion_settings == sample_paper_record.conversion_settings
    assert retrieved.warnings == sample_paper_record.warnings
    assert retrieved.completion == sample_paper_record.completion

    # Get by checksum
    retrieved_by_ck = storage.get_paper_record_by_checksum(CHECKSUM_1)
    assert retrieved_by_ck == retrieved

    # Get individual paper & passages
    paper_only = storage.get_paper("paper.2024.v1")
    assert paper_only == sample_paper_record.paper

    passages_only = storage.get_passages("paper.2024.v1")
    assert passages_only == sample_paper_record.passages

    storage.close()


def test_load_corpus_contract(sample_paper_record: PaperRecord) -> None:
    storage = SQLiteStorage(":memory:")
    storage.save_paper_record(sample_paper_record)

    corpus = storage.load_corpus()
    assert isinstance(corpus, Corpus)
    assert len(corpus.papers) == 1
    assert corpus.papers[0] == sample_paper_record.paper
    assert len(corpus.passages) == 1
    assert corpus.passages[0] == sample_paper_record.passages[0]
    storage.close()


def test_idempotency_and_deterministic_replacement(
    sample_paper_record: PaperRecord,
) -> None:
    storage = SQLiteStorage(":memory:")

    # First save
    storage.save_paper_record(sample_paper_record)

    # Second save with exact same record (idempotent)
    storage.save_paper_record(sample_paper_record)
    assert storage.count_papers() == 1
    assert storage.count_passages() == 1

    # Save updated record for same paper_id (replacement)
    updated_paper = Paper(
        paper_id="paper.2024.v1",
        title="Updated Title For Economic Analysis",
        authors=("Alice Smith", "Bob Jones", "Charlie Brown"),
        year=2025,
        abstract="Updated abstract.",
        source_name="NBER",
        source_identifier="w12345",
        source_url="https://example.org/w12345.pdf",
    )
    updated_passage_1 = Passage(
        passage_id="paper.2024.v1:p1",
        paper_id="paper.2024.v1",
        text="Updated passage 1 text.",
        section_heading="1. Intro",
        page_start=1,
        page_end=1,
        ordinal_position=0,
    )
    updated_passage_2 = Passage(
        passage_id="paper.2024.v1:p2",
        paper_id="paper.2024.v1",
        text="New passage 2 text.",
        section_heading="2. Methods",
        page_start=2,
        page_end=3,
        ordinal_position=1,
    )
    updated_completion = IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T21:00:00Z",
        passage_count=2,
        warning_count=1,
    )
    updated_record = PaperRecord(
        paper=updated_paper,
        passages=(updated_passage_1, updated_passage_2),
        source_provenance=sample_paper_record.source_provenance,
        conversion_settings=sample_paper_record.conversion_settings,
        warnings=sample_paper_record.warnings,
        completion=updated_completion,
    )

    storage.save_paper_record(updated_record)
    assert storage.count_papers() == 1
    assert storage.count_passages() == 2

    retrieved = storage.get_paper_record("paper.2024.v1")
    assert retrieved is not None
    assert retrieved.paper.title == "Updated Title For Economic Analysis"
    assert len(retrieved.passages) == 2

    storage.close()


def test_checksum_uniqueness_conflict_and_case_insensitivity(
    sample_paper_record: PaperRecord,
) -> None:
    storage = SQLiteStorage(":memory:")
    storage.save_paper_record(sample_paper_record)

    # Attempt to save a DIFFERENT paper_id using the SAME checksum (checksum_1)
    conflicting_paper = Paper(
        paper_id="paper.different.v1",
        title="Different Paper Title",
        authors=("Dave Miller",),
        year=2023,
        abstract="Different abstract.",
        source_name="QJE",
        source_identifier="q123",
        source_url=None,
    )
    conflicting_passage = Passage(
        passage_id="paper.different.v1:p1",
        paper_id="paper.different.v1",
        text="Different text passage.",
        section_heading=None,
        page_start=1,
        page_end=1,
        ordinal_position=0,
    )
    conflicting_provenance = SourceProvenance(
        source_path="/papers/different.pdf",
        source_format="pdf",
        source_file_size=2048,
        content_checksum=CHECKSUM_1,  # Same checksum as paper.2024.v1!
        markdown_path="/papers/different.md",
        extraction_method="pdfplumber-v1",
        created_at="2026-07-31T20:00:00Z",
    )
    conflicting_completion = IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T20:00:00Z",
        passage_count=1,
        warning_count=0,
    )
    conflicting_record = PaperRecord(
        paper=conflicting_paper,
        passages=(conflicting_passage,),
        source_provenance=conflicting_provenance,
        conversion_settings=sample_paper_record.conversion_settings,
        warnings=(),
        completion=conflicting_completion,
    )

    with pytest.raises(ChecksumConflictError, match="already associated with paper_id"):
        storage.save_paper_record(conflicting_record)

    # Original paper remains untouched
    assert storage.count_papers() == 1
    assert storage.get_paper("paper.different.v1") is None
    storage.close()


def test_rejection_of_duplicate_passage_ordinal_positions(
    tmp_path: Path, sample_paper_record: PaperRecord
) -> None:
    db_file = tmp_path / "ordinal_test.db"
    storage = SQLiteStorage(db_file)
    storage.initialize()
    conn = storage._conn
    assert conn is not None

    storage.save_paper_record(sample_paper_record)

    # Attempt to insert a second passage with the same paper_id and same ordinal_position (0)
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """INSERT INTO passages (
                    passage_id, paper_id, text, section_heading,
                    page_start, page_end, ordinal_position
                ) VALUES ('paper.2024.v1:p2', 'paper.2024.v1', 'Duplicate ordinal text', NULL, 1, 1, 0)"""
            )

    storage.close()


def test_unsupported_newer_schema_rejection(tmp_path: Path) -> None:
    db_file = tmp_path / "newer_schema.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );"""
    )
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) VALUES (99, '2026-07-31T20:00:00Z', 'Future schema');"
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db_file)
    with pytest.raises(
        StorageIncompatibleSchemaError,
        match="Database schema version 99 is newer than maximum supported version",
    ):
        storage.initialize()

    # DB remains unchanged
    conn = sqlite3.connect(str(db_file))
    cursor = conn.execute("SELECT MAX(version) AS v FROM schema_migrations")
    assert cursor.fetchone()[0] == 99
    conn.close()


def test_transaction_rollback_on_failure(sample_paper_record: PaperRecord) -> None:
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    conn = storage._conn
    assert conn is not None

    # Inject an artificial trigger that fails during passage insert
    conn.execute(
        """CREATE TRIGGER fail_passage_insert BEFORE INSERT ON passages
           BEGIN
               SELECT RAISE(FAIL, 'Simulated passage insert failure');
           END;"""
    )

    with pytest.raises(
        StorageTransactionError, match="Simulated passage insert failure"
    ):
        storage.save_paper_record(sample_paper_record)

    # Verify that papers table is completely empty due to rollback
    assert storage.count_papers() == 0
    assert storage.count_passages() == 0
    storage.close()


def test_forward_migration_and_rollback() -> None:
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    assert storage.get_schema_version() == 1

    # Define a valid migration 2
    migration_v2 = [
        (
            2,
            "Add test metadata column",
            ["ALTER TABLE papers ADD COLUMN notes TEXT;"],
        )
    ]
    storage._run_migrations(custom_migrations=migration_v2)
    assert storage.get_schema_version() == 2

    # Define a failing migration 3
    migration_v3_failing = [
        (
            3,
            "Failing migration statement",
            ["INVALID SQL STATEMENT syntax error;"],
        )
    ]
    with pytest.raises(
        StorageMigrationError, match="Migration to schema version 3 failed"
    ):
        storage._run_migrations(custom_migrations=migration_v3_failing)

    # Version remains at 2 (rolled back)
    assert storage.get_schema_version() == 2
    storage.close()


def test_deletion_and_cascading(sample_paper_record: PaperRecord) -> None:
    storage = SQLiteStorage(":memory:")
    storage.save_paper_record(sample_paper_record)
    assert storage.count_papers() == 1
    assert storage.count_passages() == 1

    deleted = storage.delete_paper_record("paper.2024.v1")
    assert deleted is True
    assert storage.count_papers() == 0
    assert storage.count_passages() == 0
    assert storage.get_paper_record("paper.2024.v1") is None

    # Delete non-existent
    assert storage.delete_paper_record("nonexistent.id") is False
    storage.close()


def test_file_based_storage(tmp_path: Path, sample_paper_record: PaperRecord) -> None:
    db_file = tmp_path / "sub_dir" / "test_library.db"
    storage = SQLiteStorage(db_file)
    storage.save_paper_record(sample_paper_record)

    assert db_file.exists()
    assert storage.count_papers() == 1
    storage.close()

    # Reopen same database file
    reopened_storage = SQLiteStorage(db_file)
    assert reopened_storage.count_papers() == 1
    assert reopened_storage.get_paper_record("paper.2024.v1") == sample_paper_record
    reopened_storage.close()
