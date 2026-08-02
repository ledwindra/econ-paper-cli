"""Orchestrates managed ``llama.cpp`` runtime installation for ``setup``.

Contract (see issue #58's approved plan):

- Extract into a sibling staging directory, never the final path.
- Fully verify the staged bundle (every member's checksum, then the
  executable's actual readiness) *while still staged*.
- Promote only a fully-verified staged bundle, via ``os.replace``, onto a
  content-addressed final path (``<runtime_id>-<archive_sha256[:16]>``) that
  does not already exist — this makes promotion atomic and portable, and
  makes concurrent installs of the same pinned artifact race-safe by
  construction: colliding installs are byte-identical by definition, so
  losing a promotion race just means adopting the winner's (already
  verified) result instead of overwriting it.
- Never trust a directory merely because it sits under the runtime root: a
  managed install is only ever "verified" after its ``InstallReceipt`` and
  every declared bundle member checksum re-check clean.
- An existing directory at the target content-addressed path that fails
  receipt verification is corrupt garbage (e.g. an interrupted previous
  install) and is evicted before a fresh install is promoted to that same
  path — never silently overwritten by ``os.replace`` on a populated
  directory.
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from econ_paper_cli.adapters.filesystem import (
    VerificationError,
    inspect_local_file,
    verify_local_file,
)
from econ_paper_cli.domain.runtime_manifest import (
    ManagedRuntimeArtifact,
    ManagedRuntimeManifest,
    select_artifact_for_platform,
)
from econ_paper_cli.domain.runtime_manifest_data import MANAGED_RUNTIME_MANIFEST
from econ_paper_cli.domain.runtime_receipt import InstallReceipt, InstallReceiptError
from econ_paper_cli.protocols.runtime_provisioning import ArchiveExtractor, Downloader
from econ_paper_cli.services.platform_detection import (
    DetectedPlatform,
    detect_current_platform,
)

_RECEIPT_FILENAME = "receipt.json"
_EXECUTABLE_CHECK_TIMEOUT_SECONDS = 30.0


class RuntimeProvisioningError(Exception):
    """Base exception for all managed-runtime provisioning failures."""


class UnsupportedPlatformError(RuntimeProvisioningError):
    """Raised when the current platform/architecture has no pinned artifact."""


class OfflineProvisioningError(RuntimeProvisioningError):
    """Raised when no verified managed runtime exists and downloads are disabled."""


class StagedRuntimeVerificationError(RuntimeProvisioningError):
    """Raised when a freshly staged bundle fails verification before promotion."""


class CorruptManagedInstallError(RuntimeProvisioningError):
    """Raised when an existing managed install's receipt or bundle is invalid."""


ExecutableReadinessChecker = Callable[[Path, str], None]


@dataclass(frozen=True, slots=True)
class ManagedRuntimeInstall:
    """A verified, reusable managed-runtime installation."""

    executable_path: Path
    runtime_id: str
    version_marker: str
    install_dir: Path
    receipt: InstallReceipt


def ensure_managed_runtime(
    *,
    runtime_dir: Path,
    downloader: Downloader,
    extractor: ArchiveExtractor,
    manifest: ManagedRuntimeManifest = MANAGED_RUNTIME_MANIFEST,
    detected: DetectedPlatform | None = None,
    allow_download: bool = True,
    executable_readiness_checker: ExecutableReadinessChecker | None = None,
) -> ManagedRuntimeInstall:
    """Reuse a verified managed runtime, or download/verify/install a fresh one.

    Raises ``UnsupportedPlatformError`` if the platform/architecture has no
    pinned artifact, ``OfflineProvisioningError`` if ``allow_download`` is
    False and no verified managed runtime is already installed, and
    ``StagedRuntimeVerificationError`` if a freshly downloaded/extracted
    bundle fails verification (never promoted in that case). Download
    (``DownloadError`` subtypes) and extraction (``ExtractionError``
    subtypes) failures propagate directly from the injected adapters.
    """
    checker = executable_readiness_checker or verify_executable_runs
    resolved_detected = detected if detected is not None else detect_current_platform()
    if not resolved_detected.is_supported:
        raise UnsupportedPlatformError(
            "No managed llama.cpp runtime is available for "
            f"{resolved_detected.raw_system}/{resolved_detected.raw_machine}."
        )
    detected_platform = resolved_detected.platform
    detected_architecture = resolved_detected.architecture
    if detected_platform is None or detected_architecture is None:
        raise UnsupportedPlatformError(
            "No managed llama.cpp runtime is available for "
            f"{resolved_detected.raw_system}/{resolved_detected.raw_machine}."
        )

    artifact = select_artifact_for_platform(
        manifest, detected_platform, detected_architecture
    )
    if artifact is None:
        raise UnsupportedPlatformError(
            f"No pinned managed runtime artifact for platform={detected_platform.value} "
            f"architecture={detected_architecture.value}."
        )

    final_install_dir = runtime_dir / _content_addressed_dir_name(artifact)

    if final_install_dir.exists():
        try:
            receipt = verify_managed_install(final_install_dir)
        except CorruptManagedInstallError:
            shutil.rmtree(final_install_dir, ignore_errors=True)
        else:
            return _install_result(final_install_dir, receipt)

    if not allow_download:
        raise OfflineProvisioningError(
            "No verified managed runtime is installed and downloads are disabled. "
            "Run setup with network access, or supply an explicit executable path."
        )

    runtime_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".staging-", dir=str(runtime_dir)
    ) as staging:
        staging_dir = Path(staging)
        archive_path = staging_dir / f"archive.{artifact.archive_format.value}"
        staged_extract_dir = staging_dir / "extracted"
        staged_extract_dir.mkdir()

        downloader.download(
            artifact.source_url,
            archive_path,
            expected_size_bytes=artifact.archive_size_bytes,
        )
        verify_local_file(
            archive_path, artifact.archive_size_bytes, artifact.archive_sha256
        )

        extractor.extract(archive_path, artifact.archive_format, staged_extract_dir)

        staged_executable = staged_extract_dir / artifact.executable_relative_path
        if not staged_executable.is_file():
            raise StagedRuntimeVerificationError(
                f"Expected executable '{artifact.executable_relative_path}' was "
                "not found in the extracted archive."
            )

        member_checksums = _compute_bundle_member_checksums(staged_extract_dir)
        member_checksum_map = dict(member_checksums)
        executable_sha256 = member_checksum_map[
            PurePosixPath(artifact.executable_relative_path)
        ]

        checker(staged_executable, artifact.version_marker)

        receipt = InstallReceipt(
            schema_version=1,
            runtime_id=artifact.runtime_id,
            version_marker=artifact.version_marker,
            platform=artifact.platform,
            architecture=artifact.architecture,
            source_asset_identity=artifact.source_url,
            archive_size_bytes=artifact.archive_size_bytes,
            archive_sha256=artifact.archive_sha256,
            executable_relative_path=artifact.executable_relative_path,
            executable_sha256=executable_sha256,
            member_checksums=member_checksums,
        )
        _write_receipt(staged_extract_dir, receipt)

        try:
            os.replace(staged_extract_dir, final_install_dir)
        except OSError:
            if not final_install_dir.exists():
                raise
            # Lost a promotion race to a concurrent installer targeting the
            # same content-addressed path; its result is byte-identical by
            # construction, so adopt it instead of failing.
            receipt = verify_managed_install(final_install_dir)

    return _install_result(final_install_dir, receipt)


def verify_managed_install(install_dir: Path) -> InstallReceipt:
    """Validate an existing managed install directory against its receipt.

    Raises ``CorruptManagedInstallError`` if the receipt is missing,
    unreadable, or invalid, or if any declared bundle member is missing or
    fails its recorded checksum. A directory merely sitting under the
    runtime root is never treated as verified without this check passing.
    """
    receipt_path = install_dir / _RECEIPT_FILENAME
    try:
        raw_text = receipt_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CorruptManagedInstallError(
            f"No readable install receipt at '{receipt_path}'."
        ) from error
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise CorruptManagedInstallError(
            f"Install receipt at '{receipt_path}' is not valid JSON."
        ) from error
    try:
        receipt = InstallReceipt.from_mapping(data)
    except InstallReceiptError as error:
        raise CorruptManagedInstallError(
            f"Install receipt at '{receipt_path}' failed validation: {error}."
        ) from error

    for relative_path, expected_sha256 in receipt.member_checksums:
        member_path = install_dir / relative_path
        try:
            info = inspect_local_file(member_path)
        except VerificationError as error:
            raise CorruptManagedInstallError(
                f"Managed install member '{relative_path}' at '{install_dir}' is "
                "missing or unreadable."
            ) from error
        if info.sha256 != expected_sha256:
            raise CorruptManagedInstallError(
                f"Managed install member '{relative_path}' at '{install_dir}' "
                "failed checksum verification."
            )
    return receipt


def locate_managed_install_root(
    executable_path: Path, runtime_dir: Path
) -> Path | None:
    """Return the managed install directory owning ``executable_path``, if any.

    Managed installs are always direct children of ``runtime_dir`` by
    construction; this only checks path containment, not receipt validity —
    callers combine it with ``verify_managed_install`` to classify a
    configured executable as verified-managed vs. corrupt-managed vs.
    external (outside ``runtime_dir`` entirely).
    """
    try:
        resolved_executable = executable_path.resolve()
        resolved_runtime_dir = runtime_dir.resolve()
    except OSError:
        return None
    try:
        relative = resolved_executable.relative_to(resolved_runtime_dir)
    except ValueError:
        return None
    if not relative.parts:
        return None
    return resolved_runtime_dir / relative.parts[0]


def _install_result(
    install_dir: Path, receipt: InstallReceipt
) -> ManagedRuntimeInstall:
    return ManagedRuntimeInstall(
        executable_path=install_dir / receipt.executable_relative_path,
        runtime_id=receipt.runtime_id,
        version_marker=receipt.version_marker,
        install_dir=install_dir,
        receipt=receipt,
    )


def _content_addressed_dir_name(artifact: ManagedRuntimeArtifact) -> str:
    return f"{artifact.runtime_id}-{artifact.archive_sha256[:16]}"


def _compute_bundle_member_checksums(
    root: Path,
) -> tuple[tuple[PurePosixPath, str], ...]:
    """Hash every regular file under ``root``, keyed by its posix-relative path."""
    entries: list[tuple[PurePosixPath, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        info = inspect_local_file(path)
        entries.append((relative, info.sha256))
    return tuple(entries)


def _write_receipt(install_dir: Path, receipt: InstallReceipt) -> None:
    payload = json.dumps(receipt.to_mapping(), indent=2, sort_keys=True) + "\n"
    receipt_path = install_dir / _RECEIPT_FILENAME
    fd, tmp_name = tempfile.mkstemp(
        prefix=".receipt-", suffix=".tmp", dir=str(install_dir)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, receipt_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _offline_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "LLAMA_ARG_HF_REPO",
        "LLAMA_ARG_HF_FILE",
        "LLAMA_ARG_MODEL_URL",
    ):
        environment.pop(name, None)
    environment["LLAMA_ARG_OFFLINE"] = "1"
    return environment


def verify_executable_runs(executable_path: Path, version_marker: str) -> None:
    """Run the staged executable to confirm it actually starts and reports
    the expected pinned version marker, independent of any model."""
    if not executable_path.is_file():
        raise StagedRuntimeVerificationError(
            f"Staged executable is not a regular file: '{executable_path}'."
        )
    if os.name != "nt" and not os.access(executable_path, os.X_OK):
        try:
            current_mode = executable_path.stat().st_mode
            os.chmod(executable_path, current_mode | 0o111)
        except OSError as error:
            raise StagedRuntimeVerificationError(
                f"Staged executable is not executable and could not be made "
                f"executable: '{executable_path}'."
            ) from error

    try:
        result = subprocess.run(
            [str(executable_path), "--version", "--offline"],
            capture_output=True,
            text=True,
            timeout=_EXECUTABLE_CHECK_TIMEOUT_SECONDS,
            env=_offline_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StagedRuntimeVerificationError(
            f"Failed to run staged executable '{executable_path}' for readiness "
            f"verification: {error}."
        ) from error

    if result.returncode != 0:
        raise StagedRuntimeVerificationError(
            f"Staged executable '{executable_path}' failed its version readiness check."
        )
    combined_output = f"{result.stdout}\n{result.stderr}"
    if version_marker not in combined_output:
        raise StagedRuntimeVerificationError(
            f"Staged executable '{executable_path}' does not match the expected "
            f"runtime version marker '{version_marker}'."
        )
