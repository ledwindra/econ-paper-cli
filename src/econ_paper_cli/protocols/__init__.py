"""Replaceable domain protocols and contracts for econ-paper-cli."""

from econ_paper_cli.protocols.retrieval import (
    RetrievalContractError,
    RetrievalRequest,
    RetrievalRequestValidationError,
    RetrievalResultValidationError,
    Retriever,
    validate_retrieval_results,
)

__all__ = [
    "RetrievalContractError",
    "RetrievalRequest",
    "RetrievalRequestValidationError",
    "RetrievalResultValidationError",
    "Retriever",
    "validate_retrieval_results",
]
