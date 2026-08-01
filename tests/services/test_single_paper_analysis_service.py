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
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisWarningCode,
)
from econ_paper_cli.domain.errors import IngestionError, IngestionPathNotFoundError
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
    PDFParserError,
    PDFPermissionError,
    PDFReadError,
    PDFSourceNotFoundError,
    PDFSourceNotRegularFileError,
)
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper

_ALL_STAGES = tuple(SinglePaperAnalysisStage)

# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------

_DEFAULT_PAGE_TEXT = "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices."


class FakePDFExtractor(PDFExtractor):
    """Fake PDF extractor for deterministic single-paper analysis tests."""

    def __init__(
        self,
        pages_text: list[str] | None = None,
        raise_error: PDFExtractionError | None = None,
    ) -> None:
        self.pages_text = pages_text if pages_text is not None else [_DEFAULT_PAGE_TEXT]
        self.raise_error = raise_error
        self.call_count: int = 0
        self.last_path: Path | None = None

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
        self.call_count += 1
        self.last_path = pdf_path
        if self.raise_error is not None:
            raise self.raise_error

        pages = tuple(
            ExtractedPDFPage(page_number=i + 1, text=txt)
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
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)
    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.SUCCESS
    assert res.completed_stages == _ALL_STAGES
    assert res.failed_stage is None
    assert res.failure_code is None
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
    # Canonical resolved path used for extraction
    assert extractor.last_path == pdf_path.resolve()
    assert generator.call_count == 1


def test_usable_with_warnings_quality_proceeds_to_success(tmp_path: Path) -> None:
    """USABLE_WITH_WARNINGS quality must not halt the pipeline."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    abs_text = "Abstract\nWe study growth."
    exc = "We study growth."
    resp_json = _make_success_response_json(abs_text, exc)
    page_text = f"{abs_text}\n\n1. Introduction\nEconomic growth matters."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.quality_assessment is not None
    assert res.quality_assessment.status is PDFQualityStatus.USABLE_WITH_WARNINGS
    assert res.status is SinglePaperAnalysisStatus.SUCCESS


# ---------------------------------------------------------------------------
# Preflight rejection
# ---------------------------------------------------------------------------


def test_non_pdf_file_rejected_as_preflight_failed(tmp_path: Path) -> None:
    txt_path = tmp_path / "paper.txt"
    txt_path.write_text("Not a PDF file.")

    res = analyze_single_paper(
        txt_path,
        FakePDFExtractor(),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.completed_stages == ()
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert res.failure_code is SinglePaperAnalysisFailureCode.UNSUPPORTED_FILE_TYPE
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )
    assert res.extraction_result is None


def test_missing_file_rejected_as_path_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.pdf"

    res = analyze_single_paper(
        missing,
        FakePDFExtractor(),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert res.failure_code is SinglePaperAnalysisFailureCode.PATH_NOT_FOUND
    assert isinstance(res.failure_cause, IngestionPathNotFoundError)
    assert res.error_message == str(res.failure_cause)

    repeated = analyze_single_paper(
        missing,
        FakePDFExtractor(),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    assert repeated.failure_cause is not res.failure_cause
    assert repeated == res


def test_directory_with_one_pdf_rejected_as_directory_input(tmp_path: Path) -> None:
    """A directory containing exactly one PDF must be rejected: DIRECTORY_INPUT code."""
    subdir = tmp_path / "papers"
    subdir.mkdir()
    (subdir / "paper.pdf").write_bytes(b"%PDF-1.4 synthetic content")

    extractor = FakePDFExtractor()
    res = analyze_single_paper(
        subdir,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert res.failure_code is SinglePaperAnalysisFailureCode.DIRECTORY_INPUT
    assert res.failure_cause is None
    assert "single PDF file" in res.error_message
    assert extractor.call_count == 0


def test_empty_directory_rejected_as_directory_input(tmp_path: Path) -> None:
    subdir = tmp_path / "papers"
    subdir.mkdir()

    res = analyze_single_paper(
        subdir,
        FakePDFExtractor(),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failure_code is SinglePaperAnalysisFailureCode.DIRECTORY_INPUT
    assert res.failure_cause is not None
    assert res.error_message == str(res.failure_cause)


def test_directory_with_multiple_pdfs_rejected(tmp_path: Path) -> None:
    """A directory containing multiple PDFs must be rejected."""
    subdir = tmp_path / "papers"
    subdir.mkdir()
    (subdir / "paper1.pdf").write_bytes(b"%PDF-1.4 content1")
    (subdir / "paper2.pdf").write_bytes(b"%PDF-1.4 content2")

    extractor = FakePDFExtractor()
    res = analyze_single_paper(
        subdir,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.PREFLIGHT_FAILED
    assert res.failed_stage is SinglePaperAnalysisStage.PREFLIGHT
    assert extractor.call_count == 0


def test_relative_path_uses_canonical_extraction_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source_path in result must be the canonical resolved candidate path.

    This test deliberately constructs a *relative* path using os.chdir-free
    resolution: we resolve tmp_path to absolute, then build a relative path
    from the cwd to the file.
    """
    pdf_path = _create_valid_pdf_file(tmp_path)

    monkeypatch.chdir(tmp_path)
    relative = Path(pdf_path.name)
    assert not relative.is_absolute()

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)
    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    res = analyze_single_paper(
        relative, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    # source_path in result must always be absolute/canonical
    assert res.source_path.is_absolute()
    assert res.source_path == pdf_path.resolve()


# ---------------------------------------------------------------------------
# Extraction failures — exact failure codes
# ---------------------------------------------------------------------------


def test_extraction_malformed_pdf_returns_pdf_malformed_code(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    cause = PDFMalformedError(pdf_path, ValueError("truncated xref"))
    extractor = FakePDFExtractor(raise_error=cause)
    res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.completed_stages == (SinglePaperAnalysisStage.PREFLIGHT,)
    assert res.failed_stage is SinglePaperAnalysisStage.EXTRACTION
    assert res.failure_code is SinglePaperAnalysisFailureCode.PDF_MALFORMED
    assert res.failure_cause is cause
    assert res.error_message == str(cause)
    assert res.skipped_stages == (
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
        SinglePaperAnalysisStage.SECTION_DETECTION,
        SinglePaperAnalysisStage.QUESTION_EXTRACTION,
    )


def test_extraction_encrypted_pdf_returns_pdf_encrypted_code(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(raise_error=PDFEncryptedError(pdf_path))
    res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.failure_code is SinglePaperAnalysisFailureCode.PDF_ENCRYPTED


def test_extraction_permission_error_returns_pdf_permission_denied_code(
    tmp_path: Path,
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(
        raise_error=PDFPermissionError(pdf_path, PermissionError("denied"))
    )
    res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.failure_code is SinglePaperAnalysisFailureCode.PDF_PERMISSION_DENIED


@pytest.mark.parametrize(
    ("error_factory", "expected_code"),
    (
        (
            lambda path: PDFSourceNotFoundError(path),
            SinglePaperAnalysisFailureCode.PDF_NOT_FOUND,
        ),
        (
            lambda path: PDFSourceNotRegularFileError(path),
            SinglePaperAnalysisFailureCode.PDF_NOT_REGULAR_FILE,
        ),
        (
            lambda path: PDFReadError(path, OSError("read failed")),
            SinglePaperAnalysisFailureCode.PDF_READ_ERROR,
        ),
        (
            lambda path: PDFParserError(path, ValueError("parser failed")),
            SinglePaperAnalysisFailureCode.PDF_PARSER_ERROR,
        ),
    ),
    ids=("not-found", "not-regular-file", "read-error", "parser-error"),
)
def test_remaining_extraction_errors_map_to_exact_codes(
    tmp_path: Path,
    error_factory,
    expected_code: SinglePaperAnalysisFailureCode,
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    cause = error_factory(pdf_path)

    res = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(raise_error=cause),
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.EXTRACTION_FAILED
    assert res.failure_code is expected_code
    assert res.failure_cause is cause


def test_unexpected_extractor_exception_propagates(tmp_path: Path) -> None:
    """Non-PDFExtractionError exceptions must propagate, not be swallowed."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor(raise_error=RuntimeError("unexpected crash"))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="unexpected crash"):
        analyze_single_paper(
            pdf_path,
            extractor,
            FakeGenerator(),
            settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        )


def test_unmapped_ingestion_error_subclass_propagates(tmp_path: Path) -> None:
    """Future ingestion errors need an explicit code before translation."""
    from unittest.mock import patch

    import econ_paper_cli.services.single_paper_analysis as svc_mod

    class FutureIngestionError(IngestionError):
        pass

    cause = FutureIngestionError("future preflight failure")
    with (
        patch.object(svc_mod, "run_ingestion_preflight", side_effect=cause),
        pytest.raises(FutureIngestionError) as raised,
    ):
        analyze_single_paper(
            tmp_path / "paper.pdf",
            FakePDFExtractor(),
            FakeGenerator(),
            settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        )

    assert raised.value is cause


def test_unmapped_pdf_extraction_error_subclass_propagates(tmp_path: Path) -> None:
    """Future extraction errors need an explicit code before translation."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    class FuturePDFExtractionError(PDFExtractionError):
        pass

    cause = FuturePDFExtractionError("future extraction failure")
    extractor = FakePDFExtractor(raise_error=cause)
    with pytest.raises(FuturePDFExtractionError) as raised:
        analyze_single_paper(
            pdf_path,
            extractor,
            FakeGenerator(),
            settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        )

    assert raised.value is cause


# ---------------------------------------------------------------------------
# Quality halt: LIKELY_NEEDS_OCR and UNUSABLE tested separately
# ---------------------------------------------------------------------------


def test_empty_page_halts_at_quality_likely_needs_ocr(tmp_path: Path) -> None:
    """Empty page text -> ALL_PAGES_EMPTY warning -> LIKELY_NEEDS_OCR status."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    # Empty page -> ALL_PAGES_EMPTY warning -> LIKELY_NEEDS_OCR
    extractor = FakePDFExtractor(pages_text=[""])
    res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.QUALITY_HALTED
    assert res.completed_stages == (
        SinglePaperAnalysisStage.PREFLIGHT,
        SinglePaperAnalysisStage.EXTRACTION,
        SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
    )
    assert res.failed_stage is None
    assert res.failure_code is None
    assert res.quality_assessment is not None
    assert res.quality_assessment.status is PDFQualityStatus.LIKELY_NEEDS_OCR
    assert res.section_result is None
    assert res.research_question_result is None
    assert any(
        w.code is SinglePaperAnalysisWarningCode.QUALITY_HALTED for w in res.warnings
    )


def test_garbage_page_halts_at_quality_unusable(tmp_path: Path) -> None:
    """Page dominated by replacement chars -> EXTRACTION_GARBAGE -> UNUSABLE."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    # Text consisting mostly of Unicode replacement characters (\ufffd) exceeds
    # the 10% anomaly_ratio_unusable_threshold -> EXTRACTION_GARBAGE -> UNUSABLE
    garbage_text = "\ufffd" * 100  # 100% replacement chars
    extractor = FakePDFExtractor(pages_text=[garbage_text])
    res = analyze_single_paper(
        pdf_path,
        extractor,
        FakeGenerator(),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res.status is SinglePaperAnalysisStatus.QUALITY_HALTED
    assert res.quality_assessment is not None
    assert res.quality_assessment.status is PDFQualityStatus.UNUSABLE
    assert res.section_result is None
    assert res.research_question_result is None
    assert any(
        w.code is SinglePaperAnalysisWarningCode.QUALITY_HALTED for w in res.warnings
    )


# ---------------------------------------------------------------------------
# No usable sections → extract_research_question always called
# ---------------------------------------------------------------------------


def test_no_usable_sections_always_calls_extract_research_question(
    tmp_path: Path,
) -> None:
    """With no Abstract/Introduction, extract_research_question is still called
    and returns NO_USABLE_SECTIONS; result is QUESTION_EXTRACTION_HALTED."""
    pdf_path = _create_valid_pdf_file(tmp_path)

    page_text = "3. Methodology and Data\nWe describe the regression model here."
    extractor = FakePDFExtractor(pages_text=[page_text])
    generator = FakeGenerator()

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.completed_stages == _ALL_STAGES
    assert res.failed_stage is None
    assert res.failure_code is None
    assert res.section_result is not None
    assert res.research_question_result is not None
    assert res.research_question_result.kind is ResearchQuestionKind.UNAVAILABLE
    assert any(
        w.code is ResearchQuestionWarningCode.NO_USABLE_SECTIONS
        for w in res.research_question_result.warnings
    )
    # Generator short-circuited by extract_research_question (no usable sections)
    assert generator.call_count == 0


# ---------------------------------------------------------------------------
# Generator failures and abstention
# ---------------------------------------------------------------------------


def test_generator_abstention_halts_question_extraction(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(abstained=True)

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.completed_stages == _ALL_STAGES
    assert res.failed_stage is None
    assert res.failure_code is None
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

    extractor = FakePDFExtractor()
    generator = FakeGenerator(raise_error=RuntimeError("model crashed"))

    res = analyze_single_paper(
        pdf_path, extractor, generator, settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS
    )

    assert res.status is SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED
    assert res.research_question_result is not None
    assert any(
        w.code is ResearchQuestionWarningCode.GENERATION_FAILED
        for w in res.research_question_result.warnings
    )


def test_malformed_json_response_halts_question_extraction(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    extractor = FakePDFExtractor()
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

    extractor = FakePDFExtractor()
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
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    res1 = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(),
        FakeGenerator(response_text=resp_json),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    res2 = analyze_single_paper(
        pdf_path,
        FakePDFExtractor(),
        FakeGenerator(response_text=resp_json),
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )

    assert res1 == res2
    assert res1.status is SinglePaperAnalysisStatus.SUCCESS


# ---------------------------------------------------------------------------
# Stage invocation ordering: all 5 stages instrumented
# ---------------------------------------------------------------------------


def test_all_five_stage_calls_in_correct_order(tmp_path: Path) -> None:
    """Verify all 5 stage calls happen in canonical order using a call log."""
    from unittest.mock import patch

    pdf_path = _create_valid_pdf_file(tmp_path)
    call_log: list[str] = []

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    import econ_paper_cli.services.single_paper_analysis as svc_mod

    original_preflight = svc_mod.run_ingestion_preflight
    original_quality = svc_mod.assess_pdf_extraction_quality
    original_sections = svc_mod.detect_pdf_sections
    original_rq = svc_mod.extract_research_question

    class _OrderedExtractor(FakePDFExtractor):
        def extract(self, pdf_path: Path) -> PDFExtractionResult:
            call_log.append("extraction")
            return super().extract(pdf_path)

    def _preflight_wrap(path):
        call_log.append("preflight")
        return original_preflight(path)

    def _quality_wrap(result, settings):
        call_log.append("quality_assessment")
        return original_quality(result, settings=settings)

    def _sections_wrap(result, settings):
        call_log.append("section_detection")
        return original_sections(result, settings=settings)

    def _rq_wrap(section_result, gen, settings):
        call_log.append("question_extraction")
        return original_rq(section_result, gen, settings)

    ordered_extractor = _OrderedExtractor()
    ordered_generator = FakeGenerator(response_text=resp_json)

    with (
        patch.object(svc_mod, "run_ingestion_preflight", _preflight_wrap),
        patch.object(svc_mod, "assess_pdf_extraction_quality", _quality_wrap),
        patch.object(svc_mod, "detect_pdf_sections", _sections_wrap),
        patch.object(svc_mod, "extract_research_question", _rq_wrap),
    ):
        res = analyze_single_paper(
            pdf_path,
            ordered_extractor,
            ordered_generator,
            settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        )

    assert res.status is SinglePaperAnalysisStatus.SUCCESS
    assert call_log == [
        "preflight",
        "extraction",
        "quality_assessment",
        "section_detection",
        "question_extraction",
    ]
