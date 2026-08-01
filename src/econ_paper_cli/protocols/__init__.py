"""Replaceable domain protocols and contracts for econ-paper-cli."""

from econ_paper_cli.protocols.generation import (
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
from econ_paper_cli.protocols.retrieval import (
    RetrievalContractError,
    RetrievalRequest,
    RetrievalRequestValidationError,
    RetrievalResultValidationError,
    Retriever,
    validate_retrieval_results,
)
from econ_paper_cli.protocols.storage import (
    ChecksumConflictError,
    StorageBackend,
    StorageConnectionError,
    StorageError,
    StorageIncompatibleSchemaError,
    StorageMigrationError,
    StorageTransactionError,
    StorageValidationError,
)

__all__ = [
    "AbstentionReason",
    "ChecksumConflictError",
    "FindingKind",
    "GenerationContractError",
    "GenerationRequest",
    "GenerationRequestValidationError",
    "GenerationResponse",
    "GenerationResponseValidationError",
    "Generator",
    "RetrievalContractError",
    "RetrievalRequest",
    "RetrievalRequestValidationError",
    "RetrievalResultValidationError",
    "Retriever",
    "StorageBackend",
    "StorageConnectionError",
    "StorageError",
    "StorageIncompatibleSchemaError",
    "StorageMigrationError",
    "StorageTransactionError",
    "StorageValidationError",
    "validate_generation_response",
    "validate_retrieval_results",
]
