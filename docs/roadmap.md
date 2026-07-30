# Roadmap

This roadmap orders small, reviewable slices. It does not imply that unbuilt
features are available.

## 1. Repository foundation (implemented)

- Installable Python 3.10+ package using a `src` layout
- Thin `econpapers` CLI with setup, status, chat, and update placeholders
- Cross-platform lint and test automation
- Initial product and architecture constraints

## 2. Artifact contracts (implemented)

- Define manifests, checksum syntax, local paths, and actionable validation
  errors (schema-version-1 domain contract implemented)
- Add filesystem loading, size checking, and checksum calculation behind adapters (implemented)
- Keep downloads absent until sources, licenses, sizes, and update policies are
  approved

## 3. Corpus contracts and fixtures (implemented)

- Define pure, immutable `Corpus` domain contract with cross-record invariant validation
- Add 100% synthetic, legally redistributable CC0-1.0 fixture corpus (`synthetic-economics-v1.json`)
- Add filesystem loader adapter (`load_corpus_from_file`) returning `Corpus` domain objects
- Verify manifest size and SHA-256 digests relative to repo root
- Preserve clean separation between artifact digest verification and domain parsing

## 4. Evidence and retrieval contracts

Completed:
- `Paper`, `Passage`, `RetrievalEvidence`, `Citation`, and `Corpus` domain contracts
- Synthetic CC0 corpus suitable for deterministic retrieval evaluation

Not yet implemented:
- Replaceable retrieval protocol interface
- Deterministic retrieval contract tests
- Sparse retrieval baseline adapter (e.g. BM25 / TF-IDF)
- Representative retrieval evaluation on economics queries
- Retrieval adapter selection

## 5. Local inference and synthesis

- Define a replaceable local generation protocol
- Validate citations, preserve uncertainty, and support abstention
- Approve a default model separately before adding download behavior

## 6. End-to-end MVP

- Connect setup, status, update, chat, follow-up, and evidence inspection
- Verify offline operation, privacy, restart safety, and cross-platform behavior
- Document artifact licenses and release procedures
