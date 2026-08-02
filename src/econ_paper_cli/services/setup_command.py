"""Application service for the deterministic local configuration ``setup`` command."""

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
from econ_paper_cli.adapters.runtime_downloader import UrllibDownloader
from econ_paper_cli.adapters.runtime_extractor import SafeArchiveExtractor
from econ_paper_cli.adapters.storage_paths import get_default_runtime_dir
from econ_paper_cli.domain.errors import LocalConfigValidationError
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.protocols.config import ConfigBackend, ConfigPersistenceError
from econ_paper_cli.protocols.runtime_provisioning import DownloadError, ExtractionError
from econ_paper_cli.services.runtime_provisioning import (
    ManagedRuntimeInstall,
    RuntimeProvisioningError,
    ensure_managed_runtime,
)
from econ_paper_cli.services.single_paper_analysis_cli import CLIExitCode

ReadinessChecker = Callable[[LlamaCppConfig], None]
RuntimeProvisioner = Callable[[bool], ManagedRuntimeInstall]


@dataclass(frozen=True, slots=True)
class SetupCommandOptions:
    """Parsed options for the ``econpapers setup`` command.

    ``executable_path`` is optional: when omitted, a managed llama.cpp
    runtime is reused (if already verified-installed) or downloaded,
    verified, and installed. Supplying it bypasses managed provisioning
    entirely and never triggers a download. ``offline`` refuses any
    download, failing with a typed error if no explicit path is given and
    no verified managed runtime is already installed.
    """

    executable_path: Path | None
    model_path: Path
    model_id: str
    model_bytes: int
    model_checksum: str
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | None = None
    offline: bool = False


def _default_readiness_checker(config: LlamaCppConfig) -> None:
    LlamaCppGenerator(config).check_readiness()


def _default_runtime_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
    return ensure_managed_runtime(
        runtime_dir=get_default_runtime_dir(),
        downloader=UrllibDownloader(),
        extractor=SafeArchiveExtractor(),
        allow_download=allow_download,
    )


def run_setup_command(
    options: SetupCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    readiness_checker: ReadinessChecker | None = None,
    runtime_provisioner: RuntimeProvisioner | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Validate a proposed local configuration and, on success, persist it durably.

    Nothing is written unless the proposed configuration is structurally
    valid and its runtime/model artifacts pass local readiness verification.
    A failure at either step leaves any prior durable configuration
    untouched.
    """
    out, err = _resolve_streams(stdout, stderr)

    runtime_id: str | None = None
    runtime_version_marker: str | None = None

    if options.executable_path is not None:
        # An explicit executable path always bypasses managed provisioning:
        # never a download, unchanged CLI-override precedence.
        resolved_executable_path = Path(options.executable_path).resolve()
    else:
        provisioner = runtime_provisioner or _default_runtime_provisioner
        try:
            install = provisioner(not options.offline)
        except (RuntimeProvisioningError, DownloadError, ExtractionError) as error:
            err.write(f"Managed runtime provisioning failed: {error}\n")
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
        resolved_executable_path = install.executable_path
        runtime_id = install.runtime_id
        runtime_version_marker = install.version_marker

    try:
        config = LocalRuntimeModelConfig(
            # Canonicalize to absolute paths before they become durable: a
            # relative path validated in this invocation's cwd must resolve
            # the same way from any later working directory.
            executable_path=resolved_executable_path,
            model_path=Path(options.model_path).resolve(),
            model_id=options.model_id,
            model_bytes=options.model_bytes,
            model_checksum=options.model_checksum,
            threads=options.threads,
            timeout_seconds=options.timeout,
            db_path=(
                Path(options.db_path).resolve() if options.db_path is not None else None
            ),
            runtime_id=runtime_id,
            runtime_version_marker=runtime_version_marker,
        )
    except LocalConfigValidationError as error:
        err.write(f"Configuration error: {error}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

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
    if runtime_id is not None:
        llama_config_kwargs["runtime_id"] = runtime_id
    if runtime_version_marker is not None:
        llama_config_kwargs["runtime_version_marker"] = runtime_version_marker
    llama_config = LlamaCppConfig(**llama_config_kwargs)

    checker = readiness_checker or _default_readiness_checker
    try:
        checker(llama_config)
    except (
        LlamaCppConfigurationError,
        LlamaCppReadinessError,
        VerificationError,
    ) as error:
        err.write(f"Readiness check failed; configuration was not written: {error}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

    backend = config_backend or JSONConfigStorage()
    try:
        backend.save(config)
    except ConfigPersistenceError as error:
        err.write(f"Failed to persist configuration: {error}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

    result_lines = [
        "=== Setup Result ===",
        "Status: ready",
        f"Configuration Path: {backend.config_path}",
        f"Runtime Executable: {config.executable_path}",
    ]
    if config.runtime_id is not None:
        result_lines.append(f"Runtime ID: {config.runtime_id}")
    result_lines.append(f"Model Path: {config.model_path}")
    result_lines.append(f"Model ID: {config.model_id}")
    out.write("\n".join(result_lines) + "\n")
    return CLIExitCode.SUCCESS


def _resolve_streams(
    stdout: TextIO | None, stderr: TextIO | None
) -> tuple[TextIO, TextIO]:
    if stdout is not None and stderr is not None:
        return stdout, stderr
    import sys

    return (stdout or sys.stdout), (stderr or sys.stderr)
