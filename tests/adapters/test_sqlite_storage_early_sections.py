"""SQLite contract tests for durable early-section library records."""

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from econ_paper_cli.adapters import BM25Retriever
from econ_paper_cli.adapters.sqlite_storage import CURRENT_SCHEMA_VERSION, SQLiteStorage
from econ_paper_cli.domain import (
    EarlySectionLibraryRecord,
    ExtractedPDFPage,
    PDFConversionSettings,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSection,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
    compute_conversion_settings_fingerprint,
)
from econ_paper_cli.protocols import StorageTransactionError, StorageValidationError
from econ_paper_cli.protocols.storage import ChecksumConflictError
from econ_paper_cli.services import (
    convert_pdf_early_sections,
    project_early_section_library_record,
)

CHECKSUM = "b" * 64


def _record(
    *,
    timestamp: str = "2026-08-01T12:00:00+00:00",
    max_characters: int = 1200,
    source_path: Path | None = None,
    section_policy_version: str = "pdf-section-detection-v2",
    checksum: str = CHECKSUM,
) -> EarlySectionLibraryRecord:
    """Build a record genuinely produced under ``section_policy_version``.

    The section policy identity flows into both the detection result and the
    conversion settings, so a "v1 record" really is one the v1 pipeline
    would have produced — passage identities included. Patching settings
    onto a v2-derived record afterwards would not be a real v1 record,
    since passage IDs are derived from the settings fingerprint.
    """
    text = "First paragraph.\n\nSecond paragraph."
    path = source_path or (Path.cwd().resolve() / "stored.pdf")
    extraction = PDFExtractionResult(
        source_path=path,
        pages=(ExtractedPDFPage(1, text),),
        page_count=1,
        metadata=PDFDocumentMetadata(title="Stored", author_text=None),
        extraction_method="synthetic",
        parser_version="1.0",
    )
    detection = PDFSectionDetectionResult(
        policy_version=section_policy_version,
        sections=(
            PDFSection(
                kind=PDFSectionKind.INTRODUCTION,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Introduction",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 0, len(text)),),
                text=text,
            ),
        ),
        candidates=(),
        warnings=(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),),
    )
    settings = PDFConversionSettings(
        max_passage_characters=max_characters,
        section_policy_version=section_policy_version,
    )
    conversion = convert_pdf_early_sections(
        extraction, detection, content_checksum=checksum, settings=settings
    )
    return project_early_section_library_record(
        extraction,
        detection,
        conversion,
        source_file_size=1234,
        timestamp=timestamp,
    )


def test_fresh_schema_atomic_round_trip_restart_and_bm25(tmp_path: Path) -> None:
    database = tmp_path / "library.sqlite3"
    record = _record()
    storage = SQLiteStorage(database)

    storage.save_early_section_record(record)
    assert storage.get_schema_version() == CURRENT_SCHEMA_VERSION == 6
    assert storage.get_early_section_record(record.paper.paper_id) == record
    generic_record = storage.get_paper_record(record.paper.paper_id)
    assert generic_record is not None
    assert generic_record.passages == record.passages
    assert generic_record.source_provenance.markdown_path is None
    storage.close()

    reopened = SQLiteStorage(database)
    assert reopened.get_early_section_record(record.paper.paper_id) == record
    corpus = reopened.load_corpus()
    assert corpus.papers == (record.paper,)
    assert corpus.passages == record.passages
    BM25Retriever(corpus)
    reopened.close()


def test_stale_v1_early_section_record_cache_invalidation(tmp_path: Path) -> None:
    database = tmp_path / "library.sqlite3"
    storage = SQLiteStorage(database)
    storage.initialize()

    # 1. Construct and save a record genuinely produced under section
    #    policy v1 (detection policy and conversion settings both v1, so
    #    passage identities really are the v1 pipeline's).
    v1_record = _record(
        timestamp="2026-08-01T00:00:00Z",
        section_policy_version="pdf-section-detection-v1",
    )
    v1_settings = v1_record.conversion_settings
    storage.save_early_section_record(v1_record)

    # 2. Querying with requested v2 settings returns None (cache miss) —
    #    section_policy_version is part of the composite fingerprint, so
    #    v1 and v2 can never collide on one fingerprint.
    v2_record = _record(
        timestamp="2026-08-01T00:00:00Z",
        section_policy_version="pdf-section-detection-v2",
    )
    v2_settings = v2_record.conversion_settings
    assert compute_conversion_settings_fingerprint(
        v1_settings
    ) != compute_conversion_settings_fingerprint(v2_settings)
    retrieved_v2 = storage.get_early_section_record(
        v1_record.paper.paper_id, settings=v2_settings
    )
    assert retrieved_v2 is None

    # 3. Replace with the v2 record
    v2_record = dataclasses.replace(v2_record, updated_at="2026-08-02T12:00:00Z")
    storage.save_early_section_record(v2_record)
    storage.close()

    # 4. Reopen database after restart and verify it remains v2
    reopened = SQLiteStorage(database)
    reopened.initialize()
    persisted = reopened.get_early_section_record(
        v2_record.paper.paper_id, settings=v2_settings
    )
    assert persisted is not None
    assert (
        persisted.conversion_settings.section_policy_version
        == "pdf-section-detection-v2"
    )
    reopened.close()


def test_replacement_preserves_created_at_and_removes_stale_rows() -> None:
    storage = SQLiteStorage(":memory:")
    original = _record(max_characters=1200)
    replacement = _record(timestamp="2026-08-02T12:00:00+00:00", max_characters=18)
    old_ids = {passage.passage_id for passage in original.passages}

    storage.save_early_section_record(original)
    storage.save_early_section_record(replacement)
    stored = storage.get_early_section_record(original.paper.paper_id)

    assert stored is not None
    assert stored.created_at == original.created_at
    assert stored.source_provenance.created_at == original.created_at
    assert stored.updated_at == replacement.updated_at
    assert stored.settings_fingerprint == replacement.settings_fingerprint
    assert len(stored.passages) > len(original.passages)
    conn = storage._conn
    assert conn is not None
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM passages WHERE passage_id IN ({})".format(
                ",".join("?" for _ in old_ids)
            ),
            tuple(old_ids),
        ).fetchone()[0]
        == 0
    )
    storage.close()


def test_exact_resave_is_idempotent() -> None:
    storage = SQLiteStorage(":memory:")
    record = _record()
    storage.save_early_section_record(record)
    storage.save_early_section_record(record)
    assert storage.list_early_section_records() == (record,)
    assert storage.count_papers() == 1
    assert storage.count_passages() == len(record.passages)
    storage.close()


def test_renamed_source_updates_path_without_changing_identities(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(":memory:")
    original = _record(source_path=tmp_path / "original.pdf")
    renamed = _record(
        timestamp="2026-08-02T12:00:00+00:00",
        source_path=tmp_path / "renamed.pdf",
    )
    storage.save_early_section_record(original)
    storage.save_early_section_record(renamed)
    stored = storage.get_early_section_record(original.paper.paper_id)
    assert stored is not None
    assert stored.paper.paper_id == original.paper.paper_id
    assert stored.passages == original.passages
    assert stored.source_provenance.source_path == str(tmp_path / "renamed.pdf")
    storage.close()


def test_case_insensitive_checksum_conflict_with_different_paper_id() -> None:
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    conn = storage._conn
    assert conn is not None
    conn.execute(
        """INSERT INTO papers (
            paper_id, title, authors_json, source_name, source_identifier,
            content_checksum, created_at, updated_at
        ) VALUES ('different-paper', 'Different', '[]', 'local-pdf', ?, ?, 'c', 'u')""",
        (CHECKSUM.upper(), CHECKSUM.upper()),
    )
    conn.commit()

    with pytest.raises(ChecksumConflictError, match="different-paper"):
        storage.save_early_section_record(_record())
    assert storage.get_paper("different-paper") is not None
    assert storage.get_early_section_record(_record().paper.paper_id) is None
    storage.close()


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("early_section_records", "parent failure"),
        ("passages", "passage failure"),
        ("passage_provenance", "provenance failure"),
        ("passage_source_fragments", "fragment failure"),
    ],
)
def test_write_failure_rolls_back_every_stage(table: str, message: str) -> None:
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    conn = storage._conn
    assert conn is not None
    conn.execute(
        f"""CREATE TRIGGER fail_write BEFORE INSERT ON {table}
            BEGIN SELECT RAISE(FAIL, '{message}'); END;"""
    )

    with pytest.raises(StorageTransactionError, match=message):
        storage.save_early_section_record(_record())

    assert storage.count_papers() == 0
    assert storage.count_passages() == 0
    storage.close()


def test_failed_replacement_preserves_previous_complete_record() -> None:
    storage = SQLiteStorage(":memory:")
    original = _record()
    storage.save_early_section_record(original)
    conn = storage._conn
    assert conn is not None
    conn.execute(
        """CREATE TRIGGER fail_fragment BEFORE INSERT ON passage_source_fragments
           BEGIN SELECT RAISE(FAIL, 'replacement fragment failure'); END;"""
    )

    with pytest.raises(StorageTransactionError, match="replacement fragment failure"):
        storage.save_early_section_record(
            _record(timestamp="2026-08-02T12:00:00+00:00", max_characters=18)
        )

    assert storage.get_early_section_record(original.paper.paper_id) == original
    storage.close()


def test_corrupt_fragment_source_text_fails_strict_read_back() -> None:
    storage = SQLiteStorage(":memory:")
    record = _record()
    storage.save_early_section_record(record)
    conn = storage._conn
    assert conn is not None
    conn.execute("UPDATE passage_source_fragments SET source_text = 'corrupt'")
    conn.commit()

    with pytest.raises(StorageValidationError, match="source_text|passage slice"):
        storage.get_early_section_record(record.paper.paper_id)
    storage.close()


def test_corrupt_nonempty_markdown_fails_strict_read_back() -> None:
    storage = SQLiteStorage(":memory:")
    record = _record()
    storage.save_early_section_record(record)
    conn = storage._conn
    assert conn is not None
    conn.execute(
        "UPDATE early_section_records SET markdown = '# unrelated but non-empty' "
        "WHERE paper_id = ?",
        (record.paper.paper_id,),
    )
    conn.commit()

    with pytest.raises(StorageValidationError, match="markdown_sha256"):
        storage.get_early_section_record(record.paper.paper_id)
    storage.close()


def test_list_and_delete_are_scoped_to_early_section_records() -> None:
    storage = SQLiteStorage(":memory:")
    record = _record()
    storage.save_early_section_record(record)
    assert storage.list_early_section_records() == (record,)
    assert storage.delete_early_section_record("missing") is False
    assert storage.delete_early_section_record(record.paper.paper_id) is True
    assert storage.get_early_section_record(record.paper.paper_id) is None
    assert storage.list_early_section_records() == ()
    storage.close()


def test_v3_migration_preserves_legacy_markdown_path(tmp_path: Path) -> None:
    database = tmp_path / "v3.sqlite3"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            authors_json TEXT NOT NULL, year INTEGER, abstract TEXT,
            source_name TEXT NOT NULL, source_identifier TEXT NOT NULL,
            source_url TEXT, content_checksum TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE source_provenance (
            paper_id TEXT PRIMARY KEY, source_path TEXT NOT NULL,
            source_format TEXT NOT NULL, source_file_size INTEGER NOT NULL,
            content_checksum TEXT NOT NULL COLLATE NOCASE,
            markdown_path TEXT NOT NULL, extraction_method TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
        );
        CREATE TABLE passages (
            passage_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, text TEXT NOT NULL,
            section_heading TEXT, page_start INTEGER, page_end INTEGER,
            ordinal_position INTEGER NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE,
            UNIQUE(paper_id, ordinal_position)
        );
        CREATE TABLE single_paper_analysis_sections (
            analysis_id TEXT NOT NULL,
            section_kind TEXT NOT NULL,
            heading_text TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            ordinal_position INTEGER NOT NULL,
            PRIMARY KEY(analysis_id, section_kind)
        );
        INSERT INTO schema_migrations VALUES
            (1, '2026-08-01', 'v1'), (2, '2026-08-01', 'v2'),
            (3, '2026-08-01', 'v3');
        """
    )
    conn.execute(
        """INSERT INTO papers VALUES
           ('legacy', 'Legacy', '["Author"]', NULL, NULL, 'local', 'legacy',
            NULL, ?, 'created', 'updated')""",
        ("c" * 64,),
    )
    conn.execute(
        """INSERT INTO source_provenance VALUES
           ('legacy', '/legacy.pdf', 'pdf', 12, ?, '/legacy.md',
            'legacy-parser', 'created')""",
        ("c" * 64,),
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(database)
    storage.initialize()
    conn = storage._conn
    assert conn is not None
    row = conn.execute(
        "SELECT markdown_path, parser_version FROM source_provenance WHERE paper_id = 'legacy'"
    ).fetchone()
    assert tuple(row) == ("/legacy.md", "legacy-unknown")
    assert storage.get_schema_version() == 6
    storage.close()


def test_row_stale_under_a_since_changed_fingerprint_formula_is_skipped_not_fatal(
    tmp_path: Path,
) -> None:
    """A record saved before ``section_policy_version`` was folded into the
    composite fingerprint has passage_ids permanently derived from the
    *old*-formula fingerprint value stored on that row. It cannot satisfy
    today's formula (recomputing would break its own passage_id identity
    instead) and is therefore genuinely stale — direct access reports it as
    such, exactly like any other integrity failure. What must not happen is
    one such row aborting ``list_early_section_records()`` (and therefore
    ``load_corpus()``/chat) for every *other*, still-valid paper in the
    library."""
    database = tmp_path / "library.sqlite3"
    storage = SQLiteStorage(database)
    storage.initialize()

    stale_record = _record(
        source_path=Path.cwd().resolve() / "stale.pdf", checksum="a" * 64
    )
    healthy_record = _record(
        source_path=Path.cwd().resolve() / "healthy.pdf",
        timestamp="2026-08-01T13:00:00+00:00",
        checksum="c" * 64,
    )
    storage.save_early_section_record(stale_record)
    storage.save_early_section_record(healthy_record)

    conn = storage._conn
    assert conn is not None
    # Simulate a stored fingerprint computed under a formula that predates
    # today's code — self-consistent with nothing this build can recompute.
    stale_fingerprint = "0" * 64
    assert stale_fingerprint != stale_record.settings_fingerprint
    conn.execute(
        "UPDATE early_section_records SET settings_fingerprint = ? WHERE paper_id = ?",
        (stale_fingerprint, stale_record.paper.paper_id),
    )
    conn.commit()

    with pytest.raises(StorageValidationError):
        storage.get_early_section_record(stale_record.paper.paper_id)

    all_records = storage.list_early_section_records()
    assert {record.paper.paper_id for record in all_records} == {
        healthy_record.paper.paper_id
    }
    storage.close()
