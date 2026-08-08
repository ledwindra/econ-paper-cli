# AGENTS.md

This file defines how coding agents must work in the `econ-paper-cli` repository.

## Mission

Build a free, open-source, local-first conversational literature search tool for economists.

The product should answer research questions with concise synthesis and inspectable evidence while running without paid cloud services.

## Source-of-truth order

When instructions conflict, use this priority:

1. The active GitHub issue and explicit maintainer instructions
2. `docs/product-requirements.md`
3. `docs/architecture.md`
4. `docs/roadmap.md`
5. This file
6. Existing implementation conventions

Do not silently reinterpret product requirements. Ask for clarification when a requested change would alter product scope, architecture, legal posture, or user-facing behavior.

## Required workflow

For every issue:

1. Read the entire issue and relevant documentation.
2. Inspect existing code and tests before proposing changes.
3. State a compact implementation plan.
4. Implement the smallest complete change that satisfies the issue.
5. Add or update tests.
6. Update documentation when behavior changes.
7. Run all applicable checks.
8. Summarize what changed, why, and any remaining risks.

Work on one issue per pull request.

Do not mix unrelated refactors, formatting changes, or cleanup into feature work.

## Product guardrails

Do not:

- introduce a required paid API;
- require OpenAI, Anthropic, or another hosted model provider;
- require Docker;
- require a GPU;
- redistribute copyrighted paper full text without documented permission;
- weaken citation or evidence traceability;
- collect telemetry by default;
- upload user queries, documents, or indexes without explicit opt-in;
- redesign the architecture when a narrow interface extension is sufficient.

A cloud backend may only be added later as an optional adapter. It must never become necessary for the default workflow.

## Architecture rules

- Keep domain types and protocols independent of concrete inference and retrieval libraries.
- Put orchestration in service modules, not in CLI handlers.
- Keep filesystem and network effects behind adapters.
- Prefer dependency injection over global state.
- Preserve deterministic, testable interfaces.
- Keep the CLI thin.
- Represent evidence explicitly; do not pass unstructured strings when source identity and passage boundaries matter.
- Keep retrieval, generation, corpus ingestion, and artifact management separable.
- Do not bind core logic directly to FAISS, a database, `llama.cpp`, or a specific embedding model.
- Model downloads, corpus downloads, and index updates must eventually be resumable or safely restartable.
- Every downloaded artifact must eventually have a manifest entry and checksum verification.

## Python conventions

- Support Python 3.10 and newer unless the product requirements change.
- Use type hints for public functions and methods.
- Prefer the standard library when it is adequate.
- Add dependencies only when they materially reduce risk or implementation complexity.
- Use `pathlib.Path` for filesystem paths.
- Use explicit exceptions with actionable messages.
- Keep functions focused and side effects visible.
- Avoid broad `except Exception` blocks unless re-raising with useful context.
- Do not suppress type, lint, or test errors without an explanation.
- Keep platform-specific code isolated and tested.

## Retrieval requirements

Retrieval code must:

- return structured evidence objects;
- preserve paper identity;
- preserve passage boundaries;
- expose ranking scores;
- support deterministic tests;
- avoid duplicate or near-duplicate passages;
- allow later replacement of the retrieval backend;
- separate indexing from query-time retrieval.

Do not claim that a retrieval method is superior without a benchmark against representative economics questions.

## Generation requirements

Generation code must:

- use only supplied evidence for substantive claims;
- return structured evidence references;
- support abstention when evidence is insufficient;
- distinguish descriptive findings from causal findings;
- preserve uncertainty and disagreement;
- avoid inventing paper titles, authors, methods, or results;
- allow the local model backend to be replaced without changing application services.

Generated citation identifiers must be validated against retrieved evidence before an answer is shown to the user.

## Corpus and licensing requirements

Before adding a corpus, model, or index artifact, document:

- source;
- license;
- redistribution status;
- expected file size;
- checksum;
- update policy;
- whether it contains copyrighted full text.

Do not commit:

- NBER PDFs;
- converted copyrighted papers;
- model weights;
- user-generated indexes;
- cached downloads;
- restricted datasets.

When redistribution rights are unclear, stop and ask the maintainer.

## Testing

Changed behavior must have tests.

Prefer:

- unit tests for domain logic;
- adapter contract tests;
- temporary directories for filesystem behavior;
- deterministic fixtures;
- synthetic or redistributable text;
- no network calls in the default test suite.

Before finishing, run:

```bash
ruff check .
ruff format --check .
pytest
```

If a command cannot run, state exactly why.

Tests must not download models, papers, or indexes unless explicitly marked as integration or release tests.

## Pre-PR contract self-review

Before opening or updating a pull request, reread the issue's exact scope and behavioral requirements sentence by sentence against the diff. Green CI is necessary, not sufficient — reviews on this repository have repeatedly found contract-level gaps that still pass every test:

- Verify every "once", "fixed", "snapshot", "read-only", or "immutable" claim is literally true: find every later read of that data and confirm none of them re-hit a live source (open connection, filesystem, config backend) instead of the captured state.
- Verify every "even when X" / "regardless of Y" clause has a regression covering exactly that combination, not just the common case.
- Do not collapse distinct outcomes the issue lists (for example, typed vs. internal failure) into one generic result, and do not let exception-handling branches silently drop fields that should still be populated on a failure path.
- Isolate tests from the real machine: inject storage/config backends, or set `ECONPAPERS_CONFIG_DIR`/`ECONPAPERS_LIBRARY_DIR`, rather than relying on defaults that resolve to the developer's actual home directory.
- Do not assert that a literal path string survives unchanged; `Path.resolve()` and other canonicalization can change it, especially on Windows, where a POSIX-style literal is not a real absolute path.

## Git discipline

- Use small, descriptive commits.
- Keep one issue per pull request.
- Do not rewrite unrelated history.
- Do not modify lockfiles unless dependencies changed.
- Never commit secrets, tokens, cookies, or personal paths.
- Do not commit model files, paper PDFs, local indexes, caches, or virtual environments.
- Inspect the final diff before completion.
- Keep pull requests narrowly reviewable.

## Documentation

Update documentation when changing:

- CLI commands;
- configuration;
- supported platforms;
- artifact layout;
- corpus behavior;
- model behavior;
- retrieval behavior;
- installation;
- privacy or network behavior.

Documentation should describe current behavior, not merely intended future behavior.

## Definition of done

An issue is complete only when:

- acceptance criteria are satisfied;
- tests cover the new behavior;
- lint and tests pass;
- documentation is updated;
- Windows, macOS, and Linux implications were considered;
- failure modes produce actionable messages;
- no prohibited cloud or corpus dependency was introduced;
- the final diff contains no unrelated changes.

## Decisions requiring maintainer approval

Ask before:

- changing CLI command names;
- changing the default local-model family;
- selecting a corpus redistribution strategy;
- adopting a database or vector-store dependency;
- introducing automatic model downloads;
- changing the package name;
- changing the license;
- adding telemetry;
- adding hosted inference;
- adding web search;
- changing the supported Python versions;
- changing the evidence citation format.

## Agent responsibilities

The maintainer defines product direction and approves major decisions.

The coding agent:

- implements scoped issues;
- preserves architecture and constraints;
- adds tests;
- reports uncertainty;
- avoids making product decisions by implication;
- automatically schedules background monitoring for open PR reviews when requested, without requiring explicit user scheduling prompts or slash commands.

When requirements are incomplete, prefer clarification over architectural invention.
