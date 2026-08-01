"""Service integration tests for single-paper analysis CLI command execution and rendering."""

import json
from pathlib import Path

import pytest

from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    PDFDocumentMetadata,
    PDFExtractionResult,
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
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor
from econ_paper_cli.services.single_paper_analysis_cli import (
    AnalyzeCommandOptions,
    CLIExitCode,
    run_single_paper_analysis_command,
)


class FakePDFExtractor(PDFExtractor):
    """Fake PDF extractor for CLI service tests."""

    def __init__(self, pages_text: list[str] | None = None) -> None:
        self.pages_text = (
            pages_text
            if pages_text is not None
            else [
                "Abstract\nWe evaluate trade policy.\n\n1. Introduction\nTrade policy affects prices on page 1.",
                "1. Introduction (continued)\nTrade policy affects prices on page 2 as well.",
            ]
        )

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
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


def _create_valid_pdf_file(tmp_path: Path, filename: str = "paper.pdf") -> Path:
    path = (tmp_path / filename).resolve()
    path.write_bytes(b"%PDF-1.4 synthetic content for CLI service test")
    return path


def _make_options(
    pdf_path: Path,
    tmp_path: Path,
    db_path: Path | None = None,
) -> AnalyzeCommandOptions:
    dummy_exe = tmp_path / "llama-cli"
    dummy_exe.write_bytes(b"dummy")
    dummy_model = tmp_path / "model.gguf"
    dummy_model.write_bytes(b"dummy_model")

    return AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=dummy_exe,
        model_path=dummy_model,
        model_id="test-model",
        model_bytes=11,
        model_checksum="b" * 64,
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


def test_run_single_paper_analysis_command_success(
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
    assert "=== Single-Paper Analysis Record ===" in out
    assert "Status: success" in out
    assert f"Database Path: {db_path}" in out
    assert "What is the impact of trade policy?" in out
    assert "We evaluate trade policy." in out

    # Verify data in storage
    record = storage.list_single_paper_analyses()[0]
    assert record.status.value == "success"
    assert record.research_question is not None
    assert (
        record.research_question.question_text == "What is the impact of trade policy?"
    )


def test_run_single_paper_analysis_command_quality_halted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    opts = _make_options(pdf_path, tmp_path)

    # Empty pages text triggers QUALITY_HALTED
    extractor = FakePDFExtractor(pages_text=["", "   \n  "])
    generator = FakeGenerator()
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.HALTED_OR_UNAVAILABLE
    out = capsys.readouterr().out
    assert "Status: quality_halted" in out
    assert "[quality]" in out


def test_run_single_paper_analysis_command_question_extraction_halted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    opts = _make_options(pdf_path, tmp_path)

    extractor = FakePDFExtractor()
    generator = FakeGenerator(response_text="", abstained=True)
    storage = SQLiteStorage(":memory:")
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert exit_code == CLIExitCode.HALTED_OR_UNAVAILABLE
    out = capsys.readouterr().out
    assert "Status: question_extraction_halted" in out
    assert "[research_question] model_abstained" in out


def test_run_single_paper_analysis_command_preflight_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    non_existent_pdf = tmp_path / "non_existent.pdf"
    opts = _make_options(non_existent_pdf, tmp_path)

    storage = SQLiteStorage(":memory:")
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=FakeGenerator(),
        storage=storage,
    )

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    out = capsys.readouterr().out
    assert "Status: preflight_failed" in out
    assert "Failure Code: path_not_found" in out


def test_run_single_paper_analysis_command_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = _create_valid_pdf_file(tmp_path)
    bad_opts = AnalyzeCommandOptions(
        pdf_path=pdf_path,
        executable_path=tmp_path / "no_exe",
        model_path=tmp_path / "no_model",
        model_id="invalid id!!",
        model_bytes=100,
        model_checksum="invalid_sha256",
        db_path=tmp_path / "config_error.db",
    )

    exit_code = run_single_paper_analysis_command(bad_opts)

    assert exit_code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "Configuration or readiness error:" in err


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
