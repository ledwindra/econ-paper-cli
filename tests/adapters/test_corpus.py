"""Tests for the synthetic corpus loader adapter and fixture integrity."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from econ_paper_cli.adapters import (
    CorpusDomainValidationError,
    CorpusEncodingError,
    CorpusFileNotFoundError,
    CorpusInvalidJsonError,
    CorpusLoadError,
    CorpusNotARegularFileError,
    CorpusPermissionError,
    CorpusReadError,
    load_corpus_from_file,
    load_manifest_from_file,
    verify_artifact,
)
from econ_paper_cli.domain import (
    Corpus,
    CorpusValidationError,
    Paper,
    PaperValidationError,
    Passage,
    PassageValidationError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "corpus"
FIXTURE_JSON = FIXTURE_DIR / "synthetic-economics-v1.json"
FIXTURE_MANIFEST = FIXTURE_DIR / "synthetic-economics-v1.manifest.json"


def test_synthetic_corpus_manifest_verification() -> None:
    """Verify that the committed synthetic corpus fixture passes artifact verification."""
    manifest = load_manifest_from_file(FIXTURE_MANIFEST)
    result = verify_artifact(manifest, base_dir=REPO_ROOT)

    assert result.artifact_id == "synthetic-economics-v1"
    assert result.size_bytes == manifest.expected_size_bytes
    assert result.sha256 == manifest.sha256


def test_synthetic_corpus_loader_loads_committed_fixture() -> None:
    """Verify that load_corpus_from_file loads all 5 papers and 15 passages accurately."""
    corpus = load_corpus_from_file(FIXTURE_JSON)

    assert isinstance(corpus, Corpus)
    assert corpus.schema_version == 1
    assert corpus.corpus_id == "synthetic-economics-v1"

    assert len(corpus.papers) == 5
    assert len(corpus.passages) == 15

    assert all(isinstance(paper, Paper) for paper in corpus.papers)
    assert all(isinstance(passage, Passage) for passage in corpus.passages)

    paper_ids = [paper.paper_id for paper in corpus.papers]
    expected_ids = [
        "synthetic-elections-roads-2024",
        "synthetic-brt-landvalues-2023",
        "synthetic-power-productivity-2022",
        "synthetic-flood-migration-2024",
        "synthetic-housing-regulation-2023",
    ]
    assert paper_ids == expected_ids

    # Assert source_name and source_url conventions
    for paper in corpus.papers:
        assert paper.source_name == "Econ Paper CLI Synthetic Fixture Series"
        assert paper.source_url is not None
        assert paper.source_url.startswith("https://example.invalid/")

        paper_passages = [p for p in corpus.passages if p.paper_id == paper.paper_id]
        assert len(paper_passages) == 3


def test_corpus_loader_raises_file_not_found(tmp_path: Path) -> None:
    """Test that CorpusFileNotFoundError is raised for non-existent file paths."""
    non_existent = tmp_path / "missing-corpus.json"
    with pytest.raises(CorpusFileNotFoundError):
        load_corpus_from_file(non_existent)


def test_corpus_loader_raises_not_a_regular_file(tmp_path: Path) -> None:
    """Test that CorpusNotARegularFileError is raised when path is a directory."""
    with pytest.raises(CorpusNotARegularFileError):
        load_corpus_from_file(tmp_path)


def test_corpus_loader_raises_encoding_error(tmp_path: Path) -> None:
    """Test that CorpusEncodingError is raised for non-UTF-8 files."""
    bad_encoding_file = tmp_path / "bad_encoding.json"
    bad_encoding_file.write_bytes(b"\x80\x81\x82")

    with pytest.raises(CorpusEncodingError):
        load_corpus_from_file(bad_encoding_file)


def test_corpus_loader_raises_invalid_json(tmp_path: Path) -> None:
    """Test that CorpusInvalidJsonError is raised for malformed JSON files."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{invalid json:", encoding="utf-8")

    with pytest.raises(CorpusInvalidJsonError):
        load_corpus_from_file(bad_json)


def test_corpus_loader_raises_invalid_json_root_not_mapping(tmp_path: Path) -> None:
    """Test that CorpusInvalidJsonError is raised when JSON root is not an object."""
    array_json = tmp_path / "array.json"
    array_json.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(CorpusInvalidJsonError, match="Root|mapping"):
        load_corpus_from_file(array_json)


def test_corpus_loader_raises_permission_error(tmp_path: Path) -> None:
    """Test that CorpusPermissionError is raised when permission is denied."""
    restricted_file = tmp_path / "restricted.json"
    restricted_file.write_text("{}", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=PermissionError("Denied")):
        with pytest.raises(CorpusPermissionError):
            load_corpus_from_file(restricted_file)


def test_corpus_loader_raises_general_read_error(tmp_path: Path) -> None:
    """Test that CorpusReadError is raised when a general OSError occurs."""
    read_err_file = tmp_path / "read_err.json"
    read_err_file.write_text("{}", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=OSError("Drive fail")):
        with pytest.raises(CorpusReadError):
            load_corpus_from_file(read_err_file)


def test_corpus_loader_preserves_underlying_paper_validation_error(
    tmp_path: Path,
) -> None:
    """Test that PaperValidationError is preserved inside CorpusDomainValidationError."""
    bad_paper_file = tmp_path / "bad_paper.json"
    data = {
        "schema_version": 1,
        "corpus_id": "corpus-1",
        "papers": [
            {
                "paper_id": "invalid ID",
                "title": "Title",
                "authors": ["Author"],
                "year": 2024,
                "abstract": None,
                "source_name": "Series",
                "source_identifier": "id-1",
                "source_url": None,
            }
        ],
        "passages": [],
    }
    bad_paper_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorpusDomainValidationError) as exc_info:
        load_corpus_from_file(bad_paper_file)

    assert isinstance(exc_info.value.error, PaperValidationError)


def test_corpus_loader_preserves_underlying_passage_validation_error(
    tmp_path: Path,
) -> None:
    """Test that PassageValidationError is preserved inside CorpusDomainValidationError."""
    bad_passage_file = tmp_path / "bad_passage.json"
    paper_mapping = {
        "paper_id": "paper-1",
        "title": "Title",
        "authors": ["Author"],
        "year": 2024,
        "abstract": None,
        "source_name": "Series",
        "source_identifier": "id-1",
        "source_url": None,
    }
    passage_mapping = {
        "passage_id": "p1",
        "paper_id": "paper-1",
        "text": "Text",
        "section_heading": None,
        "page_start": 0,  # Invalid 0 page_start
        "page_end": 1,
        "ordinal_position": 0,
    }
    data = {
        "schema_version": 1,
        "corpus_id": "corpus-1",
        "papers": [paper_mapping],
        "passages": [passage_mapping],
    }
    bad_passage_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorpusDomainValidationError) as exc_info:
        load_corpus_from_file(bad_passage_file)

    assert isinstance(exc_info.value.error, PassageValidationError)


def test_corpus_loader_preserves_underlying_corpus_validation_error(
    tmp_path: Path,
) -> None:
    """Test that CorpusValidationError is preserved inside CorpusDomainValidationError."""
    dup_file = tmp_path / "dup.json"
    paper_mapping = {
        "paper_id": "paper-1",
        "title": "Title",
        "authors": ["Author"],
        "year": 2024,
        "abstract": None,
        "source_name": "Series",
        "source_identifier": "id-1",
        "source_url": None,
    }
    data = {
        "schema_version": 1,
        "corpus_id": "corpus-1",
        "papers": [paper_mapping, paper_mapping],  # Duplicate paper_id
        "passages": [],
    }
    dup_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorpusDomainValidationError) as exc_info:
        load_corpus_from_file(dup_file)

    assert isinstance(exc_info.value.error, CorpusValidationError)


def test_corpus_load_error_catch_all_behavior(tmp_path: Path) -> None:
    """Test that CorpusLoadError catches all adapter exceptions."""
    missing_file = tmp_path / "missing.json"
    with pytest.raises(CorpusLoadError):
        load_corpus_from_file(missing_file)
