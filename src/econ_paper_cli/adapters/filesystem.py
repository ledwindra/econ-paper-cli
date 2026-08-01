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


class FilesystemPermissionError(FilesystemAdapterError):
    """Base exception for filesystem permission errors."""

    def __init__(self, path: Path, error: PermissionError) -> None:
        super().__init__(f"Permission denied accessing '{path}': {error}.")
        self.path = path
        self.error = error


class FilesystemReadError(FilesystemAdapterError):
    """Base exception for filesystem read OS errors."""

    def __init__(self, path: Path, error: OSError) -> None:
        super().__init__(f"Read error accessing '{path}': {error}.")
        self.path = path
        self.error = error


class ManifestPermissionError(ManifestLoadError, FilesystemPermissionError):
    """Raised when reading a manifest fails due to permission denied."""


class ManifestReadError(ManifestLoadError, FilesystemReadError):
    """Raised when reading a manifest fails due to an OS error."""


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


class InvalidChunkSizeError(VerificationError, ValueError):
    """Raised when chunk_size is not a positive integer."""

    def __init__(self, chunk_size: object) -> None:
        super().__init__(
            f"chunk_size must be a positive integer; got {chunk_size!r} "
            f"({type(chunk_size).__name__})."
        )
        self.chunk_size = chunk_size


class VerificationPermissionError(VerificationError, FilesystemPermissionError):
    """Raised when verifying an artifact fails due to permission denied."""


class VerificationReadError(VerificationError, FilesystemReadError):
    """Raised when verifying an artifact fails due to an OS error."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Structured result of a successful artifact verification."""

    artifact_id: str
    file_path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class FileInspectionResult:
    """Canonical path, size, and SHA-256 hex digest of a local file."""

    file_path: Path
    size_bytes: int
    sha256: str


def inspect_local_file(
    path: Path,
    chunk_size: int = 65536,
) -> FileInspectionResult:
    """Inspect a local file, returning its canonical path, size, and SHA-256 digest.

    Args:
        path: Path to the local file.
        chunk_size: Chunk size in bytes for reading the file. Must be a positive int.

    Returns:
        A FileInspectionResult containing canonical file_path, size_bytes, and lowercase sha256.

    Raises:
        InvalidChunkSizeError: If chunk_size is not a positive integer.
        ArtifactFileNotFoundError: If the file does not exist.
        ArtifactNotARegularFileError: If the path is not a regular file.
        VerificationPermissionError: If read permission is denied.
        VerificationReadError: If other OS errors occur during inspection.
    """
    validated_chunk_size = _validate_chunk_size(chunk_size)

    try:
        resolved_path = path.resolve()
        if not resolved_path.exists():
            raise ArtifactFileNotFoundError(resolved_path)
        if not resolved_path.is_file():
            raise ArtifactNotARegularFileError(resolved_path)
        actual_size = resolved_path.stat().st_size
    except (ArtifactFileNotFoundError, ArtifactNotARegularFileError):
        raise
    except PermissionError as error:
        raise VerificationPermissionError(path, error) from error
    except OSError as error:
        raise VerificationReadError(path, error) from error

    hasher = hashlib.sha256()
    try:
        with open(resolved_path, "rb") as f:
            while True:
                chunk = f.read(validated_chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
    except PermissionError as error:
        raise VerificationPermissionError(resolved_path, error) from error
    except OSError as error:
        raise VerificationReadError(resolved_path, error) from error

    return FileInspectionResult(
        file_path=resolved_path,
        size_bytes=actual_size,
        sha256=hasher.hexdigest().lower(),
    )


def _validate_chunk_size(chunk_size: object) -> int:
    """Validate that chunk_size is a positive integer.

    Args:
        chunk_size: The value to validate.

    Returns:
        The validated chunk_size as an integer.

    Raises:
        InvalidChunkSizeError: If chunk_size is a bool, <= 0, or not an integer.
    """
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size <= 0
    ):
        raise InvalidChunkSizeError(chunk_size)
    return chunk_size


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
        ManifestPermissionError: If read permission is denied.
        ManifestReadError: If other OS errors occur during reading.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError as error:
        raise ManifestFileNotFoundError(path) from error
    except UnicodeDecodeError as error:
        raise ManifestEncodingError(path, error) from error
    except PermissionError as error:
        raise ManifestPermissionError(path, error) from error
    except OSError as error:
        raise ManifestReadError(path, error) from error

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
        chunk_size: Chunk size in bytes for reading the file. Must be a positive int.

    Returns:
        A tuple of (actual_size_bytes, actual_sha256_hex).

    Raises:
        InvalidChunkSizeError: If chunk_size is not a positive integer.
        ArtifactFileNotFoundError: If the file does not exist.
        ArtifactNotARegularFileError: If the path is not a regular file.
        SizeMismatchError: If the actual file size differs from expected size.
        ChecksumMismatchError: If the actual SHA-256 digest differs from expected.
        VerificationPermissionError: If read permission is denied.
        VerificationReadError: If other OS errors occur during verification.
    """
    # Delegate inspection and hashing to inspect_local_file; chunk_size validation
    # happens inside inspect_local_file via _validate_chunk_size.
    inspection = inspect_local_file(path, chunk_size=chunk_size)

    if inspection.size_bytes != expected_size_bytes:
        raise SizeMismatchError(
            inspection.file_path, expected_size_bytes, inspection.size_bytes
        )

    actual_sha256 = inspection.sha256
    if actual_sha256 != expected_sha256.lower():
        raise ChecksumMismatchError(
            inspection.file_path, expected_sha256, actual_sha256
        )

    return inspection.size_bytes, actual_sha256


def verify_artifact(
    manifest: ArtifactManifest,
    base_dir: Path,
    chunk_size: int = 65536,
) -> VerificationResult:
    """Verify an artifact file against its manifest, relative to a base directory.

    Args:
        manifest: The ArtifactManifest domain object.
        base_dir: Base directory to resolve the manifest's local_path against.
        chunk_size: Chunk size in bytes for reading the file. Must be a positive int.

    Returns:
        A VerificationResult containing successful verification details.

    Raises:
        InvalidChunkSizeError: If chunk_size is not a positive integer.
        ArtifactFileNotFoundError: If the file does not exist.
        ArtifactNotARegularFileError: If the path is not a regular file.
        SizeMismatchError: If the actual file size differs from expected size.
        ChecksumMismatchError: If the actual SHA-256 digest differs from expected.
        VerificationPermissionError: If read permission is denied.
        VerificationReadError: If other OS errors occur during verification.
    """
    validated_chunk_size = _validate_chunk_size(chunk_size)
    artifact_path = base_dir / manifest.local_path

    actual_size, actual_sha256 = verify_local_file(
        path=artifact_path,
        expected_size_bytes=manifest.expected_size_bytes,
        expected_sha256=manifest.sha256,
        chunk_size=validated_chunk_size,
    )

    return VerificationResult(
        artifact_id=manifest.artifact_id,
        file_path=artifact_path,
        size_bytes=actual_size,
        sha256=actual_sha256,
    )
