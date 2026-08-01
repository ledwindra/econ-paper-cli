"""Unit tests for ingestion preflight service."""

import hashlib
from pathlib import Path

import pytest

from econ_paper_cli.adapters.filesystem import FileInspectionResult
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import Paper, Passage
from econ_paper_cli.domain.errors import (
    IngestionEmptyDirectoryError,
    IngestionInvalidPathError,
    IngestionPathNotFoundError,
    IngestionUnsupportedFileError,
)
from econ_paper_cli.domain.storage import (
    ConversionSettings,
    IngestionCompletion,
    PaperRecord,
    SourceProvenance,
)
from econ_paper_cli.protocols.storage import StorageBackend
from econ_paper_cli.services.ingestion import run_ingestion_preflight


class FakeStorageBackend(StorageBackend):
    """Minimal fake StorageBackend implementation for unit testing preflight classification."""

    def __init__(self, stored_checksums: set[str] | None = None) -> None:
        self.stored_checksums = stored_checksums or set()
        self.queried_checksums: list[str] = []

    def get_paper_record_by_checksum(self, checksum: str) -> PaperRecord | None:
        self.queried_checksums.append(checksum)
        if checksum in self.stored_checksums:
            dummy_paper = Paper(
                paper_id="fake.1",
                title="Fake Paper",
                authors=("Fake Author",),
                year=2024,
                abstract="Fake abstract.",
                source_name="FakeSource",
                source_identifier="f1",
                source_url=None,
            )
            dummy_prov = SourceProvenance(
                source_path="/fake/path.pdf",
                source_format="pdf",
                source_file_size=1024,
                content_checksum=checksum,
                markdown_path="/fake/path.md",
                extraction_method="fake",
                created_at="2026-07-31T20:00:00Z",
            )
            return PaperRecord(
                paper=dummy_paper,
                passages=(),
                source_provenance=dummy_prov,
                conversion_settings=ConversionSettings("1.0", False, {}),
                warnings=(),
                completion=IngestionCompletion(
                    "completed", "2026-07-31T20:00:00Z", 0, 0
                ),
            )
        return None

    def initialize(self) -> None:
        pass

    def close(self) -> None:
        pass

    def get_schema_version(self) -> int:
        return 1

    def save_paper_record(self, record: PaperRecord) -> None:
        pass

    def get_paper_record(self, paper_id: str) -> PaperRecord | None:
        return None

    def get_paper(self, paper_id: str) -> Paper | None:
        return None

    def get_passages(self, paper_id: str) -> tuple[Passage, ...]:
        return ()

    def list_paper_ids(self) -> tuple[str, ...]:
        return ()

    def list_paper_records(self) -> tuple[PaperRecord, ...]:
        return ()

    def delete_paper_record(self, paper_id: str) -> bool:
        return False

    def count_papers(self) -> int:
        return len(self.stored_checksums)

    def count_passages(self) -> int:
        return 0


def test_existing_checksum_classification_with_fake_storage(tmp_path: Path) -> None:
    folder = tmp_path / "fake_test_folder"
    folder.mkdir()

    file_new = folder / "new.pdf"
    file_stored = folder / "stored.pdf"
    file_new.write_bytes(b"New PDF content")
    file_stored.write_bytes(b"Stored PDF content")

    checksum_stored = hashlib.sha256(b"Stored PDF content").hexdigest().lower()
    checksum_new = hashlib.sha256(b"New PDF content").hexdigest().lower()

    fake_storage = FakeStorageBackend(stored_checksums={checksum_stored})

    result = run_ingestion_preflight(folder, storage=fake_storage)

    assert fake_storage.queried_checksums == [checksum_new, checksum_stored]
    assert result.total_candidate_count == 2
    assert result.new_candidate_count == 1
    assert result.stored_candidate_count == 1
    assert result.batch_duplicate_count == 0

    candidate_new = next(
        c for c in result.candidates if c.source_path == file_new.resolve()
    )
    candidate_stored = next(
        c for c in result.candidates if c.source_path == file_stored.resolve()
    )

    assert candidate_new.is_stored is False
    assert candidate_stored.is_stored is True


def test_file_inspector_dependency_injection(tmp_path: Path) -> None:
    pdf_file = tmp_path / "custom_inspect.pdf"
    pdf_file.write_bytes(b"Custom bytes")

    mock_sha256 = "c" * 64

    def custom_inspector(path: Path) -> FileInspectionResult:
        return FileInspectionResult(
            file_path=path.resolve(), size_bytes=999, sha256=mock_sha256
        )

    result = run_ingestion_preflight(pdf_file, file_inspector=custom_inspector)
    assert result.total_candidate_count == 1
    assert result.candidates[0].file_size_bytes == 999
    assert result.candidates[0].content_checksum == mock_sha256


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


def test_existing_path_that_is_neither_file_nor_directory_raises_invalid_path_error(
    tmp_path: Path,
) -> None:
    import os

    fifo_path = tmp_path / "test.fifo"
    os.mkfifo(fifo_path)

    with pytest.raises(
        IngestionInvalidPathError, match="is not a regular file or directory"
    ):
        run_ingestion_preflight(fifo_path)


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
