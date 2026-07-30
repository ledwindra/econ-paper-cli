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

Issue 4 adds pure, immutable domain contracts for `Paper`, `Passage`, `RetrievalEvidence`, and `Citation` with strict validation rules and JSON-compatible mapping conversion.

Issue 5 introduces the pure immutable `Corpus` domain object (`econ_paper_cli.domain.corpora.Corpus`), a 100% synthetic, legally redistributable CC0-1.0 economics fixture corpus (`tests/fixtures/corpus/synthetic-economics-v1.json`), and a local corpus loader adapter (`econ_paper_cli.adapters.corpus.load_corpus_from_file`). Artifact verification and corpus parsing remain strictly separate concerns: filesystem adapters verify artifact digests, while `Corpus.from_mapping` enforces corpus-level schema and cross-record integrity.

Issue 6 defines the backend-independent retrieval boundary in `econ_paper_cli.protocols.retrieval`. It introduces the immutable `RetrievalRequest` domain object, the replaceable `Retriever` protocol, and pure result contract validation via `validate_retrieval_results` (enforcing contiguous 1-based ranks, non-increasing score order, ascending `passage_id` tie-breaking, uniform non-empty `retrieval_method` labels, and exact duplicate passage rejection). Near-duplicate passage suppression is deferred to adapter evaluation. For full specification details, see [`docs/retrieval-contract.md`](retrieval-contract.md).

Issue 7 implements the first concrete `Retriever` adapter: `econ_paper_cli.adapters.bm25.BM25Retriever`. It is a pure-Python, standard-library-only BM25 baseline constructed over an in-memory `Corpus`. It computes corpus-wide term statistics once during initialization and indexes `Passage.text` only. It employs the `bm25-v1` tokenizer (`char.isalnum()` character scan with NFKC normalization and casefolding), positive IDF, score-descending sorting with `passage_id` ascending tie-breaking, and normalized lexical duplicate suppression. Returned tuples pass `validate_retrieval_results` with `retrieval_method="bm25-v1"`. Query-time retrieval performs zero filesystem or network I/O. Persisted index artifacts, CLI integration, PDF parsing, Markdown conversion, and local model inference remain unimplemented. `BM25Retriever` is not selected as the default retrieval backend.

Issue 8 adds `econ_paper_cli.evaluation.retrieval`, a pure evaluation boundary
over the existing `Retriever` protocol. A frozen CC0 benchmark supplies 25
synthetic economics queries, binary passage-level relevance judgments, ranked
metrics at `k=1,3,5`, and conservative CI regression gates. The benchmark is
pinned to canonical retrieval-relevant corpus content with a SHA-256 fingerprint
validated before retrieval. The untuned `BM25Retriever` is measured by this
benchmark but is not selected as the permanent default or claimed to outperform
another adapter. See [`docs/retrieval-evaluation.md`](retrieval-evaluation.md).

Issue 9 selects `BM25Retriever` as the initial, replaceable retrieval backend.
The decision uses the frozen benchmark together with installation, artifact,
CPU, portability, licensing, privacy, and maintenance evidence; it does not
claim BM25 is permanently optimal. `econ_paper_cli.evaluation.resources` adds a
backend-independent, standard-library observation boundary for stable result
digests, initialization and query timings, Python heap, available process RSS,
and machine metadata. Correctness remains CI-gated while timing and memory are
non-gating observations. No second adapter, production dependency, model
artifact, persisted index, CLI wiring, or generation integration is added. See
[`docs/retrieval-selection.md`](retrieval-selection.md).

Issue 10 defines the backend-independent local-generation boundary in
`econ_paper_cli.protocols.generation`. Immutable request and response objects
carry the user's question, ranked retrieval evidence, answer text, structured
citations, generation-method identity, answer-level finding kinds, and explicit
abstention state. `validate_generation_response` verifies rank-derived citation
identity, supplied-evidence membership, canonical citation ordering, and
abstention consistency. These structural checks do not prove factual grounding
or sentence-level citation support. No model adapter, runtime dependency,
artifact, retrieval orchestration, CLI integration, or PDF ingestion is added.
See [`docs/generation-contract.md`](generation-contract.md).

Future changes should introduce only the narrow interfaces required by their
issue and use dependency injection rather than global state.
