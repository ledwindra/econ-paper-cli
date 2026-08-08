"""Structural cross-paper leakage detection for generated claims."""

import pytest

from econ_paper_cli.domain.claim_grounding import (
    CLAIM_GROUNDING_METHOD,
    ClaimGroundingError,
    ClaimGroundingResult,
    GroundingEvidence,
    check_claim_grounding,
    tokenize_for_grounding,
)

TRANSIT_A = GroundingEvidence(
    citation_id="e1",
    paper_id="paper-transit",
    text=(
        "We develop a quantitative urban model of the TransMilenio bus rapid "
        "transit system and evaluate its welfare effects across worker groups."
    ),
)
TRANSIT_B = GroundingEvidence(
    citation_id="e2",
    paper_id="paper-transit",
    text=(
        "The model is estimated with reduced-form elasticities recovered from "
        "the staggered opening of transit corridors."
    ),
)
FOOD = GroundingEvidence(
    citation_id="e3",
    paper_id="paper-food",
    text=(
        "Households reduce grocery store trips and increase drug and dollar "
        "store trips, worsening the nutrition of their purchases."
    ),
)
EVIDENCE = (TRANSIT_A, TRANSIT_B, FOOD)


def test_claim_using_another_papers_vocabulary_is_reported_as_leaked() -> None:
    """The observed production failure: a fluent sentence citing one paper
    while describing another paper's outcomes. Every citation identifier is
    real, so only term provenance can distinguish it from an honest claim."""
    result = check_claim_grounding(
        claim_index=0,
        claim_text=(
            "The transit study finds households reduce grocery trips and "
            "worsen the nutrition of their purchases."
        ),
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )

    assert result.grounded is False
    assert "grocery" in result.leaked_terms
    assert "nutrition" in result.leaked_terms
    assert result.leaked_from_papers == ("paper-food",)
    assert result.method == CLAIM_GROUNDING_METHOD


def test_claim_citing_every_paper_it_draws_from_is_grounded() -> None:
    """The same wording is legitimate once it cites the papers it came from,
    so the check reports misattribution rather than penalizing breadth."""
    result = check_claim_grounding(
        claim_index=0,
        claim_text=(
            "The transit study finds households reduce grocery trips and "
            "worsen the nutrition of their purchases."
        ),
        cited_ids=["e1", "e3"],
        evidence=EVIDENCE,
    )

    assert result.grounded is True
    assert result.leaked_terms == ()
    assert result.leaked_from_papers == ()


def test_sibling_passages_of_the_cited_paper_count_as_support() -> None:
    """Scoping is per paper, not per passage. A claim citing e1 may use wording
    that only appears in e2, because both are the same paper retrieved twice --
    passage-level scoping flagged exactly this as a false positive."""
    result = check_claim_grounding(
        claim_index=0,
        claim_text="The model recovers reduced-form elasticities from corridors.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )

    assert result.grounded is True


def test_terms_shared_across_several_papers_are_not_reported() -> None:
    """Field vocabulary present in more than `max_source_papers` other papers
    identifies no particular study, so it must not be treated as a fingerprint."""
    shared_one = GroundingEvidence(
        citation_id="e4", paper_id="paper-x", text="Estimates of welfare effects."
    )
    shared_two = GroundingEvidence(
        citation_id="e5", paper_id="paper-y", text="Estimates of welfare effects."
    )
    cited = GroundingEvidence(
        citation_id="e6", paper_id="paper-z", text="A different topic entirely."
    )

    result = check_claim_grounding(
        claim_index=0,
        claim_text="Welfare estimates are reported.",
        cited_ids=["e6"],
        evidence=(shared_one, shared_two, cited),
    )

    assert result.grounded is True


def test_max_source_papers_widens_what_counts_as_distinctive() -> None:
    """The threshold is an upper bound on how many other papers may share a
    term before it stops being a fingerprint, so raising it reports *more*.
    A term in two other papers is field vocabulary at the default and a
    distinctive term at `max_source_papers=2`."""
    shared_one = GroundingEvidence(
        citation_id="e4", paper_id="paper-x", text="Estimates of welfare effects."
    )
    shared_two = GroundingEvidence(
        citation_id="e5", paper_id="paper-y", text="Estimates of welfare effects."
    )
    cited = GroundingEvidence(
        citation_id="e6", paper_id="paper-z", text="A different topic entirely."
    )
    evidence = (shared_one, shared_two, cited)

    default = check_claim_grounding(
        claim_index=0,
        claim_text="Welfare estimates are reported.",
        cited_ids=["e6"],
        evidence=evidence,
    )
    widened = check_claim_grounding(
        claim_index=0,
        claim_text="Welfare estimates are reported.",
        cited_ids=["e6"],
        evidence=evidence,
        max_source_papers=2,
    )

    assert default.grounded is True
    assert widened.grounded is False
    assert widened.leaked_from_papers == ("paper-x", "paper-y")


def test_paraphrase_absent_from_all_evidence_is_not_reported() -> None:
    """Wording the generator invented appears in no passage at all. That is a
    different failure from misattribution and is out of scope here, so ordinary
    rewording must never be penalized."""
    result = check_claim_grounding(
        claim_index=0,
        claim_text="The quantitative urban model appears methodologically robust.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )

    assert result.grounded is True


def test_question_wording_is_exempt_from_leakage() -> None:
    """A generator echoing the user's own question attributes nothing, so
    question terms must not be charged against the cited paper."""
    leaked = check_claim_grounding(
        claim_index=0,
        claim_text="Nutrition outcomes are discussed.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )
    exempt = check_claim_grounding(
        claim_index=0,
        claim_text="Nutrition outcomes are discussed.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
        question="What happens to nutrition?",
    )

    assert leaked.grounded is False
    assert exempt.grounded is True


def test_numerals_are_checked_rather_than_treated_as_noise() -> None:
    """A sample size or year taken from the wrong paper is precisely the error
    this check exists to catch, so digits must not be filtered out."""
    other = GroundingEvidence(
        citation_id="e4", paper_id="paper-x", text="We study 138 metropolitan areas."
    )
    cited = GroundingEvidence(
        citation_id="e5", paper_id="paper-y", text="We study metropolitan areas."
    )

    result = check_claim_grounding(
        claim_index=0,
        claim_text="The sample covers 138 metropolitan areas.",
        cited_ids=["e5"],
        evidence=(other, cited),
    )

    assert result.grounded is False
    assert "138" in result.leaked_terms


def test_leaked_terms_are_deduplicated_in_first_occurrence_order() -> None:
    """The verdict is user-facing, so a term repeated in one sentence must be
    reported once, in the order a reader encounters it."""
    result = check_claim_grounding(
        claim_index=0,
        claim_text="Grocery trips fell; grocery nutrition worsened.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )

    assert result.leaked_terms == ("grocery", "trips", "nutrition")


def test_tokenizer_matches_the_bm25_v1_rules() -> None:
    """Grounding must split terms exactly as retrieval does, or a verdict could
    disagree with the index that produced the evidence."""
    assert tokenize_for_grounding("Reduced-form BRT: 4 units!") == (
        "reduced",
        "form",
        "brt",
        "4",
        "units",
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"claim_text": "   "}, "non-empty string"),
        ({"cited_ids": []}, "non-empty list"),
        ({"cited_ids": ["e404"]}, "unknown evidence"),
        ({"evidence": ()}, "non-empty list"),
        ({"max_source_papers": 0}, "at least 1"),
    ),
)
def test_invalid_inputs_are_rejected(kwargs: dict[str, object], message: str) -> None:
    base: dict[str, object] = {
        "claim_index": 0,
        "claim_text": "A claim.",
        "cited_ids": ["e1"],
        "evidence": EVIDENCE,
    }
    base.update(kwargs)

    with pytest.raises(ClaimGroundingError, match=message):
        check_claim_grounding(**base)  # type: ignore[arg-type]


def test_result_rejects_internally_inconsistent_verdicts() -> None:
    """A verdict is evidence shown to a user, so "grounded with leaked terms"
    and "ungrounded with nothing to point at" must be unconstructible."""
    with pytest.raises(ClaimGroundingError, match="must not report leaked"):
        ClaimGroundingResult(
            claim_index=0,
            grounded=True,
            leaked_terms=("grocery",),
            leaked_from_papers=("paper-food",),
        )
    with pytest.raises(ClaimGroundingError, match="at least one leaked term"):
        ClaimGroundingResult(
            claim_index=0, grounded=False, leaked_terms=(), leaked_from_papers=()
        )
    with pytest.raises(ClaimGroundingError, match="at least one source paper"):
        ClaimGroundingResult(
            claim_index=0,
            grounded=False,
            leaked_terms=("grocery",),
            leaked_from_papers=(),
        )


def test_research_discourse_words_do_not_trigger_leakage() -> None:
    """Regression: an entire real answer was suppressed because the word
    "study" happened to appear in one uncited paper. Words describing the act
    of studying frame any claim about any paper and identify no subject, so
    they must never count as a fingerprint -- while subject-matter nouns in the
    very same sentence still must."""
    framing_only = check_claim_grounding(
        claim_index=0,
        claim_text="The study finds that the reported effects are substantial.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )
    with_subject_matter = check_claim_grounding(
        claim_index=0,
        claim_text="The study finds that grocery purchases worsened.",
        cited_ids=["e1"],
        evidence=EVIDENCE,
    )

    assert framing_only.grounded is True
    assert with_subject_matter.grounded is False
    assert with_subject_matter.leaked_terms == ("grocery", "purchases")
    assert "study" not in with_subject_matter.leaked_terms
    assert "finds" not in with_subject_matter.leaked_terms
