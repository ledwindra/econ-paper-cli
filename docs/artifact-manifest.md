# Artifact manifest schema

Artifact manifests describe metadata for one corpus, index, or model artifact.
Committed corpus and model instances use the contract today. The `index` kind
is **[planned]**: the schema accepts it, but no index artifact is built, shipped,
or manifested. Schema version 1 is a domain contract only: it does not load
files, access the network, calculate a checksum, or authorize downloading or
redistributing an artifact.

## Schema version 1

The example below is illustrative only. It is **[planned]**: no
`synthetic-fixture-index` artifact — or any other index artifact — exists,
ships, or is downloadable.

```json
{
  "schema_version": 1,
  "artifact_id": "synthetic-fixture-index",
  "kind": "index",
  "version": "1.0.0",
  "source": "https://example.invalid/synthetic-fixture-index",
  "license": "CC0-1.0",
  "redistribution_status": "permitted",
  "expected_size_bytes": 128,
  "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "update_policy": "Pinned test fixture; update manually.",
  "contains_copyrighted_full_text": false,
  "local_path": "indexes/synthetic-fixture-index.bin"
}
```

Every field is required, and unknown fields are rejected.

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1`. |
| `artifact_id` | Lowercase identifier matching `[a-z0-9]+(?:[._-][a-z0-9]+)*`. |
| `kind` | `corpus`, `index`, or `model`. The enum accepts `index` (**[current]**); no `index` artifact exists (**[planned]**). |
| `version` | Nonempty version label supplied by the artifact maintainer. |
| `source` | Nonempty provenance string; schema version 1 does not require a URL scheme. |
| `license` | Nonempty license identifier or description. |
| `redistribution_status` | `permitted`, `prohibited`, or `unknown`. |
| `expected_size_bytes` | Positive integer expected file size. |
| `sha256` | Exactly 64 lowercase hexadecimal characters. |
| `update_policy` | Nonempty description of how and when the entry is updated. |
| `contains_copyrighted_full_text` | Boolean disclosure — see the scope definition below. |
| `local_path` | Portable relative path using `/`, with no parent traversal or components invalid on a supported platform. |

## Python contract

`econ_paper_cli.domain.ArtifactManifest` validates direct construction and
JSON-compatible mappings. `from_mapping()` maps the serialized `license` field
to the Python attribute `license_name`; `to_mapping()` returns the canonical
serialized form.

Validation raises `ArtifactManifestError` with the affected field and expected
format. The manifest is immutable after construction.

## Scope of `contains_copyrighted_full_text`

`contains_copyrighted_full_text` discloses **whether the artifact's
distributed bytes contain copyrighted full text of research papers**. It is a
corpus-content disclosure, which is why `AGENTS.md`'s corpus-and-licensing
section requires it: the rule it serves is that this project must not
redistribute copyrighted paper text.

It is **not** a claim about a model's training data, which this project does
not characterize. A quantized GGUF is `false` because the file itself carries
no paper text, not because anything here establishes what the weights were
trained on.

This definition governs both `ArtifactManifest` and the managed catalogs
described below, so the same field name means the same thing everywhere.

## Relationship to the managed catalogs

`ManagedModelArtifact` (`domain/model_manifest.py`) and
`ManagedRuntimeArtifact` (`domain/runtime_manifest.py`) describe the artifacts
`econpapers setup` and `econpapers update` download. They carry the same seven
licensing facts this schema does, and they **share this module's vocabulary**:
the `RedistributionStatus` enum, the `str` type for
`update_policy`, and the disclosure scope defined immediately above. All six
pinned artifacts reference one `domain.artifacts.PINNED_UPDATE_POLICY`
constant.

They do **not** conform to or reuse `ArtifactManifest` itself. Three reasons,
each checkable against the code:

1. **`local_path` is required and cannot be pinned for the runtime.** The
   runtime's install directory is content-addressed and computed at
   provisioning time, and `runtime_manifest.py` has no path field at all. This
   is weaker for models — the committed `artifacts/models/*.manifest.json`
   records show a model `local_path` is perfectly expressible — but the two
   managed types are treated together.
2. **`ArtifactKind` has no `runtime` member, and adding one forces an
   unanswered design question.** Widening the enum would itself be
   backward-compatible; existing records stay valid. The real cost is deciding
   whether runtime bundles belong in a serialized manifest at all, and if so
   what `local_path` means for a content-addressed install directory and where
   `bundle_member_checksums`, `archive_format`, and
   `executable_relative_path` live. That is a schema design task, not a
   metadata one.
3. **The managed types carry fields this schema has no slot for**:
   `archive_format`, `bundle_member_checksums`, `executable_relative_path`,
   `platform`, `architecture`, `minimum_free_ram_bytes`, `display_name`,
   `summary`, and `attribution_text`. Conformance would either drop them or
   force a parallel type anyway.

### Where this schema is actually used

No module under `src/` consumes `ArtifactManifest`; the application's
provisioning paths use the managed catalogs instead. It is exercised by
`tests/adapters/test_filesystem.py`, `tests/adapters/test_corpus.py`,
`tests/evaluation/test_generation_evaluation.py`, and
`integration_tests/test_llama_cpp_model.py`, the last of which loads a
manifest from a user-supplied `ECONPAPERS_MODEL_MANIFEST` path.

Four instances are committed: the synthetic corpus fixture
(`tests/fixtures/corpus/synthetic-economics-v1.manifest.json`) and three
Issue-13 model evaluation candidates under `artifacts/models/`. One of those
three describes the same bytes as the managed catalog's default model; see
[`artifact-licensing.md`](artifact-licensing.md) for how the two records
relate and which fields may legitimately differ.

## Filesystem adapters

`econ_paper_cli.adapters.filesystem` contains adapters to load and verify local artifact manifests and files:

- `load_manifest_from_file(path: Path) -> ArtifactManifest`: Loads and validates a manifest from a local JSON file. Raises `ManifestLoadError` subclasses on failure.
- `verify_artifact(manifest: ArtifactManifest, base_dir: Path) -> VerificationResult`: Resolves the relative path in the manifest against the `base_dir` and verifies the size and checksum of the file. Raises `VerificationError` subclasses on mismatch or access errors.
- `verify_local_file(path: Path, expected_size_bytes: int, expected_sha256: str) -> tuple[int, str]`: Computes a file's size and SHA-256 in chunks to check for validity.

## Safety boundary

A syntactically valid manifest is not proof that its source, license, checksum,
or redistribution claim is correct. In particular:

- `permitted` records a maintainer-supplied classification; it does not create
  legal permission.
- `unknown` and `prohibited` must never be treated as download or redistribution
  authorization.
- SHA-256 validation checks the digest's representation, not a file's contents.
- `local_path` is a logical relative destination, not evidence that a file
  exists.

Filesystem adapters verify these claims locally. Network adapters and other integrations must preserve the repository's licensing and privacy guardrails before performing effects.
