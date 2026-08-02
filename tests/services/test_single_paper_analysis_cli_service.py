"""Service integration tests for single-paper analysis CLI command execution and rendering."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from econ_paper_cli.adapters.llama_cpp import LlamaCppReadinessError
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    PDFDocumentMetadata,
    PDFExtractionResult,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
)
from econ_paper_cli.domain.pdf_extraction import ExtractedPDFPage
from econ_paper_cli.protocols.generation import (
    AbstentionReason,
    Citation,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.protocols.pdf_extraction import (
    PDFExtractor,
    PDFParserError,
)
from econ_paper_cli.protocols.storage import (
    StorageConnectionError,
    StorageMigrationError,
)
from econ_paper_cli.services.single_paper_analysis_cli import (
    AnalyzeCommandOptions,
    CLIExitCode,
    run_single_paper_analysis_command,
)


class FakePDFExtractor(PDFExtractor):
    """Fake PDF extractor for CLI service tests."""

    def __init__(
        self,
        pages_text: list[str] | None = None,
        raise_error: Exception | None = None,
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
        meta = PDFDocumentMetadata(title="Test CLI Paper")
        return PDFExtractionResult(
            source_path=pdf_path.resolve(),
            pages=pages,
            page_count=len(pages),
            metadata=meta,
            extraction_method="fake_extractor",
            parser_version="1.0.0",
        )


class FailingPDFExtractor(PDFExtractor):
    """PDF Extractor that raises PDFParserError."""

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
        raise PDFParserError(pdf_path, RuntimeError("PDF file corrupt or unreadable."))


class FakeGenerator(Generator):
    """Fake generator for CLI service tests."""

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


class ModifyingStorageWrapper(SQLiteStorage):
    """Storage double that modifies the retrieved record to prove rendering uses storage read-back."""

    def get_single_paper_analysis(
        self, analysis_id: str
    ) -> SinglePaperAnalysisRecord | None:
        record = super().get_single_paper_analysis(analysis_id)
        if record is None:
            return None
        new_rq = (
            SinglePaperAnalysisQuestionRecord(
                kind=record.research_question.kind,
                question_text="MODIFIED FROM STORAGE READ-BACK",
                sections_used=record.research_question.sections_used,
            )
            if record.research_question
            else None
        )
        return SinglePaperAnalysisRecord(
            analysis_id=record.analysis_id,
            source_path=record.source_path,
            content_checksum=record.content_checksum,
            status=record.status,
            completed_stages=record.completed_stages,
            failed_stage=record.failed_stage,
            skipped_stages=record.skipped_stages,
            failure_code=record.failure_code,
            error_message=record.error_message,
            quality_status=record.quality_status,
            settings=record.settings,
            settings_fingerprint=record.settings_fingerprint,
            quality_warnings=record.quality_warnings,
            section_warnings=record.section_warnings,
            research_question_warnings=record.research_question_warnings,
            warnings=record.warnings,
            sections=record.sections,
            research_question=new_rq,
            evidence=record.evidence,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class MissingReadBackStorageWrapper(SQLiteStorage):
    """Storage double that returns None on read-back."""

    def get_single_paper_analysis(
        self, analysis_id: str
    ) -> SinglePaperAnalysisRecord | None:
        return None


class FailingConnectionStorageWrapper(SQLiteStorage):
    """Storage double that raises StorageConnectionError on initialize."""

    def initialize(self) -> None:
        raise StorageConnectionError("Database path unwritable or connection failed.")


class FailingMigrationStorageWrapper(SQLiteStorage):
    """Storage double that raises StorageMigrationError on initialize."""

    def initialize(self) -> None:
        raise StorageMigrationError("Migration v3 failed due to schema corruption.")


def _create_valid_pdf_file(tmp_path: Path, filename: str = "paper.pdf") -> Path:
    path = (tmp_path / filename).resolve()
    path.write_bytes(b"%PDF-1.4 synthetic content for CLI service test")
    return path


def _make_options(
    pdf_path: Path,
    tmp_path: Path,
    db_path: Path | None = None,
    executable_path: Path | None = None,
    model_path: Path | None = None,
    model_bytes: int = 11,
    model_checksum: str = "b" * 64,
) -> AnalyzeCommandOptions:
    dummy_exe = executable_path or (tmp_path / "llama-cli")
    if executable_path is None:
        dummy_exe.write_bytes(b"dummy")
    dummy_model = model_path or (tmp_path / "model.gguf")
    if model_path is None:
        dummy_model.write_bytes(b"dummy_model")

    return AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=dummy_exe,
        model_path=dummy_model,
        model_id="test-model",
        model_bytes=model_bytes,
        model_checksum=model_checksum,
        db_path=db_path or (tmp_path / "test.db"),
    )


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


def test_run_single_paper_analysis_command_success_exact_rendering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "analysis.db"
    opts = _make_options(pdf_path, tmp_path, db_path=db_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)
    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.SUCCESS
    out = capsys.readouterr().out
    durable_rec = storage.list_single_paper_analyses()[0]
    expected_exact_output = (
        "=== Single-Paper Analysis Record ===\n"
        f"Analysis ID: {durable_rec.analysis_id}\n"
        f"Source Path: {pdf_path}\n"
        f"Content Checksum: {durable_rec.content_checksum}\n"
        f"Database Path: {db_path}\n"
        "Status: success\n"
        "Quality Status: usable_with_warnings\n\n"
        "--- Research Question ---\n"
        "Kind: explicit\n"
        "Question Text: What is the impact of trade policy?\n"
        "Sections Used: abstract\n\n"
        "--- Evidence Excerpts ---\n"
        "[0] section=abstract, page=1, span=[9, 34]\n"
        '  Excerpt: "We evaluate trade policy."\n\n'
        "--- Warnings ---\n"
        "[quality] very_low_text_volume: The document contains very little extracted text. Confirm that extraction captured the intended content.\n"
        "[quality] sparse_pages: Some non-empty pages contain unusually little text. Inspect the listed pages for extraction problems. (pages: [1, 2])\n"
        "[section] missing_next_section_boundary: Introduction heading was detected, but no subsequent top-level section heading was found. Introduction extends to the end of the extracted text.\n"
    )
    assert out == expected_exact_output


def test_run_single_paper_analysis_command_quality_halted_exact_rendering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "quality_halted.db"
    opts = _make_options(pdf_path, tmp_path, db_path=db_path)

    extractor = FakePDFExtractor(pages_text=["", "   \n  "])
    generator = FakeGenerator()
    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.HALTED_OR_UNAVAILABLE
    out = capsys.readouterr().out
    durable_rec = storage.list_single_paper_analyses()[0]
    expected_exact_output = (
        "=== Single-Paper Analysis Record ===\n"
        f"Analysis ID: {durable_rec.analysis_id}\n"
        f"Source Path: {pdf_path}\n"
        f"Content Checksum: {durable_rec.content_checksum}\n"
        f"Database Path: {db_path}\n"
        "Status: quality_halted\n"
        "Quality Status: likely_needs_ocr\n\n"
        "--- Warnings ---\n"
        "[quality] all_pages_empty: No extractable text was found on any page. Run local OCR or inspect the document manually. (pages: [1, 2])\n"
        "[orchestration] quality_halted - Extraction quality status is likely_needs_ocr.\n"
    )
    assert out == expected_exact_output


def test_run_single_paper_analysis_command_question_extraction_halted_exact_rendering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "question_halted.db"
    opts = _make_options(pdf_path, tmp_path, db_path=db_path)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text="", abstained=True)
    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.HALTED_OR_UNAVAILABLE
    out = capsys.readouterr().out
    durable_rec = storage.list_single_paper_analyses()[0]
    expected_exact_output = (
        "=== Single-Paper Analysis Record ===\n"
        f"Analysis ID: {durable_rec.analysis_id}\n"
        f"Source Path: {pdf_path}\n"
        f"Content Checksum: {durable_rec.content_checksum}\n"
        f"Database Path: {db_path}\n"
        "Status: question_extraction_halted\n"
        "Quality Status: usable_with_warnings\n\n"
        "--- Research Question ---\n"
        "Kind: unavailable\n"
        "Question Text: N/A\n"
        "Sections Used: None\n\n"
        "--- Warnings ---\n"
        "[quality] very_low_text_volume: The document contains very little extracted text. Confirm that extraction captured the intended content.\n"
        "[quality] sparse_pages: Some non-empty pages contain unusually little text. Inspect the listed pages for extraction problems. (pages: [1, 2])\n"
        "[section] missing_next_section_boundary: Introduction heading was detected, but no subsequent top-level section heading was found. Introduction extends to the end of the extracted text.\n"
        "[research_question] model_abstained: Model abstained from generating a research question response due to insufficient evidence.\n"
        "[orchestration] question_extraction_halted\n"
    )
    assert out == expected_exact_output


def test_run_single_paper_analysis_command_preflight_failed_exact_rendering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    non_existent_pdf = tmp_path / "non_existent.pdf"
    db_path = tmp_path / "preflight_failed.db"
    opts = _make_options(non_existent_pdf, tmp_path, db_path=db_path)

    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=FakeGenerator(),
        storage=storage,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    out = capsys.readouterr().out
    durable_rec = storage.list_single_paper_analyses()[0]
    expected_exact_output = (
        "=== Single-Paper Analysis Record ===\n"
        f"Analysis ID: {durable_rec.analysis_id}\n"
        f"Source Path: {non_existent_pdf}\n"
        "Content Checksum: N/A\n"
        f"Database Path: {db_path}\n"
        "Status: preflight_failed\n"
        "Failure Code: path_not_found\n"
        f"Error Message: Target path for ingestion does not exist: '{non_existent_pdf}'.\n"
    )
    assert out == expected_exact_output


def test_run_single_paper_analysis_command_extraction_failed_exact_rendering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "extraction_failed.db"
    opts = _make_options(pdf_path, tmp_path, db_path=db_path)

    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts,
        extractor=FailingPDFExtractor(),
        generator=FakeGenerator(),
        storage=storage,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    out = capsys.readouterr().out
    durable_rec = storage.list_single_paper_analyses()[0]
    expected_exact_output = (
        "=== Single-Paper Analysis Record ===\n"
        f"Analysis ID: {durable_rec.analysis_id}\n"
        f"Source Path: {pdf_path}\n"
        f"Content Checksum: {durable_rec.content_checksum}\n"
        f"Database Path: {db_path}\n"
        "Status: extraction_failed\n"
        "Failure Code: pdf_parser_error\n"
        f"Error Message: PDF parser failed for '{pdf_path}': PDF file corrupt or unreadable..\n"
    )
    assert out == expected_exact_output


def test_database_connection_error_returns_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    opts = _make_options(pdf_path, tmp_path)

    storage = FailingConnectionStorageWrapper(":memory:")

    exit_code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=FakeGenerator(),
        storage=storage,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "Database connection error:" in err


def test_database_migration_error_returns_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    opts = _make_options(pdf_path, tmp_path)

    storage = FailingMigrationStorageWrapper(":memory:")

    exit_code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=FakeGenerator(),
        storage=storage,
    )

    assert exit_code == CLIExitCode.UNEXPECTED_ERROR
    err = capsys.readouterr().err
    assert "Unexpected internal error:" in err
    assert "Migration v3 failed" in err


def test_run_single_paper_analysis_command_uses_read_back_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "readback.db"
    opts = _make_options(pdf_path, tmp_path, db_path=db_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)
    storage = ModifyingStorageWrapper(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "MODIFIED FROM STORAGE READ-BACK" in out


def test_run_single_paper_analysis_command_missing_read_back_raises_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    opts = _make_options(pdf_path, tmp_path)

    extractor = FakePDFExtractor()
    generator = FakeGenerator()
    storage = MissingReadBackStorageWrapper(":memory:")
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.UNEXPECTED_ERROR
    err = capsys.readouterr().err
    assert "Unexpected internal error:" in err
    assert "Failed to read back persisted analysis record" in err


def test_invalid_policy_versions_return_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    # 1. Invalid quality policy version
    opts1 = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=tmp_path / "exe",
        model_path=tmp_path / "model",
        model_id="id",
        model_bytes=10,
        model_checksum="a" * 64,
        quality_policy_version="pdf-quality-v999",
    )
    assert (
        run_single_paper_analysis_command(opts1)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    assert "Configuration error: invalid policy version" in capsys.readouterr().err

    # 2. Invalid section policy version
    opts2 = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=tmp_path / "exe",
        model_path=tmp_path / "model",
        model_id="id",
        model_bytes=10,
        model_checksum="a" * 64,
        section_policy_version="pdf-sections-v999",
    )
    assert (
        run_single_paper_analysis_command(opts2)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    assert "Configuration error: invalid policy version" in capsys.readouterr().err

    # 3. Invalid research question policy version
    opts3 = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=tmp_path / "exe",
        model_path=tmp_path / "model",
        model_id="id",
        model_bytes=10,
        model_checksum="a" * 64,
        research_question_policy_version="rq-v999",
    )
    assert (
        run_single_paper_analysis_command(opts3)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    assert "Configuration error: invalid policy version" in capsys.readouterr().err

    # 4. Invalid single paper policy version
    opts4 = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=tmp_path / "exe",
        model_path=tmp_path / "model",
        model_id="id",
        model_bytes=10,
        model_checksum="a" * 64,
        single_paper_policy_version="single-paper-analysis-v999",
    )
    assert (
        run_single_paper_analysis_command(opts4)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    assert "Configuration error: invalid policy version" in capsys.readouterr().err


def test_invalid_executable_path_returns_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"dummy")

    opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=tmp_path / "non_existent_exe",
        model_path=model_file,
        model_id="test-model",
        model_bytes=5,
        model_checksum="a" * 64,
    )

    assert (
        run_single_paper_analysis_command(opts)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err


def test_invalid_model_path_returns_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy")

    opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=exe_file,
        model_path=tmp_path / "non_existent_model.gguf",
        model_id="test-model",
        model_bytes=5,
        model_checksum="a" * 64,
    )

    assert (
        run_single_paper_analysis_command(opts)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err


def test_invalid_model_size_returns_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy")
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"dummy_content")

    opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=99999,
        model_checksum="a" * 64,
    )

    assert (
        run_single_paper_analysis_command(opts)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err


def test_invalid_model_checksum_returns_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy")
    model_content = b"dummy_content"
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(model_content)

    opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=len(model_content),
        model_checksum="f" * 64,
    )

    assert (
        run_single_paper_analysis_command(opts)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err


def test_runtime_readiness_failure_returns_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy_exe_bytes")
    model_content = b"dummy_model_bytes"
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(model_content)
    model_sha256 = hashlib.sha256(model_content).hexdigest()

    opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=len(model_content),
        model_checksum=model_sha256,
        db_path=tmp_path / "readiness_test.db",
    )

    def failing_check_readiness(self_gen: object) -> None:
        raise LlamaCppReadinessError("Runtime executable exited with non-zero status.")

    monkeypatch.setattr(
        "econ_paper_cli.adapters.llama_cpp.LlamaCppGenerator.check_readiness",
        failing_check_readiness,
    )

    assert (
        run_single_paper_analysis_command(opts, extractor=FakePDFExtractor())
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err
    assert "Runtime executable exited with non-zero status." in err


def test_command_execution_uses_default_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    mock_default_db = tmp_path / "mock_default.db"
    monkeypatch.setattr(
        "econ_paper_cli.services.single_paper_analysis_cli.get_default_db_path",
        lambda: mock_default_db,
    )

    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy")
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"dummy")

    opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=5,
        model_checksum="a" * 64,
        db_path=None,
    )

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator
    )

    assert exit_code == CLIExitCode.SUCCESS
    assert mock_default_db.exists()
    out = capsys.readouterr().out
    assert f"Database Path: {mock_default_db}" in out


def test_command_execution_uses_explicit_db_path_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    custom_db = tmp_path / "explicit_override.db"
    opts = _make_options(pdf_path, tmp_path, db_path=custom_db)

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator
    )

    assert exit_code == CLIExitCode.SUCCESS
    assert custom_db.exists()
    out = capsys.readouterr().out
    assert f"Database Path: {custom_db}" in out


def test_command_runs_fully_offline_without_network_or_pdf_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden_socket(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Network socket call forbidden during offline analysis!")

    monkeypatch.setattr(socket, "socket", forbidden_socket)

    pdf_path = _create_valid_pdf_file(tmp_path, "authoritative.pdf")
    initial_bytes = pdf_path.read_bytes()
    initial_hash = hashlib.sha256(initial_bytes).hexdigest()

    opts = _make_options(pdf_path, tmp_path)
    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator
    )

    assert exit_code == CLIExitCode.SUCCESS
    assert pdf_path.exists()
    final_bytes = pdf_path.read_bytes()
    assert final_bytes == initial_bytes
    assert hashlib.sha256(final_bytes).hexdigest() == initial_hash


def test_run_single_paper_analysis_command_idempotence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "idempotence.db"
    opts = _make_options(pdf_path, tmp_path, db_path=db_path)

    abs_text = "Abstract\nWe evaluate trade policy."
    exc = "We evaluate trade policy."
    resp_json = _make_success_response_json(abs_text, exc)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text=resp_json)
    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code1 = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )
    rec1 = storage.list_single_paper_analyses()[0]

    exit_code2 = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )
    rec2 = storage.list_single_paper_analyses()[0]

    assert exit_code1 == CLIExitCode.SUCCESS
    assert exit_code2 == CLIExitCode.SUCCESS
    assert rec1 == rec2
    assert rec1.created_at == rec2.created_at
    assert rec1.updated_at == rec2.updated_at
