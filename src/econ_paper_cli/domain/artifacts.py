"""Pure domain contracts for locally managed artifacts."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypeVar, cast

from econ_paper_cli.domain.errors import DomainError

_ARTIFACT_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
_SERIALIZED_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_id",
        "kind",
        "version",
        "source",
        "license",
        "redistribution_status",
        "expected_size_bytes",
        "sha256",
        "update_policy",
        "contains_copyrighted_full_text",
        "local_path",
    }
)


class ArtifactKind(str, Enum):
    """The role an artifact has in the local application."""

    CORPUS = "corpus"
    INDEX = "index"
    MODEL = "model"


class RedistributionStatus(str, Enum):
    """The documented redistribution status of an artifact."""

    PERMITTED = "permitted"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


_EnumT = TypeVar("_EnumT", ArtifactKind, RedistributionStatus)


class ArtifactManifestError(DomainError):
    """Raised when artifact manifest data violates the schema."""


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Validated metadata for one locally managed artifact."""

    schema_version: int
    artifact_id: str
    kind: ArtifactKind
    version: str
    source: str
    license_name: str
    redistribution_status: RedistributionStatus
    expected_size_bytes: int
    sha256: str
    update_policy: str
    contains_copyrighted_full_text: bool
    local_path: Path

    def __post_init__(self) -> None:
        """Validate direct construction as strictly as parsed construction."""
        _validate_schema_version(self.schema_version)
        _validate_artifact_id(self.artifact_id)
        _validate_enum("kind", self.kind, ArtifactKind)
        _validate_nonempty_text("version", self.version)
        _validate_nonempty_text("source", self.source)
        _validate_nonempty_text("license", self.license_name)
        _validate_enum(
            "redistribution_status",
            self.redistribution_status,
            RedistributionStatus,
        )
        _validate_expected_size(self.expected_size_bytes)
        _validate_sha256(self.sha256)
        _validate_nonempty_text("update_policy", self.update_policy)
        if not isinstance(self.contains_copyrighted_full_text, bool):
            raise ArtifactManifestError(
                "contains_copyrighted_full_text must be a boolean."
            )
        _validate_local_path(self.local_path)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ArtifactManifest":
        """Validate and construct a manifest from JSON-compatible data."""
        if not isinstance(data, Mapping):
            raise ArtifactManifestError("Artifact manifest must be a mapping.")
        if any(not isinstance(field, str) for field in data):
            raise ArtifactManifestError(
                "Artifact manifest field names must be strings."
            )

        provided_fields = set(data)
        missing_fields = sorted(_SERIALIZED_FIELDS - provided_fields)
        if missing_fields:
            raise ArtifactManifestError(
                "Artifact manifest is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided_fields - _SERIALIZED_FIELDS)
        if unknown_fields:
            raise ArtifactManifestError(
                "Artifact manifest contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        return cls(
            schema_version=cast(int, data["schema_version"]),
            artifact_id=cast(str, data["artifact_id"]),
            kind=_parse_enum("kind", data["kind"], ArtifactKind),
            version=cast(str, data["version"]),
            source=cast(str, data["source"]),
            license_name=cast(str, data["license"]),
            redistribution_status=_parse_enum(
                "redistribution_status",
                data["redistribution_status"],
                RedistributionStatus,
            ),
            expected_size_bytes=cast(int, data["expected_size_bytes"]),
            sha256=cast(str, data["sha256"]),
            update_policy=cast(str, data["update_policy"]),
            contains_copyrighted_full_text=cast(
                bool,
                data["contains_copyrighted_full_text"],
            ),
            local_path=_parse_local_path(data["local_path"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "version": self.version,
            "source": self.source,
            "license": self.license_name,
            "redistribution_status": self.redistribution_status.value,
            "expected_size_bytes": self.expected_size_bytes,
            "sha256": self.sha256,
            "update_policy": self.update_policy,
            "contains_copyrighted_full_text": (self.contains_copyrighted_full_text),
            "local_path": self.local_path.as_posix(),
        }


def _allowed_values(enum_type: type[Enum]) -> str:
    return ", ".join(str(member.value) for member in enum_type)


def _parse_enum(
    field: str,
    value: object,
    enum_type: type[_EnumT],
) -> _EnumT:
    if not isinstance(value, str):
        raise ArtifactManifestError(
            f"{field} must be one of: {_allowed_values(enum_type)}."
        )
    try:
        return enum_type(value)
    except ValueError as error:
        raise ArtifactManifestError(
            f"{field} must be one of: {_allowed_values(enum_type)}; got {value!r}."
        ) from error


def _validate_enum(field: str, value: object, enum_type: type[Enum]) -> None:
    if not isinstance(value, enum_type):
        raise ArtifactManifestError(
            f"{field} must be one of: {_allowed_values(enum_type)}."
        )


def _validate_schema_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise ArtifactManifestError("schema_version must be the integer 1.")


def _validate_artifact_id(value: object) -> None:
    if not isinstance(value, str) or _ARTIFACT_ID_PATTERN.fullmatch(value) is None:
        raise ArtifactManifestError(
            "artifact_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_nonempty_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactManifestError(f"{field} must be a nonempty string.")


def _validate_expected_size(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactManifestError("expected_size_bytes must be a positive integer.")


def _validate_sha256(value: object) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ArtifactManifestError(
            "sha256 must contain exactly 64 lowercase hexadecimal characters."
        )


def _parse_local_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ArtifactManifestError(
            "local_path must be a nonempty portable relative path."
        )
    if "\\" in value:
        raise ArtifactManifestError(
            "local_path must use forward slashes and be relative."
        )
    return Path(value)


def _validate_local_path(path: object) -> None:
    if not isinstance(path, Path):
        raise ArtifactManifestError("local_path must be a pathlib.Path.")

    raw_path = str(path)
    if "\\" in raw_path and not isinstance(path, PureWindowsPath):
        raise ArtifactManifestError("local_path must use the platform path separator.")
    posix_path = PurePosixPath(raw_path.replace("\\", "/"))
    windows_path = PureWindowsPath(raw_path)
    if (
        raw_path in {"", "."}
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
    ):
        raise ArtifactManifestError(
            "local_path must be a nonempty relative path without parent traversal."
        )
    _validate_portable_path_parts(posix_path.parts)


def _validate_portable_path_parts(parts: tuple[str, ...]) -> None:
    for part in parts:
        reserved_name = part.split(".", maxsplit=1)[0].upper()
        if (
            any(ord(character) < 32 for character in part)
            or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part)
            or part.endswith((" ", "."))
            or reserved_name in _WINDOWS_RESERVED_NAMES
        ):
            raise ArtifactManifestError(
                "local_path contains a component that is not portable across "
                "Windows, macOS, and Linux."
            )
