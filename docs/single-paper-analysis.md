# Single-Paper Research Question Analysis

## Goal

`econ-paper-cli` orchestrates single-PDF research-question analysis by connecting individual application services into a unified, deterministic workflow for a single local PDF.

## Execution Stages

The workflow executes 5 stages in deterministic order:

1. **Ingestion Preflight** (`run_ingestion_preflight`):
   - Validates PDF file existence, extension (`.pdf`), readability, non-emptiness, and computes SHA-256 checksum identity.
   - Halts with `PREFLIGHT_FAILED` if rejected.
2. **Text Extraction** (`pdf_extractor.extract`):
   - Extracts page text and metadata using the injected `PDFExtractor`.
   - Halts with `EXTRACTION_FAILED` if extraction raises an error.
3. **Quality Assessment** (`assess_pdf_extraction_quality`):
   - Measures page character counts and printable text ratio.
   - Halts with `QUALITY_HALTED` if quality status is `likely_needs_ocr` or `unusable`.
4. **Section Detection** (`detect_pdf_sections`):
   - Detects `Abstract` and `Introduction` sections deterministically.
   - Halts with `SECTION_DETECTION_HALTED` if no usable early sections are present.
5. **Research Question Extraction** (`extract_research_question`):
   - Extracts structured, evidence-backed research question through the replaceable `Generator` protocol.
   - Halts with `QUESTION_EXTRACTION_HALTED` if generation abstains, fails, or produces ungrounded evidence.

## Composite Result Structure

The service returns a `SinglePaperAnalysisResult` containing:
- `source_path`: Canonical path to the source PDF.
- `checksum`: SHA-256 hex digest of the file bytes.
- `status`: `success`, `preflight_failed`, `extraction_failed`, `quality_halted`, `section_detection_halted`, `question_extraction_halted`.
- `completed_stages` and `skipped_stages`: Explicit stage execution lists.
- `preflight_result`, `extraction_result`, `quality_assessment`, `section_result`, `research_question_result`: Typed stage outputs preserving section and page provenance.
- `warnings`: Actionable warnings explaining why a workflow stopped early.
- `error_message`: Details for preflight or extraction failures.

## Local & Offline Operation

- Operates 100% locally and offline without external network or API calls.
- Preserves full section and page offset provenance.
- Leaves source PDF files untouched.
