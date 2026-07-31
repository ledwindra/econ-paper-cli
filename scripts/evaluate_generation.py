"""Run the opt-in local generation benchmark for Issue 13 evidence."""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections.abc import Sequence
from pathlib import Path

from econ_paper_cli.adapters import (
    OUTPUT_GRAMMAR_SHA256,
    LlamaCppAdapterError,
    LlamaCppCancelledError,
    LlamaCppConfig,
    LlamaCppGenerator,
    LlamaCppOutputError,
    LlamaCppOutputLimitError,
    LlamaCppProcessError,
    LlamaCppReadinessError,
    LlamaCppTimeoutError,
)
from econ_paper_cli.evaluation import (
    SEMANTIC_SCORE_DIMENSIONS,
    GenerationBenchmarkCase,
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
_PROCESS_EXIT_PATTERN = re.compile(
    r"^Local generation runtime exited with status (-?[0-9]+)\.$"
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
    readiness_failure: dict[str, object] | None = None
    try:
        generator.check_readiness()
    except LlamaCppAdapterError as error:
        readiness_failure = _sanitized_failure(error)
    readiness_seconds = time.perf_counter() - readiness_started

    runs: list[dict[str, object]] = []
    blind_runs: list[dict[str, object]] = []
    candidate_stopped = readiness_failure is not None
    for case in benchmark.cases:
        for repeat in range(1, args.repeats + 1):
            if candidate_stopped:
                runs.append(_not_run_record(case.case_id, repeat))
                blind_runs.append(_blind_run_record(case, repeat, status="not_run"))
                continue

            started = time.perf_counter()
            try:
                response = generator.generate(case.request)
                mechanical = evaluate_generation_response(case, response)
            except LlamaCppAdapterError as error:
                runs.append(
                    {
                        "case_id": case.case_id,
                        "repeat": repeat,
                        "status": "failed",
                        "total_latency_seconds": time.perf_counter() - started,
                        "failure": _sanitized_failure(error),
                        "response": None,
                        "mechanical": None,
                    }
                )
                blind_runs.append(_blind_run_record(case, repeat, status="failed"))
                candidate_stopped = True
                continue

            total_latency_seconds = time.perf_counter() - started
            runs.append(
                {
                    "case_id": case.case_id,
                    "repeat": repeat,
                    "status": "succeeded",
                    "total_latency_seconds": total_latency_seconds,
                    "failure": None,
                    "response": response.to_mapping(),
                    "mechanical": mechanical.to_mapping(),
                }
            )
            blind_runs.append(
                _blind_run_record(
                    case,
                    repeat,
                    status="succeeded",
                    response={
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
                )
            )

    random.Random(args.review_seed).shuffle(blind_runs)
    machine_profile = detect_machine_profile()
    evaluation_status = (
        "readiness_failed"
        if readiness_failure is not None
        else "stopped_after_run_failure"
        if candidate_stopped
        else "completed"
    )
    technical_report: dict[str, object] = {
        "schema_version": 2,
        "evaluation_status": evaluation_status,
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_fingerprint": benchmark.fingerprint,
        "runtime_id": config.runtime_id,
        "runtime_version_marker": config.runtime_version_marker,
        "runtime_executable_path": str(config.executable_path),
        "runtime_executable_size_bytes": _file_size_or_none(config.executable_path),
        "model_id": config.model_id,
        "model_path": str(config.model_path),
        "model_size_bytes": config.model_expected_size_bytes,
        "model_sha256": config.model_sha256,
        "generation_method": generator.generation_method,
        "prompt_version": "generation-v1",
        "output_constraint": {
            "authoritative_schema": "generation-v1.schema.json",
            "runtime_grammar": "generation-v1.gbnf",
            "runtime_grammar_sha256": OUTPUT_GRAMMAR_SHA256,
            "derivation": "llama.cpp b10199 json_schema_to_grammar.py",
        },
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
        "readiness_status": (
            "failed" if readiness_failure is not None else "succeeded"
        ),
        "readiness_failure": readiness_failure,
        "initialization_time_seconds": None,
        "initialization_time_status": (
            "unavailable: one-shot llama-completion initialization is included in "
            "each total-latency observation"
        ),
        "time_to_first_token_seconds": None,
        "time_to_first_token_status": (
            "unavailable: the pinned bounded subprocess path does not expose "
            "a reliable first-token timestamp"
        ),
        "output_throughput_tokens_per_second": None,
        "output_throughput_status": (
            "unavailable: runtime logs are redirected away from captured output"
        ),
        "peak_process_rss_bytes": None,
        "peak_process_rss_status": "unavailable in the portable Issue 12 runner",
        "runtime_installation_footprint_bytes": None,
        "runtime_installation_footprint_status": (
            "unavailable: only the configured executable size is recorded"
        ),
        "resource_observations_are_gating": False,
        "run_summary": {
            "scheduled": len(runs),
            "succeeded": sum(run["status"] == "succeeded" for run in runs),
            "failed": sum(run["status"] == "failed" for run in runs),
            "not_run": sum(run["status"] == "not_run" for run in runs),
        },
        "runs": runs,
    }
    blind_review: dict[str, object] = {
        "schema_version": 2,
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
        "dimension_applicability": {
            "policy": (
                "Every dimension receives an integer score of 0, 1, or 2 for "
                "every succeeded run; N/A is not used and blank null values mean "
                "not yet reviewed."
            ),
            "no_substantive_claim_rule": (
                "When a dimension has no positive claim to assess, score 2 only "
                "if the response correctly avoids unsupported content in that "
                "dimension, 1 for a non-material ambiguity, and 0 for an "
                "unsupported or materially misleading assertion."
            ),
            "required_abstention_rule": (
                "For the required-abstention case, score 2 for claim support, "
                "citation support, causal characterization, and uncertainty or "
                "disagreement only when the response makes no substantive claim, "
                "returns no citation, adds no causal characterization, and "
                "invents no certainty or disagreement. Score abstention or "
                "partial-answer appropriateness as 2 only for a correct explicit "
                "abstention. Failure to abstain scores 0 and triggers the "
                "required-abstention veto."
            ),
            "operational_failure_rule": (
                "Failed and not-run entries are not semantically scored; their "
                "null scores are excluded because the candidate has already "
                "failed the mechanical gate."
            ),
        },
        "reviewer_count": None,
        "review_procedure": None,
        "runs": blind_runs,
    }
    return technical_report, blind_review


def _not_run_record(case_id: str, repeat: int) -> dict[str, object]:
    return {
        "case_id": case_id,
        "repeat": repeat,
        "status": "not_run",
        "total_latency_seconds": None,
        "failure": None,
        "not_run_reason": "candidate_stopped_after_failure",
        "response": None,
        "mechanical": None,
    }


def _blind_run_record(
    case: GenerationBenchmarkCase,
    repeat: int,
    *,
    status: str,
    response: dict[str, object] | None = None,
) -> dict[str, object]:
    evidence = [
        {
            "citation_id": f"e{item.rank}",
            "paper_id": item.passage.paper_id,
            "passage_id": item.passage.passage_id,
            "text": item.passage.text,
            "section_heading": item.passage.section_heading,
            "page_start": item.passage.page_start,
            "page_end": item.passage.page_end,
        }
        for item in case.request.evidence
    ]
    return {
        "case_id": case.case_id,
        "category": case.category,
        "repeat": repeat,
        "operational_status": status,
        "question": case.request.question,
        "evidence": evidence,
        "response": response,
        "semantic_expectations": list(case.semantic_expectations),
        "prohibited_claims": list(case.prohibited_claims),
        "critical_vetoes": list(case.critical_vetoes),
        "scores": {dimension: None for dimension in SEMANTIC_SCORE_DIMENSIONS},
        "triggered_vetoes": [],
        "reviewer_notes": None,
    }


def _sanitized_failure(error: LlamaCppAdapterError) -> dict[str, object]:
    details: dict[str, object] = {}
    if isinstance(error, LlamaCppTimeoutError):
        code = "timeout"
    elif isinstance(error, LlamaCppOutputLimitError):
        code = "output_limit"
    elif isinstance(error, LlamaCppCancelledError):
        code = "cancelled"
    elif isinstance(error, LlamaCppReadinessError):
        code = "readiness_failure"
    elif isinstance(error, LlamaCppOutputError):
        code = (
            "invalid_json"
            if str(error) == "Local model returned invalid JSON."
            else "invalid_model_output"
        )
    elif isinstance(error, LlamaCppProcessError):
        exit_match = _PROCESS_EXIT_PATTERN.fullmatch(str(error))
        if exit_match is None:
            code = "process_failure"
        else:
            code = "process_exit"
            details["exit_status"] = int(exit_match.group(1))
    else:
        code = "adapter_failure"
    failure: dict[str, object] = {
        "code": code,
        "exception_type": type(error).__name__,
    }
    failure.update(details)
    return failure


def _file_size_or_none(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


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
    if technical_report["evaluation_status"] != "completed":
        print(
            "Generation evaluation stopped after a recorded failure; "
            "inspect the report before evaluating another candidate."
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
