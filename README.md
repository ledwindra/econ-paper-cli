# Econ Paper CLI

A free, open-source, local-first conversational literature search tool for economists.

The goal is to let users ask research questions in natural language and receive synthesized answers backed by inspectable evidence. The following session is an illustration of the intended product, not current behavior:

```text
$ econpapers chat

> Has anyone studied the effect of direct regional elections on infrastructure investment?

Answer:
...

Evidence:
[1] Author (Year), Paper Title
    Relevant passage: ...
```

## Product principles

- **Free by default:** no API keys, subscriptions, or per-token charges.
- **Local-first:** retrieval and generation run on the user's machine.
- **Cross-platform:** Windows, macOS, and Linux are first-class targets.
- **CPU-capable:** a GPU may improve performance but must not be required.
- **Evidence-driven:** substantive claims must be traceable to retrieved sources.
- **Open source:** the codebase should remain auditable and extensible.
- **Legally conservative:** copyrighted paper full text must not be redistributed without permission.

## MVP

The MVP should include:

- a conversational command-line interface;
- a local language model;
- a bundled economics-paper metadata and retrieval index;
- literature synthesis;
- follow-up questions;
- evidence inspection;
- local setup and status commands.

The MVP should not require:

- OpenAI;
- Anthropic;
- any paid cloud model;
- API keys;
- Docker;
- a GPU;
- internet access after required artifacts are installed.

## CLI scaffold

Install the project from the repository using Python 3.10 or newer:

```bash
python -m pip install -e .
```

The package currently exposes these commands:

```bash
econpapers setup
econpapers chat
econpapers status
econpapers update
```

All four commands are deterministic placeholders. They do not download files,
access the network, retrieve papers, or run a language model.

### Intended future workflow

```bash
econpapers setup
econpapers chat
```

Inside the chat:

```text
> What is the literature on direct regional elections and public investment?

> :evidence 1

> How credible is the causal identification?

> :quit
```

## Planned local inference stack

The current baseline is:

- `llama.cpp`-compatible runtime;
- quantized GGUF instruction model;
- CPU-only support;
- SmolLM2 1.7B Q4 as the initial default candidate;
- Qwen3 0.6B Q4 as a smaller fallback candidate.

These model choices are provisional. Exact artifacts, licenses, checksums, memory requirements, and prompt compatibility must be verified before release.

## Corpus policy

NBER working papers and many other economics papers are copyrighted. Converting them to Markdown does not remove copyright restrictions.

The project may distribute:

- bibliographic metadata;
- paper identifiers;
- source URLs;
- abstracts where redistribution is permitted;
- topic labels;
- derived indexes that do not reconstruct copyrighted full text;
- code that helps users build a local corpus from authorized sources.

The project must not bundle converted full-text papers unless redistribution permission is established.

## Repository direction

The repository is growing toward this structure as later scoped issues add the
remaining layers:

```text
econ-paper-cli/
├── README.md
├── AGENTS.md
├── LICENSE
├── pyproject.toml
├── docs/
│   ├── product-requirements.md
│   ├── architecture.md
│   └── roadmap.md
├── src/
│   └── econ_paper_cli/
├── tests/
├── skills/
└── .github/
    └── workflows/
```

## Development workflow

Development is issue-driven:

1. Define one narrowly scoped GitHub issue.
2. Assign explicit acceptance criteria.
3. Implement one issue per pull request.
4. Add or update tests.
5. Update documentation when behavior changes.
6. Run linting and tests before merge.

Install the package and development tools in editable mode from an activated
Python 3.10 or newer virtual environment:

```bash
python -m pip install -e ".[dev]"
```

Run the required checks before opening a pull request:

```bash
ruff check .
ruff format --check .
pytest
```

## Architecture principles

- Keep the CLI thin.
- Put orchestration in application services.
- Represent papers and evidence as typed domain objects.
- Keep retrieval and generation behind replaceable interfaces.
- Isolate filesystem, network, model, and index operations in adapters.
- Avoid unnecessary frameworks.
- Prefer deterministic, offline tests.
- Do not bind the core domain to a specific vector database or model runtime.

## Current status

This project has an installable package, placeholder CLI commands, initial
requirements and architecture documents, cross-platform CI configuration, a
pure domain contract for validating artifact metadata, filesystem adapters
for loading local manifests and verifying file checksums, pure domain
contracts for papers, passages, retrieval evidence, citations, and corpora,
and a synthetic CC0 fixture corpus with a local corpus loader adapter.
PDF/document ingestion, passage segmentation, retrieval, local inference, and conversational
execution are not implemented.

The immediate priorities are:

1. build a retrieval baseline;
2. add an approved local generation adapter;
3. implement grounded synthesis and evidence validation;
4. validate installation on Windows, macOS, and Linux.

## Contributing

Before making changes, read [`AGENTS.md`](AGENTS.md).

Pull requests should be small, issue-linked, tested, and limited to one coherent change.

## License

The source code is released under the [MIT License](LICENSE). Models, corpora,
indexes, and datasets retain their own licenses and terms.
