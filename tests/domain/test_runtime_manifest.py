"""Domain tests for the version-controlled managed runtime manifest."""

from pathlib import PurePosixPath

import pytest

from econ_paper_cli.domain import (
    MANAGED_RUNTIME_MANIFEST,
    ArchiveFormat,
    ManagedRuntimeArtifact,
    ManagedRuntimeManifest,
    ManagedRuntimeManifestError,
    SupportedArchitecture,
    SupportedPlatform,
    select_artifact_for_platform,
)

VALID_SHA256 = "a" * 64


def _artifact(**overrides: object) -> ManagedRuntimeArtifact:
    base: dict[str, object] = {
        "runtime_id": "llama.cpp-b10199",
        "version_marker": "10199",
        "platform": SupportedPlatform.LINUX,
        "architecture": SupportedArchitecture.X86_64,
        "source_url": "https://example.com/runtime.tar.gz",
        "archive_format": ArchiveFormat.TAR_GZ,
        "archive_size_bytes": 1024,
        "archive_sha256": VALID_SHA256,
        "executable_relative_path": PurePosixPath("bin/llama-completion"),
        "license_name": "MIT",
        "attribution_text": "Some attribution.",
    }
    base.update(overrides)
    return ManagedRuntimeArtifact(**base)


def test_valid_artifact_constructs() -> None:
    artifact = _artifact()
    assert artifact.runtime_id == "llama.cpp-b10199"


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_id", "Bad Id"),
        ("version_marker", ""),
        ("platform", "macos"),
        ("architecture", "arm64"),
        ("source_url", "http://example.com/x.tar.gz"),
        ("source_url", "not-a-url"),
        ("archive_format", "tar.gz"),
        ("archive_size_bytes", 0),
        ("archive_size_bytes", -1),
        ("archive_size_bytes", True),
        ("archive_sha256", "not-hex"),
        ("archive_sha256", "A" * 64),
        ("executable_relative_path", PurePosixPath("/abs/path")),
        ("executable_relative_path", PurePosixPath("../escape")),
        ("executable_relative_path", "bin/llama-completion"),
        ("license_name", ""),
        ("license_name", "   "),
        ("attribution_text", ""),
    ],
)
def test_invalid_field_rejected(field: str, value: object) -> None:
    with pytest.raises(ManagedRuntimeManifestError):
        _artifact(**{field: value})


def test_manifest_requires_schema_version_one() -> None:
    with pytest.raises(ManagedRuntimeManifestError):
        ManagedRuntimeManifest(schema_version=2, artifacts=(_artifact(),))


def test_manifest_requires_nonempty_artifacts() -> None:
    with pytest.raises(ManagedRuntimeManifestError):
        ManagedRuntimeManifest(schema_version=1, artifacts=())


def test_manifest_rejects_duplicate_platform_architecture() -> None:
    with pytest.raises(ManagedRuntimeManifestError):
        ManagedRuntimeManifest(schema_version=1, artifacts=(_artifact(), _artifact()))


def test_select_artifact_for_platform_finds_match() -> None:
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(_artifact(),))
    found = select_artifact_for_platform(
        manifest, SupportedPlatform.LINUX, SupportedArchitecture.X86_64
    )
    assert found is not None
    assert found.platform is SupportedPlatform.LINUX


def test_select_artifact_for_platform_returns_none_when_absent() -> None:
    manifest = ManagedRuntimeManifest(schema_version=1, artifacts=(_artifact(),))
    found = select_artifact_for_platform(
        manifest, SupportedPlatform.WINDOWS, SupportedArchitecture.ARM64
    )
    assert found is None


# --- Real, version-controlled manifest data --------------------------------


def test_managed_runtime_manifest_covers_approved_platform_matrix() -> None:
    """The approved matrix (issue #58 plan) is macOS arm64 *and* x86_64,
    Linux x86_64, and Windows x86_64 — not merely whatever the CI matrix
    happens to run, so an Intel Mac is covered too."""
    for platform, architecture in (
        (SupportedPlatform.MACOS, SupportedArchitecture.ARM64),
        (SupportedPlatform.MACOS, SupportedArchitecture.X86_64),
        (SupportedPlatform.LINUX, SupportedArchitecture.X86_64),
        (SupportedPlatform.WINDOWS, SupportedArchitecture.X86_64),
    ):
        artifact = select_artifact_for_platform(
            MANAGED_RUNTIME_MANIFEST, platform, architecture
        )
        assert artifact is not None
        assert artifact.runtime_id == "llama.cpp-b10199"
        assert artifact.version_marker == "10199"
        assert artifact.source_url.startswith("https://")
