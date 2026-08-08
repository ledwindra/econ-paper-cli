"""Application service for the read-only local ``status`` command."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TextIO

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.filesystem import (
    ArtifactFileNotFoundError,
    VerificationError,
    verify_local_file,
)
from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.domain.runtime_manifest import (
    ManagedRuntimeArtifact,
    select_artifact_for_platform,
)
from econ_paper_cli.domain.runtime_manifest_data import MANAGED_RUNTIME_MANIFEST
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
from econ_paper_cli.services.platform_detection import (
    DetectedPlatform,
    detect_current_platform,
)
from econ_paper_cli.services.runtime_provisioning import (
    RuntimeOrigin,
    RuntimeState,
    classify_runtime_origin,
    verify_executable_runs,
)
from econ_paper_cli.services.single_paper_analysis_cli import CLIExitCode


class ModelState(str, Enum):
    """Independent model-artifact readiness classification."""

    VERIFIED = "verified"
    MISSING = "missing"
    CORRUPT_OR_MISMATCHED = "corrupt_or_mismatched"
    NOT_CONFIGURED = "not_configured"


RuntimeReadinessChecker = Callable[[Path, str], None]
ModelReadinessChecker = Callable[[LlamaCppConfig], None]


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
    runtime_origin: RuntimeOrigin
    runtime_state: RuntimeState
    runtime_error: str | None
    model_state: ModelState
    model_error: str | None
    db_path: Path
    db_state: str
    db_error: str | None
    schema_version: int | None
    paper_count: int | None
    passage_count: int | None

    @property
    def overall_ready(self) -> bool:
        """Convenience: both the runtime executable and model are verified."""
        return (
            self.runtime_state is RuntimeState.VERIFIED
            and self.model_state is ModelState.VERIFIED
        )


def _default_runtime_readiness_checker(
    executable_path: Path, version_marker: str
) -> None:
    verify_executable_runs(executable_path, version_marker)


def _default_model_readiness_checker(config: LlamaCppConfig) -> None:
    verify_local_file(
        config.model_path, config.model_expected_size_bytes, config.model_sha256
    )


def _select_pinned_artifact(
    detected: DetectedPlatform,
) -> ManagedRuntimeArtifact | None:
    """Return the manifest-selected artifact for the current machine, if any.

    ``DetectedPlatform.is_supported`` only means the raw system/machine
    values mapped to a recognized enum member — a recognized combination
    with no manifest entry (e.g. Linux arm64) is still not "supported" in
    the sense that matters here. Callers must always treat "no artifact
    selected" as the true unsupported-platform signal, not
    ``is_supported`` alone.
    """
    if not detected.is_supported:
        return None
    return select_artifact_for_platform(
        MANAGED_RUNTIME_MANIFEST,
        detected.platform,
        detected.architecture,
    )


def _classify_runtime(
    config: LocalRuntimeModelConfig,
    llama_config: LlamaCppConfig,
    runtime_checker: RuntimeReadinessChecker,
    expected_artifact: ManagedRuntimeArtifact | None,
) -> tuple[RuntimeOrigin, RuntimeState, str | None]:
    """Classify the configured runtime's origin and independent readiness state."""
    return classify_runtime_origin(
        config,
        llama_config,
        runtime_checker,
        expected_artifact,
        require_declared_identity=False,
    )


def _classify_model(
    llama_config: LlamaCppConfig,
    model_checker: ModelReadinessChecker,
) -> tuple[ModelState, str | None]:
    try:
        model_checker(llama_config)
    except ArtifactFileNotFoundError as error:
        return ModelState.MISSING, str(error)
    except VerificationError as error:
        return ModelState.CORRUPT_OR_MISMATCHED, str(error)
    return ModelState.VERIFIED, None


def execute_status_command(
    options: StatusCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    storage: StorageBackend | None = None,
    runtime_readiness_checker: RuntimeReadinessChecker | None = None,
    model_readiness_checker: ModelReadinessChecker | None = None,
) -> StatusReport:
    """Inspect durable configuration and library state without mutating anything."""
    backend = config_backend or JSONConfigStorage()

    config_present = backend.exists()
    config: LocalRuntimeModelConfig | None = None
    config_error: str | None = None
    try:
        config = backend.load()
    except ConfigError as error:
        config_error = str(error)

    runtime_checker = runtime_readiness_checker or _default_runtime_readiness_checker
    model_checker = model_readiness_checker or _default_model_readiness_checker

    runtime_origin: RuntimeOrigin
    runtime_state: RuntimeState
    runtime_error: str | None
    model_state: ModelState
    model_error: str | None

    detected = detect_current_platform()
    expected_artifact = _select_pinned_artifact(detected)

    if config is None:
        runtime_origin = RuntimeOrigin.UNKNOWN
        model_state = ModelState.NOT_CONFIGURED
        model_error = None
        if expected_artifact is not None:
            runtime_state, runtime_error = RuntimeState.NOT_CHECKED, None
        else:
            runtime_state = RuntimeState.UNSUPPORTED_PLATFORM
            runtime_error = (
                "No configuration is present, and this platform "
                f"({detected.raw_system}/{detected.raw_machine}) has no pinned "
                "managed runtime artifact; `econpapers setup` requires an "
                "explicit --llama-cpp-path here."
            )
    else:
        llama_config_kwargs: dict[str, object] = {
            "executable_path": config.executable_path,
            "model_path": config.model_path,
            "model_id": config.model_id,
            "model_expected_size_bytes": config.model_bytes,
            "model_sha256": config.model_checksum,
            "threads": config.threads,
            "timeout_seconds": (
                config.timeout_seconds if config.timeout_seconds is not None else 300.0
            ),
        }
        if config.runtime_id is not None:
            llama_config_kwargs["runtime_id"] = config.runtime_id
        if config.runtime_version_marker is not None:
            llama_config_kwargs["runtime_version_marker"] = (
                config.runtime_version_marker
            )
        llama_config = LlamaCppConfig(**llama_config_kwargs)
        runtime_origin, runtime_state, runtime_error = _classify_runtime(
            config, llama_config, runtime_checker, expected_artifact
        )
        model_state, model_error = _classify_model(llama_config, model_checker)

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
        config_present=config_present,
        config_valid=config_error is None,
        config_error=config_error,
        model_id=config.model_id if config is not None else None,
        runtime_origin=runtime_origin,
        runtime_state=runtime_state,
        runtime_error=runtime_error,
        model_state=model_state,
        model_error=model_error,
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
    if report.config_error is not None:
        state = "present but invalid" if report.config_present else "invalid"
        lines.append(f"Configuration: {state} ({report.config_error})")
    elif not report.config_present:
        lines.append("Configuration: missing (run `econpapers setup`)")
    else:
        lines.append("Configuration: present and valid")
        lines.append(f"Model ID: {report.model_id}")

    lines.append(f"Runtime Origin: {report.runtime_origin.value}")
    if report.runtime_error is not None:
        lines.append(
            f"Runtime State: {report.runtime_state.value} ({report.runtime_error})"
        )
    else:
        lines.append(f"Runtime State: {report.runtime_state.value}")

    if report.model_error is not None:
        lines.append(f"Model State: {report.model_state.value} ({report.model_error})")
    else:
        lines.append(f"Model State: {report.model_state.value}")

    lines.append(f"Overall Ready: {report.overall_ready}")

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
    runtime_readiness_checker: RuntimeReadinessChecker | None = None,
    model_readiness_checker: ModelReadinessChecker | None = None,
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
        runtime_readiness_checker=runtime_readiness_checker,
        model_readiness_checker=model_readiness_checker,
    )
    out.write(format_status_report(report) + "\n")
    return CLIExitCode.SUCCESS
