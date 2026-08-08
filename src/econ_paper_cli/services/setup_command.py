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
from econ_paper_cli.adapters.runtime_downloader import (
    DEFAULT_TRUSTED_REDIRECT_HOST_SUFFIXES,
    UrllibDownloader,
)
from econ_paper_cli.adapters.runtime_extractor import SafeArchiveExtractor
from econ_paper_cli.adapters.storage_paths import (
    get_default_model_dir,
    get_default_runtime_dir,
)
from econ_paper_cli.domain.errors import LocalConfigValidationError
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.domain.model_manifest import ManagedModelManifestError
from econ_paper_cli.protocols.config import ConfigBackend, ConfigPersistenceError
from econ_paper_cli.protocols.runtime_provisioning import DownloadError, ExtractionError
from econ_paper_cli.services.model_provisioning import (
    ManagedModelInstall,
    ModelProvisioningError,
    ensure_managed_model,
)
from econ_paper_cli.services.runtime_provisioning import (
    ManagedRuntimeInstall,
    RuntimeProvisioningError,
    ensure_managed_runtime,
)
from econ_paper_cli.services.single_paper_analysis_cli import CLIExitCode

ReadinessChecker = Callable[[LlamaCppConfig], None]
RuntimeProvisioner = Callable[[bool], ManagedRuntimeInstall]
ModelProvisioner = Callable[[str | None, bool], ManagedModelInstall]


class SetupOptionsError(ValueError):
    """Raised when setup options are internally inconsistent."""


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
    model_path: Path | None = None
    model_id: str | None = None
    model_bytes: int | None = None
    model_checksum: str | None = None
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | None = None
    offline: bool = False
    # Selects which pinned catalog model to provision when the four explicit
    # model-identity fields are omitted. Ignored when they are supplied.
    managed_model_id: str | None = None

    def __post_init__(self) -> None:
        """Reject a partially specified model identity.

        The four fields describe one artifact, so accepting a subset would
        either silently ignore what the user typed or verify a downloaded
        model against a checksum they meant for a different file. Supply all
        four, or none and let provisioning pin them.
        """
        supplied = [
            self.model_path,
            self.model_id,
            self.model_bytes,
            self.model_checksum,
        ]
        provided = [item is not None for item in supplied]
        if any(provided) and not all(provided):
            raise SetupOptionsError(
                "Supply all of --model-path, --model-id, --model-bytes, and "
                "--model-checksum together, or none of them to provision a "
                "managed model."
            )
        if all(provided) and self.managed_model_id is not None:
            raise SetupOptionsError(
                "--model cannot be combined with an explicit --model-path; "
                "the explicit path already identifies which model to use."
            )


def _default_readiness_checker(config: LlamaCppConfig) -> None:
    LlamaCppGenerator(config).check_readiness()


def _default_runtime_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
    return ensure_managed_runtime(
        runtime_dir=get_default_runtime_dir(),
        downloader=UrllibDownloader(),
        extractor=SafeArchiveExtractor(),
        allow_download=allow_download,
    )


def _default_model_provisioner(
    model_id: str | None, allow_download: bool
) -> ManagedModelInstall:
    return ensure_managed_model(
        model_dir=get_default_model_dir(),
        # Models come from Hugging Face, whose LFS objects redirect to a
        # regional CDN under hf.co. The runtime's GitHub-only exact-host
        # allowlist would refuse every one of those redirects.
        downloader=UrllibDownloader(
            trusted_redirect_host_suffixes=DEFAULT_TRUSTED_REDIRECT_HOST_SUFFIXES,
        ),
        model_id=model_id,
        allow_download=allow_download,
    )


def run_setup_command(
    options: SetupCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    readiness_checker: ReadinessChecker | None = None,
    runtime_provisioner: RuntimeProvisioner | None = None,
    model_provisioner: ModelProvisioner | None = None,
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

    model_install: ManagedModelInstall | None = None
    if options.model_path is not None:
        # Explicit model identity bypasses managed provisioning entirely, and
        # never triggers a download, exactly as an explicit executable path
        # does for the runtime.
        resolved_model_path = Path(options.model_path).resolve()
        resolved_model_id = options.model_id
        resolved_model_bytes = options.model_bytes
        resolved_model_checksum = options.model_checksum
    else:
        model_provider = model_provisioner or _default_model_provisioner
        try:
            model_install = model_provider(
                options.managed_model_id, not options.offline
            )
        except (
            ModelProvisioningError,
            ManagedModelManifestError,
            DownloadError,
        ) as error:
            err.write(f"Managed model provisioning failed: {error}\n")
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
        resolved_model_path = model_install.model_path
        resolved_model_id = model_install.model_id
        resolved_model_bytes = model_install.size_bytes
        resolved_model_checksum = model_install.sha256

    try:
        config = LocalRuntimeModelConfig(
            # Canonicalize to absolute paths before they become durable: a
            # relative path validated in this invocation's cwd must resolve
            # the same way from any later working directory.
            executable_path=resolved_executable_path,
            model_path=resolved_model_path,
            model_id=resolved_model_id,
            model_bytes=resolved_model_bytes,
            model_checksum=resolved_model_checksum,
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
    if model_install is not None:
        result_lines.append(f"Model Name: {model_install.display_name}")
        result_lines.append(
            "Model Source: "
            + ("downloaded" if model_install.downloaded else "reused existing install")
        )
    out.write("\n".join(result_lines) + "\n")
    return CLIExitCode.SUCCESS


def _resolve_streams(
    stdout: TextIO | None, stderr: TextIO | None
) -> tuple[TextIO, TextIO]:
    if stdout is not None and stderr is not None:
        return stdout, stderr
    import sys

    return (stdout or sys.stdout), (stderr or sys.stderr)
