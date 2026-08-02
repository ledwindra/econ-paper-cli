"""Unit tests for the ``econpapers setup`` application service."""

import io
from pathlib import Path

import pytest

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig, LlamaCppReadinessError
from econ_paper_cli.services.setup_command import (
    SetupCommandOptions,
    run_setup_command,
)
from econ_paper_cli.services.single_paper_analysis_cli import CLIExitCode

VALID_CHECKSUM = "f" * 64


def _options(**overrides: object) -> SetupCommandOptions:
    base: dict[str, object] = {
        "executable_path": Path("/usr/local/bin/llama-completion"),
        "model_path": Path("/models/model.gguf"),
        "model_id": "qwen2.5-1.5b-instruct-q4-k-m",
        "model_bytes": 12345,
        "model_checksum": VALID_CHECKSUM,
    }
    base.update(overrides)
    return SetupCommandOptions(**base)


def _ok_checker(_config: LlamaCppConfig) -> None:
    return None


def _failing_checker(_config: LlamaCppConfig) -> None:
    raise LlamaCppReadinessError("runtime not ready")


def test_successful_setup_persists_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)
    out, err = io.StringIO(), io.StringIO()

    exit_code = run_setup_command(
        _options(),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=out,
        stderr=err,
    )

    assert exit_code == CLIExitCode.SUCCESS
    assert err.getvalue() == ""
    assert "Status: ready" in out.getvalue()
    assert str(config_path) in out.getvalue()

    loaded = backend.load()
    assert loaded is not None
    assert loaded.model_id == "qwen2.5-1.5b-instruct-q4-k-m"


def test_setup_survives_process_restart(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    run_setup_command(
        _options(),
        config_backend=JSONConfigStorage(config_path),
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    reopened = JSONConfigStorage(config_path)
    loaded = reopened.load()
    assert loaded is not None
    assert loaded.executable_path == Path("/usr/local/bin/llama-completion")


def test_invalid_proposed_configuration_writes_nothing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)
    err = io.StringIO()

    exit_code = run_setup_command(
        _options(model_checksum="not-a-checksum"),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=err,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert not config_path.exists()
    assert "Configuration error" in err.getvalue()


def test_failed_readiness_check_writes_nothing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)
    err = io.StringIO()

    exit_code = run_setup_command(
        _options(),
        config_backend=backend,
        readiness_checker=_failing_checker,
        stdout=io.StringIO(),
        stderr=err,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert not config_path.exists()
    assert "Readiness check failed" in err.getvalue()


def test_failed_setup_does_not_destroy_prior_valid_configuration(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)
    run_setup_command(
        _options(model_id="original-model"),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    original_bytes = config_path.read_bytes()

    exit_code = run_setup_command(
        _options(model_id="replacement-model"),
        config_backend=backend,
        readiness_checker=_failing_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert config_path.read_bytes() == original_bytes
    loaded = backend.load()
    assert loaded is not None
    assert loaded.model_id == "original-model"


def test_setup_makes_no_network_request_or_download(tmp_path: Path) -> None:
    calls: list[LlamaCppConfig] = []

    def recording_checker(config: LlamaCppConfig) -> None:
        calls.append(config)

    backend = JSONConfigStorage(tmp_path / "config.json")
    run_setup_command(
        _options(),
        config_backend=backend,
        readiness_checker=recording_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert len(calls) == 1
    assert calls[0].executable_path == Path("/usr/local/bin/llama-completion")


@pytest.mark.parametrize("threads,timeout", [(4, 60.0), (None, None)])
def test_setup_persists_optional_thread_and_timeout_defaults(
    tmp_path: Path, threads: int | None, timeout: float | None
) -> None:
    backend = JSONConfigStorage(tmp_path / "config.json")
    run_setup_command(
        _options(threads=threads, timeout=timeout),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    loaded = backend.load()
    assert loaded is not None
    assert loaded.threads == threads
    assert loaded.timeout_seconds == (float(timeout) if timeout is not None else None)


def test_setup_persists_absolute_paths_independent_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative paths validated in one cwd must resolve the same way from any
    later working directory once they are durable."""
    dir_a = tmp_path / "invocation-dir-a"
    dir_b = tmp_path / "invocation-dir-b"
    dir_a.mkdir()
    dir_b.mkdir()

    exe_file = dir_a / "llama-completion"
    exe_file.write_bytes(b"dummy")
    model_file = dir_a / "model.gguf"
    model_file.write_bytes(b"dummy_model")
    configured_db = dir_a / "library" / "econpapers.db"

    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)

    monkeypatch.chdir(dir_a)
    exit_code = run_setup_command(
        _options(
            executable_path=Path("llama-completion"),
            model_path=Path("model.gguf"),
            db_path=Path("library") / "econpapers.db",
        ),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert exit_code == CLIExitCode.SUCCESS

    monkeypatch.chdir(dir_b)
    loaded = JSONConfigStorage(config_path).load()

    assert loaded is not None
    assert loaded.executable_path.is_absolute()
    assert loaded.model_path.is_absolute()
    assert loaded.db_path is not None and loaded.db_path.is_absolute()
    assert loaded.executable_path == exe_file.resolve()
    assert loaded.model_path == model_file.resolve()
    assert loaded.db_path == configured_db.resolve()
