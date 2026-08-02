"""Service tests for atomic managed-runtime installation.

Fault-injection tests mapped to the issue #58 plan: staged-readiness
failure never promotes, concurrent setup converges safely, config-save
failure after promotion leaves the promoted install reusable and the prior
config untouched (covered at the setup_command layer, not here — this file
proves the provisioning half stays reusable independent of that), corrupt
receipt / tampered supporting library is detected and never trusted, and a
failed re-provision attempt never destroys a previously verified install.
"""

import hashlib
import io
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from econ_paper_cli.domain.runtime_manifest import (
    ArchiveFormat,
    ManagedRuntimeArtifact,
    ManagedRuntimeManifest,
    SupportedArchitecture,
    SupportedPlatform,
)
from econ_paper_cli.services.platform_detection import DetectedPlatform
from econ_paper_cli.services.runtime_provisioning import (
    CorruptManagedInstallError,
    OfflineProvisioningError,
    StagedRuntimeVerificationError,
    UnsupportedPlatformError,
    ensure_managed_runtime,
    locate_managed_install_root,
    verify_managed_install,
)

_DETECTED = DetectedPlatform(
    platform=SupportedPlatform.LINUX,
    architecture=SupportedArchitecture.X86_64,
    raw_system="Linux",
    raw_machine="x86_64",
)


def _build_archive(
    tmp_path: Path, *, executable_data: bytes = b"#!/bin/sh\necho ok\n"
) -> tuple[bytes, str, int]:
    archive_path = tmp_path / "src.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        info = tarfile.TarInfo("pkg/tool")
        info.size = len(executable_data)
        tf.addfile(info, io.BytesIO(executable_data))
        lib_data = b"shared library bytes"
        lib_info = tarfile.TarInfo("pkg/lib.so")
        lib_info.size = len(lib_data)
        tf.addfile(lib_info, io.BytesIO(lib_data))
    archive_bytes = archive_path.read_bytes()
    return archive_bytes, hashlib.sha256(archive_bytes).hexdigest(), len(archive_bytes)


def _artifact(archive_sha256: str, archive_size: int) -> ManagedRuntimeArtifact:
    return ManagedRuntimeArtifact(
        runtime_id="llama.cpp-test",
        version_marker="999",
        platform=SupportedPlatform.LINUX,
        architecture=SupportedArchitecture.X86_64,
        source_url="https://example.com/tool.tar.gz",
        archive_format=ArchiveFormat.TAR_GZ,
        archive_size_bytes=archive_size,
        archive_sha256=archive_sha256,
        executable_relative_path=PurePosixPath("pkg/tool"),
        license_name="MIT",
        attribution_text="test",
    )


class _FakeDownloader:
    def __init__(self, archive_bytes: bytes, *, calls: list[str] | None = None):
        self._archive_bytes = archive_bytes
        self.calls = calls if calls is not None else []

    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        self.calls.append(url)
        destination.write_bytes(self._archive_bytes)


class _BoomDownloader:
    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        raise AssertionError("download must not be called")


class _FakeExtractor:
    def extract(
        self, archive_path: Path, archive_format, destination_dir: Path
    ) -> None:
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(destination_dir, filter="data")


def _noop_checker(executable_path: Path, version_marker: str) -> None:
    pass


def _manifest_and_bytes(tmp_path: Path):
    archive_bytes, sha, size = _build_archive(tmp_path)
    artifact = _artifact(sha, size)
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(artifact,))
    return manifest, archive_bytes


# --- Happy path / idempotency -------------------------------------------


def test_fresh_install_succeeds_and_is_reused_without_redownload(
    tmp_path: Path,
) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    downloader = _FakeDownloader(archive_bytes)

    first = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    assert first.executable_path.is_file()
    assert len(downloader.calls) == 1

    second = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_BoomDownloader(),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    assert second.install_dir == first.install_dir
    assert second.receipt == first.receipt


def test_unsupported_platform_raises_without_touching_filesystem(
    tmp_path: Path,
) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    unsupported = DetectedPlatform(
        platform=None, architecture=None, raw_system="FreeBSD", raw_machine="x86_64"
    )

    with pytest.raises(UnsupportedPlatformError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_BoomDownloader(),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=unsupported,
            executable_readiness_checker=_noop_checker,
        )
    assert not runtime_dir.exists()


def test_no_pinned_artifact_for_platform_raises(tmp_path: Path) -> None:
    manifest, _ = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    windows = DetectedPlatform(
        platform=SupportedPlatform.WINDOWS,
        architecture=SupportedArchitecture.ARM64,
        raw_system="Windows",
        raw_machine="ARM64",
    )
    with pytest.raises(UnsupportedPlatformError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_BoomDownloader(),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=windows,
            executable_readiness_checker=_noop_checker,
        )


# --- Offline / opt-out ---------------------------------------------------


def test_offline_with_no_managed_runtime_raises_without_network(tmp_path: Path) -> None:
    manifest, _ = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"

    with pytest.raises(OfflineProvisioningError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_BoomDownloader(),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=_DETECTED,
            allow_download=False,
            executable_readiness_checker=_noop_checker,
        )
    assert not runtime_dir.exists()


def test_offline_reuses_existing_verified_install(tmp_path: Path) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )

    result = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_BoomDownloader(),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        allow_download=False,
        executable_readiness_checker=_noop_checker,
    )
    assert result.executable_path.is_file()


# --- Staged verification never promotes on failure ------------------------


def test_staged_readiness_failure_never_promotes_and_cleans_up(tmp_path: Path) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"

    def failing_checker(executable_path: Path, version_marker: str) -> None:
        raise StagedRuntimeVerificationError("simulated readiness failure")

    with pytest.raises(StagedRuntimeVerificationError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_FakeDownloader(archive_bytes),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=_DETECTED,
            executable_readiness_checker=failing_checker,
        )

    remaining = list(runtime_dir.iterdir()) if runtime_dir.exists() else []
    assert remaining == [], "no install or leftover staging directory may remain"


def test_missing_executable_in_archive_never_promotes(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        data = b"not the executable"
        info = tarfile.TarInfo("pkg/other-file")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    archive_bytes = archive_path.read_bytes()
    sha = hashlib.sha256(archive_bytes).hexdigest()
    artifact = _artifact(sha, len(archive_bytes))
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(artifact,))
    runtime_dir = tmp_path / "runtime"

    with pytest.raises(StagedRuntimeVerificationError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_FakeDownloader(archive_bytes),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=_DETECTED,
            executable_readiness_checker=_noop_checker,
        )

    remaining = list(runtime_dir.iterdir()) if runtime_dir.exists() else []
    assert remaining == [], "no install or leftover staging directory may remain"


def test_downloaded_archive_checksum_mismatch_never_promotes(tmp_path: Path) -> None:
    """The manifest's pinned SHA-256 is verified against the *downloaded*
    archive before extraction; a mismatch must abort before anything is
    extracted or promoted, and clean up the staging directory. The raw
    filesystem-layer error is wrapped into a RuntimeProvisioningError
    subtype so it never escapes the typed setup boundary (issue #58
    review)."""
    archive_bytes, _correct_sha, size = _build_archive(tmp_path)
    wrong_sha = "0" * 64
    artifact = _artifact(wrong_sha, size)
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(artifact,))
    runtime_dir = tmp_path / "runtime"

    with pytest.raises(StagedRuntimeVerificationError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_FakeDownloader(archive_bytes),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=_DETECTED,
            executable_readiness_checker=_noop_checker,
        )

    remaining = list(runtime_dir.iterdir()) if runtime_dir.exists() else []
    assert remaining == [], "no install or leftover staging directory may remain"


def test_downloaded_archive_size_mismatch_never_promotes(tmp_path: Path) -> None:
    """A manifest expected_size that doesn't match the actual downloaded
    archive size is caught before extraction (independent of the
    downloader's own incremental byte-cap enforcement), and wrapped into a
    RuntimeProvisioningError subtype rather than escaping raw."""
    archive_bytes, sha, size = _build_archive(tmp_path)
    artifact = _artifact(sha, size + 1)
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(artifact,))
    runtime_dir = tmp_path / "runtime"

    class OversizedIgnoringDownloader:
        """Simulates a downloader that (incorrectly) ignores its own
        expected_size_bytes cap, so the provisioning-level size check is
        exercised independently of the downloader's own enforcement."""

        def download(
            self, url: str, destination: Path, *, expected_size_bytes: int
        ) -> None:
            destination.write_bytes(archive_bytes)

    with pytest.raises(StagedRuntimeVerificationError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=OversizedIgnoringDownloader(),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=_DETECTED,
            executable_readiness_checker=_noop_checker,
        )

    remaining = list(runtime_dir.iterdir()) if runtime_dir.exists() else []
    assert remaining == [], "no install or leftover staging directory may remain"


# --- Corruption detection --------------------------------------------------


def test_undeclared_extra_file_in_install_dir_is_detected_as_corrupt(
    tmp_path: Path,
) -> None:
    """An injected file not present in the receipt's member_checksums (e.g.
    a malicious adjacent library) must not silently pass verification just
    because every *declared* member still checks out."""
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )

    (install.install_dir / "pkg" / "injected-extra.so").write_bytes(b"malicious")

    with pytest.raises(CorruptManagedInstallError):
        verify_managed_install(install.install_dir)


def test_reuse_check_rejects_receipt_that_mismatches_current_manifest(
    tmp_path: Path,
) -> None:
    """A self-consistent receipt (every declared member checksum matches)
    that simply does not match the manifest-selected artifact anymore (e.g.
    a stale install from a since-changed pinned release) must be treated as
    corrupt and reinstalled, not silently reused."""
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    first = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    verify_managed_install(first.install_dir)  # self-consistent, still valid alone

    # A manifest update repoints the same runtime_id at a different pinned
    # archive (different source URL), without the on-disk install having
    # changed at all.
    changed_artifact = _artifact(
        first.receipt.archive_sha256, first.receipt.archive_size_bytes
    )
    changed_artifact = ManagedRuntimeArtifact(
        runtime_id=changed_artifact.runtime_id,
        version_marker=changed_artifact.version_marker,
        platform=changed_artifact.platform,
        architecture=changed_artifact.architecture,
        source_url="https://example.com/a-different-pinned-release.tar.gz",
        archive_format=changed_artifact.archive_format,
        archive_size_bytes=changed_artifact.archive_size_bytes,
        archive_sha256=changed_artifact.archive_sha256,
        executable_relative_path=changed_artifact.executable_relative_path,
        license_name=changed_artifact.license_name,
        attribution_text=changed_artifact.attribution_text,
    )

    with pytest.raises(CorruptManagedInstallError):
        verify_managed_install(first.install_dir, expected_artifact=changed_artifact)

    # And ensure_managed_runtime itself must not silently reuse it either —
    # it re-provisions (using the *new* artifact's download) instead.
    changed_manifest = ManagedRuntimeManifest(
        schema_version=1, artifacts=(changed_artifact,)
    )
    downloader = _FakeDownloader(archive_bytes)
    second = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=_FakeExtractor(),
        manifest=changed_manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    assert len(downloader.calls) == 1
    assert second.receipt.source_asset_identity == (
        "https://example.com/a-different-pinned-release.tar.gz"
    )


def test_corrupt_receipt_is_never_trusted_and_triggers_reinstall(
    tmp_path: Path,
) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    first = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )

    receipt_path = first.install_dir / "receipt.json"
    receipt_path.write_text("not valid json{{{", encoding="utf-8")

    with pytest.raises(CorruptManagedInstallError):
        verify_managed_install(first.install_dir)

    # A fresh ensure_managed_runtime call detects the corruption, evicts it,
    # and reinstalls cleanly to the same content-addressed path.
    downloader = _FakeDownloader(archive_bytes)
    second = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    assert len(downloader.calls) == 1
    assert second.install_dir == first.install_dir
    verify_managed_install(second.install_dir)  # does not raise


def test_tampered_supporting_library_detected_even_when_executable_intact(
    tmp_path: Path,
) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )

    lib_path = install.install_dir / "pkg" / "lib.so"
    lib_path.write_bytes(b"TAMPERED")

    with pytest.raises(CorruptManagedInstallError):
        verify_managed_install(install.install_dir)


def test_offline_never_treats_corrupt_managed_install_as_ready(tmp_path: Path) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    (install.install_dir / "pkg" / "tool").write_bytes(b"TAMPERED EXECUTABLE")

    with pytest.raises(OfflineProvisioningError):
        ensure_managed_runtime(
            runtime_dir=runtime_dir,
            downloader=_BoomDownloader(),
            extractor=_FakeExtractor(),
            manifest=manifest,
            detected=_DETECTED,
            allow_download=False,
            executable_readiness_checker=_noop_checker,
        )


# --- Concurrency / promotion-race safety -----------------------------------


def test_lost_promotion_race_adopts_concurrent_winner_instead_of_failing(
    tmp_path: Path,
) -> None:
    """Simulates another process finishing installation to the same
    content-addressed path in between our staging and our own promotion."""
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"

    winner_install = None

    class RacingExtractor:
        def extract(
            self, archive_path: Path, archive_format, destination_dir: Path
        ) -> None:
            nonlocal winner_install
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(destination_dir, filter="data")
            # Simulate a concurrent installer completing first: install a
            # verified bundle at the final content-addressed path now.
            winner_install = ensure_managed_runtime(
                runtime_dir=runtime_dir,
                downloader=_FakeDownloader(archive_bytes),
                extractor=_FakeExtractor(),
                manifest=manifest,
                detected=_DETECTED,
                executable_readiness_checker=_noop_checker,
            )

    result = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=RacingExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )

    assert winner_install is not None
    assert result.install_dir == winner_install.install_dir
    assert result.executable_path.is_file()
    verify_managed_install(result.install_dir)  # still valid, not corrupted


# --- locate_managed_install_root -------------------------------------------


def test_locate_managed_install_root_finds_managed_executable(tmp_path: Path) -> None:
    manifest, archive_bytes = _manifest_and_bytes(tmp_path)
    runtime_dir = tmp_path / "runtime"
    install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=_FakeDownloader(archive_bytes),
        extractor=_FakeExtractor(),
        manifest=manifest,
        detected=_DETECTED,
        executable_readiness_checker=_noop_checker,
    )

    found = locate_managed_install_root(install.executable_path, runtime_dir)
    assert found == install.install_dir


def test_locate_managed_install_root_returns_none_for_external_executable(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    external_executable = tmp_path / "external" / "llama-completion"
    external_executable.parent.mkdir(parents=True)
    external_executable.write_text("fake")

    assert locate_managed_install_root(external_executable, runtime_dir) is None
