"""Replaceable local configuration storage protocol and its error types."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig


class ConfigError(Exception):
    """Base exception for all local configuration storage operations."""


class ConfigMalformedError(ConfigError):
    """Raised when existing configuration data cannot be parsed or validated."""


class ConfigIncompatibleSchemaError(ConfigError):
    """Raised when stored configuration has an unsupported schema version."""


class ConfigPersistenceError(ConfigError):
    """Raised when configuration cannot be durably and atomically written."""


@runtime_checkable
class ConfigBackend(Protocol):
    """Replaceable storage backend for durable local runtime/model configuration."""

    @property
    def config_path(self) -> Path:
        """Return the canonical local filesystem path for this backend's configuration."""
        ...

    def exists(self) -> bool:
        """Return whether durable configuration data is present, independent of
        whether it can be successfully parsed or validated."""
        ...

    def load(self) -> LocalRuntimeModelConfig | None:
        """Return the durable configuration, or None if none has been written yet."""
        ...

    def save(self, config: LocalRuntimeModelConfig) -> None:
        """Atomically persist configuration, replacing any prior durable value."""
        ...
