"""Backend-independent generation protocol and grounded-output validation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast, runtime_checkable

from econ_paper_cli.domain import (
    Citation,
    CitationValidationError,
    DomainError,
    RetrievalEvidence,
)
from econ_paper_cli.protocols.retrieval import (
    RetrievalRequest,
    RetrievalResultValidationError,
    validate_retrieval_results,
)


class GenerationContractError(ValueError):
    """Base exception for generation protocol contract failures."""


class GenerationRequestValidationError(GenerationContractError):
    """Raised when a generation request is malformed or internally inconsistent."""


class GenerationResponseValidationError(GenerationContractError):
    """Raised when a generation response violates grounded-output rules."""


class FindingKind(str, Enum):
    """Backend-declared characterization of findings discussed in an answer."""

    DESCRIPTIVE = "descriptive"
    CAUSAL = "causal"


class AbstentionReason(str, Enum):
    """A structured reason why generation produced an explicit abstention."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


_REQUEST_FIELDS = frozenset({"question", "evidence"})
_RESPONSE_FIELDS = frozenset(
    {
        "answer_text",
        "citations",
        "generation_method",
        "abstained",
        "abstention_reason",
        "finding_kinds",
    }
)


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Immutable question and retrieved evidence supplied to a generator."""

    question: str
    evidence: tuple[RetrievalEvidence, ...]

    def __post_init__(self) -> None:
        """Validate direct construction and normalize outer question whitespace."""
        _validate_nonempty_text(
            "question", self.question, GenerationRequestValidationError
        )
        object.__setattr__(self, "question", self.question.strip())
        _validate_evidence(self.question, self.evidence)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "GenerationRequest":
        """Parse a JSON-compatible mapping and normalize evidence to a tuple."""
        _validate_mapping_shape(
            "GenerationRequest",
            data,
            _REQUEST_FIELDS,
            GenerationRequestValidationError,
        )

        raw_evidence = data["evidence"]
        if not isinstance(raw_evidence, (list, tuple)):
            raise GenerationRequestValidationError(
                "evidence must be a JSON array (list or tuple)."
            )

        evidence: list[RetrievalEvidence] = []
        for position, item in enumerate(cast(Sequence[object], raw_evidence)):
            if isinstance(item, RetrievalEvidence):
                evidence.append(item)
                continue
            if isinstance(item, Mapping):
                try:
                    evidence.append(
                        RetrievalEvidence.from_mapping(cast(Mapping[str, object], item))
                    )
                except DomainError as error:
                    raise GenerationRequestValidationError(
                        f"evidence item at position {position} is invalid: {error}"
                    ) from error
                continue
            raise GenerationRequestValidationError(
                f"evidence item at position {position} must be a "
                "RetrievalEvidence object or mapping."
            )

        return cls(
            question=cast(str, data["question"]),
            evidence=tuple(evidence),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible request mapping."""
        return {
            "question": self.question,
            "evidence": [item.to_mapping() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class GenerationResponse:
    """Immutable generated answer, structured citations, and abstention state."""

    answer_text: str
    citations: tuple[Citation, ...]
    generation_method: str
    abstained: bool
    abstention_reason: AbstentionReason | None
    finding_kinds: tuple[FindingKind, ...]

    def __post_init__(self) -> None:
        """Validate direct construction and local response invariants."""
        _validate_nonempty_text(
            "answer_text", self.answer_text, GenerationResponseValidationError
        )
        _validate_citations(self.citations)
        _validate_nonempty_text(
            "generation_method",
            self.generation_method,
            GenerationResponseValidationError,
        )
        if not isinstance(self.abstained, bool):
            raise GenerationResponseValidationError("abstained must be a boolean.")
        if self.abstention_reason is not None and not isinstance(
            self.abstention_reason, AbstentionReason
        ):
            raise GenerationResponseValidationError(
                "abstention_reason must be an AbstentionReason or None."
            )
        _validate_finding_kinds(self.finding_kinds)
        _validate_abstention_state(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "GenerationResponse":
        """Parse JSON-compatible arrays and enum strings into immutable fields."""
        _validate_mapping_shape(
            "GenerationResponse",
            data,
            _RESPONSE_FIELDS,
            GenerationResponseValidationError,
        )

        raw_citations = data["citations"]
        if not isinstance(raw_citations, (list, tuple)):
            raise GenerationResponseValidationError(
                "citations must be a JSON array (list or tuple)."
            )
        citations: list[Citation] = []
        for position, item in enumerate(cast(Sequence[object], raw_citations)):
            if isinstance(item, Citation):
                citations.append(item)
                continue
            if isinstance(item, Mapping):
                try:
                    citations.append(
                        Citation.from_mapping(cast(Mapping[str, object], item))
                    )
                except CitationValidationError as error:
                    raise GenerationResponseValidationError(
                        f"citation at position {position} is invalid: {error}"
                    ) from error
                continue
            raise GenerationResponseValidationError(
                f"citation at position {position} must be a Citation object or mapping."
            )

        raw_finding_kinds = data["finding_kinds"]
        if not isinstance(raw_finding_kinds, (list, tuple)):
            raise GenerationResponseValidationError(
                "finding_kinds must be a JSON array (list or tuple)."
            )
        finding_kinds = tuple(
            _parse_enum(
                "finding_kinds item",
                item,
                FindingKind,
                GenerationResponseValidationError,
            )
            for item in cast(Sequence[object], raw_finding_kinds)
        )

        raw_reason = data["abstention_reason"]
        abstention_reason = (
            None
            if raw_reason is None
            else _parse_enum(
                "abstention_reason",
                raw_reason,
                AbstentionReason,
                GenerationResponseValidationError,
            )
        )

        return cls(
            answer_text=cast(str, data["answer_text"]),
            citations=tuple(citations),
            generation_method=cast(str, data["generation_method"]),
            abstained=cast(bool, data["abstained"]),
            abstention_reason=cast(AbstentionReason | None, abstention_reason),
            finding_kinds=cast(tuple[FindingKind, ...], finding_kinds),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible response mapping."""
        return {
            "answer_text": self.answer_text,
            "citations": [citation.to_mapping() for citation in self.citations],
            "generation_method": self.generation_method,
            "abstained": self.abstained,
            "abstention_reason": (
                None if self.abstention_reason is None else self.abstention_reason.value
            ),
            "finding_kinds": [kind.value for kind in self.finding_kinds],
        }


@runtime_checkable
class Generator(Protocol):
    """Replaceable backend-independent generation interface."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate a structured response from only the supplied request evidence."""
        ...


def validate_generation_response(
    request: GenerationRequest,
    response: object,
) -> GenerationResponse:
    """Validate citation membership, order, and cross-request abstention rules."""
    if not isinstance(request, GenerationRequest):
        raise GenerationRequestValidationError(
            "request must be a GenerationRequest instance."
        )
    if not isinstance(response, GenerationResponse):
        raise GenerationResponseValidationError(
            "generation response must be a GenerationResponse instance."
        )

    if not request.evidence:
        if not response.abstained:
            raise GenerationResponseValidationError(
                "GenerationRequest with empty evidence requires an abstaining response."
            )
        return response
    if response.abstained:
        return response

    allowed_by_id = {f"e{evidence.rank}": evidence for evidence in request.evidence}
    seen_ids: set[str] = set()
    seen_references: set[tuple[str, str]] = set()
    previous_rank: int | None = None
    previous_id: str | None = None

    for position, citation in enumerate(response.citations):
        if citation.citation_id in seen_ids:
            raise GenerationResponseValidationError(
                f"Duplicate citation_id '{citation.citation_id}' at position {position}."
            )
        seen_ids.add(citation.citation_id)

        reference = (citation.paper_id, citation.passage_id)
        if reference in seen_references:
            raise GenerationResponseValidationError(
                "Duplicate citation reference "
                f"'{citation.paper_id}/{citation.passage_id}' at position {position}."
            )
        seen_references.add(reference)

        evidence = allowed_by_id.get(citation.citation_id)
        if evidence is None:
            allowed = ", ".join(allowed_by_id)
            raise GenerationResponseValidationError(
                f"Unknown citation_id '{citation.citation_id}'; expected one of: {allowed}."
            )
        if citation.paper_id != evidence.passage.paper_id:
            raise GenerationResponseValidationError(
                f"Citation '{citation.citation_id}' has paper_id "
                f"'{citation.paper_id}', expected '{evidence.passage.paper_id}'."
            )
        if citation.passage_id != evidence.passage.passage_id:
            raise GenerationResponseValidationError(
                f"Citation '{citation.citation_id}' has passage_id "
                f"'{citation.passage_id}', expected "
                f"'{evidence.passage.passage_id}'."
            )
        if previous_rank is not None and evidence.rank <= previous_rank:
            raise GenerationResponseValidationError(
                "Response citations must follow supplied-evidence rank order; "
                f"citation '{citation.citation_id}' follows '{previous_id}'."
            )
        previous_rank = evidence.rank
        previous_id = citation.citation_id

    return response


def _validate_evidence(question: str, evidence: object) -> None:
    if not isinstance(evidence, tuple):
        raise GenerationRequestValidationError("evidence must be a tuple.")
    if not evidence:
        return
    if any(not isinstance(item, RetrievalEvidence) for item in evidence):
        raise GenerationRequestValidationError(
            "evidence must contain only RetrievalEvidence instances."
        )
    try:
        validate_retrieval_results(
            RetrievalRequest(query=question, top_k=len(evidence)),
            evidence,
        )
    except RetrievalResultValidationError as error:
        raise GenerationRequestValidationError(
            f"evidence violates the retrieval result contract: {error}"
        ) from error


def _validate_citations(citations: object) -> None:
    if not isinstance(citations, tuple):
        raise GenerationResponseValidationError("citations must be a tuple.")
    if any(not isinstance(item, Citation) for item in citations):
        raise GenerationResponseValidationError(
            "citations must contain only Citation instances."
        )


def _validate_finding_kinds(finding_kinds: object) -> None:
    if not isinstance(finding_kinds, tuple):
        raise GenerationResponseValidationError("finding_kinds must be a tuple.")
    seen: set[FindingKind] = set()
    for item in finding_kinds:
        if not isinstance(item, FindingKind):
            raise GenerationResponseValidationError(
                "finding_kinds must contain only FindingKind values."
            )
        if item in seen:
            raise GenerationResponseValidationError(
                "finding_kinds must not contain duplicates."
            )
        seen.add(item)


def _validate_abstention_state(response: GenerationResponse) -> None:
    if response.abstained:
        if response.abstention_reason is not AbstentionReason.INSUFFICIENT_EVIDENCE:
            raise GenerationResponseValidationError(
                "abstaining response must use abstention_reason "
                "'insufficient_evidence'."
            )
        if response.citations:
            raise GenerationResponseValidationError(
                "abstaining response must not contain citations."
            )
        if response.finding_kinds:
            raise GenerationResponseValidationError(
                "abstaining response must not contain finding_kinds."
            )
        return

    if response.abstention_reason is not None:
        raise GenerationResponseValidationError(
            "non-abstaining response must not contain an abstention_reason."
        )
    if not response.citations:
        raise GenerationResponseValidationError(
            "non-abstaining response must contain at least one citation."
        )


def _validate_mapping_shape(
    label: str,
    data: object,
    required_fields: frozenset[str],
    error_type: type[GenerationContractError],
) -> None:
    if not isinstance(data, Mapping):
        raise error_type(f"{label} metadata must be a mapping.")
    if any(not isinstance(field, str) for field in data):
        raise error_type(f"{label} field names must be strings.")
    provided = set(data)
    missing = sorted(required_fields - provided)
    if missing:
        raise error_type(
            f"{label} mapping is missing required fields: {', '.join(missing)}."
        )
    unknown = sorted(provided - required_fields)
    if unknown:
        raise error_type(
            f"{label} mapping contains unknown fields: {', '.join(unknown)}."
        )


def _validate_nonempty_text(
    field: str,
    value: object,
    error_type: type[GenerationContractError],
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field} must be a non-empty string.")


def _parse_enum(
    field: str,
    value: object,
    enum_type: type[FindingKind] | type[AbstentionReason],
    error_type: type[GenerationContractError],
) -> FindingKind | AbstentionReason:
    if not isinstance(value, str):
        allowed = ", ".join(item.value for item in enum_type)
        raise error_type(f"{field} must be one of: {allowed}.")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise error_type(f"{field} must be one of: {allowed}.") from error
