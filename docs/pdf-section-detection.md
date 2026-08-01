# PDF section detection

Issue 34 defines a fully local, pure section-detection service for `PDFExtractionResult` instances. It identifies early paper sections critical for downstream extraction—specifically **Abstract** and **Introduction**—without running OCR, parsing PDFs, reading files, or writing database records.

Call `detect_pdf_sections` with a `PDFExtractionResult` and an explicit `PDFSectionSettings` instance.

## Detection rules

Detection relies on document structure and heading boundaries rather than fixed page-number assumptions.

- **Abstract Heading**: Recognized case-insensitively from line-like headings matching `Abstract`, `1. Abstract`, or `I. Abstract`. Abstract text starts on the line following the heading and ends at the Introduction heading.
- **Introduction Heading**: Recognized case-insensitively from line-like headings matching `Introduction`, `1 Introduction`, `1. Introduction`, `1.0 Introduction`, `I. Introduction`, or `I. INTRODUCTION`. Introduction text starts on the line following the heading and ends at the next top-level section heading.
- **Next Top-Level Section Heading**: Top-level section headings following the Introduction (e.g. `2. Data`, `2 Data`, `2. Model`, `II. Methodology`) serve as the boundary ending the Introduction section.
- **Prose vs. Headings**: Words "abstract" or "introduction" embedded inside prose sentences are ignored.
- **Table of Contents**: Dot-leader lines (e.g. `1. Introduction ........... 4`) are excluded from heading candidate selection.

## Spans and Provenance

Returned `PDFSection` objects contain ordered `PDFSectionSpan` instances. Each span explicitly records:
- `page_number`: 1-based source page number.
- `start_character_offset`: 0-based character index within the source page text.
- `end_character_offset`: 0-based exclusive end character index within the source page text.
- `character_count`: Total character length of the span.

Concatenating span text slices reproduces the section's exact extracted body text while preserving full source provenance.

## Warnings

Missing, ambiguous, or malformed section boundaries emit stable warning codes in canonical order:
- `no_pages`: The extraction result contains zero pages.
- `all_pages_empty`: No extractable text was found on any page.
- `missing_abstract`: No Abstract heading was detected.
- `missing_introduction`: No Introduction heading was detected.
- `missing_next_section_boundary`: Introduction heading was found, but no subsequent top-level section heading was detected. Introduction extends to the end of the extracted text.
- `duplicate_abstract_candidates`: Multiple Abstract heading candidates were detected.
- `duplicate_introduction_candidates`: Multiple Introduction heading candidates were detected.
- `ambiguous_abstract_candidates`: Multiple equally plausible Abstract heading candidates were detected.
- `ambiguous_introduction_candidates`: Multiple equally plausible Introduction heading candidates were detected.
