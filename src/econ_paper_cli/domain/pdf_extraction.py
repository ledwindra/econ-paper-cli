"""Immutable domain models for structured PDF extraction results."""

from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.domain.errors import PDFExtractionValidationError


@dataclass(frozen=True, slots=True)
class ExtractedPDFPage:
    """Text extracted from one source page with its stable 1-based number."""

    page_number: int
    text: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_number, bool)
            or not isinstance(self.page_number, int)
            or self.page_number < 1
        ):
            raise PDFExtractionValidationError(
                "page_number must be a positive integer (>= 1)."
            )
        if not isinstance(self.text, str):
            raise PDFExtractionValidationError("text must be a string.")


@dataclass(frozen=True, slots=True)
class PDFDocumentMetadata:
    """Optional raw document-information strings reported by a PDF parser."""

    title: str | None = None
    author_text: str | None = None
    subject: str | None = None
    keywords: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: str | None = None
    modification_date: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("title", self.title),
            ("author_text", self.author_text),
            ("subject", self.subject),
            ("keywords", self.keywords),
            ("creator", self.creator),
            ("producer", self.producer),
            ("creation_date", self.creation_date),
            ("modification_date", self.modification_date),
        ):
            if value is not None and not isinstance(value, str):
                raise PDFExtractionValidationError(
                    f"{field_name} must be a string or None."
                )


@dataclass(frozen=True, slots=True)
class PDFExtractionResult:
    """Structured text, metadata, and provenance extracted from one PDF."""

    source_path: Path
    pages: tuple[ExtractedPDFPage, ...]
    page_count: int
    metadata: PDFDocumentMetadata
    extraction_method: str
    parser_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, Path):
            raise PDFExtractionValidationError("source_path must be a pathlib.Path.")
        if not self.source_path.is_absolute():
            raise PDFExtractionValidationError(
                "source_path must be an absolute canonical path."
            )
        if not isinstance(self.pages, tuple) or not all(
            isinstance(page, ExtractedPDFPage) for page in self.pages
        ):
            raise PDFExtractionValidationError(
                "pages must be a tuple of ExtractedPDFPage instances."
            )
        if (
            isinstance(self.page_count, bool)
            or not isinstance(self.page_count, int)
            or self.page_count < 0
        ):
            raise PDFExtractionValidationError(
                "page_count must be a non-negative integer."
            )
        if self.page_count != len(self.pages):
            raise PDFExtractionValidationError(
                f"page_count ({self.page_count}) does not match pages length "
                f"({len(self.pages)})."
            )
        expected_page_numbers = tuple(range(1, self.page_count + 1))
        actual_page_numbers = tuple(page.page_number for page in self.pages)
        if actual_page_numbers != expected_page_numbers:
            raise PDFExtractionValidationError(
                "pages must have contiguous 1-based page numbers in source order."
            )
        if not isinstance(self.metadata, PDFDocumentMetadata):
            raise PDFExtractionValidationError(
                "metadata must be a PDFDocumentMetadata instance."
            )
        for field_name, value in (
            ("extraction_method", self.extraction_method),
            ("parser_version", self.parser_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PDFExtractionValidationError(
                    f"{field_name} must be a non-empty string."
                )
