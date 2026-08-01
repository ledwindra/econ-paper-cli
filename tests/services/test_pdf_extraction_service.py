"""Unit tests for protocol-only PDF extraction orchestration."""

from pathlib import Path

from econ_paper_cli.domain import (
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
)
from econ_paper_cli.services import extract_pdf


class RecordingPDFExtractor:
    def __init__(self, result: PDFExtractionResult) -> None:
        self.result = result
        self.calls: list[Path] = []

    def extract(self, source_path: Path) -> PDFExtractionResult:
        self.calls.append(source_path)
        return self.result


def test_extract_pdf_delegates_only_through_injected_protocol(tmp_path: Path) -> None:
    source_path = tmp_path / "paper.pdf"
    expected = PDFExtractionResult(
        source_path=source_path,
        pages=(ExtractedPDFPage(page_number=1, text="Synthetic text\n"),),
        page_count=1,
        metadata=PDFDocumentMetadata(title="Synthetic paper"),
        extraction_method="fake-pdf",
        parser_version="1.0",
    )
    extractor = RecordingPDFExtractor(expected)

    actual = extract_pdf(str(source_path), extractor=extractor)

    assert actual is expected
    assert extractor.calls == [source_path]
