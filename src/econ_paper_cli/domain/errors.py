"""Domain error types for paper, passage, evidence, and citation validation."""

from pathlib import Path


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


class PDFExtractionError(Exception):
    """Base exception for trustworthy local PDF extraction failures."""


class PDFSourceNotFoundError(PDFExtractionError):
    """Raised when the requested PDF source does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"PDF source does not exist at '{path}'.")
        self.path = path


class PDFSourceNotRegularFileError(PDFExtractionError):
    """Raised when the requested PDF source is not a regular file."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"PDF source is not a regular file: '{path}'.")
        self.path = path


class PDFPermissionError(PDFExtractionError):
    """Raised when the source PDF cannot be accessed due to permissions."""

    def __init__(self, path: Path, error: PermissionError) -> None:
        super().__init__(f"Permission denied reading PDF source '{path}': {error}.")
        self.path = path
        self.error = error


class PDFReadError(PDFExtractionError):
    """Raised when an operating-system read failure prevents extraction."""

    def __init__(self, path: Path, error: OSError) -> None:
        super().__init__(
            f"Operating-system error reading PDF source '{path}': {error}."
        )
        self.path = path
        self.error = error


class PDFMalformedError(PDFExtractionError):
    """Raised when a malformed or truncated PDF cannot be trusted."""

    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(f"PDF source is malformed or truncated: '{path}': {error}.")
        self.path = path
        self.error = error


class PDFEncryptedError(PDFExtractionError):
    """Raised when an encrypted PDF requires a non-empty password."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"PDF source is encrypted and cannot be opened without a password: '{path}'."
        )
        self.path = path


class PDFParserError(PDFExtractionError):
    """Raised when the parser cannot produce a trustworthy structured result."""

    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(f"PDF parser failed for '{path}': {error}.")
        self.path = path
        self.error = error


class PDFExtractionValidationError(DomainError):
    """Raised when a PDF extraction domain object fails validation."""


class PDFQualityValidationError(DomainError):
    """Raised when a PDF extraction-quality domain object fails validation."""


class PDFSectionValidationError(DomainError):
    """Raised when a PDF section-detection domain object fails validation."""


class PDFConversionValidationError(DomainError):
    """Raised when an early-section PDF conversion contract is invalid."""


class EarlySectionLibraryValidationError(DomainError):
    """Raised when an early-section library record or projection is invalid."""


class ResearchQuestionValidationError(DomainError):
    """Raised when a research-question domain object fails validation."""


class SinglePaperAnalysisValidationError(DomainError):
    """Raised when a single-paper analysis domain object fails validation."""
