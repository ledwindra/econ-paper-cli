"""Unit tests for ingestion preflight service."""

import hashlib
from pathlib import Path

import pytest

from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import Paper, Passage
from econ_paper_cli.domain.errors import (
    IngestionEmptyDirectoryError,
    IngestionPathNotFoundError,
    IngestionUnsupportedFileError,
)
from econ_paper_cli.domain.storage import (
    ConversionSettings,
    IngestionCompletion,
    PaperRecord,
    SourceProvenance,
)
from econ_paper_cli.services.ingestion import (
    compute_file_sha256,
    run_ingestion_preflight,
)


def test_compute_file_sha256(tmp_path: Path) -> None:
    test_file = tmp_path / "test.pdf"
    content = b"Sample PDF content bytes"
    test_file.write_bytes(content)

    expected_sha256 = hashlib.sha256(content).hexdigest().lower()
    expected_size = len(content)

    size, sha256 = compute_file_sha256(test_file)
    assert size == expected_size
    assert sha256 == expected_sha256


def test_explicit_single_pdf_file_input(tmp_path: Path) -> None:
    pdf_file = tmp_path / "single_paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample content")

    result = run_ingestion_preflight(pdf_file)
    assert result.target_path == pdf_file.resolve()
    assert result.total_candidate_count == 1
    assert result.new_candidate_count == 1
    assert result.stored_candidate_count == 0
    assert result.batch_duplicate_count == 0

    candidate = result.candidates[0]
    assert candidate.source_path == pdf_file.resolve()
    assert candidate.file_size_bytes == len(b"%PDF-1.4 sample content")
    assert candidate.is_stored is False
    assert candidate.is_batch_duplicate is False


def test_directory_discovery_deterministic_ordering_and_case_insensitive_extension(
    tmp_path: Path,
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()

    sub_dir = papers_dir / "sub"
    sub_dir.mkdir()

    # Create files in non-alphabetical order and different case extensions
    f3 = sub_dir / "z_paper.PDF"
    f1 = papers_dir / "a_paper.pdf"
    f2 = papers_dir / "m_paper.Pdf"

    f1.write_bytes(b"content 1")
    f2.write_bytes(b"content 2")
    f3.write_bytes(b"content 3")

    result = run_ingestion_preflight(papers_dir)
    assert result.total_candidate_count == 3

    # Assert exact deterministic sorting by path string
    expected_paths = tuple(
        sorted([f1.resolve(), f2.resolve(), f3.resolve()], key=lambda p: str(p))
    )
    actual_paths = tuple(c.source_path for c in result.candidates)
    assert actual_paths == expected_paths


def test_directory_discovery_ignores_non_pdf_files(tmp_path: Path) -> None:
    papers_dir = tmp_path / "mixed_folder"
    papers_dir.mkdir()

    pdf_file = papers_dir / "doc.pdf"
    pdf_file.write_bytes(b"pdf content")

    txt_file = papers_dir / "notes.txt"
    txt_file.write_bytes(b"text content")

    result = run_ingestion_preflight(papers_dir)
    assert result.total_candidate_count == 1
    assert result.candidates[0].source_path == pdf_file.resolve()


def test_explicit_non_pdf_file_raises_unsupported_error(tmp_path: Path) -> None:
    txt_file = tmp_path / "document.txt"
    txt_file.write_bytes(b"text content")

    with pytest.raises(
        IngestionUnsupportedFileError,
        match="is not a supported PDF document",
    ):
        run_ingestion_preflight(txt_file)


def test_missing_path_raises_not_found_error(tmp_path: Path) -> None:
    missing = tmp_path / "non_existent.pdf"
    with pytest.raises(
        IngestionPathNotFoundError, match="Target path for ingestion does not exist"
    ):
        run_ingestion_preflight(missing)


def test_empty_directory_raises_empty_directory_error(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_folder"
    empty_dir.mkdir()

    with pytest.raises(IngestionEmptyDirectoryError, match="No supported PDF files"):
        run_ingestion_preflight(empty_dir)


def test_batch_duplicate_files_with_identical_bytes(tmp_path: Path) -> None:
    folder = tmp_path / "duplicates_folder"
    folder.mkdir()

    content = b"Exact identical PDF bytes"

    file_a = folder / "a.pdf"
    file_b = folder / "b.pdf"
    file_a.write_bytes(content)
    file_b.write_bytes(content)

    result = run_ingestion_preflight(folder)
    assert result.total_candidate_count == 2
    assert result.new_candidate_count == 1
    assert result.batch_duplicate_count == 1

    first = result.candidates[0]
    second = result.candidates[1]

    assert first.is_batch_duplicate is False
    assert first.duplicate_of_path is None

    assert second.is_batch_duplicate is True
    assert second.duplicate_of_path == first.source_path


def test_existing_checksum_detection_through_storage_backend(
    tmp_path: Path,
) -> None:
    pdf_file = tmp_path / "stored_paper.pdf"
    content = b"Stored PDF paper content"
    pdf_file.write_bytes(content)

    checksum = hashlib.sha256(content).hexdigest().lower()

    storage = SQLiteStorage(":memory:")
    storage.initialize()

    paper = Paper(
        paper_id="paper.1",
        title="Stored Paper Title",
        authors=("Alice Smith",),
        year=2024,
        abstract="Abstract",
        source_name="Source",
        source_identifier="id1",
        source_url=None,
    )
    passage = Passage(
        passage_id="paper.1:p0",
        paper_id="paper.1",
        text="Passage text",
        section_heading=None,
        page_start=1,
        page_end=1,
        ordinal_position=0,
    )
    prov = SourceProvenance(
        source_path=str(pdf_file.resolve()),
        source_format="pdf",
        source_file_size=len(content),
        content_checksum=checksum,
        markdown_path=str(pdf_file.resolve()),
        extraction_method="test",
        created_at="2026-07-31T20:00:00Z",
    )
    sett = ConversionSettings(
        conversion_version="1.0.0", ocr_enabled=False, parameters={}
    )
    comp = IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T20:00:00Z",
        passage_count=1,
        warning_count=0,
    )
    record = PaperRecord(
        paper=paper,
        passages=(passage,),
        source_provenance=prov,
        conversion_settings=sett,
        warnings=(),
        completion=comp,
    )

    storage.save_paper_record(record)

    result = run_ingestion_preflight(pdf_file, storage=storage)
    assert result.total_candidate_count == 1
    assert result.stored_candidate_count == 1
    assert result.new_candidate_count == 0
    assert result.candidates[0].is_stored is True

    storage.close()
