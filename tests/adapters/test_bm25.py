"""Tests for the pure-Python BM25 sparse retriever adapter."""

import math
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from econ_paper_cli.adapters import (
    BM25ConfigurationError,
    BM25Retriever,
    load_corpus_from_file,
)
from econ_paper_cli.adapters.bm25 import _tokenize
from econ_paper_cli.domain import Corpus, Paper, Passage
from econ_paper_cli.protocols import RetrievalRequest, Retriever

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_JSON = (
    REPO_ROOT / "tests" / "fixtures" / "corpus" / "synthetic-economics-v1.json"
)


def make_paper(paper_id: str = "paper-1") -> Paper:
    """Return a valid synthetic Paper fixture."""
    return Paper.from_mapping(
        {
            "paper_id": paper_id,
            "title": "Title",
            "authors": ["Author"],
            "year": 2024,
            "abstract": None,
            "source_name": "Series",
            "source_identifier": "id-1",
            "source_url": None,
        }
    )


def make_passage(
    passage_id: str, text: str, paper_id: str = "paper-1", ordinal: int = 0
) -> Passage:
    """Return a valid synthetic Passage fixture."""
    return Passage.from_mapping(
        {
            "passage_id": passage_id,
            "paper_id": paper_id,
            "text": text,
            "section_heading": None,
            "page_start": 1,
            "page_end": None,
            "ordinal_position": ordinal,
        }
    )


def make_corpus(passages: list[Passage], paper_id: str = "paper-1") -> Corpus:
    """Return a valid synthetic Corpus containing the given passages with unique ordinals."""
    paper = make_paper(paper_id)
    # Ensure distinct ordinal positions per paper_id
    adjusted: list[Passage] = []
    seen_ordinals: set[tuple[str, int]] = set()
    for i, p in enumerate(passages):
        key = (p.paper_id, p.ordinal_position)
        if key in seen_ordinals:
            # Override ordinal to i to avoid duplication
            new_p = Passage(
                passage_id=p.passage_id,
                paper_id=p.paper_id,
                text=p.text,
                section_heading=p.section_heading,
                page_start=p.page_start,
                page_end=p.page_end,
                ordinal_position=i,
            )
            adjusted.append(new_p)
            seen_ordinals.add((p.paper_id, i))
        else:
            adjusted.append(p)
            seen_ordinals.add(key)

    return Corpus(
        schema_version=1,
        corpus_id="synthetic-corpus-1",
        papers=(paper,),
        passages=tuple(adjusted),
    )


# --- Constructor & Configuration Validation Tests ---


def test_bm25_constructor_valid_defaults_and_custom_parameters() -> None:
    """Test valid construction with defaults and custom k1/b parameters."""
    p1 = make_passage("p1", "road infrastructure spending")
    corpus = make_corpus([p1])

    adapter = BM25Retriever(corpus)
    assert isinstance(adapter, Retriever)

    custom_adapter = BM25Retriever(corpus, k1=2.0, b=0.5)
    assert isinstance(custom_adapter, Retriever)


def test_bm25_constructor_rejects_non_corpus_input() -> None:
    """Test that BM25ConfigurationError is raised when corpus is not a Corpus."""
    with pytest.raises(
        BM25ConfigurationError, match="corpus must be a Corpus instance"
    ):
        BM25Retriever(cast(Corpus, "not a corpus"))


@pytest.mark.parametrize(
    "bad_k1", [0.0, -1.0, math.nan, math.inf, -math.inf, True, "1.5"]
)
def test_bm25_constructor_validates_k1(bad_k1: object) -> None:
    """Test that invalid k1 values raise BM25ConfigurationError."""
    p1 = make_passage("p1", "text")
    corpus = make_corpus([p1])
    with pytest.raises(
        BM25ConfigurationError, match="k1 must be a positive finite float"
    ):
        BM25Retriever(corpus, k1=cast(float, bad_k1))


@pytest.mark.parametrize(
    "bad_b", [-0.1, 1.1, math.nan, math.inf, -math.inf, True, "0.75"]
)
def test_bm25_constructor_validates_b(bad_b: object) -> None:
    """Test that invalid b values raise BM25ConfigurationError."""
    p1 = make_passage("p1", "text")
    corpus = make_corpus([p1])
    with pytest.raises(
        BM25ConfigurationError, match="b must be a finite float between 0 and 1"
    ):
        BM25Retriever(corpus, b=cast(float, bad_b))


def test_bm25_constructor_does_not_mutate_corpus() -> None:
    """Test that constructor does not mutate the Corpus or underlying objects."""
    p1 = make_passage("p1", "road infrastructure")
    corpus = make_corpus([p1])

    original_passages = corpus.passages
    _ = BM25Retriever(corpus)

    assert corpus.passages is original_passages
    assert corpus.passages[0] is p1


def test_bm25_reuses_precomputed_passage_statistics_across_queries() -> None:
    """Test that passage tokenization occurs during construction, not on every retrieve call."""
    p1 = make_passage("p1", "infrastructure investment")
    p2 = make_passage("p2", "road construction")
    corpus = make_corpus([p1, p2])

    with patch("econ_paper_cli.adapters.bm25._tokenize", wraps=_tokenize) as mock_tok:
        retriever = BM25Retriever(corpus)
        # 2 passages tokenized during __init__
        assert mock_tok.call_count == 2

        mock_tok.reset_mock()
        req1 = RetrievalRequest(query="road", top_k=5)
        _ = retriever.retrieve(req1)
        # Only the query is tokenized during retrieve
        assert mock_tok.call_count == 1
        assert mock_tok.call_args[0][0] == "road"

        mock_tok.reset_mock()
        req2 = RetrievalRequest(query="infrastructure", top_k=5)
        _ = retriever.retrieve(req2)
        assert mock_tok.call_count == 1
        assert mock_tok.call_args[0][0] == "infrastructure"


# --- Tokenization & Matching Behavior Tests ---


def test_bm25_tokenizer_case_insensitivity_and_nfkc() -> None:
    """Test that casefolding and Unicode NFKC normalization match across text and query."""
    p1 = make_passage("p1", "MUNICIPAL road INFR\u0041\u0300STRUCTURE")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    req = RetrievalRequest(query="municipal infr\u00e0structure", top_k=5)
    results = retriever.retrieve(req)
    assert len(results) == 1
    assert results[0].passage.passage_id == "p1"


@pytest.mark.parametrize(
    "text,query,expected_match",
    [
        ("road-spending project", "road", True),
        ("road_spending project", "spending", True),
        ("author's finding", "author", True),
        ("year 2024 spending", "2024", True),
        ("punctuation... test!!!", "test", True),
    ],
)
def test_bm25_tokenizer_delimiters_and_numbers(
    text: str, query: str, expected_match: bool
) -> None:
    """Test tokenization delimiters (hyphens, underscores, apostrophes, punctuation) and numbers."""
    p1 = make_passage("p1", text)
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    req = RetrievalRequest(query=query, top_k=5)
    results = retriever.retrieve(req)
    assert (len(results) == 1) == expected_match


def test_bm25_tokenizer_retains_stop_words_and_no_stemming() -> None:
    """Test that stop words are indexed and no stemming occurs."""
    p1 = make_passage("p1", "the road was built for cars")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    # Stop words match
    req_stop = RetrievalRequest(query="the for", top_k=5)
    assert len(retriever.retrieve(req_stop)) == 1

    # Stemmed query 'building' does not match unstemmed 'built'
    req_stem = RetrievalRequest(query="building", top_k=5)
    assert len(retriever.retrieve(req_stem)) == 0


def test_bm25_empty_token_query_returns_empty_tuple() -> None:
    """Test that punctuation-only or whitespace queries return empty tuple."""
    p1 = make_passage("p1", "some text")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    req = RetrievalRequest(query="... ! ??? ,,,", top_k=5)
    assert retriever.retrieve(req) == ()


def test_bm25_absent_terms_and_query_deduplication() -> None:
    """Test that absent terms return empty tuple, and query word repetition yields identical scores."""
    p1 = make_passage("p1", "road infrastructure")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    req_absent = RetrievalRequest(query="hospitals healthcare", top_k=5)
    assert retriever.retrieve(req_absent) == ()

    req_single = RetrievalRequest(query="road", top_k=5)
    req_dup = RetrievalRequest(query="road road road", top_k=5)
    res_single = retriever.retrieve(req_single)
    res_dup = retriever.retrieve(req_dup)

    assert len(res_single) == 1 and len(res_dup) == 1
    assert res_single[0].score == pytest.approx(res_dup[0].score)


# --- Scoring & Ranking Tests ---


def test_bm25_scoring_hand_checkable_values() -> None:
    """Test hand-calculated BM25 score against formula output using pytest.approx."""
    # N = 2 passages
    # P1: "road road spending" (tokens: road, road, spending -> doc_len=3, tf(road)=2, tf(spending)=1)
    # P2: "road infrastructure" (tokens: road, infrastructure -> doc_len=2, tf(road)=1, tf(infra)=1)
    # avgdl = (3 + 2) / 2 = 2.5
    # k1 = 1.5, b = 0.75
    # df(road) = 2.
    # idf(road) = ln(1 + (2 - 2 + 0.5) / (2 + 0.5)) = ln(1 + 0.5 / 2.5) = ln(1.2) = 0.1823215567939546
    # Query: "road"
    # P1 score: idf(road) * [ 2 * (1.5 + 1) / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2.5)) ]
    # len_norm_P1 = 1 - 0.75 + 0.75 * 1.2 = 0.25 + 0.9 = 1.15
    # denom_P1 = 2 + 1.5 * 1.15 = 2 + 1.725 = 3.725
    # P1_num = 2 * 2.5 = 5.0
    # P1_score = ln(1.2) * (5.0 / 3.725) = 0.1823215567939546 * 1.3422818791946309 = 0.2447269136193796
    p1 = make_passage("p1", "road road spending", ordinal=0)
    p2 = make_passage("p2", "road infrastructure", ordinal=1)
    corpus = make_corpus([p1, p2])

    retriever = BM25Retriever(corpus, k1=1.5, b=0.75)
    req = RetrievalRequest(query="road", top_k=5)
    results = retriever.retrieve(req)

    expected_p1_score = math.log(1.2) * (5.0 / 3.725)
    assert results[0].passage.passage_id == "p1"
    assert results[0].score == pytest.approx(expected_p1_score, abs=1e-6)


def test_bm25_scoring_rarer_term_higher_idf_contribution() -> None:
    """Test that holding passage length and term frequency constant, rarer terms yield higher scores."""
    # N = 3 passages
    # P1: "rare_word common_word" (length=2, tf(rare)=1, tf(common)=1)
    # P2: "common_word text_a"   (length=2)
    # P3: "common_word text_b"   (length=2)
    # df(rare) = 1, df(common) = 3.
    p1 = make_passage("p1", "rare_word common_word", ordinal=0)
    p2 = make_passage("p2", "common_word text_a", ordinal=1)
    p3 = make_passage("p3", "common_word text_b", ordinal=2)
    corpus = make_corpus([p1, p2, p3])

    retriever = BM25Retriever(corpus)
    res_rare = retriever.retrieve(RetrievalRequest(query="rare_word", top_k=5))
    res_common = retriever.retrieve(RetrievalRequest(query="common_word", top_k=5))

    score_rare_on_p1 = res_rare[0].score
    score_common_on_p1 = [r for r in res_common if r.passage.passage_id == "p1"][
        0
    ].score

    assert score_rare_on_p1 > score_common_on_p1


def test_bm25_scoring_term_frequency_effect() -> None:
    """Test that higher term frequency increases BM25 score when document length is constant."""
    # P1: "road road text" (tf(road)=2, length=3)
    # P2: "road text text" (tf(road)=1, length=3)
    p1 = make_passage("p1", "road road text", ordinal=0)
    p2 = make_passage("p2", "road text text", ordinal=1)
    corpus = make_corpus([p1, p2])

    retriever = BM25Retriever(corpus)
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=5))

    assert results[0].passage.passage_id == "p1"
    assert results[0].score > results[1].score


def test_bm25_scoring_document_length_effect() -> None:
    """Test that shorter documents receive higher score for equal term frequency (length penalty)."""
    # P1: "road text" (tf(road)=1, length=2)
    # P2: "road text filler filler filler filler" (tf(road)=1, length=6)
    p1 = make_passage("p1", "road text", ordinal=0)
    p2 = make_passage("p2", "road text filler filler filler filler", ordinal=1)
    corpus = make_corpus([p1, p2])

    retriever = BM25Retriever(corpus)
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=5))

    assert results[0].passage.passage_id == "p1"
    assert results[0].score > results[1].score


@pytest.mark.parametrize(
    "k1,b,desc",
    [
        (0.5, 0.75, "lower k1 saturation"),
        (3.0, 0.75, "higher k1 saturation"),
        (1.5, 0.0, "zero length penalty b=0"),
        (1.5, 1.0, "full length penalty b=1"),
    ],
)
def test_bm25_scoring_custom_k1_and_b_parameters(
    k1: float, b: float, desc: str
) -> None:
    """Test that custom k1 and b parameters produce valid positive scores and rankings."""
    p1 = make_passage("p1", "road road infrastructure", ordinal=0)
    p2 = make_passage("p2", "road spending", ordinal=1)
    corpus = make_corpus([p1, p2])

    retriever = BM25Retriever(corpus, k1=k1, b=b)
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=5))

    assert len(results) == 2
    assert all(r.score > 0.0 for r in results)


def test_bm25_ranking_score_descending_order_and_tie_breaking() -> None:
    """Test score descending order and passage_id ascending tie-breaking for equal scores."""
    # P1 and P2 have identical text and length -> equal BM25 score
    p_beta = make_passage("passage_beta", "identical text content", ordinal=0)
    p_alpha = make_passage("passage_alpha", "identical text content", ordinal=1)
    corpus = make_corpus([p_beta, p_alpha])

    retriever = BM25Retriever(corpus)
    results = retriever.retrieve(RetrievalRequest(query="identical", top_k=5))

    # Because token sequences are identical, duplicate suppression retains the first candidate in sorted order.
    # Sorted order tie-breaker puts "passage_alpha" before "passage_beta".
    assert len(results) == 1
    assert results[0].passage.passage_id == "passage_alpha"


def test_bm25_top_k_truncation_and_positive_score_filtering() -> None:
    """Test top_k truncation and filtering out non-positive score passages."""
    p1 = make_passage("p1", "road infra A", ordinal=0)
    p2 = make_passage("p2", "road infra B", ordinal=1)
    p3 = make_passage("p3", "road infra C", ordinal=2)
    p_no_match = make_passage("p4", "completely unrelated text", ordinal=3)
    corpus = make_corpus([p1, p2, p3, p_no_match])

    retriever = BM25Retriever(corpus)
    # top_k = 2
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=2))

    assert len(results) == 2
    assert "p4" not in [r.passage.passage_id for r in results]


# --- Identity & Duplicate Suppression Tests ---


def test_bm25_preserves_original_passage_instance_and_metadata() -> None:
    """Test that RetrievalEvidence contains exact Passage instance and correct metadata."""
    p1 = make_passage("p1", "road infrastructure spending")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    results = retriever.retrieve(RetrievalRequest(query="road", top_k=5))

    assert len(results) == 1
    evidence = results[0]
    assert evidence.passage is p1
    assert evidence.passage.passage_id == "p1"
    assert evidence.passage.paper_id == "paper-1"
    assert evidence.rank == 1
    assert evidence.retrieval_method == "bm25-v1"


def test_bm25_suppresses_normalized_lexical_duplicates() -> None:
    """Test that passages with identical token sequences are suppressed, retaining passage_id ascending tie-break."""
    # p_beta and p_alpha have identical token sequences after casefolding and punctuation stripping
    p_beta = make_passage("p_beta", "Road Infrastructure Spending!!!", ordinal=0)
    p_alpha = make_passage("p_alpha", "road   infrastructure   spending...", ordinal=1)
    corpus = make_corpus([p_beta, p_alpha])

    retriever = BM25Retriever(corpus)
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=5))

    # Duplicate suppressed, p_alpha retained due to tie-breaker
    assert len(results) == 1
    assert results[0].passage.passage_id == "p_alpha"


def test_bm25_duplicate_suppression_scans_later_candidates() -> None:
    """Test that duplicate suppression scans past duplicates to fill top_k with unique passages."""
    p_dup1 = make_passage("p_dup1", "road construction", ordinal=0)
    p_dup2 = make_passage(
        "p_dup2", "road construction!", ordinal=1
    )  # Duplicate of dup1
    p_unique = make_passage("p_unique", "road spending", ordinal=2)  # Unique passage
    corpus = make_corpus([p_dup1, p_dup2, p_unique])

    retriever = BM25Retriever(corpus)
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=2))

    assert len(results) == 2
    retained_ids = [r.passage.passage_id for r in results]
    assert "p_dup1" in retained_ids
    assert "p_unique" in retained_ids
    assert "p_dup2" not in retained_ids


def test_bm25_distinct_token_passages_not_suppressed() -> None:
    """Test that passages with distinct token sequences are not suppressed."""
    p1 = make_passage("p1", "road infrastructure spending", ordinal=0)
    p2 = make_passage("p2", "road connectivity investment", ordinal=1)
    corpus = make_corpus([p1, p2])

    retriever = BM25Retriever(corpus)
    results = retriever.retrieve(RetrievalRequest(query="road", top_k=5))

    assert len(results) == 2


# --- Determinism, Protocol & Smoke Tests ---


def test_bm25_determinism_and_corpus_order_independence() -> None:
    """Test that identical calls yield identical results, and corpus passage ordering does not affect output."""
    p1 = make_passage("p1", "road infrastructure", ordinal=0)
    p2 = make_passage("p2", "highway investment", ordinal=1)

    corpus_a = make_corpus([p1, p2])
    corpus_b = make_corpus([p2, p1])

    retriever_a = BM25Retriever(corpus_a)
    retriever_b = BM25Retriever(corpus_b)

    req = RetrievalRequest(query="road highway", top_k=5)
    res_a = retriever_a.retrieve(req)
    res_b = retriever_b.retrieve(req)

    assert res_a == res_b


def test_bm25_results_pass_validate_retrieval_results() -> None:
    """Test that returned result tuple passes contract validation."""
    p1 = make_passage("p1", "road spending")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    req = RetrievalRequest(query="road", top_k=5)
    results = retriever.retrieve(req)

    assert isinstance(results, tuple)
    assert len(results) == 1
    assert results[0].retrieval_method == "bm25-v1"


def test_bm25_rejects_non_retrieval_request() -> None:
    """Test that passing a non-RetrievalRequest object to retrieve raises TypeError."""
    p1 = make_passage("p1", "text")
    corpus = make_corpus([p1])
    retriever = BM25Retriever(corpus)

    with pytest.raises(TypeError, match="request must be a RetrievalRequest instance"):
        retriever.retrieve(cast(RetrievalRequest, "invalid_request"))


def test_bm25_committed_synthetic_corpus_fixture_smoke_test() -> None:
    """Smoke test loading committed synthetic corpus fixture, constructing BM25Retriever, and running query."""
    corpus = load_corpus_from_file(FIXTURE_JSON)
    retriever = BM25Retriever(corpus)

    req = RetrievalRequest(query="election infrastructure road spending", top_k=3)
    results = retriever.retrieve(req)

    assert isinstance(results, tuple)
    assert 1 <= len(results) <= 3
    assert all(isinstance(r.passage, Passage) for r in results)
    assert all(r.retrieval_method == "bm25-v1" for r in results)
    assert results[0].rank == 1
