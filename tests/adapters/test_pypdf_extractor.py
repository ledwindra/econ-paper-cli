"""Tests for the fully local pypdf extraction adapter."""

from pathlib import Path
from unittest.mock import patch

import pypdf
import pytest
from pypdf import PdfWriter

from econ_paper_cli.adapters import PyPDFExtractor
from econ_paper_cli.protocols import (
    PDFEncryptedError,
    PDFMalformedError,
    PDFParserError,
    PDFPermissionError,
    PDFReadError,
    PDFSourceNotFoundError,
    PDFSourceNotRegularFileError,
)


def _pdf_string(value: str) -> bytes:
    escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({escaped})".encode("ascii")


def _synthetic_pdf_bytes(
    page_texts: tuple[str | None, ...],
    metadata: dict[str, str] | None = None,
) -> bytes:
    """Build a small repository-owned PDF fixture without another dependency."""
    page_count = len(page_texts)
    font_object_number = 3 + (2 * page_count)
    info_object_number = font_object_number + 1 if metadata is not None else None
    object_count = font_object_number + (1 if metadata is not None else 0)
    objects: list[bytes] = [b""] * object_count

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    page_references = b" ".join(
        f"{3 + (2 * index)} 0 R".encode("ascii") for index in range(page_count)
    )
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + page_references
        + f"] /Count {page_count} >>".encode("ascii")
    )

    for index, text in enumerate(page_texts):
        page_object_number = 3 + (2 * index)
        content_object_number = page_object_number + 1
        objects[page_object_number - 1] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            + b"/Resources << /Font << /F1 "
            + f"{font_object_number} 0 R".encode("ascii")
            + b" >> >> /Contents "
            + f"{content_object_number} 0 R >>".encode("ascii")
        )
        content = (
            b""
            if text is None
            else b"BT /F1 12 Tf 72 720 Td " + _pdf_string(text) + b" Tj ET"
        )
        objects[content_object_number - 1] = (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content
            + b"\nendstream"
        )

    objects[font_object_number - 1] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    )

    if metadata is not None and info_object_number is not None:
        entries = b" ".join(
            f"/{key} ".encode("ascii") + _pdf_string(value)
            for key, value in metadata.items()
        )
        objects[info_object_number - 1] = b"<< " + entries + b" >>"

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R".encode("ascii")
    if info_object_number is not None:
        trailer += f" /Info {info_object_number} 0 R".encode("ascii")
    output.extend(trailer + b" >>\n")
    output.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def _write_synthetic_pdf(
    path: Path,
    page_texts: tuple[str | None, ...],
    metadata: dict[str, str] | None = None,
) -> bytes:
    content = _synthetic_pdf_bytes(page_texts, metadata)
    path.write_bytes(content)
    return content


def test_extracts_single_page_known_text_and_provenance(tmp_path: Path) -> None:
    source = tmp_path / "single.pdf"
    original = _write_synthetic_pdf(source, ("Known synthetic economics text",))

    result = PyPDFExtractor().extract(source)

    assert result.source_path == source.resolve()
    assert result.page_count == 1
    assert result.pages[0].page_number == 1
    assert result.pages[0].text.strip() == "Known synthetic economics text"
    assert result.extraction_method == "pypdf"
    assert result.parser_version == pypdf.__version__
    assert source.read_bytes() == original


def test_extracts_multiple_pages_in_source_order_with_boundaries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "multi.pdf"
    _write_synthetic_pdf(source, ("First page", "Second page", "Third page"))

    result = PyPDFExtractor().extract(source)

    assert result.page_count == 3
    assert tuple(page.page_number for page in result.pages) == (1, 2, 3)
    assert tuple(page.text.strip() for page in result.pages) == (
        "First page",
        "Second page",
        "Third page",
    )


def test_extracts_known_raw_document_metadata(tmp_path: Path) -> None:
    source = tmp_path / "metadata.pdf"
    _write_synthetic_pdf(
        source,
        ("Metadata page",),
        {
            "Title": "Known title",
            "Author": "Ada Lovelace; Joan Robinson",
            "Subject": "Synthetic economics",
            "Keywords": "trade, growth",
            "Creator": "Fixture creator",
            "Producer": "Fixture producer",
            "CreationDate": "D:20260102030405Z",
            "ModDate": "D:20260203040506Z",
        },
    )

    metadata = PyPDFExtractor().extract(source).metadata

    assert metadata.title == "Known title"
    assert metadata.author_text == "Ada Lovelace; Joan Robinson"
    assert metadata.subject == "Synthetic economics"
    assert metadata.keywords == "trade, growth"
    assert metadata.creator == "Fixture creator"
    assert metadata.producer == "Fixture producer"
    assert metadata.creation_date == "D:20260102030405Z"
    assert metadata.modification_date == "D:20260203040506Z"


def test_missing_document_metadata_remains_explicitly_absent(tmp_path: Path) -> None:
    source = tmp_path / "no-metadata.pdf"
    _write_synthetic_pdf(source, ("No metadata",))

    metadata = PyPDFExtractor().extract(source).metadata

    assert metadata.title is None
    assert metadata.author_text is None
    assert metadata.subject is None
    assert metadata.keywords is None
    assert metadata.creator is None
    assert metadata.producer is None
    assert metadata.creation_date is None
    assert metadata.modification_date is None


def test_page_without_extractable_text_remains_in_result(tmp_path: Path) -> None:
    source = tmp_path / "empty-page.pdf"
    _write_synthetic_pdf(source, ("Text page", None, "Final page"))

    result = PyPDFExtractor().extract(source)

    assert result.page_count == 3
    assert result.pages[1].page_number == 2
    assert result.pages[1].text == ""


def test_repeated_extraction_is_equivalent(tmp_path: Path) -> None:
    source = tmp_path / "repeat.pdf"
    _write_synthetic_pdf(source, ("Repeatable first", "Repeatable second"))
    extractor = PyPDFExtractor()

    assert extractor.extract(source) == extractor.extract(source)


def test_only_line_endings_are_normalized(tmp_path: Path) -> None:
    source = tmp_path / "line-endings.pdf"
    source.write_bytes(b"placeholder")

    class FakePage:
        def extract_text(self) -> str:
            return "First\r\nSecond\rThird  "

    class FakeReader:
        is_encrypted = False
        metadata = None
        pages = (FakePage(),)

    with patch(
        "econ_paper_cli.adapters.pypdf_extractor.PdfReader",
        return_value=FakeReader(),
    ):
        result = PyPDFExtractor().extract(source)

    assert result.pages[0].text == "First\nSecond\nThird  "


def test_password_encrypted_pdf_raises_dedicated_error(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="secret")
    with source.open("wb") as output:
        writer.write(output)

    with pytest.raises(PDFEncryptedError, match="without a password"):
        PyPDFExtractor().extract(source)


@pytest.mark.parametrize(
    "content",
    [b"not a PDF", b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>"],
)
def test_malformed_or_truncated_pdf_raises_typed_error(
    tmp_path: Path,
    content: bytes,
) -> None:
    source = tmp_path / "malformed.pdf"
    source.write_bytes(content)

    with pytest.raises(PDFMalformedError):
        PyPDFExtractor().extract(source)


def test_missing_pdf_raises_typed_error(tmp_path: Path) -> None:
    source = tmp_path / "missing.pdf"
    with pytest.raises(PDFSourceNotFoundError) as exc_info:
        PyPDFExtractor().extract(source)
    assert exc_info.value.path == source.resolve()


def test_directory_source_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(PDFSourceNotRegularFileError) as exc_info:
        PyPDFExtractor().extract(tmp_path)
    assert exc_info.value.path == tmp_path.resolve()


def test_permission_failure_is_translated(tmp_path: Path) -> None:
    source = tmp_path / "permission.pdf"
    source.write_bytes(b"placeholder")

    with patch("builtins.open", side_effect=PermissionError("denied")):
        with pytest.raises(PDFPermissionError) as exc_info:
            PyPDFExtractor().extract(source)
    assert exc_info.value.path == source.resolve()


def test_generic_os_read_failure_is_translated(tmp_path: Path) -> None:
    source = tmp_path / "read-error.pdf"
    source.write_bytes(b"placeholder")

    with patch("builtins.open", side_effect=OSError("disk failure")):
        with pytest.raises(PDFReadError) as exc_info:
            PyPDFExtractor().extract(source)
    assert exc_info.value.path == source.resolve()


def test_unexpected_parser_failure_is_translated(tmp_path: Path) -> None:
    source = tmp_path / "parser-error.pdf"
    source.write_bytes(b"placeholder")

    class FailingPage:
        def extract_text(self) -> str:
            raise ValueError("untrustworthy parse")

    class FakeReader:
        is_encrypted = False
        metadata = None
        pages = (FailingPage(),)

    with patch(
        "econ_paper_cli.adapters.pypdf_extractor.PdfReader",
        return_value=FakeReader(),
    ):
        with pytest.raises(PDFParserError, match="parser failed"):
            PyPDFExtractor().extract(source)
