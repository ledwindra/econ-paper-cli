# Research Question Extraction

## Goal

`econ-paper-cli` extracts structured research questions from economics papers using local, deterministic section detection results (`Abstract` and `Introduction`) and a backend-independent text generator.

## Architecture

The research-question extraction service (`extract_research_question`) operates purely at the application layer:

1. **Input Validation**: Accepts a `PDFSectionDetectionResult` and a replaceable `Generator` implementation.
2. **Section Filtering**: Selects completed, usable `ABSTRACT` and `INTRODUCTION` sections in canonical order. Missing or malformed sections are excluded.
3. **Prompt Construction**: Builds a provider-independent structured extraction request with section text and provenance context.
4. **Structured Parsing & Grounding Validation**: Parses the JSON response and validates that every returned evidence excerpt:
   - matches exact source section text;
   - aligns with valid character offsets (`start_character_offset`, `end_character_offset`);
   - maps to an observed page number in the source section spans.
5. **Warning & Error Codes**:
   - `no_usable_sections`: Neither section was completed or usable; generation was skipped.
   - `missing_section`: Only one section (Abstract or Introduction) was available.
   - `generation_failed`: Generator model execution failed or timed out.
   - `malformed_structured_response`: Model response was invalid JSON or lacked required fields.
   - `ungrounded_evidence`: Returned evidence text, offsets, or page numbers could not be grounded in source section spans.

## Classification

Questions are classified as:
- `explicit`: The paper explicitly asks a question or states its research question.
- `inferred`: The question is inferred from a stated objective, method, or contribution.
- `unavailable`: Neither section is available, or generation/grounding failed.

## Local & Deterministic Operation

- Fully offline and local; requires no cloud services or paid APIs.
- Provider-independent prompt formatting and structured parsing.
- Deterministic behavior across repeated runs given identical inputs.
