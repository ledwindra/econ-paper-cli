"""Pure deterministic section detection for Abstract and Introduction sections."""

import re

from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_sections import (
    _WARNING_ORDER,
    PDFSection,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSettings,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
)

_ABSTRACT_HEADING_RE = re.compile(
    r"^\s*(?:(?:1|I|A)[\.\:]?\s+)?ABSTRACT[\.\:]?\s*$",
    re.IGNORECASE,
)
_INTRODUCTION_HEADING_RE = re.compile(
    r"^\s*(?:(?:1|1\.0|I)[\.\:]?\s+)?INTRODUCTION[\.\:]?\s*$",
    re.IGNORECASE,
)
_NEXT_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:[2-9]|\d{2,}|II|III|IV|V|VI|VII|VIII|IX|X)[\.\:]?\s+[A-Z0-9]",
    re.IGNORECASE,
)
_TOC_DOT_LEADERS_RE = re.compile(r"\.{4,}")


class _LineInfo:
    __slots__ = (
        "page_number",
        "start_offset",
        "end_offset",
        "line_end_offset",
        "text",
        "trimmed",
    )

    def __init__(
        self,
        page_number: int,
        start_offset: int,
        end_offset: int,
        line_end_offset: int,
        text: str,
        trimmed: str,
    ) -> None:
        self.page_number = page_number
        self.start_offset = start_offset
        self.end_offset = end_offset
        self.line_end_offset = line_end_offset
        self.text = text
        self.trimmed = trimmed


def detect_pdf_sections(
    extraction: PDFExtractionResult,
    *,
    settings: PDFSectionSettings,
) -> PDFSectionDetectionResult:
    """Detect Abstract and Introduction sections deterministically from extraction results."""
    if not isinstance(extraction, PDFExtractionResult):
        raise TypeError("extraction must be a PDFExtractionResult instance.")
    if not isinstance(settings, PDFSectionSettings):
        raise TypeError("settings must be a PDFSectionSettings instance.")

    if extraction.page_count == 0:
        return PDFSectionDetectionResult(
            policy_version=settings.policy_version,
            sections=(),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),),
        )

    all_lines = _extract_lines(extraction)
    has_text = any(line.trimmed for line in all_lines)
    if not has_text:
        return PDFSectionDetectionResult(
            policy_version=settings.policy_version,
            sections=(),
            warnings=(
                PDFSectionWarning(
                    PDFSectionWarningCode.ALL_PAGES_EMPTY,
                    tuple(page.page_number for page in extraction.pages),
                ),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
            ),
        )

    abstract_candidates = _find_heading_candidates(all_lines, _ABSTRACT_HEADING_RE)
    intro_candidates = _find_heading_candidates(all_lines, _INTRODUCTION_HEADING_RE)

    warnings_list: list[PDFSectionWarning] = []
    sections_list: list[PDFSection] = []

    selected_abstract = _select_candidate(
        abstract_candidates,
        PDFSectionKind.ABSTRACT,
        warnings_list,
    )

    selected_intro = _select_candidate(
        intro_candidates,
        PDFSectionKind.INTRODUCTION,
        warnings_list,
    )

    if selected_abstract is None:
        warnings_list.append(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT))

    if selected_intro is None:
        warnings_list.append(
            PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION)
        )

    # Build Abstract section if intro candidate or boundary is available
    if selected_abstract is not None:
        end_line_index = (
            selected_intro.line_index if selected_intro is not None else len(all_lines)
        )
        abstract_section = _build_section(
            kind=PDFSectionKind.ABSTRACT,
            heading_line=all_lines[selected_abstract.line_index],
            start_line_index=selected_abstract.line_index + 1,
            end_line_index=end_line_index,
            all_lines=all_lines,
            extraction=extraction,
        )
        if abstract_section is not None:
            sections_list.append(abstract_section)

    # Build Introduction section
    if selected_intro is not None:
        next_section_candidate = _find_next_section_candidate(
            all_lines, start_index=selected_intro.line_index + 1
        )
        if next_section_candidate is not None:
            end_line_index = next_section_candidate.line_index
        else:
            end_line_index = len(all_lines)
            warnings_list.append(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_NEXT_SECTION_BOUNDARY)
            )

        intro_section = _build_section(
            kind=PDFSectionKind.INTRODUCTION,
            heading_line=all_lines[selected_intro.line_index],
            start_line_index=selected_intro.line_index + 1,
            end_line_index=end_line_index,
            all_lines=all_lines,
            extraction=extraction,
        )
        if intro_section is not None:
            sections_list.append(intro_section)

    # Order warnings canonically
    warnings_list.sort(key=lambda w: _WARNING_ORDER[w.code])

    return PDFSectionDetectionResult(
        policy_version=settings.policy_version,
        sections=tuple(sections_list),
        warnings=tuple(warnings_list),
    )


class _CandidateMatch:
    __slots__ = ("line_index", "line")

    def __init__(self, line_index: int, line: _LineInfo) -> None:
        self.line_index = line_index
        self.line = line


def _extract_lines(extraction: PDFExtractionResult) -> list[_LineInfo]:
    lines: list[_LineInfo] = []
    for page in extraction.pages:
        page_text = page.text
        page_len = len(page_text)
        offset = 0
        while offset < page_len:
            next_newline = page_text.find("\n", offset)
            if next_newline == -1:
                line_end = page_len
                next_offset = page_len
            else:
                line_end = next_newline
                next_offset = next_newline + 1
            raw_text = page_text[offset:line_end]
            if raw_text.endswith("\r"):
                content_end = line_end - 1
                raw_text = raw_text[:-1]
            else:
                content_end = line_end
            trimmed = raw_text.strip()
            lines.append(
                _LineInfo(
                    page_number=page.page_number,
                    start_offset=offset,
                    end_offset=content_end,
                    line_end_offset=next_offset,
                    text=raw_text,
                    trimmed=trimmed,
                )
            )
            offset = next_offset
    return lines


def _find_heading_candidates(
    lines: list[_LineInfo], pattern: re.Pattern[str]
) -> list[_CandidateMatch]:
    candidates: list[_CandidateMatch] = []
    for idx, line in enumerate(lines):
        if not line.trimmed or len(line.trimmed) > 80:
            continue
        if _TOC_DOT_LEADERS_RE.search(line.text):
            continue
        if pattern.match(line.trimmed):
            candidates.append(_CandidateMatch(idx, line))
    return candidates


def _find_next_section_candidate(
    lines: list[_LineInfo], start_index: int
) -> _CandidateMatch | None:
    for idx in range(start_index, len(lines)):
        line = lines[idx]
        if not line.trimmed or len(line.trimmed) > 100:
            continue
        if _TOC_DOT_LEADERS_RE.search(line.text):
            continue
        if _NEXT_SECTION_HEADING_RE.match(line.trimmed):
            return _CandidateMatch(idx, line)
    return None


def _select_candidate(
    candidates: list[_CandidateMatch],
    kind: PDFSectionKind,
    warnings: list[PDFSectionWarning],
) -> _CandidateMatch | None:
    if not candidates:
        return None

    if len(candidates) > 1:
        pages = tuple(sorted(set(c.line.page_number for c in candidates)))
        if kind == PDFSectionKind.ABSTRACT:
            warnings.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.DUPLICATE_ABSTRACT_CANDIDATES, pages
                )
            )
        else:
            warnings.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.DUPLICATE_INTRODUCTION_CANDIDATES, pages
                )
            )

    return candidates[0]


def _build_section(
    kind: PDFSectionKind,
    heading_line: _LineInfo,
    start_line_index: int,
    end_line_index: int,
    all_lines: list[_LineInfo],
    extraction: PDFExtractionResult,
) -> PDFSection | None:
    if start_line_index >= len(all_lines) or start_line_index >= end_line_index:
        # Empty section body
        return None

    lines_slice = all_lines[start_line_index:end_line_index]
    if not lines_slice:
        return None

    # Group lines by page to construct continuous spans per page
    spans_by_page: dict[int, list[tuple[int, int]]] = {}
    for line in lines_slice:
        p_num = line.page_number
        if p_num not in spans_by_page:
            spans_by_page[p_num] = []
        spans_by_page[p_num].append((line.start_offset, line.line_end_offset))

    # Build spans tuple
    spans_list: list[PDFSectionSpan] = []
    text_parts: list[str] = []

    page_map = {page.page_number: page.text for page in extraction.pages}

    for p_num in sorted(spans_by_page.keys()):
        page_text = page_map[p_num]
        offsets = spans_by_page[p_num]
        min_start = offsets[0][0]
        max_end = offsets[-1][1]

        # Clamp max_end to page_text length if needed
        max_end = min(max_end, len(page_text))
        if min_start < max_end:
            spans_list.append(
                PDFSectionSpan(
                    page_number=p_num,
                    start_character_offset=min_start,
                    end_character_offset=max_end,
                )
            )
            text_parts.append(page_text[min_start:max_end])

    if not spans_list:
        return None

    section_text = "".join(text_parts)
    return PDFSection(
        kind=kind,
        heading_text=heading_line.trimmed,
        start_page_number=spans_list[0].page_number,
        end_page_number=spans_list[-1].page_number,
        spans=tuple(spans_list),
        text=section_text,
    )
