"""Application-facing orchestration for replaceable PDF extraction."""

from pathlib import Path

from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor


def extract_pdf(
    source_path: str | Path,
    *,
    extractor: PDFExtractor,
) -> PDFExtractionResult:
    """Extract one PDF through the explicitly injected extractor protocol."""
    path = Path(source_path) if isinstance(source_path, str) else source_path
    return extractor.extract(path)
