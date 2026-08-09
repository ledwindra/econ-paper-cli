"""Deterministic library-record builder shared by M5 release-readiness tests.

Not named ``test_*``, so pytest does not collect it. It lives in its own
module rather than inside a test file because the concurrency scenario needs
a *separate process* to import the same builder: the writer runs as a real
subprocess against the same SQLite database, and it must produce records
indistinguishable from the ones the in-process reader stored.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
    EarlySectionLibraryRecord,
    ExtractedPDFPage,
    PDFConversionSettings,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSection,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
)
from econ_paper_cli.domain.artifacts import (
    PINNED_UPDATE_POLICY,
    RedistributionStatus,
)
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
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.model_provisioning import ensure_managed_model
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections
from econ_paper_cli.services.platform_detection import DetectedPlatform
from econ_paper_cli.services.runtime_provisioning import ensure_managed_runtime

ABSTRACT = "Abstract trade policy evidence."
INTRODUCTION = "Introduction trade policy evidence."


def library_record(
    base_dir: Path,
    *,
    title: str = "Trade Policy Paper",
    checksum: str = "c" * 64,
    source_filename: str = "paper.pdf",
    abstract_text: str = ABSTRACT,
    introduction_text: str = INTRODUCTION,
    timestamp: str = "2026-08-01T12:00:00+00:00",
) -> EarlySectionLibraryRecord:
    """Build one fully-projected early-section record from synthetic text."""
    text = f"{abstract_text}\n\n{introduction_text}"
    extraction = PDFExtractionResult(
        source_path=(base_dir / source_filename).resolve(),
        pages=(ExtractedPDFPage(1, text),),
        page_count=1,
        metadata=PDFDocumentMetadata(title=title, author_text="Ada Economist"),
        extraction_method="synthetic",
        parser_version="1.0",
    )
    detection = PDFSectionDetectionResult(
        policy_version=DEFAULT_PDF_CONVERSION_SETTINGS.section_policy_version,
        sections=(
            PDFSection(
                kind=PDFSectionKind.ABSTRACT,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Abstract",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 0, len(abstract_text)),),
                text=abstract_text,
            ),
            PDFSection(
                kind=PDFSectionKind.INTRODUCTION,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Introduction",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, len(abstract_text) + 2, len(text)),),
                text=introduction_text,
            ),
        ),
        candidates=(),
        warnings=(),
    )
    conversion = convert_pdf_early_sections(
        extraction,
        detection,
        content_checksum=checksum,
        settings=PDFConversionSettings(max_passage_characters=1200),
    )
    return project_early_section_library_record(
        extraction,
        detection,
        conversion,
        source_file_size=1024,
        timestamp=timestamp,
    )


# --- Managed runtime/model scaffolding ---------------------------------------
#
# Used by the M5 "update while a shell session is open" scenario. Synthetic
# throughout: a few hundred bytes whose real size and SHA-256 the fixture
# computes into a synthetic manifest, so nothing here resembles the multi-GB
# pinned artifacts and no download is ever attempted.

_MODEL_PAYLOAD = b"synthetic gguf payload for release-readiness tests"
_EXE_BYTES = b"#!/bin/sh\necho llama.cpp synthetic-marker-1.0\n"
_LIB_BYTES = b"synthetic runtime support library"

DETECTED_PLATFORM = DetectedPlatform(
    platform=SupportedPlatform.MACOS,
    architecture=SupportedArchitecture.ARM64,
    raw_system="Darwin",
    raw_machine="arm64",
)

MODEL_ARTIFACT = ManagedModelArtifact(
    model_id="synthetic-release-model",
    display_name="Synthetic Release Model",
    source_url="https://example.invalid/model.gguf",
    size_bytes=len(_MODEL_PAYLOAD),
    sha256=hashlib.sha256(_MODEL_PAYLOAD).hexdigest(),
    filename="synthetic-model.gguf",
    license_name="Apache-2.0",
    attribution_text="Test artifact.",
    summary="Synthetic test model.",
    minimum_free_ram_bytes=1024,
    redistribution_status=RedistributionStatus.PERMITTED,
    update_policy=PINNED_UPDATE_POLICY,
    contains_copyrighted_full_text=False,
)
MODEL_CATALOG = ManagedModelCatalog(
    artifacts=(MODEL_ARTIFACT,), default_model_id=MODEL_ARTIFACT.model_id
)


class SyntheticDownloader:
    """Serve synthetic model/runtime bytes and count calls."""

    def __init__(self, runtime_archive: bytes) -> None:
        self.runtime_archive = runtime_archive
        self.download_count = 0

    def download(
        self, url: str, destination: Path, *, expected_size_bytes: int
    ) -> None:
        self.download_count += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url == MODEL_ARTIFACT.source_url:
            destination.write_bytes(_MODEL_PAYLOAD)
        else:
            destination.write_bytes(self.runtime_archive)


class SyntheticExtractor:
    def extract(
        self, archive_path: Path, archive_format: object, destination_dir: Path
    ) -> None:
        exe = destination_dir / "bin" / "llama-completion"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(_EXE_BYTES)
        lib = destination_dir / "lib" / "libruntime.dylib"
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_bytes(_LIB_BYTES)


def noop_readiness_checker(executable_path: Path, version_marker: str) -> None:
    """Accept any executable; the real check runs a subprocess."""


def _runtime_manifest(archive_sha: str, archive_size: int) -> ManagedRuntimeManifest:
    return ManagedRuntimeManifest(
        schema_version=1,
        artifacts=(
            ManagedRuntimeArtifact(
                runtime_id="synthetic-llama-cpp",
                version_marker="synthetic-marker-1.0",
                platform=SupportedPlatform.MACOS,
                architecture=SupportedArchitecture.ARM64,
                source_url="https://example.invalid/runtime.tar.gz",
                archive_format=ArchiveFormat.TAR_GZ,
                archive_size_bytes=archive_size,
                archive_sha256=archive_sha,
                executable_relative_path=PurePosixPath("bin/llama-completion"),
                bundle_member_checksums=(
                    (
                        PurePosixPath("bin/llama-completion"),
                        hashlib.sha256(_EXE_BYTES).hexdigest(),
                    ),
                    (
                        PurePosixPath("lib/libruntime.dylib"),
                        hashlib.sha256(_LIB_BYTES).hexdigest(),
                    ),
                ),
                license_name="MIT",
                attribution_text="Test runtime artifact.",
                redistribution_status=RedistributionStatus.PERMITTED,
                update_policy=PINNED_UPDATE_POLICY,
                contains_copyrighted_full_text=False,
            ),
        ),
    )


@dataclass(frozen=True)
class ManagedInstall:
    """A fully provisioned synthetic runtime + model and its durable config."""

    config: LocalRuntimeModelConfig
    runtime_dir: Path
    model_dir: Path
    executable_path: Path
    runtime_manifest: ManagedRuntimeManifest
    downloader: SyntheticDownloader
    extractor: SyntheticExtractor


def managed_install(base_dir: Path) -> ManagedInstall:
    """Provision a synthetic managed runtime and model under ``base_dir``."""
    archive_path = base_dir / "runtime_src.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        exe_info = tarfile.TarInfo("bin/llama-completion")
        exe_info.size = len(_EXE_BYTES)
        archive.addfile(exe_info, io.BytesIO(_EXE_BYTES))
        lib_info = tarfile.TarInfo("lib/libruntime.dylib")
        lib_info.size = len(_LIB_BYTES)
        archive.addfile(lib_info, io.BytesIO(_LIB_BYTES))
    raw = archive_path.read_bytes()
    manifest = _runtime_manifest(hashlib.sha256(raw).hexdigest(), len(raw))

    runtime_dir = base_dir / "runtimes"
    model_dir = base_dir / "models"
    downloader = SyntheticDownloader(raw)
    extractor = SyntheticExtractor()

    runtime = ensure_managed_runtime(
        runtime_dir=runtime_dir,
        downloader=downloader,
        extractor=extractor,
        manifest=manifest,
        detected=DETECTED_PLATFORM,
        executable_readiness_checker=noop_readiness_checker,
    )
    model = ensure_managed_model(
        model_dir=model_dir, downloader=downloader, catalog=MODEL_CATALOG
    )

    return ManagedInstall(
        config=LocalRuntimeModelConfig(
            executable_path=runtime.executable_path,
            model_path=model.model_path,
            model_id=MODEL_ARTIFACT.model_id,
            model_bytes=MODEL_ARTIFACT.size_bytes,
            model_checksum=MODEL_ARTIFACT.sha256,
            runtime_id=runtime.runtime_id,
            runtime_version_marker=runtime.version_marker,
            managed_model_provisioning=True,
        ),
        runtime_dir=runtime_dir,
        model_dir=model_dir,
        executable_path=runtime.executable_path,
        runtime_manifest=manifest,
        downloader=downloader,
        extractor=extractor,
    )
