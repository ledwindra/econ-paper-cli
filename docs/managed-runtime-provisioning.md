# Managed runtime provisioning (issue #58)

`econpapers setup` can automatically install a pinned `llama.cpp` runtime so
an ordinary user never has to build `llama.cpp`, edit `PATH`, or locate an
executable manually. This issue provisions **only the runtime**; selecting or
downloading a default GGUF model remains a separate, unapproved decision
(issue #13) — `--model-path`/`--model-id`/`--model-bytes`/`--model-checksum`
stay required, explicit, user-supplied flags.

## When provisioning runs

- `econpapers setup --llama-cpp-path PATH ...` — unchanged: the explicit path
  is validated and used directly. Managed provisioning is never invoked and
  no download is ever triggered, regardless of whether a managed runtime
  exists.
- `econpapers setup ...` (no `--llama-cpp-path`) — detects the current
  platform/architecture, reuses an already-installed and verified managed
  runtime if one exists, otherwise downloads, verifies, and installs the
  pinned release.
- `econpapers setup --offline ...` (no `--llama-cpp-path`) — same reuse check,
  but never downloads; fails with a typed, actionable error if no verified
  managed runtime is already installed.
- `econpapers analyze`, `econpapers chat`, bare `econpapers`, and `econpapers
  status` never provision or download anything, unconditionally.
- `econpapers status`'s runtime check is strictly read-only: it never
  repairs a non-executable file's permission bits, even though a *fresh
  install*'s own staging step does exactly that for content it just
  extracted (`services.runtime_provisioning.verify_executable_runs` never
  mutates; only `_ensure_staged_executable_bit`, called solely during
  installation of newly staged content, does).

## Pinned release and platform matrix

One `llama.cpp` release (`b10199`) is pinned, with one archive per approved
platform/architecture — macOS arm64, macOS x86_64, Linux x86_64, and Windows
x86_64 — each the CPU-only build (no GPU/accelerator variant, per the
no-GPU-requirement product guardrail). macOS x86_64 (Intel) is included even
though this project's own CI matrix only runs macOS arm64 today, so an Intel
Mac user still gets managed provisioning rather than a forced manual
`--llama-cpp-path`. The pinned data lives
in `econ_paper_cli.domain.runtime_manifest_data.MANAGED_RUNTIME_MANIFEST`, a
plain Python module (not a JSON/data file) so it is always included in built
wheels/sdists with no separate packaging configuration to forget.

Each entry (`econ_paper_cli.domain.runtime_manifest.ManagedRuntimeArtifact`)
records: `runtime_id`/`version_marker`, `platform`/`architecture`,
`source_url` (HTTPS-only, enforced at construction), `archive_format`,
`archive_size_bytes`, `archive_sha256`, the archive-relative
`executable_relative_path`, and the upstream `license_name`/
`attribution_text`. A platform/architecture combination with no matching
entry is reported as unsupported (`econpapers status` shows
`unsupported_platform`; `setup` without an explicit path fails with a typed
error) rather than silently attempting an unpinned download.

## Install contract (stage → verify → promote)

`econ_paper_cli.services.runtime_provisioning.ensure_managed_runtime`:

1. Downloads the pinned archive to a temporary file
   (`adapters.runtime_downloader.UrllibDownloader`: HTTPS-only, bounded
   redirects, connect/read timeout, and an incremental byte cap at the
   manifest's expected size — aborted mid-stream on any violation, not just
   rejected after an unbounded download).
2. Verifies the whole archive's size and SHA-256 before any extraction.
3. Extracts into a **sibling staging directory**
   (`adapters.runtime_extractor.SafeArchiveExtractor`: rejects absolute
   paths, parent-directory traversal, symlink/hardlink members, and
   duplicate member names).
4. Hashes every extracted file and runs the staged executable
   (`--version --offline`) to confirm it actually starts and reports the
   expected pinned version marker — all **while still staged**.
5. Writes a schema-versioned install receipt (`receipt.json`, see below)
   into the staged directory.
6. Promotes the staged directory via `os.replace` onto a **content-addressed
   final path** (`<runtime_id>-<archive_sha256[:16]>/`) that does not
   already exist. This is atomic and portable (POSIX and Windows both permit
   renaming onto a non-existent target), and makes concurrent installs of
   the same pinned artifact race-safe by construction: colliding installs
   are byte-identical by definition, so losing the promotion race just means
   adopting the winner's already-verified result instead of overwriting it.
7. Only after promotion succeeds does `setup` persist the resolved
   executable path — **and** the installed `runtime_id`/`version_marker`
   (optional additions to `LocalRuntimeModelConfig` schema version 1; a
   config saved before they existed simply loads with both as `None`, and
   `LlamaCppConfig`'s own defaults apply exactly as before) — through the
   existing durable configuration boundary (`ConfigBackend.save()`). This is
   what makes the pinned identity survive a process restart: `analyze`,
   `chat`, and bare `econpapers` all resolve the same recorded identity
   through `config_resolution.build_llama_cpp_config_kwargs` rather than
   falling back to the adapter's hard-coded default. If the save fails, the
   promoted install stays on disk (valid and reusable on retry) and the
   previously configured runtime is untouched.

A directory found at the target content-addressed path that fails receipt
verification is treated as corrupt (e.g. from an interrupted previous
install) and is evicted before a fresh install is promoted to that same
path — never overwritten in place by `os.replace` on a populated directory.

## Install receipt

`econ_paper_cli.domain.runtime_receipt.InstallReceipt`, written once per
install (schema version 1):

```json
{
  "schema_version": 1,
  "runtime_id": "llama.cpp-b10199",
  "version_marker": "10199",
  "platform": "linux",
  "architecture": "x86_64",
  "source_asset_identity": "https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-ubuntu-x64.tar.gz",
  "archive_size_bytes": 16434223,
  "archive_sha256": "16d63bfb5c7e1c1656d940de398456ed2972af16ab5a0961f88c5929bc4fe58a",
  "executable_relative_path": "llama-b10199/llama-completion",
  "executable_sha256": "...",
  "member_checksums": [["llama-b10199/llama-completion", "..."], ["llama-b10199/libggml.so", "..."]]
}
```

`member_checksums` is the full closure of every file actually extracted, not
just the top-level executable — a managed `llama.cpp` install includes
adjacent shared libraries the executable depends on, so tampering with any of
them (not only the executable itself) is detected. Verification
(`services.runtime_provisioning.verify_managed_install`) additionally
rejects any regular file present on disk that is *not* declared in
`member_checksums` (an injected extra library cannot escape bundle-integrity
checks by simply not being listed), and — when a manifest-selected artifact
is available for comparison — checks the receipt's own identity (runtime id,
version marker, platform, architecture, source asset, archive size/hash,
executable path) against it exactly, so a self-consistent but stale or
foreign receipt is never silently reused. `econpapers status` and the setup
reuse-check both classify a configured executable as **managed** only when
it passes all of this — living under the default runtime directory is
never, by itself, sufficient to call an install "managed" or "verified."

## Recovery

To force a completely clean reinstall, delete the managed runtime directory
(or the specific content-addressed subdirectory reported by `econpapers
status`) and re-run `econpapers setup` without `--llama-cpp-path`:

```bash
econpapers status                        # shows the configured executable path and runtime state
rm -rf "<app-data-dir>/econpapers/runtime" # delete the whole managed runtime directory (all installs)
econpapers setup --model-path ... --model-id ... --model-bytes ... --model-checksum ...
```

`<app-data-dir>` is the platform's per-user application-data directory (e.g.
`~/Library/Application Support` on macOS, `~/.local/share` on Linux,
`%LOCALAPPDATA%` on Windows) — the same base directory the SQLite library
already uses, under a `runtime/` subdirectory.

The `ECONPAPERS_RUNTIME_DIR` environment variable overrides the install
location, independent of the config directory (`ECONPAPERS_CONFIG_DIR`) and
the library/database directory (`ECONPAPERS_LIBRARY_DIR`) — the same
per-variable-override pattern those two already use.

## Upstream license and attribution

The pinned runtime is `llama.cpp` (<https://github.com/ggml-org/llama.cpp>),
Copyright (c) 2023–2026 The ggml authors, licensed under the MIT License. The
full license text is bundled inside every downloaded release archive
(`LICENSE`) and is not reproduced or redistributed by this repository —
`econpapers` only downloads the official upstream release asset directly
from GitHub at setup time, checksum-verifies it, and installs it locally; it
is never vendored into this project's own source tree or packages.
