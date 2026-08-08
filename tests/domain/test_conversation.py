"""Bounded conversation history and conservative follow-up detection."""

import pytest

from econ_paper_cli.domain.conversation import (
    MAX_HISTORY_TURNS,
    ConversationError,
    ConversationHistory,
    ConversationTurn,
    needs_context_resolution,
)


def _turn(
    question: str = "what is the effect of transit on welfare?",
) -> ConversationTurn:
    return ConversationTurn(
        question=question,
        resolved_question=question,
        answer_text="Transit raises welfare.",
        cited_paper_titles=("A Transit Paper",),
    )


def test_history_is_empty_until_a_turn_is_recorded() -> None:
    history = ConversationHistory()

    assert history.is_empty is True
    assert history.latest is None


def test_appending_returns_a_new_history_and_leaves_the_original_alone() -> None:
    """History sits beside an immutable session snapshot, and a turn that fails
    partway through must not leave it half-updated."""
    original = ConversationHistory()

    updated = original.appended(_turn())

    assert original.is_empty is True
    assert updated.is_empty is False
    assert updated.latest is not None


def test_history_drops_the_oldest_turn_beyond_its_bound() -> None:
    """Unbounded history would grow the rewrite prompt without limit across a
    long session."""
    history = ConversationHistory()
    for index in range(MAX_HISTORY_TURNS + 3):
        history = history.appended(_turn(f"question number {index}?"))

    assert len(history.turns) == MAX_HISTORY_TURNS
    assert history.turns[-1].question == f"question number {MAX_HISTORY_TURNS + 2}?"


def test_clearing_history_keeps_the_configured_bound() -> None:
    history = ConversationHistory(max_turns=5).appended(_turn())

    cleared = history.cleared()

    assert cleared.is_empty is True
    assert cleared.max_turns == 5


def test_history_rejects_more_turns_than_its_bound() -> None:
    with pytest.raises(ConversationError, match="exceeds max_turns"):
        ConversationHistory(turns=(_turn(), _turn(), _turn()), max_turns=2)


def test_a_turn_requires_question_resolution_and_answer_text() -> None:
    with pytest.raises(ConversationError, match="resolved_question"):
        ConversationTurn(
            question="a question?", resolved_question="  ", answer_text="an answer"
        )


# --- Follow-up detection -----------------------------------------------------


def test_no_question_is_a_follow_up_when_there_is_no_history() -> None:
    """With nothing to refer back to there is no resolution to perform, and the
    question must be taken literally however pronoun-heavy it looks."""
    assert (
        needs_context_resolution("does it control for wages?", ConversationHistory())
        is False
    )


@pytest.mark.parametrize(
    "question",
    (
        "does it control for wages?",
        "what about wages?",
        "how about the other paper?",
        "do they report standard errors?",
        "what else did that study find?",
        "is this robust?",
    ),
)
def test_referring_and_elliptical_questions_are_detected(question: str) -> None:
    history = ConversationHistory().appended(_turn())

    assert needs_context_resolution(question, history) is True


@pytest.mark.parametrize(
    "question",
    (
        "what are the welfare effects of bus rapid transit?",
        "how does housing supply respond to local political conditions?",
        "which papers use structural estimation?",
        "",
    ),
)
def test_self_contained_questions_are_not_treated_as_follow_ups(
    question: str,
) -> None:
    """A false positive drags stale topic words into a genuinely new question,
    which silently answers something the user did not ask."""
    history = ConversationHistory().appended(_turn())

    assert needs_context_resolution(question, history) is False
