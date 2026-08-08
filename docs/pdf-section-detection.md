# PDF section detection

Issue 34 defines a fully local, pure section-detection service for `PDFExtractionResult` instances. It identifies early paper sections critical for downstream extraction—specifically **Abstract** and **Introduction**—without running OCR, parsing PDFs, reading files, or writing database records.

Call `detect_pdf_sections` with a `PDFExtractionResult` and an explicit `PDFSectionSettings` instance.

## Detection and ranking rules

Detection relies on document structure and heading boundaries rather than fixed page-number assumptions.

- **Abstract Heading**: Recognized case-insensitively from line-like headings matching `Abstract`, `1. Abstract`, or `I. Abstract`. Abstract text starts on the line following the heading and ends at the Introduction heading.
- **Introduction Heading**: Recognized case-insensitively from line-like headings matching `Introduction`, `1 Introduction`, `1. Introduction`, `1.0 Introduction`, `I. Introduction`, or `I. INTRODUCTION`. Introduction text starts on the line following the heading and ends at the next top-level section heading.
- **Next Top-Level Section Heading**: Top-level section headings following the Introduction (e.g. `2. Data`, `2 Data`, `2. Model`, `II. Methodology`) serve as the boundary ending the Introduction section.
- **Prose vs. Headings**: Words "abstract" or "introduction" embedded inside prose sentences are ignored. Numbered prose sentences or list items starting with numbers/numerals (e.g. `2 million households received...` or `2. We next estimate the model.`) are filtered out and do not truncate the Introduction section.
- **Table of Contents and Running Headers**: Dot-leader lines (e.g. `1. Introduction ........... 4`) and repeated running headers across page margins are ranked as weak candidates.
- **Candidate Ranking & Ambiguity**: Candidates are scored by structural strength. If top-ranked body candidates tie in strength, an `ambiguous_abstract_candidates` or `ambiguous_introduction_candidates` warning is emitted and no section is arbitrarily selected.
- **Front matter precedes the body**: an `Abstract` line positioned *after* the first plausible top-level body heading is scored 0 and can never be selected. It stays in `candidates` for provenance. Observed on a real 45-page AEA paper whose only `Abstract` match sat on page 19, inside the body.

## Unheaded sections (implicit detection)

Many publishers print no heading at all for the abstract, the introduction, or both — AEA articles typically run title → byline → unheaded abstract → unheaded introduction → `I. …`. Such sections are detected with `PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER`.

An implicit section never fabricates a heading: `observed_heading_text` is `None` and the boundaries are justified by a non-empty `boundary_evidence` tuple (`title_block`, `jel_classification_terminator`, `acknowledgments_start`, `first_section_heading`).

- **Unheaded abstract** is bounded by the first front-matter terminator ahead of the body: a JEL/keywords line, an acknowledgments block, or an explicit Introduction heading.
- **Inline JEL terminator**: AEA sets JEL codes at the end of the abstract's own final sentence (`…inflows of skilled labor. (JEL J24, J31, R23)`) rather than on their own line. That line is abstract prose and is kept *inside* the abstract; a line-anchored `JEL codes:` line is metadata and is kept outside. Getting this distinction wrong either truncates the abstract's last sentence or leaks a JEL fragment into the introduction.
- **Explicit abstract, no introduction heading**: the abstract's end is resolved from a front-matter terminator and an **Abstract-only** result is returned. One section is enough to make a paper searchable, so returning nothing here previously dropped the paper from the library entirely. No Introduction is inferred in this case: the text between the terminator and the first body heading is usually author footnotes and acknowledgments, not introduction prose.
- When no terminator exists there is no evidence for where the abstract stops, so `unresolved_abstract_boundary` is reported rather than guessing a boundary and filing introduction prose under the Abstract heading.

## Spans, Provenance, and Contract Invariants

Returned `PDFSection` objects contain ordered `PDFSectionSpan` instances. Each span explicitly records:
- `page_number`: 1-based source page number.
- `start_character_offset`: 0-based character index within the source page text.
- `end_character_offset`: 0-based exclusive end character index within the source page text.
- `character_count`: Total character length of the span.

Concatenating span text slices reproduces the section's exact extracted body text while preserving full source provenance. `PDFSection` validates that stored `text` length matches the sum of span character counts. `PDFSectionDetectionResult` enforces canonical policy versions, non-overlapping/ordered section spans, and cross-field warning/section consistency.

## Warnings

Missing, ambiguous, or malformed section boundaries emit stable warning codes in canonical order:
- `no_pages`: The extraction result contains zero pages.
- `all_pages_empty`: No extractable text was found on any page.
- `MISSING_ABSTRACT`: No Abstract heading candidate was detected.
- `UNRESOLVED_ABSTRACT_BOUNDARY`: An Abstract heading candidate was detected, but no front-matter terminator or top-level section boundary was found to delimit its end.
- `MISSING_INTRODUCTION`: No Introduction heading candidate was detected.
- `missing_next_section_boundary`: Introduction heading was found, but no subsequent top-level section heading was detected. Introduction extends to the end of the extracted text.
- `duplicate_abstract_candidates`: Multiple Abstract heading candidates were detected.
- `duplicate_introduction_candidates`: Multiple Introduction heading candidates were detected.
- `ambiguous_abstract_candidates`: Multiple equally plausible Abstract heading candidates were detected.
- `ambiguous_introduction_candidates`: Multiple equally plausible Introduction heading candidates were detected.

## Policy versions

`PDFSectionSettings.policy_version` names the detection contract. It is folded into
`compute_conversion_settings_fingerprint`, so a stored early-section record produced
under one policy is never reused under another — stale rows read back as `None` and are
re-converted on the next `analyze`.

- `pdf-section-detection-v1` — original explicit-heading detection.
- `pdf-section-detection-v2` — implicit front-matter inference, boundary evidence.
- `pdf-section-detection-v3` (current) — recovers papers whose Introduction is
  unheaded. An `Abstract` line after the body starts is no longer selectable; an
  inline `(JEL …)` terminator is recognized and kept inside the abstract; an explicit
  Abstract with no following Introduction heading yields an Abstract-only section
  instead of nothing.

Measured on a 532-PDF economics corpus, v3 recovered 10 previously unsearchable papers
and added a missing Abstract to 91 more, with **zero papers losing a section**.
