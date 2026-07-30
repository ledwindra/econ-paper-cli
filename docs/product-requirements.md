# Product requirements

## Implementation status

Implementation status and milestone progress are tracked in [`docs/architecture.md`](architecture.md) and [`docs/roadmap.md`](roadmap.md).

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

## Artifact requirements

Every corpus, index, or model artifact must have versioned metadata describing
its source, license, redistribution status, expected size, SHA-256 digest,
update policy, copyrighted-full-text status, and portable local path. Manifest
validation does not itself establish legal permission or verify file contents.

Artifact metadata and checksum verification must remain separable from network
downloads and filesystem persistence. Unknown or prohibited redistribution
status must never be interpreted as authorization to download or redistribute.

## Privacy and offline operation

- Retrieval, generation, indexing, and corpus inspection must run locally in the default workflow.
- No user queries, documents, or search history may be uploaded to external services by default.
- Default operation must remain offline after the required artifacts are installed.
- Network access may occur only through explicit setup, manual update, or other separately approved opt-in adapters.
- Network access must not be triggered implicitly during retrieval, generation, corpus inspection, or ordinary CLI use.

## Evidence and grounding

- Substantive claims in synthesized answers must cite valid retrieved evidence passages.
- Citation identifiers in generated answers must be verified against retrieved evidence before output is rendered.
- Retrieval results must preserve paper identity and passage boundaries.
- Results must expose deterministic ranks and scores at the protocol boundary.
- Higher scores represent stronger matches at the protocol boundary.
- Retrieval implementations must be replaceable.
- When retrieved evidence is insufficient to answer a query, the system must explicitly abstain rather than fabricate claims.

## Deferred capabilities

New or changed model, corpus, index, artifact, and citation-format decisions require explicit design and maintainer approval before implementation. The following remain out of scope for default execution:

- Paid cloud inference APIs (e.g. OpenAI, Anthropic)
- Mandatory Docker or GPU requirements
- Redistribution of copyrighted paper full text without permission
- Automatic background telemetry or query tracking
