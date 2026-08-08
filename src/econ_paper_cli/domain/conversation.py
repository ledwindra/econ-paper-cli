"""Pure conversation state and follow-up detection for the interactive shell.

The shell answers each question against retrieved evidence, so a question that
only makes sense relative to an earlier turn ("what about wages?", "does it
control for that?") retrieves nothing useful and gives the generator no
referent. This module decides *whether* a question depends on prior context and
holds the bounded history a resolver needs; it never rewrites text and never
calls a model.

Detection is deliberately conservative. A false positive drags stale topic
words into a genuinely new question, which is worse than a false negative --
an unresolved follow-up simply retrieves poorly, while a wrongly-resolved
standalone question silently answers something the user did not ask.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from econ_paper_cli.domain.errors import DomainError

# How many prior turns a resolver may see. Two is enough for "that paper" and
# "what about X?" chains while keeping the rewrite prompt small and bounded;
# unbounded history would grow the prompt without bound across a long session.
MAX_HISTORY_TURNS = 2

# Referring expressions that point outside the question. Matched as whole words
# at any position: "does it control for wages" and "what about that paper" both
# depend on an antecedent this question does not supply.
_REFERRING_TERMS = frozenset(
    {
        "it",
        "its",
        "they",
        "them",
        "their",
        "these",
        "those",
        "that",
        "this",
        "he",
        "she",
        "his",
        "her",
        "there",
        "same",
        "above",
        "former",
        "latter",
    }
)

# Openers that are elliptical by construction: they announce a continuation of
# something already under discussion rather than a self-contained question.
_ELLIPTICAL_OPENERS = (
    ("what", "about"),
    ("how", "about"),
    ("and", "what"),
    ("what", "else"),
    ("any", "others"),
    ("anything", "else"),
)

_WORD_RE = re.compile(r"[a-z0-9]+")


class ConversationError(DomainError):
    """Raised when conversation state violates its structural contract."""


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One completed, answered turn retained as context for later questions.

    Holds the *resolved* question rather than only what the user typed, so a
    chain of follow-ups resolves against fully-specified text instead of
    compounding one turn's referring expressions into the next.
    """

    question: str
    resolved_question: str
    answer_text: str
    cited_paper_titles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate that a retained turn carries usable context."""
        for field_name in ("question", "resolved_question", "answer_text"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConversationError(f"{field_name} must be a non-empty string.")
        if not isinstance(self.cited_paper_titles, tuple):
            raise ConversationError("cited_paper_titles must be a tuple.")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.cited_paper_titles
        ):
            raise ConversationError(
                "cited_paper_titles must contain only non-empty strings."
            )


@dataclass(frozen=True, slots=True)
class ConversationHistory:
    """Immutable, bounded record of recent answered turns, oldest first."""

    turns: tuple[ConversationTurn, ...] = field(default=())
    max_turns: int = MAX_HISTORY_TURNS

    def __post_init__(self) -> None:
        """Validate the turn sequence and its bound."""
        if not isinstance(self.turns, tuple):
            raise ConversationError("turns must be a tuple.")
        if any(not isinstance(item, ConversationTurn) for item in self.turns):
            raise ConversationError(
                "turns must contain only ConversationTurn instances."
            )
        if isinstance(self.max_turns, bool) or not isinstance(self.max_turns, int):
            raise ConversationError("max_turns must be an integer.")
        if self.max_turns < 1:
            raise ConversationError("max_turns must be at least 1.")
        if len(self.turns) > self.max_turns:
            raise ConversationError(
                f"turns exceeds max_turns ({len(self.turns)} > {self.max_turns})."
            )

    @property
    def is_empty(self) -> bool:
        """Whether there is any prior turn a follow-up could refer to."""
        return not self.turns

    @property
    def latest(self) -> ConversationTurn | None:
        """The most recent retained turn, or None when the history is empty."""
        return self.turns[-1] if self.turns else None

    def appended(self, turn: ConversationTurn) -> "ConversationHistory":
        """Return a new history with ``turn`` appended, dropping the oldest.

        Returns a new value rather than mutating: the session snapshot this
        history lives beside is immutable for the life of the process, and a
        turn that failed partway through must not leave history half-updated.
        """
        if not isinstance(turn, ConversationTurn):
            raise ConversationError("turn must be a ConversationTurn instance.")
        combined = (*self.turns, turn)[-self.max_turns :]
        return ConversationHistory(turns=combined, max_turns=self.max_turns)

    def cleared(self) -> "ConversationHistory":
        """Return an empty history with the same bound.

        Backs an explicit user reset: a topic change should not have to be
        worked around by restarting the process.
        """
        return ConversationHistory(turns=(), max_turns=self.max_turns)


def needs_context_resolution(question: str, history: ConversationHistory) -> bool:
    """Report whether ``question`` depends on an earlier turn to be answerable.

    Returns False whenever the history is empty: with nothing to refer back to,
    there is no resolution to perform and the question must be taken literally.
    """
    if not isinstance(question, str):
        raise ConversationError("question must be a string.")
    if not isinstance(history, ConversationHistory):
        raise ConversationError("history must be a ConversationHistory instance.")
    if history.is_empty:
        return False

    words = _words(question)
    if not words:
        return False

    if any(word in _REFERRING_TERMS for word in words):
        return True
    if any(
        tuple(words[: len(opener)]) == opener
        for opener in _ELLIPTICAL_OPENERS
        if len(words) >= len(opener)
    ):
        return True
    return False


def _words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _WORD_RE.findall(normalized)
