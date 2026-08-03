"""Application service for structured research-question extraction."""

import json
from typing import Any

from econ_paper_cli.domain.errors import ResearchQuestionValidationError
from econ_paper_cli.domain.evidence import RetrievalEvidence
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.pdf_sections import (
    PDFSection,
    PDFSectionDetectionResult,
    PDFSectionKind,
)
from econ_paper_cli.domain.research_question import (
    DEFAULT_RESEARCH_QUESTION_SETTINGS,
    ResearchQuestionEvidence,
    ResearchQuestionKind,
    ResearchQuestionResult,
    ResearchQuestionSettings,
    ResearchQuestionWarning,
    ResearchQuestionWarningCode,
)
from econ_paper_cli.protocols.generation import (
    GenerationRequest,
    GenerationResponse,
    GenerationResponseValidationError,
    Generator,
    validate_generation_response,
)

_TOP_LEVEL_KEYS = frozenset({"research_question", "kind", "evidence"})
_EVIDENCE_KEYS = frozenset(
    {
        "section_kind",
        "excerpt_text",
        "page_number",
        "start_character_offset",
        "end_character_offset",
    }
)


def extract_research_question(
    section_result: PDFSectionDetectionResult,
    generator: Generator,
    settings: ResearchQuestionSettings = DEFAULT_RESEARCH_QUESTION_SETTINGS,
) -> ResearchQuestionResult:
    """Extract a structured, evidence-backed research question from paper sections.

    Consumes completed Abstract and Introduction sections, constructs a
    provider-independent generation request, parses the structured response,
    and validates evidence grounding against source section text and page spans.
    """
    _validate_inputs(section_result, generator, settings)

    # Filter completed usable sections in canonical order (ABSTRACT then INTRODUCTION)
    usable_sections = _get_usable_sections(section_result)
    if not usable_sections:
        return ResearchQuestionResult(
            policy_version=settings.policy_version,
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=(
                ResearchQuestionWarning(ResearchQuestionWarningCode.NO_USABLE_SECTIONS),
            ),
        )

    initial_warnings: list[ResearchQuestionWarning] = []
    if len(usable_sections) == 1:
        initial_warnings.append(
            ResearchQuestionWarning(ResearchQuestionWarningCode.MISSING_SECTION)
        )

    is_v2 = settings.policy_version == "research-question-extraction-v2"
    request = (
        _build_generation_request_v2(usable_sections)
        if is_v2
        else _build_generation_request(usable_sections)
    )

    try:
        raw_response = generator.generate(request)
        response = validate_generation_response(request, raw_response)
    except (GenerationResponseValidationError, Exception) as error:
        warnings = list(initial_warnings)
        warnings.append(
            ResearchQuestionWarning(
                ResearchQuestionWarningCode.GENERATION_FAILED,
                f"Model generation failed: {error}",
            )
        )
        return ResearchQuestionResult(
            policy_version=settings.policy_version,
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=_canonicalize_warnings(warnings),
        )

    if response.abstained:
        warnings = list(initial_warnings)
        warnings.append(
            ResearchQuestionWarning(ResearchQuestionWarningCode.MODEL_ABSTAINED)
        )
        return ResearchQuestionResult(
            policy_version=settings.policy_version,
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=_canonicalize_warnings(warnings),
        )

    if is_v2:
        return _build_v2_result(
            response=response,
            usable_sections=usable_sections,
            settings=settings,
            initial_warnings=initial_warnings,
        )

    parsed_data = _parse_response_json(response.answer_text)
    if parsed_data is None:
        warnings = list(initial_warnings)
        warnings.append(
            ResearchQuestionWarning(
                ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE
            )
        )
        return ResearchQuestionResult(
            policy_version=settings.policy_version,
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=_canonicalize_warnings(warnings),
        )

    question_text, question_kind, raw_evidence = parsed_data
    validated_evidence = _validate_and_build_evidence(raw_evidence, usable_sections)

    if validated_evidence is None:
        warnings = list(initial_warnings)
        warnings.append(
            ResearchQuestionWarning(ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE)
        )
        return ResearchQuestionResult(
            policy_version=settings.policy_version,
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=_canonicalize_warnings(warnings),
        )

    # Derive sections_used from validated evidence in canonical order
    sections_used = tuple(
        kind
        for kind in (PDFSectionKind.ABSTRACT, PDFSectionKind.INTRODUCTION)
        if any(item.section_kind is kind for item in validated_evidence)
    )

    return ResearchQuestionResult(
        policy_version=settings.policy_version,
        question_text=question_text,
        kind=question_kind,
        sections_used=sections_used,
        evidence=validated_evidence,
        warnings=_canonicalize_warnings(initial_warnings),
    )


def _validate_inputs(
    section_result: object,
    generator: object,
    settings: object,
) -> None:
    if not isinstance(section_result, PDFSectionDetectionResult):
        raise ResearchQuestionValidationError(
            "section_result must be a PDFSectionDetectionResult instance."
        )
    if not isinstance(generator, Generator):
        raise ResearchQuestionValidationError(
            "generator must implement the Generator protocol."
        )
    if not isinstance(settings, ResearchQuestionSettings):
        raise ResearchQuestionValidationError(
            "settings must be a ResearchQuestionSettings instance."
        )


def _get_usable_sections(
    section_result: PDFSectionDetectionResult,
) -> tuple[PDFSection, ...]:
    by_kind = {sec.kind: sec for sec in section_result.sections if sec.text.strip()}
    usable: list[PDFSection] = []
    if PDFSectionKind.ABSTRACT in by_kind:
        usable.append(by_kind[PDFSectionKind.ABSTRACT])
    if PDFSectionKind.INTRODUCTION in by_kind:
        usable.append(by_kind[PDFSectionKind.INTRODUCTION])
    return tuple(usable)


def _build_generation_request(
    usable_sections: tuple[PDFSection, ...],
) -> GenerationRequest:
    prompt_parts = [
        "Extract the primary research question from the provided paper section(s).",
        "",
        "Return a strict JSON object with this exact schema:",
        "{",
        '  "research_question": "<concise research question>",',
        '  "kind": "explicit" | "inferred",',
        '  "evidence": [',
        "    {",
        '      "section_kind": "abstract" | "introduction",',
        '      "excerpt_text": "<exact quote from section text>",',
        '      "page_number": <int page number>,',
        '      "start_character_offset": <int start offset on page>,',
        '      "end_character_offset": <int end offset on page>',
        "    }",
        "  ]",
        "}",
        "",
        "Sections:",
    ]

    retrieval_evidence: list[RetrievalEvidence] = []
    for rank, sec in enumerate(usable_sections, start=1):
        prompt_parts.append("---")
        prompt_parts.append(
            f"[{sec.kind.value.capitalize()}] (Pages {sec.start_page_number}-{sec.end_page_number})"
        )
        for span in sec.spans:
            prompt_parts.append(
                f"  Span Page {span.page_number} (Offsets {span.start_character_offset}-{span.end_character_offset})"
            )
        prompt_parts.append(sec.text)

        passage = Passage(
            passage_id=sec.kind.value,
            paper_id="doc",
            text=sec.text,
            section_heading=sec.display_label,
            page_start=sec.start_page_number,
            page_end=sec.end_page_number,
            ordinal_position=rank,
        )
        retrieval_evidence.append(
            RetrievalEvidence(
                passage=passage,
                score=1.0,
                rank=rank,
                retrieval_method="section_detection",
            )
        )

    prompt = "\n".join(prompt_parts)
    return GenerationRequest(question=prompt, evidence=tuple(retrieval_evidence))


def _parse_response_json(
    answer_text: str,
) -> tuple[str, ResearchQuestionKind, list[dict[str, Any]]] | None:
    text = answer_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    # Strict key check for top-level JSON mapping
    if set(data.keys()) != _TOP_LEVEL_KEYS:
        return None

    question_text = data.get("research_question")
    if not isinstance(question_text, str) or not question_text.strip():
        return None

    kind_str = data.get("kind")
    if kind_str not in ("explicit", "inferred"):
        return None
    question_kind = ResearchQuestionKind(kind_str)

    raw_evidence = data.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        return None
    for item in raw_evidence:
        if not isinstance(item, dict):
            return None
        # Strict key check for evidence mapping
        if set(item.keys()) != _EVIDENCE_KEYS:
            return None

    return question_text.strip(), question_kind, raw_evidence


def _validate_and_build_evidence(
    raw_evidence: list[dict[str, Any]],
    usable_sections: tuple[PDFSection, ...],
) -> tuple[ResearchQuestionEvidence, ...] | None:
    section_map = {sec.kind: sec for sec in usable_sections}
    validated: list[ResearchQuestionEvidence] = []

    for item in raw_evidence:
        sec_kind_str = item.get("section_kind")
        if not isinstance(sec_kind_str, str):
            return None
        try:
            sec_kind = PDFSectionKind(sec_kind_str)
        except ValueError:
            return None

        if sec_kind not in section_map:
            return None
        sec = section_map[sec_kind]

        excerpt_text = item.get("excerpt_text")
        if not isinstance(excerpt_text, str) or not excerpt_text:
            return None

        if excerpt_text not in sec.text:
            return None

        page_number = item.get("page_number")
        start_offset = item.get("start_character_offset")
        end_offset = item.get("end_character_offset")

        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or isinstance(start_offset, bool)
            or not isinstance(start_offset, int)
            or isinstance(end_offset, bool)
            or not isinstance(end_offset, int)
        ):
            return None

        if len(excerpt_text) != (end_offset - start_offset):
            return None

        # Verify page_number, offsets, and exact character slice match against sec.spans and sec.text
        span_found = False
        sec_text_offset = 0

        for span in sec.spans:
            if (
                span.page_number == page_number
                and span.start_character_offset <= start_offset
                and end_offset <= span.end_character_offset
            ):
                span_found = True
                offset_into_span = start_offset - span.start_character_offset
                sec_text_start = sec_text_offset + offset_into_span
                sec_text_end = sec_text_start + (end_offset - start_offset)
                if sec.text[sec_text_start:sec_text_end] != excerpt_text:
                    return None
                break
            sec_text_offset += span.character_count

        if not span_found:
            return None

        try:
            ev = ResearchQuestionEvidence(
                section_kind=sec_kind,
                excerpt_text=excerpt_text,
                page_number=page_number,
                start_character_offset=start_offset,
                end_character_offset=end_offset,
            )
            validated.append(ev)
        except ResearchQuestionValidationError:
            return None

    return tuple(validated)


def _canonicalize_warnings(
    warnings: list[ResearchQuestionWarning],
) -> tuple[ResearchQuestionWarning, ...]:
    from econ_paper_cli.domain.research_question import _WARNING_ORDER

    seen: set[ResearchQuestionWarningCode] = set()
    unique_warnings: list[ResearchQuestionWarning] = []
    for w in warnings:
        if w.code not in seen:
            seen.add(w.code)
            unique_warnings.append(w)

    unique_warnings.sort(key=lambda w: _WARNING_ORDER[w.code])
    return tuple(unique_warnings)


# --- research-question-extraction-v2 ---------------------------------------
#
# v2 splits responsibility by what each party can actually know. The model
# supplies only the question sentence and which detected section it came
# from — both things it can read off the supplied text. Everything traceable
# (page number, character offsets, exact excerpt) is derived here from the
# already-validated PDFSection spans, so provenance is correct by
# construction instead of being invented by the model and then rejected.

# A guard against the model echoing an entire abstract, not a style limit.
# Real research questions from local models routinely exceed a few hundred
# characters when they enumerate sub-questions.
_MAX_QUESTION_TEXT_CHARACTERS = 1000


def _build_generation_request_v2(
    usable_sections: tuple[PDFSection, ...],
) -> GenerationRequest:
    prompt_parts = [
        "Identify the primary research question of this paper.",
        "",
        "Answer with the research question as a single sentence, and nothing "
        "else. Do not restate the instructions, add a preamble, or return "
        "JSON. Cite the section the question came from.",
        "",
        "Sections:",
    ]

    retrieval_evidence: list[RetrievalEvidence] = []
    for rank, sec in enumerate(usable_sections, start=1):
        prompt_parts.append("---")
        prompt_parts.append(f"[{sec.kind.value.capitalize()}]")
        prompt_parts.append(sec.text)

        passage = Passage(
            passage_id=sec.kind.value,
            paper_id="doc",
            text=sec.text,
            section_heading=sec.display_label,
            page_start=sec.start_page_number,
            page_end=sec.end_page_number,
            ordinal_position=rank,
        )
        retrieval_evidence.append(
            RetrievalEvidence(
                passage=passage,
                score=1.0,
                rank=rank,
                retrieval_method="section_detection",
            )
        )

    return GenerationRequest(
        question="\n".join(prompt_parts), evidence=tuple(retrieval_evidence)
    )


def _build_v2_result(
    *,
    response: GenerationResponse,
    usable_sections: tuple[PDFSection, ...],
    settings: ResearchQuestionSettings,
    initial_warnings: list[ResearchQuestionWarning],
) -> ResearchQuestionResult:
    def _unavailable(code: ResearchQuestionWarningCode) -> ResearchQuestionResult:
        warnings = list(initial_warnings)
        warnings.append(ResearchQuestionWarning(code))
        return ResearchQuestionResult(
            policy_version=settings.policy_version,
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=_canonicalize_warnings(warnings),
        )

    question_text = response.answer_text.strip()
    if not question_text or len(question_text) > _MAX_QUESTION_TEXT_CHARACTERS:
        return _unavailable(ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE)

    sections_by_kind = {section.kind.value: section for section in usable_sections}
    cited_sections: list[PDFSection] = []
    for citation in response.citations:
        section = sections_by_kind.get(citation.passage_id)
        if section is None:
            # Citation identifiers are already validated against supplied
            # evidence upstream, so this means the evidence/section mapping
            # itself disagrees — refuse rather than guess a section.
            return _unavailable(ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE)
        if section not in cited_sections:
            cited_sections.append(section)

    if not cited_sections:
        return _unavailable(ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE)

    evidence: list[ResearchQuestionEvidence] = []
    for section in cited_sections:
        item = _derive_section_evidence(section)
        if item is None:
            return _unavailable(ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE)
        evidence.append(item)

    sections_used = tuple(
        kind
        for kind in (PDFSectionKind.ABSTRACT, PDFSectionKind.INTRODUCTION)
        if any(section.kind is kind for section in cited_sections)
    )

    return ResearchQuestionResult(
        policy_version=settings.policy_version,
        question_text=question_text,
        kind=_derive_question_kind(cited_sections),
        sections_used=sections_used,
        evidence=tuple(evidence),
        warnings=_canonicalize_warnings(initial_warnings),
    )


def _derive_section_evidence(
    section: PDFSection,
) -> ResearchQuestionEvidence | None:
    """Build exactly-grounded evidence from a section's own first span.

    The excerpt is sliced out of the section text the span already accounts
    for, so `len(excerpt_text) == end - start` holds by construction rather
    than depending on a model reporting offsets correctly.
    """
    if not section.spans:
        return None
    span = section.spans[0]
    length = span.end_character_offset - span.start_character_offset
    excerpt = section.text[:length]
    if not excerpt.strip():
        return None
    return ResearchQuestionEvidence(
        section_kind=section.kind,
        excerpt_text=excerpt,
        page_number=span.page_number,
        start_character_offset=span.start_character_offset,
        end_character_offset=span.start_character_offset + len(excerpt),
    )


def _derive_question_kind(
    cited_sections: list[PDFSection],
) -> ResearchQuestionKind:
    """Classify deterministically from the source text, not from the model.

    A paper that poses its question outright contains an interrogative
    sentence in the cited section; otherwise the question was inferred from
    surrounding prose. Asking the model to self-report this invites it to
    claim "explicit" for text it actually paraphrased.
    """
    for section in cited_sections:
        if "?" in section.text:
            return ResearchQuestionKind.EXPLICIT
    return ResearchQuestionKind.INFERRED
