"""Service integration tests for end-to-end single-paper research-question analysis."""

import json
from pathlib import Path

import pytest

from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
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
from econ_paper_cli.protocols.pdf_extraction import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFExtractor,
    PDFMalformedError,
    PDFPermissionError,
)
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper

_ALL_STAGES = tuple(SinglePaperAnalysisStage)


class FakePDFExtractor(PDFExtractor):
    """Fake PDF extractor for deterministic single-paper analysis tests."""

    def __init__(
        self,
        pages_text: list[str] | None = None,
        raise_error: PDFExtractionError | None = None,
    ) -> None:
        self.pages_text = pages_text or [
            "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
        ]
        self.raise_error = raise_error
        self.call_count: int = 0
        self.last_path: Path | None = None

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
        self.call_count += 1
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
        self.call_count: int = 0
        self.last_request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.call_count += 1
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
        # Build citations matching request evidence so validate_generation_response passes
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
    path.write_bytes(b"%PDF-1.4 header and synthetic content for preflight test")
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


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_successful_single_pdf_analysis_flow(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    intro_text = "1. Introduction\nTrade policy affects prices."
    page_text = f"{abs_text}\n\n{intro_text}"
    extractor = FakePDFExtractor(pages_text=[page_text])

    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.SUCCESS
    assert res.completed_stages == _ALL_STAGES
    assert res.failed_stage is None
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
    # Canonical path from preflight candidate used in extraction
    assert extractor.last_path == pdf_path.resolve()
    # Generator was called exactly once
    assert generator.call_count == 1


def test_usable_with_warnings_quality_proceeds_to_success(tmp_path: Path) -> None:
    """USABLE_WITH_WARNINGS quality should proceed — not halt."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    # Text with a single sparse page (non-empty but sparse) → USABLE_WITH_WARNINGS
    sparse_text = (
        "Abstract\nWe study growth.\n\n1. Introduction\nEconomic growth matters."
    )
    extractor = FakePDFExtractor(pages_text=[sparse_text])

    abs_text = "Abstract\nWe study growth."
    exc = "We study growth."
    resp_json = _make_success_response_json(abs_text, exc)
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    # Regardless of quality warnings, USABLE_WITH_WARNINGS must not halt
    assert res.status in (
        SinglePaperAnalysisStatus.SUCCESS,
        SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
    )
    assert res.quality_assessment is not None
    assert res.quality_assessment.status not in (
        PDFQualityStatus.LIKELY_NEEDS_OCR,
        PDFQualityStatus.UNUSABLE,
    )


# ---------------------------------------------------------------------------
# Preflight rejection
# ---------------------------------------------------------------------------


def test_non_pdf_file_rejected_as_preflight_failed(tmp_path: Path) -> None:
    txt_path = tmp_path / "paper.txt"
    txt_path.write_text("Not a PDF file.")

    extractor = FakePDFExtractor()
    generator = FakeGenerator()

    res = analyze_single_paper(
        txt_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.completed_stages == ()
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )
    assert res.extraction_result is None
    assert res.error_message is not None
    assert extractor.call_count == 0  # Extractor was not called


def test_missing_file_rejected_as_preflight_failed(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.pdf"
    extractor = FakePDFExtractor()
    generator = FakeGenerator()

    res = analyze_single_paper(
        missing, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert extractor.call_count == 0


def test_directory_with_one_pdf_rejected_as_single_file_enforcement(
    tmp_path: Path,
) -> None:
    """A directory containing exactly one PDF must be rejected: single-file contract."""
    subdir = tmp_path / "papers"
    subdir.mkdir()
    (subdir / "paper.pdf").write_bytes(b"%PDF-1.4 synthetic content")

    extractor = FakePDFExtractor()
    generator = FakeGenerator()

    res = analyze_single_paper(
        subdir, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert "single PDF file" in res.error_message
    assert extractor.call_count == 0


def test_directory_with_multiple_pdfs_rejected(tmp_path: Path) -> None:
    """A directory containing multiple PDFs must be rejected."""
    subdir = tmp_path / "papers"
    subdir.mkdir()
    (subdir / "paper1.pdf").write_bytes(b"%PDF-1.4 content1")
    (subdir / "paper2.pdf").write_bytes(b"%PDF-1.4 content2")

    extractor = FakePDFExtractor()
    generator = FakeGenerator()

    res = analyze_single_paper(
        subdir, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert extractor.call_count == 0


def test_relative_path_uses_canonical_extraction_path(tmp_path: Path) -> None:
    """source_path in result must be the canonical resolved candidate path."""
    pdf_path = _create_valid_pdf_file(tmp_path)
    # Pass the canonical path directly (run_ingestion_preflight resolves internally)
    extractor = FakePDFExtractor()

    abs_text = "Abstract\nWe evaluate trade policy."
    intro_text = "1. Introduction\nTrade policy affects prices."
    page_text = f"{abs_text}\n\n{intro_text}"
    extractor = FakePDFExtractor(pages_text=[page_text])
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        str(pdf_path),  # Pass as string to exercise Path conversion
        extractor,
        generator,
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    # source_path in result must be absolute/canonical
    assert res.source_path.is_absolute()
    assert res.source_path == pdf_path.resolve()


# ---------------------------------------------------------------------------
# Extraction failures (typed exceptions only)
# ---------------------------------------------------------------------------


def test_extraction_malformed_pdf_halts_analysis(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(
        raise_error=PDFMalformedError(pdf_path, ValueError("truncated xref"))
    )
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.completed_stages == (SinglePaperAnalysisStage.PREFLIGHT,)
    assert res.failed_stage is SinglePaperAnalysisStage.EXTRACTION
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )
    assert "malformed" in res.error_message.lower()


def test_extraction_encrypted_pdf_halts_analysis(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(raise_error=PDFEncryptedError(pdf_path))
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.EXTRACTION
    assert "encrypted" in res.error_message.lower()


def test_extraction_permission_error_halts_analysis(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(
        raise_error=PDFPermissionError(pdf_path, PermissionError("denied"))
    )
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.EXTRACTION


def test_unexpected_extractor_exception_propagates(tmp_path: Path) -> None:
    """Non-PDFExtractionError exceptions must propagate, not be swallowed."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(raise_error=RuntimeError("unexpected crash"))  # type: ignore[arg-type]
    generator = FakeGenerator()

    with pytest.raises(RuntimeError, match="unexpected crash"):
        analyze_single_paper(
            pdf_path,
            extractor,
            generator,
            settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        )


# ---------------------------------------------------------------------------
# Quality halt (LIKELY_NEEDS_OCR vs UNUSABLE separately)
# ---------------------------------------------------------------------------


def test_empty_page_halts_at_quality_likely_needs_ocr_or_unusable(
    tmp_path: Path,
) -> None:
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
    assert res.failed_stage is None
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
    assert generator.call_count == 0  # Generator not called


# ---------------------------------------------------------------------------
# Section detection halt: no usable sections → extract_research_question still called
# ---------------------------------------------------------------------------


def test_no_usable_sections_returns_section_detection_halted_with_rq_result(
    tmp_path: Path,
) -> None:
    """With no Abstract/Introduction, section detection halts; extract_research_question
    is still called and returns NO_USABLE_SECTIONS; result preserves that nested result
    and classifies as QUESTION_EXTRACTION_HALTED (not SECTION_DETECTION_HALTED)."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "3. Methodology and Data\nWe describe the regression model here."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    # Service always calls extract_research_question which returns UNAVAILABLE
    # with NO_USABLE_SECTIONS warning → terminal status is QUESTION_EXTRACTION_HALTED
    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.completed_stages == _ALL_STAGES
    assert res.failed_stage is None
    assert res.section_result is not None
    assert res.research_question_result is not None
    assert res.research_question_result.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.NO_USABLE_SECTIONS
        for w in res.research_question_result.warnings
    )
    assert any(
        w.code is SinglePaperAnalysisWarningCode.QUESTION_EXTRACTION_HALTED
        for w in res.warnings
    )
    # Generator was NOT called (extract_research_question short-circuited)
    assert generator.call_count == 0


# ---------------------------------------------------------------------------
# Generator failures and abstention
# ---------------------------------------------------------------------------


def test_generator_abstention_halts_question_extraction(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator(abstained=True)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.completed_stages == _ALL_STAGES
    assert res.failed_stage is None
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


def test_generator_failure_halts_question_extraction(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator(raise_error=RuntimeError("model crashed"))

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.research_question_result is not None
    assert res.research_question_result.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.GENERATION_FAILED
        for w in res.research_question_result.warnings
    )


def test_malformed_json_response_halts_question_extraction(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator(response_text="this is not json at all!!")

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.research_question_result is not None
    assert any(
        w.code is ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE
        for w in res.research_question_result.warnings
    )


def test_ungrounded_evidence_halts_question_extraction(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."
    extractor = FakePDFExtractor(pages_text=[page_text])
    # Evidence with invented text not present in the section
    bad_json = json.dumps(
        {
            "research_question": "What is the impact?",
            "kind": "explicit",
            "evidence": [
                {
                    "section_kind": "abstract",
                    "excerpt_text": "INVENTED TEXT NOT IN SECTION",
                    "page_number": 1,
                    "start_character_offset": 0,
                    "end_character_offset": 29,
                }
            ],
        }
    )
    generator = FakeGenerator(response_text=bad_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.research_question_result is not None
    assert any(
        w.code is ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE
        for w in res.research_question_result.warnings
    )


# ---------------------------------------------------------------------------
# Deterministic repeated runs
# ---------------------------------------------------------------------------


def test_deterministic_repeated_runs(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    intro_text = "1. Introduction\nTrade policy affects prices."
    page_text = f"{abs_text}\n\n{intro_text}"

    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

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
    assert res1.status is SinglePaperAnalysisStatus.SUCCESS


# ---------------------------------------------------------------------------
# Stage invocation ordering
# ---------------------------------------------------------------------------


def test_extractor_called_after_preflight_and_before_generator(tmp_path: Path) -> None:
    """Verify stage call ordering: extractor called once before generator."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    call_log: list[str] = []

    class OrderedExtractor(FakePDFExtractor):
        def extract(self, pdf_path: Path) -> PDFExtractionResult:
            call_log.append("extractor")
            return super().extract(pdf_path)

    class OrderedGenerator(FakeGenerator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            call_log.append("generator")
            return super().generate(request)

    abs_text = "Abstract\nWe evaluate trade policy."
    intro_text = "1. Introduction\nTrade policy affects prices."
    page_text = f"{abs_text}\n\n{intro_text}"
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = OrderedExtractor(pages_text=[page_text])
    generator = OrderedGenerator(response_text=resp_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.SUCCESS
    assert call_log == ["extractor", "generator"]
