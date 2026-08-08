"""Unit tests for the ``econpapers setup`` application service."""

import io
from pathlib import Path, PurePosixPath

import pytest

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig, LlamaCppReadinessError
from econ_paper_cli.domain.runtime_manifest import (
    SupportedArchitecture,
    SupportedPlatform,
)
from econ_paper_cli.domain.runtime_receipt import InstallReceipt
from econ_paper_cli.services.model_provisioning import (
    ManagedModelInstall,
    OfflineModelProvisioningError,
)
from econ_paper_cli.services.runtime_provisioning import (
    ManagedRuntimeInstall,
    OfflineProvisioningError,
    UnsupportedPlatformError,
)
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
    assert loaded.executable_path == Path("/usr/local/bin/llama-completion").resolve()


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


def test_setup_with_explicit_path_makes_no_network_request_or_download(
    tmp_path: Path,
) -> None:
    """Narrowed per issue #58: setup is network-free only when an explicit
    --llama-cpp-path is given (bypasses provisioning) or --offline is set.
    Omitting the path with network access allowed is the one documented
    exception — covered separately by the managed-provisioning tests below."""
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
    assert calls[0].executable_path == Path("/usr/local/bin/llama-completion").resolve()


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


# --- Managed runtime provisioning ------------------------------------------


def _fake_install(tmp_path: Path) -> ManagedRuntimeInstall:
    install_dir = tmp_path / "runtime" / "llama.cpp-b10199-aaaaaaaaaaaaaaaa"
    install_dir.mkdir(parents=True)
    executable_path = install_dir / "llama-completion"
    executable_path.write_bytes(b"fake")
    receipt = InstallReceipt(
        schema_version=1,
        runtime_id="llama.cpp-b10199",
        version_marker="10199",
        platform=SupportedPlatform.LINUX,
        architecture=SupportedArchitecture.X86_64,
        source_asset_identity="https://example.com/runtime.tar.gz",
        archive_size_bytes=1024,
        archive_sha256="a" * 64,
        executable_relative_path=PurePosixPath("llama-completion"),
        executable_sha256="b" * 64,
        member_checksums=((PurePosixPath("llama-completion"), "b" * 64),),
    )
    return ManagedRuntimeInstall(
        executable_path=executable_path,
        runtime_id="llama.cpp-b10199",
        version_marker="10199",
        install_dir=install_dir,
        receipt=receipt,
    )


def test_omitted_executable_path_triggers_managed_provisioning(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    calls: list[bool] = []

    def provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        calls.append(allow_download)
        return install

    backend = JSONConfigStorage(tmp_path / "config.json")
    exit_code = run_setup_command(
        _options(executable_path=None),
        config_backend=backend,
        readiness_checker=_ok_checker,
        runtime_provisioner=provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == CLIExitCode.SUCCESS
    assert calls == [True]
    loaded = backend.load()
    assert loaded is not None
    assert loaded.executable_path == install.executable_path.resolve()


def test_explicit_executable_path_never_calls_provisioner(tmp_path: Path) -> None:
    def boom_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        raise AssertionError("provisioner must not be called with an explicit path")

    backend = JSONConfigStorage(tmp_path / "config.json")
    exit_code = run_setup_command(
        _options(),  # supplies an explicit executable_path
        config_backend=backend,
        readiness_checker=_ok_checker,
        runtime_provisioner=boom_provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert exit_code == CLIExitCode.SUCCESS


def test_offline_flag_is_threaded_through_to_provisioner(tmp_path: Path) -> None:
    calls: list[bool] = []

    def provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        calls.append(allow_download)
        return _fake_install(tmp_path)

    run_setup_command(
        _options(executable_path=None, offline=True),
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        readiness_checker=_ok_checker,
        runtime_provisioner=provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert calls == [False]


def test_offline_with_no_managed_runtime_is_typed_failure_writes_nothing(
    tmp_path: Path,
) -> None:
    def failing_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        raise OfflineProvisioningError("no verified managed runtime is installed")

    config_path = tmp_path / "config.json"
    err = io.StringIO()
    exit_code = run_setup_command(
        _options(executable_path=None, offline=True),
        config_backend=JSONConfigStorage(config_path),
        readiness_checker=_ok_checker,
        runtime_provisioner=failing_provisioner,
        stdout=io.StringIO(),
        stderr=err,
    )
    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert not config_path.exists()
    assert "Managed runtime provisioning failed" in err.getvalue()


def test_unsupported_platform_is_typed_failure_writes_nothing(tmp_path: Path) -> None:
    def failing_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        raise UnsupportedPlatformError("no pinned artifact for this platform")

    config_path = tmp_path / "config.json"
    err = io.StringIO()
    exit_code = run_setup_command(
        _options(executable_path=None),
        config_backend=JSONConfigStorage(config_path),
        readiness_checker=_ok_checker,
        runtime_provisioner=failing_provisioner,
        stdout=io.StringIO(),
        stderr=err,
    )
    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert not config_path.exists()


def test_managed_runtime_identity_is_threaded_into_readiness_check(
    tmp_path: Path,
) -> None:
    install = _fake_install(tmp_path)
    seen: list[LlamaCppConfig] = []

    def recording_checker(config: LlamaCppConfig) -> None:
        seen.append(config)

    run_setup_command(
        _options(executable_path=None),
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        readiness_checker=recording_checker,
        runtime_provisioner=lambda allow_download: install,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert len(seen) == 1
    assert seen[0].runtime_id == "llama.cpp-b10199"
    assert seen[0].runtime_version_marker == "10199"
    assert seen[0].executable_path == install.executable_path


def test_provisioning_failure_after_config_never_written(tmp_path: Path) -> None:
    """Config-save failure isn't reachable if provisioning itself fails first
    (provisioning happens before config validation/save), so a failed
    provision attempt can never leave a partially-written config."""

    def failing_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        raise UnsupportedPlatformError("simulated failure")

    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)
    # Seed a prior valid configuration.
    run_setup_command(
        _options(),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    original_bytes = config_path.read_bytes()

    exit_code = run_setup_command(
        _options(executable_path=None),
        config_backend=backend,
        readiness_checker=_ok_checker,
        runtime_provisioner=failing_provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert config_path.read_bytes() == original_bytes


def test_real_provisioning_checksum_mismatch_is_typed_failure_and_preserves_config(
    tmp_path: Path,
) -> None:
    """Issue #58 review: a real archive checksum mismatch inside the actual
    ensure_managed_runtime service (not a fake provisioner bypassing it)
    must surface as the documented typed exit code 2, not an unhandled
    VerificationError, and must never touch a prior valid configuration."""
    from econ_paper_cli.domain.runtime_manifest import (
        ArchiveFormat,
        ManagedRuntimeArtifact,
        ManagedRuntimeManifest,
        SupportedArchitecture,
        SupportedPlatform,
    )
    from econ_paper_cli.services.platform_detection import DetectedPlatform
    from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

    archive_bytes = b"not the real archive content"
    artifact = ManagedRuntimeArtifact(
        runtime_id="llama.cpp-test",
        version_marker="999",
        platform=SupportedPlatform.LINUX,
        architecture=SupportedArchitecture.X86_64,
        source_url="https://example.com/tool.tar.gz",
        archive_format=ArchiveFormat.TAR_GZ,
        archive_size_bytes=len(archive_bytes),
        archive_sha256="0" * 64,  # deliberately wrong
        executable_relative_path=PurePosixPath("pkg/tool"),
        bundle_member_checksums=((PurePosixPath("pkg/tool"), "1" * 64),),
        license_name="MIT",
        attribution_text="test",
    )
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(artifact,))

    class FakeDownloader:
        def download(
            self, url: str, destination: Path, *, expected_size_bytes: int
        ) -> None:
            destination.write_bytes(archive_bytes)

    class UnusedExtractor:
        def extract(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("extraction must not be reached")

    def real_provisioner(allow_download: bool) -> ManagedRuntimeInstall:
        return ensure_managed_runtime(
            runtime_dir=tmp_path / "runtime",
            downloader=FakeDownloader(),
            extractor=UnusedExtractor(),
            manifest=manifest,
            detected=DetectedPlatform(
                platform=SupportedPlatform.LINUX,
                architecture=SupportedArchitecture.X86_64,
                raw_system="Linux",
                raw_machine="x86_64",
            ),
            allow_download=allow_download,
        )

    config_path = tmp_path / "real_provisioning_config.json"
    backend = JSONConfigStorage(config_path)
    run_setup_command(
        _options(),
        config_backend=backend,
        readiness_checker=_ok_checker,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    original_bytes = config_path.read_bytes()

    err = io.StringIO()
    exit_code = run_setup_command(
        _options(executable_path=None),
        config_backend=backend,
        readiness_checker=_ok_checker,
        runtime_provisioner=real_provisioner,
        stdout=io.StringIO(),
        stderr=err,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert config_path.read_bytes() == original_bytes
    assert "Managed runtime provisioning failed" in err.getvalue()


# --- Managed model provisioning ---------------------------------------------


def _model_install(tmp_path: Path, *, downloaded: bool = True) -> ManagedModelInstall:
    return ManagedModelInstall(
        model_path=tmp_path / "provisioned.gguf",
        model_id="provisioned-model",
        size_bytes=4096,
        sha256="d" * 64,
        display_name="Provisioned Model",
        downloaded=downloaded,
    )


def test_setup_without_model_flags_provisions_and_persists_that_identity(
    tmp_path: Path,
) -> None:
    """A fresh user supplies no model flags at all. The provisioned artifact's
    path, id, size, and checksum must become the durable configuration, or
    later commands would have nothing to verify the model against."""
    backend = JSONConfigStorage(tmp_path / "config.json")
    calls: list[tuple[str | None, bool]] = []

    def provisioner(model_id: str | None, allow_download: bool):
        calls.append((model_id, allow_download))
        return _model_install(tmp_path)

    exit_code = run_setup_command(
        _options(model_path=None, model_id=None, model_bytes=None, model_checksum=None),
        config_backend=backend,
        readiness_checker=_ok_checker,
        model_provisioner=provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert calls == [(None, True)]
    stored = backend.load()
    assert stored.model_path == (tmp_path / "provisioned.gguf").resolve()
    assert stored.model_id == "provisioned-model"
    assert stored.model_bytes == 4096
    assert stored.model_checksum == "d" * 64


def test_explicit_model_path_never_triggers_model_provisioning(
    tmp_path: Path,
) -> None:
    """Supplying the model identity must bypass provisioning entirely, exactly
    as an explicit executable path bypasses runtime provisioning -- a user who
    named their own GGUF must never see a download."""
    backend = JSONConfigStorage(tmp_path / "config.json")

    def exploding(model_id: str | None, allow_download: bool):
        raise AssertionError("Model provisioning must not run.")

    exit_code = run_setup_command(
        _options(),
        config_backend=backend,
        readiness_checker=_ok_checker,
        model_provisioner=exploding,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert backend.load().model_id == "qwen2.5-1.5b-instruct-q4-k-m"


def test_offline_setup_forwards_the_download_refusal_to_model_provisioning(
    tmp_path: Path,
) -> None:
    """--offline must suppress the model download too, not only the runtime."""
    calls: list[tuple[str | None, bool]] = []

    def provisioner(model_id: str | None, allow_download: bool):
        calls.append((model_id, allow_download))
        return _model_install(tmp_path, downloaded=False)

    run_setup_command(
        _options(
            model_path=None,
            model_id=None,
            model_bytes=None,
            model_checksum=None,
            offline=True,
        ),
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        readiness_checker=_ok_checker,
        model_provisioner=provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert calls == [(None, False)]


def test_a_requested_catalog_model_is_forwarded_to_provisioning(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str | None, bool]] = []

    def provisioner(model_id: str | None, allow_download: bool):
        calls.append((model_id, allow_download))
        return _model_install(tmp_path)

    run_setup_command(
        _options(
            model_path=None,
            model_id=None,
            model_bytes=None,
            model_checksum=None,
            managed_model_id="qwen2.5-7b-instruct-q4-k-m",
        ),
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        readiness_checker=_ok_checker,
        model_provisioner=provisioner,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert calls == [("qwen2.5-7b-instruct-q4-k-m", True)]


def test_failed_model_provisioning_writes_nothing_and_exits_typed(
    tmp_path: Path,
) -> None:
    """Provisioning failure must leave prior durable configuration untouched,
    matching the runtime-provisioning failure contract."""
    config_path = tmp_path / "config.json"
    backend = JSONConfigStorage(config_path)
    err = io.StringIO()

    def failing(model_id: str | None, allow_download: bool):
        raise OfflineModelProvisioningError("no model and downloads disabled")

    exit_code = run_setup_command(
        _options(model_path=None, model_id=None, model_bytes=None, model_checksum=None),
        config_backend=backend,
        readiness_checker=_ok_checker,
        model_provisioner=failing,
        stdout=io.StringIO(),
        stderr=err,
    )

    assert exit_code == 2
    assert "Managed model provisioning failed" in err.getvalue()
    assert not config_path.exists()


def test_provisioned_setup_reports_the_model_name_and_whether_it_downloaded(
    tmp_path: Path,
) -> None:
    """A first run downloads gigabytes; the user must be told what was fetched
    and, on later runs, that nothing was re-downloaded."""
    out = io.StringIO()
    run_setup_command(
        _options(model_path=None, model_id=None, model_bytes=None, model_checksum=None),
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        readiness_checker=_ok_checker,
        model_provisioner=lambda _id, _allow: _model_install(
            tmp_path, downloaded=False
        ),
        stdout=out,
        stderr=io.StringIO(),
    )

    rendered = out.getvalue()
    assert "Model Name: Provisioned Model" in rendered
    assert "Model Source: reused existing install" in rendered
