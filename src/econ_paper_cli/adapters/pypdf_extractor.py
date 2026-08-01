"""Fully local structured PDF extraction implemented with pypdf."""

from collections.abc import Mapping
from pathlib import Path

import pypdf
from pypdf import PasswordType, PdfReader
from pypdf.errors import (
    FileNotDecryptedError,
    PdfReadError,
    PyPdfError,
    WrongPasswordError,
)

from econ_paper_cli.domain.pdf_extraction import (
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
)
from econ_paper_cli.protocols.pdf_extraction import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFMalformedError,
    PDFParserError,
    PDFPermissionError,
    PDFReadError,
    PDFSourceNotFoundError,
    PDFSourceNotRegularFileError,
)


def _resolve_regular_source(source_path: Path) -> Path:
    """Return the canonical source path after typed filesystem validation."""
    try:
        resolved_path = source_path.expanduser().resolve()
        if not resolved_path.exists():
            raise PDFSourceNotFoundError(resolved_path)
        if not resolved_path.is_file():
            raise PDFSourceNotRegularFileError(resolved_path)
        return resolved_path
    except (PDFSourceNotFoundError, PDFSourceNotRegularFileError):
        raise
    except PermissionError as error:
        raise PDFPermissionError(source_path, error) from error
    except OSError as error:
        raise PDFReadError(source_path, error) from error


def _normalize_page_text(text: str | None) -> str:
    """Normalize line endings only, preserving all other parser output."""
    if text is None:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _metadata_string(
    metadata: Mapping[str, object] | None,
    key: str,
) -> str | None:
    """Return one parser-decoded raw document-information value."""
    if metadata is None:
        return None
    value = metadata.get(key)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _extract_metadata(
    metadata: Mapping[str, object] | None,
) -> PDFDocumentMetadata:
    return PDFDocumentMetadata(
        title=_metadata_string(metadata, "/Title"),
        author_text=_metadata_string(metadata, "/Author"),
        subject=_metadata_string(metadata, "/Subject"),
        keywords=_metadata_string(metadata, "/Keywords"),
        creator=_metadata_string(metadata, "/Creator"),
        producer=_metadata_string(metadata, "/Producer"),
        creation_date=_metadata_string(metadata, "/CreationDate"),
        modification_date=_metadata_string(metadata, "/ModDate"),
    )


class PyPDFExtractor:
    """Extract page text and raw metadata locally through pypdf."""

    def extract(self, source_path: Path) -> PDFExtractionResult:
        """Return deterministic structured extraction without modifying the source."""
        resolved_path = _resolve_regular_source(source_path)

        try:
            with open(resolved_path, "rb") as source_file:
                reader = PdfReader(source_file, strict=True)
                if (
                    reader.is_encrypted
                    and reader.decrypt("") == PasswordType.NOT_DECRYPTED
                ):
                    raise PDFEncryptedError(resolved_path)

                pages = tuple(
                    ExtractedPDFPage(
                        page_number=page_number,
                        text=_normalize_page_text(page.extract_text()),
                    )
                    for page_number, page in enumerate(reader.pages, start=1)
                )
                metadata = _extract_metadata(reader.metadata)
        except PDFExtractionError:
            raise
        except FileNotFoundError as error:
            raise PDFSourceNotFoundError(resolved_path) from error
        except PermissionError as error:
            raise PDFPermissionError(resolved_path, error) from error
        except OSError as error:
            raise PDFReadError(resolved_path, error) from error
        except (FileNotDecryptedError, WrongPasswordError) as error:
            raise PDFEncryptedError(resolved_path) from error
        except PdfReadError as error:
            raise PDFMalformedError(resolved_path, error) from error
        except PyPdfError as error:
            raise PDFParserError(resolved_path, error) from error
        except Exception as error:
            raise PDFParserError(resolved_path, error) from error

        return PDFExtractionResult(
            source_path=resolved_path,
            pages=pages,
            page_count=len(pages),
            metadata=metadata,
            extraction_method="pypdf",
            parser_version=pypdf.__version__,
        )
