"""Unit tests for storage domain contracts and records."""

import pytest

from econ_paper_cli.domain import Paper, Passage
from econ_paper_cli.domain.errors import StorageRecordValidationError
from econ_paper_cli.domain.storage import (
    ConversionSettings,
    IngestionCompletion,
    IngestionWarning,
    PaperRecord,
    SourceProvenance,
)

VALID_CHECKSUM = "a" * 64
VALID_PAPER_ID = "paper.2024.v1"
VALID_PASSAGE_ID = "paper.2024.v1:p1"


@pytest.fixture
def sample_paper() -> Paper:
    return Paper(
        paper_id=VALID_PAPER_ID,
        title="Economic Analysis of Local Libraries",
        authors=("Alice Smith", "Bob Jones"),
        year=2024,
        abstract="This paper analyzes local library economies.",
        source_name="NBER",
        source_identifier="w12345",
        source_url="https://example.org/w12345.pdf",
    )


@pytest.fixture
def sample_passage() -> Passage:
    return Passage(
        passage_id=VALID_PASSAGE_ID,
        paper_id=VALID_PAPER_ID,
        text="Local libraries provide public goods with high returns.",
        section_heading="1. Introduction",
        page_start=1,
        page_end=2,
        ordinal_position=0,
    )


@pytest.fixture
def sample_provenance() -> SourceProvenance:
    return SourceProvenance(
        source_path="/papers/2024/w12345.pdf",
        source_format="pdf",
        content_checksum=VALID_CHECKSUM,
        extraction_method="pdfplumber-v1",
        created_at="2026-07-31T20:00:00Z",
    )


@pytest.fixture
def sample_conversion() -> ConversionSettings:
    return ConversionSettings(
        conversion_version="1.0.0",
        ocr_enabled=False,
        parameters={"max_passage_tokens": 512, "overlap": 50},
    )


@pytest.fixture
def sample_warning() -> IngestionWarning:
    return IngestionWarning(
        warning_code="LOW_RESOLUTION_PAGE",
        message="Page 3 has low resolution image.",
        created_at="2026-07-31T20:00:00Z",
    )


@pytest.fixture
def sample_completion() -> IngestionCompletion:
    return IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T20:01:00Z",
        passage_count=1,
        warning_count=1,
        error_message=None,
    )


@pytest.fixture
def sample_paper_record(
    sample_paper: Paper,
    sample_passage: Passage,
    sample_provenance: SourceProvenance,
    sample_conversion: ConversionSettings,
    sample_warning: IngestionWarning,
    sample_completion: IngestionCompletion,
) -> PaperRecord:
    return PaperRecord(
        paper=sample_paper,
        passages=(sample_passage,),
        source_provenance=sample_provenance,
        conversion_settings=sample_conversion,
        warnings=(sample_warning,),
        completion=sample_completion,
    )


def test_source_provenance_validation() -> None:
    prov = SourceProvenance(
        source_path="/path/doc.pdf",
        source_format="pdf",
        content_checksum="b" * 64,
        extraction_method="test",
        created_at="2026-07-31T20:00:00Z",
    )
    assert prov.content_checksum == "b" * 64

    with pytest.raises(StorageRecordValidationError, match="content_checksum"):
        SourceProvenance(
            source_path="/path/doc.pdf",
            source_format="pdf",
            content_checksum="invalid_hex",
            extraction_method="test",
            created_at="2026-07-31T20:00:00Z",
        )


def test_conversion_settings_validation() -> None:
    sett = ConversionSettings(
        conversion_version="2.0", ocr_enabled=True, parameters={"foo": "bar"}
    )
    assert sett.ocr_enabled is True
    assert sett.parameters == {"foo": "bar"}

    with pytest.raises(StorageRecordValidationError, match="ocr_enabled"):
        ConversionSettings(
            conversion_version="2.0",
            ocr_enabled="yes",  # type: ignore[arg-type]
            parameters={},
        )


def test_ingestion_completion_validation() -> None:
    comp = IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T20:00:00Z",
        passage_count=0,
        warning_count=0,
    )
    assert comp.status == "completed"

    with pytest.raises(StorageRecordValidationError, match="status"):
        IngestionCompletion(
            status="unknown_status",
            completed_at="2026-07-31T20:00:00Z",
            passage_count=0,
            warning_count=0,
        )

    with pytest.raises(StorageRecordValidationError, match="passage_count"):
        IngestionCompletion(
            status="completed",
            completed_at="2026-07-31T20:00:00Z",
            passage_count=-1,
            warning_count=0,
        )


def test_paper_record_integrity_validation(
    sample_paper: Paper,
    sample_passage: Passage,
    sample_provenance: SourceProvenance,
    sample_conversion: ConversionSettings,
    sample_warning: IngestionWarning,
    sample_completion: IngestionCompletion,
) -> None:
    # Valid construction
    rec = PaperRecord(
        paper=sample_paper,
        passages=(sample_passage,),
        source_provenance=sample_provenance,
        conversion_settings=sample_conversion,
        warnings=(sample_warning,),
        completion=sample_completion,
    )
    assert rec.paper.paper_id == VALID_PAPER_ID

    # Passage paper_id mismatch
    mismatched_passage = Passage(
        passage_id="other.paper:p1",
        paper_id="other.paper",
        text="Text",
        section_heading=None,
        page_start=1,
        page_end=1,
        ordinal_position=0,
    )
    with pytest.raises(StorageRecordValidationError, match="does not match paper"):
        PaperRecord(
            paper=sample_paper,
            passages=(mismatched_passage,),
            source_provenance=sample_provenance,
            conversion_settings=sample_conversion,
            warnings=(sample_warning,),
            completion=sample_completion,
        )

    # Passage count mismatch
    wrong_comp = IngestionCompletion(
        status="completed",
        completed_at="2026-07-31T20:01:00Z",
        passage_count=99,
        warning_count=1,
    )
    with pytest.raises(StorageRecordValidationError, match="passage_count"):
        PaperRecord(
            paper=sample_paper,
            passages=(sample_passage,),
            source_provenance=sample_provenance,
            conversion_settings=sample_conversion,
            warnings=(sample_warning,),
            completion=wrong_comp,
        )


def test_paper_record_mapping_round_trip(sample_paper_record: PaperRecord) -> None:
    mapping = sample_paper_record.to_mapping()
    assert isinstance(mapping, dict)
    reconstructed = PaperRecord.from_mapping(mapping)
    assert reconstructed == sample_paper_record
