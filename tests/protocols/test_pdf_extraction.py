"""Tests for the replaceable PDF extraction protocol."""

from pathlib import Path

from econ_paper_cli.domain import PDFDocumentMetadata, PDFExtractionResult
from econ_paper_cli.protocols import PDFExtractor


class FakePDFExtractor:
    def extract(self, source_path: Path) -> PDFExtractionResult:
        return PDFExtractionResult(
            source_path=source_path,
            pages=(),
            page_count=0,
            metadata=PDFDocumentMetadata(),
            extraction_method="fake-pdf",
            parser_version="1.0",
        )


def test_pdf_extractor_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakePDFExtractor(), PDFExtractor)
