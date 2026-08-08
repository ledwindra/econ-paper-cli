"""Provision a pinned GGUF model into an application-managed directory.

Mirrors ``services.runtime_provisioning`` and keeps its two load-bearing
guarantees:

- Verify a *staged* download in full before promoting it, so a truncated or
  tampered file never becomes the installed model.
- Promote only via ``os.replace`` onto a content-addressed final path, so a
  concurrent ``setup`` cannot observe a half-written model.

It is much smaller than the runtime equivalent because a GGUF is a single
file: no archive extraction, no per-member safety checks, and no
platform/architecture selection.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.domain.model_manifest import (
    MANAGED_MODEL_CATALOG,
    ManagedModelArtifact,
    ManagedModelCatalog,
)
from econ_paper_cli.protocols.runtime_provisioning import Downloader

_HASH_CHUNK_BYTES = 1024 * 1024


class ModelProvisioningError(Exception):
    """Base exception for managed-model provisioning failures."""


class OfflineModelProvisioningError(ModelProvisioningError):
    """Raised when a download is required but downloads are disabled."""


class StagedModelVerificationError(ModelProvisioningError):
    """Raised when a freshly downloaded model fails size/checksum verification."""


class ModelInstallIOError(ModelProvisioningError):
    """Raised when the managed model directory cannot be prepared or written."""


@dataclass(frozen=True, slots=True)
class ManagedModelInstall:
    """The verified on-disk result of provisioning one pinned model."""

    model_path: Path
    model_id: str
    size_bytes: int
    sha256: str
    display_name: str
    downloaded: bool


def ensure_managed_model(
    *,
    model_dir: Path,
    downloader: Downloader,
    model_id: str | None = None,
    catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
    allow_download: bool = True,
) -> ManagedModelInstall:
    """Reuse a verified managed model, or download, verify, and install one.

    ``model_id`` of ``None`` selects the catalog default. An unknown id raises
    rather than falling back, so a user's explicit choice is never silently
    replaced. Raises ``OfflineModelProvisioningError`` when a download would be
    required but ``allow_download`` is False, and
    ``StagedModelVerificationError`` when a downloaded file fails verification
    (it is never promoted in that case). ``DownloadError`` subtypes propagate
    directly from the injected downloader.
    """
    artifact = catalog.default if model_id is None else catalog.get(model_id)
    final_path = model_dir / artifact.filename

    if final_path.exists():
        if _verify_local_model(final_path, artifact):
            return _install_result(final_path, artifact, downloaded=False)
        if not allow_download:
            raise OfflineModelProvisioningError(
                f"The installed model at '{final_path}' failed integrity "
                "verification and downloads are disabled. Re-run setup with "
                "network access, or supply an explicit --model-path."
            )

    if not allow_download:
        raise OfflineModelProvisioningError(
            f"No verified managed model is installed at '{final_path}' and "
            "downloads are disabled. Re-run setup with network access, or "
            "supply an explicit --model-path."
        )

    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        staging_context = tempfile.TemporaryDirectory(
            prefix=".staging-", dir=str(model_dir)
        )
    except OSError as error:
        raise ModelInstallIOError(
            f"Failed to prepare the model directory '{model_dir}': {error}."
        ) from error

    with staging_context as staging_name:
        staged_path = Path(staging_name) / artifact.filename
        downloader.download(
            artifact.source_url,
            staged_path,
            expected_size_bytes=artifact.size_bytes,
        )
        if not _verify_local_model(staged_path, artifact):
            raise StagedModelVerificationError(
                f"The downloaded model for '{artifact.model_id}' did not match "
                "its pinned size and checksum; it was discarded."
            )
        try:
            os.replace(staged_path, final_path)
        except OSError as error:
            raise ModelInstallIOError(
                f"Failed to install the model into '{final_path}': {error}."
            ) from error

    return _install_result(final_path, artifact, downloaded=True)


def verify_managed_model(model_path: Path, artifact: ManagedModelArtifact) -> bool:
    """Report whether ``model_path`` matches the artifact's pinned identity."""
    return _verify_local_model(model_path, artifact)


def compute_sha256(path: Path) -> str:
    """Return the SHA-256 of ``path``, read incrementally.

    Chunked rather than read-whole-file: a managed model runs to several
    gigabytes, and buffering one in memory to hash it would fail on exactly
    the modest hardware this project promises to support.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_local_model(path: Path, artifact: ManagedModelArtifact) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size != artifact.size_bytes:
            # Cheap reject before hashing gigabytes: a truncated or replaced
            # file almost always differs in size.
            return False
        return compute_sha256(path) == artifact.sha256
    except OSError:
        return False


def _install_result(
    model_path: Path, artifact: ManagedModelArtifact, *, downloaded: bool
) -> ManagedModelInstall:
    return ManagedModelInstall(
        model_path=model_path.resolve(),
        model_id=artifact.model_id,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        display_name=artifact.display_name,
        downloaded=downloaded,
    )


def locate_managed_model_artifact(
    model_path: Path,
    model_dir: Path,
    catalog: ManagedModelCatalog = MANAGED_MODEL_CATALOG,
) -> ManagedModelArtifact | None:
    """Return the catalog artifact ``model_path`` is lexically a managed
    install location for, if any -- filename/containment only, mirroring
    ``locate_managed_install_root``. Containment and ``model_id`` agreement
    are necessary but *not sufficient* to classify a model as managed --
    callers must additionally require ``config.managed_model_provisioning
    is True``, the only durable signal that actually distinguishes a
    managed install from an explicit one with coincidentally identical
    identity (see step 3's design note).
    """
    from econ_paper_cli.services.runtime_provisioning import _containment_candidates

    candidate_paths = _containment_candidates(model_path)
    candidate_dirs = _containment_candidates(model_dir)

    for candidate_dir in candidate_dirs:
        for candidate_path in candidate_paths:
            try:
                relative = candidate_path.relative_to(candidate_dir)
            except ValueError:
                continue
            if len(relative.parts) == 1:
                filename = relative.parts[0]
                for artifact in catalog.artifacts:
                    if artifact.filename == filename:
                        return artifact
    return None
