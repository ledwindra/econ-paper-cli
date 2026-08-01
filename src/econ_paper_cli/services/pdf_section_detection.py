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

_NEXT_SECTION_TITLE_RE = re.compile(
    r"^\s*(?:[2-9]|\d{2,}|II|III|IV|V|VI|VII|VIII|IX|X)[\.\:]?\s+(.+)$",
    re.IGNORECASE,
)
_TOC_DOT_LEADERS_RE = re.compile(r"\.{4,}")

_PROSE_FIRST_WORDS = frozenset(
    {
        "we",
        "they",
        "i",
        "he",
        "she",
        "it",
        "you",
        "our",
        "their",
        "my",
        "your",
        "this",
        "these",
        "that",
        "those",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "million",
        "billion",
        "thousand",
        "percent",
        "pct",
        "show",
        "shows",
        "showed",
        "estimate",
        "estimates",
        "estimated",
        "find",
        "finds",
        "found",
        "present",
        "presents",
        "presented",
        "next",
        "first",
        "second",
        "third",
        "finally",
        "moreover",
        "however",
    }
)


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


class _CandidateMatch:
    __slots__ = ("line_index", "line", "score")

    def __init__(self, line_index: int, line: _LineInfo, score: int) -> None:
        self.line_index = line_index
        self.line = line
        self.score = score


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

    running_headers = _detect_running_headers(all_lines, extraction.page_count)

    abstract_candidates = _find_heading_candidates(
        all_lines, _ABSTRACT_HEADING_RE, running_headers
    )
    intro_candidates = _find_heading_candidates(
        all_lines, _INTRODUCTION_HEADING_RE, running_headers
    )

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

    if selected_abstract is None and not any(
        w.code
        in {
            PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES,
            PDFSectionWarningCode.DUPLICATE_ABSTRACT_CANDIDATES,
        }
        for w in warnings_list
    ):
        warnings_list.append(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT))

    if selected_intro is None and not any(
        w.code
        in {
            PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES,
            PDFSectionWarningCode.DUPLICATE_INTRODUCTION_CANDIDATES,
        }
        for w in warnings_list
    ):
        warnings_list.append(
            PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION)
        )

    # Determine Abstract section boundaries
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
        elif selected_abstract is not None:
            warnings_list.append(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT)
            )

    # Determine Introduction section boundaries
    if selected_intro is not None:
        next_section_candidate = _find_next_section_candidate(
            all_lines,
            start_index=selected_intro.line_index + 1,
            running_headers=running_headers,
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
        elif selected_intro is not None:
            warnings_list.append(
                PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION)
            )

    # Handle cross-field required missing warnings when section was not formed
    sec_kinds = {s.kind for s in sections_list}
    if PDFSectionKind.ABSTRACT not in sec_kinds and not any(
        w.code is PDFSectionWarningCode.MISSING_ABSTRACT for w in warnings_list
    ):
        warnings_list.append(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT))

    if PDFSectionKind.INTRODUCTION not in sec_kinds and not any(
        w.code is PDFSectionWarningCode.MISSING_INTRODUCTION for w in warnings_list
    ):
        warnings_list.append(
            PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION)
        )

    # Sort warnings canonically
    warnings_list = _deduplicate_and_sort_warnings(warnings_list)

    return PDFSectionDetectionResult(
        policy_version=settings.policy_version,
        sections=tuple(sections_list),
        warnings=tuple(warnings_list),
    )


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


def _detect_running_headers(lines: list[_LineInfo], page_count: int) -> set[str]:
    if page_count < 2:
        return set()
    trimmed_counts: dict[str, set[int]] = {}
    for line in lines:
        if line.trimmed and len(line.trimmed) < 100:
            if line.trimmed not in trimmed_counts:
                trimmed_counts[line.trimmed] = set()
            trimmed_counts[line.trimmed].add(line.page_number)
    return {
        text
        for text, pages in trimmed_counts.items()
        if len(pages) >= 2 and len(pages) > page_count * 0.3
    }


def _find_heading_candidates(
    lines: list[_LineInfo],
    pattern: re.Pattern[str],
    running_headers: set[str],
) -> list[_CandidateMatch]:
    candidates: list[_CandidateMatch] = []
    for idx, line in enumerate(lines):
        if not line.trimmed or len(line.trimmed) > 80:
            continue
        if _TOC_DOT_LEADERS_RE.search(line.text):
            continue
        if pattern.match(line.trimmed):
            score = 2
            if line.trimmed in running_headers:
                score = 0
            # Check context: isolated line / blank surrounding lines
            prev_blank = idx == 0 or not lines[idx - 1].trimmed
            next_blank = idx == len(lines) - 1 or not lines[idx + 1].trimmed
            if prev_blank or next_blank:
                score += 1
            candidates.append(_CandidateMatch(idx, line, score))
    return candidates


def _find_next_section_candidate(
    lines: list[_LineInfo],
    start_index: int,
    running_headers: set[str],
) -> _CandidateMatch | None:
    for idx in range(start_index, len(lines)):
        line = lines[idx]
        if not line.trimmed or len(line.trimmed) > 80:
            continue
        if line.trimmed in running_headers:
            continue
        if _TOC_DOT_LEADERS_RE.search(line.text):
            continue
        if _is_genuine_next_top_level_heading(line):
            return _CandidateMatch(idx, line, 2)
    return None


def _is_genuine_next_top_level_heading(line: _LineInfo) -> bool:
    match = _NEXT_SECTION_TITLE_RE.match(line.trimmed)
    if not match:
        return False

    title_part = match.group(1).strip()
    if not title_part:
        return False

    # Check words in title
    words = title_part.split()
    if len(words) > 8:
        return False

    first_word = words[0].rstrip(".:,;").lower()
    if first_word in _PROSE_FIRST_WORDS:
        return False

    # Ensure title starts with a capital letter or number
    if not (title_part[0].isupper() or title_part[0].isdigit()):
        return False

    # Check if line ends with a period and contains full prose sentence
    if line.trimmed.endswith(".") and len(words) > 4:
        # Check if last word is a verb/clause ending
        if first_word in {"we", "they", "this", "our", "there"}:
            return False

    return True


def _select_candidate(
    candidates: list[_CandidateMatch],
    kind: PDFSectionKind,
    warnings: list[PDFSectionWarning],
) -> _CandidateMatch | None:
    if not candidates:
        return None

    all_pages = tuple(sorted(set(c.line.page_number for c in candidates)))

    # Filter out weak/running-header candidates if strong candidates exist
    max_score = max(c.score for c in candidates)
    top_candidates = [c for c in candidates if c.score == max_score]

    if max_score == 0:
        # All candidates are running headers / weak candidates
        return None

    if len(top_candidates) > 1:
        # Ambiguous: top candidates tie in score
        if kind == PDFSectionKind.ABSTRACT:
            warnings.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES, all_pages
                )
            )
        else:
            warnings.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES, all_pages
                )
            )
        return None

    # Exactly one clear top candidate
    if len(candidates) > 1:
        if kind == PDFSectionKind.ABSTRACT:
            warnings.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.DUPLICATE_ABSTRACT_CANDIDATES, all_pages
                )
            )
        else:
            warnings.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.DUPLICATE_INTRODUCTION_CANDIDATES, all_pages
                )
            )

    return top_candidates[0]


def _build_section(
    kind: PDFSectionKind,
    heading_line: _LineInfo,
    start_line_index: int,
    end_line_index: int,
    all_lines: list[_LineInfo],
    extraction: PDFExtractionResult,
) -> PDFSection | None:
    if start_line_index >= len(all_lines) or start_line_index >= end_line_index:
        return None

    lines_slice = all_lines[start_line_index:end_line_index]
    if not lines_slice:
        return None

    # Group lines by page
    spans_by_page: dict[int, list[tuple[int, int]]] = {}
    for line in lines_slice:
        p_num = line.page_number
        if p_num not in spans_by_page:
            spans_by_page[p_num] = []
        spans_by_page[p_num].append((line.start_offset, line.line_end_offset))

    spans_list: list[PDFSectionSpan] = []
    text_parts: list[str] = []

    page_map = {page.page_number: page.text for page in extraction.pages}

    for p_num in sorted(spans_by_page.keys()):
        page_text = page_map[p_num]
        offsets = spans_by_page[p_num]
        min_start = offsets[0][0]
        max_end = offsets[-1][1]

        max_end = min(max_end, len(page_text))
        if min_start < max_end:
            slice_text = page_text[min_start:max_end]
            if slice_text:
                spans_list.append(
                    PDFSectionSpan(
                        page_number=p_num,
                        start_character_offset=min_start,
                        end_character_offset=max_end,
                    )
                )
                text_parts.append(slice_text)

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


def _deduplicate_and_sort_warnings(
    warnings: list[PDFSectionWarning],
) -> list[PDFSectionWarning]:
    by_code: dict[PDFSectionWarningCode, set[int]] = {}
    for w in warnings:
        if w.code not in by_code:
            by_code[w.code] = set()
        by_code[w.code].update(w.page_numbers)

    sorted_codes = sorted(by_code.keys(), key=_WARNING_ORDER.__getitem__)
    return [
        PDFSectionWarning(code, tuple(sorted(by_code[code]))) for code in sorted_codes
    ]
