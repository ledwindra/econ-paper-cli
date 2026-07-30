# Architecture

## Design goals

The system uses a layered, adapter-oriented design so local inference,
retrieval, storage, and corpus implementations can change without rewriting
the product workflow.

The intended dependency direction is:

```text
CLI adapters -> application services -> domain types and protocols
                                      <- infrastructure adapters
```

The domain and application layers must not depend directly on a database,
vector store, model runtime, embedding model, or network service.

## Layers

- **Domain:** typed papers, passages, evidence, scores, citations, and errors.
- **Application services:** orchestration for retrieval, generation, evidence
  validation, abstention, ingestion, and artifact management.
- **Protocols:** replaceable interfaces for retrieval, inference, storage, and
  artifact sources.
- **Adapters:** CLI, filesystem, network, model runtime, index, and corpus
  implementations. Effects remain visible and injectable.

Retrieval, generation, corpus ingestion, and artifact management remain
separate concerns. Evidence identity and passage boundaries remain structured
throughout the pipeline, and generated citation identifiers must be validated
against retrieved evidence before display.

## Current scaffold

Issue 1 contains a standard-library CLI adapter and side-effect-free placeholder
application services. The CLI parses commands, delegates once, prints the
result, and returns an exit code.

Issue 2 adds the first domain object: an immutable artifact manifest with pure
mapping conversion and validation.

Issue 3 implements local filesystem adapters in `econ_paper_cli.adapters.filesystem` to load artifact manifests from JSON files and verify local file sizes and SHA-256 digests using chunked streams.

No network adapter, retrieval engine, or local inference runtime exists yet.

Future changes should introduce only the narrow interfaces required by their
issue and use dependency injection rather than global state.

