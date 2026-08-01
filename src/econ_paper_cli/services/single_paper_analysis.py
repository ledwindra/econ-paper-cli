"""Application service orchestrating single-paper research-question analysis."""

from pathlib import Path

from econ_paper_cli.domain.errors import (
    IngestionEmptyDirectoryError,
    IngestionError,
    IngestionInvalidPathError,
    IngestionPathNotFoundError,
    IngestionPermissionError,
    IngestionReadError,
    IngestionUnsupportedFileError,
    SinglePaperAnalysisValidationError,
)
from econ_paper_cli.domain.pdf_quality import PDFQualityStatus
from econ_paper_cli.domain.research_question import ResearchQuestionKind
from econ_paper_cli.domain.single_paper_analysis import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisWarning,
    SinglePaperAnalysisWarningCode,
)
from econ_paper_cli.protocols.generation import Generator
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
from econ_paper_cli.services.ingestion import run_ingestion_preflight
from econ_paper_cli.services.pdf_quality import assess_pdf_extraction_quality
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.research_question_extraction import (
    extract_research_question,
)

_SKIPPED_AFTER_PREFLIGHT = (
    SinglePaperAnalysisStage.EXTRACTION,
    SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
    SinglePaperAnalysisStage.SECTION_DETECTION,
    SinglePaperAnalysisStage.QUESTION_EXTRACTION,
)
_SKIPPED_AFTER_EXTRACTION = (
    SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
    SinglePaperAnalysisStage.SECTION_DETECTION,
    SinglePaperAnalysisStage.QUESTION_EXTRACTION,
)
_SKIPPED_AFTER_QUALITY = (
    SinglePaperAnalysisStage.SECTION_DETECTION,
    SinglePaperAnalysisStage.QUESTION_EXTRACTION,
)
_ALL_STAGES = tuple(SinglePaperAnalysisStage)

# Map typed IngestionError subclasses → stable failure codes
_INGESTION_ERROR_CODES: dict[type[IngestionError], SinglePaperAnalysisFailureCode] = {
    IngestionPathNotFoundError: SinglePaperAnalysisFailureCode.PATH_NOT_FOUND,
    IngestionInvalidPathError: SinglePaperAnalysisFailureCode.PATH_INVALID,
    IngestionUnsupportedFileError: SinglePaperAnalysisFailureCode.UNSUPPORTED_FILE_TYPE,
    IngestionEmptyDirectoryError: SinglePaperAnalysisFailureCode.DIRECTORY_INPUT,
    IngestionPermissionError: SinglePaperAnalysisFailureCode.PREFLIGHT_PERMISSION_DENIED,
    IngestionReadError: SinglePaperAnalysisFailureCode.PREFLIGHT_READ_ERROR,
}

# Map typed PDFExtractionError subclasses → stable failure codes
_PDF_EXTRACTION_ERROR_CODES: dict[
    type[PDFExtractionError], SinglePaperAnalysisFailureCode
] = {
    PDFSourceNotFoundError: SinglePaperAnalysisFailureCode.PDF_NOT_FOUND,
    PDFSourceNotRegularFileError: SinglePaperAnalysisFailureCode.PDF_NOT_REGULAR_FILE,
    PDFPermissionError: SinglePaperAnalysisFailureCode.PDF_PERMISSION_DENIED,
    PDFReadError: SinglePaperAnalysisFailureCode.PDF_READ_ERROR,
    PDFMalformedError: SinglePaperAnalysisFailureCode.PDF_MALFORMED,
    PDFEncryptedError: SinglePaperAnalysisFailureCode.PDF_ENCRYPTED,
    PDFParserError: SinglePaperAnalysisFailureCode.PDF_PARSER_ERROR,
}


def analyze_single_paper(
    pdf_path: Path | str,
    pdf_extractor: PDFExtractor,
    generator: Generator,
    settings: SinglePaperAnalysisSettings = DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
) -> SinglePaperAnalysisResult:
    """Orchestrate end-to-end single-PDF research-question analysis.

    Accepts exactly one local PDF file path. Directories and non-PDF paths are
    rejected as ``PREFLIGHT_FAILED`` with a stable ``failure_code``.

    Executes ingestion preflight → text extraction → quality assessment →
    section detection → research question extraction in deterministic order.
    Typed ``IngestionError`` and ``PDFExtractionError`` subclasses are mapped
    to stable ``SinglePaperAnalysisFailureCode`` values; unexpected exceptions
    propagate unchanged.
    """
    _validate_inputs(pdf_extractor, generator, settings)

    source_path = Path(pdf_path) if isinstance(pdf_path, str) else pdf_path
    if not isinstance(source_path, Path):
        raise SinglePaperAnalysisValidationError("pdf_path must be a Path or string.")

    # Stage 1: Ingestion Preflight
    # Only typed IngestionError subclasses are caught; programming errors propagate.
    try:
        preflight_result = run_ingestion_preflight(source_path)
    except IngestionError as error:
        failure_code = _map_ingestion_error(error)
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=_SKIPPED_AFTER_PREFLIGHT,
            failure_code=failure_code,
            preflight_result=None,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=str(error),
            failure_cause=error,
        )

    # Enforce single-file contract: the target must be a regular file, not a
    # directory (even if the directory contains exactly one PDF).
    if not preflight_result.target_path.is_file():
        reason = (
            f"'{source_path}' is a directory, not a single PDF file. "
            "analyze_single_paper requires a path to a single PDF file."
        )
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=_SKIPPED_AFTER_PREFLIGHT,
            failure_code=SinglePaperAnalysisFailureCode.DIRECTORY_INPUT,
            preflight_result=preflight_result,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=reason,
        )

    # Additional guard: preflight must have discovered exactly one candidate.
    if preflight_result.total_candidate_count != 1:
        reason = (
            f"Expected exactly one PDF candidate but found "
            f"{preflight_result.total_candidate_count}. "
            "analyze_single_paper requires a path to a single PDF file."
        )
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=source_path,
            checksum=None,
            status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            completed_stages=(),
            failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
            skipped_stages=_SKIPPED_AFTER_PREFLIGHT,
            failure_code=SinglePaperAnalysisFailureCode.MULTI_CANDIDATE_BATCH,
            preflight_result=preflight_result,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=reason,
        )

    candidate = preflight_result.candidates[0]
    checksum = candidate.content_checksum
    # Use the canonical resolved path from the preflight candidate for extraction.
    canonical_path = candidate.source_path

    # Stage 2: PDF Text Extraction
    # Only typed PDFExtractionError subclasses are caught; programming errors propagate.
    try:
        extraction_result = pdf_extractor.extract(canonical_path)
    except PDFExtractionError as error:
        failure_code = _map_pdf_extraction_error(error)
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=canonical_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.EXTRACTION_FAILED,
            completed_stages=(SinglePaperAnalysisStage.PREFLIGHT,),
            failed_stage=SinglePaperAnalysisStage.EXTRACTION,
            skipped_stages=_SKIPPED_AFTER_EXTRACTION,
            failure_code=failure_code,
            preflight_result=preflight_result,
            extraction_result=None,
            quality_assessment=None,
            section_result=None,
            research_question_result=None,
            warnings=(),
            error_message=str(error),
            failure_cause=error,
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
            source_path=canonical_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.QUALITY_HALTED,
            completed_stages=(
                SinglePaperAnalysisStage.PREFLIGHT,
                SinglePaperAnalysisStage.EXTRACTION,
                SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            ),
            failed_stage=None,
            skipped_stages=_SKIPPED_AFTER_QUALITY,
            failure_code=None,
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

    # Stage 5: Research Question Extraction
    # Always call extract_research_question, even when no usable sections are
    # present — the service returns NO_USABLE_SECTIONS internally.
    research_question_result = extract_research_question(
        section_result, generator, settings.research_question_settings
    )

    if research_question_result.kind is ResearchQuestionKind.UNAVAILABLE:
        return SinglePaperAnalysisResult(
            policy_version=settings.policy_version,
            source_path=canonical_path,
            checksum=checksum,
            status=SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
            completed_stages=_ALL_STAGES,
            failed_stage=None,
            skipped_stages=(),
            failure_code=None,
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
        source_path=canonical_path,
        checksum=checksum,
        status=SinglePaperAnalysisStatus.SUCCESS,
        completed_stages=_ALL_STAGES,
        failed_stage=None,
        skipped_stages=(),
        failure_code=None,
        preflight_result=preflight_result,
        extraction_result=extraction_result,
        quality_assessment=quality_assessment,
        section_result=section_result,
        research_question_result=research_question_result,
        warnings=(),
        error_message=None,
    )


def _map_ingestion_error(error: IngestionError) -> SinglePaperAnalysisFailureCode:
    """Map a typed IngestionError subclass to a stable failure code."""
    for exc_type, code in _INGESTION_ERROR_CODES.items():
        if isinstance(error, exc_type):
            return code
    # A future subtype needs an explicit public code rather than a misleading fallback.
    raise error


def _map_pdf_extraction_error(
    error: PDFExtractionError,
) -> SinglePaperAnalysisFailureCode:
    """Map a typed PDFExtractionError subclass to a stable failure code."""
    for exc_type, code in _PDF_EXTRACTION_ERROR_CODES.items():
        if isinstance(error, exc_type):
            return code
    # A future subtype needs an explicit public code rather than a misleading fallback.
    raise error


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
