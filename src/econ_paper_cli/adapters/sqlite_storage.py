import dataclasses
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain.corpora import Corpus
from econ_paper_cli.domain.early_section_library import (
    EarlySectionLibraryRecord,
    StoredPassageProvenance,
    StoredPassageSourceFragment,
)
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.pdf_conversion import PDFConversionSettings
from econ_paper_cli.domain.pdf_quality import (
    PDFQualitySettings,
    PDFQualityStatus,
    PDFQualityWarning,
    PDFQualityWarningCode,
)
from econ_paper_cli.domain.pdf_sections import (
    PDFSectionKind,
    PDFSectionSettings,
    PDFSectionWarning,
    PDFSectionWarningCode,
)
from econ_paper_cli.domain.research_question import (
    ResearchQuestionKind,
    ResearchQuestionSettings,
    ResearchQuestionWarning,
    ResearchQuestionWarningCode,
)
from econ_paper_cli.domain.single_paper_analysis import (
    SinglePaperAnalysisEvidenceRecord,
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSectionRecord,
    SinglePaperAnalysisSectionSpanRecord,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisWarning,
    SinglePaperAnalysisWarningCode,
)
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
    StorageConnectionError,
    StorageIncompatibleSchemaError,
    StorageMigrationError,
    StorageTransactionError,
    StorageValidationError,
)

CURRENT_SCHEMA_VERSION = 4

_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "Initial schema creation",
        [
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors_json TEXT NOT NULL,
                year INTEGER,
                abstract TEXT,
                source_name TEXT NOT NULL,
                source_identifier TEXT NOT NULL,
                source_url TEXT,
                content_checksum TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );""",
            "CREATE INDEX IF NOT EXISTS idx_papers_checksum ON papers(content_checksum);",
            """CREATE TABLE IF NOT EXISTS source_provenance (
                paper_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_format TEXT NOT NULL,
                content_checksum TEXT NOT NULL,
                extraction_method TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            """CREATE TABLE IF NOT EXISTS conversion_settings (
                paper_id TEXT PRIMARY KEY,
                conversion_version TEXT NOT NULL,
                ocr_enabled INTEGER NOT NULL,
                parameters_json TEXT NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            """CREATE TABLE IF NOT EXISTS passages (
                passage_id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL,
                text TEXT NOT NULL,
                section_heading TEXT,
                page_start INTEGER,
                page_end INTEGER,
                ordinal_position INTEGER NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            "CREATE INDEX IF NOT EXISTS idx_passages_paper_ordinal ON passages(paper_id, ordinal_position);",
            """CREATE TABLE IF NOT EXISTS ingestion_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT NOT NULL,
                warning_code TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            "CREATE INDEX IF NOT EXISTS idx_warnings_paper_id ON ingestion_warnings(paper_id);",
            """CREATE TABLE IF NOT EXISTS ingestion_completions (
                paper_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                passage_count INTEGER NOT NULL,
                warning_count INTEGER NOT NULL,
                error_message TEXT,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
        ],
    ),
    (
        2,
        "Add provenance fields, case-insensitive checksums, and passage ordinal uniqueness constraint",
        [
            """CREATE TABLE papers_v2 (
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
            );""",
            """INSERT INTO papers_v2 (
                paper_id, title, authors_json, year, abstract,
                source_name, source_identifier, source_url,
                content_checksum, created_at, updated_at
            ) SELECT
                paper_id, title, authors_json, year, abstract,
                source_name, source_identifier, source_url,
                LOWER(content_checksum), created_at, updated_at
            FROM papers;""",
            "DROP TABLE papers;",
            "ALTER TABLE papers_v2 RENAME TO papers;",
            "CREATE INDEX IF NOT EXISTS idx_papers_checksum ON papers(content_checksum);",
            """CREATE TABLE source_provenance_v2 (
                paper_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_format TEXT NOT NULL,
                source_file_size INTEGER NOT NULL DEFAULT 1,
                content_checksum TEXT NOT NULL COLLATE NOCASE,
                markdown_path TEXT NOT NULL DEFAULT '',
                extraction_method TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            """INSERT INTO source_provenance_v2 (
                paper_id, source_path, source_format, source_file_size,
                content_checksum, markdown_path, extraction_method, created_at
            ) SELECT
                paper_id, source_path, source_format, 1,
                LOWER(content_checksum), source_path, extraction_method, created_at
            FROM source_provenance;""",
            "DROP TABLE source_provenance;",
            "ALTER TABLE source_provenance_v2 RENAME TO source_provenance;",
            """CREATE TABLE passages_v2 (
                passage_id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL,
                text TEXT NOT NULL,
                section_heading TEXT,
                page_start INTEGER,
                page_end INTEGER,
                ordinal_position INTEGER NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE,
                CONSTRAINT uq_paper_ordinal UNIQUE(paper_id, ordinal_position)
            );""",
            """INSERT INTO passages_v2 (
                passage_id, paper_id, text, section_heading,
                page_start, page_end, ordinal_position
            ) SELECT
                passage_id, paper_id, text, section_heading,
                page_start, page_end, ordinal_position
            FROM passages;""",
            "DROP TABLE passages;",
            "ALTER TABLE passages_v2 RENAME TO passages;",
            "CREATE INDEX IF NOT EXISTS idx_passages_paper_ordinal ON passages(paper_id, ordinal_position);",
        ],
    ),
    (
        3,
        "Single paper analysis research-question persistence tables",
        [
            """CREATE TABLE IF NOT EXISTS single_paper_analyses (
                analysis_id TEXT PRIMARY KEY,
                content_checksum TEXT COLLATE NOCASE,
                source_path TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                status TEXT NOT NULL,
                failed_stage TEXT,
                failure_code TEXT,
                error_message TEXT,
                completed_stages_json TEXT NOT NULL,
                skipped_stages_json TEXT NOT NULL,
                quality_status TEXT,
                quality_settings_json TEXT NOT NULL,
                section_settings_json TEXT NOT NULL,
                research_question_settings_json TEXT NOT NULL,
                settings_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );""",
            "CREATE INDEX IF NOT EXISTS idx_single_paper_analyses_checksum ON single_paper_analyses(content_checksum);",
            "CREATE INDEX IF NOT EXISTS idx_single_paper_analyses_fingerprint ON single_paper_analyses(settings_fingerprint);",
            """CREATE TABLE IF NOT EXISTS single_paper_analysis_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL,
                warning_domain TEXT NOT NULL,
                warning_code TEXT NOT NULL,
                details TEXT,
                page_numbers_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(analysis_id) REFERENCES single_paper_analyses(analysis_id) ON DELETE CASCADE
            );""",
            "CREATE INDEX IF NOT EXISTS idx_analysis_warnings_analysis_id ON single_paper_analysis_warnings(analysis_id);",
            """CREATE TABLE IF NOT EXISTS single_paper_analysis_sections (
                analysis_id TEXT NOT NULL,
                section_kind TEXT NOT NULL,
                heading_text TEXT NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                ordinal_position INTEGER NOT NULL,
                PRIMARY KEY(analysis_id, section_kind),
                FOREIGN KEY(analysis_id) REFERENCES single_paper_analyses(analysis_id) ON DELETE CASCADE,
                CONSTRAINT uq_analysis_section_ordinal UNIQUE(analysis_id, ordinal_position)
            );""",
            "CREATE INDEX IF NOT EXISTS idx_analysis_sections_analysis_id ON single_paper_analysis_sections(analysis_id);",
            """CREATE TABLE IF NOT EXISTS single_paper_analysis_section_spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL,
                section_kind TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                start_character_offset INTEGER NOT NULL,
                end_character_offset INTEGER NOT NULL,
                ordinal_position INTEGER NOT NULL,
                FOREIGN KEY(analysis_id, section_kind) REFERENCES single_paper_analysis_sections(analysis_id, section_kind) ON DELETE CASCADE,
                CONSTRAINT uq_analysis_section_span_ordinal UNIQUE(analysis_id, section_kind, ordinal_position)
            );""",
            "CREATE INDEX IF NOT EXISTS idx_analysis_section_spans_analysis_sec ON single_paper_analysis_section_spans(analysis_id, section_kind);",
            """CREATE TABLE IF NOT EXISTS single_paper_analysis_questions (
                analysis_id TEXT PRIMARY KEY,
                question_text TEXT,
                kind TEXT NOT NULL,
                sections_used_json TEXT NOT NULL,
                FOREIGN KEY(analysis_id) REFERENCES single_paper_analyses(analysis_id) ON DELETE CASCADE
            );""",
            """CREATE TABLE IF NOT EXISTS single_paper_analysis_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL,
                section_kind TEXT NOT NULL,
                excerpt_text TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                start_character_offset INTEGER NOT NULL,
                end_character_offset INTEGER NOT NULL,
                ordinal_position INTEGER NOT NULL,
                FOREIGN KEY(analysis_id, section_kind) REFERENCES single_paper_analysis_sections(analysis_id, section_kind) ON DELETE CASCADE,
                CONSTRAINT uq_analysis_evidence_ordinal UNIQUE(analysis_id, ordinal_position)
            );""",
            "CREATE INDEX IF NOT EXISTS idx_analysis_evidence_analysis_id ON single_paper_analysis_evidence(analysis_id);",
        ],
    ),
    (
        4,
        "Early-section Markdown and exact passage provenance persistence",
        [
            """CREATE TABLE source_provenance_v4 (
                paper_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_format TEXT NOT NULL,
                source_file_size INTEGER NOT NULL,
                content_checksum TEXT NOT NULL COLLATE NOCASE,
                markdown_path TEXT,
                extraction_method TEXT NOT NULL,
                parser_version TEXT NOT NULL DEFAULT 'legacy-unknown',
                created_at TEXT NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            """INSERT INTO source_provenance_v4 (
                paper_id, source_path, source_format, source_file_size,
                content_checksum, markdown_path, extraction_method,
                parser_version, created_at
            ) SELECT
                paper_id, source_path, source_format, source_file_size,
                LOWER(content_checksum), markdown_path, extraction_method,
                'legacy-unknown', created_at
            FROM source_provenance;""",
            "DROP TABLE source_provenance;",
            "ALTER TABLE source_provenance_v4 RENAME TO source_provenance;",
            """CREATE TABLE early_section_records (
                paper_id TEXT PRIMARY KEY,
                conversion_policy_version TEXT NOT NULL,
                settings_fingerprint TEXT NOT NULL,
                max_passage_characters INTEGER NOT NULL,
                markdown TEXT NOT NULL,
                markdown_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );""",
            """CREATE TABLE passage_provenance (
                passage_id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL,
                section_kind TEXT NOT NULL,
                ordinal_position INTEGER NOT NULL,
                FOREIGN KEY(passage_id) REFERENCES passages(passage_id) ON DELETE CASCADE,
                FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE,
                CONSTRAINT uq_passage_provenance_ordinal UNIQUE(paper_id, ordinal_position)
            );""",
            "CREATE INDEX idx_passage_provenance_paper_ordinal ON passage_provenance(paper_id, ordinal_position);",
            """CREATE TABLE passage_source_fragments (
                passage_id TEXT NOT NULL,
                ordinal_position INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                start_character_offset INTEGER NOT NULL,
                end_character_offset INTEGER NOT NULL,
                passage_start_character_offset INTEGER NOT NULL,
                passage_end_character_offset INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                PRIMARY KEY(passage_id, ordinal_position),
                FOREIGN KEY(passage_id) REFERENCES passage_provenance(passage_id) ON DELETE CASCADE
            );""",
            "CREATE INDEX idx_passage_fragments_passage_ordinal ON passage_source_fragments(passage_id, ordinal_position);",
        ],
    ),
]


class SQLiteStorage(StorageBackend):
    """Local SQLite storage adapter using Python standard library sqlite3."""

    def __init__(
        self, db_path: str | Path | None = None, read_only: bool = False
    ) -> None:
        """Initialize SQLite storage adapter.

        If db_path is None, defaults to the canonical cross-platform user path.
        If db_path is ':memory:', an in-memory SQLite database is used.
        """
        if db_path is None:
            self._db_path = get_default_db_path()
        elif isinstance(db_path, str):
            self._db_path = Path(db_path) if db_path != ":memory:" else db_path
        else:
            self._db_path = db_path
        self._read_only = read_only

        self._conn: sqlite3.Connection | None = None
        self._coordinated_transaction_active = False

    @property
    def db_path(self) -> Path | str:
        """Return the target database path."""
        return self._db_path

    def initialize(self) -> None:
        """Connect to SQLite database, enable foreign keys, and run migrations."""
        if self._conn is not None:
            return

        try:
            if self._read_only and self._db_path == ":memory:":
                raise StorageConnectionError(
                    "Read-only SQLiteStorage cannot use an in-memory database."
                )

            if isinstance(self._db_path, Path):
                if not self._read_only:
                    self._db_path.parent.mkdir(parents=True, exist_ok=True)
                    conn_str = str(self._db_path)
                    connect_kwargs: dict[str, object] = {}
                else:
                    conn_str = f"{self._db_path.resolve(strict=False).as_uri()}?mode=ro"
                    connect_kwargs = {"uri": True}
            else:
                conn_str = self._db_path
                connect_kwargs = {"uri": True} if self._read_only else {}

            self._conn = sqlite3.connect(conn_str, **connect_kwargs)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON;")
        except (sqlite3.Error, OSError, StorageConnectionError) as err:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            if isinstance(err, StorageConnectionError):
                raise
            raise StorageConnectionError(
                f"Failed to connect to SQLite database at '{self._db_path}': {err}."
            ) from err
        except Exception:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            raise

        try:
            if self._read_only:
                current_version = self.get_schema_version()
                if current_version == 0:
                    raise StorageMigrationError(
                        f"Read-only database at '{self._db_path}' is not initialized."
                    )
                if current_version < CURRENT_SCHEMA_VERSION:
                    raise StorageMigrationError(
                        f"Read-only database schema version {current_version} is older than supported version {CURRENT_SCHEMA_VERSION}; reopen the database in write mode to migrate it."
                    )
                if current_version > CURRENT_SCHEMA_VERSION:
                    raise StorageIncompatibleSchemaError(
                        f"Database schema version {current_version} is newer than maximum supported version {CURRENT_SCHEMA_VERSION}."
                    )
                return

            self._run_migrations()
        except Exception:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            raise

    def close(self) -> None:
        """Close active database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _ensure_initialized(self) -> sqlite3.Connection:
        if self._conn is None:
            self.initialize()
        assert self._conn is not None
        return self._conn

    def get_schema_version(self) -> int:
        """Return current database schema version."""
        conn = self._ensure_initialized()
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            )
            if cursor.fetchone() is None:
                return 0
            cursor = conn.execute("SELECT MAX(version) AS max_v FROM schema_migrations")
            row = cursor.fetchone()
            if row is None or row["max_v"] is None:
                return 0
            return int(row["max_v"])
        except sqlite3.Error as err:
            raise StorageConnectionError(
                f"Failed to query schema version: {err}."
            ) from err

    def _run_migrations(
        self, custom_migrations: list[tuple[int, str, list[str]]] | None = None
    ) -> None:
        conn = self._conn
        assert conn is not None

        migrations = custom_migrations if custom_migrations is not None else _MIGRATIONS

        current_version = self.get_schema_version()

        if current_version > CURRENT_SCHEMA_VERSION and custom_migrations is None:
            raise StorageIncompatibleSchemaError(
                f"Database schema version {current_version} is newer than maximum supported version {CURRENT_SCHEMA_VERSION}."
            )

        try:
            conn.execute("PRAGMA foreign_keys = OFF;")
            for version, description, statements in migrations:
                if version > current_version:
                    stmt_list = list(statements)
                    if version == 2 and custom_migrations is None:
                        # Check for existing records in papers for version 1 database
                        cur = conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
                        )
                        if cur.fetchone() is not None:
                            cur = conn.execute(
                                "SELECT LOWER(content_checksum) AS ck FROM papers GROUP BY LOWER(content_checksum) HAVING COUNT(*) > 1"
                            )
                            if cur.fetchone() is not None:
                                raise StorageMigrationError(
                                    f"Migration to schema version {version} failed: database contains legacy records with case-insensitive checksum conflicts. Please rebuild the database library."
                                )

                            cur = conn.execute("PRAGMA table_info(source_provenance)")
                            cols = {row["name"] for row in cur.fetchall()}
                            has_provenance_cols = (
                                "source_file_size" in cols and "markdown_path" in cols
                            )

                            if not has_provenance_cols:
                                cur = conn.execute("SELECT COUNT(*) AS c FROM papers")
                                row = cur.fetchone()
                                if row is not None and row["c"] > 0:
                                    raise StorageMigrationError(
                                        f"Migration to schema version {version} failed: database contains {row['c']} existing paper record(s) missing required provenance metadata (source_file_size and markdown_path). Please rebuild the library database."
                                    )
                            else:
                                # Preserve existing provenance columns in v2 copy statement
                                stmt_list[6] = """INSERT INTO source_provenance_v2 (
                                    paper_id, source_path, source_format, source_file_size,
                                    content_checksum, markdown_path, extraction_method, created_at
                                ) SELECT
                                    paper_id, source_path, source_format, source_file_size,
                                    LOWER(content_checksum), markdown_path, extraction_method, created_at
                                FROM source_provenance;"""

                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        for stmt in stmt_list:
                            conn.execute(stmt)
                        now_str = datetime.now(timezone.utc).isoformat()
                        conn.execute(
                            "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                            (version, now_str, description),
                        )
                        conn.commit()
                    except Exception as err:
                        conn.rollback()
                        if isinstance(err, sqlite3.Error):
                            raise StorageMigrationError(
                                f"Migration to schema version {version} failed: {err}."
                            ) from err
                        raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON;")

    def save_paper_record(self, record: PaperRecord) -> None:
        """Persist or replace a paper record in a single atomic transaction."""
        conn = self._ensure_initialized()

        paper = record.paper
        prov = record.source_provenance
        sett = record.conversion_settings
        comp = record.completion

        # Check for checksum conflict under a different paper_id (case-insensitive)
        try:
            cursor = conn.execute(
                "SELECT paper_id FROM papers WHERE LOWER(content_checksum) = LOWER(?)",
                (prov.content_checksum,),
            )
            existing_row = cursor.fetchone()
            if existing_row is not None and existing_row["paper_id"] != paper.paper_id:
                raise ChecksumConflictError(
                    f"Content checksum '{prov.content_checksum}' is already associated "
                    f"with paper_id '{existing_row['paper_id']}'."
                )
        except sqlite3.Error as err:
            raise StorageTransactionError(
                f"Failed checksum conflict check: {err}."
            ) from err

        now_str = datetime.now(timezone.utc).isoformat()

        try:
            conn.execute("BEGIN IMMEDIATE")

            # Delete existing record if updating (cascades to related tables)
            conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper.paper_id,))

            # Insert into papers
            conn.execute(
                """INSERT INTO papers (
                    paper_id, title, authors_json, year, abstract,
                    source_name, source_identifier, source_url,
                    content_checksum, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    paper.title,
                    json.dumps(list(paper.authors)),
                    paper.year,
                    paper.abstract,
                    paper.source_name,
                    paper.source_identifier,
                    paper.source_url,
                    prov.content_checksum.lower(),
                    prov.created_at,
                    now_str,
                ),
            )

            # Insert into source_provenance
            conn.execute(
                """INSERT INTO source_provenance (
                    paper_id, source_path, source_format, source_file_size,
                    content_checksum, markdown_path, extraction_method, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    prov.source_path,
                    prov.source_format,
                    prov.source_file_size,
                    prov.content_checksum.lower(),
                    prov.markdown_path,
                    prov.extraction_method,
                    prov.created_at,
                ),
            )

            # Insert into conversion_settings
            conn.execute(
                """INSERT INTO conversion_settings (
                    paper_id, conversion_version, ocr_enabled, parameters_json
                ) VALUES (?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    sett.conversion_version,
                    1 if sett.ocr_enabled else 0,
                    json.dumps(sett.parameters),
                ),
            )

            # Insert into passages
            for passage in record.passages:
                conn.execute(
                    """INSERT INTO passages (
                        passage_id, paper_id, text, section_heading,
                        page_start, page_end, ordinal_position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        passage.passage_id,
                        passage.paper_id,
                        passage.text,
                        passage.section_heading,
                        passage.page_start,
                        passage.page_end,
                        passage.ordinal_position,
                    ),
                )

            # Insert into ingestion_warnings
            for warning in record.warnings:
                conn.execute(
                    """INSERT INTO ingestion_warnings (
                        paper_id, warning_code, message, created_at
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        paper.paper_id,
                        warning.warning_code,
                        warning.message,
                        warning.created_at or now_str,
                    ),
                )

            # Insert into ingestion_completions
            conn.execute(
                """INSERT INTO ingestion_completions (
                    paper_id, status, completed_at, passage_count,
                    warning_count, error_message
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    comp.status,
                    comp.completed_at,
                    comp.passage_count,
                    comp.warning_count,
                    comp.error_message,
                ),
            )

            conn.commit()
        except Exception as err:
            conn.rollback()
            if isinstance(err, sqlite3.Error):
                raise StorageTransactionError(
                    f"Failed to save paper record '{paper.paper_id}': {err}."
                ) from err
            raise

    def get_paper_record(self, paper_id: str) -> PaperRecord | None:
        """Retrieve a full PaperRecord by paper_id."""
        paper = self.get_paper(paper_id)
        if paper is None:
            return None

        conn = self._ensure_initialized()
        try:
            # Source provenance
            cur = conn.execute(
                "SELECT * FROM source_provenance WHERE paper_id = ?", (paper_id,)
            )
            prov_row = cur.fetchone()
            if prov_row is None:
                raise StorageValidationError(
                    f"Missing source_provenance for paper_id '{paper_id}'."
                )
            prov = SourceProvenance(
                source_path=prov_row["source_path"],
                source_format=prov_row["source_format"],
                source_file_size=prov_row["source_file_size"],
                content_checksum=prov_row["content_checksum"],
                markdown_path=prov_row["markdown_path"],
                extraction_method=prov_row["extraction_method"],
                created_at=prov_row["created_at"],
            )

            # Conversion settings
            cur = conn.execute(
                "SELECT * FROM conversion_settings WHERE paper_id = ?", (paper_id,)
            )
            sett_row = cur.fetchone()
            if sett_row is None:
                raise StorageValidationError(
                    f"Missing conversion_settings for paper_id '{paper_id}'."
                )
            params = json.loads(sett_row["parameters_json"])
            sett = ConversionSettings(
                conversion_version=sett_row["conversion_version"],
                ocr_enabled=bool(sett_row["ocr_enabled"]),
                parameters=params,
            )

            # Passages
            passages = self.get_passages(paper_id)

            # Ingestion warnings
            cur = conn.execute(
                "SELECT * FROM ingestion_warnings WHERE paper_id = ? ORDER BY id ASC",
                (paper_id,),
            )
            warn_rows = cur.fetchall()
            warnings = tuple(
                IngestionWarning(
                    warning_code=r["warning_code"],
                    message=r["message"],
                    created_at=r["created_at"],
                )
                for r in warn_rows
            )

            # Ingestion completion
            cur = conn.execute(
                "SELECT * FROM ingestion_completions WHERE paper_id = ?", (paper_id,)
            )
            comp_row = cur.fetchone()
            if comp_row is None:
                raise StorageValidationError(
                    f"Missing ingestion_completions for paper_id '{paper_id}'."
                )
            comp = IngestionCompletion(
                status=comp_row["status"],
                completed_at=comp_row["completed_at"],
                passage_count=comp_row["passage_count"],
                warning_count=comp_row["warning_count"],
                error_message=comp_row["error_message"],
            )

            return PaperRecord(
                paper=paper,
                passages=passages,
                source_provenance=prov,
                conversion_settings=sett,
                warnings=warnings,
                completion=comp,
            )
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as err:
            raise StorageValidationError(
                f"Failed to load PaperRecord for '{paper_id}': {err}."
            ) from err

    def get_paper_record_by_checksum(self, checksum: str) -> PaperRecord | None:
        """Retrieve a full PaperRecord by content_checksum."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute(
                "SELECT paper_id FROM papers WHERE LOWER(content_checksum) = LOWER(?)",
                (checksum,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return self.get_paper_record(row["paper_id"])
        except sqlite3.Error as err:
            raise StorageValidationError(
                f"Failed to query paper record by checksum '{checksum}': {err}."
            ) from err

    def get_paper(self, paper_id: str) -> Paper | None:
        """Retrieve Paper bibliographic metadata by paper_id."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,))
            row = cur.fetchone()
            if row is None:
                return None

            authors_list = json.loads(row["authors_json"])
            return Paper(
                paper_id=row["paper_id"],
                title=row["title"],
                authors=tuple(authors_list),
                year=row["year"],
                abstract=row["abstract"],
                source_name=row["source_name"],
                source_identifier=row["source_identifier"],
                source_url=row["source_url"],
            )
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as err:
            raise StorageValidationError(
                f"Failed to parse Paper for paper_id '{paper_id}': {err}."
            ) from err

    def get_passages(self, paper_id: str) -> tuple[Passage, ...]:
        """Retrieve all Passages for paper_id ordered by ordinal position."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute(
                "SELECT * FROM passages WHERE paper_id = ? ORDER BY ordinal_position ASC",
                (paper_id,),
            )
            rows = cur.fetchall()
            return tuple(
                Passage(
                    passage_id=r["passage_id"],
                    paper_id=r["paper_id"],
                    text=r["text"],
                    section_heading=r["section_heading"],
                    page_start=r["page_start"],
                    page_end=r["page_end"],
                    ordinal_position=r["ordinal_position"],
                )
                for r in rows
            )
        except (sqlite3.Error, ValueError) as err:
            raise StorageValidationError(
                f"Failed to load passages for paper_id '{paper_id}': {err}."
            ) from err

    def load_corpus(self, corpus_id: str = "local-library") -> Corpus:
        """Reconstruct and return a validated Corpus from stored paper and passage data."""
        conn = self._ensure_initialized()
        try:
            papers_list = []
            cur = conn.execute("SELECT * FROM papers ORDER BY paper_id ASC")
            for row in cur.fetchall():
                authors_list = json.loads(row["authors_json"])
                papers_list.append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["title"],
                        "authors": authors_list,
                        "year": row["year"],
                        "abstract": row["abstract"],
                        "source_name": row["source_name"],
                        "source_identifier": row["source_identifier"],
                        "source_url": row["source_url"],
                    }
                )

            passages_list = []
            cur = conn.execute(
                "SELECT * FROM passages ORDER BY paper_id ASC, ordinal_position ASC"
            )
            for row in cur.fetchall():
                passages_list.append(
                    {
                        "passage_id": row["passage_id"],
                        "paper_id": row["paper_id"],
                        "text": row["text"],
                        "section_heading": row["section_heading"],
                        "page_start": row["page_start"],
                        "page_end": row["page_end"],
                        "ordinal_position": row["ordinal_position"],
                    }
                )

            mapping = {
                "schema_version": 1,
                "corpus_id": corpus_id,
                "papers": papers_list,
                "passages": passages_list,
            }
            return Corpus.from_mapping(mapping)
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as err:
            raise StorageValidationError(
                f"Failed to load Corpus from database: {err}."
            ) from err

    def list_paper_ids(self) -> tuple[str, ...]:
        """Return a tuple of all stored paper_id identifiers sorted alphabetically."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute("SELECT paper_id FROM papers ORDER BY paper_id ASC")
            rows = cur.fetchall()
            return tuple(r["paper_id"] for r in rows)
        except sqlite3.Error as err:
            raise StorageConnectionError(f"Failed to list paper_ids: {err}.") from err

    def list_paper_records(self) -> tuple[PaperRecord, ...]:
        """Return all stored PaperRecords ordered by paper_id."""
        paper_ids = self.list_paper_ids()
        records: list[PaperRecord] = []
        for pid in paper_ids:
            rec = self.get_paper_record(pid)
            if rec is not None:
                records.append(rec)
        return tuple(records)

    def delete_paper_record(self, paper_id: str) -> bool:
        """Delete paper_id and all associated data in a single transaction."""
        conn = self._ensure_initialized()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
            deleted_count = cur.rowcount
            conn.commit()
            return deleted_count > 0
        except Exception as err:
            conn.rollback()
            if isinstance(err, sqlite3.Error):
                raise StorageTransactionError(
                    f"Failed to delete paper_id '{paper_id}': {err}."
                ) from err
            raise

    def count_papers(self) -> int:
        """Return total count of stored papers."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute("SELECT COUNT(*) AS c FROM papers")
            row = cur.fetchone()
            return int(row["c"]) if row else 0
        except sqlite3.Error as err:
            raise StorageConnectionError(f"Failed to count papers: {err}.") from err

    def count_passages(self) -> int:
        """Return total count of stored passages across all papers."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute("SELECT COUNT(*) AS c FROM passages")
            row = cur.fetchone()
            return int(row["c"]) if row else 0
        except sqlite3.Error as err:
            raise StorageConnectionError(f"Failed to count passages: {err}.") from err

    def save_early_section_record(self, record: EarlySectionLibraryRecord) -> None:
        """Persist or replace a complete early-section record atomically."""
        if not isinstance(record, EarlySectionLibraryRecord):
            raise TypeError("record must be an EarlySectionLibraryRecord instance.")
        conn = self._ensure_initialized()
        paper = record.paper
        provenance = record.source_provenance

        try:
            if not self._coordinated_transaction_active:
                conn.execute("BEGIN IMMEDIATE")
            conflict = conn.execute(
                """SELECT paper_id FROM papers
                   WHERE LOWER(content_checksum) = LOWER(?) AND paper_id <> ?""",
                (provenance.content_checksum, paper.paper_id),
            ).fetchone()
            if conflict is not None:
                raise ChecksumConflictError(
                    f"Content checksum '{provenance.content_checksum}' is already "
                    f"associated with paper_id '{conflict['paper_id']}'."
                )

            existing = conn.execute(
                "SELECT created_at FROM papers WHERE paper_id = ?", (paper.paper_id,)
            ).fetchone()
            created_at = (
                existing["created_at"] if existing is not None else record.created_at
            )
            conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper.paper_id,))

            conn.execute(
                """INSERT INTO papers (
                    paper_id, title, authors_json, year, abstract,
                    source_name, source_identifier, source_url,
                    content_checksum, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    paper.title,
                    json.dumps(list(paper.authors)),
                    paper.year,
                    paper.abstract,
                    paper.source_name,
                    paper.source_identifier,
                    paper.source_url,
                    provenance.content_checksum.lower(),
                    created_at,
                    record.updated_at,
                ),
            )
            conn.execute(
                """INSERT INTO source_provenance (
                    paper_id, source_path, source_format, source_file_size,
                    content_checksum, markdown_path, extraction_method,
                    parser_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    provenance.source_path,
                    provenance.source_format,
                    provenance.source_file_size,
                    provenance.content_checksum.lower(),
                    provenance.markdown_path,
                    provenance.extraction_method,
                    record.parser_version,
                    created_at,
                ),
            )
            conn.execute(
                """INSERT INTO conversion_settings (
                    paper_id, conversion_version, ocr_enabled, parameters_json
                ) VALUES (?, ?, 0, ?)""",
                (
                    paper.paper_id,
                    record.conversion_settings.policy_version,
                    json.dumps(
                        {
                            "max_passage_characters": record.conversion_settings.max_passage_characters
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.execute(
                """INSERT INTO early_section_records (
                    paper_id, conversion_policy_version, settings_fingerprint,
                    max_passage_characters, markdown, markdown_sha256,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paper.paper_id,
                    record.conversion_settings.policy_version,
                    record.settings_fingerprint,
                    record.conversion_settings.max_passage_characters,
                    record.markdown,
                    record.markdown_sha256,
                    created_at,
                    record.updated_at,
                ),
            )

            for passage, passage_provenance in zip(
                record.passages, record.passage_provenance, strict=True
            ):
                conn.execute(
                    """INSERT INTO passages (
                        passage_id, paper_id, text, section_heading,
                        page_start, page_end, ordinal_position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        passage.passage_id,
                        passage.paper_id,
                        passage.text,
                        passage.section_heading,
                        passage.page_start,
                        passage.page_end,
                        passage.ordinal_position,
                    ),
                )
                conn.execute(
                    """INSERT INTO passage_provenance (
                        passage_id, paper_id, section_kind, ordinal_position
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        passage_provenance.passage_id,
                        paper.paper_id,
                        passage_provenance.section_kind.value,
                        passage.ordinal_position,
                    ),
                )
                for fragment_ordinal, fragment in enumerate(
                    passage_provenance.fragments
                ):
                    conn.execute(
                        """INSERT INTO passage_source_fragments (
                            passage_id, ordinal_position, page_number,
                            start_character_offset, end_character_offset,
                            passage_start_character_offset,
                            passage_end_character_offset, source_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            passage.passage_id,
                            fragment_ordinal,
                            fragment.page_number,
                            fragment.start_character_offset,
                            fragment.end_character_offset,
                            fragment.passage_start_character_offset,
                            fragment.passage_end_character_offset,
                            fragment.source_text,
                        ),
                    )

            conn.execute(
                """INSERT INTO ingestion_completions (
                    paper_id, status, completed_at, passage_count,
                    warning_count, error_message
                ) VALUES (?, 'completed', ?, ?, 0, NULL)""",
                (paper.paper_id, record.updated_at, len(record.passages)),
            )

            expected = dataclasses.replace(
                record,
                source_provenance=dataclasses.replace(
                    record.source_provenance, created_at=created_at
                ),
                created_at=created_at,
            )
            read_back = self.get_early_section_record(paper.paper_id)
            if read_back != expected:
                raise StorageValidationError(
                    f"Strict read-back mismatch for early-section record '{paper.paper_id}'."
                )
            if not self._coordinated_transaction_active:
                conn.commit()
        except Exception as error:
            conn.rollback()
            if isinstance(error, sqlite3.Error):
                raise StorageTransactionError(
                    f"Failed to save early-section record '{paper.paper_id}': {error}."
                ) from error
            raise

    def get_early_section_record(
        self, paper_id: str
    ) -> EarlySectionLibraryRecord | None:
        """Retrieve and strictly validate an early-section record."""
        conn = self._ensure_initialized()
        try:
            early_row = conn.execute(
                "SELECT * FROM early_section_records WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if early_row is None:
                return None
            paper_storage_row = conn.execute(
                """SELECT content_checksum, created_at, updated_at
                   FROM papers WHERE paper_id = ?""",
                (paper_id,),
            ).fetchone()
            if paper_storage_row is None:
                raise StorageValidationError(
                    f"Missing paper row for early-section record '{paper_id}'."
                )
            if (
                paper_storage_row["created_at"] != early_row["created_at"]
                or paper_storage_row["updated_at"] != early_row["updated_at"]
            ):
                raise StorageValidationError(
                    f"Timestamp rows disagree for early-section record '{paper_id}'."
                )
            paper = self.get_paper(paper_id)
            if paper is None:
                raise StorageValidationError(
                    f"Missing paper row for early-section record '{paper_id}'."
                )
            source_row = conn.execute(
                "SELECT * FROM source_provenance WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if source_row is None:
                raise StorageValidationError(
                    f"Missing source provenance for early-section record '{paper_id}'."
                )
            if paper_storage_row["content_checksum"] != source_row["content_checksum"]:
                raise StorageValidationError(
                    f"Checksum rows disagree for early-section record '{paper_id}'."
                )
            settings_row = conn.execute(
                "SELECT * FROM conversion_settings WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if settings_row is None:
                raise StorageValidationError(
                    f"Missing conversion settings for early-section record '{paper_id}'."
                )
            generic_parameters = json.loads(settings_row["parameters_json"])
            if (
                settings_row["conversion_version"]
                != early_row["conversion_policy_version"]
                or settings_row["ocr_enabled"] != 0
                or generic_parameters
                != {"max_passage_characters": early_row["max_passage_characters"]}
            ):
                raise StorageValidationError(
                    f"Conversion settings rows disagree for early-section record '{paper_id}'."
                )
            source_provenance = SourceProvenance(
                source_path=source_row["source_path"],
                source_format=source_row["source_format"],
                source_file_size=source_row["source_file_size"],
                content_checksum=source_row["content_checksum"],
                markdown_path=source_row["markdown_path"],
                extraction_method=source_row["extraction_method"],
                created_at=source_row["created_at"],
            )
            passages = self.get_passages(paper_id)
            if not passages:
                raise StorageValidationError(
                    f"Missing passage rows for early-section record '{paper_id}'."
                )
            provenance_rows = conn.execute(
                """SELECT * FROM passage_provenance
                   WHERE paper_id = ? ORDER BY ordinal_position ASC""",
                (paper_id,),
            ).fetchall()
            if not provenance_rows:
                raise StorageValidationError(
                    f"Missing passage provenance rows for early-section record '{paper_id}'."
                )
            stored_provenance = []
            for expected_ordinal, provenance_row in enumerate(provenance_rows):
                if provenance_row["ordinal_position"] != expected_ordinal:
                    raise StorageValidationError(
                        f"Passage provenance ordinals are not contiguous for '{paper_id}'."
                    )
                fragment_rows = conn.execute(
                    """SELECT * FROM passage_source_fragments
                       WHERE passage_id = ? ORDER BY ordinal_position ASC""",
                    (provenance_row["passage_id"],),
                ).fetchall()
                for fragment_ordinal, fragment_row in enumerate(fragment_rows):
                    if fragment_row["ordinal_position"] != fragment_ordinal:
                        raise StorageValidationError(
                            "Passage source fragment ordinals are not contiguous for "
                            f"'{provenance_row['passage_id']}'."
                        )
                stored_provenance.append(
                    StoredPassageProvenance(
                        passage_id=provenance_row["passage_id"],
                        section_kind=PDFSectionKind(provenance_row["section_kind"]),
                        fragments=tuple(
                            StoredPassageSourceFragment(
                                page_number=row["page_number"],
                                start_character_offset=row["start_character_offset"],
                                end_character_offset=row["end_character_offset"],
                                passage_start_character_offset=row[
                                    "passage_start_character_offset"
                                ],
                                passage_end_character_offset=row[
                                    "passage_end_character_offset"
                                ],
                                source_text=row["source_text"],
                            )
                            for row in fragment_rows
                        ),
                    )
                )
            return EarlySectionLibraryRecord(
                paper=paper,
                source_provenance=source_provenance,
                parser_version=source_row["parser_version"],
                conversion_settings=PDFConversionSettings(
                    policy_version=early_row["conversion_policy_version"],
                    max_passage_characters=early_row["max_passage_characters"],
                ),
                settings_fingerprint=early_row["settings_fingerprint"],
                markdown=early_row["markdown"],
                markdown_sha256=early_row["markdown_sha256"],
                passages=passages,
                passage_provenance=tuple(stored_provenance),
                created_at=early_row["created_at"],
                updated_at=early_row["updated_at"],
            )
        except StorageValidationError:
            raise
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise StorageValidationError(
                f"Failed to load early-section record '{paper_id}': {error}."
            ) from error

    def list_early_section_records(self) -> tuple[EarlySectionLibraryRecord, ...]:
        """Return early-section records ordered by paper_id."""
        conn = self._ensure_initialized()
        try:
            rows = conn.execute(
                "SELECT paper_id FROM early_section_records ORDER BY paper_id ASC"
            ).fetchall()
        except sqlite3.Error as error:
            raise StorageValidationError(
                f"Failed to list early-section records: {error}."
            ) from error
        records = tuple(self.get_early_section_record(row["paper_id"]) for row in rows)
        if any(record is None for record in records):
            raise StorageValidationError(
                "An early-section record disappeared during list reconstruction."
            )
        return tuple(record for record in records if record is not None)

    def save_analysis_and_early_section(
        self,
        analysis: SinglePaperAnalysisRecord,
        library: EarlySectionLibraryRecord,
    ) -> None:
        """Persist complete analysis and library records in one transaction."""
        if not isinstance(analysis, SinglePaperAnalysisRecord):
            raise TypeError("analysis must be a SinglePaperAnalysisRecord instance.")
        if not isinstance(library, EarlySectionLibraryRecord):
            raise TypeError("library must be an EarlySectionLibraryRecord instance.")
        if analysis.content_checksum != library.source_provenance.content_checksum:
            raise StorageValidationError(
                "analysis and library records must have the same content checksum."
            )
        conn = self._ensure_initialized()
        if self._coordinated_transaction_active:
            raise StorageTransactionError(
                "A coordinated transaction is already active."
            )
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._coordinated_transaction_active = True
            self.save_single_paper_analysis(analysis)
            self.save_early_section_record(library)
            durable_analysis = self.get_single_paper_analysis(analysis.analysis_id)
            durable_library = self.get_early_section_record(library.paper.paper_id)
            if durable_analysis != analysis or durable_library is None:
                raise StorageValidationError(
                    "Strict coordinated read-back did not match prepared records."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._coordinated_transaction_active = False

    def delete_early_section_record(self, paper_id: str) -> bool:
        """Delete an early-section record and its shared paper representation."""
        conn = self._ensure_initialized()
        try:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM early_section_records WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if exists is None:
                conn.commit()
                return False
            conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
            conn.commit()
            return True
        except Exception as error:
            conn.rollback()
            if isinstance(error, sqlite3.Error):
                raise StorageTransactionError(
                    f"Failed to delete early-section record '{paper_id}': {error}."
                ) from error
            raise

    def save_single_paper_analysis(self, record: SinglePaperAnalysisRecord) -> None:
        """Persist or replace a single-paper analysis record in a single transaction."""
        conn = self._ensure_initialized()

        chk = record.content_checksum.lower() if record.content_checksum else None

        try:
            if not self._coordinated_transaction_active:
                conn.execute("BEGIN IMMEDIATE")

            # Check if record already exists to preserve created_at / updated_at when equivalent
            cur_ex = conn.execute(
                "SELECT created_at, updated_at FROM single_paper_analyses WHERE analysis_id = ?",
                (record.analysis_id,),
            )
            ex_row = cur_ex.fetchone()
            c_at = ex_row["created_at"] if ex_row else record.created_at
            u_at = record.updated_at

            if ex_row is not None:
                ex_rec = self.get_single_paper_analysis(record.analysis_id)
                if ex_rec is not None:
                    rec_dict = dataclasses.asdict(record)
                    ex_dict = dataclasses.asdict(ex_rec)
                    rec_dict["updated_at"] = ""
                    ex_dict["updated_at"] = ""
                    if rec_dict == ex_dict:
                        u_at = ex_row["updated_at"]

            # Delete existing analysis record if updating (cascades to related child tables)
            conn.execute(
                "DELETE FROM single_paper_analyses WHERE analysis_id = ?",
                (record.analysis_id,),
            )

            # Insert into single_paper_analyses
            conn.execute(
                """INSERT INTO single_paper_analyses (
                    analysis_id, content_checksum, source_path, policy_version,
                    status, failed_stage, failure_code, error_message,
                    completed_stages_json, skipped_stages_json, quality_status,
                    quality_settings_json, section_settings_json, research_question_settings_json,
                    settings_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.analysis_id,
                    chk,
                    str(record.source_path),
                    record.settings.policy_version,
                    record.status.value,
                    record.failed_stage.value if record.failed_stage else None,
                    record.failure_code.value if record.failure_code else None,
                    record.error_message,
                    json.dumps([s.value for s in record.completed_stages]),
                    json.dumps([s.value for s in record.skipped_stages]),
                    record.quality_status.value if record.quality_status else None,
                    json.dumps(dataclasses.asdict(record.settings.quality_settings)),
                    json.dumps(dataclasses.asdict(record.settings.section_settings)),
                    json.dumps(
                        dataclasses.asdict(record.settings.research_question_settings)
                    ),
                    record.settings_fingerprint,
                    c_at,
                    u_at,
                ),
            )

            # Insert into single_paper_analysis_warnings
            for warning in record.quality_warnings:
                conn.execute(
                    """INSERT INTO single_paper_analysis_warnings (
                        analysis_id, warning_domain, warning_code, details, page_numbers_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        "quality",
                        warning.code.value,
                        warning.message,
                        json.dumps(warning.page_numbers),
                        c_at,
                    ),
                )
            for warning in record.section_warnings:
                conn.execute(
                    """INSERT INTO single_paper_analysis_warnings (
                        analysis_id, warning_domain, warning_code, details, page_numbers_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        "section",
                        warning.code.value,
                        None,
                        json.dumps(warning.page_numbers)
                        if warning.page_numbers
                        else None,
                        c_at,
                    ),
                )
            for warning in record.research_question_warnings:
                conn.execute(
                    """INSERT INTO single_paper_analysis_warnings (
                        analysis_id, warning_domain, warning_code, details, page_numbers_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        "research_question",
                        warning.code.value,
                        warning.details,
                        None,
                        c_at,
                    ),
                )
            for warning in record.warnings:
                conn.execute(
                    """INSERT INTO single_paper_analysis_warnings (
                        analysis_id, warning_domain, warning_code, details, page_numbers_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        "orchestration",
                        warning.code.value,
                        warning.details,
                        None,
                        c_at,
                    ),
                )

            # Insert into single_paper_analysis_sections and section_spans
            for sec in record.sections:
                conn.execute(
                    """INSERT INTO single_paper_analysis_sections (
                        analysis_id, section_kind, heading_text,
                        page_start, page_end, ordinal_position
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        sec.section_kind.value,
                        sec.heading_text,
                        sec.page_start,
                        sec.page_end,
                        sec.ordinal_position,
                    ),
                )
                for sp in sec.spans:
                    conn.execute(
                        """INSERT INTO single_paper_analysis_section_spans (
                            analysis_id, section_kind, page_number,
                            start_character_offset, end_character_offset, ordinal_position
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            record.analysis_id,
                            sec.section_kind.value,
                            sp.page_number,
                            sp.start_character_offset,
                            sp.end_character_offset,
                            sp.ordinal_position,
                        ),
                    )

            # Insert into single_paper_analysis_questions
            if record.research_question is not None:
                rq = record.research_question
                conn.execute(
                    """INSERT INTO single_paper_analysis_questions (
                        analysis_id, question_text, kind, sections_used_json
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        rq.question_text,
                        rq.kind.value,
                        json.dumps([sk.value for sk in rq.sections_used]),
                    ),
                )

            # Insert into single_paper_analysis_evidence
            for ev in record.evidence:
                conn.execute(
                    """INSERT INTO single_paper_analysis_evidence (
                        analysis_id, section_kind, excerpt_text, page_number,
                        start_character_offset, end_character_offset, ordinal_position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.analysis_id,
                        ev.section_kind.value,
                        ev.excerpt_text,
                        ev.page_number,
                        ev.start_character_offset,
                        ev.end_character_offset,
                        ev.ordinal_position,
                    ),
                )

            if not self._coordinated_transaction_active:
                conn.commit()
        except Exception as err:
            conn.rollback()
            if isinstance(err, sqlite3.Error):
                raise StorageTransactionError(
                    f"Failed to save single paper analysis '{record.analysis_id}': {err}."
                ) from err
            raise

    def get_single_paper_analysis(
        self, analysis_id: str
    ) -> SinglePaperAnalysisRecord | None:
        """Retrieve a single-paper analysis record by analysis_id."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute(
                "SELECT * FROM single_paper_analyses WHERE analysis_id = ?",
                (analysis_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            completed_stages = tuple(
                SinglePaperAnalysisStage(s)
                for s in json.loads(row["completed_stages_json"])
            )
            skipped_stages = tuple(
                SinglePaperAnalysisStage(s)
                for s in json.loads(row["skipped_stages_json"])
            )
            failed_stage = (
                SinglePaperAnalysisStage(row["failed_stage"])
                if row["failed_stage"]
                else None
            )
            failure_code = (
                SinglePaperAnalysisFailureCode(row["failure_code"])
                if row["failure_code"]
                else None
            )
            quality_status = (
                PDFQualityStatus(row["quality_status"])
                if row["quality_status"]
                else None
            )

            # Reconstruct settings
            q_dict = json.loads(row["quality_settings_json"])
            sec_dict = json.loads(row["section_settings_json"])
            rq_dict = json.loads(row["research_question_settings_json"])

            settings = SinglePaperAnalysisSettings(
                policy_version=row["policy_version"],
                quality_settings=PDFQualitySettings(**q_dict),
                section_settings=PDFSectionSettings(**sec_dict),
                research_question_settings=ResearchQuestionSettings(**rq_dict),
            )

            # Warnings
            cur_w = conn.execute(
                "SELECT * FROM single_paper_analysis_warnings WHERE analysis_id = ? ORDER BY id ASC",
                (analysis_id,),
            )
            w_rows = cur_w.fetchall()
            q_warnings: list[PDFQualityWarning] = []
            s_warnings: list[PDFSectionWarning] = []
            rq_warnings: list[ResearchQuestionWarning] = []
            o_warnings: list[SinglePaperAnalysisWarning] = []
            for w in w_rows:
                domain = w["warning_domain"]
                code_val = w["warning_code"]
                if domain == "quality":
                    pgs = (
                        tuple(json.loads(w["page_numbers_json"]))
                        if w["page_numbers_json"]
                        else ()
                    )
                    q_warnings.append(
                        PDFQualityWarning(
                            code=PDFQualityWarningCode(code_val), page_numbers=pgs
                        )
                    )
                elif domain == "section":
                    pgs = (
                        tuple(json.loads(w["page_numbers_json"]))
                        if w["page_numbers_json"]
                        else ()
                    )
                    s_warnings.append(
                        PDFSectionWarning(
                            code=PDFSectionWarningCode(code_val), page_numbers=pgs
                        )
                    )
                elif domain == "research_question":
                    rq_warnings.append(
                        ResearchQuestionWarning(
                            code=ResearchQuestionWarningCode(code_val),
                            details=w["details"],
                        )
                    )
                elif domain == "orchestration":
                    o_warnings.append(
                        SinglePaperAnalysisWarning(
                            code=SinglePaperAnalysisWarningCode(code_val),
                            details=w["details"],
                        )
                    )

            # Sections and Section Spans
            cur_s = conn.execute(
                "SELECT * FROM single_paper_analysis_sections WHERE analysis_id = ? ORDER BY ordinal_position ASC",
                (analysis_id,),
            )
            sec_rows = cur_s.fetchall()
            sections_list: list[SinglePaperAnalysisSectionRecord] = []
            for s in sec_rows:
                sk = s["section_kind"]
                cur_sp = conn.execute(
                    "SELECT * FROM single_paper_analysis_section_spans WHERE analysis_id = ? AND section_kind = ? ORDER BY ordinal_position ASC",
                    (analysis_id, sk),
                )
                spans = tuple(
                    SinglePaperAnalysisSectionSpanRecord(
                        page_number=sp["page_number"],
                        start_character_offset=sp["start_character_offset"],
                        end_character_offset=sp["end_character_offset"],
                        ordinal_position=sp["ordinal_position"],
                    )
                    for sp in cur_sp.fetchall()
                )
                sections_list.append(
                    SinglePaperAnalysisSectionRecord(
                        section_kind=PDFSectionKind(sk),
                        heading_text=s["heading_text"],
                        page_start=s["page_start"],
                        page_end=s["page_end"],
                        spans=spans,
                        ordinal_position=s["ordinal_position"],
                    )
                )

            # Question
            cur_q = conn.execute(
                "SELECT * FROM single_paper_analysis_questions WHERE analysis_id = ?",
                (analysis_id,),
            )
            row_q = cur_q.fetchone()
            rq_record: SinglePaperAnalysisQuestionRecord | None = None
            if row_q is not None:
                rq_record = SinglePaperAnalysisQuestionRecord(
                    kind=ResearchQuestionKind(row_q["kind"]),
                    question_text=row_q["question_text"],
                    sections_used=tuple(
                        PDFSectionKind(sk)
                        for sk in json.loads(row_q["sections_used_json"])
                    ),
                )

            # Evidence
            cur_ev = conn.execute(
                "SELECT * FROM single_paper_analysis_evidence WHERE analysis_id = ? ORDER BY ordinal_position ASC",
                (analysis_id,),
            )
            evidence = tuple(
                SinglePaperAnalysisEvidenceRecord(
                    section_kind=PDFSectionKind(ev["section_kind"]),
                    excerpt_text=ev["excerpt_text"],
                    page_number=ev["page_number"],
                    start_character_offset=ev["start_character_offset"],
                    end_character_offset=ev["end_character_offset"],
                    ordinal_position=ev["ordinal_position"],
                )
                for ev in cur_ev.fetchall()
            )

            return SinglePaperAnalysisRecord(
                analysis_id=row["analysis_id"],
                source_path=Path(row["source_path"]),
                content_checksum=row["content_checksum"],
                status=SinglePaperAnalysisStatus(row["status"]),
                completed_stages=completed_stages,
                failed_stage=failed_stage,
                skipped_stages=skipped_stages,
                failure_code=failure_code,
                error_message=row["error_message"],
                quality_status=quality_status,
                settings=settings,
                settings_fingerprint=row["settings_fingerprint"],
                quality_warnings=tuple(q_warnings),
                section_warnings=tuple(s_warnings),
                research_question_warnings=tuple(rq_warnings),
                warnings=tuple(o_warnings),
                sections=tuple(sections_list),
                research_question=rq_record,
                evidence=evidence,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (sqlite3.Error, json.JSONDecodeError, ValueError, KeyError) as err:
            raise StorageValidationError(
                f"Failed to load SinglePaperAnalysisRecord for '{analysis_id}': {err}."
            ) from err

    def get_single_paper_analysis_by_checksum(
        self, checksum: str, settings_fingerprint: str | None = None
    ) -> SinglePaperAnalysisRecord | None:
        """Retrieve a single-paper analysis record by content checksum."""
        conn = self._ensure_initialized()
        try:
            if settings_fingerprint is not None:
                cur = conn.execute(
                    "SELECT analysis_id FROM single_paper_analyses WHERE LOWER(content_checksum) = LOWER(?) AND settings_fingerprint = ? ORDER BY updated_at DESC",
                    (checksum, settings_fingerprint),
                )
            else:
                cur = conn.execute(
                    "SELECT analysis_id FROM single_paper_analyses WHERE LOWER(content_checksum) = LOWER(?) ORDER BY updated_at DESC",
                    (checksum,),
                )
            row = cur.fetchone()
            if row is None:
                return None
            return self.get_single_paper_analysis(row["analysis_id"])
        except sqlite3.Error as err:
            raise StorageValidationError(
                f"Failed to query single paper analysis by checksum '{checksum}': {err}."
            ) from err

    def list_single_paper_analyses(self) -> tuple[SinglePaperAnalysisRecord, ...]:
        """Return all stored single-paper analysis records ordered by analysis_id."""
        conn = self._ensure_initialized()
        try:
            cur = conn.execute(
                "SELECT analysis_id FROM single_paper_analyses ORDER BY analysis_id ASC"
            )
            rows = cur.fetchall()
            records = []
            for r in rows:
                rec = self.get_single_paper_analysis(r["analysis_id"])
                if rec is not None:
                    records.append(rec)
            return tuple(records)
        except sqlite3.Error as err:
            raise StorageConnectionError(
                f"Failed to list single paper analyses: {err}."
            ) from err

    def delete_single_paper_analysis(self, analysis_id: str) -> bool:
        """Delete single-paper analysis by analysis_id."""
        conn = self._ensure_initialized()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "DELETE FROM single_paper_analyses WHERE analysis_id = ?",
                (analysis_id,),
            )
            count = cur.rowcount
            conn.commit()
            return count > 0
        except Exception as err:
            conn.rollback()
            if isinstance(err, sqlite3.Error):
                raise StorageTransactionError(
                    f"Failed to delete single paper analysis '{analysis_id}': {err}."
                ) from err
            raise
