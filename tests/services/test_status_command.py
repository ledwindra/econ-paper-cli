"""Unit tests for the read-only ``econpapers status`` application service."""

import io
from pathlib import Path

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig, LlamaCppReadinessError
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.services.status_command import (
    StatusCommandOptions,
    execute_status_command,
    run_status_command,
)

VALID_CHECKSUM = "9" * 64


def _config(**overrides: object) -> LocalRuntimeModelConfig:
    base: dict[str, object] = {
        "executable_path": Path("/usr/local/bin/llama-completion"),
        "model_path": Path("/models/model.gguf"),
        "model_id": "status-model",
        "model_bytes": 42,
        "model_checksum": VALID_CHECKSUM,
    }
    base.update(overrides)
    return LocalRuntimeModelConfig(**base)


def _ok_checker(_config: LlamaCppConfig) -> None:
    return None


def _failing_checker(_config: LlamaCppConfig) -> None:
    raise LlamaCppReadinessError("model checksum mismatch")


def test_status_reports_missing_configuration(tmp_path: Path) -> None:
    config_backend = JSONConfigStorage(tmp_path / "missing-config.json")
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.config_present is False
    assert report.config_valid is True
    assert report.runtime_ready is None
    assert report.db_state == "missing"


def test_status_reports_malformed_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("not json", encoding="utf-8")
    config_backend = JSONConfigStorage(config_path)
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.config_present is False
    assert report.config_valid is False
    assert report.config_error is not None


def test_status_reports_ready_runtime_and_populated_library(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_backend = JSONConfigStorage(config_path)
    config_backend.save(_config())

    db_path = tmp_path / "econpapers.db"
    write_storage = SQLiteStorage(str(db_path))
    write_storage.initialize()
    write_storage.close()

    read_storage = SQLiteStorage(str(db_path), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=read_storage,
        readiness_checker=_ok_checker,
    )

    assert report.config_present is True
    assert report.config_valid is True
    assert report.model_id == "status-model"
    assert report.runtime_ready is True
    assert report.db_state == "ready"
    assert report.schema_version is not None and report.schema_version > 0
    assert report.paper_count == 0
    assert report.passage_count == 0


def test_status_reports_failed_runtime_verification(tmp_path: Path) -> None:
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config())
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        readiness_checker=_failing_checker,
    )

    assert report.runtime_ready is False
    assert report.runtime_error is not None


def test_status_never_creates_database_or_config(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "config.json"
    db_path = tmp_path / "other-nested" / "econpapers.db"
    config_backend = JSONConfigStorage(config_path)
    storage = SQLiteStorage(str(db_path), read_only=True)

    execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert not config_path.exists()
    assert not config_path.parent.exists()
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_status_does_not_mutate_existing_configuration_bytes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_backend = JSONConfigStorage(config_path)
    config_backend.save(_config())
    before = config_path.read_bytes()
    before_mtime = config_path.stat().st_mtime_ns

    execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=SQLiteStorage(str(tmp_path / "missing.db"), read_only=True),
        readiness_checker=_ok_checker,
    )

    assert config_path.read_bytes() == before
    assert config_path.stat().st_mtime_ns == before_mtime


def test_run_status_command_writes_rendered_report_and_returns_zero(
    tmp_path: Path,
) -> None:
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)
    out = io.StringIO()

    exit_code = run_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        stdout=out,
    )

    assert exit_code == 0
    rendered = out.getvalue()
    assert "=== Local Status ===" in rendered
    assert "missing" in rendered
