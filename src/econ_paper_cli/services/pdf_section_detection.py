"""Pure deterministic section detection for Abstract and Introduction sections."""

import re

from econ_paper_cli.domain.pdf_extraction import PDFExtractionResult
from econ_paper_cli.domain.pdf_sections import (
    _WARNING_ORDER,
    PDFHeadingCandidate,
    PDFSection,
    PDFSectionBoundaryEvidence,
    PDFSectionBoundaryEvidenceType,
    PDFSectionDetectionMethod,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSettings,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
)

_ABSTRACT_HEADING_RE = re.compile(
    r"^\s*(?:(?:SECTION\s+)?(?:1|I|A)(?:[\.\:]|\s*\|\s*)?\s+)?ABSTRACT[\.\:]?\s*$",
    re.IGNORECASE,
)
_INTRODUCTION_HEADING_RE = re.compile(
    r"^\s*(?:(?:SECTION\s+)?(?:1|1\.0|I)(?:[\.\:]|\s*\|\s*)?\s+)?INTRODUCTION[\.\:]?\s*$",
    re.IGNORECASE,
)

_EXPLICIT_NEXT_SECTION_TITLE_RE = re.compile(
    r"^\s*(?:(?:SECTION\s+)?(?:[2-9]|\d{2,}|II|III|IV|V|VI|VII|VIII|IX|X)(?:[\.\:]|\s*\|\s*)?\s+)(.+)$",
    re.IGNORECASE,
)
_IMPLICIT_NEXT_SECTION_TITLE_RE = re.compile(
    r"^\s*(?:(?:SECTION\s+)?(?:[1-9]|\d{2,}|I{1,3}|IV|V|VI|VII|VIII|IX|X)(?:[\.\:]|\s*\|\s*)?\s+)(.+)$",
    re.IGNORECASE,
)

_TOC_DOT_LEADERS_RE = re.compile(r"\.{4,}")
_CITATION_PATTERNS_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+et\s+al\.)?\s*\(\d{4}\)|\(\s*[A-Z][a-zA-Z]+(?:\s+et\s+al\.)?,\s*\d{4}\s*\)|\[\d+\]"
)
_EMBEDDED_CROSS_REF_RE = re.compile(
    r"\b(?:Section|Table|Figure|Fig|Equation|Eq|Appendix|Column|Row)\s+\d+",
    re.IGNORECASE,
)
_PROSE_DEMONSTRATIVE_START_RE = re.compile(
    r"^(?:This|The|These|Those|That|Here|There|Each|Every|All|Some|Many|Most|Several|We|It|A|An|In|Under|For)\b",
    re.IGNORECASE,
)
_PUBLISHER_COVER_SHEET_RE = re.compile(
    r"(?:This\s+content\s+downloaded\s+from|JSTOR\s+is\s+a\s+not-for-profit|Downloaded\s+from\s+http|NBER\s+Working\s+Paper\s+Series)",
    re.IGNORECASE,
)
_JEL_KEYWORDS_RE = re.compile(
    r"^\s*\(?\s*(?:JEL\s+Classification|JEL\s+codes?|JEL\b|Keywords?|Key\s+words?)\s*[\:\.]?\s*",
    re.IGNORECASE,
)
_ARTICLE_HISTORY_RE = re.compile(
    r"^\s*(?:ARTICLE\s+INFO|ARTICLE\s+HISTORY|Received\s+\d|Accepted\s+\d|Available\s+online|doi\.org|\bDOI\:)\b",
    re.IGNORECASE,
)
_FOOTNOTE_AFFILIATION_RE = re.compile(
    r"^(?:[\*\dagger\d]\s*)?(?:Department\s+of|Faculty\s+of|School\s+of|University\s+of|Email\:|Corresponding\s+author|Financial\s+support)",
    re.IGNORECASE,
)
_SENTENCE_TERMINATOR_RE = re.compile(r"[.!?][\"'\u201d\u2019)\]]*$")
_ACKNOWLEDGMENTS_RE = re.compile(
    r"^\s*(?:We\s+thank|Thanks\s+to|Financial\s+support|The\s+authors\s+thank|Acknowledge?ments?)\b",
    re.IGNORECASE,
)
_LOWERCASE_PROSE_VERBS = {
    "studies",
    "predicts",
    "implies",
    "shows",
    "reports",
    "found",
    "finds",
    "suggests",
    "indicates",
    "varies",
    "converges",
    "increases",
    "decreases",
    "differs",
    "depends",
    "contains",
    "presents",
    "examines",
    "describes",
    "thank",
    "thanks",
    "acknowledge",
    "acknowledges",
    "were",
    "was",
    "are",
    "is",
    "have",
    "has",
}


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
    __slots__ = ("kind", "line_index", "line", "score")

    def __init__(
        self, kind: PDFSectionKind, line_index: int, line: _LineInfo, score: int
    ) -> None:
        self.kind = kind
        self.line_index = line_index
        self.line = line
        self.score = score

    def to_domain_candidate(self) -> PDFHeadingCandidate:
        return PDFHeadingCandidate(
            kind=self.kind,
            heading_text=self.line.trimmed,
            page_number=self.line.page_number,
            start_character_offset=self.line.start_offset,
            end_character_offset=self.line.end_offset,
        )


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
            candidates=(),
            warnings=(PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),),
        )

    all_lines = _extract_lines(extraction)
    has_text = any(line.trimmed for line in all_lines)
    if not has_text:
        return PDFSectionDetectionResult(
            policy_version=settings.policy_version,
            sections=(),
            candidates=(),
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
        all_lines, _ABSTRACT_HEADING_RE, PDFSectionKind.ABSTRACT, running_headers
    )
    intro_candidates = _find_heading_candidates(
        all_lines,
        _INTRODUCTION_HEADING_RE,
        PDFSectionKind.INTRODUCTION,
        running_headers,
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

    intro_ambiguous = any(
        w.code is PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES
        for w in warnings_list
    )
    abstract_ambiguous = any(
        w.code is PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES
        for w in warnings_list
    )

    valid_intro = selected_intro
    if (
        selected_abstract is not None
        and selected_intro is not None
        and selected_intro.line_index <= selected_abstract.line_index
    ):
        valid_intro = None
        warnings_list.append(
            PDFSectionWarning(
                PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY,
                (all_lines[selected_abstract.line_index].page_number,),
            )
        )

    start_line_idx = 0
    if len(extraction.pages) > 1 and _PUBLISHER_COVER_SHEET_RE.search(
        all_lines[0].text
    ):
        for idx, line in enumerate(all_lines):
            if line.page_number > 1:
                start_line_idx = idx
                break

    # Determine Abstract section boundaries (explicit or implicit)
    if selected_abstract is not None:
        if valid_intro is not None:
            abstract_section = _build_section(
                kind=PDFSectionKind.ABSTRACT,
                heading_line=all_lines[selected_abstract.line_index],
                start_line_index=selected_abstract.line_index + 1,
                end_line_index=valid_intro.line_index,
                all_lines=all_lines,
                extraction=extraction,
                running_headers=running_headers,
            )
            if abstract_section is not None:
                sections_list.append(abstract_section)
            else:
                warnings_list.append(
                    PDFSectionWarning(
                        PDFSectionWarningCode.EMPTY_ABSTRACT_BODY,
                        (all_lines[selected_abstract.line_index].page_number,),
                    )
                )
        else:
            warnings_list.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY,
                    (all_lines[selected_abstract.line_index].page_number,),
                )
            )
    elif not abstract_ambiguous:
        # Implicit Abstract inference (Cases C & F)
        end_search_idx = (
            valid_intro.line_index if valid_intro is not None else len(all_lines)
        )
        if valid_intro is None:
            first_sec_cand = _find_next_section_candidate(
                all_lines,
                start_index=start_line_idx,
                running_headers=running_headers,
                is_implicit_intro=True,
            )
            if first_sec_cand is not None:
                end_search_idx = first_sec_cand.line_index

        jel_idx = None
        ack_idx = None
        for idx in range(start_line_idx, end_search_idx):
            if jel_idx is None and _JEL_KEYWORDS_RE.match(all_lines[idx].trimmed):
                jel_idx = idx
            if ack_idx is None and _ACKNOWLEDGMENTS_RE.match(all_lines[idx].trimmed):
                ack_idx = idx

        if jel_idx is not None and jel_idx > start_line_idx + 1:
            title_ev = PDFSectionBoundaryEvidence(
                page_number=all_lines[start_line_idx].page_number,
                start_character_offset=all_lines[start_line_idx].start_offset,
                end_character_offset=all_lines[start_line_idx].end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.TITLE_BLOCK,
                description="Implicit front-matter inferred from title block and abstract text",
            )
            jel_ev = PDFSectionBoundaryEvidence(
                page_number=all_lines[jel_idx].page_number,
                start_character_offset=all_lines[jel_idx].start_offset,
                end_character_offset=all_lines[jel_idx].end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.JEL_CLASSIFICATION_TERMINATOR,
                description="Bounded by JEL classification / Keywords block",
            )
            implicit_abs = _build_implicit_section(
                kind=PDFSectionKind.ABSTRACT,
                start_line_index=start_line_idx + 1,
                end_line_index=jel_idx,
                all_lines=all_lines,
                extraction=extraction,
                boundary_evidence=(title_ev, jel_ev),
                running_headers=running_headers,
            )
            if implicit_abs is not None:
                sections_list.append(implicit_abs)
        elif ack_idx is not None and ack_idx > start_line_idx + 1:
            title_ev = PDFSectionBoundaryEvidence(
                page_number=all_lines[start_line_idx].page_number,
                start_character_offset=all_lines[start_line_idx].start_offset,
                end_character_offset=all_lines[start_line_idx].end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.TITLE_BLOCK,
                description="Implicit front-matter inferred from title block and acknowledgments",
            )
            ack_ev = PDFSectionBoundaryEvidence(
                page_number=all_lines[ack_idx].page_number,
                start_character_offset=all_lines[ack_idx].start_offset,
                end_character_offset=all_lines[ack_idx].end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.ACKNOWLEDGMENTS_START,
                description="Implicit Abstract bounded by acknowledgments block",
            )
            implicit_abs = _build_implicit_section(
                kind=PDFSectionKind.ABSTRACT,
                start_line_index=start_line_idx + 1,
                end_line_index=ack_idx,
                all_lines=all_lines,
                extraction=extraction,
                boundary_evidence=(title_ev, ack_ev),
                running_headers=running_headers,
            )
            if implicit_abs is not None:
                sections_list.append(implicit_abs)
        elif valid_intro is not None and valid_intro.line_index > start_line_idx + 1:
            title_ev = PDFSectionBoundaryEvidence(
                page_number=all_lines[start_line_idx].page_number,
                start_character_offset=all_lines[start_line_idx].start_offset,
                end_character_offset=all_lines[start_line_idx].end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.TITLE_BLOCK,
                description="Implicit front-matter inferred from title block and intro heading",
            )
            intro_ev = PDFSectionBoundaryEvidence(
                page_number=all_lines[valid_intro.line_index].page_number,
                start_character_offset=all_lines[valid_intro.line_index].start_offset,
                end_character_offset=all_lines[valid_intro.line_index].end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.FIRST_SECTION_HEADING,
                description=f"Bounded by section heading '{all_lines[valid_intro.line_index].trimmed}'",
            )
            implicit_abs = _build_implicit_section(
                kind=PDFSectionKind.ABSTRACT,
                start_line_index=start_line_idx + 1,
                end_line_index=valid_intro.line_index,
                all_lines=all_lines,
                extraction=extraction,
                boundary_evidence=(title_ev, intro_ev),
                running_headers=running_headers,
            )
            if implicit_abs is not None:
                sections_list.append(implicit_abs)

    # Determine Introduction section boundaries (explicit or implicit)
    if valid_intro is not None:
        next_section_candidate = _find_next_section_candidate(
            all_lines,
            start_index=valid_intro.line_index + 1,
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
            heading_line=all_lines[valid_intro.line_index],
            start_line_index=valid_intro.line_index + 1,
            end_line_index=end_line_index,
            all_lines=all_lines,
            extraction=extraction,
            running_headers=running_headers,
        )
        if intro_section is not None:
            sections_list.append(intro_section)
        else:
            warnings_list.append(
                PDFSectionWarning(
                    PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY,
                    (all_lines[valid_intro.line_index].page_number,),
                )
            )
    elif (
        selected_intro is None
        and not intro_ambiguous
        and not any(
            w.code is PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY
            for w in warnings_list
        )
    ):
        # Implicit Introduction inference (Case C)
        intro_start_idx = start_line_idx + 1
        if len(sections_list) > 0 and sections_list[-1].kind is PDFSectionKind.ABSTRACT:
            abs_end_page = sections_list[-1].end_page_number
            for idx, line in enumerate(all_lines):
                if (
                    line.page_number >= abs_end_page
                    and line.start_offset
                    >= sections_list[-1].spans[-1].end_character_offset
                ):
                    intro_start_idx = idx
                    break

        next_sec_cand = _find_next_section_candidate(
            all_lines,
            start_index=intro_start_idx,
            running_headers=running_headers,
            is_implicit_intro=True,
        )
        if next_sec_cand is not None and next_sec_cand.line_index > intro_start_idx:
            first_sec_ev = PDFSectionBoundaryEvidence(
                page_number=next_sec_cand.line.page_number,
                start_character_offset=next_sec_cand.line.start_offset,
                end_character_offset=next_sec_cand.line.end_offset,
                evidence_type=PDFSectionBoundaryEvidenceType.FIRST_SECTION_HEADING,
                description=f"Implicit Introduction bounded by first top-level section heading '{next_sec_cand.line.trimmed}'",
            )
            implicit_intro = _build_implicit_section(
                kind=PDFSectionKind.INTRODUCTION,
                start_line_index=intro_start_idx,
                end_line_index=next_sec_cand.line_index,
                all_lines=all_lines,
                extraction=extraction,
                boundary_evidence=(first_sec_ev,),
                running_headers=running_headers,
            )
            if implicit_intro is not None:
                sections_list.append(implicit_intro)

    # Cross-field required missing warnings
    sec_kinds = {s.kind for s in sections_list}
    if (
        PDFSectionKind.ABSTRACT not in sec_kinds
        and not any(
            w.code is PDFSectionWarningCode.MISSING_ABSTRACT for w in warnings_list
        )
        and not any(
            w.code is PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES
            for w in warnings_list
        )
        and not any(
            w.code is PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY
            for w in warnings_list
        )
        and not any(
            w.code is PDFSectionWarningCode.EMPTY_ABSTRACT_BODY for w in warnings_list
        )
    ):
        warnings_list.append(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT))

    if (
        PDFSectionKind.INTRODUCTION not in sec_kinds
        and not any(
            w.code is PDFSectionWarningCode.MISSING_INTRODUCTION for w in warnings_list
        )
        and not any(
            w.code is PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES
            for w in warnings_list
        )
        and not any(
            w.code is PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY
            for w in warnings_list
        )
    ):
        warnings_list.append(
            PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION)
        )

    # Collect all candidates for domain result provenance
    all_domain_candidates = tuple(
        c.to_domain_candidate() for c in (abstract_candidates + intro_candidates)
    )

    # Sort warnings canonically
    warnings_list = _deduplicate_and_sort_warnings(warnings_list)

    return PDFSectionDetectionResult(
        policy_version=settings.policy_version,
        sections=tuple(sections_list),
        candidates=all_domain_candidates,
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


def _clean_header_text(text: str) -> str:
    return re.sub(r"^\d+\s*|\s*\d+$", "", text).strip()


def _detect_running_headers(lines: list[_LineInfo], page_count: int) -> set[str]:
    if page_count < 2:
        return set()
    trimmed_counts: dict[str, set[int]] = {}
    for idx, line in enumerate(lines):
        if not line.trimmed or len(line.trimmed) >= 100:
            continue
        cleaned = _clean_header_text(line.trimmed)
        if not cleaned:
            continue
        prev_blank = idx == 0 or not lines[idx - 1].trimmed
        next_blank = idx == len(lines) - 1 or not lines[idx + 1].trimmed
        is_first_line = idx == 0 or lines[idx - 1].page_number != line.page_number
        is_last_line = (
            idx == len(lines) - 1 or lines[idx + 1].page_number != line.page_number
        )
        if prev_blank or next_blank or is_first_line or is_last_line:
            if line.trimmed not in trimmed_counts:
                trimmed_counts[line.trimmed] = set()
            trimmed_counts[line.trimmed].add(line.page_number)
            if cleaned not in trimmed_counts:
                trimmed_counts[cleaned] = set()
            trimmed_counts[cleaned].add(line.page_number)
    return {
        text
        for text, pages in trimmed_counts.items()
        if len(pages) >= 2 and len(pages) > page_count * 0.3
    }


def _find_heading_candidates(
    lines: list[_LineInfo],
    pattern: re.Pattern[str],
    kind: PDFSectionKind,
    running_headers: set[str],
) -> list[_CandidateMatch]:
    candidates: list[_CandidateMatch] = []
    for idx, line in enumerate(lines):
        if not line.trimmed or len(line.trimmed) > 80:
            continue
        if _TOC_DOT_LEADERS_RE.search(line.text):
            continue
        if pattern.match(line.trimmed):
            is_first_line = idx == 0 or lines[idx - 1].page_number != line.page_number
            is_last_line = (
                idx == len(lines) - 1 or lines[idx + 1].page_number != line.page_number
            )
            is_page_margin_header = (
                is_first_line or is_last_line
            ) and line.page_number > 1
            if line.trimmed in running_headers and is_page_margin_header:
                score = 0
            else:
                score = 2
                prev_blank = idx == 0 or not lines[idx - 1].trimmed
                next_blank = idx == len(lines) - 1 or not lines[idx + 1].trimmed
                if prev_blank or next_blank or is_first_line or is_last_line:
                    score += 1
            candidates.append(_CandidateMatch(kind, idx, line, score))
    return candidates


def _find_next_section_candidate(
    lines: list[_LineInfo],
    start_index: int,
    running_headers: set[str],
    *,
    is_implicit_intro: bool = False,
) -> _CandidateMatch | None:
    for idx in range(start_index, len(lines)):
        line = lines[idx]
        if not line.trimmed or len(line.trimmed) > 80:
            continue
        if line.trimmed in running_headers:
            continue
        if _TOC_DOT_LEADERS_RE.search(line.text):
            continue
        prev_blank = idx == start_index or not lines[idx - 1].trimmed
        next_blank = idx == len(lines) - 1 or not lines[idx + 1].trimmed
        is_first_line = idx == 0 or lines[idx - 1].page_number != line.page_number
        is_last_line = (
            idx == len(lines) - 1 or lines[idx + 1].page_number != line.page_number
        )
        is_boundary = (
            prev_blank
            or next_blank
            or is_first_line
            or is_last_line
            or _is_paragraph_break_heading_context(lines, idx)
        )
        if _is_genuine_next_top_level_heading(
            line, is_boundary, is_implicit_intro=is_implicit_intro
        ):
            return _CandidateMatch(PDFSectionKind.INTRODUCTION, idx, line, 2)
    return None


def _is_paragraph_break_heading_context(lines: list[_LineInfo], idx: int) -> bool:
    """Return whether line ``idx`` sits at a heading-shaped paragraph break.

    Several real journal layouts (issue #59 cases A, B, D, E) emit the next
    top-level heading with no blank line and no page edge around it, so the
    blank-line/page-edge signal alone misses them. The remaining
    layout-derived evidence is that a heading interrupts hard-wrapped body
    text: the preceding line completes a sentence rather than wrapping, and
    the candidate is markedly shorter than the surrounding wrapped column
    width.

    This stays deterministic and generic — it reads only line lengths and
    terminal punctuation, never journal names, fixed pages, or article
    text — and is *additive*: it only ever admits a candidate that
    ``_is_genuine_next_top_level_heading`` must still independently accept.
    """
    previous = None
    for back in range(idx - 1, -1, -1):
        if lines[back].trimmed:
            previous = lines[back]
            break
    if previous is None:
        return False
    if previous.page_number != lines[idx].page_number:
        return False
    if not _SENTENCE_TERMINATOR_RE.search(previous.trimmed):
        return False

    neighbour_lengths = sorted(
        len(lines[offset].trimmed)
        for offset in range(max(0, idx - 8), min(len(lines), idx + 9))
        if lines[offset].trimmed and offset != idx
    )
    if len(neighbour_lengths) < 4:
        return False
    median_width = neighbour_lengths[len(neighbour_lengths) // 2]
    if median_width <= 0:
        return False
    return len(lines[idx].trimmed) <= 0.7 * median_width


def _is_genuine_next_top_level_heading(
    line: _LineInfo,
    is_boundary: bool,
    *,
    is_implicit_intro: bool = False,
) -> bool:
    pattern = (
        _IMPLICIT_NEXT_SECTION_TITLE_RE
        if is_implicit_intro
        else _EXPLICIT_NEXT_SECTION_TITLE_RE
    )
    match = pattern.match(line.trimmed)
    if not match:
        return False

    title_part = match.group(1).strip()
    if not title_part or len(title_part) > 60:
        return False

    # Disambiguation: if implicit intro, title_part cannot be 'Introduction'
    if is_implicit_intro and title_part.lower() == "introduction":
        return False

    # Check terminal period clause: prose sentences ending in a period with > 3 words
    if title_part.endswith("."):
        title_without_period = title_part[:-1].strip()
        words_without_period = title_without_period.split()
        if len(words_without_period) > 3:
            return False
        title_part = title_without_period

    words = title_part.split()
    if not words or len(words) > 6:
        return False

    # Title must start with a capital letter or digit
    if not (title_part[0].isupper() or title_part[0].isdigit()):
        return False

    # Structured Non-Heading Rejections:
    # 1. Equations (e.g. "2 Utility = income + leisure", "1 y = x + z")
    if "=" in title_part or "+" in title_part:
        return False

    # 2. Citations (e.g. "2 Smith (2020) shows effects")
    if _CITATION_PATTERNS_RE.search(title_part):
        return False

    # 3. Embedded cross-references (e.g. "2 Section 3 reports results", "2 Results in Table 4")
    if _EMBEDDED_CROSS_REF_RE.search(title_part):
        return False

    # 4. Declarative demonstratives and prose verbs (e.g. "2 This paper studies urban growth", "2 The model predicts higher wages")
    if _PROSE_DEMONSTRATIVE_START_RE.search(title_part):
        # Check if title contains any active prose verbs
        lower_words = [w.lower().rstrip(".,;:") for w in words]
        if any(verb in lower_words for verb in _LOWERCASE_PROSE_VERBS):
            return False

    # 5. Lowercase active prose verb check for any non-ALL-CAPS candidate
    if not title_part.isupper():
        lower_words = [w.lower().rstrip(".,;:") for w in words[1:]]
        if any(verb in lower_words for verb in _LOWERCASE_PROSE_VERBS):
            return False

    # Must have structural heading context (blank line or page boundary)
    if not is_boundary:
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

    max_score = max(c.score for c in candidates)
    top_candidates = [c for c in candidates if c.score == max_score]

    if max_score == 0:
        return None

    if len(top_candidates) > 1:
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


def _build_spans_and_text(
    kind: PDFSectionKind,
    lines_slice: list[_LineInfo],
    start_line_index: int,
    all_lines: list[_LineInfo],
    extraction: PDFExtractionResult,
    running_headers: set[str],
) -> tuple[tuple[PDFSectionSpan, ...], str] | None:
    page_map = {page.page_number: page.text for page in extraction.pages}

    spans_list: list[PDFSectionSpan] = []
    text_parts: list[str] = []

    lines_by_page: dict[int, list[tuple[int, _LineInfo]]] = {}
    for idx, line in enumerate(lines_slice, start=start_line_index):
        p_num = line.page_number
        if p_num not in lines_by_page:
            lines_by_page[p_num] = []
        lines_by_page[p_num].append((idx, line))

    for p_num in sorted(lines_by_page.keys()):
        page_text = page_map[p_num]
        page_lines = lines_by_page[p_num]

        current_run: list[_LineInfo] = []
        in_excluded_block = False

        for global_idx, line in page_lines:
            is_first_on_page = (
                global_idx == 0 or all_lines[global_idx - 1].page_number != p_num
            )
            is_last_on_page = (
                global_idx == len(all_lines) - 1
                or all_lines[global_idx + 1].page_number != p_num
            )

            cleaned_header = _clean_header_text(line.trimmed)
            is_rh = (
                p_num > 1
                and (
                    line.trimmed in running_headers or cleaned_header in running_headers
                )
                and (is_first_on_page or is_last_on_page)
            )
            is_page_num = (
                p_num > 1
                and line.trimmed.isdigit()
                and (is_first_on_page or is_last_on_page)
            )
            is_cover = bool(_PUBLISHER_COVER_SHEET_RE.search(line.text)) and p_num == 1

            has_prose_verb = any(
                f" {verb} " in f" {line.trimmed.lower()} "
                for verb in _LOWERCASE_PROSE_VERBS
            )
            is_metadata_start = (
                bool(_JEL_KEYWORDS_RE.match(line.trimmed))
                or bool(_ARTICLE_HISTORY_RE.match(line.trimmed))
            ) and not has_prose_verb

            is_affiliation_start = bool(_FOOTNOTE_AFFILIATION_RE.match(line.trimmed))
            is_ack_start = (
                kind is PDFSectionKind.ABSTRACT
                and bool(_ACKNOWLEDGMENTS_RE.match(line.trimmed))
                and not (
                    "in this paper" in line.trimmed.lower()
                    or "we show" in line.trimmed.lower()
                )
            )

            if is_metadata_start or is_affiliation_start or is_ack_start:
                in_excluded_block = True
                is_excluded = True
            elif in_excluded_block:
                if not line.trimmed or (
                    has_prose_verb and len(line.trimmed.split()) > 4
                ):
                    in_excluded_block = False
                    is_excluded = is_rh or is_page_num or is_cover
                else:
                    is_excluded = True
            else:
                is_excluded = is_rh or is_page_num or is_cover

            if is_excluded:
                if current_run:
                    _emit_run_span(
                        current_run, p_num, page_text, spans_list, text_parts
                    )
                    current_run = []
            else:
                current_run.append(line)

        if current_run:
            _emit_run_span(current_run, p_num, page_text, spans_list, text_parts)

    if not spans_list:
        return None

    section_text = "".join(text_parts)
    if not section_text.strip():
        return None

    return tuple(spans_list), section_text


def _emit_run_span(
    run: list[_LineInfo],
    p_num: int,
    page_text: str,
    spans_list: list[PDFSectionSpan],
    text_parts: list[str],
) -> None:
    start_off = run[0].start_offset
    end_off = min(run[-1].line_end_offset, len(page_text))
    if start_off < end_off:
        slice_text = page_text[start_off:end_off]
        if slice_text:
            spans_list.append(
                PDFSectionSpan(
                    page_number=p_num,
                    start_character_offset=start_off,
                    end_character_offset=end_off,
                )
            )
            text_parts.append(slice_text)


def _build_section(
    kind: PDFSectionKind,
    heading_line: _LineInfo,
    start_line_index: int,
    end_line_index: int,
    all_lines: list[_LineInfo],
    extraction: PDFExtractionResult,
    running_headers: set[str] | None = None,
) -> PDFSection | None:
    if start_line_index >= len(all_lines) or start_line_index >= end_line_index:
        return None

    lines_slice = all_lines[start_line_index:end_line_index]
    if not lines_slice:
        return None

    res = _build_spans_and_text(
        kind,
        lines_slice,
        start_line_index,
        all_lines,
        extraction,
        running_headers or set(),
    )
    if res is None:
        return None

    spans_tuple, section_text = res

    return PDFSection(
        kind=kind,
        detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        observed_heading_text=heading_line.trimmed,
        start_page_number=spans_tuple[0].page_number,
        end_page_number=spans_tuple[-1].page_number,
        spans=spans_tuple,
        text=section_text,
    )


def _build_implicit_section(
    kind: PDFSectionKind,
    start_line_index: int,
    end_line_index: int,
    all_lines: list[_LineInfo],
    extraction: PDFExtractionResult,
    boundary_evidence: tuple[PDFSectionBoundaryEvidence, ...],
    running_headers: set[str] | None = None,
) -> PDFSection | None:
    if start_line_index >= len(all_lines) or start_line_index >= end_line_index:
        return None

    lines_slice = all_lines[start_line_index:end_line_index]
    if not lines_slice:
        return None

    res = _build_spans_and_text(
        kind,
        lines_slice,
        start_line_index,
        all_lines,
        extraction,
        running_headers or set(),
    )
    if res is None:
        return None

    spans_tuple, section_text = res

    sorted_evidence = tuple(
        sorted(
            set(boundary_evidence),
            key=lambda e: (
                e.page_number,
                e.start_character_offset,
                e.end_character_offset,
                e.evidence_type.value,
                e.description,
            ),
        )
    )

    return PDFSection(
        kind=kind,
        detection_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        observed_heading_text=None,
        start_page_number=spans_tuple[0].page_number,
        end_page_number=spans_tuple[-1].page_number,
        spans=spans_tuple,
        text=section_text,
        boundary_evidence=sorted_evidence,
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
