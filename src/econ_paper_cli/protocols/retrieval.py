"""Backend-independent retrieval protocol and contract validation."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from econ_paper_cli.domain.evidence import RetrievalEvidence


class RetrievalContractError(ValueError):
    """Base exception for all retrieval protocol contract failures."""


class RetrievalRequestValidationError(RetrievalContractError):
    """Raised when a RetrievalRequest query, top_k, or mapping is invalid."""


class RetrievalResultValidationError(RetrievalContractError):
    """Raised when returned retrieval results violate output contract rules."""


_REQUEST_FIELDS = frozenset({"query", "top_k"})


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """Immutable domain representation of a retrieval query request."""

    query: str
    top_k: int = 10

    def __post_init__(self) -> None:
        """Validate and normalize query and top_k on direct construction."""
        _validate_query(self.query)
        stripped = self.query.strip()
        object.__setattr__(self, "query", stripped)

        _validate_top_k(self.top_k)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "RetrievalRequest":
        """Construct and validate a RetrievalRequest from a mapping."""
        if not isinstance(data, Mapping):
            raise RetrievalRequestValidationError(
                "RetrievalRequest metadata must be a mapping."
            )
        if any(not isinstance(k, str) for k in data):
            raise RetrievalRequestValidationError(
                "RetrievalRequest mapping keys must be strings."
            )

        provided = set(data)
        missing_fields = sorted(_REQUEST_FIELDS - provided)
        if missing_fields:
            raise RetrievalRequestValidationError(
                "RetrievalRequest mapping is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided - _REQUEST_FIELDS)
        if unknown_fields:
            raise RetrievalRequestValidationError(
                "RetrievalRequest mapping contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        raw_query = data["query"]
        _validate_query(raw_query)

        raw_top_k = data["top_k"]
        _validate_top_k(raw_top_k)

        return cls(query=cast(str, raw_query), top_k=cast(int, raw_top_k))

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible mapping representation."""
        return {"query": self.query, "top_k": self.top_k}


@runtime_checkable
class Retriever(Protocol):
    """Protocol defining the backend-independent retrieval interface.

    Implementations are configured over a corpus or index during construction.

    Note on @runtime_checkable: Runtime protocol checking verifies structural
    presence of the required `retrieve` attribute but does not validate the full
    method signature, argument types, return type, or retrieval semantics.
    Application correctness must not rely on `isinstance(obj, Retriever)`.
    """

    def retrieve(
        self,
        request: RetrievalRequest,
    ) -> tuple[RetrievalEvidence, ...]:
        """Retrieve ranked evidence passages matching the request query.

        Args:
            request: Validated retrieval request query.

        Returns:
            Tuple of RetrievalEvidence instances conforming to result invariants.
        """
        ...


def validate_retrieval_results(
    request: RetrievalRequest,
    results: object,
) -> tuple[RetrievalEvidence, ...]:
    """Validate and enforce protocol contract rules on returned retrieval results.

    Enforces that:
    - results is a tuple;
    - every item is a RetrievalEvidence instance;
    - empty tuple is valid when no passage matches;
    - len(results) <= request.top_k;
    - ranks are contiguous integers starting at 1 (1-indexed);
    - passage_id values are unique (exact duplicate rejection);
    - results are ordered by non-increasing score;
    - equal scores use passage_id ascending as deterministic final tie-breaker;
    - retrieval_method is a non-empty string for every returned result;
    - all results share the exact same retrieval_method label.

    Returns:
        The exact validated results tuple without mutation.
    """
    if not isinstance(results, tuple):
        raise RetrievalResultValidationError("retrieval results must be a tuple.")

    if not results:
        return results

    if len(results) > request.top_k:
        raise RetrievalResultValidationError(
            f"Returned result count ({len(results)}) exceeds request.top_k ({request.top_k})."
        )

    seen_passage_ids: set[str] = set()
    first_method: str | None = None

    for i, item in enumerate(results):
        if not isinstance(item, RetrievalEvidence):
            raise RetrievalResultValidationError(
                f"Result at position {i} is not a RetrievalEvidence instance."
            )

        if item.rank != i + 1:
            raise RetrievalResultValidationError(
                f"Result at position {i} has rank {item.rank}, expected contiguous rank {i + 1}."
            )

        if item.passage.passage_id in seen_passage_ids:
            raise RetrievalResultValidationError(
                f"Duplicate passage_id '{item.passage.passage_id}' found at position {i}."
            )
        seen_passage_ids.add(item.passage.passage_id)

        if item.retrieval_method is None or not item.retrieval_method.strip():
            raise RetrievalResultValidationError(
                f"Result at position {i} must have a non-empty retrieval_method string."
            )

        if first_method is None:
            first_method = item.retrieval_method
        elif item.retrieval_method != first_method:
            raise RetrievalResultValidationError(
                f"Inconsistent retrieval_method '{item.retrieval_method}' at position {i}; "
                f"expected uniform '{first_method}'."
            )

        if i > 0:
            prev = cast(RetrievalEvidence, results[i - 1])
            if item.score > prev.score:
                raise RetrievalResultValidationError(
                    f"Results not ordered by non-increasing score at position {i}: "
                    f"{prev.score} < {item.score}."
                )
            if item.score == prev.score:
                if item.passage.passage_id <= prev.passage.passage_id:
                    raise RetrievalResultValidationError(
                        f"Equal scores ({item.score}) at positions {i - 1} and {i} must be ordered "
                        f"by ascending passage_id: '{prev.passage.passage_id}' <= '{item.passage.passage_id}'."
                    )

    return results


def _validate_query(value: object) -> None:
    if not isinstance(value, str):
        raise RetrievalRequestValidationError("query must be a string.")
    if not value.strip():
        raise RetrievalRequestValidationError("query must be a non-empty string.")


def _validate_top_k(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RetrievalRequestValidationError(
            "top_k must be a positive integer (>= 1)."
        )
