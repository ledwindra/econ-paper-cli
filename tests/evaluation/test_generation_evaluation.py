"""Tests for the synthetic, model-independent generation benchmark."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from econ_paper_cli.adapters import load_manifest_from_file
from econ_paper_cli.domain import Citation
from econ_paper_cli.evaluation import (
    SEMANTIC_SCORE_DIMENSIONS,
    GenerationEvaluationError,
    evaluate_generation_response,
    generation_benchmark_fingerprint,
    load_generation_benchmark,
)
from econ_paper_cli.protocols import GenerationResponse

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "generation"
    / "synthetic-economics-generation-v1.json"
)
MODEL_MANIFEST_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "models"


def test_frozen_benchmark_has_all_required_synthetic_cases() -> None:
    benchmark = load_generation_benchmark(BENCHMARK_PATH)

    assert benchmark.license == "CC0-1.0"
    assert len(benchmark.cases) == 12
    assert {case.category for case in benchmark.cases} == {
        "descriptive",
        "causal",
        "observational",
        "agreement",
        "conflict",
        "uncertainty",
        "null",
        "insufficient",
        "distractors",
        "partial",
        "multiple_citations",
        "prompt_injection",
    }
    assert benchmark.fingerprint == generation_benchmark_fingerprint(benchmark)
    assert "independently checkable assertion" in benchmark.substantive_claim_unit
    assert SEMANTIC_SCORE_DIMENSIONS == (
        "claim_support",
        "citation_support",
        "causal_characterization",
        "uncertainty_and_disagreement",
        "abstention_or_partial_answer",
    )


def test_benchmark_loader_is_deterministic() -> None:
    assert load_generation_benchmark(BENCHMARK_PATH) == load_generation_benchmark(
        BENCHMARK_PATH
    )


def test_fingerprint_detects_semantic_judgment_change() -> None:
    benchmark = load_generation_benchmark(BENCHMARK_PATH)
    changed_case = replace(
        benchmark.cases[0],
        semantic_expectations=("A changed expectation.",),
    )

    with pytest.raises(GenerationEvaluationError, match="fingerprint mismatch"):
        replace(
            benchmark,
            cases=(changed_case, *benchmark.cases[1:]),
        )


def test_loader_rejects_changed_fixture_with_stale_fingerprint(
    tmp_path: Path,
) -> None:
    data = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    data["cases"][0]["question"] = "Changed question?"
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GenerationEvaluationError, match="fingerprint mismatch"):
        load_generation_benchmark(changed_path)


def test_mechanical_result_uses_existing_generation_contract() -> None:
    benchmark = load_generation_benchmark(BENCHMARK_PATH)
    case = benchmark.cases[0]
    evidence = case.request.evidence[0]
    response = GenerationResponse.from_mapping(
        {
            "answer_text": "Thirty-eight percent offered paid training.",
            "citations": [
                Citation(
                    citation_id="e1",
                    paper_id=evidence.passage.paper_id,
                    passage_id=evidence.passage.passage_id,
                ).to_mapping()
            ],
            "generation_method": "synthetic-test",
            "abstained": False,
            "abstention_reason": None,
            "finding_kinds": ["descriptive"],
        }
    )

    result = evaluate_generation_response(case, response)

    assert result.case_id == "descriptive-supported"
    assert result.citation_ids == ("e1",)
    assert result.abstained is False
    assert result.finding_kinds == ("descriptive",)
    assert result.response_digest.startswith("sha256:")
    assert result.to_mapping()["citation_ids"] == ["e1"]


def test_candidate_model_manifests_are_schema_valid_and_pinned() -> None:
    manifests = tuple(
        load_manifest_from_file(path)
        for path in sorted(MODEL_MANIFEST_DIRECTORY.glob("*.manifest.json"))
    )

    assert [manifest.artifact_id for manifest in manifests] == [
        "qwen2.5-1.5b-instruct-q4-k-m",
        "qwen3-0.6b-q8-0",
        "smollm2-1.7b-instruct-q4-k-m",
    ]
    assert all(manifest.kind.value == "model" for manifest in manifests)
    assert all(
        manifest.update_policy.startswith("Pinned Issue 13") for manifest in manifests
    )
    assert all(not manifest.contains_copyrighted_full_text for manifest in manifests)
    smollm = manifests[-1]
    assert smollm.redistribution_status.value == "unknown"
