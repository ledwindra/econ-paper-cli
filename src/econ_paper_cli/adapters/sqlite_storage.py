"""SQLite storage adapter implementing StorageBackend protocol."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain.corpora import Corpus
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage
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

CURRENT_SCHEMA_VERSION = 2

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
]


class SQLiteStorage(StorageBackend):
    """Local SQLite storage adapter using Python standard library sqlite3."""

    def __init__(self, db_path: str | Path | None = None) -> None:
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

        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path | str:
        """Return the target database path."""
        return self._db_path

    def initialize(self) -> None:
        """Connect to SQLite database, enable foreign keys, and run migrations."""
        if self._conn is not None:
            return

        try:
            if isinstance(self._db_path, Path):
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                conn_str = str(self._db_path)
            else:
                conn_str = self._db_path

            self._conn = sqlite3.connect(conn_str)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON;")
        except sqlite3.Error as err:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
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

                            cur = conn.execute("SELECT COUNT(*) AS c FROM papers")
                            row = cur.fetchone()
                            if row is not None and row["c"] > 0:
                                raise StorageMigrationError(
                                    f"Migration to schema version {version} failed: database contains {row['c']} existing paper record(s) missing required provenance metadata (source_file_size and markdown_path). Please rebuild the library database."
                                )

                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        for stmt in statements:
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
