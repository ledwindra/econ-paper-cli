"""Tests for local manifest loading and file checksum verification adapters."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from econ_paper_cli.adapters.filesystem import (
    ArtifactFileNotFoundError,
    ArtifactNotARegularFileError,
    ChecksumMismatchError,
    FilesystemPermissionError,
    FilesystemReadError,
    ManifestEncodingError,
    ManifestFileNotFoundError,
    ManifestInvalidJsonError,
    ManifestValidationError,
    SizeMismatchError,
    load_manifest_from_file,
    verify_artifact,
)
from econ_paper_cli.domain import ArtifactManifest, ArtifactManifestError


def valid_manifest_dict() -> dict[str, object]:
    """Return a dictionary representing a valid artifact manifest."""
    return {
        "schema_version": 1,
        "artifact_id": "synthetic-fixture-index",
        "kind": "index",
        "version": "1.0.0",
        "source": "https://example.invalid/synthetic-fixture-index",
        "license": "CC0-1.0",
        "redistribution_status": "permitted",
        "expected_size_bytes": 128,
        # SHA-256 of 128 'A's
        "sha256": "b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e",
        "update_policy": "Pinned test fixture; update manually.",
        "contains_copyrighted_full_text": False,
        "local_path": "indexes/synthetic-fixture-index.bin",
    }


def test_load_manifest_success(tmp_path: Path) -> None:
    """Test successful loading and parsing of a valid manifest file."""
    manifest_path = tmp_path / "manifest.json"
    data = valid_manifest_dict()
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    manifest = load_manifest_from_file(manifest_path)
    assert manifest.artifact_id == "synthetic-fixture-index"
    assert manifest.schema_version == 1
    assert manifest.expected_size_bytes == 128
    assert (
        manifest.sha256
        == "b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e"
    )


def test_load_manifest_not_found(tmp_path: Path) -> None:
    """Test that a missing manifest file raises ManifestFileNotFoundError."""
    non_existent = tmp_path / "missing.json"
    with pytest.raises(ManifestFileNotFoundError) as exc_info:
        load_manifest_from_file(non_existent)
    assert exc_info.value.path == non_existent
    assert "does not exist" in str(exc_info.value)


def test_load_manifest_invalid_utf8(tmp_path: Path) -> None:
    """Test that invalid UTF-8 manifest files raise ManifestEncodingError."""
    manifest_path = tmp_path / "manifest.json"
    # Write invalid UTF-8 bytes (0xff)
    manifest_path.write_bytes(b"\xff\xff\xff")

    with pytest.raises(ManifestEncodingError) as exc_info:
        load_manifest_from_file(manifest_path)
    assert exc_info.value.path == manifest_path
    assert "not valid UTF-8" in str(exc_info.value)


def test_load_manifest_invalid_json(tmp_path: Path) -> None:
    """Test that malformed JSON manifest files raise ManifestInvalidJsonError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ManifestInvalidJsonError) as exc_info:
        load_manifest_from_file(manifest_path)
    assert exc_info.value.path == manifest_path
    assert "contains invalid JSON" in str(exc_info.value)


def test_load_manifest_root_not_object(tmp_path: Path) -> None:
    """Test that manifest files without a JSON object root raise ManifestInvalidJsonError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("12345", encoding="utf-8")

    with pytest.raises(ManifestInvalidJsonError) as exc_info:
        load_manifest_from_file(manifest_path)
    assert exc_info.value.path == manifest_path
    assert "JSON root must be a JSON object mapping" in str(exc_info.value)


def test_load_manifest_validation_error(tmp_path: Path) -> None:
    """Test that manifest files violating the domain contract raise ManifestValidationError."""
    manifest_path = tmp_path / "manifest.json"
    data = valid_manifest_dict()
    data["expected_size_bytes"] = -10  # Invalid positive integer
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest_from_file(manifest_path)
    assert exc_info.value.path == manifest_path
    assert isinstance(exc_info.value.error, ArtifactManifestError)


def test_load_manifest_permission_error(tmp_path: Path) -> None:
    """Test that permission denied during manifest load raises FilesystemPermissionError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.touch()

    with patch("builtins.open", side_effect=PermissionError("Permission denied")):
        with pytest.raises(FilesystemPermissionError) as exc_info:
            load_manifest_from_file(manifest_path)
        assert exc_info.value.path == manifest_path
        assert "Permission denied" in str(exc_info.value)


def test_load_manifest_os_error(tmp_path: Path) -> None:
    """Test that general OS errors during manifest load raise FilesystemReadError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.touch()

    with patch("builtins.open", side_effect=OSError("Read failure")):
        with pytest.raises(FilesystemReadError) as exc_info:
            load_manifest_from_file(manifest_path)
        assert exc_info.value.path == manifest_path
        assert "Read error accessing" in str(exc_info.value)


def test_verify_artifact_success(tmp_path: Path) -> None:
    """Test successful verification of a valid artifact file."""
    manifest_data = valid_manifest_dict()
    manifest = ArtifactManifest.from_mapping(manifest_data)

    artifact_file_path = tmp_path / manifest.local_path
    artifact_file_path.parent.mkdir(parents=True, exist_ok=True)

    content = b"A" * 128
    artifact_file_path.write_bytes(content)

    result = verify_artifact(manifest, base_dir=tmp_path)

    assert result.artifact_id == manifest.artifact_id
    assert result.file_path == artifact_file_path
    assert result.size_bytes == 128
    assert (
        result.sha256
        == "b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e"
    )


def test_verify_artifact_file_not_found(tmp_path: Path) -> None:
    """Test that missing artifact files raise ArtifactFileNotFoundError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())

    with pytest.raises(ArtifactFileNotFoundError) as exc_info:
        verify_artifact(manifest, base_dir=tmp_path)
    assert exc_info.value.path == tmp_path / manifest.local_path
    assert "does not exist" in str(exc_info.value)


def test_verify_artifact_not_a_regular_file(tmp_path: Path) -> None:
    """Test that directory targets instead of regular files raise ArtifactNotARegularFileError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ArtifactNotARegularFileError) as exc_info:
        verify_artifact(manifest, base_dir=tmp_path)
    assert exc_info.value.path == artifact_path
    assert "not a regular file" in str(exc_info.value)


def test_verify_artifact_size_mismatch(tmp_path: Path) -> None:
    """Test that size mismatch raises SizeMismatchError before computing hash."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    # Write 100 'A's instead of 128
    artifact_path.write_bytes(b"A" * 100)

    # If it computes the hash anyway or does not check size, that's incorrect.
    # We also mock file open to make sure it is NEVER opened if size mismatches.
    with patch("builtins.open") as mock_open:
        with pytest.raises(SizeMismatchError) as exc_info:
            verify_artifact(manifest, base_dir=tmp_path)
        assert exc_info.value.path == artifact_path
        assert exc_info.value.expected == 128
        assert exc_info.value.actual == 100
        assert "size mismatch" in str(exc_info.value)
        mock_open.assert_not_called()


def test_verify_artifact_checksum_mismatch(tmp_path: Path) -> None:
    """Test that checksum mismatch raises ChecksumMismatchError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    # Write 128 'B's (size correct, content/hash wrong)
    artifact_path.write_bytes(b"B" * 128)

    with pytest.raises(ChecksumMismatchError) as exc_info:
        verify_artifact(manifest, base_dir=tmp_path)
    assert exc_info.value.path == artifact_path
    assert (
        exc_info.value.expected
        == "b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e"
    )
    assert exc_info.value.actual != exc_info.value.expected
    assert "SHA-256 digest mismatch" in str(exc_info.value)


def test_verify_artifact_chunk_size(tmp_path: Path) -> None:
    """Test that chunked reading successfully verifies file hash."""
    manifest_data = valid_manifest_dict()
    manifest = ArtifactManifest.from_mapping(manifest_data)

    artifact_file_path = tmp_path / manifest.local_path
    artifact_file_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_file_path.write_bytes(b"A" * 128)

    # Verify with chunk_size smaller than file size to verify loop
    result = verify_artifact(manifest, base_dir=tmp_path, chunk_size=10)
    assert result.size_bytes == 128
    assert (
        result.sha256
        == "b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e"
    )


def test_verify_artifact_permission_error(tmp_path: Path) -> None:
    """Test that permission denied during verification raises FilesystemPermissionError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    # Write exactly 128 bytes so it passes the size check before open raises PermissionError
    artifact_path.write_bytes(b"A" * 128)

    with patch("builtins.open", side_effect=PermissionError("Permission denied")):
        with pytest.raises(FilesystemPermissionError) as exc_info:
            verify_artifact(manifest, base_dir=tmp_path)
        assert exc_info.value.path == artifact_path
        assert "Permission denied" in str(exc_info.value)


def test_verify_artifact_os_error(tmp_path: Path) -> None:
    """Test that OS read errors during verification raise FilesystemReadError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    # Write exactly 128 bytes so it passes the size check before open raises OSError
    artifact_path.write_bytes(b"A" * 128)

    with patch("builtins.open", side_effect=OSError("Disk failure")):
        with pytest.raises(FilesystemReadError) as exc_info:
            verify_artifact(manifest, base_dir=tmp_path)
        assert exc_info.value.path == artifact_path
        assert "Read error accessing" in str(exc_info.value)
