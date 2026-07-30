"""Filesystem adapters for loading manifests and verifying artifacts."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.domain import ArtifactManifest, ArtifactManifestError


class FilesystemAdapterError(Exception):
    """Base exception for all filesystem adapter operations."""


class ManifestLoadError(FilesystemAdapterError):
    """Base exception for errors encountered while loading an artifact manifest."""


class ManifestFileNotFoundError(ManifestLoadError):
    """Raised when the manifest file does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Manifest file does not exist at '{path}'.")
        self.path = path


class ManifestEncodingError(ManifestLoadError):
    """Raised when the manifest file is not valid UTF-8."""

    def __init__(self, path: Path, error: UnicodeDecodeError) -> None:
        super().__init__(f"Manifest file at '{path}' is not valid UTF-8: {error}.")
        self.path = path
        self.error = error


class ManifestInvalidJsonError(ManifestLoadError):
    """Raised when the manifest file contains invalid JSON or root is not an object."""

    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(f"Manifest file at '{path}' contains invalid JSON: {error}.")
        self.path = path
        self.error = error


class ManifestValidationError(ManifestLoadError):
    """Raised when the manifest violates ArtifactManifest schema validation."""

    def __init__(self, path: Path, error: ArtifactManifestError) -> None:
        super().__init__(
            f"Manifest file at '{path}' violates validation rules: {error}."
        )
        self.path = path
        self.error = error


class VerificationError(FilesystemAdapterError):
    """Base exception for artifact file verification errors."""


class ArtifactFileNotFoundError(VerificationError):
    """Raised when the artifact file does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Artifact file does not exist at '{path}'.")
        self.path = path


class ArtifactNotARegularFileError(VerificationError):
    """Raised when the artifact path is not a regular file (e.g., is a directory)."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Artifact path is not a regular file: '{path}'.")
        self.path = path


class SizeMismatchError(VerificationError):
    """Raised when the calculated file size does not match the expected size."""

    def __init__(self, path: Path, expected: int, actual: int) -> None:
        super().__init__(
            f"Artifact size mismatch for file at '{path}': "
            f"expected {expected} bytes, got {actual} bytes."
        )
        self.path = path
        self.expected = expected
        self.actual = actual


class ChecksumMismatchError(VerificationError):
    """Raised when the calculated SHA-256 does not match the expected SHA-256."""

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        super().__init__(
            f"Artifact SHA-256 digest mismatch for file at '{path}': "
            f"expected {expected}, got {actual}."
        )
        self.path = path
        self.expected = expected
        self.actual = actual


class FilesystemPermissionError(FilesystemAdapterError):
    """Raised when a filesystem operation fails due to insufficient permissions."""

    def __init__(self, path: Path, error: PermissionError) -> None:
        super().__init__(f"Permission denied accessing '{path}': {error}.")
        self.path = path
        self.error = error


class FilesystemReadError(FilesystemAdapterError):
    """Raised when a filesystem read operation fails with an OS error."""

    def __init__(self, path: Path, error: OSError) -> None:
        super().__init__(f"Read error accessing '{path}': {error}.")
        self.path = path
        self.error = error


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Structured result of a successful artifact verification."""

    artifact_id: str
    file_path: Path
    size_bytes: int
    sha256: str


def load_manifest_from_file(path: Path) -> ArtifactManifest:
    """Load, parse, and validate an ArtifactManifest from a local JSON file.

    Args:
        path: Path to the JSON manifest file.

    Returns:
        A validated ArtifactManifest domain object.

    Raises:
        ManifestFileNotFoundError: If the manifest file does not exist.
        ManifestEncodingError: If the file is not valid UTF-8.
        ManifestInvalidJsonError: If JSON parsing fails or the root is not an object.
        ManifestValidationError: If domain validation fails.
        FilesystemPermissionError: If read permission is denied.
        FilesystemReadError: If other OS errors occur during reading.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError as error:
        raise ManifestFileNotFoundError(path) from error
    except UnicodeDecodeError as error:
        raise ManifestEncodingError(path, error) from error
    except PermissionError as error:
        raise FilesystemPermissionError(path, error) from error
    except OSError as error:
        raise FilesystemReadError(path, error) from error

    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise ManifestInvalidJsonError(path, error) from error

    if not isinstance(data, dict):
        raise ManifestInvalidJsonError(
            path, ValueError("JSON root must be a JSON object mapping.")
        )

    try:
        return ArtifactManifest.from_mapping(data)
    except ArtifactManifestError as error:
        raise ManifestValidationError(path, error) from error


def verify_local_file(
    path: Path,
    expected_size_bytes: int,
    expected_sha256: str,
    chunk_size: int = 65536,
) -> tuple[int, str]:
    """Calculate local file size and SHA-256, verifying against expected values.

    Args:
        path: Path to the local artifact file.
        expected_size_bytes: The expected size of the file in bytes.
        expected_sha256: The expected SHA-256 hex digest of the file.
        chunk_size: Chunk size in bytes for reading the file.

    Returns:
        A tuple of (actual_size_bytes, actual_sha256_hex).

    Raises:
        ArtifactFileNotFoundError: If the file does not exist.
        ArtifactNotARegularFileError: If the path is not a regular file.
        SizeMismatchError: If the actual file size differs from expected size.
        ChecksumMismatchError: If the actual SHA-256 digest differs from expected.
        FilesystemPermissionError: If read permission is denied.
        FilesystemReadError: If other OS errors occur during verification.
    """
    try:
        # Check existence and type using lstat/stat to handle permissions safely
        if not path.exists():
            raise ArtifactFileNotFoundError(path)
        if not path.is_file():
            raise ArtifactNotARegularFileError(path)
        actual_size = path.stat().st_size
    except PermissionError as error:
        raise FilesystemPermissionError(path, error) from error
    except OSError as error:
        raise FilesystemReadError(path, error) from error

    if actual_size != expected_size_bytes:
        raise SizeMismatchError(path, expected_size_bytes, actual_size)

    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
    except PermissionError as error:
        raise FilesystemPermissionError(path, error) from error
    except OSError as error:
        raise FilesystemReadError(path, error) from error

    actual_sha256 = hasher.hexdigest()
    if actual_sha256 != expected_sha256:
        raise ChecksumMismatchError(path, expected_sha256, actual_sha256)

    return actual_size, actual_sha256


def verify_artifact(
    manifest: ArtifactManifest,
    base_dir: Path,
    chunk_size: int = 65536,
) -> VerificationResult:
    """Verify an artifact file against its manifest, relative to a base directory.

    Args:
        manifest: The ArtifactManifest domain object.
        base_dir: Base directory to resolve the manifest's local_path against.
        chunk_size: Chunk size in bytes for reading the file.

    Returns:
        A VerificationResult containing successful verification details.

    Raises:
        ArtifactFileNotFoundError: If the file does not exist.
        ArtifactNotARegularFileError: If the path is not a regular file.
        SizeMismatchError: If the actual file size differs from expected size.
        ChecksumMismatchError: If the actual SHA-256 digest differs from expected.
        FilesystemPermissionError: If read permission is denied.
        FilesystemReadError: If other OS errors occur during verification.
    """
    artifact_path = base_dir / manifest.local_path

    actual_size, actual_sha256 = verify_local_file(
        path=artifact_path,
        expected_size_bytes=manifest.expected_size_bytes,
        expected_sha256=manifest.sha256,
        chunk_size=chunk_size,
    )

    return VerificationResult(
        artifact_id=manifest.artifact_id,
        file_path=artifact_path,
        size_bytes=actual_size,
        sha256=actual_sha256,
    )
