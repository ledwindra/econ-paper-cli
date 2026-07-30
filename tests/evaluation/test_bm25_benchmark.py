"""End-to-end regression benchmark for the untuned BM25 adapter."""

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from econ_paper_cli.adapters import BM25Retriever, load_corpus_from_file
from econ_paper_cli.evaluation import (
    RetrievalBenchmark,
    corpus_retrieval_fingerprint,
    evaluate_retriever,
    find_threshold_failures,
    stable_retrieval_result_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "corpus" / "synthetic-economics-v1.json"
)
BENCHMARK_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "retrieval"
    / "synthetic-economics-v1-benchmark.json"
)
APPROVED_QUERY_JUDGMENTS_SHA256 = (
    "1f7f4f9b7b51b9fdc26602c57096f9c05ce676925392aca726b3a6df5d549138"
)
APPROVED_CORPUS_FINGERPRINT = (
    "sha256:3af9525b39cbd83576b1563f8ae0cc399ce886d57172485defe0a83ba5cefb48"
)
APPROVED_BM25_RESULT_DIGEST = (
    "sha256:13766bd01249f0c595f8b39ad6617fa78eade2bbb1710042a8ba407cc236e0ee"
)


def load_benchmark() -> RetrievalBenchmark:
    data = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    return RetrievalBenchmark.from_mapping(data)


def test_benchmark_fixture_contains_approved_design() -> None:
    benchmark = load_benchmark()
    corpus = load_corpus_from_file(CORPUS_PATH)
    approved_projection = [
        {
            "query_id": case.query_id,
            "question_type": case.question_type,
            "query": case.query,
            "relevant_passage_ids": case.relevant_passage_ids,
        }
        for case in benchmark.queries
    ]
    serialized_projection = json.dumps(
        approved_projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert benchmark.corpus_id == "synthetic-economics-v1"
    assert benchmark.corpus_fingerprint == APPROVED_CORPUS_FINGERPRINT
    assert corpus_retrieval_fingerprint(corpus) == APPROVED_CORPUS_FINGERPRINT
    assert benchmark.k_values == (1, 3, 5)
    assert len(benchmark.queries) == 25
    assert hashlib.sha256(serialized_projection).hexdigest() == (
        APPROVED_QUERY_JUDGMENTS_SHA256
    )
    assert [
        (threshold.metric, threshold.k, threshold.minimum)
        for threshold in benchmark.thresholds
    ] == [
        ("hit_rate", 1, 0.6),
        ("hit_rate", 3, 0.9),
        ("recall", 5, 0.9),
        ("mrr", 5, 0.75),
    ]


def test_untuned_bm25_meets_frozen_benchmark_regression_gates() -> None:
    corpus = load_corpus_from_file(CORPUS_PATH)
    benchmark = load_benchmark()
    retriever = BM25Retriever(corpus)

    io_error = AssertionError("evaluation attempted external I/O")
    with (
        patch("builtins.open", side_effect=io_error),
        patch.object(io, "open", side_effect=io_error),
        patch("socket.socket", side_effect=io_error),
    ):
        first = evaluate_retriever(retriever, benchmark, corpus)
        second = evaluate_retriever(retriever, benchmark, corpus)

    assert second == first
    assert first.retrieval_method == "bm25-v1"
    assert first.metric("hit_rate", 1) == pytest.approx(0.68)
    assert first.metric("hit_rate", 3) == pytest.approx(0.96)
    assert first.metric("hit_rate", 5) == pytest.approx(0.96)
    assert first.metric("recall", 1) == pytest.approx(0.5733333333333334)
    assert first.metric("recall", 3) == pytest.approx(0.88)
    assert first.metric("recall", 5) == pytest.approx(0.9333333333333333)
    assert first.metric("mrr", 1) == pytest.approx(0.68)
    assert first.metric("mrr", 3) == pytest.approx(0.8066666666666666)
    assert first.metric("mrr", 5) == pytest.approx(0.8066666666666666)
    assert stable_retrieval_result_digest(first) == APPROVED_BM25_RESULT_DIGEST
    assert find_threshold_failures(first, benchmark.thresholds) == ()
