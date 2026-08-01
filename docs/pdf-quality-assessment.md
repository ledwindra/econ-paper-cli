# PDF extraction-quality assessment

Issue 32 defines a fully local, pure assessment of successful
`PDFExtractionResult` values. It does not run a parser or OCR, read files, write
application state, or alter extracted text. Parser failures remain the typed
errors defined by the `PDFExtractor` protocol.

Call `assess_pdf_extraction_quality` with an extraction result and an explicit
`PDFQualitySettings` instance. The assessment records the settings'
`policy_version`; changing a threshold or measurement rule requires a new
policy version so later re-ingestion can reproduce the decision. Each
`policy_version` is bound to an immutable threshold set; attempting to reuse
a `policy_version` with different thresholds raises a `PDFQualityValidationError`.

## Default policy

The default `pdf-extraction-quality-v1` policy uses these thresholds:

| Setting | Default | Boundary behavior |
| --- | ---: | --- |
| Sparse page text | 80 non-whitespace characters | A non-empty page below 80 is sparse. |
| Very low document text | 200 non-whitespace characters | A non-empty document below 200 receives a warning. |
| High empty-page ratio | 0.50 | A non-total ratio at or above 0.50 likely needs OCR. |
| Character-anomaly warning ratio | 0.01 | A control- or replacement-character ratio at or above 0.01 receives a warning. |
| Character-anomaly unusable ratio | 0.10 | A control- or replacement-character ratio at or above 0.10 is unusable. |
| Repeated-character run | 12 characters | A non-whitespace run of at least 12 characters is measured and warned. |
| Repeated-character unusable ratio | 0.20 | Repeated-run characters at or above 0.20 of non-whitespace text are unusable. |
| Minimum pages for imbalance | 4 pages | Page imbalance is assessed only at or above 4 pages. |
| Severe page imbalance | 0.80 | A page containing at least 0.80 of document non-whitespace text receives a warning. |

Ratios are derived from validated integer counts. Empty- and sparse-page ratios
use total page count; control- and replacement-character ratios use total
character count; repeated-character and maximum-page ratios use total
non-whitespace character count. A zero denominator produces `0.0`.

A page is empty when it contains no non-whitespace characters. Suspicious
controls are C0 characters except tab, line feed, carriage return, and form
feed, plus DEL and the C1 range. This explicit code-point rule avoids depending
on platform or Unicode-database classifications. Printable counts exclude
those controls and the four structural whitespace characters. Repeated-run
measurement ignores whitespace runs and never rewrites the source text.
Control-, replacement-, and repeated-character counts are bounded by
`non_whitespace_character_count`.

## Status precedence

Status selection is deterministic and follows this precedence:

1. A zero-page result or severe character garbage is `unusable`.
2. A nonzero all-empty result or a high non-total empty-page ratio is
   `likely_needs_ocr`.
3. Any other warning produces `usable_with_warnings`.
4. A result without warnings is `usable`.

Warnings use stable snake-case codes and canonical enum order. Page-specific
warnings carry unique ascending source page numbers. All models are immutable,
and document measurements are validated against the ordered page observations.

The defaults are intentionally conservative: warnings and statuses guide later
orchestration, but assessment never discards, normalizes, or rewrites extracted
text. OCR execution, parser retry, conversion, segmentation, persistence, and
CLI behavior remain separate later work.
