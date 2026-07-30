"""Backend-independent, offline retrieval resource observations."""

import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import cast

from econ_paper_cli.domain import Corpus, Passage
from econ_paper_cli.evaluation.retrieval import (
    RetrievalBenchmark,
    RetrievalEvaluationReport,
    evaluate_retriever,
)
from econ_paper_cli.protocols import (
    RetrievalRequest,
    Retriever,
    validate_retrieval_results,
)

Clock = Callable[[], float]
RetrieverFactory = Callable[[], Retriever]


class ResourceMeasurementError(ValueError):
    """Raised when a resource-observation run cannot satisfy its contract."""


@dataclass(frozen=True, slots=True)
class TimingSummary:
    """Descriptive timing observations; never a correctness threshold."""

    sample_count: int
    minimum_seconds: float
    median_seconds: float
    p95_seconds: float
    maximum_seconds: float

    @classmethod
    def from_samples(cls, samples: tuple[float, ...]) -> "TimingSummary":
        """Summarize non-negative finite durations using nearest-rank p95."""
        if not isinstance(samples, tuple) or not samples:
            raise ResourceMeasurementError("timing samples must be a non-empty tuple.")
        for sample in samples:
            if (
                isinstance(sample, bool)
                or not isinstance(sample, (int, float))
                or not math.isfinite(float(sample))
                or sample < 0
            ):
                raise ResourceMeasurementError(
                    "timing samples must contain only finite non-negative numbers."
                )
        ordered = tuple(sorted(float(sample) for sample in samples))
        p95_index = math.ceil(0.95 * len(ordered)) - 1
        return cls(
            sample_count=len(ordered),
            minimum_seconds=ordered[0],
            median_seconds=statistics.median(ordered),
            p95_seconds=ordered[p95_index],
            maximum_seconds=ordered[-1],
        )

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-compatible observation mapping."""
        return {
            "sample_count": self.sample_count,
            "minimum_seconds": self.minimum_seconds,
            "median_seconds": self.median_seconds,
            "p95_seconds": self.p95_seconds,
            "maximum_seconds": self.maximum_seconds,
        }


@dataclass(frozen=True, slots=True)
class MachineProfile:
    """Machine metadata captured without inferring unsupported hardware claims."""

    system: str
    release: str
    machine: str
    processor: str | None
    python_implementation: str
    python_version: str
    logical_cpu_count: int | None
    total_memory_bytes: int | None
    total_memory_status: str
    power_mode: str | None

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-compatible machine-profile mapping."""
        return {
            "system": self.system,
            "release": self.release,
            "machine": self.machine,
            "processor": self.processor,
            "python_implementation": self.python_implementation,
            "python_version": self.python_version,
            "logical_cpu_count": self.logical_cpu_count,
            "total_memory_bytes": self.total_memory_bytes,
            "total_memory_status": self.total_memory_status,
            "power_mode": self.power_mode,
        }


@dataclass(frozen=True, slots=True)
class RetrievalResourceObservations:
    """Non-gating resource observations for one deterministic retrieval run."""

    schema_version: int
    benchmark_id: str
    corpus_fingerprint: str
    retrieval_method: str | None
    result_digest: str
    machine_profile: MachineProfile
    initialization_runs: int
    warmup_passes: int
    measured_passes: int
    query_count: int
    initialization: TimingSummary
    query_latency: TimingSummary
    python_heap_peak_bytes: int | None
    process_peak_rss_bytes: int | None
    process_rss_status: str

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-compatible observation mapping with explicit units."""
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "corpus_fingerprint": self.corpus_fingerprint,
            "retrieval_method": self.retrieval_method,
            "result_digest": self.result_digest,
            "machine_profile": self.machine_profile.to_mapping(),
            "protocol": {
                "initialization_runs": self.initialization_runs,
                "warmup_passes": self.warmup_passes,
                "measured_passes": self.measured_passes,
                "query_count": self.query_count,
            },
            "initialization": self.initialization.to_mapping(),
            "query_latency": self.query_latency.to_mapping(),
            "python_heap_peak_bytes": self.python_heap_peak_bytes,
            "process_peak_rss_bytes": self.process_peak_rss_bytes,
            "process_rss_status": self.process_rss_status,
        }


def stable_retrieval_result_digest(report: RetrievalEvaluationReport) -> str:
    """Hash stable ranked identities without raw floating-point scores."""
    if not isinstance(report, RetrievalEvaluationReport):
        raise ResourceMeasurementError(
            "report must be a RetrievalEvaluationReport instance."
        )
    payload = _stable_result_payload(
        tuple(
            (
                result.query_id,
                tuple(
                    (passage_id, rank, report.retrieval_method)
                    for rank, passage_id in enumerate(
                        result.retrieved_passage_ids, start=1
                    )
                ),
            )
            for result in report.query_results
        )
    )
    return _sha256_json(payload)


def detect_machine_profile() -> MachineProfile:
    """Capture reproducibility metadata available from the standard library."""
    total_memory_bytes, total_memory_status = _read_total_memory()
    processor = platform.processor().strip() or None
    return MachineProfile(
        system=platform.system() or "unknown",
        release=platform.release() or "unknown",
        machine=platform.machine() or "unknown",
        processor=processor,
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        logical_cpu_count=os.cpu_count(),
        total_memory_bytes=total_memory_bytes,
        total_memory_status=total_memory_status,
        power_mode=None,
    )


def build_synthetic_scaling_corpus(
    corpus: Corpus, *, target_passage_count: int
) -> Corpus:
    """Build a deterministic in-memory corpus for resource scaling only."""
    if not isinstance(corpus, Corpus):
        raise ResourceMeasurementError("corpus must be a Corpus instance.")
    if (
        isinstance(target_passage_count, bool)
        or not isinstance(target_passage_count, int)
        or target_passage_count < len(corpus.passages)
    ):
        raise ResourceMeasurementError(
            "target_passage_count must be an integer greater than or equal to "
            f"the source passage count ({len(corpus.passages)})."
        )

    passages = list(corpus.passages)
    next_ordinal_by_paper = {paper.paper_id: 0 for paper in corpus.papers}
    for passage in passages:
        next_ordinal_by_paper[passage.paper_id] = max(
            next_ordinal_by_paper[passage.paper_id],
            passage.ordinal_position + 1,
        )
    for index in range(len(passages), target_passage_count):
        source = corpus.passages[index % len(corpus.passages)]
        ordinal_position = next_ordinal_by_paper[source.paper_id]
        next_ordinal_by_paper[source.paper_id] += 1
        passages.append(
            replace(
                source,
                passage_id=f"scale-{index + 1:08d}-{source.passage_id}",
                text=(
                    f"{source.text}\n\nSynthetic resource-scaling variant {index + 1}."
                ),
                ordinal_position=ordinal_position,
            )
        )
    return Corpus(
        schema_version=corpus.schema_version,
        corpus_id=f"{corpus.corpus_id}-scale-{target_passage_count}",
        papers=corpus.papers,
        passages=tuple(passages),
    )


def measure_retriever_resources(
    retriever_factory: RetrieverFactory,
    benchmark: RetrievalBenchmark,
    corpus: Corpus,
    *,
    initialization_runs: int = 5,
    warmup_passes: int = 1,
    measured_passes: int = 30,
    clock: Clock = time.perf_counter,
    measure_python_heap: bool = True,
) -> RetrievalResourceObservations:
    """Observe initialization, latency, and memory without defining pass/fail gates.

    Every measured pass must preserve ranked identities and locally exact scores.
    The cross-platform digest deliberately excludes raw scores. Python heap data
    comes from ``tracemalloc`` and is distinct from total process RSS.
    """
    if not callable(retriever_factory):
        raise ResourceMeasurementError("retriever_factory must be callable.")
    if not isinstance(benchmark, RetrievalBenchmark):
        raise ResourceMeasurementError(
            "benchmark must be a RetrievalBenchmark instance."
        )
    if not isinstance(corpus, Corpus):
        raise ResourceMeasurementError("corpus must be a Corpus instance.")
    _validate_count("initialization_runs", initialization_runs, minimum=1)
    _validate_count("warmup_passes", warmup_passes, minimum=0)
    _validate_count("measured_passes", measured_passes, minimum=2)
    if not callable(clock):
        raise ResourceMeasurementError("clock must be callable.")
    if not isinstance(measure_python_heap, bool):
        raise ResourceMeasurementError("measure_python_heap must be a boolean.")
    if measure_python_heap and tracemalloc.is_tracing():
        raise ResourceMeasurementError(
            "tracemalloc is already running; stop it before measuring Python heap usage."
        )

    initialization_samples: list[float] = []
    for _ in range(initialization_runs):
        started = clock()
        candidate = retriever_factory()
        finished = clock()
        _validate_retriever(candidate)
        initialization_samples.append(_elapsed(started, finished))
        del candidate

    retriever = retriever_factory()
    _validate_retriever(retriever)
    baseline = evaluate_retriever(retriever, benchmark, corpus)
    expected_digest = stable_retrieval_result_digest(baseline)
    corpus_passages = {passage.passage_id: passage for passage in corpus.passages}

    for _ in range(warmup_passes):
        digest, _, _ = _run_query_pass(
            retriever,
            benchmark,
            corpus_passages,
            clock=None,
        )
        _validate_pass_digest(expected_digest, digest)

    query_samples: list[float] = []
    expected_scores: tuple[tuple[float, ...], ...] | None = None
    for pass_number in range(1, measured_passes + 1):
        digest, scores, durations = _run_query_pass(
            retriever,
            benchmark,
            corpus_passages,
            clock=clock,
        )
        _validate_pass_digest(expected_digest, digest)
        if expected_scores is None:
            expected_scores = scores
        elif scores != expected_scores:
            raise ResourceMeasurementError(
                "Retriever scores changed between measured passes; "
                f"pass {pass_number} was not locally deterministic."
            )
        query_samples.extend(durations)

    python_heap_peak_bytes: int | None = None
    if measure_python_heap:
        tracemalloc.start()
        try:
            heap_retriever = retriever_factory()
            _validate_retriever(heap_retriever)
            heap_digest, heap_scores, _ = _run_query_pass(
                heap_retriever,
                benchmark,
                corpus_passages,
                clock=None,
            )
            _validate_pass_digest(expected_digest, heap_digest)
            if heap_scores != expected_scores:
                raise ResourceMeasurementError(
                    "Retriever scores changed during the untimed Python heap "
                    "observation pass."
                )
            _, python_heap_peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

    process_peak_rss_bytes, process_rss_status = _read_peak_process_rss()
    return RetrievalResourceObservations(
        schema_version=1,
        benchmark_id=benchmark.benchmark_id,
        corpus_fingerprint=benchmark.corpus_fingerprint,
        retrieval_method=baseline.retrieval_method,
        result_digest=expected_digest,
        machine_profile=detect_machine_profile(),
        initialization_runs=initialization_runs,
        warmup_passes=warmup_passes,
        measured_passes=measured_passes,
        query_count=len(benchmark.queries),
        initialization=TimingSummary.from_samples(tuple(initialization_samples)),
        query_latency=TimingSummary.from_samples(tuple(query_samples)),
        python_heap_peak_bytes=python_heap_peak_bytes,
        process_peak_rss_bytes=process_peak_rss_bytes,
        process_rss_status=process_rss_status,
    )


def _run_query_pass(
    retriever: Retriever,
    benchmark: RetrievalBenchmark,
    corpus_passages: Mapping[str, Passage],
    *,
    clock: Clock | None,
) -> tuple[str, tuple[tuple[float, ...], ...], tuple[float, ...]]:
    max_k = max(benchmark.k_values)
    rows: list[tuple[str, tuple[tuple[str, int, str | None], ...]]] = []
    scores: list[tuple[float, ...]] = []
    durations: list[float] = []
    retrieval_method: str | None = None

    for case in benchmark.queries:
        request = RetrievalRequest(query=case.query, top_k=max_k)
        started = clock() if clock is not None else None
        results = validate_retrieval_results(request, retriever.retrieve(request))
        if clock is not None:
            finished = clock()
            durations.append(_elapsed(cast(float, started), finished))

        result_rows: list[tuple[str, int, str | None]] = []
        for result in results:
            canonical = corpus_passages.get(result.passage.passage_id)
            if (
                canonical is None
                or result.passage.paper_id != canonical.paper_id
                or result.passage.text != canonical.text
            ):
                raise ResourceMeasurementError(
                    f"Query '{case.query_id}' returned passage_id "
                    f"'{result.passage.passage_id}' that does not match the validated corpus snapshot."
                )
            if retrieval_method is None:
                retrieval_method = result.retrieval_method
            elif result.retrieval_method != retrieval_method:
                raise ResourceMeasurementError(
                    "Retriever returned inconsistent retrieval_method labels across queries."
                )
            result_rows.append(
                (
                    result.passage.passage_id,
                    result.rank,
                    result.retrieval_method,
                )
            )
        rows.append((case.query_id, tuple(result_rows)))
        scores.append(tuple(result.score for result in results))

    return (
        _sha256_json(_stable_result_payload(tuple(rows))),
        tuple(scores),
        tuple(durations),
    )


def _stable_result_payload(
    rows: tuple[tuple[str, tuple[tuple[str, int, str | None], ...]], ...],
) -> Mapping[str, object]:
    return {
        "schema_version": 1,
        "queries": [
            {
                "query_id": query_id,
                "results": [
                    {
                        "passage_id": passage_id,
                        "rank": rank,
                        "retrieval_method": retrieval_method,
                    }
                    for passage_id, rank, retrieval_method in results
                ],
            }
            for query_id, results in rows
        ],
    }


def _sha256_json(data: Mapping[str, object]) -> str:
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_count(label: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ResourceMeasurementError(
            f"{label} must be an integer greater than or equal to {minimum}."
        )


def _validate_retriever(value: object) -> None:
    if not callable(getattr(value, "retrieve", None)):
        raise ResourceMeasurementError(
            "retriever_factory must return an object with a callable retrieve method."
        )


def _elapsed(started: object, finished: object) -> float:
    if (
        isinstance(started, bool)
        or not isinstance(started, (int, float))
        or isinstance(finished, bool)
        or not isinstance(finished, (int, float))
    ):
        raise ResourceMeasurementError("clock must return finite numeric seconds.")
    duration = float(finished) - float(started)
    if not math.isfinite(duration) or duration < 0:
        raise ResourceMeasurementError(
            "clock must return finite monotonically non-decreasing seconds."
        )
    return duration


def _validate_pass_digest(expected: str, actual: str) -> None:
    if actual != expected:
        raise ResourceMeasurementError(
            "Stable retrieval identities, ranks, or retrieval_method changed between passes."
        )


def _read_total_memory() -> tuple[int | None, str]:
    if os.name != "posix" or not hasattr(os, "sysconf"):
        return (
            None,
            "unavailable: total physical memory is not exposed portably by the standard library on this platform",
        )
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError):
        return None, "unavailable: os.sysconf could not read total physical memory"
    if not isinstance(page_size, int) or not isinstance(page_count, int):
        return None, "unavailable: os.sysconf returned non-integer memory values"
    if page_size <= 0 or page_count <= 0:
        return None, "unavailable: os.sysconf returned non-positive memory values"
    return page_size * page_count, "observed via os.sysconf"


def _read_peak_process_rss() -> tuple[int | None, str]:
    if sys.platform not in {"linux", "darwin"}:
        return (
            None,
            "unavailable: reliable process peak RSS requires platform-specific support not provided here",
        )
    try:
        import resource
    except ImportError:
        return None, "unavailable: resource module is not installed"

    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (AttributeError, OSError, ValueError):
        return None, "unavailable: resource.getrusage could not read peak process RSS"
    if (
        isinstance(peak, bool)
        or not isinstance(peak, (int, float))
        or not math.isfinite(float(peak))
        or peak < 0
    ):
        return None, "unavailable: resource.getrusage returned an invalid peak RSS"
    if sys.platform == "linux":
        return int(
            peak * 1024
        ), "process-lifetime peak via resource.getrusage; Linux KiB normalized to bytes"
    return int(peak), "process-lifetime peak via resource.getrusage; macOS bytes"
