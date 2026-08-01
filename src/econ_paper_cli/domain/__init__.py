"""Domain types that are independent of infrastructure adapters."""

from econ_paper_cli.domain.artifacts import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactManifestError,
    RedistributionStatus,
)
from econ_paper_cli.domain.citations import Citation
from econ_paper_cli.domain.corpora import Corpus, CorpusValidationError
from econ_paper_cli.domain.errors import (
    CitationValidationError,
    DomainError,
    EvidenceValidationError,
    IngestionEmptyDirectoryError,
    IngestionError,
    IngestionInvalidPathError,
    IngestionPathNotFoundError,
    IngestionPermissionError,
    IngestionReadError,
    IngestionUnsupportedFileError,
    IngestionValidationError,
    PaperValidationError,
    PassageValidationError,
    StorageRecordValidationError,
)
from econ_paper_cli.domain.evidence import RetrievalEvidence
from econ_paper_cli.domain.ingestion import (
    IngestionPreflightResult,
    PreflightCandidate,
)
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.storage import (
    ConversionSettings,
    IngestionCompletion,
    IngestionWarning,
    PaperRecord,
    SourceProvenance,
)

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactManifestError",
    "Citation",
    "CitationValidationError",
    "ConversionSettings",
    "Corpus",
    "CorpusValidationError",
    "DomainError",
    "EvidenceValidationError",
    "IngestionCompletion",
    "IngestionEmptyDirectoryError",
    "IngestionError",
    "IngestionInvalidPathError",
    "IngestionPathNotFoundError",
    "IngestionPermissionError",
    "IngestionPreflightResult",
    "IngestionReadError",
    "IngestionUnsupportedFileError",
    "IngestionValidationError",
    "IngestionWarning",
    "Paper",
    "PaperRecord",
    "PaperValidationError",
    "Passage",
    "PassageValidationError",
    "PreflightCandidate",
    "RedistributionStatus",
    "RetrievalEvidence",
    "SourceProvenance",
    "StorageRecordValidationError",
]
