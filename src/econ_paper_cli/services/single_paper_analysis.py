"""Application service orchestrating single-paper research-question analysis."""

from pathlib import Path

from econ_paper_cli.domain.errors import (
    IngestionError,
    SinglePaperAnalysisValidationError,
)
from econ_paper_cli.domain.pdf_quality import PDFQualityStatus
from econ_paper_cli.domain.pdf_sections import PDFSectionKind
from econ_paper_cli.domain.research_question import ResearchQuestionKind
from econ_paper_cli.domain.single_paper_analysis import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisWarning,
    SinglePaperAnalysisWarningCode,
)
from econ_paper_cli.protocols.generation import Generator
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor
from econ_paper_cli.services.ingestion import run_ingestion_preflight
from econ_paper_cli.services.pdf_quality import assess_pdf_extraction_quality
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.research_question_extraction import (
    extract_research_question,
)


def analyze_single_paper(
    pdf_path: Path | str,
    pdf_extractor: PDFExtractor,
    generator: Generator,
    settings: SinglePaperAnalysisSettings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
) -> SinglePaperAnalysisResult:
    """Orchestrate end-to-end single-PDF research-question analysis.

    Executes ingestion preflight, text extraction, extraction quality assessment,
    Abstract/Introduction section detection, and structured research question
    extraction in deterministic order, preserving typed stage results and provenance.
    """
    _validate_inputs(pdf_extractor, generator, settings)

    source_path = Path(pdf_path) if isinstance(pdf_path, str) else pdf_path
    if not isinstance(source_path, Path):
        raise SinglePaperAnalysisValidationError("pdf_path must be a Path or string.")

    # Stage 1: Ingestion Preflight
    try:
        preflight_result = run_ingestion_preflight(source_path)
    except (IngestionError, Exception) as error:
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(SinglePaperAnalysisStage.PREFLIGHT,),
            skipped_stages=(
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=str(error),
        )

    checksum = preflight_result.candidates[0].content_checksum

    # Stage 2: PDF Text Extraction
    try:
        extraction_result = pdf_extractor.extract(source_path)
    except Exception as error:
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.EXTRACTION_FAILED,
            completed_stages=(
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
            ),
            skipped_stages=(
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            preflight_result=preflight_result,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=f"PDF text extraction failed: {error}",
        )

    # Stage 3: Extraction Quality Assessment
    quality_assessment = assess_pdf_extraction_quality(
        extraction_result, settings=settings.quality_settings
    )
    if quality_assessment.status in (
        PDFQualityStatus.LIKELY_NEEDS_OCR,
        PDFQualityStatus.UNUSABLE,
    ):
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.QUALITY_HALTED,
            completed_stages=(
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            ),
            skipped_stages=(
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            preflight_result=preflight_result,
            extraction_result=extraction_result,
            quality_assessment=quality_assessment,
            section_result=None,
            research_question_result=None,
            warnings=(
                SinglePaperAnalysisWarning(
                    SinglePaperAnalysisWarningCode.QUALITY_HALTED,
                    f"Extraction quality status is {quality_assessment.status.value}.",
                ),
            ),
            error_message=None,
        )

    # Stage 4: Section Detection
    section_result = detect_pdf_sections(
        extraction_result, settings=settings.section_settings
    )
    has_usable_sections = any(
        sec.kind in (PDFSectionKind.ABSTRACT, PDFSectionKind.INTRODUCTION)
        and bool(sec.text.strip())
        for sec in section_result.sections
    )
    if not has_usable_sections:
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.SECTION_DETECTION_HALTED,
            completed_stages=(
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
            ),
            skipped_stages=(SinglePaperAnalysisStage.QUESTION_EXTRACTION,),
            preflight_result=preflight_result,
            extraction_result=extraction_result,
            quality_assessment=quality_assessment,
            section_result=section_result,
            research_question_result=None,
            warnings=(
                SinglePaperAnalysisWarning(
                    SinglePaperAnalysisWarningCode.SECTION_DETECTION_HALTED,
                ),
            ),
            error_message=None,
        )

    # Stage 5: Research Question Extraction
    research_question_result = extract_research_question(
        section_result, generator, settings.research_question_settings
    )

    if research_question_result.kind is ResearchQuestionKind.UNAVAILABLE:
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
            completed_stages=(
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
                SinglePaperAnalysisStage.SECTION_DETECTION,
                SinglePaperAnalysisStage.QUESTION_EXTRACTION,
            ),
            skipped_stages=(),
            preflight_result=preflight_result,
            extraction_result=extraction_result,
            quality_assessment=quality_assessment,
            section_result=section_result,
            research_question_result=research_question_result,
            warnings=(
                SinglePaperAnalysisWarning(
                    SinglePaperAnalysisWarningCode.QUESTION_EXTRACTION_HALTED,
                ),
            ),
            error_message=None,
        )

    return SinglePaperAnalysisResult(
        policy_version=settings.policy_version,
        source_path=source_path,
        checksum=checksum,
        status=SinglePaperAnalysisStatus.SUCCESS,
        completed_stages=(
            SinglePaperAnalysisStage.PREFLIGHT,
            SinglePaperAnalysisStage.EXTRACTION,
            SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            SinglePaperAnalysisStage.SECTION_DETECTION,
            SinglePaperAnalysisStage.QUESTION_EXTRACTION,
        ),
        skipped_stages=(),
        preflight_result=preflight_result,
        extraction_result=extraction_result,
        quality_assessment=quality_assessment,
        section_result=section_result,
        research_question_result=research_question_result,
        warnings=(),
        error_message=None,
    )


def _validate_inputs(
    pdf_extractor: object,
    generator: object,
    settings: object,
) -> None:
    if not isinstance(pdf_extractor, PDFExtractor):
        raise SinglePaperAnalysisValidationError(
            "pdf_extractor must implement the PDFExtractor protocol."
        )
    if not isinstance(generator, Generator):
        raise SinglePaperAnalysisValidationError(
            "generator must implement the Generator protocol."
        )
    if not isinstance(settings, SinglePaperAnalysisSettings):
        raise SinglePaperAnalysisValidationError(
            "settings must be a SinglePaperAnalysisSettings instance."
        )
