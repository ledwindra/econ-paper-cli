"""Application service and output rendering for the offline single-paper analysis CLI command."""

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from econ_paper_cli.adapters.filesystem import VerificationError
from econ_paper_cli.adapters.llama_cpp import (
    LlamaCppConfig,
    LlamaCppConfigurationError,
    LlamaCppGenerator,
    LlamaCppReadinessError,
)
from econ_paper_cli.adapters.pypdf_extractor import PyPDFExtractor
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    PDFQualitySettings,
    PDFQualityValidationError,
    PDFSectionSettings,
    PDFSectionValidationError,
    ResearchQuestionSettings,
    ResearchQuestionValidationError,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStatus,
    SinglePaperAnalysisValidationError,
)
from econ_paper_cli.protocols.generation import Generator
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor
from econ_paper_cli.protocols.storage import StorageBackend
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper
from econ_paper_cli.services.single_paper_analysis_storage import (
    save_single_paper_analysis_result,
)


class CLIExitCode:
    """Stable exit codes for the single-paper analysis CLI command."""

    SUCCESS = 0
    HALTED_OR_UNAVAILABLE = 1
    TYPED_FAILURE_OR_CONFIG_ERROR = 2
    UNEXPECTED_ERROR = 3


@dataclass(frozen=True, slots=True)
class AnalyzeCommandOptions:
    """Parsed options for the single-paper analysis CLI command."""

    pdf_path: Path
    executable_path: Path
    model_path: Path
    model_id: str
    model_bytes: int
    model_checksum: str
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | str | None = None
    quality_policy_version: str | None = None
    section_policy_version: str | None = None
    research_question_policy_version: str | None = None
    single_paper_policy_version: str | None = None


def format_analysis_record_output(
    record: SinglePaperAnalysisRecord,
    db_path: Path | str,
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

    return "\n".join(lines)


def run_single_paper_analysis_command(
    options: AnalyzeCommandOptions,
    extractor: PDFExtractor | None = None,
    generator: Generator | None = None,
    storage: StorageBackend | None = None,
) -> int:
    """Execute single-paper analysis from CLI options and persist/render the record."""
    try:
        # 1. Build and validate settings
        try:
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

            settings = SinglePaperAnalysisSettings(
                policy_version=single_policy,
                quality_settings=q_settings,
                section_settings=sec_settings,
                research_question_settings=rq_settings,
            )
        except (
            PDFQualityValidationError,
            PDFSectionValidationError,
            ResearchQuestionValidationError,
            SinglePaperAnalysisValidationError,
            ValueError,
        ) as err:
            sys.stderr.write(f"Configuration error: invalid policy version: {err}\n")
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        target_db_path = (
            options.db_path if options.db_path is not None else get_default_db_path()
        )

        # 2. Initialize storage
        if storage is None:
            db_adapter = SQLiteStorage(target_db_path)
            try:
                db_adapter.initialize()
            except (sqlite3.Error, OSError) as err:
                sys.stderr.write(f"Database initialization failed: {err}\n")
                return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
            storage = db_adapter
        else:
            if hasattr(storage, "initialize"):
                storage.initialize()

        # 3. Generator setup & readiness check
        if generator is None:
            try:
                cfg = LlamaCppConfig(
                    executable_path=options.executable_path,
                    model_path=options.model_path,
                    model_id=options.model_id,
                    model_expected_size_bytes=options.model_bytes,
                    model_sha256=options.model_checksum,
                    threads=options.threads,
                    timeout_seconds=options.timeout
                    if options.timeout is not None
                    else 300.0,
                )
                gen = LlamaCppGenerator(cfg)
                gen.check_readiness()
                generator = gen
            except (
                LlamaCppConfigurationError,
                LlamaCppReadinessError,
                VerificationError,
                ValueError,
                FileNotFoundError,
            ) as err:
                sys.stderr.write(f"Configuration or readiness error: {err}\n")
                return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        if extractor is None:
            extractor = PyPDFExtractor()

        # 4. Run five-stage analysis service
        result = analyze_single_paper(
            options.pdf_path,
            extractor,
            generator,
            settings=settings,
        )

        # 5. Persist record to SQLite
        record = save_single_paper_analysis_result(storage, result, settings=settings)

        # 6. Read back durable record from storage (MUST NOT fall back to transient object)
        durable_record = storage.get_single_paper_analysis(record.analysis_id)
        if durable_record is None:
            raise RuntimeError(
                f"Failed to read back persisted analysis record '{record.analysis_id}' from storage."
            )

        # 7. Render output from durable_record
        output_str = format_analysis_record_output(durable_record, target_db_path)
        sys.stdout.write(f"{output_str}\n")

        # 8. Return exit code based on terminal status
        if durable_record.status is SinglePaperAnalysisStatus.SUCCESS:
            return CLIExitCode.SUCCESS
        if durable_record.status in (
            SinglePaperAnalysisStatus.QUALITY_HALTED,
            SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
        ):
            return CLIExitCode.HALTED_OR_UNAVAILABLE
        if durable_record.status in (
            SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
            SinglePaperAnalysisStatus.EXTRACTION_FAILED,
        ):
            return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

        return CLIExitCode.UNEXPECTED_ERROR

    except Exception as err:
        sys.stderr.write(f"Unexpected internal error: {err}\n")
        return CLIExitCode.UNEXPECTED_ERROR
