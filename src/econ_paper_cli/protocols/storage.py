"""Replaceable storage backend protocol and storage error types."""

from typing import Protocol, runtime_checkable

from econ_paper_cli.domain.corpora import Corpus
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage
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

    def count_papers(self) -> int:
        """Return total count of stored papers."""
        ...

    def count_passages(self) -> int:
        """Return total count of stored passages across all papers."""
        ...
