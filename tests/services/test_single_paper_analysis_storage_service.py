"""Service integration tests for single-paper analysis storage service."""

import json
from pathlib import Path

from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFQualityStatus,
    ResearchQuestionKind,
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
)
from econ_paper_cli.protocols.generation import (
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.protocols.pdf_extraction import (
    PDFExtractionError,
    PDFExtractor,
    PDFMalformedError,
)
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper
from econ_paper_cli.services.single_paper_analysis_storage import (
    delete_single_paper_analysis_record,
    get_single_paper_analysis_record,
    get_single_paper_analysis_record_by_checksum,
    list_single_paper_analysis_records,
    save_single_paper_analysis_result,
)


class FakePDFExtractor(PDFExtractor):
    """Fake PDF extractor for deterministic storage service tests."""

    def __init__(
        self,
        pages_text: list[str] | None = None,
        raise_error: PDFExtractionError | None = None,
    ) -> None:
        self.pages_text = (
            pages_text
            if pages_text is not None
            else [
                "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
            ]
        )
        self.raise_error = raise_error

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
        if self.raise_error is not None:
            raise self.raise_error

        pages = tuple(
            ExtractedPDFPage(page_number=i + 1, text=txt)
            for i, txt in enumerate(self.pages_text)
        )
        meta = PDFDocumentMetadata(title="Test Storage Paper")
        return PDFExtractionResult(
            source_path=pdf_path.resolve(),
            pages=pages,
            page_count=len(pages),
            metadata=meta,
            extraction_method="fake_extractor",
            parser_version="1.0.0",
        )


class FakeGenerator(Generator):
    """Fake model generator for deterministic storage service tests."""

    def __init__(self, response_text: str | None = None) -> None:
        self.response_text = response_text

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        from econ_paper_cli.domain.citations import Citation

        citations = tuple(
            Citation(
                citation_id=f"e{ev.rank}",
                paper_id=ev.passage.paper_id,
                passage_id=ev.passage.passage_id,
            )
            for ev in request.evidence
        )
        return GenerationResponse(
            answer_text=self.response_text or "",
            citations=citations,
            generation_method="fake_generator",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )


def _create_valid_pdf_file(tmp_path: Path, filename: str = "paper.pdf") -> Path:
    path = tmp_path / filename
    path.write_bytes(b"%PDF-1.4 synthetic content for storage service test")
    return path


def _make_success_response_json(abs_text: str, exc: str) -> str:
    start_off = abs_text.find(exc)
    return json.dumps(
        {
            "research_question": "What is the impact of trade policy?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": exc,
                    "page_number": 1,
                    "start_character_offset": start_off,
                    "end_character_offset": start_off + len(exc),
                }
            ],
        }
    )


def test_end_to_end_analyze_save_and_retrieve_success(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    analysis_res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )
    assert analysis_res.status is SinglePaperAnalysisStatus.SUCCESS

    # Save to storage
    record = save_single_paper_analysis_result(
        storage, analysis_res, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )
    assert record.status is SinglePaperAnalysisStatus.SUCCESS

    # Retrieve from storage by analysis_id
    retrieved = get_single_paper_analysis_record(storage, record.analysis_id)
    assert retrieved is not None
    assert retrieved == record
    assert retrieved.content_checksum == analysis_res.checksum

    # Retrieve from storage by checksum
    retrieved_by_ck = get_single_paper_analysis_record_by_checksum(
        storage, analysis_res.checksum
    )
    assert retrieved_by_ck is not None
    assert retrieved_by_ck.analysis_id == record.analysis_id

    # List all records
    all_recs = list_single_paper_analysis_records(storage)
    assert len(all_recs) == 1
    assert all_recs[0].analysis_id == record.analysis_id

    # Delete record
    deleted = delete_single_paper_analysis_record(storage, record.analysis_id)
    assert deleted is True
    assert get_single_paper_analysis_record(storage, record.analysis_id) is None

    storage.close()


def test_end_to_end_analyze_save_and_retrieve_preflight_failed(tmp_path: Path) -> None:
    nonexistent = tmp_path / "nonexistent.pdf"
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    analysis_res = analyze_single_paper(
        nonexistent,
        FakePDFExtractor(),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert analysis_res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED

    record = save_single_paper_analysis_result(storage, analysis_res)
    assert record.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert record.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert record.failure_code is SinglePaperAnalysisFailureCode.PATH_NOT_FOUND

    retrieved = get_single_paper_analysis_record(storage, record.analysis_id)
    assert retrieved is not None
    assert retrieved == record
    assert retrieved.failure_code is SinglePaperAnalysisFailureCode.PATH_NOT_FOUND

    storage.close()


def test_end_to_end_analyze_save_and_retrieve_extraction_failed(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    cause = PDFMalformedError(pdf_path, ValueError("truncated xref"))
    extractor = FakePDFExtractor(raise_error=cause)

    analysis_res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert analysis_res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED

    record = save_single_paper_analysis_result(storage, analysis_res)
    assert record.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert record.failure_code is SinglePaperAnalysisFailureCode.PDF_MALFORMED

    retrieved = get_single_paper_analysis_record(storage, record.analysis_id)
    assert retrieved is not None
    assert retrieved.failure_code is SinglePaperAnalysisFailureCode.PDF_MALFORMED

    storage.close()


def test_end_to_end_analyze_save_and_retrieve_quality_halted(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    extractor = FakePDFExtractor(pages_text=[""])  # empty page -> LIKELY_NEEDS_OCR

    analysis_res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert analysis_res.status is SinglePaperAnalysisStatus.QUALITY_HALTED

    record = save_single_paper_analysis_result(storage, analysis_res)
    assert record.status is SinglePaperAnalysisStatus.QUALITY_HALTED
    assert record.quality_status is PDFQualityStatus.LIKELY_NEEDS_OCR
    assert len(record.sections) == 0
    assert record.research_question is None

    retrieved = get_single_paper_analysis_record(storage, record.analysis_id)
    assert retrieved is not None
    assert retrieved == record

    storage.close()


def test_end_to_end_analyze_save_and_retrieve_question_extraction_halted(
    tmp_path: Path,
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    # Text without Abstract/Intro -> NO_USABLE_SECTIONS -> QUESTION_EXTRACTION_HALTED
    page_text = "3. Methodology and Data\nWe describe the regression model here."
    extractor = FakePDFExtractor(pages_text=[page_text])

    analysis_res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert analysis_res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED

    record = save_single_paper_analysis_result(storage, analysis_res)
    assert record.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert record.research_question is not None
    assert record.research_question.kind is ResearchQuestionKind.UNAVAILABLE

    retrieved = get_single_paper_analysis_record(storage, record.analysis_id)
    assert retrieved is not None
    assert retrieved == record
    assert retrieved.research_question.kind is ResearchQuestionKind.UNAVAILABLE

    storage.close()
