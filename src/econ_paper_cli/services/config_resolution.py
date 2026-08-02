"""Deterministic CLI-over-durable-configuration resolution for runtime/model identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig

DEFAULT_GENERATION_TIMEOUT_SECONDS = 300.0

_IDENTITY_FIELDS = (
    "executable_path",
    "model_path",
    "model_id",
    "model_bytes",
    "model_checksum",
)


class ConfigResolutionError(ValueError):
    """Raised when CLI overrides and durable configuration cannot resolve one
    coherent runtime/model identity for this invocation."""


@dataclass(frozen=True, slots=True)
class RuntimeModelOverrides:
    """Optional per-invocation CLI overrides for runtime/model identity and options.

    These values apply only to the current invocation and never mutate
    durable configuration.
    """

    executable_path: Path | None = None
    model_path: Path | None = None
    model_id: str | None = None
    model_bytes: int | None = None
    model_checksum: str | None = None
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeModelConfig:
    """Fully resolved runtime/model identity and options for one invocation."""

    executable_path: Path
    model_path: Path
    model_id: str
    model_bytes: int
    model_checksum: str
    threads: int | None
    timeout_seconds: float
    source: str


def resolve_runtime_model_config(
    overrides: RuntimeModelOverrides,
    durable_config: LocalRuntimeModelConfig | None,
) -> ResolvedRuntimeModelConfig:
    """Resolve one coherent runtime/model identity: explicit CLI value, then
    durable user configuration, then command default (where valid).

    The five runtime/model identity fields (executable path, model path,
    model id, expected size, expected checksum) are treated as one unit: a
    CLI invocation must supply all five together or none of them. Supplying
    only some of them can never form one coherent, previously-verified
    identity, so it is rejected as a typed configuration error rather than
    silently mixing an unverified CLI value with unrelated durable fields.
    """
    if not isinstance(overrides, RuntimeModelOverrides):
        raise TypeError("overrides must be a RuntimeModelOverrides instance.")
    if durable_config is not None and not isinstance(
        durable_config, LocalRuntimeModelConfig
    ):
        raise TypeError(
            "durable_config must be a LocalRuntimeModelConfig instance or None."
        )

    identity_values = {field: getattr(overrides, field) for field in _IDENTITY_FIELDS}
    provided_fields = [
        field for field, value in identity_values.items() if value is not None
    ]

    if provided_fields:
        missing_fields = [
            field for field in _IDENTITY_FIELDS if identity_values[field] is None
        ]
        if missing_fields:
            raise ConfigResolutionError(
                "Partial runtime/model override: provide "
                f"{', '.join(_IDENTITY_FIELDS)} together via explicit CLI "
                "arguments, or omit all of them to use durable configuration "
                f"from `econpapers setup` (missing: {', '.join(missing_fields)})."
            )
        executable_path = Path(overrides.executable_path)  # type: ignore[arg-type]
        model_path = Path(overrides.model_path)  # type: ignore[arg-type]
        model_id = str(overrides.model_id)
        model_bytes = int(overrides.model_bytes)  # type: ignore[arg-type]
        model_checksum = str(overrides.model_checksum)
        source = "cli"
    elif durable_config is not None:
        executable_path = durable_config.executable_path
        model_path = durable_config.model_path
        model_id = durable_config.model_id
        model_bytes = durable_config.model_bytes
        model_checksum = durable_config.model_checksum
        source = "config"
    else:
        raise ConfigResolutionError(
            "No runtime/model configuration is available. Run `econpapers "
            "setup` once, or supply --llama-cpp-path, --model-path, "
            "--model-id, --model-bytes, and --model-checksum explicitly."
        )

    if overrides.threads is not None:
        threads = overrides.threads
    elif durable_config is not None:
        threads = durable_config.threads
    else:
        threads = None

    if overrides.timeout is not None:
        timeout_seconds = overrides.timeout
    elif durable_config is not None and durable_config.timeout_seconds is not None:
        timeout_seconds = durable_config.timeout_seconds
    else:
        timeout_seconds = DEFAULT_GENERATION_TIMEOUT_SECONDS

    return ResolvedRuntimeModelConfig(
        executable_path=executable_path,
        model_path=model_path,
        model_id=model_id,
        model_bytes=model_bytes,
        model_checksum=model_checksum,
        threads=threads,
        timeout_seconds=timeout_seconds,
        source=source,
    )


def resolve_db_path(
    overrides: RuntimeModelOverrides,
    durable_config: LocalRuntimeModelConfig | None,
) -> Path:
    """Resolve the effective database path: CLI override, durable config, then default."""
    if overrides.db_path is not None:
        return Path(overrides.db_path)
    if durable_config is not None and durable_config.db_path is not None:
        return durable_config.db_path
    return get_default_db_path()
