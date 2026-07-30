"""Backend-independent deterministic evaluation utilities."""

from econ_paper_cli.evaluation.resources import (
    MachineProfile,
    ResourceMeasurementError,
    RetrievalResourceObservations,
    TimingSummary,
    build_synthetic_scaling_corpus,
    detect_machine_profile,
    measure_retriever_resources,
    stable_retrieval_result_digest,
)
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
    "MachineProfile",
    "QueryEvaluation",
    "ResourceMeasurementError",
    "RetrievalBenchmark",
    "RetrievalBenchmarkCase",
    "RetrievalEvaluationError",
    "RetrievalEvaluationReport",
    "RetrievalMetric",
    "RetrievalResourceObservations",
    "RetrievalThreshold",
    "TimingSummary",
    "build_synthetic_scaling_corpus",
    "corpus_retrieval_fingerprint",
    "detect_machine_profile",
    "evaluate_retriever",
    "find_threshold_failures",
    "measure_retriever_resources",
    "stable_retrieval_result_digest",
]
