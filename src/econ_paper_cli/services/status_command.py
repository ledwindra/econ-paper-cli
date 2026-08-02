"""Application service for the read-only local ``status`` command."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.filesystem import VerificationError
from econ_paper_cli.adapters.llama_cpp import (
    LlamaCppConfig,
    LlamaCppConfigurationError,
    LlamaCppGenerator,
    LlamaCppReadinessError,
)
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.protocols.config import ConfigBackend, ConfigError
from econ_paper_cli.protocols.storage import (
    StorageBackend,
    StorageConnectionError,
    StorageIncompatibleSchemaError,
    StorageMigrationError,
)
from econ_paper_cli.services.config_resolution import (
    RuntimeModelOverrides,
    resolve_db_path,
)
from econ_paper_cli.services.single_paper_analysis_cli import CLIExitCode

ReadinessChecker = Callable[[LlamaCppConfig], None]


@dataclass(frozen=True, slots=True)
class StatusCommandOptions:
    """Parsed options for the ``econpapers status`` command."""

    db_path: Path | None = None


@dataclass(frozen=True, slots=True)
class StatusReport:
    """Immutable, read-only snapshot of local configuration and library state."""

    config_path: Path
    config_present: bool
    config_valid: bool
    config_error: str | None
    model_id: str | None
    runtime_ready: bool | None
    runtime_error: str | None
    db_path: Path
    db_state: str
    db_error: str | None
    schema_version: int | None
    paper_count: int | None
    passage_count: int | None


def _default_readiness_checker(config: LlamaCppConfig) -> None:
    LlamaCppGenerator(config).check_readiness()


def execute_status_command(
    options: StatusCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    storage: StorageBackend | None = None,
    readiness_checker: ReadinessChecker | None = None,
) -> StatusReport:
    """Inspect durable configuration and library state without mutating anything."""
    backend = config_backend or JSONConfigStorage()

    config: LocalRuntimeModelConfig | None = None
    config_error: str | None = None
    try:
        config = backend.load()
    except ConfigError as error:
        config_error = str(error)

    runtime_ready: bool | None = None
    runtime_error: str | None = None
    if config is not None:
        llama_config = LlamaCppConfig(
            executable_path=config.executable_path,
            model_path=config.model_path,
            model_id=config.model_id,
            model_expected_size_bytes=config.model_bytes,
            model_sha256=config.model_checksum,
            threads=config.threads,
            timeout_seconds=(
                config.timeout_seconds if config.timeout_seconds is not None else 300.0
            ),
        )
        checker = readiness_checker or _default_readiness_checker
        try:
            checker(llama_config)
            runtime_ready = True
        except (
            LlamaCppConfigurationError,
            LlamaCppReadinessError,
            VerificationError,
        ) as error:
            runtime_ready = False
            runtime_error = str(error)

    resolved_db_path = resolve_db_path(
        RuntimeModelOverrides(db_path=options.db_path), config
    )

    db_state = "ready"
    db_error: str | None = None
    schema_version: int | None = None
    paper_count: int | None = None
    passage_count: int | None = None

    db_backend = storage or SQLiteStorage(resolved_db_path, read_only=True)
    try:
        db_backend.initialize()
        schema_version = db_backend.get_schema_version()
        paper_count = db_backend.count_papers()
        passage_count = db_backend.count_passages()
    except StorageConnectionError as error:
        db_state = "missing"
        db_error = str(error)
    except StorageIncompatibleSchemaError as error:
        db_state = "incompatible_schema"
        db_error = str(error)
    except StorageMigrationError as error:
        db_state = "outdated_schema"
        db_error = str(error)
    finally:
        try:
            db_backend.close()
        except Exception:
            pass

    return StatusReport(
        config_path=backend.config_path,
        config_present=config is not None,
        config_valid=config_error is None,
        config_error=config_error,
        model_id=config.model_id if config is not None else None,
        runtime_ready=runtime_ready,
        runtime_error=runtime_error,
        db_path=resolved_db_path,
        db_state=db_state,
        db_error=db_error,
        schema_version=schema_version,
        paper_count=paper_count,
        passage_count=passage_count,
    )


def format_status_report(report: StatusReport) -> str:
    """Render a deterministic, inspectable read-only status report."""
    lines = [
        "=== Local Status ===",
        f"Configuration Path: {report.config_path}",
    ]
    if not report.config_present and report.config_error is None:
        lines.append("Configuration: missing (run `econpapers setup`)")
    elif report.config_error is not None:
        lines.append(f"Configuration: invalid ({report.config_error})")
    else:
        lines.append("Configuration: present and valid")
        lines.append(f"Model ID: {report.model_id}")

    if report.runtime_ready is None:
        lines.append("Runtime/Model Readiness: not checked (no configuration)")
    elif report.runtime_ready:
        lines.append("Runtime/Model Readiness: ready")
    else:
        lines.append(f"Runtime/Model Readiness: not ready ({report.runtime_error})")

    lines.append(f"Database Path: {report.db_path}")
    lines.append(f"Database State: {report.db_state}")
    if report.db_error is not None:
        lines.append(f"Database Detail: {report.db_error}")
    if report.schema_version is not None:
        lines.append(f"Schema Version: {report.schema_version}")
    if report.paper_count is not None:
        lines.append(f"Paper Count: {report.paper_count}")
    if report.passage_count is not None:
        lines.append(f"Passage Count: {report.passage_count}")

    return "\n".join(lines)


def run_status_command(
    options: StatusCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    storage: StorageBackend | None = None,
    readiness_checker: ReadinessChecker | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Execute the status command and render deterministic terminal output."""
    out = stdout
    if out is None:
        import sys

        out = sys.stdout

    report = execute_status_command(
        options,
        config_backend=config_backend,
        storage=storage,
        readiness_checker=readiness_checker,
    )
    out.write(format_status_report(report) + "\n")
    return CLIExitCode.SUCCESS
