"""Explicitly opt-in smoke test for a manually installed runtime and model."""

import os
from pathlib import Path

import pytest

from econ_paper_cli.adapters import (
    LlamaCppConfig,
    LlamaCppGenerator,
    load_manifest_from_file,
)
from econ_paper_cli.evaluation import (
    evaluate_generation_response,
    load_generation_benchmark,
)

pytestmark = pytest.mark.model

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_manually_installed_model_satisfies_one_generation_contract() -> None:
    executable_value = os.environ.get("ECONPAPERS_LLAMA_CPP")
    manifest_value = os.environ.get("ECONPAPERS_MODEL_MANIFEST")
    model_base_value = os.environ.get("ECONPAPERS_MODEL_BASE")
    if not executable_value or not manifest_value or not model_base_value:
        pytest.skip(
            "Set ECONPAPERS_LLAMA_CPP, ECONPAPERS_MODEL_MANIFEST, and "
            "ECONPAPERS_MODEL_BASE to run this opt-in test."
        )

    manifest = load_manifest_from_file(Path(manifest_value))
    benchmark = load_generation_benchmark(
        REPOSITORY_ROOT
        / "tests"
        / "fixtures"
        / "generation"
        / "synthetic-economics-generation-v1.json"
    )
    config = LlamaCppConfig(
        executable_path=Path(executable_value),
        model_path=Path(model_base_value) / manifest.local_path,
        model_id=manifest.artifact_id,
        model_expected_size_bytes=manifest.expected_size_bytes,
        model_sha256=manifest.sha256,
        threads=2,
    )
    generator = LlamaCppGenerator(config)

    response = generator.generate(benchmark.cases[0].request)

    result = evaluate_generation_response(benchmark.cases[0], response)
    assert result.case_id == benchmark.cases[0].case_id
