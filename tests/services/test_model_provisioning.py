"""Managed GGUF model provisioning: reuse, verification, and atomic install."""

import hashlib
from pathlib import Path

import pytest

from econ_paper_cli.domain.model_manifest import (
    MANAGED_MODEL_CATALOG,
    ManagedModelArtifact,
    ManagedModelCatalog,
    ManagedModelManifestError,
)
from econ_paper_cli.services.model_provisioning import (
    ModelInstallIOError,
    OfflineModelProvisioningError,
    StagedModelVerificationError,
    ensure_managed_model,
)

_PAYLOAD = b"synthetic gguf payload"
_SHA256 = hashlib.sha256(_PAYLOAD).hexdigest()

ARTIFACT = ManagedModelArtifact(
    model_id="synthetic-small",
    display_name="Synthetic Small",
    source_url="https://example.invalid/synthetic-small.gguf",
    size_bytes=len(_PAYLOAD),
    sha256=_SHA256,
    filename="synthetic-small.gguf",
    license_name="Apache-2.0",
    attribution_text="Synthetic test artifact.",
    summary="A synthetic model used only by tests.",
    minimum_free_ram_bytes=1024,
)
OTHER = ManagedModelArtifact(
    model_id="synthetic-large",
    display_name="Synthetic Large",
    source_url="https://example.invalid/synthetic-large.gguf",
    size_bytes=len(_PAYLOAD),
    sha256=_SHA256,
    filename="synthetic-large.gguf",
    license_name="Apache-2.0",
    attribution_text="Synthetic test artifact.",
    summary="A second synthetic model used only by tests.",
    minimum_free_ram_bytes=2048,
)
CATALOG = ManagedModelCatalog(
    artifacts=(ARTIFACT, OTHER), default_model_id=ARTIFACT.model_id
)


class RecordingDownloader:
    """Write configured bytes and record every requested URL."""

    def __init__(self, payload: bytes = _PAYLOAD) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        self.urls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)


class ExplodingDownloader:
    """Fail the test if a download is attempted at all."""

    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        raise AssertionError(f"Unexpected download attempt for {url}.")


def test_first_run_downloads_verifies_and_installs_the_default_model(
    tmp_path: Path,
) -> None:
    downloader = RecordingDownloader()

    install = ensure_managed_model(
        model_dir=tmp_path, downloader=downloader, catalog=CATALOG
    )

    assert downloader.urls == [ARTIFACT.source_url]
    assert install.model_id == ARTIFACT.model_id
    assert install.downloaded is True
    assert install.model_path == (tmp_path / ARTIFACT.filename).resolve()
    assert install.model_path.read_bytes() == _PAYLOAD
    # Staging must leave nothing behind next to the installed model.
    assert [item.name for item in tmp_path.iterdir()] == [ARTIFACT.filename]


def test_a_verified_existing_model_is_reused_without_any_download(
    tmp_path: Path,
) -> None:
    """Re-running setup must not re-download gigabytes that are already on
    disk and already match their pinned checksum."""
    (tmp_path / ARTIFACT.filename).write_bytes(_PAYLOAD)

    install = ensure_managed_model(
        model_dir=tmp_path, downloader=ExplodingDownloader(), catalog=CATALOG
    )

    assert install.downloaded is False
    assert install.model_id == ARTIFACT.model_id


def test_a_corrupt_existing_model_is_replaced_rather_than_trusted(
    tmp_path: Path,
) -> None:
    """A file of the right name but wrong content must never be accepted:
    that is the case where a truncated earlier download would otherwise be
    used as if it were a verified model."""
    (tmp_path / ARTIFACT.filename).write_bytes(b"corrupt")
    downloader = RecordingDownloader()

    install = ensure_managed_model(
        model_dir=tmp_path, downloader=downloader, catalog=CATALOG
    )

    assert downloader.urls == [ARTIFACT.source_url]
    assert install.model_path.read_bytes() == _PAYLOAD


def test_a_download_failing_verification_is_discarded_not_promoted(
    tmp_path: Path,
) -> None:
    """The staged file is verified before promotion, so a bad download must
    leave no installed model at all."""
    downloader = RecordingDownloader(payload=b"wrong content entirely")

    with pytest.raises(StagedModelVerificationError, match="did not match"):
        ensure_managed_model(model_dir=tmp_path, downloader=downloader, catalog=CATALOG)

    assert not (tmp_path / ARTIFACT.filename).exists()
    assert list(tmp_path.iterdir()) == []


def test_a_corrupt_existing_model_is_kept_when_offline_refuses_the_repair(
    tmp_path: Path,
) -> None:
    """Offline must fail loudly rather than deleting the user's only copy: a
    corrupt file they could still repair beats no file at all."""
    corrupt_path = tmp_path / ARTIFACT.filename
    corrupt_path.write_bytes(b"corrupt")

    with pytest.raises(OfflineModelProvisioningError, match="failed integrity"):
        ensure_managed_model(
            model_dir=tmp_path,
            downloader=ExplodingDownloader(),
            catalog=CATALOG,
            allow_download=False,
        )

    assert corrupt_path.read_bytes() == b"corrupt"


def test_offline_with_no_installed_model_fails_without_touching_the_network(
    tmp_path: Path,
) -> None:
    with pytest.raises(OfflineModelProvisioningError, match="downloads are disabled"):
        ensure_managed_model(
            model_dir=tmp_path,
            downloader=ExplodingDownloader(),
            catalog=CATALOG,
            allow_download=False,
        )


def test_an_explicit_model_choice_is_honored_rather_than_defaulted(
    tmp_path: Path,
) -> None:
    downloader = RecordingDownloader()

    install = ensure_managed_model(
        model_dir=tmp_path,
        downloader=downloader,
        model_id=OTHER.model_id,
        catalog=CATALOG,
    )

    assert downloader.urls == [OTHER.source_url]
    assert install.model_id == OTHER.model_id


def test_an_unknown_model_choice_raises_instead_of_silently_defaulting(
    tmp_path: Path,
) -> None:
    """Substituting the default for an unrecognized id would leave the user
    running a model they did not ask for, with no signal."""
    with pytest.raises(ManagedModelManifestError, match="Unknown model id"):
        ensure_managed_model(
            model_dir=tmp_path,
            downloader=ExplodingDownloader(),
            model_id="no-such-model",
            catalog=CATALOG,
        )


def test_an_unwritable_model_directory_raises_a_typed_error(tmp_path: Path) -> None:
    blocking_file = tmp_path / "blocked"
    blocking_file.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ModelInstallIOError, match="Failed to prepare"):
        ensure_managed_model(
            model_dir=blocking_file / "models",
            downloader=RecordingDownloader(),
            catalog=CATALOG,
        )


# --- Shipped catalog ---------------------------------------------------------


def test_shipped_catalog_defaults_to_the_smallest_model() -> None:
    """The product guarantees modest hardware, so the first-run download must
    be the small model; a multi-gigabyte default would contradict that."""
    default = MANAGED_MODEL_CATALOG.default
    smallest = min(MANAGED_MODEL_CATALOG.artifacts, key=lambda item: item.size_bytes)

    assert default.model_id == smallest.model_id


def test_shipped_catalog_pins_https_sources_and_full_checksums() -> None:
    """Every pin is an integrity anchor for an unattended download."""
    for artifact in MANAGED_MODEL_CATALOG.artifacts:
        assert artifact.source_url.startswith("https://")
        assert len(artifact.sha256) == 64
        assert artifact.size_bytes > 0
        assert artifact.license_name
        assert artifact.attribution_text


@pytest.mark.parametrize(
    "filename",
    ("../escape.gguf", "/absolute.gguf", "nested/path.gguf", "no-extension"),
)
def test_manifest_rejects_a_filename_that_could_escape_the_install_dir(
    filename: str,
) -> None:
    """The manifest-controlled filename becomes a path segment on disk."""
    with pytest.raises(ManagedModelManifestError, match="filename"):
        ManagedModelArtifact(
            model_id="synthetic",
            display_name="Synthetic",
            source_url="https://example.invalid/x.gguf",
            size_bytes=1,
            sha256="a" * 64,
            filename=filename,
            license_name="Apache-2.0",
            attribution_text="Synthetic.",
            summary="Synthetic.",
            minimum_free_ram_bytes=1,
        )


def test_manifest_rejects_a_non_https_source_url() -> None:
    with pytest.raises(ManagedModelManifestError, match="https"):
        ManagedModelArtifact(
            model_id="synthetic",
            display_name="Synthetic",
            source_url="http://example.invalid/x.gguf",
            size_bytes=1,
            sha256="a" * 64,
            filename="x.gguf",
            license_name="Apache-2.0",
            attribution_text="Synthetic.",
            summary="Synthetic.",
            minimum_free_ram_bytes=1,
        )


def test_catalog_rejects_a_default_id_that_is_not_in_the_catalog() -> None:
    with pytest.raises(ManagedModelManifestError, match="not in the catalog"):
        ManagedModelCatalog(artifacts=(ARTIFACT,), default_model_id="absent")


class FailingDownloader:
    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        raise OSError("simulated network failure during download")


def test_failed_repair_preserves_corrupt_file_on_disk(tmp_path: Path) -> None:
    """Regression test for finding 3 (step 0a): if repair of an existing corrupt model
    fails during replacement download, the original corrupt file is preserved on disk
    (never deleted before replacement verifies) so it is still present and fails verification."""
    corrupt_path = tmp_path / ARTIFACT.filename
    corrupt_path.write_bytes(b"corrupt")

    with pytest.raises(OSError, match="simulated network failure"):
        ensure_managed_model(
            model_dir=tmp_path,
            downloader=FailingDownloader(),
            catalog=CATALOG,
            allow_download=True,
        )

    assert corrupt_path.exists()
    assert corrupt_path.read_bytes() == b"corrupt"


def test_locate_managed_model_artifact_filename_containment(tmp_path: Path) -> None:
    from econ_paper_cli.services.model_provisioning import locate_managed_model_artifact

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    valid_path = model_dir / ARTIFACT.filename
    valid_path.touch()

    artifact = locate_managed_model_artifact(valid_path, model_dir, catalog=CATALOG)
    assert artifact == ARTIFACT

    outside_path = tmp_path / ARTIFACT.filename
    assert (
        locate_managed_model_artifact(outside_path, model_dir, catalog=CATALOG) is None
    )

    unknown_path = model_dir / "unknown.gguf"
    assert (
        locate_managed_model_artifact(unknown_path, model_dir, catalog=CATALOG) is None
    )


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="Symlinks not supported on this platform"
)
def test_locate_managed_model_artifact_symlink_containment(tmp_path: Path) -> None:
    from econ_paper_cli.services.model_provisioning import locate_managed_model_artifact

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    target = tmp_path / "target.gguf"
    target.touch()
    symlink_path = model_dir / ARTIFACT.filename
    try:
        symlink_path.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation failed")

    # Lexical containment under model_dir still matches ARTIFACT.filename
    artifact = locate_managed_model_artifact(symlink_path, model_dir, catalog=CATALOG)
    assert artifact == ARTIFACT
