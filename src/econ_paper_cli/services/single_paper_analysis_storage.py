"""Application service for atomic single-paper analysis persistence and retrieval."""

from econ_paper_cli.domain.single_paper_analysis import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
)
from econ_paper_cli.protocols.storage import StorageBackend


def save_single_paper_analysis_result(
    storage: StorageBackend,
    result: SinglePaperAnalysisResult,
    settings: SinglePaperAnalysisSettings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
) -> SinglePaperAnalysisRecord:
    """Convert and atomically persist a SinglePaperAnalysisResult to storage.

    Returns the resulting immutable SinglePaperAnalysisRecord.
    """
    record = SinglePaperAnalysisRecord.from_result(result, settings=settings)
    storage.save_single_paper_analysis(record)
    return record


def get_single_paper_analysis_record(
    storage: StorageBackend,
    analysis_id: str,
) -> SinglePaperAnalysisRecord | None:
    """Retrieve a stored SinglePaperAnalysisRecord by analysis_id."""
    return storage.get_single_paper_analysis(analysis_id)


def get_single_paper_analysis_record_by_checksum(
    storage: StorageBackend,
    checksum: str,
    settings_fingerprint: str | None = None,
) -> SinglePaperAnalysisRecord | None:
    """Retrieve a stored SinglePaperAnalysisRecord by content checksum."""
    return storage.get_single_paper_analysis_by_checksum(
        checksum, settings_fingerprint=settings_fingerprint
    )


def list_single_paper_analysis_records(
    storage: StorageBackend,
) -> tuple[SinglePaperAnalysisRecord, ...]:
    """Retrieve all stored SinglePaperAnalysisRecords."""
    return storage.list_single_paper_analyses()


def delete_single_paper_analysis_record(
    storage: StorageBackend,
    analysis_id: str,
) -> bool:
    """Delete a stored SinglePaperAnalysisRecord by analysis_id."""
    return storage.delete_single_paper_analysis(analysis_id)
