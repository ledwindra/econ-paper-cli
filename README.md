# econpapers

[![CI](https://github.com/ledwindra/econ-paper-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ledwindra/econ-paper-cli/actions/workflows/ci.yml)
[![Offline CLI](https://github.com/ledwindra/econ-paper-cli/actions/workflows/release-readiness.yml/badge.svg)](https://github.com/ledwindra/econ-paper-cli/actions/workflows/release-readiness.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`econpapers` is a free, open-source literature search tool for economists. It
turns a local collection of papers into a conversational library, retrieves
relevant passages, and answers questions with citations that can be inspected
in the terminal.

The default workflow runs on CPU, requires no API key, and stays offline after
setup. Your papers, questions, and library remain on your machine.

## What it does

The unit of evidence in `econpapers` is a stored passage from a paper's
Abstract or Introduction. A question follows four steps:

1. BM25 retrieves passages from the local SQLite library.
2. By default, a local Qwen2.5 model receives the question and retrieved evidence.
3. The model returns an answer with claim-level citation identifiers.
4. `econpapers` validates those identifiers and withholds claims that appear
   to attribute one paper's distinctive wording to another.

This design makes the answer inspectable. It does not make the answer
infallible. The grounding checks are structural, not a semantic proof that a
claim is true or that a finding is causal. See the
[generation contract](docs/generation-contract.md#claim-grounding) for the
exact boundary.

## Requirements

- Python 3.10.12 or newer
- Windows, macOS, or Linux
- A CPU with enough memory for the selected model
- Internet access during the first `setup`
- Local PDF files that you are entitled to use

A GPU is not required. Docker, cloud inference, paid APIs, and API keys are
not part of the default workflow.

## Install

Clone the repository and install it into a virtual environment:

```bash
git clone https://github.com/ledwindra/econ-paper-cli.git
cd econ-paper-cli
python -m venv .venv
```

Activate the environment on macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Then install the package:

```bash
python -m pip install -e .
```

## Quickstart

The shortest path from a directory of PDFs to a cited answer is:

```bash
econpapers setup
econpapers analyze /path/to/papers
econpapers
```

`setup` downloads and verifies a pinned `llama.cpp` runtime and the default
1.5B GGUF model when they are absent. It is idempotent, so a verified
installation is reused.

`analyze` accepts one PDF or a directory. Directory runs show progress, reuse
exact checksum matches, and print a batch summary. Source PDFs are read in
place and are never modified, moved, or deleted.

Bare `econpapers` opens the interactive shell:

```text
$ econpapers
=== econpapers interactive shell ===
Paper Count: 1179
Passage Count: 16940
Evidence scope: stored Abstract and Introduction passages only.
econpapers> Has land titling affected urban development in Indonesia?
```

Use `/show e1` to inspect the passage behind citation `e1`, `/reset` to clear
conversation context, and `/exit` to leave the shell.

For one non-interactive question:

```bash
econpapers chat "What does the literature say about land informality?"
```

Add `--show-evidence` to print the full stored passages with the answer:

```bash
econpapers chat \
  "What does the literature say about land informality?" \
  --show-evidence
```

## Evidence scope

The searchable corpus contains detected Abstract and Introduction passages
only. `analyze` extracts every page locally to locate those sections, but it
persists only the detected early-section text. Results, methods, appendices,
and conclusions are therefore not available unless their content also appears
in an Abstract or Introduction.

This scope has an economic consequence. A question about a paper's setting or
research question may be answerable, while a question about a coefficient,
standard error, welfare calculation, or robustness exercise may not be. When
the available evidence is insufficient, the model may abstain or produce only
the claims supported by the early sections.

Full-document ingestion and OCR are not implemented.

## Commands

| Command | Purpose |
| --- | --- |
| `econpapers setup` | Provision and persist a verified local runtime and model. |
| `econpapers analyze TARGET` | Analyze one PDF or a directory and update the local library. |
| `econpapers` | Open the interactive cited-chat shell. |
| `econpapers chat QUESTION` | Ask one question without opening a shell. |
| `econpapers status` | Report configuration, artifact readiness, and library counts. |
| `econpapers update` | Verify and repair managed runtime and model artifacts. |

Run `econpapers COMMAND --help` for the complete option list.

### Follow-up questions

The interactive shell keeps the last two answered turns in memory. A question
such as "what about its effect on housing?" may be rewritten into a standalone
question before retrieval. The shell prints the rewrite as `Interpreted as:`
so the interpretation remains visible.

Only answered turns enter this context. `/reset` clears it, and one-shot
`econpapers chat` has no conversation history.

### Evidence inspection

Citation identifiers such as `[e1]` refer to retrieved passages, not merely to
paper titles. The following surfaces expose the underlying text:

- `/show` lists citations from the latest answered shell turn.
- `/show e1` prints one cited passage and its provenance.
- `econpapers chat QUESTION --show-evidence` prints all cited passages.

Evidence is scoped to the latest shell turn. An abstention, withheld answer,
failed turn, or `/reset` clears the previous citation set so stale evidence is
not presented as support for a new question.

## Choosing a model

Two checksum-pinned CPU-capable models are available:

| Model | Download | Suggested use |
| --- | ---: | --- |
| Qwen2.5 1.5B Instruct Q4_K_M | about 1.0 GB | Default. Lower memory use and faster CPU generation. |
| Qwen2.5 7B Instruct Q4_K_M | about 4.4 GB | Larger model for users willing to trade memory and time for answer quality. |

Install the default model:

```bash
econpapers setup
```

Select the 7B model:

```bash
econpapers setup --model qwen2.5-7b-instruct-q4-k-m
```

The 7B model may produce stronger synthesis, but CPU generation is much
slower. It was selected outside the repository's frozen model benchmark, so
the project does not claim a measured quality gain. Roughly 8 GB of free
memory is a sensible minimum. The 1.5B model is the product default because
the first run should work on ordinary computers, including machines without a
GPU.

You may also supply your own compatible GGUF and `llama.cpp` executable. The
explicit model path, identifier, size, and checksum must be supplied together.
See `econpapers setup --help` for the full contract.

Artifact sources, checksums, licenses, and redistribution decisions are
recorded in [artifact licensing](docs/artifact-licensing.md).

## Performance

Retrieval and generation have different costs:

- Retrieval uses an in-memory BM25 index built from passages already stored in
  SQLite. No retrieval index is persisted or bundled.
- Local generation starts `llama-completion` for the selected model. On CPU,
  this usually dominates response time.
- The interactive shell builds one retrieval snapshot when it opens. Papers
  added by another `analyze` process become visible after the shell restarts.

If answers take several minutes, confirm the active model with
`econpapers status`. Switching from 7B to 1.5B is the most direct available
speed improvement without changing hardware:

```bash
econpapers setup --model qwen2.5-1.5b-instruct-q4-k-m
```

## Local data and privacy

After setup, `status`, `analyze`, `chat`, and the interactive shell run without
network access. Only explicit `setup` and `update` operations may download
pinned artifacts. The release workflow verifies the offline CLI paths on
Linux, macOS, and Windows.

Default data locations are platform-specific application directories:

| Data | Override |
| --- | --- |
| SQLite library | `ECONPAPERS_LIBRARY_DIR` |
| Runtime and model configuration | `ECONPAPERS_CONFIG_DIR` |
| Managed runtime | `ECONPAPERS_RUNTIME_DIR` |
| Managed models | `ECONPAPERS_MODEL_DIR` |

Run `econpapers status` to see the resolved paths on the current machine.
Questions, answers, and conversation history are not persisted. The SQLite
library contains derived records and passages from the PDFs you analyzed.

## Interpreting an answer

Treat an `econpapers` response as a cited reading aid, not as an estimator or
literature-review substitute. Before using a claim:

1. Inspect its cited passage.
2. Open the source PDF when the distinction depends on methods or results.
3. Check whether the answer describes an association, a research design, or a
   causal estimate.
4. Verify magnitudes and uncertainty against the paper itself.

The optional `descriptive` or `causal` label is declared by the model. The
application validates the label's form but does not compare it with the cited
evidence. It is metadata, not an econometric judgment.

## Troubleshooting

### Setup or update cannot download

Check the network connection, then rerun the command. Downloads are verified
before promotion, and verified artifacts are reused. `econpapers update`
repairs managed artifacts that no longer match their pins.

### A PDF is not stored in the library

Read the batch summary. `Library no sections` means the detector did not find
a usable Abstract or Introduction. Image-only papers may require OCR, which is
not supported. Parser and layout improvements are tracked in
[GitHub Issues](https://github.com/ledwindra/econ-paper-cli/issues).

### Retrieval returns an unexpected paper

BM25 is lexical. A passage can rank highly because it shares uncommon words
with the question even when its setting differs. Ask a more specific question
and inspect the retrieval scores and cited passages. Broader retrieval methods
remain future work and should be adopted only after evaluation on economics
questions.

### The shell does not see a newly analyzed paper

Restart the shell. Its library and retriever are fixed snapshots for the life
of the process.

## Project documentation

| Document | Contents |
| --- | --- |
| [Product requirements](docs/product-requirements.md) | Product scope and standing constraints |
| [Architecture](docs/architecture.md) | Layers, protocols, and implementation history |
| [Roadmap](docs/roadmap.md) | Completed work and future development |
| [Generation contract](docs/generation-contract.md) | Citation, abstention, and grounding behavior |
| [Retrieval contract](docs/retrieval-contract.md) | Ranking and evidence invariants |
| [Release checklist](docs/release-checklist.md) | Reproducible release procedure and known limitations |
| [Artifact licensing](docs/artifact-licensing.md) | Download sources, checksums, and licenses |

## Development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the required checks:

```bash
ruff check .
ruff format --check .
pytest
```

The default suite uses synthetic or redistributable fixtures and makes no
network calls. Model-dependent and private real-PDF tests are opt-in.

Before contributing, read [AGENTS.md](AGENTS.md). Keep pull requests small,
issue-linked, and limited to one coherent change.

## License

The source code is released under the [MIT License](LICENSE). Models, papers,
corpora, and datasets retain their own licenses and terms.
