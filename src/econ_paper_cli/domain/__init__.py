"""Domain types that are independent of infrastructure adapters."""

from econ_paper_cli.domain.artifacts import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactManifestError,
    RedistributionStatus,
)
from econ_paper_cli.domain.citations import Citation
from econ_paper_cli.domain.errors import (
    CitationValidationError,
    DomainError,
    EvidenceValidationError,
    PaperValidationError,
    PassageValidationError,
)
from econ_paper_cli.domain.evidence import RetrievalEvidence
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactManifestError",
    "Citation",
    "CitationValidationError",
    "DomainError",
    "EvidenceValidationError",
    "Paper",
    "PaperValidationError",
    "Passage",
    "PassageValidationError",
    "RedistributionStatus",
    "RetrievalEvidence",
]
