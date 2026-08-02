"""Unit tests for the read-only ``econpapers status`` application service."""

import io
import json
import sqlite3
from pathlib import Path, PurePosixPath

import pytest

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.domain.runtime_manifest import (
    SupportedArchitecture,
    SupportedPlatform,
)
from econ_paper_cli.domain.runtime_receipt import InstallReceipt
from econ_paper_cli.services.runtime_provisioning import StagedRuntimeVerificationError
from econ_paper_cli.services.status_command import (
    ModelState,
    RuntimeOrigin,
    RuntimeState,
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


def _ok_runtime_checker(_executable_path: Path, _version_marker: str) -> None:
    return None


def _failing_runtime_checker(_executable_path: Path, _version_marker: str) -> None:
    raise StagedRuntimeVerificationError("runtime not ready")


def _ok_model_checker(_config: LlamaCppConfig) -> None:
    return None


def test_status_reports_missing_configuration(tmp_path: Path) -> None:
    config_backend = JSONConfigStorage(tmp_path / "missing-config.json")
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.config_present is False
    assert report.config_valid is True
    assert report.runtime_origin is RuntimeOrigin.UNKNOWN
    assert report.model_state is ModelState.NOT_CONFIGURED
    assert report.db_state == "missing"


def test_status_reports_malformed_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("not json", encoding="utf-8")
    config_backend = JSONConfigStorage(config_path)
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.config_present is True
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
        runtime_readiness_checker=_ok_runtime_checker,
        model_readiness_checker=_ok_model_checker,
    )

    assert report.config_present is True
    assert report.config_valid is True
    assert report.model_id == "status-model"
    assert report.runtime_origin is RuntimeOrigin.EXTERNAL
    assert report.runtime_state is RuntimeState.VERIFIED
    assert report.model_state is ModelState.VERIFIED
    assert report.overall_ready is True
    assert report.db_state == "ready"
    assert report.schema_version is not None and report.schema_version > 0
    assert report.paper_count == 0
    assert report.passage_count == 0


def test_status_reports_failed_runtime_verification(tmp_path: Path) -> None:
    # An executable that exists but fails its readiness check classifies as
    # corrupt/mismatched, not missing.
    existing_executable = tmp_path / "llama-completion"
    existing_executable.write_bytes(b"not really an executable")

    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config(executable_path=existing_executable))
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        runtime_readiness_checker=_failing_runtime_checker,
        model_readiness_checker=_ok_model_checker,
    )

    assert report.runtime_state is RuntimeState.CORRUPT_OR_MISMATCHED
    assert report.runtime_error is not None
    assert report.model_state is ModelState.VERIFIED
    assert report.overall_ready is False


def test_status_reports_missing_external_runtime(tmp_path: Path) -> None:
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config())  # default executable_path does not exist
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        runtime_readiness_checker=_failing_runtime_checker,
        model_readiness_checker=_ok_model_checker,
    )

    assert report.runtime_origin is RuntimeOrigin.EXTERNAL
    assert report.runtime_state is RuntimeState.MISSING


def test_status_reports_missing_model_independent_of_runtime(tmp_path: Path) -> None:
    """A missing/corrupt model must never be misreported as a corrupt
    managed runtime, and vice versa — the two states are independent."""
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config())
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        runtime_readiness_checker=_ok_runtime_checker,
        model_readiness_checker=None,  # real checker: model file does not exist
    )

    assert report.runtime_state is RuntimeState.VERIFIED
    assert report.model_state is ModelState.MISSING
    assert report.overall_ready is False


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
        runtime_readiness_checker=_ok_runtime_checker,
        model_readiness_checker=_ok_model_checker,
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
    assert "Runtime Origin:" in rendered
    assert "Runtime State:" in rendered
    assert "Model State:" in rendered
    assert "Overall Ready:" in rendered


def _seed_schema_migrations_version(db_path: Path, version: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );"""
    )
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) "
        "VALUES (?, '2026-07-31T20:00:00Z', 'seeded for status test');",
        (version,),
    )
    conn.commit()
    conn.close()


def test_status_reports_outdated_database_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "old_schema.db"
    _seed_schema_migrations_version(db_path, 1)
    config_backend = JSONConfigStorage(tmp_path / "missing-config.json")
    storage = SQLiteStorage(str(db_path), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.db_state == "outdated_schema"
    assert report.db_error is not None
    assert "older than supported version" in report.db_error
    assert report.schema_version is None
    assert report.paper_count is None
    assert report.passage_count is None


def test_status_reports_incompatible_newer_database_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "new_schema.db"
    _seed_schema_migrations_version(db_path, 99)
    config_backend = JSONConfigStorage(tmp_path / "missing-config.json")
    storage = SQLiteStorage(str(db_path), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.db_state == "incompatible_schema"
    assert report.db_error is not None
    assert "newer than maximum supported version" in report.db_error
    assert report.schema_version is None
    assert report.paper_count is None
    assert report.passage_count is None


# --- Managed vs. external runtime origin classification --------------------


def _install_managed_runtime(tmp_path: Path, runtime_dir: Path) -> Path:
    import hashlib

    install_dir = runtime_dir / "llama.cpp-b10199-aaaaaaaaaaaaaaaa"
    install_dir.mkdir(parents=True)
    executable_path = install_dir / "llama-completion"
    executable_bytes = b"fake"
    executable_path.write_bytes(executable_bytes)
    executable_sha256 = hashlib.sha256(executable_bytes).hexdigest()
    receipt = InstallReceipt(
        schema_version=1,
        runtime_id="llama.cpp-b10199",
        version_marker="10199",
        platform=SupportedPlatform.LINUX,
        architecture=SupportedArchitecture.X86_64,
        source_asset_identity="https://example.com/runtime.tar.gz",
        archive_size_bytes=1024,
        archive_sha256="a" * 64,
        executable_relative_path=PurePosixPath("llama-completion"),
        executable_sha256=executable_sha256,
        member_checksums=((PurePosixPath("llama-completion"), executable_sha256),),
    )
    (install_dir / "receipt.json").write_text(
        json.dumps(receipt.to_mapping()), encoding="utf-8"
    )
    return executable_path


def test_status_classifies_managed_runtime_as_managed_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("ECONPAPERS_RUNTIME_DIR", str(runtime_dir))
    executable_path = _install_managed_runtime(tmp_path, runtime_dir)

    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config(executable_path=executable_path))
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        runtime_readiness_checker=_ok_runtime_checker,
        model_readiness_checker=_ok_model_checker,
    )

    assert report.runtime_origin is RuntimeOrigin.MANAGED
    assert report.runtime_state is RuntimeState.VERIFIED


def test_status_classifies_corrupt_managed_receipt_distinctly_from_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("ECONPAPERS_RUNTIME_DIR", str(runtime_dir))
    executable_path = _install_managed_runtime(tmp_path, runtime_dir)
    install_dir = executable_path.parent
    (install_dir / "receipt.json").write_text("not valid json{{{", encoding="utf-8")

    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config(executable_path=executable_path))
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    report = execute_status_command(
        StatusCommandOptions(),
        config_backend=config_backend,
        storage=storage,
        runtime_readiness_checker=_ok_runtime_checker,
        model_readiness_checker=_ok_model_checker,
    )

    assert report.runtime_origin is RuntimeOrigin.MANAGED
    assert report.runtime_state is RuntimeState.CORRUPT_OR_MISMATCHED
    # The model check ran independently and was unaffected by the corrupt
    # runtime receipt.
    assert report.model_state is ModelState.VERIFIED


def test_status_reports_unsupported_platform_when_no_config_and_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import econ_paper_cli.services.status_command as status_module
    from econ_paper_cli.services.platform_detection import DetectedPlatform

    config_backend = JSONConfigStorage(tmp_path / "missing-config.json")
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)

    def fake_detect() -> DetectedPlatform:
        return DetectedPlatform(
            platform=None,
            architecture=None,
            raw_system="FreeBSD",
            raw_machine="x86_64",
        )

    monkeypatch.setattr(status_module, "detect_current_platform", fake_detect)
    report = execute_status_command(
        StatusCommandOptions(), config_backend=config_backend, storage=storage
    )

    assert report.runtime_origin is RuntimeOrigin.UNKNOWN
    assert report.runtime_state is RuntimeState.UNSUPPORTED_PLATFORM
    assert report.runtime_error is not None
