"""Validation tests for immutable research-question extraction domain contracts."""

from dataclasses import replace

import pytest

from econ_paper_cli.domain import (
    DEFAULT_RESEARCH_QUESTION_SETTINGS,
    PDFSectionKind,
    ResearchQuestionEvidence,
    ResearchQuestionKind,
    ResearchQuestionResult,
    ResearchQuestionSettings,
    ResearchQuestionValidationError,
    ResearchQuestionWarning,
    ResearchQuestionWarningCode,
)


def test_research_question_evidence_validation() -> None:
    excerpt = "Does tariff reduction increase firm productivity?"
    ev = ResearchQuestionEvidence(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text=excerpt,
        page_number=1,
        start_character_offset=10,
        end_character_offset=10 + len(excerpt),
    )
    assert ev.section_kind is PDFSectionKind.ABSTRACT
    assert ev.page_number == 1
    assert ev.start_character_offset == 10
    assert ev.end_character_offset == 10 + len(excerpt)

    with pytest.raises(ResearchQuestionValidationError, match="excerpt_text"):
        ResearchQuestionEvidence(
            section_kind=PDFSectionKind.ABSTRACT,
            excerpt_text="   ",
            page_number=1,
            start_character_offset=0,
            end_character_offset=3,
        )

    with pytest.raises(ResearchQuestionValidationError, match="page_number"):
        ResearchQuestionEvidence(
            section_kind=PDFSectionKind.ABSTRACT,
            excerpt_text="Text",
            page_number=0,
            start_character_offset=0,
            end_character_offset=4,
        )

    with pytest.raises(ResearchQuestionValidationError, match="cannot exceed"):
        ResearchQuestionEvidence(
            section_kind=PDFSectionKind.ABSTRACT,
            excerpt_text="Text",
            page_number=1,
            start_character_offset=10,
            end_character_offset=5,
        )

    with pytest.raises(ResearchQuestionValidationError, match="length"):
        ResearchQuestionEvidence(
            section_kind=PDFSectionKind.ABSTRACT,
            excerpt_text="Text",
            page_number=1,
            start_character_offset=0,
            end_character_offset=10,
        )


def test_research_question_warning_validation() -> None:
    w1 = ResearchQuestionWarning(code=ResearchQuestionWarningCode.NO_USABLE_SECTIONS)
    assert w1.code is ResearchQuestionWarningCode.NO_USABLE_SECTIONS
    assert "skipped" in w1.message

    w2 = ResearchQuestionWarning(
        code=ResearchQuestionWarningCode.GENERATION_FAILED,
        details="Connection refused",
    )
    assert "Details: Connection refused" in w2.message

    with pytest.raises(ResearchQuestionValidationError, match="details"):
        ResearchQuestionWarning(
            code=ResearchQuestionWarningCode.GENERATION_FAILED,
            details="   ",
        )


def test_research_question_settings_validation() -> None:
    assert (
        DEFAULT_RESEARCH_QUESTION_SETTINGS.policy_version
        == "research-question-extraction-v1"
    )

    with pytest.raises(
        ResearchQuestionValidationError, match="not a recognized policy version"
    ):
        ResearchQuestionSettings(policy_version="unknown-v99")


def test_research_question_result_explicit_validation() -> None:
    excerpt = "We study the impact of microcredit on poverty."
    ev = ResearchQuestionEvidence(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text=excerpt,
        page_number=1,
        start_character_offset=5,
        end_character_offset=5 + len(excerpt),
    )

    res = ResearchQuestionResult(
        policy_version="research-question-extraction-v1",
        question_text="What is the impact of microcredit on poverty?",
        kind=ResearchQuestionKind.EXPLICIT,
        sections_used=(PDFSectionKind.ABSTRACT,),
        evidence=(ev,),
        warnings=(),
    )
    assert res.kind is ResearchQuestionKind.EXPLICIT
    assert res.question_text == "What is the impact of microcredit on poverty?"

    # Available result requires question_text
    with pytest.raises(ResearchQuestionValidationError, match="question_text"):
        replace(res, question_text=None)

    # Available result requires non-empty sections_used
    with pytest.raises(ResearchQuestionValidationError, match="sections_used"):
        replace(res, sections_used=())

    # Available result requires non-empty evidence
    with pytest.raises(ResearchQuestionValidationError, match="evidence"):
        replace(res, evidence=())

    # Evidence section_kind must be in sections_used
    ev_intro = replace(ev, section_kind=PDFSectionKind.INTRODUCTION)
    with pytest.raises(
        ResearchQuestionValidationError, match="not included in sections_used"
    ):
        replace(res, evidence=(ev_intro,))


def test_research_question_result_unavailable_validation() -> None:
    res = ResearchQuestionResult(
        policy_version="research-question-extraction-v1",
        question_text=None,
        kind=ResearchQuestionKind.UNAVAILABLE,
        sections_used=(),
        evidence=(),
        warnings=(
            ResearchQuestionWarning(ResearchQuestionWarningCode.NO_USABLE_SECTIONS),
        ),
    )
    assert res.kind is ResearchQuestionKind.UNAVAILABLE
    assert res.question_text is None

    # UNAVAILABLE requires question_text is None
    with pytest.raises(ResearchQuestionValidationError, match="must be None"):
        replace(res, question_text="Some question")

    # UNAVAILABLE requires empty evidence
    excerpt = "We study the impact of microcredit on poverty."
    ev = ResearchQuestionEvidence(
        section_kind=PDFSectionKind.ABSTRACT,
        excerpt_text=excerpt,
        page_number=1,
        start_character_offset=5,
        end_character_offset=5 + len(excerpt),
    )
    with pytest.raises(ResearchQuestionValidationError, match="must be empty"):
        replace(res, evidence=(ev,))

    # UNAVAILABLE requires at least one warning
    with pytest.raises(
        ResearchQuestionValidationError, match="must contain at least one warning"
    ):
        replace(res, warnings=())


def test_result_rejects_non_canonical_warning_ordering_and_duplicates() -> None:
    with pytest.raises(
        ResearchQuestionValidationError, match="warnings must use canonical code order"
    ):
        ResearchQuestionResult(
            policy_version="research-question-extraction-v1",
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=(
                ResearchQuestionWarning(ResearchQuestionWarningCode.GENERATION_FAILED),
                ResearchQuestionWarning(ResearchQuestionWarningCode.NO_USABLE_SECTIONS),
            ),
        )

    with pytest.raises(
        ResearchQuestionValidationError, match="warning codes must be unique"
    ):
        ResearchQuestionResult(
            policy_version="research-question-extraction-v1",
            question_text=None,
            kind=ResearchQuestionKind.UNAVAILABLE,
            sections_used=(),
            evidence=(),
            warnings=(
                ResearchQuestionWarning(ResearchQuestionWarningCode.NO_USABLE_SECTIONS),
                ResearchQuestionWarning(ResearchQuestionWarningCode.NO_USABLE_SECTIONS),
            ),
        )
