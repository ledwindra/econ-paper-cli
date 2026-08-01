"""Service integration tests for single-paper analysis storage service."""

import json
from pathlib import Path

from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    PDFDocumentMetadata,
    PDFExtractionResult,
    SinglePaperAnalysisStatus,
)
from econ_paper_cli.domain.pdf_extraction import ExtractedPDFPage
from econ_paper_cli.protocols.generation import (
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.protocols.pdf_extraction import (
    PDFExtractionError,
    PDFExtractor,
)
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper
from econ_paper_cli.services.single_paper_analysis_storage import (
    get_single_paper_analysis_record,
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
                "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices on page 1.",
                "1. Introduction (continued)\nTrade policy affects prices on page 2 as well.",
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

    def __init__(
        self,
        response_text: str | None = None,
        abstained: bool = False,
        raise_error: Exception | None = None,
    ) -> None:
        self.response_text = response_text
        self.abstained = abstained
        self.raise_error = raise_error

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if self.raise_error is not None:
            raise self.raise_error
        from econ_paper_cli.protocols.generation import AbstentionReason, Citation

        citations = (
            ()
            if self.abstained
            else tuple(
                Citation(
                    citation_id=f"e{ev.rank}",
                    paper_id=ev.passage.paper_id,
                    passage_id=ev.passage.passage_id,
                )
                for ev in request.evidence
            )
        )
        return GenerationResponse(
            answer_text=self.response_text or "The model abstained.",
            citations=citations,
            generation_method="fake_generator",
            abstained=self.abstained,
            abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE
            if self.abstained
            else None,
            finding_kinds=() if self.abstained else (FindingKind.DESCRIPTIVE,),
        )


def _create_valid_pdf_file(tmp_path: Path, filename: str = "paper.pdf") -> Path:
    path = (tmp_path / filename).resolve()
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


def test_service_write_path_idempotence_preserves_timestamps(
    tmp_path: Path,
) -> None:
    """Repeated calls to save_single_paper_analysis_result leave record equivalent with stable timestamps."""
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    analysis_res = analyze_single_paper(
        pdf_path,
        extractor,
        generator,
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    rec1 = save_single_paper_analysis_result(
        storage, analysis_res, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )
    rec2 = save_single_paper_analysis_result(
        storage, analysis_res, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert rec1 == rec2
    assert rec1.created_at == rec2.created_at
    assert rec1.updated_at == rec2.updated_at

    storage.close()


def test_end_to_end_multi_page_sections_round_trip(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    analysis_res = analyze_single_paper(
        pdf_path,
        extractor,
        generator,
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    record = save_single_paper_analysis_result(storage, analysis_res)
    retrieved = get_single_paper_analysis_record(storage, record.analysis_id)

    assert retrieved is not None
    assert len(retrieved.sections) == 2
    intro = retrieved.sections[1]
    assert intro.heading_text == "1. Introduction"
    assert len(intro.spans) == 2
    assert intro.spans[0].page_number == 1
    assert intro.spans[1].page_number == 2

    storage.close()


def test_end_to_end_question_extraction_halted_terminal_causes(
    tmp_path: Path,
) -> None:
    """Distinct QUESTION_EXTRACTION_HALTED outcomes preserve terminal warning codes when saved/retrieved."""
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    # 1. NO_USABLE_SECTIONS cause
    res_no_sections = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(
            pages_text=["Just raw text without any abstract or intro headings."]
        ),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert (
        res_no_sections.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    )
    rec_no_sections = save_single_paper_analysis_result(storage, res_no_sections)
    ret_no_sections = get_single_paper_analysis_record(
        storage, rec_no_sections.analysis_id
    )
    assert ret_no_sections is not None
    assert (
        ret_no_sections.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    )
    assert (
        ret_no_sections.research_question_warnings
        == res_no_sections.research_question_result.warnings
    )

    # 2. MODEL_ABSTAINED cause
    res_abstained = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(),
        FakeGenerator(response_text="", abstained=True),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert res_abstained.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    rec_abstained = save_single_paper_analysis_result(storage, res_abstained)
    ret_abstained = get_single_paper_analysis_record(storage, rec_abstained.analysis_id)
    assert ret_abstained is not None
    assert ret_abstained.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert (
        ret_abstained.research_question_warnings
        == res_abstained.research_question_result.warnings
    )

    # 3. MALFORMED_STRUCTURED_RESPONSE cause
    res_malformed = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(),
        FakeGenerator(response_text="INVALID JSON {", abstained=False),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert res_malformed.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    rec_malformed = save_single_paper_analysis_result(storage, res_malformed)
    ret_malformed = get_single_paper_analysis_record(storage, rec_malformed.analysis_id)
    assert ret_malformed is not None
    assert ret_malformed.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert (
        ret_malformed.research_question_warnings
        == res_malformed.research_question_result.warnings
    )

    # 4. UNGROUNDED_EVIDENCE cause
    ungrounded_json = json.dumps(
        {
            "research_question": "Unused question",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": "Excerpt that does not exist in source PDF text!",
                    "page_number": 1,
                    "start_character_offset": 0,
                    "end_character_offset": 46,
                }
            ],
        }
    )
    res_ungrounded = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(),
        FakeGenerator(response_text=ungrounded_json, abstained=False),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert res_ungrounded.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    rec_ungrounded = save_single_paper_analysis_result(storage, res_ungrounded)
    ret_ungrounded = get_single_paper_analysis_record(
        storage, rec_ungrounded.analysis_id
    )
    assert ret_ungrounded is not None
    assert ret_ungrounded.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert (
        ret_ungrounded.research_question_warnings
        == res_ungrounded.research_question_result.warnings
    )

    # 5. GENERATION_FAILED cause
    res_failed = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(),
        FakeGenerator(raise_error=RuntimeError("LLM execution error")),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert res_failed.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    rec_failed = save_single_paper_analysis_result(storage, res_failed)
    ret_failed = get_single_paper_analysis_record(storage, rec_failed.analysis_id)
    assert ret_failed is not None
    assert ret_failed.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert (
        ret_failed.research_question_warnings
        == res_failed.research_question_result.warnings
    )
    assert (
        ret_failed.research_question_warnings[0].details
        == "Model generation failed: LLM execution error"
    )

    storage.close()
