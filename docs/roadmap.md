# Roadmap

This roadmap orders small, reviewable slices. It does not imply that unbuilt
features are available.

## 1. Repository foundation

- Installable Python 3.10+ package using a `src` layout
- Thin `econpapers` CLI with setup, status, chat, and update placeholders
- Cross-platform lint and test automation
- Initial product and architecture constraints

## 2. Artifact contracts

- Define manifests, checksum syntax, local paths, and actionable validation
  errors (schema-version-1 domain contract implemented)
- Add filesystem loading and checksum calculation behind adapters
- Keep downloads absent until sources, licenses, sizes, and update policies are
  approved

## 3. Evidence and retrieval contracts

- Add typed paper, passage, score, evidence, and citation objects
- Establish deterministic protocol tests with synthetic text
- Select a retrieval adapter only after representative evaluation

## 4. Local inference and synthesis

- Define a replaceable local generation protocol
- Validate citations, preserve uncertainty, and support abstention
- Approve a default model separately before adding download behavior

## 5. End-to-end MVP

- Connect setup, status, update, chat, follow-up, and evidence inspection
- Verify offline operation, privacy, restart safety, and cross-platform behavior
- Document artifact licenses and release procedures
