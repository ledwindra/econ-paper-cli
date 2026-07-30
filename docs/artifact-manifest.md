# Artifact manifest schema

Artifact manifests describe metadata for one future corpus, index, or model
artifact. Schema version 1 is a domain contract only: it does not load files,
access the network, calculate a checksum, or authorize downloading or
redistributing an artifact.

## Schema version 1

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
| `kind` | `corpus`, `index`, or `model`. |
| `version` | Nonempty version label supplied by the artifact maintainer. |
| `source` | Nonempty provenance string; schema version 1 does not require a URL scheme. |
| `license` | Nonempty license identifier or description. |
| `redistribution_status` | `permitted`, `prohibited`, or `unknown`. |
| `expected_size_bytes` | Positive integer expected file size. |
| `sha256` | Exactly 64 lowercase hexadecimal characters. |
| `update_policy` | Nonempty description of how and when the entry is updated. |
| `contains_copyrighted_full_text` | Boolean disclosure. |
| `local_path` | Portable relative path using `/`, with no parent traversal or components invalid on a supported platform. |

## Python contract

`econ_paper_cli.domain.ArtifactManifest` validates direct construction and
JSON-compatible mappings. `from_mapping()` maps the serialized `license` field
to the Python attribute `license_name`; `to_mapping()` returns the canonical
serialized form.

Validation raises `ArtifactManifestError` with the affected field and expected
format. The manifest is immutable after construction.

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

