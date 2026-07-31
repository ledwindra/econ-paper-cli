"""Model-free tests for the opt-in generation evaluation script."""

import argparse
from pathlib import Path

import pytest
from scripts import evaluate_generation

from econ_paper_cli.adapters import (
    OUTPUT_GRAMMAR_SHA256,
    LlamaCppOutputError,
    LlamaCppOutputLimitError,
    LlamaCppProcessError,
    LlamaCppTimeoutError,
)
from econ_paper_cli.domain import Citation
from econ_paper_cli.protocols import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
)


class SuccessfulGenerator:
    """Return contract-valid synthetic responses without running a model."""

    calls = 0

    def __init__(self, config: object) -> None:
        self.generation_method = "synthetic-evaluation-test"

    def check_readiness(self) -> None:
        return None

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        type(self).calls += 1
        if not request.evidence:
            return GenerationResponse(
                answer_text="The supplied evidence is insufficient.",
                citations=(),
                generation_method=self.generation_method,
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )
        citations = tuple(
            Citation(
                citation_id=f"e{item.rank}",
                paper_id=item.passage.paper_id,
                passage_id=item.passage.passage_id,
            )
            for item in request.evidence
        )
        return GenerationResponse(
            answer_text="The supplied synthetic evidence reports a finding.",
            citations=citations,
            generation_method=self.generation_method,
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )


def make_args(tmp_path: Path) -> argparse.Namespace:
    executable = tmp_path / "llama-completion"
    executable.write_bytes(b"synthetic runtime")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"synthetic model")
    return argparse.Namespace(
        benchmark=evaluate_generation.DEFAULT_BENCHMARK_PATH,
        executable=executable,
        model=model,
        model_id="synthetic-model",
        model_size_bytes=model.stat().st_size,
        model_sha256="a" * 64,
        runtime_id="llama.cpp-b10199",
        runtime_version_marker="10199",
        candidate_code="candidate-test",
        threads=2,
        repeats=3,
        review_seed=12013,
        output=tmp_path / "technical.json",
        review_output=tmp_path / "review.json",
    )


@pytest.mark.parametrize(
    ("error", "expected_failure"),
    (
        (
            LlamaCppOutputError("Local model returned invalid JSON."),
            {"code": "invalid_json", "exception_type": "LlamaCppOutputError"},
        ),
        (
            LlamaCppTimeoutError("Local generation exceeded its configured timeout."),
            {"code": "timeout", "exception_type": "LlamaCppTimeoutError"},
        ),
        (
            LlamaCppProcessError("Local generation runtime exited with status 7."),
            {
                "code": "process_exit",
                "exception_type": "LlamaCppProcessError",
                "exit_status": 7,
            },
        ),
        (
            LlamaCppOutputLimitError(
                "Local generation output exceeded the configured capture limit."
            ),
            {
                "code": "output_limit",
                "exception_type": "LlamaCppOutputLimitError",
            },
        ),
    ),
)
def test_run_records_first_failure_and_marks_remaining_runs_not_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_failure: dict[str, object],
) -> None:
    class FailingGenerator(SuccessfulGenerator):
        calls = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            type(self).calls += 1
            raise error

    monkeypatch.setattr(evaluate_generation, "LlamaCppGenerator", FailingGenerator)

    report, review = evaluate_generation.run(make_args(tmp_path))

    assert FailingGenerator.calls == 1
    assert report["evaluation_status"] == "stopped_after_run_failure"
    assert report["run_summary"] == {
        "scheduled": 36,
        "succeeded": 0,
        "failed": 1,
        "not_run": 35,
    }
    runs = report["runs"]
    assert isinstance(runs, list)
    assert runs[0]["status"] == "failed"
    assert runs[0]["failure"] == expected_failure
    assert all(run["status"] == "not_run" for run in runs[1:])
    assert all(run["response"] is None for run in runs)
    assert all(
        run["operational_status"] in {"failed", "not_run"} for run in review["runs"]
    )


def test_blinded_packet_contains_frozen_evidence_and_scoring_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SuccessfulGenerator.calls = 0
    monkeypatch.setattr(evaluate_generation, "LlamaCppGenerator", SuccessfulGenerator)

    report, review = evaluate_generation.run(make_args(tmp_path))

    assert report["evaluation_status"] == "completed"
    assert report["schema_version"] == 2
    assert report["output_constraint"] == {
        "authoritative_schema": "generation-v1.schema.json",
        "runtime_grammar": "generation-v1.gbnf",
        "runtime_grammar_sha256": OUTPUT_GRAMMAR_SHA256,
        "derivation": "llama.cpp b10199 json_schema_to_grammar.py",
    }
    assert review["schema_version"] == 2
    assert SuccessfulGenerator.calls == 36
    runs = review["runs"]
    descriptive = next(
        run
        for run in runs
        if run["case_id"] == "descriptive-supported" and run["repeat"] == 1
    )
    assert descriptive["evidence"] == [
        {
            "citation_id": "e1",
            "paper_id": "synthetic-training",
            "passage_id": "synthetic-training:results-1",
            "text": (
                "In the synthetic survey sample of 800 firms, 38 percent "
                "reported offering paid worker training in 2024. The statistic "
                "is descriptive and is not an estimate of a policy effect."
            ),
            "section_heading": "Descriptive results",
            "page_start": 4,
            "page_end": 4,
        }
    ]
    applicability = review["dimension_applicability"]
    assert "N/A is not used" in applicability["policy"]
    assert "required-abstention case" in applicability["required_abstention_rule"]
    assert "not semantically scored" in applicability["operational_failure_rule"]


def test_successful_runs_are_preserved_before_a_later_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LaterFailingGenerator(SuccessfulGenerator):
        calls = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            if type(self).calls == 1:
                type(self).calls += 1
                raise LlamaCppTimeoutError(
                    "Local generation exceeded its configured timeout."
                )
            return super().generate(request)

    monkeypatch.setattr(evaluate_generation, "LlamaCppGenerator", LaterFailingGenerator)

    report, _ = evaluate_generation.run(make_args(tmp_path))

    assert LaterFailingGenerator.calls == 2
    assert report["run_summary"] == {
        "scheduled": 36,
        "succeeded": 1,
        "failed": 1,
        "not_run": 34,
    }
    runs = report["runs"]
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["response"] is not None
    assert runs[1]["status"] == "failed"
    assert all(run["status"] == "not_run" for run in runs[2:])


def test_main_writes_partial_reports_before_returning_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingGenerator(SuccessfulGenerator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            raise LlamaCppTimeoutError(
                "Local generation exceeded its configured timeout."
            )

    monkeypatch.setattr(evaluate_generation, "LlamaCppGenerator", FailingGenerator)
    args = make_args(tmp_path)

    exit_code = evaluate_generation.main(
        [
            "--benchmark",
            str(args.benchmark),
            "--executable",
            str(args.executable),
            "--model",
            str(args.model),
            "--model-id",
            args.model_id,
            "--model-size-bytes",
            str(args.model_size_bytes),
            "--model-sha256",
            args.model_sha256,
            "--candidate-code",
            args.candidate_code,
            "--threads",
            str(args.threads),
            "--repeats",
            str(args.repeats),
            "--review-seed",
            str(args.review_seed),
            "--output",
            str(args.output),
            "--review-output",
            str(args.review_output),
        ]
    )

    assert exit_code == 2
    assert args.output.exists()
    assert args.review_output.exists()
    assert '"evaluation_status": "stopped_after_run_failure"' in (
        args.output.read_text(encoding="utf-8")
    )
