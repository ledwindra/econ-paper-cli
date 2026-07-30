"""Domain error types for paper, passage, evidence, and citation validation."""


class DomainError(ValueError):
    """Base exception for all domain validation errors."""


class PaperValidationError(DomainError):
    """Raised when a Paper domain object fails validation."""


class PassageValidationError(DomainError):
    """Raised when a Passage domain object fails validation."""


class EvidenceValidationError(DomainError):
    """Raised when a RetrievalEvidence domain object fails validation."""


class CitationValidationError(DomainError):
    """Raised when a Citation domain object fails validation."""
