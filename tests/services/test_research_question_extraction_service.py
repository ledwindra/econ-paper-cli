"""Service integration tests for structured research-question extraction."""

import json

from econ_paper_cli.domain import (
    DEFAULT_RESEARCH_QUESTION_SETTINGS,
    Citation,
    PDFSection,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
    PDFSectionWarning,
    PDFSectionWarningCode,
    ResearchQuestionKind,
    ResearchQuestionWarningCode,
)
from econ_paper_cli.protocols.generation import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.services.research_question_extraction import (
    extract_research_question,
)


class FakeGenerator(Generator):
    """Fake model generator for deterministic research question extraction tests."""

    def __init__(
        self,
        response_text: str | None = None,
        raise_error: Exception | None = None,
        abstained: bool = False,
    ) -> None:
        self.response_text = response_text
        self.raise_error = raise_error
        self.abstained = abstained
        self.last_request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.last_request = request
        if self.raise_error is not None:
            raise self.raise_error
        if self.abstained:
            return GenerationResponse(
                answer_text="Abstaining due to insufficient evidence.",
                citations=(),
                generation_method="fake_generator",
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )
        citations = tuple(
            Citation(
                citation_id=f"e{ev.rank}",
                paper_id=ev.passage.paper_id,
                passage_id=ev.passage.passage_id,
            )
            for ev in request.evidence
        )
        return GenerationResponse(
            answer_text=self.response_text or "",
            citations=citations,
            generation_method="fake_generator",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )


def _make_section_result(
    sections: tuple[PDFSection, ...],
    warnings: tuple[PDFSectionWarning, ...] = (),
) -> PDFSectionDetectionResult:
    sec_kinds = {s.kind for s in sections}
    w_list = list(warnings)
    w_codes = {w.code for w in w_list}

    if PDFSectionKind.ABSTRACT not in sec_kinds and not any(
        c in w_codes
        for c in (
            PDFSectionWarningCode.MISSING_ABSTRACT,
            PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES,
            PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY,
            PDFSectionWarningCode.EMPTY_ABSTRACT_BODY,
            PDFSectionWarningCode.NO_PAGES,
            PDFSectionWarningCode.ALL_PAGES_EMPTY,
        )
    ):
        w_list.append(PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT))

    if PDFSectionKind.INTRODUCTION not in sec_kinds and not any(
        c in w_codes
        for c in (
            PDFSectionWarningCode.MISSING_INTRODUCTION,
            PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES,
            PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY,
            PDFSectionWarningCode.NO_PAGES,
            PDFSectionWarningCode.ALL_PAGES_EMPTY,
        )
    ):
        w_list.append(PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION))

    from econ_paper_cli.domain.pdf_sections import _WARNING_ORDER

    w_list.sort(key=lambda w: _WARNING_ORDER[w.code])

    return PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=sections,
        candidates=(),
        warnings=tuple(w_list),
    )


def _make_section(
    kind: PDFSectionKind,
    text: str,
    page_number: int = 1,
    start_offset: int = 0,
) -> PDFSection:
    span = PDFSectionSpan(
        page_number=page_number,
        start_character_offset=start_offset,
        end_character_offset=start_offset + len(text),
    )
    heading = "Abstract" if kind is PDFSectionKind.ABSTRACT else "1. Introduction"
    return PDFSection(
        kind=kind,
        heading_text=heading,
        start_page_number=page_number,
        end_page_number=page_number,
        spans=(span,),
        text=text,
    )


def test_explicit_question_in_introduction() -> None:
    intro_text = "1. Introduction\nThis paper asks whether carbon taxes reduce emissions without reducing employment."
    sec_intro = _make_section(
        PDFSectionKind.INTRODUCTION, intro_text, page_number=1, start_offset=100
    )
    sec_res = _make_section_result((sec_intro,))

    excerpt = "This paper asks whether carbon taxes reduce emissions without reducing employment."
    start_off = 100 + intro_text.find(excerpt)
    resp_json = json.dumps(
        {
            "research_question": "Do carbon taxes reduce emissions without reducing employment?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "introduction",
                    "excerpt_text": excerpt,
                    "page_number": 1,
                    "start_character_offset": start_off,
                    "end_character_offset": start_off + len(excerpt),
                }
            ],
        }
    )

    gen = FakeGenerator(response_text=resp_json)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.EXPLICIT
    assert (
        res.question_text
        == "Do carbon taxes reduce emissions without reducing employment?"
    )
    assert res.sections_used == (PDFSectionKind.INTRODUCTION,)
    assert len(res.evidence) == 1
    assert res.evidence[0].excerpt_text == excerpt
    assert any(
        w.code is ResearchQuestionWarningCode.MISSING_SECTION for w in res.warnings
    )


def test_explicit_question_in_abstract() -> None:
    abs_text = (
        "Abstract: We evaluate the effect of interest rate hikes on housing prices."
    )
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_res = _make_section_result((sec_abs,))

    excerpt = "We evaluate the effect of interest rate hikes on housing prices."
    start_off = abs_text.find(excerpt)
    resp_json = json.dumps(
        {
            "research_question": "What is the effect of interest rate hikes on housing prices?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": excerpt,
                    "page_number": 1,
                    "start_character_offset": start_off,
                    "end_character_offset": start_off + len(excerpt),
                }
            ],
        }
    )

    gen = FakeGenerator(response_text=resp_json)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.EXPLICIT
    assert (
        res.question_text
        == "What is the effect of interest rate hikes on housing prices?"
    )
    assert res.sections_used == (PDFSectionKind.ABSTRACT,)


def test_inferred_question_from_objective_with_both_sections_multi_page() -> None:
    abs_text = "Abstract\nWe measure the returns to schooling in rural India."
    intro_part1 = "1. Introduction\nOur goal is to estimate wage gains"
    intro_part2 = " from an extra year of primary education."

    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    span1 = PDFSectionSpan(
        page_number=2,
        start_character_offset=50,
        end_character_offset=50 + len(intro_part1),
    )
    span2 = PDFSectionSpan(
        page_number=3, start_character_offset=0, end_character_offset=len(intro_part2)
    )
    sec_intro = PDFSection(
        kind=PDFSectionKind.INTRODUCTION,
        heading_text="1. Introduction",
        start_page_number=2,
        end_page_number=3,
        spans=(span1, span2),
        text=intro_part1 + intro_part2,
    )
    sec_res = _make_section_result((sec_abs, sec_intro))

    exc_abs = "We measure the returns to schooling in rural India."
    exc_intro = "from an extra year of primary education."

    resp_json = json.dumps(
        {
            "research_question": "What are the wage returns to primary education in rural India?",
            "kind": "inferred",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": exc_abs,
                    "page_number": 1,
                    "start_character_offset": abs_text.find(exc_abs),
                    "end_character_offset": abs_text.find(exc_abs) + len(exc_abs),
                },
                {
                    "section_kind": "introduction",
                    "excerpt_text": exc_intro,
                    "page_number": 3,
                    "start_character_offset": 1,
                    "end_character_offset": 1 + len(exc_intro),
                },
            ],
        }
    )

    gen = FakeGenerator(response_text=resp_json)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.INFERRED
    assert res.sections_used == (PDFSectionKind.ABSTRACT, PDFSectionKind.INTRODUCTION)
    assert len(res.evidence) == 2
    assert len(res.warnings) == 0


def test_neither_section_usable_skips_generation() -> None:
    sec_res = _make_section_result(
        sections=(),
        warnings=(
            PDFSectionWarning(PDFSectionWarningCode.NO_PAGES),
            PDFSectionWarning(PDFSectionWarningCode.MISSING_ABSTRACT),
            PDFSectionWarning(PDFSectionWarningCode.MISSING_INTRODUCTION),
        ),
    )

    gen = FakeGenerator(response_text="{}")
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert res.question_text is None
    assert res.evidence == ()
    assert gen.last_request is None  # Generation was skipped!
    assert any(
        w.code is ResearchQuestionWarningCode.NO_USABLE_SECTIONS for w in res.warnings
    )


def test_generator_abstention_emits_model_abstained_warning() -> None:
    abs_text = "Abstract\nWe study inflation persistence."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_res = _make_section_result((sec_abs,))

    gen = FakeGenerator(abstained=True)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert res.question_text is None
    codes = [w.code for w in res.warnings]
    assert ResearchQuestionWarningCode.MODEL_ABSTAINED in codes
    assert ResearchQuestionWarningCode.MISSING_SECTION in codes


def test_generator_failure_handled_gracefully() -> None:
    abs_text = "Abstract\nWe study inflation persistence."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_res = _make_section_result((sec_abs,))

    gen = FakeGenerator(raise_error=RuntimeError("Model connection timeout"))
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert res.question_text is None
    codes = [w.code for w in res.warnings]
    assert ResearchQuestionWarningCode.GENERATION_FAILED in codes
    assert ResearchQuestionWarningCode.MISSING_SECTION in codes


def test_extra_top_level_keys_rejected() -> None:
    abs_text = "Abstract\nWe study inflation persistence."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_res = _make_section_result((sec_abs,))

    exc = "We study inflation persistence."
    resp_extra_top = json.dumps(
        {
            "research_question": "What drives inflation persistence?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": exc,
                    "page_number": 1,
                    "start_character_offset": abs_text.find(exc),
                    "end_character_offset": abs_text.find(exc) + len(exc),
                }
            ],
            "unsupported_field": "extra_value",
        }
    )

    gen = FakeGenerator(response_text=resp_extra_top)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )
    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE
        for w in res.warnings
    )


def test_extra_evidence_keys_rejected() -> None:
    abs_text = "Abstract\nWe study inflation persistence."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_res = _make_section_result((sec_abs,))

    exc = "We study inflation persistence."
    resp_extra_ev = json.dumps(
        {
            "research_question": "What drives inflation persistence?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": exc,
                    "page_number": 1,
                    "start_character_offset": abs_text.find(exc),
                    "end_character_offset": abs_text.find(exc) + len(exc),
                    "confidence_score": 0.99,
                }
            ],
        }
    )

    gen = FakeGenerator(response_text=resp_extra_ev)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )
    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE
        for w in res.warnings
    )


def test_mismatched_text_at_valid_offsets_rejected() -> None:
    abs_text = "Abstract\nFirst sentence here. Second sentence here."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_res = _make_section_result((sec_abs,))

    first_sent = "First sentence here."
    second_sent = "Second sentence here."
    resp_json = json.dumps(
        {
            "research_question": "What is the topic?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": second_sent,
                    "page_number": 1,
                    "start_character_offset": abs_text.find(first_sent),
                    "end_character_offset": abs_text.find(first_sent)
                    + len(second_sent),
                }
            ],
        }
    )

    gen = FakeGenerator(response_text=resp_json)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE for w in res.warnings
    )


def test_both_sections_supplied_but_sections_used_derived_from_abstract_evidence_only() -> (
    None
):
    abs_text = "Abstract\nWe study trade tariffs."
    intro_text = "1. Introduction\nTrade tariffs affect welfare."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_intro = _make_section(
        PDFSectionKind.INTRODUCTION, intro_text, page_number=2, start_offset=0
    )
    sec_res = _make_section_result((sec_abs, sec_intro))

    exc_abs = "We study trade tariffs."
    resp_json = json.dumps(
        {
            "research_question": "How do tariffs affect welfare?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": exc_abs,
                    "page_number": 1,
                    "start_character_offset": abs_text.find(exc_abs),
                    "end_character_offset": abs_text.find(exc_abs) + len(exc_abs),
                }
            ],
        }
    )

    gen = FakeGenerator(response_text=resp_json)
    res = extract_research_question(
        sec_res, gen, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res.kind is ResearchQuestionKind.EXPLICIT
    assert res.sections_used == (
        PDFSectionKind.ABSTRACT,
    )  # Derived only from evidence!


def test_deterministic_prompt_ordering_and_repeated_runs() -> None:
    abs_text = "Abstract\nWe study trade tariffs."
    intro_text = "1. Introduction\nTrade tariffs affect welfare."
    sec_abs = _make_section(
        PDFSectionKind.ABSTRACT, abs_text, page_number=1, start_offset=0
    )
    sec_intro = _make_section(
        PDFSectionKind.INTRODUCTION, intro_text, page_number=2, start_offset=0
    )
    sec_res = _make_section_result((sec_abs, sec_intro))

    exc_abs = "We study trade tariffs."
    resp_json = json.dumps(
        {
            "research_question": "How do tariffs affect welfare?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": exc_abs,
                    "page_number": 1,
                    "start_character_offset": abs_text.find(exc_abs),
                    "end_character_offset": abs_text.find(exc_abs) + len(exc_abs),
                }
            ],
        }
    )

    gen1 = FakeGenerator(response_text=resp_json)
    res1 = extract_research_question(
        sec_res, gen1, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    gen2 = FakeGenerator(response_text=resp_json)
    res2 = extract_research_question(
        sec_res, gen2, settings=DEFAULT_RESEARCH_QUESTION_SETTINGS
    )

    assert res1 == res2
    assert gen1.last_request is not None
    assert gen2.last_request is not None
    assert gen1.last_request.question == gen2.last_request.question
