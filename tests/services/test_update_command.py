"""Unit tests for the ``econpapers update`` application service."""

import hashlib
import io
import tarfile
from pathlib import Path, PurePosixPath

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.domain.model_manifest import (
    ManagedModelArtifact,
    ManagedModelCatalog,
)
from econ_paper_cli.domain.runtime_manifest import (
    ArchiveFormat,
    ManagedRuntimeArtifact,
    ManagedRuntimeManifest,
    SupportedArchitecture,
    SupportedPlatform,
)
from econ_paper_cli.services.platform_detection import DetectedPlatform
from econ_paper_cli.services.update_command import (
    UpdateArtifactOutcome,
    UpdateCommandOptions,
    execute_update_command,
    format_update_report,
    run_update_command,
)

_MODEL_PAYLOAD = b"synthetic update gguf model payload"
_MODEL_SHA256 = hashlib.sha256(_MODEL_PAYLOAD).hexdigest()

MODEL_ARTIFACT = ManagedModelArtifact(
    model_id="synthetic-update-model",
    display_name="Synthetic Update Model",
    source_url="https://example.invalid/model.gguf",
    size_bytes=len(_MODEL_PAYLOAD),
    sha256=_MODEL_SHA256,
    filename="synthetic-model.gguf",
    license_name="Apache-2.0",
    attribution_text="Test artifact.",
    summary="Synthetic test model.",
    minimum_free_ram_bytes=1024,
)
MODEL_CATALOG = ManagedModelCatalog(
    artifacts=(MODEL_ARTIFACT,), default_model_id=MODEL_ARTIFACT.model_id
)

_RUNTIME_EXE_BYTES = b"#!/bin/sh\necho llama.cpp test-version-1.0\n"
_RUNTIME_EXE_SHA = hashlib.sha256(_RUNTIME_EXE_BYTES).hexdigest()
_RUNTIME_LIB_BYTES = b"synthetic runtime lib"
_RUNTIME_LIB_SHA = hashlib.sha256(_RUNTIME_LIB_BYTES).hexdigest()


def _build_runtime_archive(tmp_path: Path) -> tuple[bytes, str, int]:
    archive_path = tmp_path / "runtime_src.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        info = tarfile.TarInfo("bin/llama-completion")
        info.size = len(_RUNTIME_EXE_BYTES)
        tf.addfile(info, io.BytesIO(_RUNTIME_EXE_BYTES))
        lib_info = tarfile.TarInfo("lib/libruntime.dylib")
        lib_info.size = len(_RUNTIME_LIB_BYTES)
        tf.addfile(lib_info, io.BytesIO(_RUNTIME_LIB_BYTES))
    raw = archive_path.read_bytes()
    return raw, hashlib.sha256(raw).hexdigest(), len(raw)


def _make_runtime_manifest(
    archive_sha: str, archive_size: int
) -> ManagedRuntimeManifest:
    artifact = ManagedRuntimeArtifact(
        runtime_id="synthetic-llama-cpp",
        version_marker="test-version-1.0",
        platform=SupportedPlatform.MACOS,
        architecture=SupportedArchitecture.ARM64,
        source_url="https://example.invalid/runtime.tar.gz",
        archive_format=ArchiveFormat.TAR_GZ,
        archive_size_bytes=archive_size,
        archive_sha256=archive_sha,
        executable_relative_path=PurePosixPath("bin/llama-completion"),
        bundle_member_checksums=(
            (PurePosixPath("bin/llama-completion"), _RUNTIME_EXE_SHA),
            (PurePosixPath("lib/libruntime.dylib"), _RUNTIME_LIB_SHA),
        ),
        license_name="MIT",
        attribution_text="Test runtime artifact.",
    )
    return ManagedRuntimeManifest(schema_version=1, artifacts=(artifact,))


DETECTED = DetectedPlatform(
    platform=SupportedPlatform.MACOS,
    architecture=SupportedArchitecture.ARM64,
    raw_system="Darwin",
    raw_machine="arm64",
)


class RecordingDownloader:
    def __init__(
        self,
        model_payload: bytes = _MODEL_PAYLOAD,
        runtime_payload: bytes = b"",
    ) -> None:
        self.model_payload = model_payload
        self.runtime_payload = runtime_payload
        self.download_count = 0

    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        self.download_count += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url == MODEL_ARTIFACT.source_url:
            destination.write_bytes(self.model_payload)
        else:
            destination.write_bytes(self.runtime_payload)


class FakeExtractor:
    def extract(
        self, archive_path: Path, archive_format: object, destination_dir: Path
    ) -> None:
        exe_path = destination_dir / "bin" / "llama-completion"
        exe_path.parent.mkdir(parents=True, exist_ok=True)
        exe_path.write_bytes(_RUNTIME_EXE_BYTES)

        lib_path = destination_dir / "lib" / "libruntime.dylib"
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        lib_path.write_bytes(_RUNTIME_LIB_BYTES)


def _noop_checker(exe: Path, marker: str) -> None:
    pass


def test_no_durable_config_returns_not_configured_and_exit_code_1(
    tmp_path: Path,
) -> None:
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    config_backend = JSONConfigStorage(tmp_path / "missing_config.json")
    options = UpdateCommandOptions()

    report = execute_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=tmp_path / "runtimes",
        model_dir=tmp_path / "models",
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        detected_platform=DETECTED,
    )

    assert report.config_present is False
    assert report.runtime_outcome is UpdateArtifactOutcome.NOT_CONFIGURED
    assert report.model_outcome is UpdateArtifactOutcome.NOT_CONFIGURED
    assert report.overall_success is False

    exit_code = run_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=tmp_path / "runtimes",
        model_dir=tmp_path / "models",
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        detected_platform=DETECTED,
    )
    assert exit_code == 1


def test_valid_managed_runtime_and_model_are_both_reused_exit_code_0(
    tmp_path: Path,
) -> None:
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    downloader.download_count = 0

    options = UpdateCommandOptions()
    report = execute_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.config_present is True
    assert report.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert report.model_outcome is UpdateArtifactOutcome.REUSED
    assert report.overall_success is True
    assert downloader.download_count == 0

    exit_code = run_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )
    assert exit_code == 0


def test_corrupt_managed_artifacts_are_repaired_acceptance_test(
    tmp_path: Path,
) -> None:
    """Acceptance test: setup -> update (REUSED) -> corrupt GGUF & runtime -> update (REPAIRED)
    -> second update immediately after reports REUSED with zero network calls."""
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    # Corrupt model and runtime lib
    md_install.model_path.write_bytes(b"corrupt model bytes")
    (rt_install.install_dir / "lib" / "libruntime.dylib").write_bytes(b"corrupt lib")

    downloader.download_count = 0

    options = UpdateCommandOptions()
    report = execute_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.runtime_outcome is UpdateArtifactOutcome.REPAIRED
    assert report.model_outcome is UpdateArtifactOutcome.REPAIRED
    assert report.overall_success is True
    assert downloader.download_count == 2

    # Second update immediately after must report REUSED with 0 downloads
    downloader.download_count = 0
    report2 = execute_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report2.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert report2.model_outcome is UpdateArtifactOutcome.REUSED
    assert downloader.download_count == 0


def test_runtime_lacking_declared_identity_is_external_skipped(tmp_path: Path) -> None:
    """Regression test for finding 5's runtime half: executable under managed root but
    config.runtime_id / runtime_version_marker are None -> EXTERNAL_SKIPPED."""
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=None,
        runtime_version_marker=None,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    downloader.download_count = 0

    report = execute_update_command(
        UpdateCommandOptions(),
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.runtime_outcome is UpdateArtifactOutcome.EXTERNAL_SKIPPED
    assert report.model_outcome is UpdateArtifactOutcome.REUSED
    assert downloader.download_count == 0


def test_model_lacking_managed_origin_flag_is_external_skipped(
    tmp_path: Path,
) -> None:
    """Regression test for finding 5's model half: model path matches catalog filename
    and config.model_id matches, but managed_model_provisioning is False -> EXTERNAL_SKIPPED."""
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=False,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    downloader.download_count = 0

    report = execute_update_command(
        UpdateCommandOptions(),
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.model_outcome is UpdateArtifactOutcome.EXTERNAL_SKIPPED
    assert report.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert downloader.download_count == 0


def test_externally_supplied_runtime_and_model_are_external_skipped(
    tmp_path: Path,
) -> None:
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    ext_exe = tmp_path / "external" / "llama-completion"
    ext_exe.parent.mkdir()
    ext_exe.write_bytes(b"ext exe")
    ext_model = tmp_path / "external" / "model.gguf"
    ext_model.write_bytes(b"ext model")

    config = LocalRuntimeModelConfig(
        executable_path=ext_exe,
        model_path=ext_model,
        model_id="custom-model",
        model_bytes=len(b"ext model"),
        model_checksum=hashlib.sha256(b"ext model").hexdigest(),
        runtime_id=None,
        runtime_version_marker=None,
        managed_model_provisioning=False,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    downloader = RecordingDownloader(runtime_payload=rt_bytes)

    report = execute_update_command(
        UpdateCommandOptions(),
        config_backend=config_backend,
        runtime_dir=tmp_path / "runtimes",
        model_dir=tmp_path / "models",
        downloader=downloader,
        extractor=FakeExtractor(),
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.runtime_outcome is UpdateArtifactOutcome.EXTERNAL_SKIPPED
    assert report.model_outcome is UpdateArtifactOutcome.EXTERNAL_SKIPPED
    assert downloader.download_count == 0


def test_manifest_version_mismatch_reports_newer_version_available(
    tmp_path: Path,
) -> None:
    """Regression test for finding 2/4: code manifest pins a newer identity than configured -> NEWER_VERSION_AVAILABLE, no repair attempted."""
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    # Config holds an older sha256 checksum for model
    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum="0" * 64,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    downloader.download_count = 0

    report = execute_update_command(
        UpdateCommandOptions(),
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.model_outcome is UpdateArtifactOutcome.NEWER_VERSION_AVAILABLE
    assert report.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert downloader.download_count == 0


def test_offline_mode_refuses_required_download(tmp_path: Path) -> None:
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    # Corrupt model file
    md_install.model_path.write_bytes(b"corrupt")

    downloader.download_count = 0

    report = execute_update_command(
        UpdateCommandOptions(offline=True),
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.model_outcome is UpdateArtifactOutcome.UNAVAILABLE_OFFLINE
    assert report.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert downloader.download_count == 0
    assert md_install.model_path.read_bytes() == b"corrupt"


def test_model_filename_rename_reports_newer_version_available(
    tmp_path: Path,
) -> None:
    """Regression test for P1: model catalog update renames the GGUF filename.
    A managed-origin config with matching model_id but older filename must report
    NEWER_VERSION_AVAILABLE (exit code 1) without being mislabeled EXTERNAL_SKIPPED."""
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    # Config has old filename under model_dir
    old_model_path = model_dir / "old-model-name.gguf"
    old_model_path.write_bytes(_MODEL_PAYLOAD)

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=old_model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    downloader.download_count = 0

    options = UpdateCommandOptions()
    report = execute_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.model_outcome is UpdateArtifactOutcome.NEWER_VERSION_AVAILABLE
    assert report.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert downloader.download_count == 0

    exit_code = run_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )
    assert exit_code == 1


class ExplodingDownloader:
    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        raise OSError("simulated network connection failure")


def test_update_download_failure_reports_failed_and_exit_code_3(
    tmp_path: Path,
) -> None:
    """Test driving an update repair download failure to FAILED and exit code 3."""
    rt_bytes, rt_sha, rt_size = _build_runtime_archive(tmp_path)
    manifest = _make_runtime_manifest(rt_sha, rt_size)
    runtime_dir = tmp_path / "runtimes"
    model_dir = tmp_path / "models"
    downloader = RecordingDownloader(runtime_payload=rt_bytes)
    extractor = FakeExtractor()

    from econ_paper_cli.services.model_provisioning import ensure_managed_model
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    rt_install = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED,
        executable_readiness_checker=_noop_checker,
    )
    md_install = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    config = LocalRuntimeModelConfig(
        executable_path=rt_install.executable_path,
        model_path=md_install.model_path,
        model_id=MODEL_ARTIFACT.model_id,
        model_bytes=MODEL_ARTIFACT.size_bytes,
        model_checksum=MODEL_ARTIFACT.sha256,
        runtime_id=rt_install.runtime_id,
        runtime_version_marker=rt_install.version_marker,
        managed_model_provisioning=True,
    )
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(config)

    # Corrupt model file
    md_install.model_path.write_bytes(b"corrupt")

    exploding_downloader = ExplodingDownloader()

    options = UpdateCommandOptions()
    report = execute_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=exploding_downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )

    assert report.model_outcome is UpdateArtifactOutcome.FAILED
    assert report.runtime_outcome is UpdateArtifactOutcome.REUSED
    assert "simulated network connection failure" in str(report.model_detail)

    exit_code = run_update_command(
        options,
        config_backend=config_backend,
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        downloader=exploding_downloader,
        extractor=extractor,
        runtime_manifest=manifest,
        model_catalog=MODEL_CATALOG,
        runtime_readiness_checker=_noop_checker,
        detected_platform=DETECTED,
    )
    assert exit_code == 3


def test_format_update_report_rendering() -> None:
    report = execute_update_command(
        UpdateCommandOptions(),
        config_backend=JSONConfigStorage(Path("/custom/config.json")),
    )
    text = format_update_report(report)
    assert "=== Local Update Result ===" in text
    assert "Configuration Path: /custom/config.json" in text
