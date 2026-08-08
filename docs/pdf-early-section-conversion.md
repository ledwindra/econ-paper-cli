# PDF early-section conversion

Issue 46 adds a pure application service that converts already detected
Abstract and Introduction sections into inspectable Markdown and stable
retrieval-ready passages. It does not perform extraction, section detection,
OCR, persistence, indexing, model inference, subprocess execution, or network
access. **[current]**; indexing happens later and in memory, and a persisted
index is **[planned]**.

## Public contract

`convert_pdf_early_sections` accepts a successful `PDFExtractionResult`, its
corresponding `PDFSectionDetectionResult`, a canonical lowercase SHA-256 source
checksum, and explicit `PDFConversionSettings`. The v1 policy is
`early-section-markdown-v1`; its default passage budget is 1,200 characters.
The budget is configurable and is included in the deterministic settings
fingerprint.

The detector result does not contain a source path, so conversion does not
claim literal path equality between its inputs. Instead, it establishes
correspondence by requiring every section page to exist, every page-local span
to be in bounds, all spans and sections to be ordered and non-overlapping, and
the exact concatenation of source slices to equal the detector's stored section
text.

The result is terminal and typed:

- `success` contains non-empty Markdown, ordered `Passage` objects, and one
  aligned provenance record per passage;
- `no_usable_sections` contains no Markdown, passages, or passage provenance.

The immutable conversion result validates its settings fingerprint,
checksum-derived paper identity, zero-based contiguous passage ordinals,
passage identities, passage budget, section headings, page ranges, and complete
fragment accounting. Across passages, provenance must follow canonical Abstract
then Introduction progression and preserve non-overlapping source order.

## Markdown policy v1

Markdown begins with `# Title`. The title is the PDF metadata title with outer
whitespace removed when that value is non-empty; otherwise it is the source
filename stem. A rename may therefore change the fallback Markdown title, but
never the checksum-derived paper identity, passage text, or passage identities.

Detected sections remain in detector order and use canonical headings:

```markdown
## Abstract
## Introduction
```

Only detected section spans are rendered. Their wording and paragraph breaks
are preserved. At each transition to a new source page within a section, the
converter inserts a stable standalone marker immediately before the new page's
first content:

```html
<!-- econpapers-page: 2 -->
```

No marker is emitted for the first page of a section or between two spans on
the same page. Markdown headings and provenance markers do not count toward
the passage-character budget.

## Passage and identity policy v1

Each section is segmented independently. Paragraphs are packed in source order
up to `max_passage_characters`. An oversized paragraph is split at the last
whitespace boundary that fits the budget, or at the hard character boundary
when no such whitespace exists. Conversion performs no semantic splitting,
tokenization, overlap, or reordering.

`paper_id` is `paper-` followed by the exact source checksum and never includes
a path. Each `passage_id` is a SHA-256 identity over the paper ID, conversion
settings fingerprint, section kind, global zero-based ordinal, and exact
passage text. Changing bytes or conversion settings therefore changes the
affected identities deterministically.

The existing `Passage` contract remains unchanged. Separate immutable
`PassageProvenance` records contain ordered, non-overlapping
`PassageSourceFragment` values. Each fragment records its exact page-local
source offsets and corresponding passage-text offsets. Fragment offsets are
contiguous from zero and collectively account for the complete passage text;
`page_start` and `page_end` are derived from the first and last fragments.

## Scope limits

Conversion has no fixed page limit, but it intentionally converts only the
detected Abstract and Introduction. It does not create full-document Markdown,
write files or SQLite records, build a retrieval index, inspect PDF bytes, or
validate the checksum against a filesystem source. **[current]**.
