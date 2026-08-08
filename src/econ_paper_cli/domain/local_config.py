"""Immutable, versioned durable local runtime/model configuration contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.domain.errors import LocalConfigValidationError

LOCAL_CONFIG_SCHEMA_VERSION = 3
"""Current schema version written by this build.

Bumped from 2 to 3 when ``managed_model_provisioning`` was added: that field is
new *serialized* content, so a config written by this build must not claim to be
schema version 1 or 2. Versions 1 and 2 files remain fully readable — see
``READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS`` — and are transparently upgraded to
version 3 the next time configuration is written.
"""

READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS = frozenset({1, 2, 3})

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MODEL_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")

_REQUIRED_FIELDS = (
    "schema_version",
    "executable_path",
    "model_path",
    "model_id",
    "model_bytes",
    "model_checksum",
)
_OPTIONAL_FIELDS = (
    "threads",
    "timeout_seconds",
    "db_path",
    "runtime_id",
    "runtime_version_marker",
    "managed_model_provisioning",
)
_ALL_FIELDS = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)

_SCHEMA_2_ONLY_FIELDS = frozenset({"runtime_id", "runtime_version_marker"})
_SCHEMA_3_ONLY_FIELDS = frozenset({"managed_model_provisioning"})


def _validate_nonempty_path(field_name: str, value: object) -> Path:
    if isinstance(value, Path):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise LocalConfigValidationError(
            f"{field_name} must be a string or pathlib.Path, got {type(value).__name__}."
        )
    if not text.strip():
        raise LocalConfigValidationError(f"{field_name} must not be empty.")
    return Path(text)


@dataclass(frozen=True, slots=True)
class LocalRuntimeModelConfig:
    """Durable, versioned local runtime/model configuration."""

    executable_path: Path
    model_path: Path
    model_id: str
    model_bytes: int
    model_checksum: str
    threads: int | None = None
    timeout_seconds: float | None = None
    db_path: Path | None = None
    runtime_id: str | None = None
    runtime_version_marker: str | None = None
    managed_model_provisioning: bool = False
    schema_version: int = LOCAL_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS
        ):
            raise LocalConfigValidationError(
                f"Unsupported local configuration schema_version "
                f"{self.schema_version!r}; expected one of "
                f"{sorted(READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS)}."
            )

        object.__setattr__(
            self,
            "executable_path",
            _validate_nonempty_path("executable_path", self.executable_path),
        )
        object.__setattr__(
            self, "model_path", _validate_nonempty_path("model_path", self.model_path)
        )

        if (
            not isinstance(self.model_id, str)
            or _MODEL_ID_PATTERN.fullmatch(self.model_id) is None
        ):
            raise LocalConfigValidationError(
                "model_id must be a non-empty string matching "
                "[a-z0-9]+(?:[._-][a-z0-9]+)*."
            )

        if (
            isinstance(self.model_bytes, bool)
            or not isinstance(self.model_bytes, int)
            or self.model_bytes <= 0
        ):
            raise LocalConfigValidationError("model_bytes must be a positive integer.")

        if not isinstance(self.model_checksum, str):
            raise LocalConfigValidationError("model_checksum must be a string.")
        normalized_checksum = self.model_checksum.lower()
        if _SHA256_PATTERN.fullmatch(normalized_checksum) is None:
            raise LocalConfigValidationError(
                "model_checksum must be a 64-character hexadecimal SHA-256 digest."
            )
        object.__setattr__(self, "model_checksum", normalized_checksum)

        if not isinstance(self.managed_model_provisioning, bool):
            raise LocalConfigValidationError(
                "managed_model_provisioning must be a boolean."
            )

        if self.threads is not None:
            if (
                isinstance(self.threads, bool)
                or not isinstance(self.threads, int)
                or self.threads <= 0
            ):
                raise LocalConfigValidationError(
                    "threads must be a positive integer when set."
                )

        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(
                self.timeout_seconds, (int, float)
            ):
                raise LocalConfigValidationError(
                    "timeout_seconds must be a number when set."
                )
            if self.timeout_seconds <= 0:
                raise LocalConfigValidationError(
                    "timeout_seconds must be positive when set."
                )
            object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        if self.db_path is not None:
            object.__setattr__(
                self, "db_path", _validate_nonempty_path("db_path", self.db_path)
            )

        if self.runtime_id is not None:
            if (
                not isinstance(self.runtime_id, str)
                or _MODEL_ID_PATTERN.fullmatch(self.runtime_id) is None
            ):
                raise LocalConfigValidationError(
                    "runtime_id must be a non-empty string matching "
                    "[a-z0-9]+(?:[._-][a-z0-9]+)* when set."
                )

        if self.runtime_version_marker is not None:
            if (
                not isinstance(self.runtime_version_marker, str)
                or not self.runtime_version_marker.strip()
            ):
                raise LocalConfigValidationError(
                    "runtime_version_marker must be a non-empty string when set."
                )

        if (self.runtime_id is None) != (self.runtime_version_marker is None):
            raise LocalConfigValidationError(
                "runtime_id and runtime_version_marker must both be set or both "
                "be omitted together."
            )

        if self.schema_version == 1 and (
            self.runtime_id is not None or self.runtime_version_marker is not None
        ):
            raise LocalConfigValidationError(
                "runtime_id/runtime_version_marker require schema_version 2 or higher; "
                "schema_version 1 predates these fields."
            )

        if self.schema_version < 3 and self.managed_model_provisioning:
            raise LocalConfigValidationError(
                "managed_model_provisioning requires schema_version 3; "
                "earlier schema versions predate this field."
            )

    def to_mapping(self) -> dict[str, object]:
        """Return a canonical, JSON-compatible mapping of this configuration."""
        return {
            "schema_version": LOCAL_CONFIG_SCHEMA_VERSION,
            "executable_path": str(self.executable_path),
            "model_path": str(self.model_path),
            "model_id": self.model_id,
            "model_bytes": self.model_bytes,
            "model_checksum": self.model_checksum,
            "threads": self.threads,
            "timeout_seconds": self.timeout_seconds,
            "db_path": str(self.db_path) if self.db_path is not None else None,
            "runtime_id": self.runtime_id,
            "runtime_version_marker": self.runtime_version_marker,
            "managed_model_provisioning": self.managed_model_provisioning,
        }

    @staticmethod
    def from_mapping(data: object) -> "LocalRuntimeModelConfig":
        """Construct and validate a config from a decoded JSON mapping."""
        if not isinstance(data, Mapping):
            raise LocalConfigValidationError(
                "Local configuration data must be a JSON object."
            )

        unknown_fields = set(data.keys()) - _ALL_FIELDS
        if unknown_fields:
            raise LocalConfigValidationError(
                f"Unknown local configuration fields: {sorted(unknown_fields)}."
            )
        missing_fields = set(_REQUIRED_FIELDS) - set(data.keys())
        if missing_fields:
            raise LocalConfigValidationError(
                f"Missing required local configuration fields: {sorted(missing_fields)}."
            )

        present_keys = set(data.keys())
        schema_version = data["schema_version"]
        if schema_version == 1:
            forbidden_present = (
                _SCHEMA_2_ONLY_FIELDS | _SCHEMA_3_ONLY_FIELDS
            ) & present_keys
            if forbidden_present:
                raise LocalConfigValidationError(
                    "schema_version 1 configuration must not contain "
                    f"{sorted(forbidden_present)}; these fields were "
                    "introduced in later schema_versions."
                )
            runtime_id: object = None
            runtime_version_marker: object = None
            managed_model_provisioning: object = False
            effective_schema_version: object = schema_version
        elif schema_version == 2:
            missing_v2_fields = _SCHEMA_2_ONLY_FIELDS - present_keys
            if missing_v2_fields:
                raise LocalConfigValidationError(
                    "schema_version 2 configuration is missing required "
                    f"fields: {sorted(missing_v2_fields)}."
                )
            forbidden_present = _SCHEMA_3_ONLY_FIELDS & present_keys
            if forbidden_present:
                raise LocalConfigValidationError(
                    "schema_version 2 configuration must not contain "
                    f"{sorted(forbidden_present)}; these fields were "
                    "introduced in schema_version 3."
                )
            runtime_id = data["runtime_id"]
            runtime_version_marker = data["runtime_version_marker"]
            managed_model_provisioning = False
            effective_schema_version = schema_version
        elif schema_version == 3:
            missing_v3_fields = (
                _SCHEMA_2_ONLY_FIELDS | _SCHEMA_3_ONLY_FIELDS
            ) - present_keys
            if missing_v3_fields:
                raise LocalConfigValidationError(
                    "schema_version 3 configuration is missing required "
                    f"fields: {sorted(missing_v3_fields)}."
                )
            runtime_id = data["runtime_id"]
            runtime_version_marker = data["runtime_version_marker"]
            managed_model_provisioning = data["managed_model_provisioning"]
            effective_schema_version = schema_version
        else:
            runtime_id = data.get("runtime_id")
            runtime_version_marker = data.get("runtime_version_marker")
            managed_model_provisioning = data.get("managed_model_provisioning", False)
            effective_schema_version = schema_version

        return LocalRuntimeModelConfig(
            executable_path=data["executable_path"],
            model_path=data["model_path"],
            model_id=data["model_id"],
            model_bytes=data["model_bytes"],
            model_checksum=data["model_checksum"],
            threads=data.get("threads"),
            timeout_seconds=data.get("timeout_seconds"),
            db_path=data.get("db_path"),
            runtime_id=runtime_id,
            runtime_version_marker=runtime_version_marker,
            managed_model_provisioning=managed_model_provisioning,
            schema_version=effective_schema_version,
        )
