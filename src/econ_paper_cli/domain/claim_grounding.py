"""Structural detection of cross-paper leakage in generated claims.

A claim *leaks* when it states a distinctive term that belongs to some other
paper in the retrieved evidence set and appears nowhere in the paper the claim
cites. That is the signature of a generator welding two studies into one
sentence: the wording is fluent, every citation identifier is real, and
`validate_generation_response` passes, yet the claim attributes one paper's
method, population, or outcome to a different paper.

The check is deliberately structural, matching this repository's evaluation
stance: it never asks whether a claim is *true*, only whether the words it uses
could have come from the paper it points at. Two scoping rules are what keep it
precise, and both were established empirically against a real conflated answer:

1. **Paper-level, not passage-level.** Several passages of one paper are
   routinely retrieved at different ranks. A claim citing one of them may
   legitimately use vocabulary from its siblings, so every retrieved passage of
   a cited paper counts as support.
2. **Distinctive terms only.** A term occurring across several papers is field
   vocabulary ("model", "urban", "effects") and carries no attribution signal.
   Only a term concentrated in at most `max_source_papers` other papers is
   reported, which is what separates "nutrition" from "study".

Terms absent from the entire evidence set are ignored: those are ordinary
paraphrase, not misattribution, and are out of scope here.
"""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from econ_paper_cli.domain.errors import DomainError

CLAIM_GROUNDING_METHOD = "cross-paper-leakage-v1"

DEFAULT_MAX_SOURCE_PAPERS = 1

# Function words occur in nearly every passage, so their presence under another
# paper means nothing. Numerals are NOT excluded -- a sample size or year lifted
# from the wrong paper is exactly the failure this check exists to catch.
_FUNCTION_WORDS = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "may",
        "me",
        "might",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    }
)

# The discourse vocabulary of research writing: words a synthesis layer uses to
# frame *any* claim about *any* paper. They describe the act of studying rather
# than a subject matter, so their presence under another paper identifies
# nothing. Excluding them is what separates "the study finds" from "the study
# finds worsening nutrition" -- observed as a false positive that suppressed an
# entire answer over the word "study", which the prompt template itself tells
# the model to use. Subject-matter nouns ("nutrition", "grocery", "transit")
# are deliberately absent from this list.
_RESEARCH_DISCOURSE_WORDS = frozenset(
    {
        "analysis",
        "analyze",
        "analyzed",
        "analyses",
        "approach",
        "article",
        "author",
        "authors",
        "conclude",
        "concludes",
        "data",
        "dataset",
        "describe",
        "described",
        "describes",
        "discuss",
        "discussed",
        "discusses",
        "document",
        "documents",
        "effect",
        "effects",
        "estimate",
        "estimated",
        "estimates",
        "evidence",
        "examine",
        "examined",
        "examines",
        "explore",
        "explores",
        "find",
        "finding",
        "findings",
        "finds",
        "found",
        "impact",
        "impacts",
        "indicate",
        "indicates",
        "investigate",
        "investigates",
        "literature",
        "measure",
        "measured",
        "measures",
        "method",
        "methods",
        "outcome",
        "outcomes",
        "paper",
        "papers",
        "provide",
        "provides",
        "report",
        "reported",
        "reports",
        "research",
        "result",
        "results",
        "review",
        "sample",
        "show",
        "shows",
        "showed",
        "studied",
        "studies",
        "study",
        "suggest",
        "suggests",
        "using",
        "work",
    }
)

_IGNORED_TERMS = _FUNCTION_WORDS | _RESEARCH_DISCOURSE_WORDS


class ClaimGroundingError(DomainError):
    """Raised when claim-grounding inputs violate their structural contract."""


@dataclass(frozen=True, slots=True)
class GroundingEvidence:
    """One retrieved passage reduced to what grounding needs: paper and text."""

    citation_id: str
    paper_id: str
    text: str

    def __post_init__(self) -> None:
        """Validate that every field is present and non-empty."""
        for field in ("citation_id", "paper_id", "text"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ClaimGroundingError(f"{field} must be a non-empty string.")


@dataclass(frozen=True, slots=True)
class ClaimGroundingResult:
    """Immutable per-claim verdict naming the exact terms that leaked."""

    claim_index: int
    grounded: bool
    leaked_terms: tuple[str, ...]
    leaked_from_papers: tuple[str, ...]
    method: str = CLAIM_GROUNDING_METHOD

    def __post_init__(self) -> None:
        """Validate the verdict's internal consistency."""
        if not isinstance(self.claim_index, int) or isinstance(self.claim_index, bool):
            raise ClaimGroundingError("claim_index must be an integer.")
        if self.claim_index < 0:
            raise ClaimGroundingError("claim_index must be non-negative.")
        if not isinstance(self.grounded, bool):
            raise ClaimGroundingError("grounded must be a boolean.")
        _validate_string_tuple("leaked_terms", self.leaked_terms)
        _validate_string_tuple("leaked_from_papers", self.leaked_from_papers)
        if self.grounded and (self.leaked_terms or self.leaked_from_papers):
            raise ClaimGroundingError(
                "A grounded claim must not report leaked terms or papers."
            )
        if not self.grounded and not self.leaked_terms:
            raise ClaimGroundingError(
                "An ungrounded claim must report at least one leaked term."
            )
        if not self.grounded and not self.leaked_from_papers:
            raise ClaimGroundingError(
                "An ungrounded claim must report at least one source paper."
            )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible verdict mapping."""
        return {
            "claim_index": self.claim_index,
            "grounded": self.grounded,
            "leaked_terms": list(self.leaked_terms),
            "leaked_from_papers": list(self.leaked_from_papers),
            "method": self.method,
        }


def tokenize_for_grounding(text: str) -> tuple[str, ...]:
    """Tokenize with the `bm25-v1` rules so claim and passage terms compare.

    NFKC normalization, casefolding, and a character scan splitting on
    non-alphanumeric characters. Kept deliberately identical to the retrieval
    tokenizer: a term BM25 treats as one unit must not split differently here,
    or the grounding verdict would disagree with what was actually retrieved.
    """
    if not isinstance(text, str):
        raise ClaimGroundingError("text must be a string.")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def check_claim_grounding(
    *,
    claim_index: int,
    claim_text: str,
    cited_ids: Sequence[str],
    evidence: Sequence[GroundingEvidence],
    question: str = "",
    max_source_papers: int = DEFAULT_MAX_SOURCE_PAPERS,
) -> ClaimGroundingResult:
    """Report distinctive terms a claim took from papers it does not cite.

    `evidence` must be the whole retrieved set, not only the cited passages --
    the uncited papers are precisely what the claim is checked against. Terms
    already present in the user's question are exempt, because a generator
    echoing the question back is not attributing anything to anyone.
    """
    if not isinstance(claim_text, str) or not claim_text.strip():
        raise ClaimGroundingError("claim_text must be a non-empty string.")
    if not isinstance(cited_ids, (list, tuple)) or not cited_ids:
        raise ClaimGroundingError("cited_ids must be a non-empty list or tuple.")
    if not isinstance(evidence, (list, tuple)) or not evidence:
        raise ClaimGroundingError("evidence must be a non-empty list or tuple.")
    if any(not isinstance(item, GroundingEvidence) for item in evidence):
        raise ClaimGroundingError(
            "evidence must contain only GroundingEvidence instances."
        )
    if not isinstance(max_source_papers, int) or isinstance(max_source_papers, bool):
        raise ClaimGroundingError("max_source_papers must be an integer.")
    if max_source_papers < 1:
        raise ClaimGroundingError("max_source_papers must be at least 1.")

    paper_by_citation: Mapping[str, str] = {
        item.citation_id: item.paper_id for item in evidence
    }
    unknown = sorted({item for item in cited_ids if item not in paper_by_citation})
    if unknown:
        raise ClaimGroundingError(
            f"cited_ids reference unknown evidence: {', '.join(unknown)}."
        )

    cited_papers = {paper_by_citation[item] for item in cited_ids}

    # Paper-level term sets: every retrieved passage of a paper supports every
    # claim citing that paper, whichever rank the passage happened to land at.
    terms_by_paper: dict[str, set[str]] = {}
    for item in evidence:
        terms_by_paper.setdefault(item.paper_id, set()).update(
            tokenize_for_grounding(item.text)
        )

    supported_terms: set[str] = set()
    for paper_id in cited_papers:
        supported_terms.update(terms_by_paper[paper_id])
    if question:
        supported_terms.update(tokenize_for_grounding(question))

    other_papers = {
        paper_id: terms
        for paper_id, terms in terms_by_paper.items()
        if paper_id not in cited_papers
    }

    leaked_terms: list[str] = []
    leaked_from: set[str] = set()
    seen: set[str] = set()
    for token in tokenize_for_grounding(claim_text):
        if token in seen or token in _IGNORED_TERMS or token in supported_terms:
            continue
        sources = sorted(
            paper_id for paper_id, terms in other_papers.items() if token in terms
        )
        if not sources:
            # Absent from every other paper too: ordinary paraphrase, not a
            # cross-paper attribution error, and out of scope for this check.
            continue
        if len(sources) > max_source_papers:
            # Shared across several papers: field vocabulary, not a fingerprint
            # of any one study.
            continue
        seen.add(token)
        leaked_terms.append(token)
        leaked_from.update(sources)

    if not leaked_terms:
        return ClaimGroundingResult(
            claim_index=claim_index,
            grounded=True,
            leaked_terms=(),
            leaked_from_papers=(),
        )
    return ClaimGroundingResult(
        claim_index=claim_index,
        grounded=False,
        leaked_terms=tuple(leaked_terms),
        leaked_from_papers=tuple(sorted(leaked_from)),
    )


def _validate_string_tuple(field: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise ClaimGroundingError(f"{field} must be a tuple.")
    if any(not isinstance(item, str) or not item for item in value):
        raise ClaimGroundingError(f"{field} must contain only non-empty strings.")
