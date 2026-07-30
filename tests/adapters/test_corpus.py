"""Tests for the synthetic corpus loader adapter and fixture integrity."""

import json
from pathlib import Path

import pytest

from econ_paper_cli.adapters import (
    CorpusFileNotFoundError,
    CorpusInvalidJsonError,
    CorpusValidationError,
    load_corpus_from_file,
    load_manifest_from_file,
    verify_artifact,
)
from econ_paper_cli.domain import Paper, Passage

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
    papers, passages = load_corpus_from_file(FIXTURE_JSON)

    assert len(papers) == 5
    assert len(passages) == 15

    assert all(isinstance(paper, Paper) for paper in papers)
    assert all(isinstance(passage, Passage) for passage in passages)

    paper_ids = [paper.paper_id for paper in papers]
    expected_ids = [
        "synthetic-elections-roads-2024",
        "synthetic-brt-landvalues-2023",
        "synthetic-power-productivity-2022",
        "synthetic-flood-migration-2024",
        "synthetic-housing-regulation-2023",
    ]
    assert paper_ids == expected_ids

    # Each paper has exactly 3 passages
    for paper in papers:
        paper_passages = [p for p in passages if p.paper_id == paper.paper_id]
        assert len(paper_passages) == 3


def test_corpus_loader_raises_file_not_found(tmp_path: Path) -> None:
    """Test that CorpusFileNotFoundError is raised for non-existent file paths."""
    non_existent = tmp_path / "missing-corpus.json"
    with pytest.raises(CorpusFileNotFoundError):
        load_corpus_from_file(non_existent)


def test_corpus_loader_raises_invalid_json(tmp_path: Path) -> None:
    """Test that CorpusInvalidJsonError is raised for malformed JSON files."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{invalid json:", encoding="utf-8")

    with pytest.raises(CorpusInvalidJsonError):
        load_corpus_from_file(bad_json)


def test_corpus_loader_raises_validation_error_on_missing_keys(tmp_path: Path) -> None:
    """Test that CorpusValidationError is raised if 'papers' or 'passages' key is missing."""
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text('{"papers": []}', encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="passages"):
        load_corpus_from_file(incomplete)


def test_corpus_loader_raises_validation_error_on_invalid_paper(tmp_path: Path) -> None:
    """Test that invalid paper metadata raises CorpusValidationError."""
    bad_paper_file = tmp_path / "bad_paper.json"
    data = {
        "papers": [
            {
                "paper_id": "invalid ID",  # Fails paper_id grammar validation
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

    with pytest.raises(CorpusValidationError, match="invalid"):
        load_corpus_from_file(bad_paper_file)


def test_corpus_loader_raises_validation_error_on_duplicate_paper_id(
    tmp_path: Path,
) -> None:
    """Test that duplicate paper_ids raise CorpusValidationError."""
    dup_file = tmp_path / "duplicate_paper.json"
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
        "papers": [paper_mapping, paper_mapping],
        "passages": [],
    }
    dup_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="Duplicate paper_id"):
        load_corpus_from_file(dup_file)


def test_corpus_loader_raises_validation_error_on_orphan_passage(
    tmp_path: Path,
) -> None:
    """Test that a passage referencing a non-existent paper_id raises CorpusValidationError."""
    orphan_file = tmp_path / "orphan_passage.json"
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
        "passage_id": "p1:sec1:pos0",
        "paper_id": "paper-non-existent",  # References missing paper
        "text": "Text content...",
        "section_heading": None,
        "page_start": None,
        "page_end": None,
        "ordinal_position": 0,
    }
    data = {
        "papers": [paper_mapping],
        "passages": [passage_mapping],
    }
    orphan_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="unknown paper_id"):
        load_corpus_from_file(orphan_file)
