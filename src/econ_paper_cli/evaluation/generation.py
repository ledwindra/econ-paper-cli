"""Deterministic, model-independent generation benchmark contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from econ_paper_cli.protocols import (
    GenerationRequest,
    GenerationRequestValidationError,
    GenerationResponse,
    validate_generation_response,
)

_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_CASE_CATEGORIES = frozenset(
    {
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
)
_CRITICAL_VETOES = frozenset(
    {
        "fabricated_paper_attribute_or_result",
        "prompt_injection_obedience",
        "unsupported_causal_claim",
        "required_abstention_failure",
    }
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "question",
        "evidence",
        "semantic_expectations",
        "prohibited_claims",
        "critical_vetoes",
    }
)
_BENCHMARK_FIELDS = frozenset(
    {
        "schema_version",
        "benchmark_id",
        "license",
        "source",
        "substantive_claim_unit",
        "cases",
        "fingerprint",
    }
)

SEMANTIC_SCORE_DIMENSIONS = (
    "claim_support",
    "citation_support",
    "causal_characterization",
    "uncertainty_and_disagreement",
    "abstention_or_partial_answer",
)


class GenerationEvaluationError(ValueError):
    """Raised when generation benchmark data violates its frozen contract."""


@dataclass(frozen=True, slots=True)
class GenerationBenchmarkCase:
    """One synthetic question, ranked evidence set, and review instructions."""

    case_id: str
    category: str
    request: GenerationRequest
    semantic_expectations: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    critical_vetoes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_id("case_id", self.case_id)
        if self.category not in _CASE_CATEGORIES:
            raise GenerationEvaluationError(
                f"category must be one of: {', '.join(sorted(_CASE_CATEGORIES))}."
            )
        if not isinstance(self.request, GenerationRequest):
            raise GenerationEvaluationError(
                "request must be a GenerationRequest instance."
            )
        _validate_text_tuple(
            "semantic_expectations", self.semantic_expectations, allow_empty=False
        )
        _validate_text_tuple("prohibited_claims", self.prohibited_claims)
        if not isinstance(self.critical_vetoes, tuple):
            raise GenerationEvaluationError("critical_vetoes must be a tuple.")
        if len(set(self.critical_vetoes)) != len(self.critical_vetoes):
            raise GenerationEvaluationError("critical_vetoes must not repeat values.")
        unknown = sorted(set(self.critical_vetoes) - _CRITICAL_VETOES)
        if unknown:
            raise GenerationEvaluationError(
                f"critical_vetoes contains unknown values: {', '.join(unknown)}."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "GenerationBenchmarkCase":
        _validate_mapping_fields("generation benchmark case", data, _CASE_FIELDS)
        raw_expectations = _require_sequence(
            "semantic_expectations", data["semantic_expectations"]
        )
        raw_prohibited = _require_sequence(
            "prohibited_claims", data["prohibited_claims"]
        )
        raw_vetoes = _require_sequence("critical_vetoes", data["critical_vetoes"])
        try:
            request = GenerationRequest.from_mapping(
                {
                    "question": data["question"],
                    "evidence": data["evidence"],
                }
            )
        except GenerationRequestValidationError as error:
            raise GenerationEvaluationError(
                f"Generation benchmark case request is invalid: {error}"
            ) from error
        return cls(
            case_id=cast(str, data["case_id"]),
            category=cast(str, data["category"]),
            request=request,
            semantic_expectations=tuple(cast(Sequence[str], raw_expectations)),
            prohibited_claims=tuple(cast(Sequence[str], raw_prohibited)),
            critical_vetoes=tuple(cast(Sequence[str], raw_vetoes)),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical benchmark-case representation."""
        return {
            "case_id": self.case_id,
            "category": self.category,
            "question": self.request.question,
            "evidence": [item.to_mapping() for item in self.request.evidence],
            "semantic_expectations": list(self.semantic_expectations),
            "prohibited_claims": list(self.prohibited_claims),
            "critical_vetoes": list(self.critical_vetoes),
        }


@dataclass(frozen=True, slots=True)
class GenerationBenchmark:
    """Frozen synthetic benchmark with self-verifying content fingerprint."""

    schema_version: int
    benchmark_id: str
    license: str
    source: str
    substantive_claim_unit: str
    cases: tuple[GenerationBenchmarkCase, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise GenerationEvaluationError("schema_version must be the integer 1.")
        _validate_id("benchmark_id", self.benchmark_id)
        _validate_nonempty_text("license", self.license)
        _validate_nonempty_text("source", self.source)
        _validate_nonempty_text("substantive_claim_unit", self.substantive_claim_unit)
        if not isinstance(self.cases, tuple) or not self.cases:
            raise GenerationEvaluationError("cases must be a non-empty tuple.")
        if any(not isinstance(case, GenerationBenchmarkCase) for case in self.cases):
            raise GenerationEvaluationError(
                "cases must contain only GenerationBenchmarkCase instances."
            )
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise GenerationEvaluationError("case_id values must be unique.")
        categories = {case.category for case in self.cases}
        missing_categories = sorted(_CASE_CATEGORIES - categories)
        if missing_categories:
            raise GenerationEvaluationError(
                "benchmark is missing required categories: "
                + ", ".join(missing_categories)
                + "."
            )
        if (
            not isinstance(self.fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(self.fingerprint) is None
        ):
            raise GenerationEvaluationError(
                "fingerprint must use 'sha256:' followed by 64 lowercase hex characters."
            )
        actual = generation_benchmark_fingerprint(self)
        if actual != self.fingerprint:
            raise GenerationEvaluationError(
                f"benchmark fingerprint mismatch: expected '{self.fingerprint}', "
                f"got '{actual}'. Review semantic judgments before updating it."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "GenerationBenchmark":
        _validate_mapping_fields("generation benchmark", data, _BENCHMARK_FIELDS)
        raw_cases = _require_sequence("cases", data["cases"])
        return cls(
            schema_version=cast(int, data["schema_version"]),
            benchmark_id=cast(str, data["benchmark_id"]),
            license=cast(str, data["license"]),
            source=cast(str, data["source"]),
            substantive_claim_unit=cast(str, data["substantive_claim_unit"]),
            cases=tuple(
                GenerationBenchmarkCase.from_mapping(cast(Mapping[str, object], item))
                for item in raw_cases
            ),
            fingerprint=cast(str, data["fingerprint"]),
        )


@dataclass(frozen=True, slots=True)
class GenerationMechanicalResult:
    """Mechanically verifiable facts for one candidate response."""

    case_id: str
    response_digest: str
    citation_ids: tuple[str, ...]
    abstained: bool
    finding_kinds: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-compatible result mapping."""
        return {
            "case_id": self.case_id,
            "response_digest": self.response_digest,
            "citation_ids": list(self.citation_ids),
            "abstained": self.abstained,
            "finding_kinds": list(self.finding_kinds),
        }


def load_generation_benchmark(path: Path) -> GenerationBenchmark:
    """Load and validate one UTF-8 JSON generation benchmark."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GenerationEvaluationError(
            f"Unable to read generation benchmark at '{path}'."
        ) from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GenerationEvaluationError(
            f"Generation benchmark at '{path}' is not valid JSON."
        ) from error
    if not isinstance(data, Mapping):
        raise GenerationEvaluationError(
            f"Generation benchmark at '{path}' must contain a JSON object."
        )
    return GenerationBenchmark.from_mapping(cast(Mapping[str, object], data))


def generation_benchmark_fingerprint(benchmark: GenerationBenchmark) -> str:
    """Hash exact benchmark content, excluding the stored fingerprint."""
    content = {
        "schema_version": benchmark.schema_version,
        "benchmark_id": benchmark.benchmark_id,
        "license": benchmark.license,
        "source": benchmark.source,
        "substantive_claim_unit": benchmark.substantive_claim_unit,
        "cases": [case.to_mapping() for case in benchmark.cases],
    }
    canonical = json.dumps(
        content, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def evaluate_generation_response(
    case: GenerationBenchmarkCase, response: GenerationResponse
) -> GenerationMechanicalResult:
    """Apply existing contracts and return structural observations only."""
    validated = validate_generation_response(case.request, response)
    mapping = validated.to_mapping()
    canonical = json.dumps(
        mapping, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return GenerationMechanicalResult(
        case_id=case.case_id,
        response_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        citation_ids=tuple(citation.citation_id for citation in validated.citations),
        abstained=validated.abstained,
        finding_kinds=tuple(kind.value for kind in validated.finding_kinds),
    )


def _validate_mapping_fields(
    label: str, data: Mapping[str, object], expected: frozenset[str]
) -> None:
    if not isinstance(data, Mapping):
        raise GenerationEvaluationError(f"{label} must be a mapping.")
    if any(not isinstance(key, str) for key in data):
        raise GenerationEvaluationError(f"{label} keys must be strings.")
    provided = set(data)
    missing = sorted(expected - provided)
    unknown = sorted(provided - expected)
    if missing:
        raise GenerationEvaluationError(
            f"{label} is missing required fields: {', '.join(missing)}."
        )
    if unknown:
        raise GenerationEvaluationError(
            f"{label} contains unknown fields: {', '.join(unknown)}."
        )


def _require_sequence(label: str, value: object) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise GenerationEvaluationError(f"{label} must be a JSON array.")
    return cast(Sequence[object], value)


def _validate_text_tuple(
    label: str, value: object, *, allow_empty: bool = True
) -> None:
    if not isinstance(value, tuple):
        raise GenerationEvaluationError(f"{label} must be a tuple.")
    if not allow_empty and not value:
        raise GenerationEvaluationError(f"{label} must not be empty.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise GenerationEvaluationError(f"{label} must contain only non-empty strings.")
    if len(set(value)) != len(value):
        raise GenerationEvaluationError(f"{label} must not contain duplicates.")


def _validate_id(label: str, value: object) -> None:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise GenerationEvaluationError(
            f"{label} must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_nonempty_text(label: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GenerationEvaluationError(f"{label} must be a non-empty string.")
