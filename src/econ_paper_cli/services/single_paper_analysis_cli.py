"""Application service and output rendering for offline PDF analysis commands."""

import enum
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.filesystem import (
    FileInspectionResult,
    VerificationError,
    inspect_local_file,
)
from econ_paper_cli.adapters.llama_cpp import (
    LlamaCppConfig,
    LlamaCppConfigurationError,
    LlamaCppGenerator,
    LlamaCppReadinessError,
)
from econ_paper_cli.adapters.pypdf_extractor import PyPDFExtractor
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    LibraryPopulationResult,
    LibraryPopulationStatus,
    PDFConversionSettings,
    PDFConversionValidationError,
    PDFQualitySettings,
    PDFQualityValidationError,
    PDFSectionSettings,
    PDFSectionValidationError,
    ResearchQuestionSettings,
    ResearchQuestionValidationError,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisResult,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisValidationError,
)
from econ_paper_cli.domain.errors import IngestionError
from econ_paper_cli.domain.ingestion import IngestionPreflightResult, PreflightCandidate
from econ_paper_cli.domain.pdf_conversion import (
    compute_conversion_paper_id,
    compute_conversion_settings_fingerprint,
)
from econ_paper_cli.domain.pdf_quality import PDFQualityStatus
from econ_paper_cli.domain.single_paper_analysis import compute_analysis_id
from econ_paper_cli.protocols.config import ConfigBackend, ConfigError
from econ_paper_cli.protocols.generation import Generator
from econ_paper_cli.protocols.pdf_extraction import PDFExtractionError, PDFExtractor
from econ_paper_cli.protocols.storage import StorageBackend, StorageConnectionError
from econ_paper_cli.services.analysis_library import (
    prepare_analysis_library,
    prepare_analysis_record,
    prepare_early_section_library,
)
from econ_paper_cli.services.config_resolution import (
    ConfigResolutionError,
    LazyConfigLoader,
    RuntimeModelOverrides,
    identity_fully_specified,
    resolve_db_path,
    resolve_runtime_model_config,
    validate_identity_override_shape,
)
from econ_paper_cli.services.ingestion import (
    discover_pdf_paths,
    run_ingestion_preflight,
)
from econ_paper_cli.services.pdf_quality import assess_pdf_extraction_quality
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.single_paper_analysis import (
    analyze_single_paper,
    build_preflight_failure_result,
)


class CLIExitCode:
    """Stable exit codes for PDF analysis CLI commands."""

    SUCCESS = 0
    HALTED_OR_UNAVAILABLE = 1
    TYPED_FAILURE_OR_CONFIG_ERROR = 2
    UNEXPECTED_ERROR = 3


class _GeneratorSetupError(RuntimeError):
    """Typed boundary error for lazily initialized local generation."""


class BatchOutcomeKind(str, enum.Enum):
    """Outcome category for a single path in a batch analysis run."""

    NEWLY_ANALYZED = "newly_analyzed"
    REUSED = "reused"
    BATCH_DUPLICATE = "batch_duplicate"
    TYPED_TERMINAL = "typed_terminal"
    UNEXPECTED_FAILURE = "unexpected_failure"


@dataclass(frozen=True, slots=True)
class BatchItemOutcome:
    """Outcome for one discovered path in a batch analysis run."""

    path: Path
    kind: BatchOutcomeKind
    record: SinglePaperAnalysisRecord | None = None
    duplicate_of: Path | None = None
    error: str | None = None
    library_result: LibraryPopulationResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("path must be a pathlib.Path.")
        if not isinstance(self.kind, BatchOutcomeKind):
            raise TypeError("kind must be a BatchOutcomeKind.")

        if self.kind is BatchOutcomeKind.BATCH_DUPLICATE:
            if not isinstance(self.duplicate_of, Path):
                raise TypeError(
                    "duplicate_of must be a pathlib.Path for a batch duplicate."
                )
            if (
                self.record is not None
                or self.error is not None
                or self.library_result is not None
            ):
                raise ValueError(
                    "A batch duplicate cannot include a record or error message."
                )
            return

        if self.kind is BatchOutcomeKind.UNEXPECTED_FAILURE:
            if not isinstance(self.error, str) or not self.error.strip():
                raise ValueError(
                    "error is required for an unexpected per-file failure."
                )
            if self.record is not None or self.duplicate_of is not None:
                raise ValueError(
                    "An unexpected failure cannot include a record or duplicate path."
                )
            if (
                self.library_result is not None
                and self.library_result.status is not LibraryPopulationStatus.FAILED
            ):
                raise ValueError(
                    "unexpected failures may only carry a FAILED library outcome."
                )
            return

        if not isinstance(self.record, SinglePaperAnalysisRecord):
            raise TypeError(
                f"record must be a SinglePaperAnalysisRecord for outcome "
                f"'{self.kind.value}'."
            )
        if self.duplicate_of is not None or self.error is not None:
            raise ValueError(
                "A durable-record outcome cannot include duplicate or error details."
            )
        if not isinstance(self.library_result, LibraryPopulationResult):
            raise TypeError("durable-record outcomes require a library_result.")
        is_success = self.record.status is SinglePaperAnalysisStatus.SUCCESS
        if self.kind is BatchOutcomeKind.NEWLY_ANALYZED and not is_success:
            raise ValueError("newly_analyzed requires a successful durable record.")
        if self.kind is BatchOutcomeKind.TYPED_TERMINAL and is_success:
            raise ValueError("typed_terminal requires a non-success durable record.")


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Immutable aggregate result of a directory batch analysis run."""

    items: tuple[BatchItemOutcome, ...]
    total_discovered: int
    unique_checksums: int
    newly_analyzed: int
    reused: int
    batch_duplicates: int
    successes: int
    halted: int
    typed_failures: int
    unexpected_failures: int
    library_stored: int = 0
    library_reused: int = 0
    library_no_usable_sections: int = 0
    library_not_eligible: int = 0
    library_failed: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, BatchItemOutcome) for item in self.items
        ):
            raise TypeError("items must be a tuple of BatchItemOutcome instances.")

        for field_name in (
            "total_discovered",
            "unique_checksums",
            "newly_analyzed",
            "reused",
            "batch_duplicates",
            "successes",
            "halted",
            "typed_failures",
            "unexpected_failures",
            "library_stored",
            "library_reused",
            "library_no_usable_sections",
            "library_not_eligible",
            "library_failed",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(f"{field_name} must be a non-negative integer.")

        expected = {
            "total_discovered": len(self.items),
            "newly_analyzed": sum(
                item.kind
                in (BatchOutcomeKind.NEWLY_ANALYZED, BatchOutcomeKind.TYPED_TERMINAL)
                for item in self.items
            ),
            "reused": sum(item.kind is BatchOutcomeKind.REUSED for item in self.items),
            "batch_duplicates": sum(
                item.kind is BatchOutcomeKind.BATCH_DUPLICATE for item in self.items
            ),
            "successes": sum(
                item.record is not None
                and item.record.status is SinglePaperAnalysisStatus.SUCCESS
                for item in self.items
            ),
            "halted": sum(
                item.record is not None and _is_halted(item.record.status)
                for item in self.items
            ),
            "typed_failures": sum(
                item.record is not None and _is_typed_failure(item.record.status)
                for item in self.items
            ),
            "unexpected_failures": sum(
                item.kind is BatchOutcomeKind.UNEXPECTED_FAILURE for item in self.items
            ),
            "library_stored": _count_library_status(
                self.items, LibraryPopulationStatus.STORED
            ),
            "library_reused": _count_library_status(
                self.items, LibraryPopulationStatus.REUSED
            ),
            "library_no_usable_sections": _count_library_status(
                self.items, LibraryPopulationStatus.NO_USABLE_SECTIONS
            ),
            "library_not_eligible": _count_library_status(
                self.items, LibraryPopulationStatus.NOT_ELIGIBLE
            ),
            "library_failed": _count_library_status(
                self.items, LibraryPopulationStatus.FAILED
            ),
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(
                    f"{field_name} must be {expected_value} for the supplied items."
                )
        if self.unique_checksums > self.total_discovered - self.batch_duplicates:
            raise ValueError(
                "unique_checksums cannot exceed discovered non-duplicate paths."
            )


@dataclass(frozen=True, slots=True)
class AnalyzeCommandOptions:
    """Parsed options for the PDF analysis CLI command."""

    target_path: Path
    executable_path: Path | None = None
    model_path: Path | None = None
    model_id: str | None = None
    model_bytes: int | None = None
    model_checksum: str | None = None
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | str | None = None
    config_path: Path | None = None
    quality_policy_version: str | None = None
    section_policy_version: str | None = None
    research_question_policy_version: str | None = None
    single_paper_policy_version: str | None = None
    conversion_policy_version: str | None = None
    max_passage_characters: int = 1200


def format_analysis_record_output(
    record: SinglePaperAnalysisRecord,
    db_path: Path | str,
    library_result: LibraryPopulationResult | None = None,
) -> str:
    """Render deterministic, inspectable terminal output from a durable analysis record."""
    lines: list[str] = [
        "=== Single-Paper Analysis Record ===",
        f"Analysis ID: {record.analysis_id}",
        f"Source Path: {record.source_path}",
        f"Content Checksum: {record.content_checksum or 'N/A'}",
        f"Database Path: {db_path}",
        f"Status: {record.status.value}",
    ]

    if record.quality_status is not None:
        lines.append(f"Quality Status: {record.quality_status.value}")

    if record.failure_code is not None or record.error_message is not None:
        lines.append(
            f"Failure Code: {record.failure_code.value if record.failure_code else 'N/A'}"
        )
        lines.append(f"Error Message: {record.error_message or 'N/A'}")

    if record.research_question is not None:
        rq = record.research_question
        lines.append("\n--- Research Question ---")
        lines.append(f"Kind: {rq.kind.value}")
        lines.append(f"Question Text: {rq.question_text or 'N/A'}")
        sec_used_str = (
            ", ".join(sk.value for sk in rq.sections_used)
            if rq.sections_used
            else "None"
        )
        lines.append(f"Sections Used: {sec_used_str}")

    if record.evidence:
        lines.append("\n--- Evidence Excerpts ---")
        for ev in record.evidence:
            lines.append(
                f"[{ev.ordinal_position}] section={ev.section_kind.value}, page={ev.page_number}, "
                f"span=[{ev.start_character_offset}, {ev.end_character_offset}]"
            )
            lines.append(f'  Excerpt: "{ev.excerpt_text}"')

    has_any_warnings = (
        record.quality_warnings
        or record.section_warnings
        or record.research_question_warnings
        or record.warnings
    )
    if has_any_warnings:
        lines.append("\n--- Warnings ---")
        for qw in record.quality_warnings:
            pg_str = f" (pages: {list(qw.page_numbers)})" if qw.page_numbers else ""
            lines.append(f"[quality] {qw.code.value}: {qw.message}{pg_str}")
        for sw in record.section_warnings:
            pg_str = f" (pages: {list(sw.page_numbers)})" if sw.page_numbers else ""
            lines.append(f"[section] {sw.code.value}: {sw.message}{pg_str}")
        for rqw in record.research_question_warnings:
            det_str = f" - {rqw.details}" if rqw.details else ""
            lines.append(
                f"[research_question] {rqw.code.value}: {rqw.message}{det_str}"
            )
        for ow in record.warnings:
            det_str = f" - {ow.details}" if ow.details else ""
            lines.append(f"[orchestration] {ow.code.value}{det_str}")

    if library_result is not None:
        lines.extend(
            (
                "\n--- Early-Section Library ---",
                f"Outcome: {library_result.status.value}",
                f"Paper ID: {library_result.paper_id or 'N/A'}",
                "Conversion Fingerprint: "
                f"{library_result.conversion_fingerprint or 'N/A'}",
                f"Passage Count: {library_result.passage_count}",
            )
        )

    return "\n".join(lines)


def format_batch_result_output(result: BatchResult, db_path: Path | str) -> str:
    """Render deterministic batch analysis output for all discovered paths."""
    sections: list[str] = []

    for item in result.items:
        header = f"=== [{item.kind.value}] {item.path} ==="
        if item.kind == BatchOutcomeKind.BATCH_DUPLICATE:
            sections.append(f"{header}\nDuplicate of: {item.duplicate_of}")
        elif item.kind == BatchOutcomeKind.UNEXPECTED_FAILURE:
            sections.append(f"{header}\nError: {item.error}")
        elif item.record is not None:
            record_str = format_analysis_record_output(
                item.record, db_path, item.library_result
            )
            sections.append(f"{header}\n{record_str}")
        else:
            sections.append(header)

    summary_lines = [
        "=== Batch Summary ===",
        f"Total discovered:    {result.total_discovered}",
        f"Unique checksums:    {result.unique_checksums}",
        f"Newly analyzed:      {result.newly_analyzed}",
        f"Reused (exact):      {result.reused}",
        f"Batch duplicates:    {result.batch_duplicates}",
        f"Successes:           {result.successes}",
        f"Halted/unavailable:  {result.halted}",
        f"Typed failures:      {result.typed_failures}",
        f"Unexpected failures: {result.unexpected_failures}",
        f"Library stored:      {result.library_stored}",
        f"Library reused:      {result.library_reused}",
        f"Library no sections: {result.library_no_usable_sections}",
        f"Library ineligible:  {result.library_not_eligible}",
        f"Library failed:      {result.library_failed}",
    ]
    sections.append("\n".join(summary_lines))
    return "\n\n".join(sections)


def _build_settings(options: AnalyzeCommandOptions) -> SinglePaperAnalysisSettings:
    """Build and validate SinglePaperAnalysisSettings from CLI options."""
    q_settings = (
        PDFQualitySettings(policy_version=options.quality_policy_version)
        if options.quality_policy_version
        else DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.quality_settings
    )
    sec_settings = (
        PDFSectionSettings(policy_version=options.section_policy_version)
        if options.section_policy_version
        else DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.section_settings
    )
    rq_settings = (
        ResearchQuestionSettings(
            policy_version=options.research_question_policy_version
        )
        if options.research_question_policy_version
        else DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.research_question_settings
    )
    single_policy = (
        options.single_paper_policy_version
        if options.single_paper_policy_version
        else DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS.policy_version
    )
    return SinglePaperAnalysisSettings(
        policy_version=single_policy,
        quality_settings=q_settings,
        section_settings=sec_settings,
        research_question_settings=rq_settings,
    )


def _build_conversion_settings(options: AnalyzeCommandOptions) -> PDFConversionSettings:
    """Build and validate conversion settings from command options."""
    return PDFConversionSettings(
        policy_version=(
            options.conversion_policy_version
            or DEFAULT_PDF_CONVERSION_SETTINGS.policy_version
        ),
        max_passage_characters=options.max_passage_characters,
    )


def _count_library_status(
    items: tuple[BatchItemOutcome, ...], status: LibraryPopulationStatus
) -> int:
    return sum(
        item.library_result is not None and item.library_result.status is status
        for item in items
    )


def _batch_exit_code(result: BatchResult) -> int:
    """Derive aggregate exit code from batch result."""
    if result.unexpected_failures > 0:
        return CLIExitCode.UNEXPECTED_ERROR
    if result.typed_failures > 0:
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    if result.halted > 0:
        return CLIExitCode.HALTED_OR_UNAVAILABLE
    return CLIExitCode.SUCCESS


def _is_halted(status: SinglePaperAnalysisStatus) -> bool:
    return status in (
        SinglePaperAnalysisStatus.QUALITY_HALTED,
        SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
    )


def _is_typed_failure(status: SinglePaperAnalysisStatus) -> bool:
    return status in (
        SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
        SinglePaperAnalysisStatus.EXTRACTION_FAILED,
    )


def _persist_analysis_only(
    pdf_path: Path,
    result: SinglePaperAnalysisResult,
    storage: StorageBackend,
    settings: SinglePaperAnalysisSettings,
    *,
    timestamp: str,
) -> BatchItemOutcome:
    """Persist an analysis-only terminal result with explicit timing."""
    record = prepare_analysis_record(result, settings, timestamp=timestamp)
    storage.save_single_paper_analysis(record)
    durable = storage.get_single_paper_analysis(record.analysis_id)
    if durable is None:
        raise RuntimeError(
            f"Failed to read back persisted analysis record '{record.analysis_id}' from storage."
        )
    kind = (
        BatchOutcomeKind.NEWLY_ANALYZED
        if durable.status is SinglePaperAnalysisStatus.SUCCESS
        else BatchOutcomeKind.TYPED_TERMINAL
    )
    return BatchItemOutcome(
        path=pdf_path,
        kind=kind,
        record=durable,
        library_result=LibraryPopulationResult(LibraryPopulationStatus.NOT_ELIGIBLE),
    )


def _single_candidate_preflight(
    candidate: PreflightCandidate,
) -> IngestionPreflightResult:
    """Project one directory candidate into the single-paper preflight contract."""
    return IngestionPreflightResult(
        target_path=candidate.source_path,
        candidates=(candidate,),
        new_candidate_count=1,
        stored_candidate_count=0,
        batch_duplicate_count=0,
        total_candidate_count=1,
    )


def _inspection_from_candidate(candidate: PreflightCandidate) -> FileInspectionResult:
    return FileInspectionResult(
        file_path=candidate.source_path,
        size_bytes=candidate.file_size_bytes,
        sha256=candidate.content_checksum,
    )


def _analysis_is_library_eligible(record: SinglePaperAnalysisRecord) -> bool:
    return (
        record.content_checksum is not None
        and record.quality_status
        not in (PDFQualityStatus.LIKELY_NEEDS_OCR, PDFQualityStatus.UNUSABLE, None)
        and record.status
        not in (
            SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            SinglePaperAnalysisStatus.EXTRACTION_FAILED,
            SinglePaperAnalysisStatus.QUALITY_HALTED,
        )
    )


def _process_candidate(
    pdf_path: Path,
    candidate: PreflightCandidate,
    extractor: PDFExtractor,
    generator_provider: Callable[[], Generator],
    storage: StorageBackend,
    analysis_settings: SinglePaperAnalysisSettings,
    conversion_settings: PDFConversionSettings,
    timestamp_provider: Callable[[], str],
) -> BatchItemOutcome:
    """Reuse, backfill, or newly analyze one inspected candidate."""
    inspection = _inspection_from_candidate(candidate)
    checksum = candidate.content_checksum
    analysis_id = compute_analysis_id(checksum, analysis_settings, pdf_path)
    paper_id = compute_conversion_paper_id(checksum)
    conversion_fingerprint = compute_conversion_settings_fingerprint(
        conversion_settings
    )
    existing_analysis = storage.get_single_paper_analysis(analysis_id)
    existing_library = storage.get_early_section_record(paper_id)

    if existing_analysis is not None:
        if existing_analysis.content_checksum != checksum:
            raise RuntimeError(
                f"Stored analysis '{analysis_id}' does not match current PDF checksum."
            )
        if not _analysis_is_library_eligible(existing_analysis):
            return BatchItemOutcome(
                path=pdf_path,
                kind=BatchOutcomeKind.REUSED,
                record=existing_analysis,
                library_result=LibraryPopulationResult(
                    LibraryPopulationStatus.NOT_ELIGIBLE
                ),
            )
        if not existing_analysis.sections:
            return BatchItemOutcome(
                path=pdf_path,
                kind=BatchOutcomeKind.REUSED,
                record=existing_analysis,
                library_result=LibraryPopulationResult(
                    LibraryPopulationStatus.NO_USABLE_SECTIONS,
                    paper_id=paper_id,
                    conversion_fingerprint=conversion_fingerprint,
                ),
            )
        if (
            existing_library is not None
            and existing_library.settings_fingerprint == conversion_fingerprint
        ):
            return BatchItemOutcome(
                path=pdf_path,
                kind=BatchOutcomeKind.REUSED,
                record=existing_analysis,
                library_result=LibraryPopulationResult(
                    LibraryPopulationStatus.REUSED,
                    paper_id=paper_id,
                    conversion_fingerprint=conversion_fingerprint,
                    passage_count=len(existing_library.passages),
                ),
            )
        try:
            extraction = extractor.extract(candidate.source_path)
        except PDFExtractionError:
            return BatchItemOutcome(
                path=pdf_path,
                kind=BatchOutcomeKind.REUSED,
                record=existing_analysis,
                library_result=LibraryPopulationResult(
                    LibraryPopulationStatus.NOT_ELIGIBLE
                ),
            )
        quality = assess_pdf_extraction_quality(
            extraction, settings=analysis_settings.quality_settings
        )
        if quality.status in (
            PDFQualityStatus.LIKELY_NEEDS_OCR,
            PDFQualityStatus.UNUSABLE,
        ):
            library_result = LibraryPopulationResult(
                LibraryPopulationStatus.NOT_ELIGIBLE
            )
        else:
            sections = detect_pdf_sections(
                extraction, settings=analysis_settings.section_settings
            )
            prepared = prepare_early_section_library(
                inspection,
                extraction,
                sections,
                conversion_settings,
                timestamp=timestamp_provider(),
            )
            library_result = prepared.library_result
            if prepared.library_record is not None:
                storage.save_early_section_record(prepared.library_record)
                durable_library = storage.get_early_section_record(paper_id)
                if durable_library is None:
                    raise RuntimeError(
                        f"Failed to read back early-section record '{paper_id}'."
                    )
                library_result = LibraryPopulationResult(
                    LibraryPopulationStatus.STORED,
                    paper_id=paper_id,
                    conversion_fingerprint=durable_library.settings_fingerprint,
                    passage_count=len(durable_library.passages),
                )
        return BatchItemOutcome(
            path=pdf_path,
            kind=BatchOutcomeKind.REUSED,
            record=existing_analysis,
            library_result=library_result,
        )

    result = analyze_single_paper(
        pdf_path,
        extractor,
        generator_provider(),
        settings=analysis_settings,
        preflight_result=_single_candidate_preflight(candidate),
    )
    timestamp = timestamp_provider()
    prepared = prepare_analysis_library(
        inspection,
        result,
        analysis_settings,
        conversion_settings,
        timestamp=timestamp,
    )
    if prepared.library_record is None:
        storage.save_single_paper_analysis(prepared.analysis_record)
    else:
        storage.save_analysis_and_early_section(
            prepared.analysis_record, prepared.library_record
        )
    durable = storage.get_single_paper_analysis(prepared.analysis_record.analysis_id)
    if durable is None:
        raise RuntimeError(
            f"Failed to read back persisted analysis record "
            f"'{prepared.analysis_record.analysis_id}' from storage."
        )
    kind = (
        BatchOutcomeKind.NEWLY_ANALYZED
        if durable.status is SinglePaperAnalysisStatus.SUCCESS
        else BatchOutcomeKind.TYPED_TERMINAL
    )
    return BatchItemOutcome(
        path=pdf_path,
        kind=kind,
        record=durable,
        library_result=prepared.library_result,
    )


def run_batch_analysis(
    options: AnalyzeCommandOptions,
    extractor: PDFExtractor,
    generator_provider: Callable[[], Generator],
    storage: StorageBackend,
    settings: SinglePaperAnalysisSettings,
    conversion_settings: PDFConversionSettings,
    file_inspector: Callable[[Path], FileInspectionResult] = inspect_local_file,
    timestamp_provider: Callable[[], str] = lambda: datetime.now(
        timezone.utc
    ).isoformat(),
) -> BatchResult:
    """Run deterministic batch analysis over a directory target.

    Discovers paths first, then performs checksum inspection and all storage work
    inside the per-candidate isolation boundary.
    """
    pdf_paths = discover_pdf_paths(options.target_path)

    items: list[BatchItemOutcome] = []
    newly_analyzed = 0
    reused = 0
    batch_duplicates = 0
    successes = 0
    halted = 0
    typed_failures = 0
    unexpected_failures = 0
    seen_checksums: dict[str, Path] = {}
    for pdf_path in pdf_paths:
        try:
            preflight = run_ingestion_preflight(
                pdf_path,
                file_inspector=file_inspector,
            )
        except IngestionError as err:
            try:
                result = build_preflight_failure_result(
                    pdf_path,
                    err,
                    settings=settings,
                )
                outcome = _persist_analysis_only(
                    pdf_path,
                    result,
                    storage,
                    settings,
                    timestamp=timestamp_provider(),
                )
                items.append(outcome)
                newly_analyzed += 1
                typed_failures += 1
            except Exception as internal_err:
                items.append(
                    BatchItemOutcome(
                        path=pdf_path,
                        kind=BatchOutcomeKind.UNEXPECTED_FAILURE,
                        error=f"{type(internal_err).__name__}: {internal_err}",
                    )
                )
                unexpected_failures += 1
            continue
        except _GeneratorSetupError:
            raise
        except Exception as err:
            items.append(
                BatchItemOutcome(
                    path=pdf_path,
                    kind=BatchOutcomeKind.UNEXPECTED_FAILURE,
                    error=f"{type(err).__name__}: {err}",
                    library_result=LibraryPopulationResult(
                        LibraryPopulationStatus.FAILED,
                        error_message=f"{type(err).__name__}: {err}",
                    ),
                )
            )
            unexpected_failures += 1
            continue

        candidate = preflight.candidates[0]
        checksum = candidate.content_checksum

        duplicate_of = seen_checksums.get(checksum)
        if duplicate_of is not None:
            items.append(
                BatchItemOutcome(
                    path=pdf_path,
                    kind=BatchOutcomeKind.BATCH_DUPLICATE,
                    duplicate_of=duplicate_of,
                )
            )
            batch_duplicates += 1
            continue
        seen_checksums[checksum] = pdf_path

        try:
            outcome = _process_candidate(
                pdf_path,
                candidate,
                extractor,
                generator_provider,
                storage,
                settings,
                conversion_settings,
                timestamp_provider,
            )
            items.append(outcome)
            if outcome.kind is BatchOutcomeKind.REUSED:
                reused += 1
            else:
                newly_analyzed += 1
            if outcome.record is not None and _is_typed_failure(outcome.record.status):
                typed_failures += 1
            elif outcome.record is not None:
                if outcome.record.status is SinglePaperAnalysisStatus.SUCCESS:
                    successes += 1
                elif _is_halted(outcome.record.status):
                    halted += 1

        except _GeneratorSetupError:
            raise
        except Exception as err:
            items.append(
                BatchItemOutcome(
                    path=pdf_path,
                    kind=BatchOutcomeKind.UNEXPECTED_FAILURE,
                    error=f"{type(err).__name__}: {err}",
                    library_result=LibraryPopulationResult(
                        LibraryPopulationStatus.FAILED,
                        error_message=f"{type(err).__name__}: {err}",
                    ),
                )
            )
            unexpected_failures += 1

    return BatchResult(
        items=tuple(items),
        total_discovered=len(pdf_paths),
        unique_checksums=len(seen_checksums),
        newly_analyzed=newly_analyzed,
        reused=reused,
        batch_duplicates=batch_duplicates,
        successes=successes,
        halted=halted,
        typed_failures=typed_failures,
        unexpected_failures=unexpected_failures,
        library_stored=_count_library_status(
            tuple(items), LibraryPopulationStatus.STORED
        ),
        library_reused=_count_library_status(
            tuple(items), LibraryPopulationStatus.REUSED
        ),
        library_no_usable_sections=_count_library_status(
            tuple(items), LibraryPopulationStatus.NO_USABLE_SECTIONS
        ),
        library_not_eligible=_count_library_status(
            tuple(items), LibraryPopulationStatus.NOT_ELIGIBLE
        ),
        library_failed=_count_library_status(
            tuple(items), LibraryPopulationStatus.FAILED
        ),
    )


def run_single_paper_analysis_command(
    options: AnalyzeCommandOptions,
    extractor: PDFExtractor | None = None,
    generator: Generator | None = None,
    storage: StorageBackend | None = None,
    config_backend: ConfigBackend | None = None,
    file_inspector: Callable[[Path], FileInspectionResult] = inspect_local_file,
    timestamp_provider: Callable[[], str] = lambda: datetime.now(
        timezone.utc
    ).isoformat(),
) -> int:
    """Execute analysis from CLI options — accepts a single PDF or a directory."""
    try:
        # 1. Build and validate settings
        try:
            settings = _build_settings(options)
            conversion_settings = _build_conversion_settings(options)
        except (
            PDFConversionValidationError,
            PDFQualityValidationError,
            PDFSectionValidationError,
            ResearchQuestionValidationError,
            SinglePaperAnalysisValidationError,
            ValueError,
        ) as err:
            sys.stderr.write(f"Configuration error: invalid policy version: {err}\n")
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        # Durable configuration is read lazily and at most once below; it is
        # never mutated by analyze, and reuse/backfill paths never force a
        # load merely because a config_backend was supplied.
        overrides = RuntimeModelOverrides(
            executable_path=options.executable_path,
            model_path=options.model_path,
            model_id=options.model_id,
            model_bytes=options.model_bytes,
            model_checksum=options.model_checksum,
            threads=options.threads,
            timeout=options.timeout,
            db_path=Path(options.db_path) if options.db_path is not None else None,
        )
        try:
            validate_identity_override_shape(overrides)
        except ConfigResolutionError as err:
            sys.stderr.write(f"Configuration error: {err}\n")
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        lazy_config = LazyConfigLoader(
            config_backend or JSONConfigStorage(options.config_path)
        )
        if overrides.db_path is not None:
            target_db_path = Path(overrides.db_path)
        else:
            try:
                target_db_path = resolve_db_path(overrides, lazy_config.get())
            except ConfigError as err:
                sys.stderr.write(f"Configuration error: {err}\n")
                return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        # 2. Initialize storage
        if storage is None:
            db_adapter = SQLiteStorage(target_db_path)
            try:
                db_adapter.initialize()
            except StorageConnectionError as err:
                sys.stderr.write(f"Database connection error: {err}\n")
                return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
            storage = db_adapter
        else:
            if hasattr(storage, "initialize"):
                try:
                    storage.initialize()
                except StorageConnectionError as err:
                    sys.stderr.write(f"Database connection error: {err}\n")
                    return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        if extractor is None:
            extractor = PyPDFExtractor()

        cached_generator = generator

        def get_generator() -> Generator:
            """Construct and verify the local generator only when analysis needs it."""
            nonlocal cached_generator
            if cached_generator is not None:
                return cached_generator
            try:
                # A fully-specified CLI identity never forces a load. But if
                # a load already happened for another reason (e.g. resolving
                # --db-path), its optional threads/timeout defaults still
                # apply per CLI > config > default precedence.
                needed_config = (
                    lazy_config.peek()
                    if identity_fully_specified(overrides)
                    else lazy_config.get()
                )
                resolved = resolve_runtime_model_config(overrides, needed_config)
                cfg = LlamaCppConfig(
                    executable_path=resolved.executable_path,
                    model_path=resolved.model_path,
                    model_id=resolved.model_id,
                    model_expected_size_bytes=resolved.model_bytes,
                    model_sha256=resolved.model_checksum,
                    threads=resolved.threads,
                    timeout_seconds=resolved.timeout_seconds,
                )
                gen = LlamaCppGenerator(cfg)
                gen.check_readiness()
                cached_generator = gen
            except (
                ConfigError,
                ConfigResolutionError,
                LlamaCppConfigurationError,
                LlamaCppReadinessError,
                VerificationError,
                ValueError,
                FileNotFoundError,
            ) as err:
                raise _GeneratorSetupError(str(err)) from err
            return cached_generator

        # 4a. Directory mode — batch analysis
        if options.target_path.is_dir():
            try:
                batch = run_batch_analysis(
                    options,
                    extractor,
                    get_generator,
                    storage,
                    settings,
                    conversion_settings,
                    file_inspector=file_inspector,
                    timestamp_provider=timestamp_provider,
                )
            except IngestionError as err:
                sys.stderr.write(f"Input preflight error: {err}\n")
                return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
            except _GeneratorSetupError as err:
                sys.stderr.write(f"Configuration or readiness error: {err}\n")
                return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
            output_str = format_batch_result_output(batch, target_db_path)
            sys.stdout.write(f"{output_str}\n")
            return _batch_exit_code(batch)

        try:
            preflight = run_ingestion_preflight(
                options.target_path, file_inspector=file_inspector
            )
        except IngestionError as err:
            result = build_preflight_failure_result(
                options.target_path, err, settings=settings
            )
            outcome = _persist_analysis_only(
                options.target_path,
                result,
                storage,
                settings,
                timestamp=timestamp_provider(),
            )
        else:
            outcome = _process_candidate(
                options.target_path,
                preflight.candidates[0],
                extractor,
                get_generator,
                storage,
                settings,
                conversion_settings,
                timestamp_provider,
            )

        if outcome.record is None:
            raise RuntimeError("Single-file analysis did not produce a durable record.")
        durable_record = outcome.record
        output_str = format_analysis_record_output(
            durable_record, target_db_path, outcome.library_result
        )
        sys.stdout.write(f"{output_str}\n")

        # 8. Return exit code based on terminal status
        if durable_record.status is SinglePaperAnalysisStatus.SUCCESS:
            return CLIExitCode.SUCCESS
        if _is_halted(durable_record.status):
            return CLIExitCode.HALTED_OR_UNAVAILABLE
        if _is_typed_failure(durable_record.status):
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        return CLIExitCode.UNEXPECTED_ERROR

    except _GeneratorSetupError as err:
        sys.stderr.write(f"Configuration or readiness error: {err}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    except Exception as err:
        sys.stderr.write(f"Unexpected internal error: {err}\n")
        return CLIExitCode.UNEXPECTED_ERROR
