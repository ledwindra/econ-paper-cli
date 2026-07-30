"""Tests for the backend-independent generation protocol."""

import json
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from econ_paper_cli.domain import Citation, Passage, RetrievalEvidence
from econ_paper_cli.protocols import (
    AbstentionReason,
    FindingKind,
    GenerationContractError,
    GenerationRequest,
    GenerationRequestValidationError,
    GenerationResponse,
    GenerationResponseValidationError,
    Generator,
    validate_generation_response,
)


def make_evidence(
    rank: int,
    *,
    paper_id: str = "paper-1",
    passage_id: str | None = None,
) -> RetrievalEvidence:
    resolved_passage_id = passage_id or f"paper-1:passage-{rank}"
    return RetrievalEvidence(
        passage=Passage(
            passage_id=resolved_passage_id,
            paper_id=paper_id,
            text=f"Synthetic evidence passage {rank}.",
            section_heading="Results",
            page_start=rank,
            page_end=None,
            ordinal_position=rank - 1,
        ),
        score=float(10 - rank),
        rank=rank,
        retrieval_method="scripted-retrieval-v1",
    )


def make_request(*evidence: RetrievalEvidence) -> GenerationRequest:
    return GenerationRequest(
        question="What does the synthetic evidence show?",
        evidence=tuple(evidence),
    )


def make_answered_response(*citations: Citation) -> GenerationResponse:
    return GenerationResponse(
        answer_text="The supplied studies report a synthetic finding.",
        citations=tuple(citations),
        generation_method="scripted-generator-v1",
        abstained=False,
        abstention_reason=None,
        finding_kinds=(FindingKind.DESCRIPTIVE,),
    )


def make_abstaining_response() -> GenerationResponse:
    return GenerationResponse(
        answer_text="The supplied evidence is insufficient to answer the question.",
        citations=(),
        generation_method="scripted-generator-v1",
        abstained=True,
        abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
        finding_kinds=(),
    )


class ScriptedGenerator:
    """Deterministic no-I/O generator used only for protocol tests."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if not request.evidence:
            return make_abstaining_response()
        first = request.evidence[0]
        return make_answered_response(
            Citation(
                citation_id="e1",
                paper_id=first.passage.paper_id,
                passage_id=first.passage.passage_id,
            )
        )


def test_generation_request_is_immutable_and_normalizes_question() -> None:
    request = GenerationRequest(question="  A question?\n", evidence=())

    assert request.question == "A question?"
    with pytest.raises(FrozenInstanceError):
        request.question = "changed"  # type: ignore[misc]


def test_generation_request_from_mapping_accepts_json_lists_and_round_trips() -> None:
    evidence = make_evidence(1)
    serialized = json.dumps(
        {
            "question": "What is reported?",
            "evidence": [evidence.to_mapping()],
        }
    )

    request = GenerationRequest.from_mapping(json.loads(serialized))

    assert request.evidence == (evidence,)
    assert request.to_mapping() == {
        "question": "What is reported?",
        "evidence": [evidence.to_mapping()],
    }
    json.dumps(request.to_mapping())


@pytest.mark.parametrize(
    ("data", "message"),
    (
        ({"question": "", "evidence": []}, "question must be a non-empty"),
        ({"question": "valid", "evidence": "invalid"}, "evidence must be a JSON array"),
        ({"question": "valid"}, "missing required fields: evidence"),
        (
            {"question": "valid", "evidence": [], "extra": True},
            "unknown fields: extra",
        ),
    ),
)
def test_generation_request_from_mapping_rejects_invalid_shapes(
    data: object, message: str
) -> None:
    with pytest.raises(GenerationRequestValidationError, match=message):
        GenerationRequest.from_mapping(data)  # type: ignore[arg-type]


def test_generation_request_direct_constructor_requires_tuple() -> None:
    with pytest.raises(
        GenerationRequestValidationError, match="evidence must be a tuple"
    ):
        GenerationRequest(question="Question?", evidence=[])  # type: ignore[arg-type]


def test_generation_request_rejects_invalid_retrieval_contract() -> None:
    evidence = make_evidence(2)

    with pytest.raises(
        GenerationRequestValidationError,
        match="evidence violates the retrieval result contract.*contiguous rank 1",
    ):
        make_request(evidence)


def test_answered_response_json_round_trip_uses_lists_and_enum_values() -> None:
    citation = Citation(
        citation_id="e1",
        paper_id="paper-1",
        passage_id="paper-1:passage-1",
    )
    response = GenerationResponse(
        answer_text="A causal and descriptive answer.",
        citations=(citation,),
        generation_method="scripted-generator-v1",
        abstained=False,
        abstention_reason=None,
        finding_kinds=(FindingKind.DESCRIPTIVE, FindingKind.CAUSAL),
    )

    mapping = response.to_mapping()
    reparsed = GenerationResponse.from_mapping(json.loads(json.dumps(mapping)))

    assert mapping["citations"] == [citation.to_mapping()]
    assert mapping["finding_kinds"] == ["descriptive", "causal"]
    assert mapping["abstention_reason"] is None
    assert reparsed == response


def test_abstaining_response_json_round_trip_uses_enum_value() -> None:
    response = make_abstaining_response()

    mapping = response.to_mapping()
    reparsed = GenerationResponse.from_mapping(json.loads(json.dumps(mapping)))

    assert mapping["citations"] == []
    assert mapping["finding_kinds"] == []
    assert mapping["abstention_reason"] == "insufficient_evidence"
    assert reparsed == response


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        (
            {"citations": ()},
            "non-abstaining response must contain at least one citation",
        ),
        (
            {"abstention_reason": AbstentionReason.INSUFFICIENT_EVIDENCE},
            "non-abstaining response must not contain an abstention_reason",
        ),
        (
            {
                "abstained": True,
                "abstention_reason": None,
                "citations": (),
                "finding_kinds": (),
            },
            "abstaining response must use abstention_reason 'insufficient_evidence'",
        ),
        (
            {
                "abstained": True,
                "abstention_reason": AbstentionReason.INSUFFICIENT_EVIDENCE,
            },
            "abstaining response must not contain citations",
        ),
        (
            {
                "abstained": True,
                "abstention_reason": AbstentionReason.INSUFFICIENT_EVIDENCE,
                "citations": (),
            },
            "abstaining response must not contain finding_kinds",
        ),
        (
            {"finding_kinds": (FindingKind.DESCRIPTIVE, FindingKind.DESCRIPTIVE)},
            "finding_kinds must not contain duplicates",
        ),
    ),
)
def test_generation_response_rejects_inconsistent_state(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "answer_text": "Answer.",
        "citations": (
            Citation(
                citation_id="e1",
                paper_id="paper-1",
                passage_id="paper-1:passage-1",
            ),
        ),
        "generation_method": "scripted-generator-v1",
        "abstained": False,
        "abstention_reason": None,
        "finding_kinds": (FindingKind.DESCRIPTIVE,),
    }
    values.update(overrides)

    with pytest.raises(GenerationResponseValidationError, match=message):
        GenerationResponse(**values)  # type: ignore[arg-type]


def test_generation_response_from_mapping_accepts_json_lists() -> None:
    response = GenerationResponse.from_mapping(
        {
            "answer_text": "Answer.",
            "citations": [
                {
                    "citation_id": "e1",
                    "paper_id": "paper-1",
                    "passage_id": "paper-1:passage-1",
                }
            ],
            "generation_method": "scripted-generator-v1",
            "abstained": False,
            "abstention_reason": None,
            "finding_kinds": ["descriptive"],
        }
    )

    assert isinstance(response.citations, tuple)
    assert response.finding_kinds == (FindingKind.DESCRIPTIVE,)


def test_generation_response_from_mapping_rejects_unknown_enum_values() -> None:
    data = make_abstaining_response().to_mapping()
    data["abstention_reason"] = "runtime_failure"

    with pytest.raises(
        GenerationResponseValidationError,
        match="abstention_reason must be one of: insufficient_evidence",
    ):
        GenerationResponse.from_mapping(data)


def test_validate_generation_response_accepts_canonical_citations() -> None:
    request = make_request(make_evidence(1), make_evidence(2))
    response = make_answered_response(
        Citation("e1", "paper-1", "paper-1:passage-1"),
        Citation("e2", "paper-1", "paper-1:passage-2"),
    )

    assert validate_generation_response(request, response) is response


@pytest.mark.parametrize(
    ("citations", "message"),
    (
        (
            (Citation("e3", "paper-1", "paper-1:passage-1"),),
            "[Uu]nknown citation_id 'e3'",
        ),
        (
            (Citation("e1", "wrong-paper", "paper-1:passage-1"),),
            "Citation 'e1' has paper_id 'wrong-paper'.*expected 'paper-1'",
        ),
        (
            (Citation("e1", "paper-1", "paper-1:passage-2"),),
            "Citation 'e1' has passage_id 'paper-1:passage-2'.*expected 'paper-1:passage-1'",
        ),
        (
            (
                Citation("e1", "paper-1", "paper-1:passage-1"),
                Citation("e1", "paper-1", "paper-1:passage-1"),
            ),
            "Duplicate citation_id 'e1'",
        ),
        (
            (
                Citation("e1", "paper-1", "paper-1:passage-1"),
                Citation("e2", "paper-1", "paper-1:passage-1"),
            ),
            "Duplicate citation reference.*paper-1:passage-1",
        ),
    ),
)
def test_validate_generation_response_rejects_invalid_citation_membership(
    citations: tuple[Citation, ...], message: str
) -> None:
    request = make_request(make_evidence(1), make_evidence(2))
    response = make_answered_response(*citations)

    with pytest.raises(GenerationResponseValidationError, match=message):
        validate_generation_response(request, response)


def test_validate_generation_response_rejects_out_of_rank_order() -> None:
    request = make_request(make_evidence(1), make_evidence(2))
    response = make_answered_response(
        Citation("e2", "paper-1", "paper-1:passage-2"),
        Citation("e1", "paper-1", "paper-1:passage-1"),
    )

    with pytest.raises(
        GenerationResponseValidationError,
        match="[Cc]itations must follow supplied-evidence rank order.*e1.*e2",
    ):
        validate_generation_response(request, response)


def test_validate_generation_response_requires_abstention_for_empty_evidence() -> None:
    request = make_request()
    response = make_answered_response(Citation("e1", "paper-1", "paper-1:passage-1"))

    with pytest.raises(
        GenerationResponseValidationError,
        match="empty evidence requires an abstaining response",
    ):
        validate_generation_response(request, response)


def test_validate_generation_response_allows_abstention_with_supplied_evidence() -> (
    None
):
    request = make_request(make_evidence(1))
    response = make_abstaining_response()

    assert validate_generation_response(request, response) is response


def test_scripted_generator_is_deterministic_offline_and_satisfies_protocol() -> None:
    request = make_request(make_evidence(1))
    generator = ScriptedGenerator()

    with (
        patch("socket.socket", side_effect=AssertionError("network access")),
        patch("builtins.open", side_effect=AssertionError("filesystem access")),
    ):
        first = validate_generation_response(request, generator.generate(request))
        second = validate_generation_response(request, generator.generate(request))

    assert isinstance(generator, Generator)
    assert first == second


def test_generation_contract_error_catches_request_and_response_failures() -> None:
    with pytest.raises(GenerationContractError):
        GenerationRequest(question="", evidence=())
    with pytest.raises(GenerationContractError):
        make_answered_response()
