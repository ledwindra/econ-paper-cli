# Single-Paper Research-Question Analysis

This document describes the `analyze_single_paper` application service,
which orchestrates end-to-end analysis of a single local PDF to extract a
structured, evidence-backed research question.

## Overview

`analyze_single_paper` is a deterministic, offline orchestration service. It
accepts exactly one local PDF file path and runs five stages in canonical order:

| Stage | Service |
|---|---|
| 1. Preflight | `run_ingestion_preflight` |
| 2. Extraction | `PDFExtractor.extract` |
| 3. Quality assessment | `assess_pdf_extraction_quality` |
| 4. Section detection | `detect_pdf_sections` |
| 5. Research question extraction | `extract_research_question` |

No network calls are made. All backends are replaceable through the
`PDFExtractor` and `Generator` protocols.

## Input requirements

- `pdf_path`: path to a **single PDF file**. Directories are rejected, even if
  they contain exactly one PDF.
- `pdf_extractor`: any object implementing `protocols.pdf_extraction.PDFExtractor`.
- `generator`: any object implementing `protocols.generation.Generator`.
- `settings`: optional `SinglePaperAnalysisSettings` (defaults to
  `DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS`).

## Stage outcomes

Each stage produces one of three outcomes:

- **Completed**: the stage ran and succeeded; it is included in `completed_stages`.
- **Failed**: the stage ran but failed (`PREFLIGHT_FAILED` or `EXTRACTION_FAILED`);
  the failing stage is recorded in `failed_stage` and a stable `failure_code` is set.
- **Skipped**: the stage was not attempted because a prior stage failed or halted;
  it is included in `skipped_stages`.

The invariant `completed_stages + (failed_stage,) + skipped_stages` always
equals the canonical five-stage tuple, in order.

## Terminal statuses

| Status | Meaning |
|---|---|
| `SUCCESS` | All five stages completed. Research question extracted with evidence. |
| `PREFLIGHT_FAILED` | Ingestion preflight rejected the input (see `failure_code`). |
| `EXTRACTION_FAILED` | PDF extraction failed with a typed error (see `failure_code`). |
| `QUALITY_HALTED` | Extraction quality is `LIKELY_NEEDS_OCR` or `UNUSABLE`; downstream stages skipped. |
| `QUESTION_EXTRACTION_HALTED` | All five stages ran, but research question is `UNAVAILABLE` (no usable sections, generator abstained, malformed response, ungrounded evidence, or generation error). |

## Failure codes (`failure_code`)

Set only for `PREFLIGHT_FAILED` and `EXTRACTION_FAILED` statuses. Each code maps
deterministically to a typed exception subclass.

### Preflight failure codes

| Code | Cause |
|---|---|
| `PATH_NOT_FOUND` | File or directory does not exist |
| `PATH_INVALID` | Path is not a regular file or directory |
| `UNSUPPORTED_FILE_TYPE` | File is not a `.pdf` document |
| `DIRECTORY_INPUT` | Path is a directory (single-file contract violation) |
| `MULTI_CANDIDATE_BATCH` | Preflight unexpectedly found multiple candidates |
| `PREFLIGHT_PERMISSION_DENIED` | OS permission denied during preflight |
| `PREFLIGHT_READ_ERROR` | OS read error during preflight |

### Extraction failure codes

| Code | Cause |
|---|---|
| `PDF_NOT_FOUND` | PDF source does not exist at extraction time |
| `PDF_NOT_REGULAR_FILE` | PDF source is not a regular file |
| `PDF_PERMISSION_DENIED` | OS permission denied reading the PDF |
| `PDF_READ_ERROR` | OS read error during extraction |
| `PDF_MALFORMED` | PDF is malformed or truncated |
| `PDF_ENCRYPTED` | PDF is encrypted and cannot be opened without a password |
| `PDF_PARSER_ERROR` | Parser failed to produce a trustworthy result |

## Composite result structure

```python
SinglePaperAnalysisResult(
    policy_version="single-paper-analysis-v1",
    source_path=Path(...),  # canonical resolved candidate path
    checksum=str | None,  # SHA-256 of PDF content (None if preflight failed)
    status=SinglePaperAnalysisStatus,
    completed_stages=(...),  # successfully completed stages only
    failed_stage=... | None,  # stage that failed (PREFLIGHT/EXTRACTION only)
    skipped_stages=(...),  # stages not attempted
    failure_code=... | None,  # typed code (PREFLIGHT_FAILED / EXTRACTION_FAILED only)
    failure_cause=... | None,  # original caught typed exception, when applicable
    preflight_result=... | None,
    extraction_result=... | None,
    quality_assessment=... | None,
    section_result=... | None,
    research_question_result=... | None,
    warnings=(...),  # orchestration-level warnings
    error_message=str | None,  # human-readable error (failure statuses only)
)
```

For failures raised by preflight or extraction, `failure_cause` preserves the
original exception object and `error_message` is its human-readable string.
`DIRECTORY_INPUT` may retain `IngestionEmptyDirectoryError` when preflight
rejects an empty directory; otherwise it is a structural rejection with no
exception. `MULTI_CANDIDATE_BATCH` is always a structural guard and has
`failure_cause=None`.

## No-usable-sections handling

When the PDF contains no Abstract or Introduction sections, `extract_research_question`
is still called. It returns an `UNAVAILABLE` result with `NO_USABLE_SECTIONS` warning
(the generator is never called in this case). The terminal status is
`QUESTION_EXTRACTION_HALTED`, and the nested `research_question_result` is preserved
in the composite result.

## Unexpected exceptions

Only explicitly mapped typed `IngestionError` and `PDFExtractionError`
subclasses are translated to failure codes, with the original exception retained
in `failure_cause`. All other exceptions, including future unmapped subclasses,
propagate unchanged.

## Usage example

```python
from pathlib import Path
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper
from econ_paper_cli.adapters.pypdf_extractor import PyPDFExtractor
from econ_paper_cli.adapters.llama_cpp import LlamaCppGenerator

result = analyze_single_paper(
    pdf_path=Path("paper.pdf"),
    pdf_extractor=PyPDFExtractor(),
    generator=LlamaCppGenerator(model_path=Path("model.gguf")),
)

if result.status.value == "success":
    print(result.research_question_result.question_text)
elif result.status.value == "preflight_failed":
    print(f"Preflight failed [{result.failure_code.value}]: {result.error_message}")
elif result.status.value == "extraction_failed":
    print(f"Extraction failed [{result.failure_code.value}]: {result.error_message}")
else:
    print(f"Analysis halted at status: {result.status.value}")
    for w in result.warnings:
        print(f"  Warning [{w.code.value}]: {w.message}")
```
