"""Unit tests for the durable local runtime/model configuration contract."""

from pathlib import Path

import pytest

from econ_paper_cli.domain.errors import LocalConfigValidationError
from econ_paper_cli.domain.local_config import (
    LOCAL_CONFIG_SCHEMA_VERSION,
    LocalRuntimeModelConfig,
)

VALID_CHECKSUM = "a" * 64


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "executable_path": Path("/usr/local/bin/llama-completion"),
        "model_path": Path("/models/model.gguf"),
        "model_id": "qwen2.5-1.5b-instruct-q4-k-m",
        "model_bytes": 123456,
        "model_checksum": VALID_CHECKSUM,
    }
    base.update(overrides)
    return base


def test_construction_normalizes_paths_and_checksum() -> None:
    config = LocalRuntimeModelConfig(
        **_valid_kwargs(
            executable_path="/usr/local/bin/llama-completion",
            model_checksum=VALID_CHECKSUM.upper(),
        )
    )
    assert config.executable_path == Path("/usr/local/bin/llama-completion")
    assert config.model_checksum == VALID_CHECKSUM
    assert config.schema_version == LOCAL_CONFIG_SCHEMA_VERSION
    assert config.threads is None
    assert config.timeout_seconds is None
    assert config.db_path is None


def test_to_mapping_round_trips_through_from_mapping() -> None:
    config = LocalRuntimeModelConfig(
        **_valid_kwargs(threads=4, timeout_seconds=120, db_path="/data/econpapers.db")
    )
    mapping = config.to_mapping()
    assert mapping == {
        "schema_version": 1,
        "executable_path": str(config.executable_path),
        "model_path": str(config.model_path),
        "model_id": config.model_id,
        "model_bytes": config.model_bytes,
        "model_checksum": config.model_checksum,
        "threads": 4,
        "timeout_seconds": 120.0,
        "db_path": str(config.db_path),
    }
    restored = LocalRuntimeModelConfig.from_mapping(mapping)
    assert restored == config


def test_from_mapping_rejects_non_mapping() -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig.from_mapping(["not", "a", "mapping"])


def test_from_mapping_rejects_unknown_fields() -> None:
    mapping = LocalRuntimeModelConfig(**_valid_kwargs()).to_mapping()
    mapping["unexpected_field"] = "value"
    with pytest.raises(LocalConfigValidationError, match="Unknown"):
        LocalRuntimeModelConfig.from_mapping(mapping)


def test_from_mapping_rejects_missing_required_fields() -> None:
    mapping = LocalRuntimeModelConfig(**_valid_kwargs()).to_mapping()
    del mapping["model_checksum"]
    with pytest.raises(LocalConfigValidationError, match="Missing required"):
        LocalRuntimeModelConfig.from_mapping(mapping)


@pytest.mark.parametrize("schema_version", [0, 2, "1", 1.0, True])
def test_rejects_invalid_schema_version(schema_version: object) -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**_valid_kwargs(schema_version=schema_version))


@pytest.mark.parametrize("field", ["executable_path", "model_path", "db_path"])
def test_rejects_empty_paths(field: str) -> None:
    kwargs = _valid_kwargs()
    if field == "db_path":
        kwargs["db_path"] = "   "
    else:
        kwargs[field] = "   "
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**kwargs)


@pytest.mark.parametrize(
    "model_id", ["", "Has-Upper", "bad space", "trailing-", "-leading"]
)
def test_rejects_invalid_model_id(model_id: str) -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**_valid_kwargs(model_id=model_id))


@pytest.mark.parametrize("model_bytes", [0, -1, 1.5, "123", True])
def test_rejects_invalid_model_bytes(model_bytes: object) -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**_valid_kwargs(model_bytes=model_bytes))


@pytest.mark.parametrize(
    "model_checksum", ["", "short", "g" * 64, "a" * 63, 12345, None]
)
def test_rejects_invalid_model_checksum(model_checksum: object) -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**_valid_kwargs(model_checksum=model_checksum))


@pytest.mark.parametrize("threads", [0, -1, 1.5, "4", True])
def test_rejects_invalid_threads(threads: object) -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**_valid_kwargs(threads=threads))


@pytest.mark.parametrize("timeout_seconds", [0, -1, "60", True])
def test_rejects_invalid_timeout(timeout_seconds: object) -> None:
    with pytest.raises(LocalConfigValidationError):
        LocalRuntimeModelConfig(**_valid_kwargs(timeout_seconds=timeout_seconds))


def test_accepts_valid_optional_fields() -> None:
    config = LocalRuntimeModelConfig(
        **_valid_kwargs(threads=8, timeout_seconds=90, db_path=Path("/data/db.sqlite"))
    )
    assert config.threads == 8
    assert config.timeout_seconds == 90.0
    assert config.db_path == Path("/data/db.sqlite")
