"""Unit tests for CLI-over-durable-configuration resolution precedence."""

from pathlib import Path

import pytest

from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.services.config_resolution import (
    DEFAULT_GENERATION_TIMEOUT_SECONDS,
    ConfigResolutionError,
    RuntimeModelOverrides,
    resolve_db_path,
    resolve_runtime_model_config,
)

VALID_CHECKSUM = "c" * 64


def _durable_config(**overrides: object) -> LocalRuntimeModelConfig:
    base: dict[str, object] = {
        "executable_path": Path("/config/llama-completion"),
        "model_path": Path("/config/model.gguf"),
        "model_id": "config-model",
        "model_bytes": 111,
        "model_checksum": VALID_CHECKSUM,
    }
    base.update(overrides)
    return LocalRuntimeModelConfig(**base)


def _cli_overrides(**overrides: object) -> RuntimeModelOverrides:
    return RuntimeModelOverrides(**overrides)


def test_full_cli_override_wins_over_config() -> None:
    config = _durable_config()
    overrides = _cli_overrides(
        executable_path=Path("/cli/llama-completion"),
        model_path=Path("/cli/model.gguf"),
        model_id="cli-model",
        model_bytes=222,
        model_checksum="d" * 64,
    )
    resolved = resolve_runtime_model_config(overrides, config)
    assert resolved.source == "cli"
    assert resolved.executable_path == Path("/cli/llama-completion")
    assert resolved.model_id == "cli-model"
    assert resolved.model_bytes == 222
    assert resolved.model_checksum == "d" * 64


def test_config_only_resolves_from_durable_config() -> None:
    config = _durable_config()
    resolved = resolve_runtime_model_config(_cli_overrides(), config)
    assert resolved.source == "config"
    assert resolved.executable_path == config.executable_path
    assert resolved.model_path == config.model_path
    assert resolved.model_id == config.model_id
    assert resolved.model_bytes == config.model_bytes
    assert resolved.model_checksum == config.model_checksum


def test_no_cli_and_no_config_raises_typed_error() -> None:
    with pytest.raises(ConfigResolutionError):
        resolve_runtime_model_config(_cli_overrides(), None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("executable_path", Path("/cli/only-exe")),
        ("model_path", Path("/cli/only-model.gguf")),
        ("model_id", "only-id"),
        ("model_bytes", 999),
        ("model_checksum", "e" * 64),
    ],
)
def test_partial_cli_override_is_typed_error_even_with_config(
    field: str, value: object
) -> None:
    config = _durable_config()
    overrides = _cli_overrides(**{field: value})
    with pytest.raises(ConfigResolutionError, match="Partial runtime/model override"):
        resolve_runtime_model_config(overrides, config)


def test_partial_cli_override_is_typed_error_without_config() -> None:
    overrides = _cli_overrides(model_id="only-id")
    with pytest.raises(ConfigResolutionError):
        resolve_runtime_model_config(overrides, None)


def test_threads_prefers_cli_then_config_then_none() -> None:
    config = _durable_config(threads=8)
    resolved = resolve_runtime_model_config(_cli_overrides(threads=2), config)
    assert resolved.threads == 2

    resolved_config_only = resolve_runtime_model_config(_cli_overrides(), config)
    assert resolved_config_only.threads == 8

    resolved_neither = resolve_runtime_model_config(_cli_overrides(), _durable_config())
    assert resolved_neither.threads is None


def test_timeout_prefers_cli_then_config_then_default() -> None:
    config = _durable_config(timeout_seconds=45.0)
    resolved = resolve_runtime_model_config(_cli_overrides(timeout=10.0), config)
    assert resolved.timeout_seconds == 10.0

    resolved_config_only = resolve_runtime_model_config(_cli_overrides(), config)
    assert resolved_config_only.timeout_seconds == 45.0

    resolved_default = resolve_runtime_model_config(_cli_overrides(), _durable_config())
    assert resolved_default.timeout_seconds == DEFAULT_GENERATION_TIMEOUT_SECONDS


def test_db_path_prefers_cli_then_config_then_default() -> None:
    config = _durable_config(db_path=Path("/config/db.sqlite"))
    resolved = resolve_db_path(_cli_overrides(db_path=Path("/cli/db.sqlite")), config)
    assert resolved == Path("/cli/db.sqlite")

    resolved_config_only = resolve_db_path(_cli_overrides(), config)
    assert resolved_config_only == Path("/config/db.sqlite")

    resolved_default = resolve_db_path(_cli_overrides(), _durable_config())
    assert resolved_default != Path("/config/db.sqlite")
    assert resolved_default.name == "econpapers.db"


def test_resolve_rejects_wrong_types() -> None:
    with pytest.raises(TypeError):
        resolve_runtime_model_config(None, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        resolve_runtime_model_config(_cli_overrides(), object())  # type: ignore[arg-type]
