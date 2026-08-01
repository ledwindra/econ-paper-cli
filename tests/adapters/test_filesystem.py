"""Tests for local manifest loading and file checksum verification adapters."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from econ_paper_cli.adapters.filesystem import (
    ArtifactFileNotFoundError,
    ArtifactNotARegularFileError,
    ChecksumMismatchError,
    FilesystemPermissionError,
    FilesystemReadError,
    InvalidChunkSizeError,
    ManifestEncodingError,
    ManifestFileNotFoundError,
    ManifestInvalidJsonError,
    ManifestLoadError,
    ManifestPermissionError,
    ManifestReadError,
    ManifestValidationError,
    SizeMismatchError,
    VerificationError,
    VerificationPermissionError,
    VerificationReadError,
    inspect_local_file,
    load_manifest_from_file,
    verify_artifact,
    verify_local_file,
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
    data["expected_size_bytes"] = -10
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest_from_file(manifest_path)
    assert exc_info.value.path == manifest_path
    assert isinstance(exc_info.value.error, ArtifactManifestError)


def test_load_manifest_permission_error(tmp_path: Path) -> None:
    """Test that permission denied during manifest load raises ManifestPermissionError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.touch()

    with patch("builtins.open", side_effect=PermissionError("Permission denied")):
        with pytest.raises(ManifestPermissionError) as exc_info:
            load_manifest_from_file(manifest_path)
        assert exc_info.value.path == manifest_path
        assert "Permission denied" in str(exc_info.value)
        # Verify inheritance hierarchy
        assert isinstance(exc_info.value, ManifestLoadError)
        assert isinstance(exc_info.value, FilesystemPermissionError)


def test_load_manifest_os_error(tmp_path: Path) -> None:
    """Test that general OS errors during manifest load raise ManifestReadError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.touch()

    with patch("builtins.open", side_effect=OSError("Read failure")):
        with pytest.raises(ManifestReadError) as exc_info:
            load_manifest_from_file(manifest_path)
        assert exc_info.value.path == manifest_path
        assert "Read error accessing" in str(exc_info.value)
        # Verify inheritance hierarchy
        assert isinstance(exc_info.value, ManifestLoadError)
        assert isinstance(exc_info.value, FilesystemReadError)


@pytest.mark.parametrize(
    "error_func",
    [
        lambda p: load_manifest_from_file(p / "missing.json"),
        lambda p: load_manifest_from_file(
            p.joinpath("bad_utf8.json").side_effect
            if False
            else _create_and_return(p / "bad_utf8.json", b"\xff\xff")
        ),
        lambda p: load_manifest_from_file(
            _create_and_return(p / "bad_json.json", b"{bad json")
        ),
        lambda p: load_manifest_from_file(
            _create_and_return(p / "bad_root.json", b"123")
        ),
    ],
)
def test_manifest_load_error_catches_all_manifest_failures(
    tmp_path: Path, error_func: object
) -> None:
    """Verify that catching ManifestLoadError catches all manifest loading failures."""
    with pytest.raises(ManifestLoadError):
        error_func(tmp_path)


def _create_and_return(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


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
    assert isinstance(exc_info.value, VerificationError)


def test_verify_artifact_not_a_regular_file(tmp_path: Path) -> None:
    """Test that directory targets instead of regular files raise ArtifactNotARegularFileError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ArtifactNotARegularFileError) as exc_info:
        verify_artifact(manifest, base_dir=tmp_path)
    assert exc_info.value.path == artifact_path
    assert "not a regular file" in str(exc_info.value)
    assert isinstance(exc_info.value, VerificationError)


def test_verify_artifact_size_mismatch(tmp_path: Path) -> None:
    """Test that size mismatch raises SizeMismatchError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"A" * 100)

    with pytest.raises(SizeMismatchError) as exc_info:
        verify_artifact(manifest, base_dir=tmp_path)
    assert exc_info.value.path == artifact_path
    assert exc_info.value.expected == 128
    assert exc_info.value.actual == 100
    assert "size mismatch" in str(exc_info.value)
    assert isinstance(exc_info.value, VerificationError)


def test_verify_artifact_checksum_mismatch(tmp_path: Path) -> None:
    """Test that checksum mismatch raises ChecksumMismatchError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
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
    assert isinstance(exc_info.value, VerificationError)


def test_verify_artifact_chunk_size(tmp_path: Path) -> None:
    """Test that chunked reading successfully verifies file hash."""
    manifest_data = valid_manifest_dict()
    manifest = ArtifactManifest.from_mapping(manifest_data)

    artifact_file_path = tmp_path / manifest.local_path
    artifact_file_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_file_path.write_bytes(b"A" * 128)

    result = verify_artifact(manifest, base_dir=tmp_path, chunk_size=10)
    assert result.size_bytes == 128
    assert (
        result.sha256
        == "b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e"
    )


@pytest.mark.parametrize(
    "invalid_chunk_size",
    [0, -1, -65536, True, False, 1.5, "65536", None, []],
)
def test_verify_local_file_rejects_invalid_chunk_size(
    tmp_path: Path, invalid_chunk_size: object
) -> None:
    """Verify that invalid chunk sizes raise InvalidChunkSizeError."""
    artifact_file_path = tmp_path / "artifact.bin"
    artifact_file_path.write_bytes(b"A" * 128)

    with pytest.raises(InvalidChunkSizeError) as exc_info:
        verify_local_file(
            path=artifact_file_path,
            expected_size_bytes=128,
            expected_sha256="b6ac3cc10386331c765f04f041c147d0f278f2aed8eaa021e2d0057fc6f6ff9e",
            chunk_size=invalid_chunk_size,  # type: ignore[arg-type]
        )

    assert exc_info.value.chunk_size == invalid_chunk_size
    assert "positive integer" in str(exc_info.value)
    assert isinstance(exc_info.value, VerificationError)
    assert isinstance(exc_info.value, ValueError)


def test_negative_chunk_size_does_not_read_file_into_memory(tmp_path: Path) -> None:
    """Confirm that negative chunk_size fails immediately without opening or reading the file."""
    artifact_file_path = tmp_path / "artifact.bin"
    artifact_file_path.write_bytes(b"A" * 128)

    mock_path = MagicMock(spec=Path)
    # Ensure stat/exists aren't even reached if validation runs first
    with pytest.raises(InvalidChunkSizeError):
        verify_local_file(
            path=mock_path,
            expected_size_bytes=128,
            expected_sha256="dummy",
            chunk_size=-1,
        )

    mock_path.exists.assert_not_called()
    mock_path.is_file.assert_not_called()
    mock_path.stat.assert_not_called()


def test_zero_chunk_size_does_not_verify_empty_stream(tmp_path: Path) -> None:
    """Confirm that chunk_size=0 fails validation instead of calculating an empty stream SHA-256."""
    artifact_file_path = tmp_path / "artifact.bin"
    artifact_file_path.write_bytes(b"A" * 128)

    with pytest.raises(InvalidChunkSizeError):
        verify_local_file(
            path=artifact_file_path,
            expected_size_bytes=128,
            expected_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # Hash of empty bytes
            chunk_size=0,
        )


def test_verify_artifact_permission_error(tmp_path: Path) -> None:
    """Test that permission denied during verification raises VerificationPermissionError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"A" * 128)

    with patch("builtins.open", side_effect=PermissionError("Permission denied")):
        with pytest.raises(VerificationPermissionError) as exc_info:
            verify_artifact(manifest, base_dir=tmp_path)
        assert exc_info.value.path == artifact_path
        assert "Permission denied" in str(exc_info.value)
        # Verify inheritance hierarchy
        assert isinstance(exc_info.value, VerificationError)
        assert isinstance(exc_info.value, FilesystemPermissionError)


def test_verify_artifact_os_error(tmp_path: Path) -> None:
    """Test that OS read errors during verification raise VerificationReadError."""
    manifest = ArtifactManifest.from_mapping(valid_manifest_dict())
    artifact_path = tmp_path / manifest.local_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"A" * 128)

    with patch("builtins.open", side_effect=OSError("Disk failure")):
        with pytest.raises(VerificationReadError) as exc_info:
            verify_artifact(manifest, base_dir=tmp_path)
        assert exc_info.value.path == artifact_path
        assert "Read error accessing" in str(exc_info.value)
        # Verify inheritance hierarchy
        assert isinstance(exc_info.value, VerificationError)
        assert isinstance(exc_info.value, FilesystemReadError)


def test_inspect_local_file_success(tmp_path: Path) -> None:
    test_file = tmp_path / "sample.pdf"
    content = b"Sample PDF bytes for inspection"
    test_file.write_bytes(content)

    result = inspect_local_file(test_file)
    assert result.file_path == test_file.resolve()
    assert result.size_bytes == len(content)
    assert result.sha256 == hashlib.sha256(content).hexdigest().lower()


def test_inspect_local_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.pdf"
    with pytest.raises(ArtifactFileNotFoundError):
        inspect_local_file(missing)


def test_inspect_local_file_not_a_regular_file(tmp_path: Path) -> None:
    dir_path = tmp_path / "a_dir"
    dir_path.mkdir()
    with pytest.raises(ArtifactNotARegularFileError):
        inspect_local_file(dir_path)
