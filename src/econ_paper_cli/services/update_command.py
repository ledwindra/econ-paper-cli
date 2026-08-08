"""Application service for the ``econpapers update`` command."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TextIO

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig
from econ_paper_cli.adapters.runtime_downloader import (
    DEFAULT_TRUSTED_REDIRECT_HOST_SUFFIXES,
    UrllibDownloader,
)
from econ_paper_cli.adapters.runtime_extractor import SafeArchiveExtractor
from econ_paper_cli.adapters.storage_paths import (
    get_default_model_dir,
    get_default_runtime_dir,
)
from econ_paper_cli.domain.model_manifest import (
    MANAGED_MODEL_CATALOG,
    ManagedModelArtifact,
    ManagedModelCatalog,
    ManagedModelManifestError,
)
from econ_paper_cli.domain.runtime_manifest import (
    ManagedRuntimeManifest,
    select_artifact_for_platform,
)
from econ_paper_cli.domain.runtime_manifest_data import MANAGED_RUNTIME_MANIFEST
from econ_paper_cli.protocols.config import ConfigBackend, ConfigError
from econ_paper_cli.protocols.runtime_provisioning import DownloadError, ExtractionError
from econ_paper_cli.services.model_provisioning import (
    ModelProvisioningError,
    OfflineModelProvisioningError,
    ensure_managed_model,
    is_model_path_contained_in_dir,
    verify_managed_model,
)
from econ_paper_cli.services.platform_detection import (
    DetectedPlatform,
    detect_current_platform,
)
from econ_paper_cli.services.runtime_provisioning import (
    ExecutableReadinessChecker,
    OfflineProvisioningError,
    RuntimeOrigin,
    RuntimeProvisioningError,
    RuntimeState,
    classify_runtime_origin,
    content_addressed_install_dir_name,
    ensure_managed_runtime,
    locate_managed_install_root,
    verify_executable_runs,
)


class UpdateArtifactOutcome(str, Enum):
    """Result category for individual managed artifact verification/repair."""

    REUSED = "reused"
    REPAIRED = "repaired"
    NEWER_VERSION_AVAILABLE = "newer_version_available"
    EXTERNAL_SKIPPED = "external_skipped"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE_OFFLINE = "unavailable_offline"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UpdateCommandOptions:
    """Parsed options for the ``econpapers update`` command."""

    offline: bool = False
    config_path: Path | None = None


@dataclass(frozen=True, slots=True)
class UpdateReport:
    """Immutable outcome report for the update command."""

    config_path: Path
    config_present: bool
    runtime_outcome: UpdateArtifactOutcome
    runtime_detail: str | None
    model_outcome: UpdateArtifactOutcome
    model_detail: str | None

    @property
    def overall_success(self) -> bool:
        """True if all configured managed artifacts are reused or repaired."""
        return self.runtime_outcome in (
            UpdateArtifactOutcome.REUSED,
            UpdateArtifactOutcome.REPAIRED,
        ) and self.model_outcome in (
            UpdateArtifactOutcome.REUSED,
            UpdateArtifactOutcome.REPAIRED,
        )


def execute_update_command(
    options: UpdateCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    runtime_dir: Path | None = None,
    model_dir: Path | None = None,
    downloader: object | None = None,
    extractor: object | None = None,
    runtime_manifest: ManagedRuntimeManifest = MANAGED_RUNTIME_MANIFEST,
    model_catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
    runtime_readiness_checker: ExecutableReadinessChecker | None = None,
    detected_platform: DetectedPlatform | None = None,
) -> UpdateReport:
    """Inspect local config and verify/repair managed runtime and model artifacts."""
    backend = config_backend or JSONConfigStorage(options.config_path)

    if not backend.exists():
        return UpdateReport(
            config_path=backend.config_path,
            config_present=False,
            runtime_outcome=UpdateArtifactOutcome.NOT_CONFIGURED,
            runtime_detail="No durable configuration file found.",
            model_outcome=UpdateArtifactOutcome.NOT_CONFIGURED,
            model_detail="No durable configuration file found.",
        )

    config = backend.load()
    if config is None:
        return UpdateReport(
            config_path=backend.config_path,
            config_present=False,
            runtime_outcome=UpdateArtifactOutcome.NOT_CONFIGURED,
            runtime_detail="No durable configuration file found.",
            model_outcome=UpdateArtifactOutcome.NOT_CONFIGURED,
            model_detail="No durable configuration file found.",
        )

    resolved_runtime_dir = runtime_dir or get_default_runtime_dir()
    resolved_model_dir = model_dir or get_default_model_dir()
    detected = detected_platform or detect_current_platform()
    expected_artifact = (
        select_artifact_for_platform(
            runtime_manifest, detected.platform, detected.architecture
        )
        if detected.is_supported and detected.platform and detected.architecture
        else None
    )

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
        llama_config_kwargs["runtime_version_marker"] = config.runtime_version_marker
    llama_config = LlamaCppConfig(**llama_config_kwargs)

    checker = runtime_readiness_checker or verify_executable_runs

    # --- Runtime side ---
    runtime_origin, runtime_state, runtime_detail = classify_runtime_origin(
        config,
        llama_config,
        checker,
        expected_artifact,
        runtime_dir=resolved_runtime_dir,
        require_declared_identity=True,
    )

    runtime_outcome: UpdateArtifactOutcome
    install_root = locate_managed_install_root(
        config.executable_path, resolved_runtime_dir
    )
    if runtime_origin is not RuntimeOrigin.MANAGED:
        runtime_outcome = UpdateArtifactOutcome.EXTERNAL_SKIPPED
        runtime_detail = runtime_detail or "Configured runtime executable is external."
    elif (
        expected_artifact is None
        or expected_artifact.runtime_id != config.runtime_id
        or expected_artifact.version_marker != config.runtime_version_marker
        or (
            install_root is not None
            and content_addressed_install_dir_name(expected_artifact)
            != install_root.name
        )
    ):
        runtime_outcome = UpdateArtifactOutcome.NEWER_VERSION_AVAILABLE
        runtime_detail = (
            "Manifest pinned runtime version differs from configured runtime identity."
        )
    elif runtime_state is RuntimeState.VERIFIED:
        runtime_outcome = UpdateArtifactOutcome.REUSED
        runtime_detail = None
    elif options.offline:
        runtime_outcome = UpdateArtifactOutcome.UNAVAILABLE_OFFLINE
        runtime_detail = "Managed runtime requires repair but --offline is set."
    else:
        try:
            resolved_downloader = downloader or UrllibDownloader()
            resolved_extractor = extractor or SafeArchiveExtractor()
            ensure_managed_runtime(
                runtime_dir=resolved_runtime_dir,
                downloader=resolved_downloader,
                extractor=resolved_extractor,
                manifest=runtime_manifest,
                detected=detected,
                allow_download=True,
                executable_readiness_checker=checker,
            )
            runtime_outcome = UpdateArtifactOutcome.REPAIRED
            runtime_detail = None
        except OfflineProvisioningError as err:
            runtime_outcome = UpdateArtifactOutcome.UNAVAILABLE_OFFLINE
            runtime_detail = str(err)
        except (
            RuntimeProvisioningError,
            DownloadError,
            ExtractionError,
            OSError,
        ) as err:
            runtime_outcome = UpdateArtifactOutcome.FAILED
            runtime_detail = str(err)

    # --- Model side ---
    model_outcome: UpdateArtifactOutcome
    model_detail: str | None = None

    is_contained = is_model_path_contained_in_dir(config.model_path, resolved_model_dir)

    if not config.managed_model_provisioning or not is_contained:
        model_outcome = UpdateArtifactOutcome.EXTERNAL_SKIPPED
        model_detail = "Configured model is external or lacks managed origin flag."
    else:
        try:
            catalog_artifact: ManagedModelArtifact | None = model_catalog.get(
                config.model_id
            )
        except ManagedModelManifestError:
            catalog_artifact = None

        if (
            catalog_artifact is None
            or catalog_artifact.size_bytes != config.model_bytes
            or catalog_artifact.sha256 != config.model_checksum
            or catalog_artifact.filename != config.model_path.name
        ):
            model_outcome = UpdateArtifactOutcome.NEWER_VERSION_AVAILABLE
            model_detail = (
                f"Catalog pinned model '{config.model_id}' identity differs from "
                "configured model."
            )
        elif verify_managed_model(config.model_path, catalog_artifact):
            model_outcome = UpdateArtifactOutcome.REUSED
            model_detail = None
        elif options.offline:
            model_outcome = UpdateArtifactOutcome.UNAVAILABLE_OFFLINE
            model_detail = "Managed model requires repair but --offline is set."
        else:
            try:
                resolved_downloader = downloader or UrllibDownloader(
                    trusted_redirect_host_suffixes=DEFAULT_TRUSTED_REDIRECT_HOST_SUFFIXES
                )
                ensure_managed_model(
                    model_dir=resolved_model_dir,
                    downloader=resolved_downloader,
                    model_id=config.model_id,
                    catalog=model_catalog,
                    allow_download=True,
                )
                model_outcome = UpdateArtifactOutcome.REPAIRED
                model_detail = None
            except OfflineModelProvisioningError as err:
                model_outcome = UpdateArtifactOutcome.UNAVAILABLE_OFFLINE
                model_detail = str(err)
            except (
                ModelProvisioningError,
                DownloadError,
                ManagedModelManifestError,
                OSError,
            ) as err:
                model_outcome = UpdateArtifactOutcome.FAILED
                model_detail = str(err)

    return UpdateReport(
        config_path=backend.config_path,
        config_present=True,
        runtime_outcome=runtime_outcome,
        runtime_detail=runtime_detail,
        model_outcome=model_outcome,
        model_detail=model_detail,
    )


def format_update_report(report: UpdateReport) -> str:
    """Render a deterministic, inspectable update result report."""
    lines = [
        "=== Local Update Result ===",
        f"Configuration Path: {report.config_path}",
    ]
    if not report.config_present:
        lines.append("Configuration: missing (run `econpapers setup`)")
    else:
        lines.append("Configuration: present and valid")

    if report.runtime_detail is not None:
        lines.append(
            f"Runtime Outcome: {report.runtime_outcome.value} ({report.runtime_detail})"
        )
    else:
        lines.append(f"Runtime Outcome: {report.runtime_outcome.value}")

    if report.model_detail is not None:
        lines.append(
            f"Model Outcome: {report.model_outcome.value} ({report.model_detail})"
        )
    else:
        lines.append(f"Model Outcome: {report.model_outcome.value}")

    return "\n".join(lines)


def run_update_command(
    options: UpdateCommandOptions,
    *,
    config_backend: ConfigBackend | None = None,
    runtime_dir: Path | None = None,
    model_dir: Path | None = None,
    downloader: object | None = None,
    extractor: object | None = None,
    runtime_manifest: ManagedRuntimeManifest = MANAGED_RUNTIME_MANIFEST,
    model_catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
    runtime_readiness_checker: ExecutableReadinessChecker | None = None,
    detected_platform: DetectedPlatform | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Execute the update command and return process exit code."""
    out = stdout
    err = stderr
    if out is None or err is None:
        import sys

        out = out or sys.stdout
        err = err or sys.stderr

    try:
        report = execute_update_command(
            options,
            config_backend=config_backend,
            runtime_dir=runtime_dir,
            model_dir=model_dir,
            downloader=downloader,
            extractor=extractor,
            runtime_manifest=runtime_manifest,
            model_catalog=model_catalog,
            runtime_readiness_checker=runtime_readiness_checker,
            detected_platform=detected_platform,
        )
    except ConfigError as config_err:
        err.write(f"Configuration error: {config_err}\n")
        return 2

    out.write(format_update_report(report) + "\n")

    if (
        report.runtime_outcome is UpdateArtifactOutcome.FAILED
        or report.model_outcome is UpdateArtifactOutcome.FAILED
    ):
        return 3

    if not report.config_present or not report.overall_success:
        return 1

    return 0
