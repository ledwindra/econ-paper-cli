"""Unit tests for immutable PDF extraction domain models."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFExtractionValidationError,
)

CANONICAL_SOURCE_PATH = Path.cwd().resolve() / "paper.pdf"


def _page(page_number: int = 1, text: str = "Page text\n") -> ExtractedPDFPage:
    return ExtractedPDFPage(page_number=page_number, text=text)


def _result(**overrides: object) -> PDFExtractionResult:
    values: dict[str, object] = {
        "source_path": CANONICAL_SOURCE_PATH,
        "pages": (_page(),),
        "page_count": 1,
        "metadata": PDFDocumentMetadata(),
        "extraction_method": "fake-pdf",
        "parser_version": "1.2.3",
    }
    values.update(overrides)
    return PDFExtractionResult(**values)  # type: ignore[arg-type]


def test_pdf_document_metadata_preserves_optional_raw_strings() -> None:
    metadata = PDFDocumentMetadata(
        title="Known title",
        author_text="Ada Lovelace; Joan Robinson",
        subject="Synthetic economics",
        keywords="trade, growth",
        creator="Fixture creator",
        producer="Fixture producer",
        creation_date="D:20260102030405Z",
        modification_date="D:20260203040506Z",
    )

    assert metadata.title == "Known title"
    assert metadata.author_text == "Ada Lovelace; Joan Robinson"
    assert metadata.creation_date == "D:20260102030405Z"


def test_pdf_document_metadata_preserves_explicit_absence() -> None:
    metadata = PDFDocumentMetadata()
    assert metadata.title is None
    assert metadata.author_text is None
    assert metadata.modification_date is None


@pytest.mark.parametrize(
    "field_name",
    [
        "title",
        "author_text",
        "subject",
        "keywords",
        "creator",
        "producer",
        "creation_date",
        "modification_date",
    ],
)
def test_pdf_document_metadata_rejects_non_string_values(field_name: str) -> None:
    with pytest.raises(PDFExtractionValidationError, match=field_name):
        PDFDocumentMetadata(**{field_name: 123})  # type: ignore[arg-type]


def test_extracted_pdf_page_allows_empty_text() -> None:
    assert ExtractedPDFPage(page_number=1, text="").text == ""


@pytest.mark.parametrize("page_number", [0, -1, True, 1.5, "1"])
def test_extracted_pdf_page_rejects_invalid_page_numbers(page_number: object) -> None:
    with pytest.raises(PDFExtractionValidationError, match="page_number"):
        ExtractedPDFPage(page_number=page_number, text="text")  # type: ignore[arg-type]


def test_extracted_pdf_page_rejects_non_string_text() -> None:
    with pytest.raises(PDFExtractionValidationError, match="text"):
        ExtractedPDFPage(page_number=1, text=None)  # type: ignore[arg-type]


def test_pdf_extraction_result_preserves_ordered_pages_and_provenance() -> None:
    pages = (_page(1, "First\n"), _page(2, "Second\n"), _page(3, ""))
    result = _result(pages=pages, page_count=3)

    assert result.pages == pages
    assert result.page_count == 3
    assert result.source_path == CANONICAL_SOURCE_PATH
    assert result.extraction_method == "fake-pdf"
    assert result.parser_version == "1.2.3"


def test_pdf_extraction_result_rejects_noncanonical_source_path() -> None:
    with pytest.raises(PDFExtractionValidationError, match="absolute"):
        _result(source_path=Path("relative/paper.pdf"))


def test_pdf_extraction_result_rejects_non_path_source() -> None:
    with pytest.raises(PDFExtractionValidationError, match="pathlib.Path"):
        _result(source_path="/tmp/paper.pdf")


def test_pdf_extraction_result_requires_page_tuple() -> None:
    with pytest.raises(PDFExtractionValidationError, match="tuple"):
        _result(pages=[_page()])


@pytest.mark.parametrize("page_count", [-1, True, 1.5, "1"])
def test_pdf_extraction_result_rejects_invalid_page_count(page_count: object) -> None:
    with pytest.raises(PDFExtractionValidationError, match="page_count"):
        _result(page_count=page_count)


def test_pdf_extraction_result_requires_count_to_match_pages() -> None:
    with pytest.raises(PDFExtractionValidationError, match="does not match"):
        _result(page_count=2)


def test_pdf_extraction_result_requires_contiguous_one_based_pages() -> None:
    pages = (_page(1), _page(3))
    with pytest.raises(PDFExtractionValidationError, match="contiguous 1-based"):
        _result(pages=pages, page_count=2)


def test_pdf_extraction_result_requires_metadata_model() -> None:
    with pytest.raises(PDFExtractionValidationError, match="metadata"):
        _result(metadata={})


@pytest.mark.parametrize("field_name", ["extraction_method", "parser_version"])
@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_pdf_extraction_result_requires_nonempty_provenance_strings(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(PDFExtractionValidationError, match=field_name):
        _result(**{field_name: value})
