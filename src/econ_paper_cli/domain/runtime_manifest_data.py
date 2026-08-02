"""Version-controlled pinned managed-runtime artifact data.

Kept as a plain Python module (not a JSON/data file) so it is always
included in built wheels/sdists with no separate packaging configuration —
see the issue #58 plan discussion on manifest representation.

Pins ``llama.cpp`` release ``b10199`` (the same release
``LlamaCppConfig``'s ``runtime_id``/``runtime_version_marker`` defaults
already assume) for macOS arm64, macOS x86_64, Linux x86_64, and Windows
x86_64 — the approved supported-platform matrix (macOS arm64 *and* x86_64,
per the posted issue #58 plan, so an Intel Mac is covered too, not just the
platforms this project's CI matrix happens to run). All entries are the
CPU-only release asset for that platform (no GPU/accelerator variant),
matching the "no GPU requirement" product guardrail. ``archive_size_bytes``/
``archive_sha256`` were computed directly from the real release assets
downloaded from the URLs below.
"""

from pathlib import PurePosixPath

from econ_paper_cli.domain.runtime_manifest import (
    ArchiveFormat,
    ManagedRuntimeArtifact,
    ManagedRuntimeManifest,
    SupportedArchitecture,
    SupportedPlatform,
)

_LLAMA_CPP_LICENSE = "MIT"
_LLAMA_CPP_ATTRIBUTION = (
    "llama.cpp (https://github.com/ggml-org/llama.cpp), "
    "Copyright (c) 2023-2026 The ggml authors. Licensed under the MIT License; "
    "see the LICENSE file bundled in the downloaded archive for the full text."
)

MANAGED_RUNTIME_MANIFEST = ManagedRuntimeManifest(
    schema_version=1,
    artifacts=(
        ManagedRuntimeArtifact(
            runtime_id="llama.cpp-b10199",
            version_marker="10199",
            platform=SupportedPlatform.MACOS,
            architecture=SupportedArchitecture.ARM64,
            source_url=(
                "https://github.com/ggml-org/llama.cpp/releases/download/"
                "b10199/llama-b10199-bin-macos-arm64.tar.gz"
            ),
            archive_format=ArchiveFormat.TAR_GZ,
            archive_size_bytes=10939809,
            archive_sha256=(
                "a7bc124584fbed7e848f7d95987a6c537399a7398682f45fa32b66852269ae6c"
            ),
            executable_relative_path=PurePosixPath("llama-b10199/llama-completion"),
            license_name=_LLAMA_CPP_LICENSE,
            attribution_text=_LLAMA_CPP_ATTRIBUTION,
        ),
        ManagedRuntimeArtifact(
            runtime_id="llama.cpp-b10199",
            version_marker="10199",
            platform=SupportedPlatform.MACOS,
            architecture=SupportedArchitecture.X86_64,
            source_url=(
                "https://github.com/ggml-org/llama.cpp/releases/download/"
                "b10199/llama-b10199-bin-macos-x64.tar.gz"
            ),
            archive_format=ArchiveFormat.TAR_GZ,
            archive_size_bytes=11216652,
            archive_sha256=(
                "df24f71388941f030cf4f0f716584f0c5fdeb4465ff67a036d37575d809b4799"
            ),
            executable_relative_path=PurePosixPath("llama-b10199/llama-completion"),
            license_name=_LLAMA_CPP_LICENSE,
            attribution_text=_LLAMA_CPP_ATTRIBUTION,
        ),
        ManagedRuntimeArtifact(
            runtime_id="llama.cpp-b10199",
            version_marker="10199",
            platform=SupportedPlatform.LINUX,
            architecture=SupportedArchitecture.X86_64,
            source_url=(
                "https://github.com/ggml-org/llama.cpp/releases/download/"
                "b10199/llama-b10199-bin-ubuntu-x64.tar.gz"
            ),
            archive_format=ArchiveFormat.TAR_GZ,
            archive_size_bytes=16434223,
            archive_sha256=(
                "16d63bfb5c7e1c1656d940de398456ed2972af16ab5a0961f88c5929bc4fe58a"
            ),
            executable_relative_path=PurePosixPath("llama-b10199/llama-completion"),
            license_name=_LLAMA_CPP_LICENSE,
            attribution_text=_LLAMA_CPP_ATTRIBUTION,
        ),
        ManagedRuntimeArtifact(
            runtime_id="llama.cpp-b10199",
            version_marker="10199",
            platform=SupportedPlatform.WINDOWS,
            architecture=SupportedArchitecture.X86_64,
            source_url=(
                "https://github.com/ggml-org/llama.cpp/releases/download/"
                "b10199/llama-b10199-bin-win-cpu-x64.zip"
            ),
            archive_format=ArchiveFormat.ZIP,
            archive_size_bytes=18350490,
            archive_sha256=(
                "b10b8cbcc0fef99771daf13cfea426d1dde4baf36618a9b4c4c30a6f79115650"
            ),
            executable_relative_path=PurePosixPath("llama-completion.exe"),
            license_name=_LLAMA_CPP_LICENSE,
            attribution_text=_LLAMA_CPP_ATTRIBUTION,
        ),
    ),
)
