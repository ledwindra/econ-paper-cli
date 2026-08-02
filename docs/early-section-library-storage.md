# Early-section library storage

Issue 48 adds a durable storage boundary for successful Abstract/Introduction
conversion results. It does not connect that boundary to `econpapers analyze`.
Callers explicitly project a successful extraction, section detection, and
conversion into an `EarlySectionLibraryRecord`, then pass that record to a
`StorageBackend`.

## Record and projection contract

`project_early_section_library_record` is pure after receiving its inputs. It
accepts the extraction result, section-detection result, successful conversion,
inspected PDF size, and an injected timestamp. It repeats conversion with the
supplied checksum and settings and requires exact equality, which validates the
page structure, detected spans, reconstructed section text, Markdown, passage
order, identities, and conversion fingerprint without filesystem or network
access.

Local-PDF metadata uses the following fixed policy:

- `paper_id` is the conversion result's checksum-derived identity;
- title uses the trimmed non-empty PDF metadata title, otherwise the filename
  stem;
- non-empty `author_text` is preserved verbatim as one author entry, while
  missing or blank metadata becomes an empty author tuple;
- year and source URL are unknown (`None`);
- Abstract is the exact detected Abstract text when present;
- source name is `local-pdf`; and
- source identifier is the lowercase PDF checksum.

Generated Markdown is database content. An early-section record therefore has
no external `markdown_path`. Legacy `PaperRecord` values may retain a valid
path, and `SourceProvenance.markdown_path` is optional only to support these two
representations honestly.

## Restart-time provenance validation

Each stored passage provenance record contains ordered page-local fragments.
Schema version 4 also stores the exact source text of each fragment. On read,
the adapter reconstructs the immutable record and verifies that every fragment
text equals the corresponding passage slice and that source and passage
offsets, pages, sections, ordinals, identities, and settings remain consistent.
Missing, corrupt, reordered, or ungrounded rows raise `StorageValidationError`;
the adapter never returns a partial record.

This is deliberately narrower than revalidating against the full extraction.
The database does not retain complete extracted page text, so restart-time
validation proves persisted fragment-to-passage integrity, not correspondence
to page text that is no longer present. Write-time projection performs the full
check against the supplied extraction pages.

## Schema version 4 and replacement

The version-4 migration:

- makes `source_provenance.markdown_path` nullable and adds parser version;
- preserves existing non-null legacy Markdown paths;
- stores conversion policy, settings fingerprint, maximum passage size, and
  generated Markdown;
- stores one ordered provenance row per passage; and
- stores ordered fragments with page number, source offsets, passage offsets,
  and exact source text.

Early-section records reuse `papers` and `passages`. Consequently,
`load_corpus()` exposes their passages in deterministic paper/ordinal order and
the reopened corpus can be supplied directly to `BM25Retriever`. No BM25 token
statistics or other retrieval-index state is persisted.

Saving a record uses one `BEGIN IMMEDIATE` transaction. A checksum already
owned by another `paper_id` is rejected. Replacing the same checksum-derived
paper deletes stale shared rows and all old fragments through cascading foreign
keys before inserting the complete new representation. Any failure rolls back
the replacement. The transaction preserves the original durable `created_at`
and writes the newly projected `updated_at`; a new record initially uses the
injected timestamp for both. Strict read-back occurs before commit.

The storage protocol exposes explicit save, get, list, and delete operations
for `EarlySectionLibraryRecord`. The existing generic paper-record and
single-paper-analysis operations remain available. No Markdown file, PDF copy,
retrieval index, model call, subprocess, or network request is created by this
workflow.
