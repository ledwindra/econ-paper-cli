"""Service integration tests for single-paper analysis CLI command execution and rendering."""

import hashlib
import json
import socket
from pathlib import Path

import pytest

from econ_paper_cli.adapters.bm25 import BM25Retriever
from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import ProcessResult
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    PDFDocumentMetadata,
    PDFExtractionResult,
    SinglePaperAnalysisQuestionRecord,
    SinglePaperAnalysisRecord,
)
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
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
        self.call_count = 0
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
        self.call_count += 1
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
        self.call_count = 0
        self.response_text = response_text
        self.abstained = abstained
        self.raise_error = raise_error

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.call_count += 1
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
        target_path=pdf_path,
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
        "\n--- Early-Section Library ---\n"
        "Outcome: stored\n"
        f"Paper ID: {storage.list_early_section_records()[0].paper.paper_id}\n"
        f"Conversion Fingerprint: {storage.list_early_section_records()[0].settings_fingerprint}\n"
        "Passage Count: 2\n"
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
        "\n--- Early-Section Library ---\n"
        "Outcome: not_eligible\n"
        "Paper ID: N/A\n"
        "Conversion Fingerprint: N/A\n"
        "Passage Count: 0\n"
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
        "\n--- Early-Section Library ---\n"
        "Outcome: stored\n"
        f"Paper ID: {storage.list_early_section_records()[0].paper.paper_id}\n"
        f"Conversion Fingerprint: {storage.list_early_section_records()[0].settings_fingerprint}\n"
        "Passage Count: 2\n"
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
        "\n--- Early-Section Library ---\n"
        "Outcome: not_eligible\n"
        "Paper ID: N/A\n"
        "Conversion Fingerprint: N/A\n"
        "Passage Count: 0\n"
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
        "\n--- Early-Section Library ---\n"
        "Outcome: not_eligible\n"
        "Paper ID: N/A\n"
        "Conversion Fingerprint: N/A\n"
        "Passage Count: 0\n"
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


def test_coordinated_write_rejects_modified_analysis_read_back(
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

    assert exit_code == CLIExitCode.UNEXPECTED_ERROR
    assert "Strict coordinated read-back" in capsys.readouterr().err
    assert storage.list_single_paper_analyses() == ()
    assert storage.list_early_section_records() == ()


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
    assert "Strict coordinated read-back" in err


def test_invalid_policy_versions_return_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)

    # 1. Invalid quality policy version
    opts1 = AnalyzeCommandOptions(
        target_path=pdf_path,
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
        target_path=pdf_path,
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
        target_path=pdf_path,
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
        target_path=pdf_path,
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
        target_path=pdf_path,
        executable_path=tmp_path / "non_existent_exe",
        model_path=model_file,
        model_id="test-model",
        model_bytes=5,
        model_checksum="a" * 64,
        db_path=tmp_path / "invalid-executable.db",
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
        target_path=pdf_path,
        executable_path=exe_file,
        model_path=tmp_path / "non_existent_model.gguf",
        model_id="test-model",
        model_bytes=5,
        model_checksum="a" * 64,
        db_path=tmp_path / "invalid-model-path.db",
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
        target_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=99999,
        model_checksum="a" * 64,
        db_path=tmp_path / "invalid-model-size.db",
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
        target_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=len(model_content),
        model_checksum="f" * 64,
        db_path=tmp_path / "invalid-model-checksum.db",
    )

    assert (
        run_single_paper_analysis_command(opts)
        == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    )
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err


@pytest.mark.parametrize(
    "process_result_kwargs,expected_err_fragment",
    [
        # Case 1: subprocess exits with non-zero returncode
        (
            {"returncode": 1, "stdout": "", "stderr": "runtime crashed"},
            "Configured llama.cpp executable failed its version readiness check.",
        ),
        # Case 2: subprocess exits with zero but version marker is absent from output
        (
            {"returncode": 0, "stdout": "wrong-version-string", "stderr": ""},
            "Configured llama.cpp executable does not match the expected runtime version marker.",
        ),
    ],
)
def test_runtime_readiness_subprocess_boundary_returns_code_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    process_result_kwargs: dict,
    expected_err_fragment: str,
) -> None:
    """Verify the real check_readiness() path: valid artifacts reach the subprocess
    boundary; a nonzero result or missing version marker returns code 2 without traceback.
    """

    pdf_path = _create_valid_pdf_file(tmp_path)
    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy_exe_bytes")
    # Make executable so platform X_OK check passes on POSIX
    exe_file.chmod(exe_file.stat().st_mode | 0o111)

    model_content = b"dummy_model_bytes"
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(model_content)
    model_sha256 = hashlib.sha256(model_content).hexdigest()

    opts = AnalyzeCommandOptions(
        target_path=pdf_path,
        executable_path=exe_file,
        model_path=model_file,
        model_id="test-model",
        model_bytes=len(model_content),
        model_checksum=model_sha256,
        db_path=tmp_path / "readiness_test.db",
    )

    captured_commands: list[tuple[str, ...]] = []

    def fake_run(
        self_runner: object,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: object,
        environment: object,
    ) -> ProcessResult:
        captured_commands.append(tuple(command))
        return ProcessResult(**process_result_kwargs)

    monkeypatch.setattr(
        "econ_paper_cli.adapters.llama_cpp.SubprocessRunner.run",
        fake_run,
    )

    exit_code = run_single_paper_analysis_command(opts, extractor=FakePDFExtractor())

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err
    assert expected_err_fragment in err

    # Assert the exact subprocess command the generator constructed
    assert len(captured_commands) == 1
    assert captured_commands[0] == (str(exe_file), "--version", "--offline")


def test_command_execution_uses_default_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    mock_default_db = tmp_path / "mock_default.db"
    monkeypatch.setattr(
        "econ_paper_cli.services.config_resolution.get_default_db_path",
        lambda: mock_default_db,
    )

    exe_file = tmp_path / "llama-cli"
    exe_file.write_bytes(b"dummy")
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"dummy")

    opts = AnalyzeCommandOptions(
        target_path=pdf_path,
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


def test_success_stores_both_records_with_one_injected_timestamp(
    tmp_path: Path,
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(tmp_path / "coordinated.db")
    storage.initialize()
    extractor = FakePDFExtractor()
    generator = FakeGenerator(
        response_text=_make_success_response_json(
            "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
        )
    )
    timestamp = "2026-08-01T20:30:00+00:00"

    code = run_single_paper_analysis_command(
        _make_options(pdf_path, tmp_path, db_path=tmp_path / "coordinated.db"),
        extractor=extractor,
        generator=generator,
        storage=storage,
        timestamp_provider=lambda: timestamp,
    )

    assert code == CLIExitCode.SUCCESS
    assert extractor.call_count == 1
    assert generator.call_count == 1
    analysis = storage.list_single_paper_analyses()[0]
    library = storage.list_early_section_records()[0]
    assert analysis.created_at == analysis.updated_at == timestamp
    assert library.created_at == library.updated_at == timestamp
    assert analysis.content_checksum == library.source_provenance.content_checksum
    storage.close()
    reopened = SQLiteStorage(tmp_path / "coordinated.db")
    corpus = reopened.load_corpus()
    assert corpus.passages == library.passages
    BM25Retriever(corpus)
    reopened.close()


def test_exact_reuse_needs_neither_extractor_nor_model_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "lazy-reuse.db"
    options = _make_options(pdf_path, tmp_path, db_path=db_path)
    storage = SQLiteStorage(db_path)
    storage.initialize()
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=storage,
        )
        == CLIExitCode.SUCCESS
    )
    options.executable_path.unlink()
    options.model_path.unlink()
    extractor = FakePDFExtractor()

    def forbidden_generator(config: object) -> None:
        raise AssertionError("generator must not be constructed for exact reuse")

    monkeypatch.setattr(
        "econ_paper_cli.services.single_paper_analysis_cli.LlamaCppGenerator",
        forbidden_generator,
    )
    capsys.readouterr()

    code = run_single_paper_analysis_command(
        options, extractor=extractor, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    assert extractor.call_count == 0
    assert "Outcome: reused" in capsys.readouterr().out


def test_analysis_only_record_backfills_without_generator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "backfill.db"
    options = _make_options(pdf_path, tmp_path, db_path=db_path)
    storage = SQLiteStorage(db_path)
    storage.initialize()
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=storage,
        )
        == CLIExitCode.SUCCESS
    )
    paper_id = storage.list_early_section_records()[0].paper.paper_id
    assert storage.delete_early_section_record(paper_id)
    extractor = FakePDFExtractor()
    generator = FakeGenerator(raise_error=AssertionError("must not generate"))
    capsys.readouterr()

    code = run_single_paper_analysis_command(
        options, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    assert extractor.call_count == 1
    assert generator.call_count == 0
    assert storage.get_early_section_record(paper_id) is not None
    assert "Outcome: stored" in capsys.readouterr().out


def test_typed_extraction_failure_during_backfill_is_not_eligible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    options = _make_options(pdf_path, tmp_path)
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=storage,
        )
        == CLIExitCode.SUCCESS
    )
    paper_id = storage.list_early_section_records()[0].paper.paper_id
    assert storage.delete_early_section_record(paper_id)
    extractor = FakePDFExtractor(
        raise_error=PDFParserError(pdf_path, RuntimeError("backfill parser failure"))
    )
    generator = FakeGenerator(raise_error=AssertionError("must not generate"))
    capsys.readouterr()

    code = run_single_paper_analysis_command(
        options, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    assert extractor.call_count == 1
    assert generator.call_count == 0
    assert storage.get_early_section_record(paper_id) is None
    assert "Outcome: not_eligible" in capsys.readouterr().out


def test_ineligible_analysis_does_not_reuse_stale_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    options = _make_options(pdf_path, tmp_path)
    eligible_storage = SQLiteStorage(":memory:")
    eligible_storage.initialize()
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=eligible_storage,
        )
        == CLIExitCode.SUCCESS
    )
    stale_library = eligible_storage.list_early_section_records()[0]

    storage = SQLiteStorage(":memory:")
    storage.initialize()
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(pages_text=["", ""]),
            generator=FakeGenerator(),
            storage=storage,
        )
        == CLIExitCode.HALTED_OR_UNAVAILABLE
    )
    storage.save_early_section_record(stale_library)
    extractor = FakePDFExtractor()
    generator = FakeGenerator(raise_error=AssertionError("must not generate"))
    capsys.readouterr()

    code = run_single_paper_analysis_command(
        options, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.HALTED_OR_UNAVAILABLE
    assert extractor.call_count == 0
    assert generator.call_count == 0
    assert "Outcome: not_eligible" in capsys.readouterr().out


def test_corrupt_library_fails_visibly_without_rebuild(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    options = _make_options(pdf_path, tmp_path)
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=storage,
        )
        == CLIExitCode.SUCCESS
    )
    connection = storage._conn
    assert connection is not None
    connection.execute("UPDATE early_section_records SET markdown = 'corrupt'")
    connection.commit()
    extractor = FakePDFExtractor()
    generator = FakeGenerator(raise_error=AssertionError("must not generate"))
    capsys.readouterr()

    code = run_single_paper_analysis_command(
        options, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.UNEXPECTED_ERROR
    assert "markdown_sha256 does not match" in capsys.readouterr().err
    assert extractor.call_count == 0
    assert generator.call_count == 0


def test_changed_conversion_budget_replaces_library_only(
    tmp_path: Path,
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "conversion-replace.db"
    options = _make_options(pdf_path, tmp_path, db_path=db_path)
    storage = SQLiteStorage(db_path)
    storage.initialize()
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    assert (
        run_single_paper_analysis_command(
            options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=storage,
        )
        == CLIExitCode.SUCCESS
    )
    original = storage.list_early_section_records()[0]
    changed = AnalyzeCommandOptions(
        **{
            name: getattr(options, name)
            for name in options.__dataclass_fields__
            if name != "max_passage_characters"
        },
        max_passage_characters=20,
    )
    extractor = FakePDFExtractor()
    generator = FakeGenerator(raise_error=AssertionError("must not generate"))

    code = run_single_paper_analysis_command(
        changed, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    replacement = storage.list_early_section_records()[0]
    assert generator.call_count == 0
    assert extractor.call_count == 1
    assert replacement.paper.paper_id == original.paper.paper_id
    assert replacement.settings_fingerprint != original.settings_fingerprint
    assert replacement.created_at == original.created_at


def test_invalid_conversion_budget_is_typed_configuration_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    options = _make_options(pdf_path, tmp_path)
    invalid = AnalyzeCommandOptions(
        **{
            name: getattr(options, name)
            for name in options.__dataclass_fields__
            if name != "max_passage_characters"
        },
        max_passage_characters=0,
    )

    assert run_single_paper_analysis_command(invalid) == 2
    assert "max_passage_characters" in capsys.readouterr().err


def test_introduction_only_analysis_populates_library(tmp_path: Path) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    code = run_single_paper_analysis_command(
        _make_options(pdf_path, tmp_path),
        extractor=FakePDFExtractor(
            pages_text=["1. Introduction\n" + "Introduction evidence. " * 30]
        ),
        generator=FakeGenerator(abstained=True),
        storage=storage,
    )

    assert code == CLIExitCode.HALTED_OR_UNAVAILABLE
    library = storage.list_early_section_records()[0]
    assert {passage.section_heading for passage in library.passages} == {"Introduction"}


def test_no_detected_early_section_is_inspectable_without_empty_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    options = _make_options(pdf_path, tmp_path)
    code = run_single_paper_analysis_command(
        options,
        extractor=FakePDFExtractor(pages_text=["Unheaded discussion text. " * 30]),
        generator=FakeGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.HALTED_OR_UNAVAILABLE
    assert storage.list_single_paper_analyses()
    assert storage.list_early_section_records() == ()
    assert "Outcome: no_usable_sections" in capsys.readouterr().out

    extractor = FakePDFExtractor()
    generator = FakeGenerator(raise_error=AssertionError("must not generate"))
    code = run_single_paper_analysis_command(
        options, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.HALTED_OR_UNAVAILABLE
    assert extractor.call_count == 0
    assert generator.call_count == 0
    assert storage.list_early_section_records() == ()
    assert "Outcome: no_usable_sections" in capsys.readouterr().out


@pytest.mark.parametrize(
    "table",
    [
        "single_paper_analyses",
        "single_paper_analysis_warnings",
        "single_paper_analysis_sections",
        "single_paper_analysis_questions",
        "single_paper_analysis_evidence",
        "early_section_records",
        "passages",
        "passage_provenance",
        "passage_source_fragments",
    ],
)
def test_coordinated_write_failure_rolls_back_analysis_and_library(
    table: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    connection = storage._conn
    assert connection is not None
    connection.execute(
        f"""CREATE TRIGGER fail_{table} BEFORE INSERT ON {table}
        BEGIN SELECT RAISE(ABORT, 'injected {table} failure'); END"""
    )
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )

    code = run_single_paper_analysis_command(
        _make_options(pdf_path, tmp_path),
        extractor=FakePDFExtractor(),
        generator=FakeGenerator(response_text=response),
        storage=storage,
    )

    assert code == CLIExitCode.UNEXPECTED_ERROR
    assert f"injected {table} failure" in capsys.readouterr().err
    assert storage.list_single_paper_analyses() == ()
    assert storage.list_early_section_records() == ()


# --- Issue 54: durable runtime/model configuration resolution -------------


def test_exact_reuse_does_not_require_durable_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reuse must succeed with no CLI identity flags and no config file present."""
    pdf_path = _create_valid_pdf_file(tmp_path)
    db_path = tmp_path / "no-config-reuse.db"
    full_options = _make_options(pdf_path, tmp_path, db_path=db_path)
    storage = SQLiteStorage(db_path)
    storage.initialize()
    response = _make_success_response_json(
        "Abstract\nWe evaluate trade policy.", "We evaluate trade policy."
    )
    assert (
        run_single_paper_analysis_command(
            full_options,
            extractor=FakePDFExtractor(),
            generator=FakeGenerator(response_text=response),
            storage=storage,
        )
        == CLIExitCode.SUCCESS
    )

    no_identity_options = AnalyzeCommandOptions(
        target_path=pdf_path,
        db_path=db_path,
    )
    missing_config = JSONConfigStorage(tmp_path / "no-such-config" / "config.json")
    capsys.readouterr()

    code = run_single_paper_analysis_command(
        no_identity_options,
        extractor=FakePDFExtractor(),
        storage=storage,
        config_backend=missing_config,
    )

    assert code == CLIExitCode.SUCCESS
    assert "Outcome: reused" in capsys.readouterr().out
    assert missing_config.load() is None


def test_new_analysis_resolves_identity_from_durable_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A new analysis with no CLI identity flags resolves runtime/model identity
    from durable configuration, and those exact values reach the generator."""
    pdf_path = _create_valid_pdf_file(tmp_path)
    exe_file = tmp_path / "configured-llama-cli"
    exe_file.write_bytes(b"dummy_exe_bytes")
    exe_file.chmod(exe_file.stat().st_mode | 0o111)
    model_content = b"configured_model_bytes"
    model_file = tmp_path / "configured-model.gguf"
    model_file.write_bytes(model_content)
    model_sha256 = hashlib.sha256(model_content).hexdigest()

    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        LocalRuntimeModelConfig(
            executable_path=exe_file,
            model_path=model_file,
            model_id="configured-model",
            model_bytes=len(model_content),
            model_checksum=model_sha256,
        )
    )

    options = AnalyzeCommandOptions(
        target_path=pdf_path,
        db_path=tmp_path / "config-resolved.db",
    )

    captured_commands: list[tuple[str, ...]] = []

    def fake_run(
        self_runner: object,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: object,
        environment: object,
    ) -> ProcessResult:
        captured_commands.append(tuple(command))
        return ProcessResult(returncode=0, stdout="unexpected-version", stderr="")

    monkeypatch.setattr(
        "econ_paper_cli.adapters.llama_cpp.SubprocessRunner.run",
        fake_run,
    )

    code = run_single_paper_analysis_command(
        options,
        extractor=FakePDFExtractor(),
        config_backend=config_backend,
    )

    assert code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert len(captured_commands) == 1
    assert captured_commands[0][0] == str(exe_file)


def test_new_analysis_partial_cli_override_is_typed_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        LocalRuntimeModelConfig(
            executable_path=tmp_path / "exe",
            model_path=tmp_path / "model.gguf",
            model_id="configured-model",
            model_bytes=10,
            model_checksum="a" * 64,
        )
    )
    options = AnalyzeCommandOptions(
        target_path=pdf_path,
        db_path=tmp_path / "partial-override.db",
        model_id="cli-only-model-id",
    )

    code = run_single_paper_analysis_command(
        options, extractor=FakePDFExtractor(), config_backend=config_backend
    )

    assert code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert "Partial runtime/model override" in capsys.readouterr().err


def test_new_analysis_without_cli_or_config_is_typed_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    options = AnalyzeCommandOptions(
        target_path=pdf_path, db_path=tmp_path / "no-config-at-all.db"
    )
    missing_config = JSONConfigStorage(tmp_path / "absent" / "config.json")

    code = run_single_paper_analysis_command(
        options, extractor=FakePDFExtractor(), config_backend=missing_config
    )

    assert code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert "No runtime/model configuration is available" in capsys.readouterr().err


def test_new_analysis_full_cli_override_wins_over_durable_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        LocalRuntimeModelConfig(
            executable_path=tmp_path / "config-exe",
            model_path=tmp_path / "config-model.gguf",
            model_id="config-model",
            model_bytes=10,
            model_checksum="a" * 64,
        )
    )
    cli_exe = tmp_path / "cli-exe"
    cli_exe.write_bytes(b"dummy")
    cli_exe.chmod(cli_exe.stat().st_mode | 0o111)
    cli_model_content = b"cli_override_model_bytes"
    cli_model = tmp_path / "cli-model.gguf"
    cli_model.write_bytes(cli_model_content)
    options = AnalyzeCommandOptions(
        target_path=pdf_path,
        db_path=tmp_path / "full-override.db",
        executable_path=cli_exe,
        model_path=cli_model,
        model_id="cli-model",
        model_bytes=len(cli_model_content),
        model_checksum=hashlib.sha256(cli_model_content).hexdigest(),
    )

    captured_commands: list[tuple[str, ...]] = []

    def fake_run(
        self_runner: object,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: object,
        environment: object,
    ) -> ProcessResult:
        captured_commands.append(tuple(command))
        return ProcessResult(returncode=0, stdout="unexpected-version", stderr="")

    monkeypatch.setattr(
        "econ_paper_cli.adapters.llama_cpp.SubprocessRunner.run",
        fake_run,
    )

    run_single_paper_analysis_command(
        options, extractor=FakePDFExtractor(), config_backend=config_backend
    )

    assert len(captured_commands) == 1
    assert captured_commands[0][0] == str(cli_exe)
