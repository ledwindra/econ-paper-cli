"""Unit tests for the atomic JSON local-configuration storage adapter."""

import json
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.protocols.config import (
    ConfigIncompatibleSchemaError,
    ConfigMalformedError,
    ConfigPersistenceError,
)

VALID_CHECKSUM = "b" * 64


def _sample_config(**overrides: object) -> LocalRuntimeModelConfig:
    base: dict[str, object] = {
        "executable_path": Path("/usr/local/bin/llama-completion"),
        "model_path": Path("/models/model.gguf"),
        "model_id": "qwen2.5-1.5b-instruct-q4-k-m",
        "model_bytes": 987654,
        "model_checksum": VALID_CHECKSUM,
    }
    base.update(overrides)
    return LocalRuntimeModelConfig(**base)


def test_load_returns_none_when_no_file_exists(tmp_path: Path) -> None:
    storage = JSONConfigStorage(tmp_path / "nested" / "config.json")
    assert storage.load() is None
    assert not (tmp_path / "nested").exists()


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    storage = JSONConfigStorage(config_path)
    config = _sample_config(threads=4, timeout_seconds=90.0)

    storage.save(config)
    assert config_path.exists()

    loaded = storage.load()
    assert loaded == config


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "dir" / "config.json"
    storage = JSONConfigStorage(config_path)
    storage.save(_sample_config())
    assert config_path.exists()


def test_save_writes_atomically_no_leftover_temp_files(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    storage = JSONConfigStorage(config_path)
    storage.save(_sample_config())
    remaining = list(tmp_path.iterdir())
    assert remaining == [config_path]


def test_save_replaces_existing_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    storage = JSONConfigStorage(config_path)
    storage.save(_sample_config(model_id="first-model"))
    storage.save(_sample_config(model_id="second-model"))

    loaded = storage.load()
    assert loaded is not None
    assert loaded.model_id == "second-model"
    remaining = list(tmp_path.iterdir())
    assert remaining == [config_path]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_save_uses_private_file_permissions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    storage = JSONConfigStorage(config_path)
    storage.save(_sample_config())

    mode = stat.S_IMODE(config_path.stat().st_mode)
    assert mode == 0o600


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not valid json", encoding="utf-8")
    storage = JSONConfigStorage(config_path)

    with pytest.raises(ConfigMalformedError):
        storage.load()


def test_load_rejects_semantically_invalid_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"schema_version": 1, "executable_path": "/bin/x"}),
        encoding="utf-8",
    )
    storage = JSONConfigStorage(config_path)

    with pytest.raises(ConfigMalformedError):
        storage.load()


def test_load_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    mapping = _sample_config().to_mapping()
    mapping["schema_version"] = 4
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    storage = JSONConfigStorage(config_path)

    with pytest.raises(ConfigIncompatibleSchemaError):
        storage.load()


def test_load_accepts_legacy_schema_version_1(tmp_path: Path) -> None:
    """Issue #58 review: schema version 3 must not break reading a genuinely older
    version-1 file that predates those fields entirely."""
    config_path = tmp_path / "config.json"
    mapping = _sample_config().to_mapping()
    mapping["schema_version"] = 1
    del mapping["runtime_id"]
    del mapping["runtime_version_marker"]
    del mapping["managed_model_provisioning"]
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    storage = JSONConfigStorage(config_path)

    loaded = storage.load()
    assert loaded is not None
    assert loaded.schema_version == 1
    assert loaded.runtime_id is None
    assert loaded.runtime_version_marker is None
    assert loaded.managed_model_provisioning is False


def test_load_accepts_legacy_schema_version_2(tmp_path: Path) -> None:
    """Schema version 3 must not break reading a genuinely older version-2 file."""
    config_path = tmp_path / "config.json"
    mapping = _sample_config().to_mapping()
    mapping["schema_version"] = 2
    del mapping["managed_model_provisioning"]
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    storage = JSONConfigStorage(config_path)

    loaded = storage.load()
    assert loaded is not None
    assert loaded.schema_version == 2
    assert loaded.managed_model_provisioning is False


def test_failed_persistence_leaves_prior_configuration_untouched(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    storage = JSONConfigStorage(config_path)
    storage.save(_sample_config(model_id="original-model"))
    original_bytes = config_path.read_bytes()

    # Force the parent "directory" to be a regular file so mkstemp/replace fail.
    blocked_config_path = tmp_path / "blocked" / "config.json"
    (tmp_path / "blocked").write_text("not a directory", encoding="utf-8")
    blocked_storage = JSONConfigStorage(blocked_config_path)
    with pytest.raises(ConfigPersistenceError):
        blocked_storage.save(_sample_config())

    # The original, unrelated configuration remains exactly as written.
    assert config_path.read_bytes() == original_bytes


def test_mkstemp_failure_is_wrapped_and_leaves_prior_configuration_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure creating the temp file itself (directory exists and is
    writable, but mkstemp fails for another reason) must surface as a typed
    ConfigPersistenceError, not a raw OSError, and must not leave stray
    temp files or disturb prior durable configuration."""
    config_path = tmp_path / "config.json"
    storage = JSONConfigStorage(config_path)
    storage.save(_sample_config(model_id="original-model"))
    original_bytes = config_path.read_bytes()

    def failing_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        raise OSError("simulated mkstemp failure")

    monkeypatch.setattr(tempfile, "mkstemp", failing_mkstemp)

    with pytest.raises(ConfigPersistenceError):
        storage.save(_sample_config(model_id="replacement-model"))

    assert config_path.read_bytes() == original_bytes
    remaining = list(tmp_path.iterdir())
    assert remaining == [config_path]


def test_config_path_property_reflects_constructor_argument(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.json"
    storage = JSONConfigStorage(config_path)
    assert storage.config_path == config_path


def test_default_config_path_used_when_not_specified(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ECONPAPERS_CONFIG_DIR", str(tmp_path / "default-dir"))
    storage = JSONConfigStorage()
    assert storage.config_path == tmp_path / "default-dir" / "config.json"
