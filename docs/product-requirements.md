# Product requirements

## Mission

Econ Paper CLI is a free, open-source, local-first conversational literature
search tool for economists. It should answer research questions with concise
synthesis and make the supporting papers and passages inspectable.

## MVP requirements

The MVP must:

- provide an `econpapers` CLI with setup, status, chat, update, follow-up, and
  evidence-inspection workflows;
- run retrieval and language-model inference locally in the default workflow;
- require no paid service, API key, Docker installation, or GPU;
- operate offline after the user has installed the required artifacts;
- support Python 3.10 or newer on Windows, macOS, and Linux;
- preserve paper identity and passage boundaries in evidence;
- ground substantive claims in supplied evidence and abstain when evidence is
  insufficient;
- distinguish descriptive findings from causal findings and retain uncertainty
  or disagreement;
- avoid telemetry and uploads of queries, documents, or indexes by default; and
- distribute only metadata, permitted derived artifacts, and content with
  documented redistribution rights.

## Issue 1 behavior

Issue 1 establishes only the installable package and command scaffold. The
`setup`, `status`, `chat`, and `update` commands are deterministic placeholders.
They do not download artifacts, access a network, retrieve papers, or run a
model. Follow-up and evidence-inspection behavior remain future work.

## Out of scope for Issue 1

- Corpus selection or distribution
- Model selection, download, or inference
- Embeddings, indexes, vector stores, or retrieval
- Evidence and citation-format decisions
- Telemetry, hosted inference, and web search

Model, corpus, artifact, and citation decisions require explicit design and
maintainer approval before implementation.
