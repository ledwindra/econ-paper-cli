"""Pure domain contracts for the version-controlled managed runtime manifest.

Describes exactly which pinned ``llama.cpp`` release archive is installed for
each supported platform/architecture. No filesystem or network access here;
see ``adapters.runtime_downloader``/``adapters.runtime_extractor`` for that.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from econ_paper_cli.domain.errors import DomainError

_RUNTIME_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SupportedPlatform(str, Enum):
    """Operating systems with a pinned managed-runtime artifact."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"


class SupportedArchitecture(str, Enum):
    """CPU architectures with a pinned managed-runtime artifact."""

    ARM64 = "arm64"
    X86_64 = "x86_64"


class ArchiveFormat(str, Enum):
    """Archive container formats a managed-runtime artifact may use."""

    TAR_GZ = "tar.gz"
    ZIP = "zip"


class ManagedRuntimeManifestError(DomainError):
    """Raised when managed-runtime manifest data violates the schema."""


@dataclass(frozen=True, slots=True)
class ManagedRuntimeArtifact:
    """Validated pinned-release metadata for one platform/architecture.

    ``archive_sha256`` is the integrity anchor for the download step: the
    whole archive is verified against it before any extraction is attempted.
    ``bundle_member_checksums`` anchors per-file integrity of the
    *installed* bundle in this version-controlled manifest itself, not only
    in the mutable ``receipt.json`` an install writes — a modified
    executable/library plus a correspondingly rewritten receipt must still
    fail verification against this static declaration.
    """

    runtime_id: str
    version_marker: str
    platform: SupportedPlatform
    architecture: SupportedArchitecture
    source_url: str
    archive_format: ArchiveFormat
    archive_size_bytes: int
    archive_sha256: str
    executable_relative_path: PurePosixPath
    bundle_member_checksums: tuple[tuple[PurePosixPath, str], ...]
    license_name: str
    attribution_text: str

    def __post_init__(self) -> None:
        _validate_runtime_id("runtime_id", self.runtime_id)
        _validate_nonempty_text("version_marker", self.version_marker)
        _validate_enum("platform", self.platform, SupportedPlatform)
        _validate_enum("architecture", self.architecture, SupportedArchitecture)
        _validate_https_url(self.source_url)
        _validate_enum("archive_format", self.archive_format, ArchiveFormat)
        _validate_positive_int("archive_size_bytes", self.archive_size_bytes)
        _validate_sha256(self.archive_sha256)
        _validate_relative_archive_member(
            "executable_relative_path", self.executable_relative_path
        )
        _validate_bundle_member_checksums(self.bundle_member_checksums)
        member_paths = {path for path, _ in self.bundle_member_checksums}
        if self.executable_relative_path not in member_paths:
            raise ManagedRuntimeManifestError(
                "bundle_member_checksums must include an entry for "
                "executable_relative_path."
            )
        _validate_nonempty_text("license_name", self.license_name)
        _validate_nonempty_text("attribution_text", self.attribution_text)


@dataclass(frozen=True, slots=True)
class ManagedRuntimeManifest:
    """A schema-versioned collection of pinned per-platform artifacts."""

    schema_version: int
    artifacts: tuple[ManagedRuntimeArtifact, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ManagedRuntimeManifestError("schema_version must be the integer 1.")
        if not isinstance(self.artifacts, tuple) or not self.artifacts:
            raise ManagedRuntimeManifestError(
                "artifacts must be a nonempty tuple of ManagedRuntimeArtifact."
            )
        seen_keys: set[tuple[SupportedPlatform, SupportedArchitecture]] = set()
        for artifact in self.artifacts:
            if not isinstance(artifact, ManagedRuntimeArtifact):
                raise ManagedRuntimeManifestError(
                    "Every manifest entry must be a ManagedRuntimeArtifact."
                )
            key = (artifact.platform, artifact.architecture)
            if key in seen_keys:
                raise ManagedRuntimeManifestError(
                    f"Duplicate manifest entry for platform={artifact.platform.value} "
                    f"architecture={artifact.architecture.value}."
                )
            seen_keys.add(key)


def select_artifact_for_platform(
    manifest: ManagedRuntimeManifest,
    platform: SupportedPlatform,
    architecture: SupportedArchitecture,
) -> ManagedRuntimeArtifact | None:
    """Return the pinned artifact for a platform/architecture, if any."""
    for artifact in manifest.artifacts:
        if artifact.platform is platform and artifact.architecture is architecture:
            return artifact
    return None


def _validate_enum(field: str, value: object, enum_type: type[Enum]) -> None:
    if not isinstance(value, enum_type):
        allowed = ", ".join(str(member.value) for member in enum_type)
        raise ManagedRuntimeManifestError(f"{field} must be one of: {allowed}.")


def _validate_runtime_id(field: str, value: object) -> None:
    if not isinstance(value, str) or _RUNTIME_ID_PATTERN.fullmatch(value) is None:
        raise ManagedRuntimeManifestError(
            f"{field} must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ManagedRuntimeManifestError(f"{field} must be a nonempty string.")


def _validate_https_url(value: object) -> None:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ManagedRuntimeManifestError("source_url must be an https:// URL.")


def _validate_positive_int(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ManagedRuntimeManifestError(f"{field} must be a positive integer.")


def _validate_sha256(value: object, field: str = "archive_sha256") -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ManagedRuntimeManifestError(
            f"{field} must contain exactly 64 lowercase hexadecimal characters."
        )


def _validate_relative_archive_member(field: str, value: object) -> None:
    if not isinstance(value, PurePosixPath):
        raise ManagedRuntimeManifestError(f"{field} must be a PurePosixPath.")
    raw = value.as_posix()
    if (
        raw in {"", "."}
        or raw.startswith("/")
        or ".." in value.parts
        or any(part == "" for part in value.parts)
    ):
        raise ManagedRuntimeManifestError(
            f"{field} must be a nonempty relative archive member path "
            "without parent traversal or an absolute prefix."
        )


def _validate_bundle_member_checksums(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise ManagedRuntimeManifestError(
            "bundle_member_checksums must be a nonempty tuple of (path, sha256) pairs."
        )
    seen_paths: set[PurePosixPath] = set()
    for entry in value:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ManagedRuntimeManifestError(
                "Each bundle_member_checksums entry must be a (path, sha256) pair."
            )
        path, sha256 = entry
        _validate_relative_archive_member("bundle_member_checksums path", path)
        _validate_sha256(sha256, "bundle_member_checksums sha256")
        if path in seen_paths:
            raise ManagedRuntimeManifestError(
                f"bundle_member_checksums contains a duplicate path: "
                f"'{path.as_posix()}'."
            )
        seen_paths.add(path)
