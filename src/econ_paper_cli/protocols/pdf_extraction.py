"""Replaceable PDF extraction protocol and actionable error boundary."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from econ_paper_cli.domain.errors import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFMalformedError,
    PDFParserError,
    PDFPermissionError,
    PDFReadError,
    PDFSourceNotFoundError,
    PDFSourceNotRegularFileError,
)
from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult

__all__ = [
    "PDFEncryptedError",
    "PDFExtractionError",
    "PDFExtractor",
    "PDFMalformedError",
    "PDFParserError",
    "PDFPermissionError",
    "PDFReadError",
    "PDFSourceNotFoundError",
    "PDFSourceNotRegularFileError",
]


@runtime_checkable
class PDFExtractor(Protocol):
    """Replaceable interface for offline structured PDF extraction."""

    def extract(self, source_path: Path) -> PDFExtractionResult:
        """Extract ordered page text, raw metadata, and parser provenance."""
        ...
