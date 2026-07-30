"""Unit and contract tests for backend-independent retrieval evaluation."""

from dataclasses import replace

import pytest

from econ_paper_cli.domain import Corpus, Paper, Passage, RetrievalEvidence
from econ_paper_cli.evaluation import (
    RetrievalBenchmark,
    RetrievalBenchmarkCase,
    RetrievalEvaluationError,
    RetrievalThreshold,
    corpus_retrieval_fingerprint,
    evaluate_retriever,
    find_threshold_failures,
)
from econ_paper_cli.protocols import RetrievalRequest, RetrievalResultValidationError


def make_corpus() -> Corpus:
    paper = Paper.from_mapping(
        {
            "paper_id": "paper-1",
            "title": "Synthetic paper",
            "authors": ["Researcher"],
            "year": 2024,
            "abstract": None,
            "source_name": "Synthetic source",
            "source_identifier": "source-1",
            "source_url": None,
        }
    )
    passages = tuple(
        Passage.from_mapping(
            {
                "passage_id": passage_id,
                "paper_id": "paper-1",
                "text": text,
                "section_heading": None,
                "page_start": index + 1,
                "page_end": None,
                "ordinal_position": index,
            }
        )
        for index, (passage_id, text) in enumerate(
            (("p1", "first passage"), ("p2", "second passage"), ("p3", "third passage"))
        )
    )
    return Corpus(
        schema_version=1,
        corpus_id="corpus-1",
        papers=(paper,),
        passages=passages,
    )


class RankedTestRetriever:
    """Return configured passage IDs in deterministic order for each query."""

    def __init__(self, corpus: Corpus, rankings: dict[str, tuple[str, ...]]) -> None:
        self._passages = {passage.passage_id: passage for passage in corpus.passages}
        self._rankings = rankings
        self.requested_top_k: list[int] = []

    def retrieve(self, request: RetrievalRequest) -> tuple[RetrievalEvidence, ...]:
        self.requested_top_k.append(request.top_k)
        passage_ids = self._rankings.get(request.query, ())[: request.top_k]
        results = tuple(
            RetrievalEvidence(
                passage=self._passages[passage_id],
                score=float(len(passage_ids) - index),
                rank=index + 1,
                retrieval_method="test-v1",
            )
            for index, passage_id in enumerate(passage_ids)
        )
        return results


class InvalidRankRetriever:
    """Return one otherwise valid result with a noncontiguous rank."""

    def __init__(self, corpus: Corpus) -> None:
        self._passage = corpus.passages[0]

    def retrieve(self, request: RetrievalRequest) -> tuple[RetrievalEvidence, ...]:
        return (
            RetrievalEvidence(
                passage=self._passage,
                score=1.0,
                rank=2,
                retrieval_method="invalid-test-v1",
            ),
        )


def make_benchmark(corpus: Corpus) -> RetrievalBenchmark:
    return RetrievalBenchmark(
        schema_version=1,
        benchmark_id="benchmark-1",
        corpus_id=corpus.corpus_id,
        corpus_fingerprint=corpus_retrieval_fingerprint(corpus),
        license="CC0-1.0",
        source="Synthetic test benchmark",
        k_values=(1, 3),
        thresholds=(RetrievalThreshold(metric="hit_rate", k=1, minimum=0.75),),
        queries=(
            RetrievalBenchmarkCase(
                query_id="q1",
                question_type="topic",
                query="query one",
                relevant_passage_ids=("p1",),
                rationale="The first passage answers the question.",
            ),
            RetrievalBenchmarkCase(
                query_id="q2",
                question_type="synthesis",
                query="query two",
                relevant_passage_ids=("p2", "p3"),
                rationale="Both passages are required.",
            ),
        ),
    )


def test_evaluate_retriever_computes_ranked_metrics_once_at_max_k() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    retriever = RankedTestRetriever(
        corpus,
        {"query one": ("p1", "p2", "p3"), "query two": ("p1", "p2", "p3")},
    )

    report = evaluate_retriever(retriever, benchmark, corpus)

    assert retriever.requested_top_k == [3, 3]
    assert report.retrieval_method == "test-v1"
    assert report.metric("hit_rate", 1) == 0.5
    assert report.metric("hit_rate", 3) == 1.0
    assert report.metric("recall", 1) == 0.5
    assert report.metric("recall", 3) == 1.0
    assert report.metric("mrr", 1) == 0.5
    assert report.metric("mrr", 3) == 0.75


def test_evaluate_retriever_is_deterministic() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    rankings = {"query one": ("p1", "p2"), "query two": ("p2", "p3")}

    first = evaluate_retriever(RankedTestRetriever(corpus, rankings), benchmark, corpus)
    second = evaluate_retriever(
        RankedTestRetriever(corpus, rankings), benchmark, corpus
    )

    assert second == first


def test_evaluate_retriever_enforces_result_contract() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)

    with pytest.raises(RetrievalResultValidationError, match="contiguous rank 1"):
        evaluate_retriever(InvalidRankRetriever(corpus), benchmark, corpus)


def test_threshold_rejects_huge_integer_with_actionable_error() -> None:
    with pytest.raises(RetrievalEvaluationError, match="finite number from 0 to 1"):
        RetrievalThreshold(metric="hit_rate", k=1, minimum=10**400)


def test_find_threshold_failures_is_actionable() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    retriever = RankedTestRetriever(
        corpus, {"query one": ("p2",), "query two": ("p1",)}
    )

    report = evaluate_retriever(retriever, benchmark, corpus)

    assert find_threshold_failures(report, benchmark.thresholds) == (
        "hit_rate@1 was 0.000, below minimum 0.750; queries without a relevant passage: q1, q2.",
    )


def test_mrr_threshold_failure_names_low_ranked_queries() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    retriever = RankedTestRetriever(
        corpus,
        {"query one": ("p2", "p1"), "query two": ("p1", "p2", "p3")},
    )
    report = evaluate_retriever(retriever, benchmark, corpus)
    threshold = RetrievalThreshold(metric="mrr", k=3, minimum=0.75)

    assert find_threshold_failures(report, (threshold,)) == (
        "mrr@3 was 0.500, below minimum 0.750; queries with reciprocal-rank "
        "contribution below the minimum: q1 (first relevant rank: 2), q2 "
        "(first relevant rank: 2).",
    )


def test_corpus_fingerprint_is_order_independent_but_text_sensitive() -> None:
    corpus = make_corpus()
    reordered = replace(corpus, passages=tuple(reversed(corpus.passages)))
    changed_passage = replace(corpus.passages[0], text="changed first passage")
    changed = replace(corpus, passages=(changed_passage, *corpus.passages[1:]))

    assert corpus_retrieval_fingerprint(reordered) == corpus_retrieval_fingerprint(
        corpus
    )
    assert corpus_retrieval_fingerprint(changed) != corpus_retrieval_fingerprint(corpus)


def test_evaluation_rejects_text_only_corpus_change_with_unchanged_ids() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    changed_passage = replace(corpus.passages[0], text="changed first passage")
    changed = replace(corpus, passages=(changed_passage, *corpus.passages[1:]))
    retriever = RankedTestRetriever(changed, {})

    with pytest.raises(
        RetrievalEvaluationError,
        match=(
            "corpus fingerprint mismatch.*explicitly review the relevance judgments "
            "and acceptance thresholds"
        ),
    ):
        evaluate_retriever(retriever, benchmark, changed)

    assert retriever.requested_top_k == []


def test_evaluation_rejects_results_from_a_different_corpus_snapshot() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    changed_passage = replace(corpus.passages[0], text="changed first passage")
    changed = replace(corpus, passages=(changed_passage, *corpus.passages[1:]))
    retriever = RankedTestRetriever(
        changed,
        {"query one": ("p1",), "query two": ("p2",)},
    )

    with pytest.raises(
        RetrievalEvaluationError,
        match="q1.*passage_id 'p1'.*validated corpus snapshot",
    ):
        evaluate_retriever(retriever, benchmark, corpus)


def test_evaluation_rejects_unknown_relevant_passage_id() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    bad_case = replace(benchmark.queries[0], relevant_passage_ids=("missing",))
    invalid = replace(benchmark, queries=(bad_case, *benchmark.queries[1:]))

    with pytest.raises(
        RetrievalEvaluationError, match="q1.*unknown relevant passage_id 'missing'"
    ):
        evaluate_retriever(RankedTestRetriever(corpus, {}), invalid, corpus)
