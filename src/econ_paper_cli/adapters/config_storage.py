"""Local-filesystem JSON adapter for durable runtime/model configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from econ_paper_cli.adapters.storage_paths import get_default_config_path
from econ_paper_cli.domain.errors import LocalConfigValidationError
from econ_paper_cli.domain.local_config import (
    READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS,
    LocalRuntimeModelConfig,
)
from econ_paper_cli.protocols.config import (
    ConfigError,
    ConfigIncompatibleSchemaError,
    ConfigMalformedError,
    ConfigPersistenceError,
)


class JSONConfigStorage:
    """Standard-library JSON adapter for the replaceable ``ConfigBackend`` protocol.

    Configuration is persisted atomically: a temporary file is written in the
    destination directory, flushed, and then replaced onto the destination
    path with ``os.replace``. A failure at any point before the replace
    leaves the previous durable configuration (if any) untouched.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = (
            Path(config_path) if config_path is not None else get_default_config_path()
        )

    @property
    def config_path(self) -> Path:
        return self._config_path

    def exists(self) -> bool:
        """Return whether a configuration file is present, independent of
        whether it can be successfully parsed or validated."""
        return self._config_path.exists()

    def load(self) -> LocalRuntimeModelConfig | None:
        """Read and validate durable configuration without mutating anything.

        Returns None when no configuration file exists yet. Never creates a
        directory or file.
        """
        if not self._config_path.exists():
            return None

        try:
            raw_text = self._config_path.read_text(encoding="utf-8")
        except OSError as err:
            raise ConfigError(
                f"Cannot read configuration file '{self._config_path}': {err}"
            ) from err

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as err:
            raise ConfigMalformedError(
                f"Configuration file '{self._config_path}' is not valid JSON: {err}"
            ) from err

        if isinstance(data, dict):
            schema_version = data.get("schema_version")
            if (
                schema_version is not None
                and schema_version not in READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS
            ):
                raise ConfigIncompatibleSchemaError(
                    f"Configuration file '{self._config_path}' has schema_version "
                    f"{schema_version!r}, which is not supported; expected one of "
                    f"{sorted(READABLE_LOCAL_CONFIG_SCHEMA_VERSIONS)}."
                )

        try:
            return LocalRuntimeModelConfig.from_mapping(data)
        except LocalConfigValidationError as err:
            raise ConfigMalformedError(
                f"Configuration file '{self._config_path}' is invalid: {err}"
            ) from err

    def save(self, config: LocalRuntimeModelConfig) -> None:
        """Atomically persist configuration, replacing any prior durable value."""
        if not isinstance(config, LocalRuntimeModelConfig):
            raise TypeError("config must be a LocalRuntimeModelConfig instance.")

        payload = json.dumps(config.to_mapping(), indent=2, sort_keys=True) + "\n"
        directory = self._config_path.parent

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            raise ConfigPersistenceError(
                f"Cannot create configuration directory '{directory}': {err}"
            ) from err

        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".econpapers-config-", suffix=".tmp", dir=str(directory)
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass  # Best-effort on platforms without POSIX permission bits.
            os.replace(tmp_path, self._config_path)
        except OSError as err:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise ConfigPersistenceError(
                f"Failed to persist configuration to '{self._config_path}': {err}"
            ) from err
