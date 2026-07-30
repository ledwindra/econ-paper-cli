"""Pure, backend-independent ranked retrieval evaluation."""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from econ_paper_cli.domain import Corpus
from econ_paper_cli.protocols import (
    RetrievalRequest,
    Retriever,
    validate_retrieval_results,
)

_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_QUESTION_TYPES = frozenset(
    {
        "topic",
        "method",
        "finding",
        "mechanism",
        "heterogeneity",
        "limitation",
        "synthesis",
    }
)
RetrievalMetricName: TypeAlias = Literal["hit_rate", "recall", "mrr"]

_METRIC_NAMES: tuple[RetrievalMetricName, ...] = ("hit_rate", "recall", "mrr")
_METRICS = frozenset(_METRIC_NAMES)
_CASE_FIELDS = frozenset(
    {"query_id", "question_type", "query", "relevant_passage_ids", "rationale"}
)
_THRESHOLD_FIELDS = frozenset({"metric", "k", "minimum"})
_BENCHMARK_FIELDS = frozenset(
    {
        "schema_version",
        "benchmark_id",
        "corpus_id",
        "corpus_fingerprint",
        "license",
        "source",
        "k_values",
        "thresholds",
        "queries",
    }
)


class RetrievalEvaluationError(ValueError):
    """Raised when a benchmark or evaluation violates its deterministic contract."""


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkCase:
    """One reviewable query with binary passage-level relevance judgments."""

    query_id: str
    question_type: str
    query: str
    relevant_passage_ids: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        _validate_id("query_id", self.query_id)
        if (
            not isinstance(self.question_type, str)
            or self.question_type not in _QUESTION_TYPES
        ):
            allowed = ", ".join(sorted(_QUESTION_TYPES))
            raise RetrievalEvaluationError(f"question_type must be one of: {allowed}.")
        _validate_nonempty_text("query", self.query)
        _validate_nonempty_text("rationale", self.rationale)
        if (
            not isinstance(self.relevant_passage_ids, tuple)
            or not self.relevant_passage_ids
        ):
            raise RetrievalEvaluationError(
                "relevant_passage_ids must be a non-empty tuple."
            )
        if len(set(self.relevant_passage_ids)) != len(self.relevant_passage_ids):
            raise RetrievalEvaluationError(
                f"Query '{self.query_id}' contains duplicate relevant passage IDs."
            )
        for passage_id in self.relevant_passage_ids:
            _validate_nonempty_text("relevant passage_id", passage_id)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "RetrievalBenchmarkCase":
        _validate_mapping_fields("benchmark query", data, _CASE_FIELDS)
        raw_relevant = data["relevant_passage_ids"]
        if not isinstance(raw_relevant, (list, tuple)):
            raise RetrievalEvaluationError(
                "relevant_passage_ids must be a non-empty sequence."
            )
        return cls(
            query_id=cast(str, data["query_id"]),
            question_type=cast(str, data["question_type"]),
            query=cast(str, data["query"]),
            relevant_passage_ids=tuple(cast(list[str] | tuple[str, ...], raw_relevant)),
            rationale=cast(str, data["rationale"]),
        )


@dataclass(frozen=True, slots=True)
class RetrievalThreshold:
    """Minimum accepted value for one ranked-retrieval metric at a cutoff."""

    metric: RetrievalMetricName
    k: int
    minimum: float

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or self.metric not in _METRICS:
            raise RetrievalEvaluationError(
                f"threshold metric must be one of: {', '.join(sorted(_METRICS))}."
            )
        _validate_positive_int("threshold k", self.k)
        _validate_unit_interval("threshold minimum", self.minimum)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "RetrievalThreshold":
        _validate_mapping_fields("benchmark threshold", data, _THRESHOLD_FIELDS)
        return cls(
            metric=cast(str, data["metric"]),
            k=cast(int, data["k"]),
            minimum=cast(float, data["minimum"]),
        )


@dataclass(frozen=True, slots=True)
class RetrievalBenchmark:
    """Validated frozen benchmark definition for one exact corpus snapshot."""

    schema_version: int
    benchmark_id: str
    corpus_id: str
    corpus_fingerprint: str
    license: str
    source: str
    k_values: tuple[int, ...]
    thresholds: tuple[RetrievalThreshold, ...]
    queries: tuple[RetrievalBenchmarkCase, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise RetrievalEvaluationError("schema_version must be the integer 1.")
        _validate_id("benchmark_id", self.benchmark_id)
        _validate_id("corpus_id", self.corpus_id)
        if (
            not isinstance(self.corpus_fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(self.corpus_fingerprint) is None
        ):
            raise RetrievalEvaluationError(
                "corpus_fingerprint must use the form 'sha256:' followed by 64 lowercase hex characters."
            )
        _validate_nonempty_text("license", self.license)
        _validate_nonempty_text("source", self.source)
        if not isinstance(self.k_values, tuple) or not self.k_values:
            raise RetrievalEvaluationError("k_values must be a non-empty tuple.")
        for k in self.k_values:
            _validate_positive_int("k value", k)
        if tuple(sorted(set(self.k_values))) != self.k_values:
            raise RetrievalEvaluationError(
                "k_values must be unique and strictly increasing."
            )
        if not isinstance(self.thresholds, tuple) or not self.thresholds:
            raise RetrievalEvaluationError("thresholds must be a non-empty tuple.")
        if any(not isinstance(item, RetrievalThreshold) for item in self.thresholds):
            raise RetrievalEvaluationError(
                "thresholds must contain only RetrievalThreshold instances."
            )
        threshold_keys = [
            (threshold.metric, threshold.k) for threshold in self.thresholds
        ]
        if len(set(threshold_keys)) != len(threshold_keys):
            raise RetrievalEvaluationError(
                "threshold metric and k pairs must be unique."
            )
        for threshold in self.thresholds:
            if threshold.k not in self.k_values:
                raise RetrievalEvaluationError(
                    f"Threshold {threshold.metric}@{threshold.k} uses a k not present in k_values."
                )
        if not isinstance(self.queries, tuple) or not self.queries:
            raise RetrievalEvaluationError("queries must be a non-empty tuple.")
        if any(not isinstance(item, RetrievalBenchmarkCase) for item in self.queries):
            raise RetrievalEvaluationError(
                "queries must contain only RetrievalBenchmarkCase instances."
            )
        query_ids = [case.query_id for case in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise RetrievalEvaluationError("query_id values must be unique.")

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "RetrievalBenchmark":
        _validate_mapping_fields("retrieval benchmark", data, _BENCHMARK_FIELDS)
        raw_k_values = data["k_values"]
        raw_thresholds = data["thresholds"]
        raw_queries = data["queries"]
        if not isinstance(raw_k_values, (list, tuple)):
            raise RetrievalEvaluationError("k_values must be a non-empty sequence.")
        if not isinstance(raw_thresholds, (list, tuple)):
            raise RetrievalEvaluationError("thresholds must be a non-empty sequence.")
        if not isinstance(raw_queries, (list, tuple)):
            raise RetrievalEvaluationError("queries must be a non-empty sequence.")
        return cls(
            schema_version=cast(int, data["schema_version"]),
            benchmark_id=cast(str, data["benchmark_id"]),
            corpus_id=cast(str, data["corpus_id"]),
            corpus_fingerprint=cast(str, data["corpus_fingerprint"]),
            license=cast(str, data["license"]),
            source=cast(str, data["source"]),
            k_values=tuple(cast(list[int] | tuple[int, ...], raw_k_values)),
            thresholds=tuple(
                RetrievalThreshold.from_mapping(cast(Mapping[str, object], item))
                for item in raw_thresholds
            ),
            queries=tuple(
                RetrievalBenchmarkCase.from_mapping(cast(Mapping[str, object], item))
                for item in raw_queries
            ),
        )


@dataclass(frozen=True, slots=True)
class QueryEvaluation:
    """Retrieved identities and judged relevance for one benchmark query."""

    query_id: str
    relevant_passage_ids: tuple[str, ...]
    retrieved_passage_ids: tuple[str, ...]

    def relevant_ranks(self, k: int) -> tuple[int, ...]:
        relevant = set(self.relevant_passage_ids)
        return tuple(
            rank
            for rank, passage_id in enumerate(self.retrieved_passage_ids[:k], start=1)
            if passage_id in relevant
        )


@dataclass(frozen=True, slots=True)
class RetrievalMetric:
    """One aggregate metric value at a cutoff."""

    metric: RetrievalMetricName
    k: int
    value: float


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    """Deterministic aggregate and per-query retrieval evaluation output."""

    benchmark_id: str
    corpus_fingerprint: str
    retrieval_method: str | None
    query_results: tuple[QueryEvaluation, ...]
    metrics: tuple[RetrievalMetric, ...]

    def metric(self, metric: RetrievalMetricName, k: int) -> float:
        for item in self.metrics:
            if item.metric == metric and item.k == k:
                return item.value
        raise RetrievalEvaluationError(f"Metric {metric}@{k} was not evaluated.")


def corpus_retrieval_fingerprint(corpus: Corpus) -> str:
    """Hash canonical passage identity and exact indexed text for a Corpus."""
    if not isinstance(corpus, Corpus):
        raise RetrievalEvaluationError("corpus must be a Corpus instance.")
    passages = [
        {
            "paper_id": passage.paper_id,
            "passage_id": passage.passage_id,
            "text": passage.text,
        }
        for passage in sorted(corpus.passages, key=lambda item: item.passage_id)
    ]
    canonical = json.dumps(
        passages,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def evaluate_retriever(
    retriever: Retriever,
    benchmark: RetrievalBenchmark,
    corpus: Corpus,
) -> RetrievalEvaluationReport:
    """Evaluate a Retriever against a validated benchmark and exact corpus snapshot."""
    _validate_benchmark_corpus(benchmark, corpus)
    corpus_passages = {passage.passage_id: passage for passage in corpus.passages}
    max_k = max(benchmark.k_values)
    query_results: list[QueryEvaluation] = []
    retrieval_method: str | None = None

    for case in benchmark.queries:
        request = RetrievalRequest(query=case.query, top_k=max_k)
        results = validate_retrieval_results(request, retriever.retrieve(request))
        for result in results:
            canonical = corpus_passages.get(result.passage.passage_id)
            if (
                canonical is None
                or result.passage.paper_id != canonical.paper_id
                or result.passage.text != canonical.text
            ):
                raise RetrievalEvaluationError(
                    f"Query '{case.query_id}' returned passage_id "
                    f"'{result.passage.passage_id}' that does not match the validated corpus snapshot."
                )
        if results:
            method = cast(str, results[0].retrieval_method)
            if retrieval_method is None:
                retrieval_method = method
            elif method != retrieval_method:
                raise RetrievalEvaluationError(
                    "Retriever returned inconsistent retrieval_method labels across benchmark queries: "
                    f"'{retrieval_method}' and '{method}'."
                )
        query_results.append(
            QueryEvaluation(
                query_id=case.query_id,
                relevant_passage_ids=case.relevant_passage_ids,
                retrieved_passage_ids=tuple(
                    result.passage.passage_id for result in results
                ),
            )
        )

    result_tuple = tuple(query_results)
    metrics = tuple(
        RetrievalMetric(
            metric=metric, k=k, value=_calculate_metric(metric, k, result_tuple)
        )
        for k in benchmark.k_values
        for metric in _METRIC_NAMES
    )
    return RetrievalEvaluationReport(
        benchmark_id=benchmark.benchmark_id,
        corpus_fingerprint=benchmark.corpus_fingerprint,
        retrieval_method=retrieval_method,
        query_results=result_tuple,
        metrics=metrics,
    )


def find_threshold_failures(
    report: RetrievalEvaluationReport,
    thresholds: tuple[RetrievalThreshold, ...],
) -> tuple[str, ...]:
    """Return deterministic actionable messages for unmet regression gates."""
    failures: list[str] = []
    for threshold in thresholds:
        actual = report.metric(threshold.metric, threshold.k)
        if actual >= threshold.minimum:
            continue
        affected = _affected_queries(report, threshold)
        if threshold.metric == "recall":
            detail = "queries with incomplete relevant passage coverage"
        elif threshold.metric == "mrr":
            detail = "queries with reciprocal-rank contribution below the minimum"
        else:
            detail = "queries without a relevant passage"
        failures.append(
            f"{threshold.metric}@{threshold.k} was {actual:.3f}, below minimum "
            f"{threshold.minimum:.3f}; {detail}: {', '.join(affected)}."
        )
    return tuple(failures)


def _validate_benchmark_corpus(benchmark: RetrievalBenchmark, corpus: Corpus) -> None:
    if benchmark.corpus_id != corpus.corpus_id:
        raise RetrievalEvaluationError(
            f"Benchmark corpus_id '{benchmark.corpus_id}' does not match corpus '{corpus.corpus_id}'."
        )
    actual_fingerprint = corpus_retrieval_fingerprint(corpus)
    if benchmark.corpus_fingerprint != actual_fingerprint:
        raise RetrievalEvaluationError(
            "Benchmark corpus fingerprint mismatch: expected "
            f"'{benchmark.corpus_fingerprint}', got '{actual_fingerprint}'. Corpus content changed; "
            "explicitly review the relevance judgments and acceptance thresholds before updating the fingerprint."
        )
    passage_ids = {passage.passage_id for passage in corpus.passages}
    for case in benchmark.queries:
        for passage_id in case.relevant_passage_ids:
            if passage_id not in passage_ids:
                raise RetrievalEvaluationError(
                    f"Query '{case.query_id}' references unknown relevant passage_id '{passage_id}'."
                )


def _calculate_metric(
    metric: RetrievalMetricName, k: int, results: tuple[QueryEvaluation, ...]
) -> float:
    values: list[float] = []
    for result in results:
        ranks = result.relevant_ranks(k)
        if metric == "hit_rate":
            values.append(float(bool(ranks)))
        elif metric == "recall":
            values.append(len(ranks) / len(result.relevant_passage_ids))
        elif metric == "mrr":
            values.append(1.0 / ranks[0] if ranks else 0.0)
        else:
            raise AssertionError(f"Unsupported retrieval metric: {metric}")
    return math.fsum(values) / len(values)


def _affected_queries(
    report: RetrievalEvaluationReport, threshold: RetrievalThreshold
) -> tuple[str, ...]:
    affected: list[str] = []
    for result in report.query_results:
        ranks = result.relevant_ranks(threshold.k)
        if threshold.metric == "recall":
            is_affected = len(ranks) < len(result.relevant_passage_ids)
            label = result.query_id
        elif threshold.metric == "mrr":
            contribution = 1.0 / ranks[0] if ranks else 0.0
            is_affected = contribution < threshold.minimum
            rank = str(ranks[0]) if ranks else "none"
            label = f"{result.query_id} (first relevant rank: {rank})"
        elif threshold.metric == "hit_rate":
            is_affected = not ranks
            label = result.query_id
        else:
            raise AssertionError(f"Unsupported retrieval metric: {threshold.metric}")
        if is_affected:
            affected.append(label)
    return tuple(affected)


def _validate_mapping_fields(
    label: str, data: Mapping[str, object], expected: frozenset[str]
) -> None:
    if not isinstance(data, Mapping):
        raise RetrievalEvaluationError(f"{label} must be a mapping.")
    if any(not isinstance(key, str) for key in data):
        raise RetrievalEvaluationError(f"{label} keys must be strings.")
    provided = set(data)
    missing = sorted(expected - provided)
    if missing:
        raise RetrievalEvaluationError(
            f"{label} is missing required fields: {', '.join(missing)}."
        )
    unknown = sorted(provided - expected)
    if unknown:
        raise RetrievalEvaluationError(
            f"{label} contains unknown fields: {', '.join(unknown)}."
        )


def _validate_id(label: str, value: object) -> None:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise RetrievalEvaluationError(
            f"{label} must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_nonempty_text(label: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalEvaluationError(f"{label} must be a non-empty string.")


def _validate_positive_int(label: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RetrievalEvaluationError(f"{label} must be a positive integer.")


def _validate_unit_interval(label: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetrievalEvaluationError(f"{label} must be a finite number from 0 to 1.")
    if value < 0 or value > 1:
        raise RetrievalEvaluationError(f"{label} must be a finite number from 0 to 1.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RetrievalEvaluationError(f"{label} must be a finite number from 0 to 1.")
