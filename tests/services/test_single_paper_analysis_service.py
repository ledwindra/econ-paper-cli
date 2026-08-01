"""Service integration tests for end-to-end single-paper research-question analysis."""

import json
from pathlib import Path

from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    Citation,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFQualityStatus,
    ResearchQuestionKind,
    ResearchQuestionWarningCode,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisWarningCode,
)
from econ_paper_cli.protocols.generation import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper


class FakePDFExtractor(PDFExtractor):
    """Fake PDF extractor for deterministic single-paper analysis tests."""

    def __init__(
        self,
        pages_text: list[str] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.pages_text = pages_text or [
            "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
        ]
        self.raise_error = raise_error
        self.last_path: Path | None = None

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
        self.last_path = pdf_path
        if self.raise_error is not None:
            raise self.raise_error

        pages = tuple(
            ExtractedPDFPage(
                page_number=i + 1,
                text=txt,
            )
            for i, txt in enumerate(self.pages_text)
        )
        meta = PDFDocumentMetadata(title="Test Paper")
        return PDFExtractionResult(
            source_path=pdf_path.resolve(),
            pages=pages,
            page_count=len(pages),
            metadata=meta,
            extraction_method="fake_extractor",
            parser_version="1.0.0",
        )


class FakeGenerator(Generator):
    """Fake model generator for deterministic single-paper analysis tests."""

    def __init__(
        self,
        response_text: str | None = None,
        raise_error: Exception | None = None,
        abstained: bool = False,
    ) -> None:
        self.response_text = response_text
        self.raise_error = raise_error
        self.abstained = abstained
        self.last_request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.last_request = request
        if self.raise_error is not None:
            raise self.raise_error
        if self.abstained:
            return GenerationResponse(
                answer_text="Abstaining due to insufficient evidence.",
                citations=(),
                generation_method="fake_generator",
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )
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
    path.write_bytes(b"%PDF-1.4 header and synthetic content for preflight test")
    return path


def test_successful_single_pdf_analysis_flow(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    intro_text = "1. Introduction\nTrade policy affects prices."
    page_text = f"{abs_text}\n\n{intro_text}"
    extractor = FakePDFExtractor(pages_text=[page_text])

    exc = "We evaluate trade policy."
    start_off = abs_text.find(exc)
    resp_json = json.dumps(
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
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.SUCCESS
    assert res.completed_stages == tuple(SinglePaperAnalysisStage)
    assert res.skipped_stages == ()
    assert res.preflight_result is not None
    assert res.extraction_result is not None
    assert res.quality_assessment is not None
    assert res.section_result is not None
    assert res.research_question_result is not None
    assert res.research_question_result.kind is ResearchQuestionKind.EXPLICIT
    assert (
        res.research_question_result.question_text
        == "What is the impact of trade policy?"
    )


def test_preflight_rejection_halts_analysis(tmp_path: Path) -> None:
    txt_path = tmp_path / "paper.txt"
    txt_path.write_text("Not a PDF file.")

    extractor = FakePDFExtractor()
    generator = FakeGenerator()

    res = analyze_single_paper(
        txt_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.completed_stages == (SinglePaperAnalysisStage.PREFLIGHT,)
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )
    assert res.extraction_result is None
    assert res.error_message is not None
    assert extractor.last_path is None  # Extractor was skipped!


def test_extraction_failure_halts_analysis(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(raise_error=RuntimeError("Corrupted PDF streams"))
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.completed_stages == (
        SinglePaperAnalysisStage.PREFLIGHT,
        SinglePaperAnalysisStage.EXTRACTION,
    )
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )
    assert "Corrupted PDF streams" in res.error_message


def test_low_quality_extraction_halts_before_section_detection(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(pages_text=["   \n   "])
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUALITY_HALTED
    assert res.completed_stages == (
        SinglePaperAnalysisStage.PREFLIGHT,
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
    )
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )
    assert res.quality_assessment is not None
    assert res.quality_assessment.status in (
        PDFQualityStatus.LIKELY_NEEDS_OCR,
        PDFQualityStatus.UNUSABLE,
    )
    assert res.section_result is None
    assert res.research_question_result is None
    assert any(
        w.code is SinglePaperAnalysisWarningCode.QUALITY_HALTED for w in res.warnings
    )


def test_no_usable_sections_halts_section_detection(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "3. Methodology and Data\nWe describe the regression model here."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.SECTION_DETECTION_HALTED
    assert res.completed_stages == (
        SinglePaperAnalysisStage.PREFLIGHT,
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
    )
    assert res.skipped_stages == (SinglePaperAnalysisStage.QUESTION_EXTRACTION,)
    assert res.section_result is not None
    assert res.research_question_result is None
    assert any(
        w.code is SinglePaperAnalysisWarningCode.SECTION_DETECTION_HALTED
        for w in res.warnings
    )


def test_generator_abstention_nested_in_analysis_result(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator(abstained=True)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.completed_stages == tuple(SinglePaperAnalysisStage)
    assert res.skipped_stages == ()
    assert res.research_question_result is not None
    assert res.research_question_result.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.MODEL_ABSTAINED
        for w in res.research_question_result.warnings
    )
    assert any(
        w.code is SinglePaperAnalysisWarningCode.QUESTION_EXTRACTION_HALTED
        for w in res.warnings
    )


def test_deterministic_repeated_runs(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    intro_text = "1. Introduction\nTrade policy affects prices."
    page_text = f"{abs_text}\n\n{intro_text}"

    exc = "We evaluate trade policy."
    start_off = abs_text.find(exc)
    resp_json = json.dumps(
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

    ext1 = FakePDFExtractor(pages_text=[page_text])
    gen1 = FakeGenerator(response_text=resp_json)
    res1 = analyze_single_paper(
        pdf_path, ext1, gen1, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    ext2 = FakePDFExtractor(pages_text=[page_text])
    gen2 = FakeGenerator(response_text=resp_json)
    res2 = analyze_single_paper(
        pdf_path, ext2, gen2, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res1 == res2
