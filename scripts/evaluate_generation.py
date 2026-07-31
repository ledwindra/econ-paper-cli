"""Run the opt-in local generation benchmark for Issue 13 evidence."""

from __future__ import annotations

import argparse
import json
import random
import time
from collections.abc import Sequence
from pathlib import Path

from econ_paper_cli.adapters import (
    LlamaCppAdapterError,
    LlamaCppConfig,
    LlamaCppGenerator,
)
from econ_paper_cli.evaluation import (
    SEMANTIC_SCORE_DIMENSIONS,
    GenerationEvaluationError,
    detect_machine_profile,
    evaluate_generation_response,
    load_generation_benchmark,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "generation"
    / "synthetic-economics-generation-v1.json"
)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than or equal to 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an explicitly installed llama.cpp/model pair against the "
            "synthetic generation benchmark. This tool never downloads artifacts."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK_PATH)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-size-bytes", type=_positive_int, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--runtime-id", default="llama.cpp-b10199")
    parser.add_argument("--runtime-version-marker", default="10199")
    parser.add_argument("--candidate-code", required=True)
    parser.add_argument("--threads", type=_positive_int)
    parser.add_argument("--repeats", type=_positive_int, default=3)
    parser.add_argument("--review-seed", type=int, default=12013)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--review-output",
        type=Path,
        help=("optional blinded review template; it omits model and runtime identity"),
    )
    return parser


def run(args: argparse.Namespace) -> tuple[dict[str, object], dict[str, object]]:
    benchmark = load_generation_benchmark(args.benchmark)
    config = LlamaCppConfig(
        executable_path=args.executable,
        model_path=args.model,
        model_id=args.model_id,
        model_expected_size_bytes=args.model_size_bytes,
        model_sha256=args.model_sha256,
        runtime_id=args.runtime_id,
        runtime_version_marker=args.runtime_version_marker,
        threads=args.threads,
    )
    generator = LlamaCppGenerator(config)

    readiness_started = time.perf_counter()
    generator.check_readiness()
    readiness_seconds = time.perf_counter() - readiness_started

    runs: list[dict[str, object]] = []
    blind_runs: list[dict[str, object]] = []
    for case in benchmark.cases:
        for repeat in range(1, args.repeats + 1):
            started = time.perf_counter()
            response = generator.generate(case.request)
            total_latency_seconds = time.perf_counter() - started
            mechanical = evaluate_generation_response(case, response)
            runs.append(
                {
                    "case_id": case.case_id,
                    "repeat": repeat,
                    "total_latency_seconds": total_latency_seconds,
                    "response": response.to_mapping(),
                    "mechanical": mechanical.to_mapping(),
                }
            )
            blind_runs.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "repeat": repeat,
                    "question": case.request.question,
                    "response": {
                        "answer_text": response.answer_text,
                        "citation_ids": [
                            citation.citation_id for citation in response.citations
                        ],
                        "abstained": response.abstained,
                        "abstention_reason": (
                            None
                            if response.abstention_reason is None
                            else response.abstention_reason.value
                        ),
                        "finding_kinds": [
                            kind.value for kind in response.finding_kinds
                        ],
                    },
                    "semantic_expectations": list(case.semantic_expectations),
                    "prohibited_claims": list(case.prohibited_claims),
                    "critical_vetoes": list(case.critical_vetoes),
                    "scores": {
                        dimension: None for dimension in SEMANTIC_SCORE_DIMENSIONS
                    },
                    "triggered_vetoes": [],
                    "reviewer_notes": None,
                }
            )

    random.Random(args.review_seed).shuffle(blind_runs)
    machine_profile = detect_machine_profile()
    technical_report: dict[str, object] = {
        "schema_version": 1,
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_fingerprint": benchmark.fingerprint,
        "runtime_id": config.runtime_id,
        "runtime_version_marker": config.runtime_version_marker,
        "runtime_executable_path": str(config.executable_path),
        "runtime_executable_size_bytes": config.executable_path.stat().st_size,
        "model_id": config.model_id,
        "model_path": str(config.model_path),
        "model_size_bytes": config.model_expected_size_bytes,
        "model_sha256": config.model_sha256,
        "generation_method": generator.generation_method,
        "prompt_version": "generation-v1",
        "inference_configuration": {
            "context_size": config.context_size,
            "max_output_tokens": config.max_output_tokens,
            "threads": config.threads,
            "seed": config.seed,
            "temperature": config.temperature,
            "top_k": config.top_k,
            "top_p": config.top_p,
            "reasoning": "off",
            "chat_template": "embedded GGUF Jinja template",
        },
        "machine_profile": machine_profile.to_mapping(),
        "readiness_validation_seconds": readiness_seconds,
        "initialization_time_seconds": None,
        "initialization_time_status": (
            "unavailable: one-shot llama-cli initialization is included in "
            "each total-latency observation"
        ),
        "time_to_first_token_seconds": None,
        "time_to_first_token_status": (
            "unavailable: the pinned no-log subprocess path does not expose "
            "a reliable first-token timestamp"
        ),
        "output_throughput_tokens_per_second": None,
        "output_throughput_status": (
            "unavailable: runtime timing logs are disabled to protect prompt data"
        ),
        "peak_process_rss_bytes": None,
        "peak_process_rss_status": "unavailable in the portable Issue 12 runner",
        "runtime_installation_footprint_bytes": None,
        "runtime_installation_footprint_status": (
            "unavailable: only the configured executable size is recorded"
        ),
        "resource_observations_are_gating": False,
        "runs": runs,
    }
    blind_review: dict[str, object] = {
        "schema_version": 1,
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_fingerprint": benchmark.fingerprint,
        "candidate_code": args.candidate_code,
        "review_seed": args.review_seed,
        "substantive_claim_unit": benchmark.substantive_claim_unit,
        "score_scale": {
            "0": "fails or materially misstates the dimension",
            "1": "partially satisfies the dimension",
            "2": "fully satisfies the dimension",
        },
        "reviewer_count": None,
        "review_procedure": None,
        "runs": blind_runs,
    }
    return technical_report, blind_review


def _write_json(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        technical_report, blind_review = run(args)
        _write_json(args.output, technical_report)
        if args.review_output is not None:
            _write_json(args.review_output, blind_review)
    except (GenerationEvaluationError, LlamaCppAdapterError, OSError) as error:
        parser.exit(2, f"generation evaluation failed: {error}\n")
    print(f"Wrote generation evaluation report to {args.output}")
    if args.review_output is not None:
        print(f"Wrote blinded semantic-review template to {args.review_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
