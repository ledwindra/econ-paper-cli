"""Run offline BM25 correctness gates and non-gating resource observations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from econ_paper_cli.adapters import (
    BM25Retriever,
    CorpusLoadError,
    load_corpus_from_file,
)
from econ_paper_cli.evaluation import (
    ResourceMeasurementError,
    RetrievalBenchmark,
    RetrievalEvaluationError,
    RetrievalEvaluationReport,
    build_synthetic_scaling_corpus,
    corpus_retrieval_fingerprint,
    evaluate_retriever,
    find_threshold_failures,
    measure_retriever_resources,
    stable_retrieval_result_digest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_PATH = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "corpus" / "synthetic-economics-v1.json"
)
DEFAULT_BENCHMARK_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "retrieval"
    / "synthetic-economics-v1-benchmark.json"
)
DEFAULT_SCALING_PASSAGE_COUNTS = (1_000, 10_000)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than or equal to 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = _positive_int(value) if value != "0" else 0
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate frozen BM25 correctness gates, then collect offline, "
            "non-gating resource observations."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK_PATH)
    parser.add_argument("--initialization-runs", type=_positive_int, default=5)
    parser.add_argument("--warmup-passes", type=_nonnegative_int, default=1)
    parser.add_argument("--measured-passes", type=_positive_int, default=30)
    parser.add_argument(
        "--scale-passages",
        action="append",
        type=_positive_int,
        default=None,
        metavar="COUNT",
        help=(
            "resource-only synthetic corpus size; may be repeated. "
            "Defaults to 1000 and 10000 unless --no-scaling is used"
        ),
    )
    parser.add_argument(
        "--no-scaling",
        action="store_true",
        help="measure only the frozen corpus",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path; stdout is used when omitted",
    )
    return parser


def load_benchmark(path: Path) -> RetrievalBenchmark:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise RetrievalEvaluationError(
            f"Benchmark file at '{path}' must contain a JSON object."
        )
    return RetrievalBenchmark.from_mapping(data)


def _correctness_mapping(
    report: RetrievalEvaluationReport, threshold_failures: tuple[str, ...]
) -> dict[str, object]:
    return {
        "benchmark_id": report.benchmark_id,
        "corpus_fingerprint": report.corpus_fingerprint,
        "retrieval_method": report.retrieval_method,
        "result_digest": stable_retrieval_result_digest(report),
        "metrics": [
            {"metric": metric.metric, "k": metric.k, "value": metric.value}
            for metric in report.metrics
        ],
        "threshold_failures": list(threshold_failures),
    }


def run(args: argparse.Namespace) -> tuple[int, dict[str, object] | None]:
    if args.no_scaling and args.scale_passages:
        raise ResourceMeasurementError(
            "--no-scaling cannot be combined with --scale-passages."
        )
    if args.measured_passes < 2:
        raise ResourceMeasurementError(
            "--measured-passes must be at least 2 to verify local repeatability."
        )

    corpus = load_corpus_from_file(args.corpus)
    benchmark = load_benchmark(args.benchmark)
    report = evaluate_retriever(BM25Retriever(corpus), benchmark, corpus)
    threshold_failures = find_threshold_failures(report, benchmark.thresholds)
    correctness = _correctness_mapping(report, threshold_failures)
    if threshold_failures:
        return 1, {"correctness": correctness, "resource_observations": None}

    observations = measure_retriever_resources(
        lambda: BM25Retriever(corpus),
        benchmark,
        corpus,
        initialization_runs=args.initialization_runs,
        warmup_passes=args.warmup_passes,
        measured_passes=args.measured_passes,
    )

    if args.no_scaling:
        scaling_counts: tuple[int, ...] = ()
    elif args.scale_passages:
        scaling_counts = tuple(args.scale_passages)
    else:
        scaling_counts = DEFAULT_SCALING_PASSAGE_COUNTS

    scaling_observations: list[dict[str, object]] = []
    for passage_count in scaling_counts:
        scaled_corpus = build_synthetic_scaling_corpus(
            corpus, target_passage_count=passage_count
        )
        scaled_benchmark = replace(
            benchmark,
            benchmark_id=f"{benchmark.benchmark_id}-scale-{passage_count}",
            corpus_id=scaled_corpus.corpus_id,
            corpus_fingerprint=corpus_retrieval_fingerprint(scaled_corpus),
        )
        scaled = measure_retriever_resources(
            lambda scaled_corpus=scaled_corpus: BM25Retriever(scaled_corpus),
            scaled_benchmark,
            scaled_corpus,
            initialization_runs=args.initialization_runs,
            warmup_passes=args.warmup_passes,
            measured_passes=args.measured_passes,
        )
        scaling_observations.append(scaled.to_mapping())

    return 0, {
        "schema_version": 1,
        "correctness": correctness,
        "resource_observations": observations.to_mapping(),
        "scaling_observations": scaling_observations,
        "interpretation": {
            "correctness_is_gating": True,
            "resource_observations_are_gating": False,
            "scaling_corpora_measure_quality": False,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code, payload = run(args)
    except (
        CorpusLoadError,
        OSError,
        json.JSONDecodeError,
        ResourceMeasurementError,
        RetrievalEvaluationError,
    ) as error:
        parser.exit(2, f"retrieval measurement failed: {error}\n")

    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        if args.output is None:
            print(serialized)
        else:
            args.output.write_text(serialized + "\n", encoding="utf-8")
            print(f"Wrote retrieval observations to {args.output}")
    except OSError as error:
        parser.exit(2, f"retrieval measurement output failed: {error}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
