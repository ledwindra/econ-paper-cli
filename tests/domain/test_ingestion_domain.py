"""Unit tests for ingestion domain models and validation."""

from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    IngestionPreflightResult,
    IngestionValidationError,
    PreflightCandidate,
)

CHECKSUM_1 = "a" * 64
CHECKSUM_2 = "b" * 64


def test_preflight_candidate_construction() -> None:
    path = Path("/tmp/paper1.pdf")
    candidate = PreflightCandidate(
        source_path=path,
        file_size_bytes=1024,
        content_checksum=CHECKSUM_1,
        is_stored=False,
        is_batch_duplicate=False,
    )
    assert candidate.source_path == path
    assert candidate.file_size_bytes == 1024
    assert candidate.content_checksum == CHECKSUM_1
    assert candidate.is_stored is False
    assert candidate.is_batch_duplicate is False
    assert candidate.duplicate_of_path is None


def test_preflight_candidate_batch_duplicate() -> None:
    path1 = Path("/tmp/paper1.pdf")
    path2 = Path("/tmp/paper2.pdf")
    candidate = PreflightCandidate(
        source_path=path2,
        file_size_bytes=1024,
        content_checksum=CHECKSUM_1,
        is_stored=False,
        is_batch_duplicate=True,
        duplicate_of_path=path1,
    )
    assert candidate.is_batch_duplicate is True
    assert candidate.duplicate_of_path == path1


def test_preflight_candidate_validation_errors() -> None:
    path = Path("/tmp/paper1.pdf")

    # Invalid source_path
    with pytest.raises(IngestionValidationError, match="source_path"):
        PreflightCandidate(
            source_path="/not/a/path",  # type: ignore[arg-type]
            file_size_bytes=1024,
            content_checksum=CHECKSUM_1,
            is_stored=False,
            is_batch_duplicate=False,
        )

    # Invalid file_size_bytes
    with pytest.raises(IngestionValidationError, match="file_size_bytes"):
        PreflightCandidate(
            source_path=path,
            file_size_bytes=0,
            content_checksum=CHECKSUM_1,
            is_stored=False,
            is_batch_duplicate=False,
        )

    # Invalid content_checksum
    with pytest.raises(IngestionValidationError, match="content_checksum"):
        PreflightCandidate(
            source_path=path,
            file_size_bytes=1024,
            content_checksum="invalid",
            is_stored=False,
            is_batch_duplicate=False,
        )

    # Missing duplicate_of_path when is_batch_duplicate is True
    with pytest.raises(IngestionValidationError, match="duplicate_of_path"):
        PreflightCandidate(
            source_path=path,
            file_size_bytes=1024,
            content_checksum=CHECKSUM_1,
            is_stored=False,
            is_batch_duplicate=True,
            duplicate_of_path=None,
        )

    # Unexpected duplicate_of_path when is_batch_duplicate is False
    with pytest.raises(IngestionValidationError, match="duplicate_of_path"):
        PreflightCandidate(
            source_path=path,
            file_size_bytes=1024,
            content_checksum=CHECKSUM_1,
            is_stored=False,
            is_batch_duplicate=False,
            duplicate_of_path=Path("/tmp/original.pdf"),
        )


def test_ingestion_preflight_result_construction() -> None:
    target = Path("/tmp/papers")
    candidate = PreflightCandidate(
        source_path=Path("/tmp/papers/p1.pdf"),
        file_size_bytes=1024,
        content_checksum=CHECKSUM_1,
        is_stored=False,
        is_batch_duplicate=False,
    )
    result = IngestionPreflightResult(
        target_path=target,
        candidates=(candidate,),
        new_candidate_count=1,
        stored_candidate_count=0,
        batch_duplicate_count=0,
        total_candidate_count=1,
    )
    assert result.target_path == target
    assert result.candidates == (candidate,)
    assert result.new_candidate_count == 1
    assert result.total_candidate_count == 1


def test_ingestion_preflight_result_validation_errors() -> None:
    target = Path("/tmp/papers")
    candidate = PreflightCandidate(
        source_path=Path("/tmp/papers/p1.pdf"),
        file_size_bytes=1024,
        content_checksum=CHECKSUM_1,
        is_stored=False,
        is_batch_duplicate=False,
    )

    # Mismatched total_candidate_count
    with pytest.raises(IngestionValidationError, match="total_candidate_count"):
        IngestionPreflightResult(
            target_path=target,
            candidates=(candidate,),
            new_candidate_count=1,
            stored_candidate_count=0,
            batch_duplicate_count=0,
            total_candidate_count=5,
        )

    # Mismatched new_candidate_count
    with pytest.raises(IngestionValidationError, match="new_candidate_count"):
        IngestionPreflightResult(
            target_path=target,
            candidates=(candidate,),
            new_candidate_count=0,
            stored_candidate_count=0,
            batch_duplicate_count=0,
            total_candidate_count=1,
        )

    # Mismatched stored_candidate_count
    with pytest.raises(IngestionValidationError, match="stored_candidate_count"):
        IngestionPreflightResult(
            target_path=target,
            candidates=(candidate,),
            new_candidate_count=1,
            stored_candidate_count=1,
            batch_duplicate_count=0,
            total_candidate_count=1,
        )

    # Mismatched batch_duplicate_count
    with pytest.raises(IngestionValidationError, match="batch_duplicate_count"):
        IngestionPreflightResult(
            target_path=target,
            candidates=(candidate,),
            new_candidate_count=1,
            stored_candidate_count=0,
            batch_duplicate_count=1,
            total_candidate_count=1,
        )

    # Boolean counts must be rejected (True == 1 in Python but not a valid int count)
    with pytest.raises(IngestionValidationError, match="must be a non-negative int"):
        IngestionPreflightResult(
            target_path=target,
            candidates=(candidate,),
            new_candidate_count=True,  # type: ignore[arg-type]
            stored_candidate_count=0,
            batch_duplicate_count=0,
            total_candidate_count=1,
        )

    # Float counts must be rejected (1.0 == 1 in Python but not a valid int count)
    with pytest.raises(IngestionValidationError, match="must be a non-negative int"):
        IngestionPreflightResult(
            target_path=target,
            candidates=(candidate,),
            new_candidate_count=1.0,  # type: ignore[arg-type]
            stored_candidate_count=0,
            batch_duplicate_count=0,
            total_candidate_count=1,
        )
