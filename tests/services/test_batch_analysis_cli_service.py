"""Service integration tests for batch directory analysis command."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import econ_paper_cli.domain.single_paper_analysis as analysis_domain
import econ_paper_cli.services.single_paper_analysis as analysis_service
import econ_paper_cli.services.single_paper_analysis_cli as cli_service
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    PDFDocumentMetadata,
    PDFExtractionResult,
    SinglePaperAnalysisFailureCode,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisStage,
    SinglePaperAnalysisStatus,
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
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor, PDFParserError
from econ_paper_cli.services.single_paper_analysis_cli import (
    AnalyzeCommandOptions,
    BatchOutcomeKind,
    BatchResult,
    CLIExitCode,
    format_analysis_record_output,
    run_single_paper_analysis_command,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakePDFExtractor(PDFExtractor):
    """Fake extractor returning two pages of synthetic text."""

    def __init__(self, raise_error: Exception | None = None) -> None:
        self.call_count = 0
        self.called_paths: list[Path] = []
        self._raise = raise_error

    def extract(self, pdf_path: Path) -> PDFExtractionResult:
        if self._raise is not None:
            raise self._raise
        self.call_count += 1
        self.called_paths.append(pdf_path)
        pages = (
            ExtractedPDFPage(
                page_number=1,
                text="Abstract\nWe study trade effects.\n\n1. Introduction\nTrade policy on page 1.",
            ),
            ExtractedPDFPage(
                page_number=2,
                text="1. Introduction (continued)\nTrade policy on page 2.",
            ),
        )
        meta = PDFDocumentMetadata(title=f"Paper: {pdf_path.name}")
        return PDFExtractionResult(
            source_path=pdf_path.resolve(),
            pages=pages,
            page_count=2,
            metadata=meta,
            extraction_method="fake",
            parser_version="0.0",
        )


class CountingFakeGenerator(Generator):
    """Fake generator that counts calls and returns a canonical success response."""

    def __init__(self) -> None:
        self.call_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.call_count += 1
        exc = "We study trade effects."
        abs_text = "Abstract\nWe study trade effects."
        start = abs_text.find(exc)
        evidence_json = json.dumps(
            {
                "research_question": "What are trade effects?",
                "kind": "explicit",
                "evidence": [
                    {
                        "section_kind": "abstract",
                        "excerpt_text": exc,
                        "page_number": 1,
                        "start_character_offset": start,
                        "end_character_offset": start + len(exc),
                    }
                ],
            }
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
            answer_text=evidence_json,
            citations=citations,
            generation_method="fake",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )


class AbstainingGenerator(Generator):
    """Fake generator that always abstains."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            answer_text="",
            citations=(),
            generation_method="fake",
            abstained=True,
            abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
            finding_kinds=(),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pdf(path: Path, content: bytes | None = None) -> Path:
    path.write_bytes(content or b"%PDF-1.4 synthetic content for batch test")
    return path


def _make_opts(
    target: Path,
    tmp_path: Path,
    db_path: Path | None = None,
) -> AnalyzeCommandOptions:
    exe = tmp_path / "llama-cli"
    if not exe.exists():
        exe.write_bytes(b"dummy")
    model = tmp_path / "model.gguf"
    if not model.exists():
        model.write_bytes(b"dummy_model")
    return AnalyzeCommandOptions(
        target_path=target,
        executable_path=exe,
        model_path=model,
        model_id="test-model",
        model_bytes=11,
        model_checksum="b" * 64,
        db_path=db_path or (tmp_path / "batch.db"),
    )


# ---------------------------------------------------------------------------
# Parser / help tests
# ---------------------------------------------------------------------------


def test_analyze_accepts_file_and_directory_paths() -> None:
    """AnalyzeCommandOptions accepts both a file path and a directory path."""
    from pathlib import Path

    file_opts = AnalyzeCommandOptions(
        target_path=Path("/tmp/paper.pdf"),
        executable_path=Path("/tmp/llama"),
        model_path=Path("/tmp/model.gguf"),
        model_id="m",
        model_bytes=10,
        model_checksum="a" * 64,
    )
    dir_opts = AnalyzeCommandOptions(
        target_path=Path("/tmp/papers/"),
        executable_path=Path("/tmp/llama"),
        model_path=Path("/tmp/model.gguf"),
        model_id="m",
        model_bytes=10,
        model_checksum="a" * 64,
    )
    assert file_opts.target_path == Path("/tmp/paper.pdf")
    assert dir_opts.target_path == Path("/tmp/papers/")


def test_batch_result_is_frozen_and_rejects_inconsistent_counts() -> None:
    assert BatchResult.__dataclass_params__.frozen is True
    with pytest.raises(ValueError, match="total_discovered must be 0"):
        BatchResult(
            items=(),
            total_discovered=1,
            unique_checksums=0,
            newly_analyzed=0,
            reused=0,
            batch_duplicates=0,
            successes=0,
            halted=0,
            typed_failures=0,
            unexpected_failures=0,
        )


def test_empty_directory_is_a_typed_shared_preflight_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "empty"
    papers_dir.mkdir()

    code = run_single_paper_analysis_command(
        _make_opts(papers_dir, tmp_path),
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=SQLiteStorage(":memory:"),
    )

    assert code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert "Input preflight error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Single-file regression — existing contract unchanged
# ---------------------------------------------------------------------------


def test_single_file_mode_success_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = _write_pdf(tmp_path / "paper.pdf")
    db = tmp_path / "single.db"
    opts = _make_opts(pdf, tmp_path, db_path=db)
    generator = CountingFakeGenerator()
    extractor = FakePDFExtractor()
    storage = SQLiteStorage(db)
    storage.initialize()

    code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "=== Single-Paper Analysis Record ===" in out
    assert "Status: success" in out
    # Single-file mode must not render Batch Summary
    assert "=== Batch Summary ===" not in out
    assert generator.call_count == 1
    assert extractor.call_count == 1


# ---------------------------------------------------------------------------
# Deterministic directory ordering
# ---------------------------------------------------------------------------


def test_directory_ordering_is_deterministic_and_canonical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Discovered PDFs are processed in canonical string path order."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    names = ["charlie.pdf", "alpha.pdf", "Bravo.PDF", "delta.pdf"]
    for index, name in enumerate(names):
        _write_pdf(papers_dir / name, content=f"%PDF-1.4 {index}".encode())

    db = tmp_path / "order.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    generator = CountingFakeGenerator()
    extractor = FakePDFExtractor()
    storage = SQLiteStorage(db)
    storage.initialize()

    code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    out = capsys.readouterr().out
    expected_paths = sorted((path.resolve() for path in papers_dir.iterdir()), key=str)
    assert extractor.called_paths == expected_paths
    item_paths = [
        Path(line.split("] ", 1)[1].removesuffix(" ==="))
        for line in out.splitlines()
        if line.startswith("=== [")
    ]
    assert item_paths == expected_paths
    assert "Total discovered:    4" in out


# ---------------------------------------------------------------------------
# Within-batch duplicate detection
# ---------------------------------------------------------------------------


def test_batch_duplicate_bytes_analyzed_only_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two files with identical bytes produce exactly one extraction/generation call."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    shared_content = b"%PDF-1.4 identical content for duplicate test"
    _write_pdf(papers_dir / "alpha.pdf", content=shared_content)
    _write_pdf(papers_dir / "beta.pdf", content=shared_content)
    _write_pdf(papers_dir / "gamma.pdf")  # distinct content

    db = tmp_path / "dup.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    generator = CountingFakeGenerator()
    extractor = FakePDFExtractor()
    storage = SQLiteStorage(db)
    storage.initialize()

    code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.SUCCESS
    out = capsys.readouterr().out
    # Only 2 unique checksums → 2 generation calls
    assert generator.call_count == 2
    assert extractor.call_count == 2
    assert "batch_duplicate" in out
    assert "Batch duplicates:    1" in out
    assert "Unique checksums:    2" in out
    assert "Total discovered:    3" in out
    assert f"Duplicate of: {(papers_dir / 'alpha.pdf').resolve()}" in out


def test_batch_reuses_precomputed_checksum_without_second_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    _write_pdf(papers_dir / "paper.pdf")

    def forbidden_preflight(*args: object, **kwargs: object) -> None:
        raise AssertionError("single-paper preflight must not run again in batch mode")

    monkeypatch.setattr(
        analysis_service, "run_ingestion_preflight", forbidden_preflight
    )
    storage = SQLiteStorage(tmp_path / "one-checksum.db")
    storage.initialize()

    code = run_single_paper_analysis_command(
        _make_opts(papers_dir, tmp_path, db_path=tmp_path / "one-checksum.db"),
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.SUCCESS
    assert "Successes:           1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Generator and readiness executed exactly once per batch
# ---------------------------------------------------------------------------


def test_generator_constructed_and_ready_once_for_multi_file_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Generator construction and readiness run once; the same instance is reused."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    for i in range(3):
        _write_pdf(papers_dir / f"paper{i}.pdf", content=f"%PDF-1.4 paper {i}".encode())

    db = tmp_path / "once.db"
    extractor = FakePDFExtractor()
    storage = SQLiteStorage(db)
    storage.initialize()
    opts = _make_opts(papers_dir, tmp_path, db_path=db)

    instances: list[CountingFakeGenerator] = []

    class FakeLocalGenerator(CountingFakeGenerator):
        def __init__(self, config: object) -> None:
            super().__init__()
            self.config = config
            self.readiness_calls = 0
            instances.append(self)

        def check_readiness(self) -> None:
            self.readiness_calls += 1

    monkeypatch.setattr(cli_service, "LlamaCppGenerator", FakeLocalGenerator)

    code = run_single_paper_analysis_command(opts, extractor=extractor, storage=storage)

    assert code == CLIExitCode.SUCCESS
    assert len(instances) == 1
    assert instances[0].readiness_calls == 1
    assert instances[0].call_count == 3
    out = capsys.readouterr().out
    assert "Newly analyzed:      3" in out


# ---------------------------------------------------------------------------
# Exact stored-identity reuse (idempotency)
# ---------------------------------------------------------------------------


def test_second_run_reuses_all_exact_durable_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-running the same directory with unchanged settings reuses all records."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    for i in range(2):
        _write_pdf(papers_dir / f"paper{i}.pdf", content=f"%PDF-1.4 paper {i}".encode())

    db = tmp_path / "idem.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)

    # First run
    gen1 = CountingFakeGenerator()
    ext1 = FakePDFExtractor()
    storage = SQLiteStorage(db)
    storage.initialize()
    code1 = run_single_paper_analysis_command(
        opts, extractor=ext1, generator=gen1, storage=storage
    )
    assert code1 == CLIExitCode.SUCCESS
    assert gen1.call_count == 2

    # Second run — zero generation calls expected
    gen2 = CountingFakeGenerator()
    ext2 = FakePDFExtractor()
    capsys.readouterr()  # flush first run output
    code2 = run_single_paper_analysis_command(
        opts, extractor=ext2, generator=gen2, storage=storage
    )
    assert code2 == CLIExitCode.SUCCESS
    assert gen2.call_count == 0
    assert ext2.call_count == 0
    out2 = capsys.readouterr().out
    assert "Reused (exact):      2" in out2
    assert "Newly analyzed:      0" in out2


# ---------------------------------------------------------------------------
# Changed settings → distinct identity, no reuse
# ---------------------------------------------------------------------------


def test_changed_settings_creates_distinct_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Same PDF bytes + different settings fingerprint → new analysis, not reuse."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    _write_pdf(papers_dir / "paper.pdf")

    db = tmp_path / "settings.db"
    storage = SQLiteStorage(db)
    storage.initialize()

    # First run with default settings
    opts1 = _make_opts(papers_dir, tmp_path, db_path=db)
    gen1 = CountingFakeGenerator()
    code1 = run_single_paper_analysis_command(
        opts1, extractor=FakePDFExtractor(), generator=gen1, storage=storage
    )
    assert code1 == CLIExitCode.SUCCESS
    assert gen1.call_count == 1

    alternate_policy = "single-paper-analysis-v2-test"
    monkeypatch.setitem(
        analysis_domain._CANONICAL_SINGLE_PAPER_SETTINGS, alternate_policy, {}
    )
    opts2 = AnalyzeCommandOptions(
        **{
            field: getattr(opts1, field)
            for field in opts1.__dataclass_fields__
            if field != "single_paper_policy_version"
        },
        single_paper_policy_version=alternate_policy,
    )
    capsys.readouterr()
    gen2 = CountingFakeGenerator()
    code2 = run_single_paper_analysis_command(
        opts2, extractor=FakePDFExtractor(), generator=gen2, storage=storage
    )
    assert code2 == CLIExitCode.SUCCESS
    assert gen2.call_count == 1
    assert len(storage.list_single_paper_analyses()) == 2
    out2 = capsys.readouterr().out
    assert "Newly analyzed:      1" in out2
    assert "Reused (exact):      0" in out2


# ---------------------------------------------------------------------------
# Mixed-outcome batch — continue after typed failures
# ---------------------------------------------------------------------------


def test_mixed_outcome_batch_continues_after_typed_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Typed failures and halted records do not abort remaining candidates."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    for name in (
        "a-success.pdf",
        "b-extraction.pdf",
        "c-quality.pdf",
        "d-question.pdf",
        "e-preflight.pdf",
    ):
        _write_pdf(papers_dir / name, content=f"%PDF-1.4 {name}".encode())

    db = tmp_path / "mixed.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)

    class OutcomeExtractor(FakePDFExtractor):
        def extract(self, pdf_path: Path) -> PDFExtractionResult:
            self.call_count += 1
            self.called_paths.append(pdf_path)
            if pdf_path.name == "b-extraction.pdf":
                raise PDFParserError(pdf_path, RuntimeError("synthetic parser failure"))
            if pdf_path.name == "c-quality.pdf":
                pages = (ExtractedPDFPage(page_number=1, text=""),)
            else:
                marker = " ABSTAIN" if pdf_path.name == "d-question.pdf" else ""
                pages = (
                    ExtractedPDFPage(
                        page_number=1,
                        text=(
                            "Abstract\nWe study trade effects."
                            f"{marker}\n\n1. Introduction\n"
                            "Trade policy affects prices and quantities across markets."
                        ),
                    ),
                    ExtractedPDFPage(
                        page_number=2,
                        text="2. Data\nSynthetic material closes the introduction boundary.",
                    ),
                )
            return PDFExtractionResult(
                source_path=pdf_path.resolve(),
                pages=pages,
                page_count=len(pages),
                metadata=PDFDocumentMetadata(title=pdf_path.name),
                extraction_method="outcome_fake",
                parser_version="0.0",
            )

    class OutcomeGenerator(CountingFakeGenerator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            if any("ABSTAIN" in item.passage.text for item in request.evidence):
                self.call_count += 1
                return AbstainingGenerator().generate(request)
            return super().generate(request)

    generator = OutcomeGenerator()
    extractor = OutcomeExtractor()
    storage = SQLiteStorage(db)
    storage.initialize()

    preflight_path = papers_dir / "e-preflight.pdf"
    extraction_failure = analysis_service.analyze_single_paper(
        preflight_path,
        FakePDFExtractor(
            raise_error=PDFParserError(
                preflight_path, RuntimeError("seed exact identity")
            )
        ),
        generator,
    )
    extraction_failure_record = SinglePaperAnalysisRecord.from_result(
        extraction_failure,
        settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    )
    stored_preflight_failure = replace(
        extraction_failure_record,
        status=SinglePaperAnalysisStatus.PREFLIGHT_FAILED,
        completed_stages=(),
        failed_stage=SinglePaperAnalysisStage.PREFLIGHT,
        skipped_stages=(
            SinglePaperAnalysisStage.EXTRACTION,
            SinglePaperAnalysisStage.QUALITY_ASSESSMENT,
            SinglePaperAnalysisStage.SECTION_DETECTION,
            SinglePaperAnalysisStage.QUESTION_EXTRACTION,
        ),
        failure_code=SinglePaperAnalysisFailureCode.MULTI_CANDIDATE_BATCH,
        error_message="Synthetic durable preflight failure.",
    )
    storage.save_single_paper_analysis(stored_preflight_failure)

    code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    out = capsys.readouterr().out
    assert code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert [path.name for path in extractor.called_paths] == [
        "a-success.pdf",
        "b-extraction.pdf",
        "c-quality.pdf",
        "d-question.pdf",
    ]
    assert out.count("=== [typed_terminal]") == 3
    assert "=== [reused]" in out
    assert "Status: preflight_failed" in out
    assert "Total discovered:    5" in out
    assert "Newly analyzed:      4" in out
    assert "Reused (exact):      1" in out
    assert "Successes:           1" in out
    assert "Halted/unavailable:  2" in out
    assert "Typed failures:      2" in out
    assert "Unexpected failures: 0" in out


# ---------------------------------------------------------------------------
# Unexpected failure isolation
# ---------------------------------------------------------------------------


def test_unexpected_failure_isolated_other_candidates_still_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unexpected failure on one file does not abort remaining candidates."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    ok_content = b"%PDF-1.4 good content"
    fail_content = b"%PDF-1.4 trigger failure"
    _write_pdf(papers_dir / "alpha.pdf", content=ok_content)
    _write_pdf(papers_dir / "fail.pdf", content=fail_content)
    _write_pdf(papers_dir / "gamma.pdf", content=b"%PDF-1.4 gamma content")

    db = tmp_path / "isolation.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)

    fail_checksum = hashlib.sha256(fail_content).hexdigest()

    class SelectivelyFailingExtractor(PDFExtractor):
        def __init__(self) -> None:
            self.call_count = 0

        def extract(self, pdf_path: Path) -> PDFExtractionResult:
            content = pdf_path.read_bytes()
            if hashlib.sha256(content).hexdigest() == fail_checksum:
                raise RuntimeError("Simulated unexpected extractor failure.")
            self.call_count += 1
            pages = (
                ExtractedPDFPage(
                    page_number=1,
                    text="Abstract\nWe study trade effects.\n\n1. Introduction\nTrade on p1.",
                ),
            )
            meta = PDFDocumentMetadata(title=pdf_path.name)
            return PDFExtractionResult(
                source_path=pdf_path.resolve(),
                pages=pages,
                page_count=1,
                metadata=meta,
                extraction_method="selective_fake",
                parser_version="0.0",
            )

    extractor = SelectivelyFailingExtractor()
    generator = CountingFakeGenerator()
    storage = SQLiteStorage(db)
    storage.initialize()

    code = run_single_paper_analysis_command(
        opts, extractor=extractor, generator=generator, storage=storage
    )

    assert code == CLIExitCode.UNEXPECTED_ERROR
    out = capsys.readouterr().out
    assert "unexpected_failure" in out
    assert "Unexpected failures: 1" in out
    assert "Total discovered:    3" in out
    # Other candidates still ran
    assert extractor.call_count >= 2


# ---------------------------------------------------------------------------
# Strict durable read-back — no transient fallback in batch
# ---------------------------------------------------------------------------


class MissingReadBackStorage(SQLiteStorage):
    """Returns None on get_single_paper_analysis — simulates a read-back failure."""

    def get_single_paper_analysis(self, analysis_id: str):  # type: ignore[override]
        return None


def test_batch_missing_durable_readback_is_unexpected_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    _write_pdf(papers_dir / "paper.pdf")

    db = tmp_path / "readback.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    storage = MissingReadBackStorage(":memory:")
    storage.initialize()

    code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.UNEXPECTED_ERROR
    out = capsys.readouterr().out
    assert "unexpected_failure" in out
    assert "Unexpected failures: 1" in out


# ---------------------------------------------------------------------------
# Aggregate exit-code precedence
# ---------------------------------------------------------------------------


def test_all_success_batch_returns_code_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    _write_pdf(papers_dir / "p1.pdf", content=b"%PDF-1.4 content one")
    _write_pdf(papers_dir / "p2.pdf", content=b"%PDF-1.4 content two")
    db = tmp_path / "exit0.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    storage = SQLiteStorage(db)
    storage.initialize()
    code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )
    assert code == CLIExitCode.SUCCESS


def test_halted_only_batch_returns_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    _write_pdf(papers_dir / "paper.pdf")
    storage = SQLiteStorage(tmp_path / "exit1.db")
    storage.initialize()

    code = run_single_paper_analysis_command(
        _make_opts(papers_dir, tmp_path, db_path=tmp_path / "exit1.db"),
        extractor=FakePDFExtractor(),
        generator=AbstainingGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.HALTED_OR_UNAVAILABLE
    assert "Halted/unavailable:  1" in capsys.readouterr().out


def test_typed_failure_batch_returns_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    pdf = _write_pdf(papers_dir / "paper.pdf")
    storage = SQLiteStorage(tmp_path / "exit2.db")
    storage.initialize()

    code = run_single_paper_analysis_command(
        _make_opts(papers_dir, tmp_path, db_path=tmp_path / "exit2.db"),
        extractor=FakePDFExtractor(
            raise_error=PDFParserError(pdf, RuntimeError("synthetic parser failure"))
        ),
        generator=CountingFakeGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    out = capsys.readouterr().out
    assert "=== [typed_terminal]" in out
    assert "Typed failures:      1" in out


def test_unexpected_failure_batch_returns_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    _write_pdf(papers_dir / "paper.pdf")
    db = tmp_path / "exit3.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    storage = MissingReadBackStorage(":memory:")
    storage.initialize()
    code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )
    assert code == CLIExitCode.UNEXPECTED_ERROR


# ---------------------------------------------------------------------------
# Idempotency and stable ordering across runs
# ---------------------------------------------------------------------------


def test_batch_idempotency_stable_ordering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    for i in range(3):
        _write_pdf(papers_dir / f"p{i}.pdf", content=f"%PDF-1.4 content {i}".encode())
    db = tmp_path / "stable.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    storage = SQLiteStorage(db)
    storage.initialize()

    code1 = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )
    out1 = capsys.readouterr().out

    code2 = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )
    out2 = capsys.readouterr().out

    assert code1 == CLIExitCode.SUCCESS
    assert code2 == CLIExitCode.SUCCESS
    # Summary line is identical across runs
    assert "Newly analyzed:      3" in out1
    assert "Reused (exact):      3" in out2
    assert "Newly analyzed:      0" in out2


def test_directory_output_is_exact_and_reuses_record_formatter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    paths = [
        _write_pdf(papers_dir / "a.pdf", b"%PDF-1.4 exact a").resolve(),
        _write_pdf(papers_dir / "b.pdf", b"%PDF-1.4 exact b").resolve(),
    ]
    db = tmp_path / "exact.db"
    storage = SQLiteStorage(db)
    storage.initialize()

    code = run_single_paper_analysis_command(
        _make_opts(papers_dir, tmp_path, db_path=db),
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.SUCCESS
    records_by_path = {
        record.source_path: record for record in storage.list_single_paper_analyses()
    }
    item_sections = [
        "\n".join(
            (
                f"=== [{BatchOutcomeKind.NEWLY_ANALYZED.value}] {path} ===",
                format_analysis_record_output(records_by_path[path], db),
            )
        )
        for path in paths
    ]
    summary = "\n".join(
        (
            "=== Batch Summary ===",
            "Total discovered:    2",
            "Unique checksums:    2",
            "Newly analyzed:      2",
            "Reused (exact):      0",
            "Batch duplicates:    0",
            "Successes:           2",
            "Halted/unavailable:  0",
            "Typed failures:      0",
            "Unexpected failures: 0",
        )
    )
    assert capsys.readouterr().out == "\n\n".join((*item_sections, summary)) + "\n"


# ---------------------------------------------------------------------------
# Offline — no network or PDF modification
# ---------------------------------------------------------------------------


def test_batch_offline_no_network_no_pdf_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket

    def forbidden_socket(*args, **kwargs):
        raise RuntimeError("Network socket forbidden during offline batch!")

    monkeypatch.setattr(socket, "socket", forbidden_socket)

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    pdf = _write_pdf(papers_dir / "paper.pdf")
    original = pdf.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()

    db = tmp_path / "offline.db"
    opts = _make_opts(papers_dir, tmp_path, db_path=db)
    storage = SQLiteStorage(db)
    storage.initialize()

    code = run_single_paper_analysis_command(
        opts,
        extractor=FakePDFExtractor(),
        generator=CountingFakeGenerator(),
        storage=storage,
    )

    assert code == CLIExitCode.SUCCESS
    assert pdf.exists()
    assert pdf.read_bytes() == original
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == original_hash
