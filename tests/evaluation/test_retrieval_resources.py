"""Unit tests for backend-independent retrieval resource observations."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from econ_paper_cli.domain import Corpus, Paper, Passage, RetrievalEvidence
from econ_paper_cli.evaluation import (
    MachineProfile,
    ResourceMeasurementError,
    RetrievalBenchmark,
    RetrievalBenchmarkCase,
    RetrievalMetric,
    RetrievalResourceObservations,
    RetrievalThreshold,
    TimingSummary,
    build_synthetic_scaling_corpus,
    corpus_retrieval_fingerprint,
    evaluate_retriever,
    measure_retriever_resources,
    stable_retrieval_result_digest,
)
from econ_paper_cli.evaluation import resources as resource_module
from econ_paper_cli.protocols import RetrievalRequest


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
    passage = Passage.from_mapping(
        {
            "passage_id": "passage-1",
            "paper_id": paper.paper_id,
            "text": "A synthetic economics finding.",
            "section_heading": "Findings",
            "page_start": 1,
            "page_end": None,
            "ordinal_position": 0,
        }
    )
    return Corpus(
        schema_version=1,
        corpus_id="corpus-1",
        papers=(paper,),
        passages=(passage,),
    )


def make_benchmark(corpus: Corpus) -> RetrievalBenchmark:
    return RetrievalBenchmark(
        schema_version=1,
        benchmark_id="benchmark-1",
        corpus_id=corpus.corpus_id,
        corpus_fingerprint=corpus_retrieval_fingerprint(corpus),
        license="CC0-1.0",
        source="Synthetic test benchmark",
        k_values=(1,),
        thresholds=(RetrievalThreshold(metric="hit_rate", k=1, minimum=1.0),),
        queries=(
            RetrievalBenchmarkCase(
                query_id="query-1",
                question_type="finding",
                query="What is the finding?",
                relevant_passage_ids=("passage-1",),
                rationale="The only passage contains the finding.",
            ),
        ),
    )


class StableRetriever:
    def __init__(self, corpus: Corpus) -> None:
        self._passage = corpus.passages[0]

    def retrieve(self, request: RetrievalRequest) -> tuple[RetrievalEvidence, ...]:
        return (
            RetrievalEvidence(
                passage=self._passage,
                score=1.25,
                rank=1,
                retrieval_method="stable-v1",
            ),
        )


class ChangingScoreRetriever(StableRetriever):
    def __init__(self, corpus: Corpus) -> None:
        super().__init__(corpus)
        self._calls = 0

    def retrieve(self, request: RetrievalRequest) -> tuple[RetrievalEvidence, ...]:
        self._calls += 1
        result = super().retrieve(request)[0]
        return (replace(result, score=result.score + self._calls),)


class ChangingMethodRetriever(StableRetriever):
    def __init__(self, corpus: Corpus) -> None:
        super().__init__(corpus)
        self._calls = 0

    def retrieve(self, request: RetrievalRequest) -> tuple[RetrievalEvidence, ...]:
        self._calls += 1
        result = super().retrieve(request)[0]
        method = "stable-v1" if self._calls == 1 else "changed-v1"
        return (replace(result, retrieval_method=method),)


class NonCallableRetriever:
    retrieve = "not callable"


def make_machine_profile() -> MachineProfile:
    return MachineProfile(
        system="TestOS",
        release="1",
        machine="test-arch",
        processor="Test CPU",
        python_implementation="CPython",
        python_version="3.10.0",
        logical_cpu_count=2,
        total_memory_bytes=None,
        total_memory_status="unavailable in test",
        power_mode=None,
    )


def test_stable_result_digest_uses_identities_ranks_and_method_not_metrics() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    report = evaluate_retriever(StableRetriever(corpus), benchmark, corpus)
    changed_metrics = replace(
        report,
        metrics=(RetrievalMetric(metric="hit_rate", k=1, value=0.0),),
    )

    assert stable_retrieval_result_digest(changed_metrics) == (
        stable_retrieval_result_digest(report)
    )
    assert stable_retrieval_result_digest(report).startswith("sha256:")


def test_stable_result_digest_changes_with_ranked_passage_identity() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    report = evaluate_retriever(StableRetriever(corpus), benchmark, corpus)
    changed_query = replace(
        report.query_results[0], retrieved_passage_ids=("different-passage",)
    )

    assert stable_retrieval_result_digest(
        replace(report, query_results=(changed_query,))
    ) != stable_retrieval_result_digest(report)
    assert stable_retrieval_result_digest(
        replace(report, retrieval_method="different-v1")
    ) != stable_retrieval_result_digest(report)


def test_synthetic_scaling_corpus_is_deterministic_and_text_distinct() -> None:
    corpus = make_corpus()

    first = build_synthetic_scaling_corpus(corpus, target_passage_count=4)
    second = build_synthetic_scaling_corpus(corpus, target_passage_count=4)

    assert first == second
    assert first.corpus_id == "corpus-1-scale-4"
    assert len(first.passages) == 4
    assert len({passage.passage_id for passage in first.passages}) == 4
    assert len({passage.text for passage in first.passages}) == 4
    assert corpus.corpus_id == "corpus-1"


def test_synthetic_scaling_allocates_next_ordinal_independently_per_paper() -> None:
    first_paper = make_corpus().papers[0]
    second_paper = Paper.from_mapping(
        {
            "paper_id": "paper-2",
            "title": "Second synthetic paper",
            "authors": ["Researcher"],
            "year": 2024,
            "abstract": None,
            "source_name": "Synthetic source",
            "source_identifier": "source-2",
            "source_url": None,
        }
    )
    first_passage = Passage.from_mapping(
        {
            "passage_id": "passage-1",
            "paper_id": first_paper.paper_id,
            "text": "First paper finding.",
            "section_heading": "Findings",
            "page_start": 1,
            "page_end": None,
            "ordinal_position": 7,
        }
    )
    second_passage = Passage.from_mapping(
        {
            "passage_id": "passage-2",
            "paper_id": second_paper.paper_id,
            "text": "Second paper finding.",
            "section_heading": "Findings",
            "page_start": 1,
            "page_end": None,
            "ordinal_position": 2,
        }
    )
    corpus = Corpus(
        schema_version=1,
        corpus_id="multi-paper",
        papers=(first_paper, second_paper),
        passages=(first_passage, second_passage),
    )

    scaled = build_synthetic_scaling_corpus(corpus, target_passage_count=4)

    assert [
        (passage.paper_id, passage.ordinal_position) for passage in scaled.passages
    ] == [
        ("paper-1", 7),
        ("paper-2", 2),
        ("paper-1", 8),
        ("paper-2", 3),
    ]


def test_timing_summary_uses_nearest_rank_p95() -> None:
    summary = TimingSummary.from_samples((4.0, 1.0, 3.0, 2.0, 5.0))

    assert summary.sample_count == 5
    assert summary.minimum_seconds == 1.0
    assert summary.median_seconds == 3.0
    assert summary.p95_seconds == 5.0
    assert summary.maximum_seconds == 5.0


def test_measure_resources_uses_controlled_clock_and_reports_unavailable_rss() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    times = iter((0.0, 1.0, 2.0, 4.0, 10.0, 10.5, 20.0, 21.0))

    with (
        patch(
            "econ_paper_cli.evaluation.resources.detect_machine_profile",
            return_value=make_machine_profile(),
        ),
        patch(
            "econ_paper_cli.evaluation.resources._read_peak_process_rss",
            return_value=(None, "unavailable on TestOS"),
        ),
        patch("socket.socket", side_effect=AssertionError("network access")),
    ):
        observations = measure_retriever_resources(
            lambda: StableRetriever(corpus),
            benchmark,
            corpus,
            initialization_runs=2,
            warmup_passes=1,
            measured_passes=2,
            clock=lambda: next(times),
            measure_python_heap=False,
        )

    assert observations.initialization.minimum_seconds == 1.0
    assert observations.initialization.maximum_seconds == 2.0
    assert observations.query_latency.minimum_seconds == 0.5
    assert observations.query_latency.maximum_seconds == 1.0
    assert observations.python_heap_peak_bytes is None
    assert observations.process_peak_rss_bytes is None
    assert observations.process_rss_status == "unavailable on TestOS"
    assert observations.retrieval_method == "stable-v1"
    assert observations.result_digest.startswith("sha256:")
    json.dumps(observations.to_mapping())


def test_measure_resources_does_not_trace_timed_observations() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    times = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    tracing_during_clock_reads: list[bool] = []

    def controlled_clock() -> float:
        tracing_during_clock_reads.append(resource_module.tracemalloc.is_tracing())
        return next(times)

    with (
        patch(
            "econ_paper_cli.evaluation.resources.detect_machine_profile",
            return_value=make_machine_profile(),
        ),
        patch(
            "econ_paper_cli.evaluation.resources._read_peak_process_rss",
            return_value=(None, "unavailable on TestOS"),
        ),
    ):
        observations = measure_retriever_resources(
            lambda: StableRetriever(corpus),
            benchmark,
            corpus,
            initialization_runs=1,
            warmup_passes=0,
            measured_passes=2,
            clock=controlled_clock,
        )

    assert tracing_during_clock_reads == [False] * 6
    assert observations.python_heap_peak_bytes is not None


def test_measure_resources_rejects_locally_changing_scores() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    times = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0))

    with pytest.raises(
        ResourceMeasurementError,
        match="scores changed between measured passes",
    ):
        measure_retriever_resources(
            lambda: ChangingScoreRetriever(corpus),
            benchmark,
            corpus,
            initialization_runs=1,
            warmup_passes=0,
            measured_passes=2,
            clock=lambda: next(times),
            measure_python_heap=False,
        )


def test_measure_resources_rejects_changed_method_during_measured_pass() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    times = iter((0.0, 1.0, 2.0, 3.0))

    with pytest.raises(
        ResourceMeasurementError,
        match="identities, ranks, or retrieval_method changed",
    ):
        measure_retriever_resources(
            lambda: ChangingMethodRetriever(corpus),
            benchmark,
            corpus,
            initialization_runs=1,
            warmup_passes=0,
            measured_passes=2,
            clock=lambda: next(times),
            measure_python_heap=False,
        )


def test_measure_resources_rejects_non_callable_retrieve() -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)

    with pytest.raises(
        ResourceMeasurementError,
        match="callable retrieve",
    ):
        measure_retriever_resources(
            NonCallableRetriever,
            benchmark,
            corpus,
            initialization_runs=1,
            warmup_passes=0,
            measured_passes=2,
            measure_python_heap=False,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("initialization_runs", 0),
        ("warmup_passes", -1),
        ("measured_passes", 1),
    ),
)
def test_measure_resources_rejects_invalid_run_counts(field: str, value: int) -> None:
    corpus = make_corpus()
    benchmark = make_benchmark(corpus)
    arguments = {
        "initialization_runs": 1,
        "warmup_passes": 0,
        "measured_passes": 2,
    }
    arguments[field] = value

    with pytest.raises(ResourceMeasurementError, match=field):
        measure_retriever_resources(
            lambda: StableRetriever(corpus),
            benchmark,
            corpus,
            measure_python_heap=False,
            **arguments,
        )


def test_resource_observations_mapping_labels_heap_and_process_rss_separately() -> None:
    timing = TimingSummary.from_samples((1.0, 2.0))
    observations = RetrievalResourceObservations(
        schema_version=1,
        benchmark_id="benchmark-1",
        corpus_fingerprint="sha256:" + "0" * 64,
        retrieval_method="stable-v1",
        result_digest="sha256:" + "1" * 64,
        machine_profile=make_machine_profile(),
        initialization_runs=2,
        warmup_passes=1,
        measured_passes=2,
        query_count=1,
        initialization=timing,
        query_latency=timing,
        python_heap_peak_bytes=128,
        process_peak_rss_bytes=None,
        process_rss_status="unavailable",
    )

    mapping = observations.to_mapping()

    assert mapping["python_heap_peak_bytes"] == 128
    assert mapping["process_peak_rss_bytes"] is None
    assert mapping["process_rss_status"] == "unavailable"


def test_peak_process_rss_reports_probe_failure_as_unavailable() -> None:
    resource_stub = SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _: (_ for _ in ()).throw(OSError("probe failed")),
    )

    with (
        patch.object(resource_module.sys, "platform", "linux"),
        patch.dict(sys.modules, {"resource": resource_stub}),
    ):
        value, status = resource_module._read_peak_process_rss()

    assert value is None
    assert status == "unavailable: resource.getrusage could not read peak process RSS"


def test_total_memory_rejects_non_positive_sysconf_values() -> None:
    with (
        patch.object(resource_module.os, "name", "posix"),
        patch.object(
            resource_module.os,
            "sysconf",
            side_effect=(-1, 1024),
            create=True,
        ),
    ):
        value, status = resource_module._read_total_memory()

    assert value is None
    assert status == "unavailable: os.sysconf returned non-positive memory values"


def test_offline_script_supports_a_small_non_performance_smoke_run() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts" / "measure_retrieval.py"),
            "--initialization-runs",
            "1",
            "--warmup-passes",
            "0",
            "--measured-passes",
            "2",
            "--no-scaling",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["correctness"]["threshold_failures"] == []
    assert payload["resource_observations"]["protocol"] == {
        "initialization_runs": 1,
        "warmup_passes": 0,
        "measured_passes": 2,
        "query_count": 25,
    }
