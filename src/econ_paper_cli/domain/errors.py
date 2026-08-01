"""Domain error types for paper, passage, evidence, and citation validation."""


class DomainError(ValueError):
    """Base exception for all domain validation errors."""


class PaperValidationError(DomainError):
    """Raised when a Paper domain object fails validation."""


class PassageValidationError(DomainError):
    """Raised when a Passage domain object fails validation."""


class EvidenceValidationError(DomainError):
    """Raised when a RetrievalEvidence domain object fails validation."""


class CitationValidationError(DomainError):
    """Raised when a Citation domain object fails validation."""


class StorageRecordValidationError(DomainError):
    """Raised when a storage paper record or its metadata fails validation."""


class IngestionError(Exception):
    """Base exception for all ingestion operations."""


class IngestionValidationError(DomainError, IngestionError):
    """Raised when an ingestion domain object fails validation."""


class IngestionPathNotFoundError(IngestionError):
    """Raised when the specified target path does not exist."""


class IngestionInvalidPathError(IngestionError):
    """Raised when target path is not a regular file or directory."""


class IngestionUnsupportedFileError(IngestionError):
    """Raised when explicit file input is not a supported .pdf document."""


class IngestionEmptyDirectoryError(IngestionError):
    """Raised when directory search finds zero .pdf files."""


class IngestionPermissionError(IngestionError):
    """Raised when permission is denied accessing target path or candidate files."""


class IngestionReadError(IngestionError):
    """Raised when OS read error occurs during file inspection or hashing."""
