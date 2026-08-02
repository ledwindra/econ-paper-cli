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


class RuntimeInstallIOError(RuntimeProvisioningError):
    """Raised when a routine filesystem operation during install/promotion
    fails (directory creation, receipt write, promotion, or eviction of a
    corrupt install) — mapped from a raw ``OSError`` so it never escapes the
    typed setup boundary as an unhandled exception."""


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

    final_install_dir = runtime_dir / content_addressed_install_dir_name(artifact)

    if final_install_dir.exists():
        receipt = _reuse_if_functional(final_install_dir, artifact, checker)
        if receipt is not None:
            return _install_result(final_install_dir, receipt)
        if not allow_download:
            raise OfflineProvisioningError(
                "An installed managed runtime exists but failed integrity or "
                "readiness verification, and downloads are disabled. Run setup "
                "with network access, or supply an explicit executable path."
            )
        try:
            shutil.rmtree(final_install_dir)
        except OSError as error:
            raise RuntimeInstallIOError(
                f"Failed to remove the corrupt install at '{final_install_dir}': "
                f"{error}."
            ) from error

    if not allow_download:
        raise OfflineProvisioningError(
            "No verified managed runtime is installed and downloads are disabled. "
            "Run setup with network access, or supply an explicit executable path."
        )

    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        staging_context = tempfile.TemporaryDirectory(
            prefix=".staging-", dir=str(runtime_dir)
        )
    except OSError as error:
        raise RuntimeInstallIOError(
            f"Failed to prepare the runtime directory '{runtime_dir}': {error}."
        ) from error

    with staging_context as staging:
        staging_dir = Path(staging)
        archive_path = staging_dir / f"archive.{artifact.archive_format.value}"
        staged_extract_dir = staging_dir / "extracted"
        try:
            staged_extract_dir.mkdir()
        except OSError as error:
            raise RuntimeInstallIOError(
                f"Failed to create staging directory '{staged_extract_dir}': {error}."
            ) from error

        downloader.download(
            artifact.source_url,
            archive_path,
            expected_size_bytes=artifact.archive_size_bytes,
        )
        try:
            verify_local_file(
                archive_path, artifact.archive_size_bytes, artifact.archive_sha256
            )
        except VerificationError as error:
            raise StagedRuntimeVerificationError(
                f"Downloaded archive for '{artifact.source_url}' failed local "
                f"verification: {error}."
            ) from error

        extractor.extract(archive_path, artifact.archive_format, staged_extract_dir)

        staged_executable = staged_extract_dir / artifact.executable_relative_path
        if not staged_executable.is_file():
            raise StagedRuntimeVerificationError(
                f"Expected executable '{artifact.executable_relative_path}' was "
                "not found in the extracted archive."
            )
        _ensure_staged_executable_bit(staged_executable)

        try:
            _verify_staged_bundle_against_manifest(staged_extract_dir, artifact)
        except VerificationError as error:
            raise StagedRuntimeVerificationError(
                f"Failed to inspect a staged bundle member: {error}."
            ) from error

        checker(staged_executable, artifact.version_marker)

        executable_sha256 = dict(artifact.bundle_member_checksums)[
            artifact.executable_relative_path
        ]
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
            member_checksums=artifact.bundle_member_checksums,
        )
        _write_receipt(staged_extract_dir, receipt)

        try:
            os.replace(staged_extract_dir, final_install_dir)
        except OSError as error:
            if not final_install_dir.exists():
                raise RuntimeInstallIOError(
                    f"Failed to promote the staged install to "
                    f"'{final_install_dir}': {error}."
                ) from error
            # Lost a promotion race to a concurrent installer targeting the
            # same content-addressed path; its result is byte-identical by
            # construction, so adopt it instead of failing — but only if it
            # actually passes the same integrity + functional checks this
            # install just passed.
            adopted = _reuse_if_functional(final_install_dir, artifact, checker)
            if adopted is None:
                raise RuntimeInstallIOError(
                    f"Failed to promote the staged install to "
                    f"'{final_install_dir}', and the existing directory there "
                    f"is not a usable managed install: {error}."
                ) from error
            receipt = adopted

    return _install_result(final_install_dir, receipt)


def _reuse_if_functional(
    install_dir: Path,
    artifact: ManagedRuntimeArtifact,
    checker: ExecutableReadinessChecker,
) -> InstallReceipt | None:
    """Return a receipt only if the install both integrity-verifies against
    the pinned artifact and functionally passes the readiness checker.

    Reuse requires both: a checksum-valid install whose executable bit was
    stripped (or that otherwise fails to actually run) must not be reused
    silently — the caller re-provisions or reports a typed offline failure.
    """
    try:
        receipt = verify_managed_install(install_dir, expected_artifact=artifact)
    except CorruptManagedInstallError:
        return None
    executable_path = install_dir / receipt.executable_relative_path
    try:
        checker(executable_path, receipt.version_marker)
    except Exception:
        return None
    return receipt


def verify_managed_install(
    install_dir: Path,
    expected_artifact: ManagedRuntimeArtifact | None = None,
) -> InstallReceipt:
    """Validate an existing managed install directory against its receipt.

    Raises ``CorruptManagedInstallError`` if the receipt is missing,
    unreadable, or invalid; if any declared bundle member is missing or
    fails its recorded checksum; if the install directory contains any
    regular file *not* declared in the receipt (so an injected adjacent
    library cannot escape bundle-integrity checks); or — when
    ``expected_artifact`` is given — if the receipt's identity (runtime id,
    version marker, platform, architecture, source asset, archive
    size/hash, executable path) does not exactly match the manifest-selected
    artifact. A directory merely sitting under the runtime root, or a
    receipt that is merely internally self-consistent, is never treated as
    verified without both checks passing.
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

    if expected_artifact is not None:
        _verify_receipt_matches_artifact(receipt, expected_artifact, install_dir)
        expected_dir_name = content_addressed_install_dir_name(expected_artifact)
        if install_dir.name != expected_dir_name:
            raise CorruptManagedInstallError(
                f"Install directory '{install_dir}' does not have the expected "
                f"content-addressed name '{expected_dir_name}' for the pinned "
                "manifest artifact."
            )

    declared_paths: set[PurePosixPath] = set()
    for relative_path, expected_sha256 in receipt.member_checksums:
        declared_paths.add(relative_path)
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

    # Every non-directory entry — including symlinks and other special
    # files a legitimate install never produces — must be declared. Only
    # excluding symlinks here would let one hide from bundle-integrity
    # checks entirely.
    actual_paths = {
        PurePosixPath(path.relative_to(install_dir).as_posix())
        for path in install_dir.rglob("*")
        if not (path.is_dir() and not path.is_symlink())
    } - {PurePosixPath(_RECEIPT_FILENAME)}
    undeclared = actual_paths - declared_paths
    if undeclared:
        raise CorruptManagedInstallError(
            f"Managed install at '{install_dir}' contains undeclared file(s) "
            f"not present in the receipt: {sorted(str(p) for p in undeclared)}."
        )
    return receipt


def _verify_receipt_matches_artifact(
    receipt: InstallReceipt,
    expected_artifact: ManagedRuntimeArtifact,
    install_dir: Path,
) -> None:
    mismatches = []
    if receipt.runtime_id != expected_artifact.runtime_id:
        mismatches.append("runtime_id")
    if receipt.version_marker != expected_artifact.version_marker:
        mismatches.append("version_marker")
    if receipt.platform != expected_artifact.platform:
        mismatches.append("platform")
    if receipt.architecture != expected_artifact.architecture:
        mismatches.append("architecture")
    if receipt.source_asset_identity != expected_artifact.source_url:
        mismatches.append("source_asset_identity")
    if receipt.archive_size_bytes != expected_artifact.archive_size_bytes:
        mismatches.append("archive_size_bytes")
    if receipt.archive_sha256 != expected_artifact.archive_sha256:
        mismatches.append("archive_sha256")
    if receipt.executable_relative_path != expected_artifact.executable_relative_path:
        mismatches.append("executable_relative_path")
    if mismatches:
        raise CorruptManagedInstallError(
            f"Install receipt at '{install_dir}' does not match the pinned "
            f"manifest artifact: {', '.join(mismatches)}."
        )


def _ensure_staged_executable_bit(staged_executable: Path) -> None:
    """Make a freshly extracted, staged executable actually runnable.

    Only ever applied to content this install just extracted into its own
    staging directory (never to an already-installed or externally
    configured executable) — ``verify_executable_runs`` itself stays
    strictly read-only so ``status`` can reuse it safely.
    """
    if os.name == "nt" or os.access(staged_executable, os.X_OK):
        return
    try:
        current_mode = staged_executable.stat().st_mode
        os.chmod(staged_executable, current_mode | 0o111)
    except OSError as error:
        raise StagedRuntimeVerificationError(
            f"Staged executable is not executable and could not be made "
            f"executable: '{staged_executable}'."
        ) from error


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


def content_addressed_install_dir_name(artifact: ManagedRuntimeArtifact) -> str:
    """Return the canonical install-directory name for one pinned artifact.

    Public so ``status`` can validate that a managed install's directory
    name actually matches what the current manifest would install, not only
    that its receipt is internally self-consistent.
    """
    return f"{artifact.runtime_id}-{artifact.archive_sha256[:16]}"


def _verify_staged_bundle_against_manifest(
    staged_extract_dir: Path,
    artifact: ManagedRuntimeArtifact,
) -> None:
    """Verify every staged file against the manifest's static declaration.

    Anchors per-file integrity in the version-controlled manifest itself,
    not merely in whatever gets computed and written to ``receipt.json`` —
    a tampered extracted file (plus a correspondingly rewritten receipt)
    still fails here. Also rejects any staged file not declared in
    ``bundle_member_checksums`` (an injected extra library) and any
    non-regular entry (symlink, etc.) a legitimate archive should never
    produce.
    """
    declared = dict(artifact.bundle_member_checksums)
    for relative_path, expected_sha256 in declared.items():
        member_path = staged_extract_dir / relative_path
        info = inspect_local_file(member_path)
        if info.sha256 != expected_sha256:
            raise StagedRuntimeVerificationError(
                f"Staged bundle member '{relative_path}' does not match the "
                "pinned manifest's declared checksum."
            )

    actual_paths: set[PurePosixPath] = set()
    for path in staged_extract_dir.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = PurePosixPath(path.relative_to(staged_extract_dir).as_posix())
        actual_paths.add(relative)

    undeclared = actual_paths - set(declared)
    if undeclared:
        raise StagedRuntimeVerificationError(
            "Staged bundle contains file(s) not declared in the pinned "
            f"manifest: {sorted(str(p) for p in undeclared)}."
        )


def _write_receipt(install_dir: Path, receipt: InstallReceipt) -> None:
    payload = json.dumps(receipt.to_mapping(), indent=2, sort_keys=True) + "\n"
    receipt_path = install_dir / _RECEIPT_FILENAME
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".receipt-", suffix=".tmp", dir=str(install_dir)
        )
    except OSError as error:
        raise RuntimeInstallIOError(
            f"Failed to create a temporary receipt file in '{install_dir}': {error}."
        ) from error
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, receipt_path)
    except OSError as error:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeInstallIOError(
            f"Failed to write the install receipt to '{receipt_path}': {error}."
        ) from error


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
    """Run an executable to confirm it actually starts and reports the
    expected pinned version marker, independent of any model.

    Strictly read-only: never modifies the executable's permissions or any
    other metadata. ``status`` relies on this for its read-only guarantee.
    Callers that own freshly staged content (see ``_ensure_staged_executable_bit``)
    are responsible for making it executable *before* calling this.
    """
    if not executable_path.is_file():
        raise StagedRuntimeVerificationError(
            f"Executable is not a regular file: '{executable_path}'."
        )
    if os.name != "nt" and not os.access(executable_path, os.X_OK):
        raise StagedRuntimeVerificationError(
            f"Executable is not marked executable: '{executable_path}'."
        )

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
            f"Executable '{executable_path}' failed its version readiness check."
        )
    combined_output = f"{result.stdout}\n{result.stderr}"
    if version_marker not in combined_output:
        raise StagedRuntimeVerificationError(
            f"Executable '{executable_path}' does not match the expected "
            f"runtime version marker '{version_marker}'."
        )
