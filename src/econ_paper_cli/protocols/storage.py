"""Replaceable storage backend protocol and storage error types."""

from typing import Protocol, runtime_checkable

from econ_paper_cli.domain.corpora import Corpus
from econ_paper_cli.domain.early_section_library import EarlySectionLibraryRecord
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.pdf_conversion import PDFConversionSettings
from econ_paper_cli.domain.single_paper_analysis import SinglePaperAnalysisRecord
from econ_paper_cli.domain.storage import PaperRecord


class StorageError(Exception):
    """Base exception for all storage protocol operations."""


class StorageConnectionError(StorageError):
    """Raised when opening or connecting to storage fails."""


class StorageTransactionError(StorageError):
    """Raised when a storage transaction fails or rolls back."""


class StorageMigrationError(StorageError):
    """Raised when database schema versioning or migration fails."""


class StorageIncompatibleSchemaError(StorageMigrationError):
    """Raised when opening a database with a schema version newer than supported."""


class StorageValidationError(StorageError):
    """Raised when data retrieved from storage violates validation contracts."""


class ChecksumConflictError(StorageError):
    """Raised when content checksum conflicts with an existing different paper_id."""


@runtime_checkable
class StorageBackend(Protocol):
    """Replaceable storage backend interface for paper library data."""

    def initialize(self) -> None:
        """Initialize connection, tables, and forward migrations."""
        ...

    def close(self) -> None:
        """Close active database connection and release resources."""
        ...

    def get_schema_version(self) -> int:
        """Return the current database schema version."""
        ...

    def save_paper_record(self, record: PaperRecord) -> None:
        """Persist or replace a paper record in a single atomic transaction."""
        ...

    def get_paper_record(self, paper_id: str) -> PaperRecord | None:
        """Retrieve a full PaperRecord by paper_id."""
        ...

    def get_paper_record_by_checksum(self, checksum: str) -> PaperRecord | None:
        """Retrieve a full PaperRecord by content_checksum."""
        ...

    def get_paper(self, paper_id: str) -> Paper | None:
        """Retrieve Paper bibliographic metadata by paper_id."""
        ...

    def get_passages(self, paper_id: str) -> tuple[Passage, ...]:
        """Retrieve all Passages for paper_id ordered by ordinal position."""
        ...

    def load_corpus(self, corpus_id: str = "local-library") -> Corpus:
        """Reconstruct and return a validated Corpus from stored paper and passage data."""
        ...

    def list_paper_ids(self) -> tuple[str, ...]:
        """Return a tuple of all stored paper_id identifiers sorted alphabetically."""
        ...

    def list_paper_records(self) -> tuple[PaperRecord, ...]:
        """Return all stored PaperRecords ordered by paper_id."""
        ...

    def delete_paper_record(self, paper_id: str) -> bool:
        """Delete paper_id and all associated data in a single transaction."""
        ...

    def save_early_section_record(self, record: EarlySectionLibraryRecord) -> None:
        """Persist or replace a complete early-section record atomically."""
        ...

    def save_analysis_and_early_section(
        self,
        analysis: SinglePaperAnalysisRecord,
        library: EarlySectionLibraryRecord,
    ) -> None:
        """Persist complete analysis and library records in one transaction."""
        ...

    def get_early_section_record(
        self, paper_id: str, settings: PDFConversionSettings | None = None
    ) -> EarlySectionLibraryRecord | None:
        """Retrieve and strictly validate an early-section record.

        When ``settings`` is given, a stored record whose settings
        fingerprint does not match the requested conversion settings is
        reported as absent (``None``) rather than returned for reuse — this
        is how a policy bump (conversion *or* section detection) invalidates
        stale library rows. Application code depends on this parameter, so
        it is part of the protocol, not an adapter-specific extension.
        """
        ...

    def list_early_section_records(self) -> tuple[EarlySectionLibraryRecord, ...]:
        """Return early-section records ordered by paper_id."""
        ...

    def delete_early_section_record(self, paper_id: str) -> bool:
        """Delete an early-section record and its shared paper representation."""
        ...

    def count_papers(self) -> int:
        """Return total count of stored papers."""
        ...

    def count_passages(self) -> int:
        """Return total count of stored passages across all papers."""
        ...

    def save_single_paper_analysis(self, record: SinglePaperAnalysisRecord) -> None:
        """Persist or replace a single-paper analysis record in a single transaction."""
        ...

    def get_single_paper_analysis(
        self, analysis_id: str
    ) -> SinglePaperAnalysisRecord | None:
        """Retrieve a single-paper analysis record by analysis_id."""
        ...

    def get_single_paper_analysis_by_checksum(
        self, checksum: str, settings_fingerprint: str | None = None
    ) -> SinglePaperAnalysisRecord | None:
        """Retrieve a single-paper analysis record by content checksum and optional settings fingerprint."""
        ...

    def list_single_paper_analyses(self) -> tuple[SinglePaperAnalysisRecord, ...]:
        """Return all stored single-paper analysis records."""
        ...

    def delete_single_paper_analysis(self, analysis_id: str) -> bool:
        """Delete a single-paper analysis record by analysis_id."""
        ...
