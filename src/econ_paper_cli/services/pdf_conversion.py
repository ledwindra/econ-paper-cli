"""Pure deterministic conversion of detected early PDF sections."""

import re
from dataclasses import dataclass

from econ_paper_cli.domain.errors import PDFConversionValidationError
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.pdf_conversion import (
    PassageProvenance,
    PassageSourceFragment,
    PDFConversionResult,
    PDFConversionSettings,
    PDFConversionStatus,
    compute_conversion_paper_id,
    compute_conversion_passage_id,
    compute_conversion_settings_fingerprint,
)
from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_sections import (
    PDFSection,
    PDFSectionDetectionResult,
    PDFSectionKind,
)

_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n+")
_SECTION_HEADINGS = {
    PDFSectionKind.ABSTRACT: "Abstract",
    PDFSectionKind.INTRODUCTION: "Introduction",
}


@dataclass(frozen=True, slots=True)
class _SectionSourceFragment:
    page_number: int
    source_start: int
    source_end: int
    text_start: int
    text_end: int


def convert_pdf_early_sections(
    extraction: PDFExtractionResult,
    detection: PDFSectionDetectionResult,
    *,
    content_checksum: str,
    settings: PDFConversionSettings,
) -> PDFConversionResult:
    """Create Markdown and stable passages from detected Abstract/Introduction text."""
    if not isinstance(extraction, PDFExtractionResult):
        raise TypeError("extraction must be a PDFExtractionResult instance.")
    if not isinstance(detection, PDFSectionDetectionResult):
        raise TypeError("detection must be a PDFSectionDetectionResult instance.")
    if not isinstance(settings, PDFConversionSettings):
        raise TypeError("settings must be a PDFConversionSettings instance.")
    if detection.policy_version != settings.section_policy_version:
        # The conversion settings are what get fingerprinted and persisted,
        # so labelling a v1 detection's output as conversion input v2 (or
        # vice versa) would store a record under an identity that does not
        # describe how its text was actually produced.
        raise PDFConversionValidationError(
            f"Section detection policy '{detection.policy_version}' does not "
            f"match the conversion settings' section_policy_version "
            f"'{settings.section_policy_version}'."
        )

    paper_id = compute_conversion_paper_id(content_checksum)
    fingerprint = compute_conversion_settings_fingerprint(settings)
    section_fragments = _validate_and_reconstruct_sections(extraction, detection)

    if not detection.sections:
        return PDFConversionResult(
            status=PDFConversionStatus.NO_USABLE_SECTIONS,
            content_checksum=content_checksum,
            settings=settings,
            settings_fingerprint=fingerprint,
            paper_id=paper_id,
            markdown=None,
            passages=(),
            passage_provenance=(),
        )

    passages: list[Passage] = []
    provenance_records: list[PassageProvenance] = []
    for section, source_fragments in zip(
        detection.sections, section_fragments, strict=True
    ):
        for range_start, range_end in _segment_text(
            section.text, settings.max_passage_characters
        ):
            text = section.text[range_start:range_end]
            ordinal = len(passages)
            passage_id = compute_conversion_passage_id(
                paper_id=paper_id,
                settings_fingerprint=fingerprint,
                section_kind=section.kind,
                ordinal_position=ordinal,
                text=text,
            )
            fragments = _passage_fragments(
                source_fragments, range_start=range_start, range_end=range_end
            )
            provenance = PassageProvenance(
                passage_id=passage_id,
                section_kind=section.kind,
                fragments=fragments,
            )
            passages.append(
                Passage(
                    passage_id=passage_id,
                    paper_id=paper_id,
                    text=text,
                    section_heading=_SECTION_HEADINGS[section.kind],
                    page_start=fragments[0].page_number,
                    page_end=fragments[-1].page_number,
                    ordinal_position=ordinal,
                )
            )
            provenance_records.append(provenance)

    markdown = _render_markdown(extraction, detection.sections)
    return PDFConversionResult(
        status=PDFConversionStatus.SUCCESS,
        content_checksum=content_checksum,
        settings=settings,
        settings_fingerprint=fingerprint,
        paper_id=paper_id,
        markdown=markdown,
        passages=tuple(passages),
        passage_provenance=tuple(provenance_records),
    )


def _validate_and_reconstruct_sections(
    extraction: PDFExtractionResult,
    detection: PDFSectionDetectionResult,
) -> tuple[tuple[_SectionSourceFragment, ...], ...]:
    pages = {page.page_number: page.text for page in extraction.pages}
    result: list[tuple[_SectionSourceFragment, ...]] = []
    previous_section_end: tuple[int, int] | None = None

    for section in detection.sections:
        fragments: list[_SectionSourceFragment] = []
        reconstructed: list[str] = []
        text_offset = 0
        previous_span_end: tuple[int, int] | None = None

        for span in section.spans:
            if span.page_number not in pages:
                raise PDFConversionValidationError(
                    f"section span references missing page {span.page_number}."
                )
            page_text = pages[span.page_number]
            if span.end_character_offset > len(page_text):
                raise PDFConversionValidationError(
                    f"section span on page {span.page_number} exceeds page text length."
                )
            current_start = (span.page_number, span.start_character_offset)
            if previous_span_end is not None and current_start < previous_span_end:
                raise PDFConversionValidationError(
                    "section spans must be ordered and non-overlapping."
                )
            slice_text = page_text[
                span.start_character_offset : span.end_character_offset
            ]
            text_end = text_offset + len(slice_text)
            fragments.append(
                _SectionSourceFragment(
                    page_number=span.page_number,
                    source_start=span.start_character_offset,
                    source_end=span.end_character_offset,
                    text_start=text_offset,
                    text_end=text_end,
                )
            )
            reconstructed.append(slice_text)
            text_offset = text_end
            previous_span_end = (span.page_number, span.end_character_offset)

        if "".join(reconstructed) != section.text:
            raise PDFConversionValidationError(
                f"reconstructed {section.kind.value} text does not match detection content."
            )
        section_start = (
            section.spans[0].page_number,
            section.spans[0].start_character_offset,
        )
        if previous_section_end is not None and section_start < previous_section_end:
            raise PDFConversionValidationError(
                "detected sections must be ordered and non-overlapping."
            )
        previous_section_end = (
            section.spans[-1].page_number,
            section.spans[-1].end_character_offset,
        )
        result.append(tuple(fragments))

    return tuple(result)


def _render_markdown(
    extraction: PDFExtractionResult, sections: tuple[PDFSection, ...]
) -> str:
    metadata_title = extraction.metadata.title
    title = (
        metadata_title.strip()
        if metadata_title is not None and metadata_title.strip()
        else extraction.source_path.stem
    )
    markdown = f"# {title}"
    pages = {page.page_number: page.text for page in extraction.pages}
    for section in sections:
        text_parts: list[str] = []
        previous_page: int | None = None
        for span in section.spans:
            slice_text = pages[span.page_number][
                span.start_character_offset : span.end_character_offset
            ]
            if not slice_text:
                continue
            if previous_page is not None and span.page_number != previous_page:
                if text_parts and not text_parts[-1].endswith("\n"):
                    text_parts.append("\n")
                text_parts.append(f"<!-- econpapers-page: {span.page_number} -->\n")
            text_parts.append(slice_text)
            previous_page = span.page_number
        if markdown.endswith("\n\n"):
            separator = ""
        elif markdown.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        markdown += (
            f"{separator}## {_SECTION_HEADINGS[section.kind]}\n\n{''.join(text_parts)}"
        )
    return markdown


def _segment_text(text: str, budget: int) -> tuple[tuple[int, int], ...]:
    paragraph_ranges = _paragraph_ranges(text)
    packed_ranges: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end = 0

    for paragraph_start, paragraph_end in paragraph_ranges:
        if paragraph_end - paragraph_start > budget:
            if current_start is not None:
                packed_ranges.append((current_start, current_end))
                current_start = None
            packed_ranges.extend(
                _split_oversized_range(text, paragraph_start, paragraph_end, budget)
            )
            continue

        if current_start is None:
            current_start = paragraph_start
            current_end = paragraph_end
        elif paragraph_end - current_start <= budget:
            current_end = paragraph_end
        else:
            packed_ranges.append((current_start, current_end))
            current_start = paragraph_start
            current_end = paragraph_end

    if current_start is not None:
        packed_ranges.append((current_start, current_end))
    return tuple(
        (range_start, range_end)
        for range_start, range_end in packed_ranges
        if text[range_start:range_end].strip()
    )


def _paragraph_ranges(text: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for match in _PARAGRAPH_BREAK_RE.finditer(text):
        ranges.append((start, match.end()))
        start = match.end()
    if start < len(text):
        ranges.append((start, len(text)))
    return tuple(ranges)


def _split_oversized_range(
    text: str, start: int, end: int, budget: int
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > budget:
        hard_end = cursor + budget
        split_at = hard_end
        for index in range(hard_end - 1, cursor, -1):
            if text[index].isspace() and text[cursor : index + 1].strip():
                split_at = index + 1
                break
        ranges.append((cursor, split_at))
        cursor = split_at
    if cursor < end:
        ranges.append((cursor, end))
    return tuple(ranges)


def _passage_fragments(
    source_fragments: tuple[_SectionSourceFragment, ...],
    *,
    range_start: int,
    range_end: int,
) -> tuple[PassageSourceFragment, ...]:
    fragments: list[PassageSourceFragment] = []
    passage_offset = 0
    for source in source_fragments:
        intersection_start = max(range_start, source.text_start)
        intersection_end = min(range_end, source.text_end)
        if intersection_start >= intersection_end:
            continue
        source_start = source.source_start + intersection_start - source.text_start
        source_end = source.source_start + intersection_end - source.text_start
        length = source_end - source_start
        fragments.append(
            PassageSourceFragment(
                page_number=source.page_number,
                start_character_offset=source_start,
                end_character_offset=source_end,
                passage_start_character_offset=passage_offset,
                passage_end_character_offset=passage_offset + length,
            )
        )
        passage_offset += length
    return tuple(fragments)
