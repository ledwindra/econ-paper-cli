"""Replaceable PDF extraction protocol and actionable error boundary."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult


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


@runtime_checkable
class PDFExtractor(Protocol):
    """Replaceable interface for offline structured PDF extraction."""

    def extract(self, source_path: Path) -> PDFExtractionResult:
        """Extract ordered page text, raw metadata, and parser provenance."""
        ...
