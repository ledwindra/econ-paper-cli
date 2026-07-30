"""Backend-independent deterministic evaluation utilities."""

from econ_paper_cli.evaluation.retrieval import (
    QueryEvaluation,
    RetrievalBenchmark,
    RetrievalBenchmarkCase,
    RetrievalEvaluationError,
    RetrievalEvaluationReport,
    RetrievalMetric,
    RetrievalThreshold,
    corpus_retrieval_fingerprint,
    evaluate_retriever,
    find_threshold_failures,
)

__all__ = [
    "QueryEvaluation",
    "RetrievalBenchmark",
    "RetrievalBenchmarkCase",
    "RetrievalEvaluationError",
    "RetrievalEvaluationReport",
    "RetrievalMetric",
    "RetrievalThreshold",
    "corpus_retrieval_fingerprint",
    "evaluate_retriever",
    "find_threshold_failures",
]
