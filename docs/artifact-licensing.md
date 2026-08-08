# Downloaded artifact licensing

This is the single authoritative record of every artifact the application
downloads, with the seven facts `AGENTS.md`'s "Corpus and licensing
requirements" section requires: source, license, redistribution status,
expected file size, checksum, update policy, and whether the artifact contains
copyrighted full text.

Six artifacts are downloadable in total — two GGUF models and four
`llama.cpp` release archives. Nothing here is committed to the repository or
redistributed by it; each is fetched directly from its upstream publisher over
HTTPS, and only by an explicit `econpapers setup` or `econpapers update`
invocation.

## Scope of this document

**[current]**: this covers exactly the artifacts the application can download.
It is not a corpus manifest. No paper corpus, converted paper text, or
retrieval index is downloaded, shipped, or redistributed — see
[Corpus policy](../README.md#corpus-policy) for the separate paper-content
rules, which are stricter.

The `redistribution_status` values below use the vocabulary of
[`docs/artifact-manifest.md`](artifact-manifest.md), whose safety boundary
applies here too: `permitted` records a maintainer-supplied classification of
the upstream license and does not itself create legal permission.

## Models

Both models are provisioned by `econpapers setup` when `--model-path` and its
companion flags are omitted. The catalog is
`econ_paper_cli.domain.model_manifest`.

### Qwen2.5 1.5B Instruct Q4_K_M — the default

| Fact | Value |
| --- | --- |
| Source | `https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| License | Apache-2.0 |
| Redistribution status | `permitted` by the upstream license; this repository does not redistribute it and downloads it directly from Hugging Face |
| Expected file size | 1,117,320,736 bytes (~1.04 GiB) |
| Checksum (SHA-256) | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| Update policy | Pinned. See "Update policy" below. |
| Contains copyrighted full text | No. Quantized model weights; it contains no paper text from this project's corpus. |

Attribution: Qwen2.5 1.5B Instruct GGUF, published by Alibaba Cloud (Qwen).

### Qwen2.5 7B Instruct Q4_K_M — opt-in via `--model`

| Fact | Value |
| --- | --- |
| Source | `https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf` |
| License | Apache-2.0 |
| Redistribution status | `permitted` by the upstream license; this repository does not redistribute it and downloads it directly from Hugging Face |
| Expected file size | 4,683,074,240 bytes (~4.36 GiB) |
| Checksum (SHA-256) | `65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423` |
| Update policy | Pinned. See "Update policy" below. |
| Contains copyrighted full text | No. Quantized model weights; it contains no paper text from this project's corpus. |

Attribution: Qwen2.5 7B Instruct GGUF quantizations by bartowski, from Alibaba
Cloud (Qwen) base weights.

## Runtime archives

`econpapers setup` downloads exactly one of these four, selected by the
detected platform and architecture. All four are pinned to `llama.cpp` release
`b10199`. Each is the standard upstream release asset for its platform: none
is a CUDA, ROCm, or Vulkan build, and none requires a GPU, which is what the
"no GPU requirement" product guardrail asks for. That is not the same as
containing no accelerator support — the macOS arm64 asset bundles the Metal
backend (`libggml-metal`), and both macOS assets bundle BLAS. The Linux and
Windows assets are CPU-only, shipping many per-microarchitecture CPU backends
instead. The catalog is `econ_paper_cli.domain.runtime_manifest_data`.

These three facts are identical for all four archives:

- **License:** MIT.
- **Redistribution status:** `permitted` by the MIT License, which requires the
  copyright and permission notice to be preserved. This repository does not
  redistribute any archive or extracted binary; each is downloaded directly
  from the upstream GitHub release, and the full license text ships inside the
  archive as `LICENSE` rather than being reproduced here.
- **Contains copyrighted full text:** No. These are compiled binaries and
  support files built from MIT-licensed source; they contain no paper text.

| Platform / architecture | Source | Size (bytes) | SHA-256 |
| --- | --- | ---: | --- |
| macOS arm64 | `https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-macos-arm64.tar.gz` | 10,939,809 | `a7bc124584fbed7e848f7d95987a6c537399a7398682f45fa32b66852269ae6c` |
| macOS x86_64 | `https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-macos-x64.tar.gz` | 11,216,652 | `df24f71388941f030cf4f0f716584f0c5fdeb4465ff67a036d37575d809b4799` |
| Linux x86_64 | `https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-ubuntu-x64.tar.gz` | 16,434,223 | `16d63bfb5c7e1c1656d940de398456ed2972af16ab5a0961f88c5929bc4fe58a` |
| Windows x86_64 | `https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-win-cpu-x64.zip` | 18,350,490 | `b10b8cbcc0fef99771daf13cfea426d1dde4baf36618a9b4c4c30a6f79115650` |

Attribution: llama.cpp (`https://github.com/ggml-org/llama.cpp`), Copyright (c)
2023–2026 The ggml authors, licensed under the MIT License.

The listed checksum is the digest of the **compressed archive**, not of the
extracted executable. Every file inside each archive additionally has its own
recorded digest (`bundle_member_checksums`), so a tampered file combined with
a rewritten install receipt still fails verification against the
version-controlled manifest.

## Update policy

One policy governs all six artifacts.

Every entry is **pinned**: an exact URL, byte size, and SHA-256 recorded in
version-controlled Python data. The application never tracks upstream
releases, resolves "latest", or upgrades an artifact on its own.

`econpapers update` verifies installed artifacts against these pins and
repairs — re-downloads — anything missing or corrupt. When a pin in the
manifest no longer matches what the user's durable configuration records, it
reports `newer_version_available` and stops rather than migrating silently;
adopting the new pin is the user's decision.

Changing a pin is a maintainer action, not an automatic one. It requires
downloading the real asset, computing its size and digest from that download,
and updating the manifest — pins are never transcribed from upstream
documentation. Both manifest modules state this rule in their docstrings,
because a wrong pin breaks provisioning for every user at once.

## Residual schema gap — M6

`ManagedModelArtifact` and `ManagedRuntimeArtifact` carry source, size,
checksum, license, and attribution as typed fields, but not
`redistribution_status`, `update_policy`, or `contains_copyrighted_full_text`
— the three facts `domain.ArtifactManifest` does carry. This document supplies
all three in prose for all six artifacts, which is what `AGENTS.md` requires:
that the facts be documented.

Conforming the two dataclasses to `ArtifactManifest` is a schema change and is
tracked as **M6** in [`MVP-PLAN.md`](../MVP-PLAN.md). M6 is explicitly
post-MVP and does not block the MVP-readiness gate, matching the 2026-08-08
decision to approve the download behavior rather than gate it on these fields.
Until M6 lands, this document is the authoritative source for those three
facts.
