"""Pure domain contract for a managed-runtime install receipt.

Written once, atomically, immediately after a successful managed-runtime
install (see ``services.runtime_provisioning``). Captures exactly what was
installed — including a per-file checksum for every extracted bundle
member, not just the download-time archive checksum — so a later reuse or
``status`` check can detect on-disk corruption or tampering of the
*installed* files independently of whether the original download was valid.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import cast

from econ_paper_cli.domain.errors import DomainError
from econ_paper_cli.domain.runtime_manifest import (
    SupportedArchitecture,
    SupportedPlatform,
)

_RUNTIME_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SERIALIZED_FIELDS = frozenset(
    {
        "schema_version",
        "runtime_id",
        "version_marker",
        "platform",
        "architecture",
        "source_asset_identity",
        "archive_size_bytes",
        "archive_sha256",
        "executable_relative_path",
        "executable_sha256",
        "member_checksums",
    }
)


class InstallReceiptError(DomainError):
    """Raised when install-receipt data violates the schema."""


@dataclass(frozen=True, slots=True)
class InstallReceipt:
    """Validated record of exactly what was installed at a content-addressed path.

    ``member_checksums`` is the full closure of every regular file actually
    extracted (relative path -> SHA-256), computed at install time — the
    "required runtime-bundle member set" that reuse/status checks validate
    against, since a managed llama.cpp install includes adjacent shared
    libraries the executable depends on, not just the top-level binary.
    """

    schema_version: int
    runtime_id: str
    version_marker: str
    platform: SupportedPlatform
    architecture: SupportedArchitecture
    source_asset_identity: str
    archive_size_bytes: int
    archive_sha256: str
    executable_relative_path: PurePosixPath
    executable_sha256: str
    member_checksums: tuple[tuple[PurePosixPath, str], ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise InstallReceiptError("schema_version must be the integer 1.")
        _validate_runtime_id(self.runtime_id)
        _validate_nonempty_text("version_marker", self.version_marker)
        _validate_enum("platform", self.platform, SupportedPlatform)
        _validate_enum("architecture", self.architecture, SupportedArchitecture)
        _validate_nonempty_text("source_asset_identity", self.source_asset_identity)
        _validate_positive_int("archive_size_bytes", self.archive_size_bytes)
        _validate_sha256("archive_sha256", self.archive_sha256)
        _validate_relative_path(
            "executable_relative_path", self.executable_relative_path
        )
        _validate_sha256("executable_sha256", self.executable_sha256)
        _validate_member_checksums(self.member_checksums)
        member_paths = {path for path, _ in self.member_checksums}
        if self.executable_relative_path not in member_paths:
            raise InstallReceiptError(
                "member_checksums must include an entry for executable_relative_path."
            )

    def member_checksum_map(self) -> dict[PurePosixPath, str]:
        """Return member checksums as a plain dict keyed by relative path."""
        return dict(self.member_checksums)

    @classmethod
    def from_mapping(cls, data: object) -> "InstallReceipt":
        """Validate and construct a receipt from JSON-compatible data."""
        if not isinstance(data, dict):
            raise InstallReceiptError("Install receipt must be a mapping.")
        provided_fields = set(data)
        missing_fields = sorted(_SERIALIZED_FIELDS - provided_fields)
        if missing_fields:
            raise InstallReceiptError(
                "Install receipt is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )
        unknown_fields = sorted(provided_fields - _SERIALIZED_FIELDS)
        if unknown_fields:
            raise InstallReceiptError(
                "Install receipt contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        raw_members = data["member_checksums"]
        if not isinstance(raw_members, list):
            raise InstallReceiptError(
                "member_checksums must be a list of [path, sha256] pairs."
            )
        try:
            member_checksums = tuple(
                (PurePosixPath(cast(str, pair[0])), cast(str, pair[1]))
                for pair in raw_members
            )
        except (TypeError, IndexError) as error:
            raise InstallReceiptError(
                "member_checksums must be a list of [path, sha256] pairs."
            ) from error

        try:
            platform_value = SupportedPlatform(cast(str, data["platform"]))
            architecture_value = SupportedArchitecture(cast(str, data["architecture"]))
        except ValueError as error:
            raise InstallReceiptError(
                f"Invalid platform/architecture: {error}."
            ) from error

        return cls(
            schema_version=cast(int, data["schema_version"]),
            runtime_id=cast(str, data["runtime_id"]),
            version_marker=cast(str, data["version_marker"]),
            platform=platform_value,
            architecture=architecture_value,
            source_asset_identity=cast(str, data["source_asset_identity"]),
            archive_size_bytes=cast(int, data["archive_size_bytes"]),
            archive_sha256=cast(str, data["archive_sha256"]),
            executable_relative_path=PurePosixPath(
                cast(str, data["executable_relative_path"])
            ),
            executable_sha256=cast(str, data["executable_sha256"]),
            member_checksums=member_checksums,
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "runtime_id": self.runtime_id,
            "version_marker": self.version_marker,
            "platform": self.platform.value,
            "architecture": self.architecture.value,
            "source_asset_identity": self.source_asset_identity,
            "archive_size_bytes": self.archive_size_bytes,
            "archive_sha256": self.archive_sha256,
            "executable_relative_path": self.executable_relative_path.as_posix(),
            "executable_sha256": self.executable_sha256,
            "member_checksums": [
                [path.as_posix(), sha256] for path, sha256 in self.member_checksums
            ],
        }


def _validate_enum(field: str, value: object, enum_type: type[Enum]) -> None:
    if not isinstance(value, enum_type):
        allowed = ", ".join(str(member.value) for member in enum_type)
        raise InstallReceiptError(f"{field} must be one of: {allowed}.")


def _validate_runtime_id(value: object) -> None:
    if not isinstance(value, str) or _RUNTIME_ID_PATTERN.fullmatch(value) is None:
        raise InstallReceiptError("runtime_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*.")


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InstallReceiptError(f"{field} must be a nonempty string.")


def _validate_positive_int(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InstallReceiptError(f"{field} must be a positive integer.")


def _validate_sha256(field: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise InstallReceiptError(
            f"{field} must contain exactly 64 lowercase hexadecimal characters."
        )


def _validate_relative_path(field: str, value: object) -> None:
    if not isinstance(value, PurePosixPath):
        raise InstallReceiptError(f"{field} must be a PurePosixPath.")
    raw = value.as_posix()
    if raw in {"", "."} or raw.startswith("/") or ".." in value.parts:
        raise InstallReceiptError(
            f"{field} must be a nonempty relative path without parent traversal "
            "or an absolute prefix."
        )


def _validate_member_checksums(
    member_checksums: object,
) -> None:
    if not isinstance(member_checksums, tuple) or not member_checksums:
        raise InstallReceiptError(
            "member_checksums must be a nonempty tuple of (path, sha256) pairs."
        )
    seen_paths: set[PurePosixPath] = set()
    for entry in member_checksums:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise InstallReceiptError(
                "Each member_checksums entry must be a (path, sha256) pair."
            )
        path, sha256 = entry
        _validate_relative_path("member_checksums path", path)
        _validate_sha256("member_checksums sha256", sha256)
        if path in seen_paths:
            raise InstallReceiptError(
                f"member_checksums contains a duplicate path: '{path.as_posix()}'."
            )
        seen_paths.add(path)
