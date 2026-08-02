"""Deterministic CLI-over-durable-configuration resolution for runtime/model identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.protocols.config import ConfigBackend, ConfigError

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
    """Fully resolved runtime/model identity and options for one invocation.

    ``runtime_id``/``runtime_version_marker`` are ``None`` unless durable
    configuration recorded them (i.e. ``setup`` resolved the executable
    through managed provisioning) — callers construct ``LlamaCppConfig``
    with these only when not ``None``, letting its own defaults apply
    otherwise, exactly as before this field existed.
    """

    executable_path: Path
    model_path: Path
    model_id: str
    model_bytes: int
    model_checksum: str
    threads: int | None
    timeout_seconds: float
    source: str
    runtime_id: str | None = None
    runtime_version_marker: str | None = None


def _identity_values(overrides: RuntimeModelOverrides) -> dict[str, object]:
    return {field: getattr(overrides, field) for field in _IDENTITY_FIELDS}


def validate_identity_override_shape(overrides: RuntimeModelOverrides) -> None:
    """Eagerly reject a partial CLI override of the five identity fields.

    This check depends only on ``overrides`` — never on durable
    configuration or on whether a generator will ever be constructed — so a
    partial override is rejected immediately, even on a code path (exact
    reuse, library backfill, empty-library/no-match chat) that would
    otherwise never resolve a full runtime/model identity at all.
    """
    if not isinstance(overrides, RuntimeModelOverrides):
        raise TypeError("overrides must be a RuntimeModelOverrides instance.")

    identity_values = _identity_values(overrides)
    provided_fields = [
        field for field, value in identity_values.items() if value is not None
    ]
    if provided_fields and len(provided_fields) != len(_IDENTITY_FIELDS):
        missing_fields = [
            field for field in _IDENTITY_FIELDS if identity_values[field] is None
        ]
        raise ConfigResolutionError(
            "Partial runtime/model override: provide "
            f"{', '.join(_IDENTITY_FIELDS)} together via explicit CLI "
            "arguments, or omit all of them to use durable configuration "
            f"from `econpapers setup` (missing: {', '.join(missing_fields)})."
        )


def identity_fully_specified(overrides: RuntimeModelOverrides) -> bool:
    """Return True when all five CLI identity overrides are present.

    When true, resolving a runtime/model identity needs no durable
    configuration at all — callers can skip loading it entirely.
    """
    return all(value is not None for value in _identity_values(overrides).values())


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
    validate_identity_override_shape(overrides)

    runtime_id: str | None = None
    runtime_version_marker: str | None = None
    if identity_fully_specified(overrides):
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
        runtime_id = durable_config.runtime_id
        runtime_version_marker = durable_config.runtime_version_marker
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
        runtime_id=runtime_id,
        runtime_version_marker=runtime_version_marker,
    )


def build_llama_cpp_config_kwargs(
    resolved: ResolvedRuntimeModelConfig,
) -> dict[str, object]:
    """Return ``LlamaCppConfig`` constructor kwargs for one resolved identity.

    Includes ``runtime_id``/``runtime_version_marker`` only when resolution
    actually recorded them (managed-runtime-provisioned durable
    configuration); otherwise omits them entirely so ``LlamaCppConfig``'s
    own defaults apply, exactly as before these fields existed. Every
    ``LlamaCppConfig`` construction site (``chat``, ``analyze``, bare
    ``econpapers``) shares this so the installed runtime's identity is
    never silently replaced by an adapter default after a restart.
    """
    kwargs: dict[str, object] = {
        "executable_path": resolved.executable_path,
        "model_path": resolved.model_path,
        "model_id": resolved.model_id,
        "model_expected_size_bytes": resolved.model_bytes,
        "model_sha256": resolved.model_checksum,
        "threads": resolved.threads,
        "timeout_seconds": resolved.timeout_seconds,
    }
    if resolved.runtime_id is not None:
        kwargs["runtime_id"] = resolved.runtime_id
    if resolved.runtime_version_marker is not None:
        kwargs["runtime_version_marker"] = resolved.runtime_version_marker
    return kwargs


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


@dataclass
class LazyConfigLoader:
    """Loads durable configuration from a ``ConfigBackend`` at most once.

    Callers that may not need durable configuration at all (exact reuse,
    library backfill, empty-library/no-match chat, or a fully-specified CLI
    override) must not force a load merely by constructing this loader —
    only calling :meth:`get` triggers ``backend.load()``, and the outcome
    (value or raised error) is cached so a later call never re-invokes it.
    """

    backend: ConfigBackend
    _loaded: bool = field(default=False, init=False)
    _value: LocalRuntimeModelConfig | None = field(default=None, init=False)
    _error: ConfigError | None = field(default=None, init=False)

    def get(self) -> LocalRuntimeModelConfig | None:
        if not self._loaded:
            try:
                self._value = self.backend.load()
            except ConfigError as error:
                self._error = error
            self._loaded = True
        if self._error is not None:
            raise self._error
        return self._value

    def peek(self) -> LocalRuntimeModelConfig | None:
        """Return the cached configuration if a load already happened.

        Never triggers ``backend.load()``. Used to honor durable optional
        defaults (threads, timeout) that a config load already performed for
        an unrelated reason — e.g. resolving a database-path fallback —
        without forcing a load merely to check them on a path that would
        otherwise be genuinely configuration-independent.
        """
        if self._loaded and self._error is None:
            return self._value
        return None
