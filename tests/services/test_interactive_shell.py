"""Service tests for the bare ``econpapers`` interactive cited-chat shell."""

import io
from pathlib import Path

import pytest

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    Citation,
    EarlySectionLibraryRecord,
    ExtractedPDFPage,
    PDFConversionSettings,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSection,
    PDFSectionDetectionResult,
    PDFSectionKind,
    PDFSectionSpan,
)
from econ_paper_cli.domain.local_config import LocalRuntimeModelConfig
from econ_paper_cli.protocols import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.services.chat_command import (
    ChatCommandResult,
    ChatTerminalOutcome,
    format_chat_command_output,
)
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.interactive_shell import (
    ShellCommandOptions,
    ShellExitCode,
    ShellSessionError,
    ShellTurnOutcome,
    format_shell_turn_output,
    open_shell_session,
    run_interactive_shell,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections

VALID_CHECKSUM = "e" * 64


class CountingGenerator(Generator):
    """Fake generator that counts constructions and calls."""

    def __init__(self, response: GenerationResponse) -> None:
        self.call_count = 0
        self.last_request: GenerationRequest | None = None
        self.response = response

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.call_count += 1
        self.last_request = request
        return self.response


class AnsweringGenerator(Generator):
    """Generator that returns citations matching the supplied evidence and
    records every request it was called with."""

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        return GenerationResponse(
            answer_text=f"Answer for: {request.question}",
            citations=tuple(
                Citation(
                    citation_id=f"e{item.rank}",
                    paper_id=item.passage.paper_id,
                    passage_id=item.passage.passage_id,
                )
                for item in request.evidence
            ),
            generation_method="fake-generator",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )


def _record(
    tmp_path: Path,
    *,
    title: str = "Trade Policy Paper",
    timestamp: str = "2026-08-01T12:00:00+00:00",
) -> EarlySectionLibraryRecord:
    text = "Abstract trade policy evidence.\n\nIntroduction trade policy evidence."
    extraction = PDFExtractionResult(
        source_path=(tmp_path / "paper.pdf").resolve(),
        pages=(ExtractedPDFPage(1, text),),
        page_count=1,
        metadata=PDFDocumentMetadata(title=title, author_text="Ada Economist"),
        extraction_method="synthetic",
        parser_version="1.0",
    )
    detection = PDFSectionDetectionResult(
        policy_version="pdf-section-detection-v1",
        sections=(
            PDFSection(
                kind=PDFSectionKind.ABSTRACT,
                heading_text="Abstract",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 0, 31),),
                text="Abstract trade policy evidence.",
            ),
            PDFSection(
                kind=PDFSectionKind.INTRODUCTION,
                heading_text="Introduction",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 33, len(text)),),
                text="Introduction trade policy evidence.",
            ),
        ),
        candidates=(),
        warnings=(),
    )
    settings = PDFConversionSettings(max_passage_characters=1200)
    conversion = convert_pdf_early_sections(
        extraction,
        detection,
        content_checksum="c" * 64,
        settings=settings,
    )
    return project_early_section_library_record(
        extraction,
        detection,
        conversion,
        source_file_size=1024,
        timestamp=timestamp,
    )


def _config(**overrides: object) -> LocalRuntimeModelConfig:
    base: dict[str, object] = {
        "executable_path": Path("/usr/local/bin/llama-completion"),
        "model_path": Path("/models/model.gguf"),
        "model_id": "shell-model",
        "model_bytes": 42,
        "model_checksum": VALID_CHECKSUM,
    }
    base.update(overrides)
    return LocalRuntimeModelConfig(**base)


def _scripted_lines(*lines: str) -> io.StringIO:
    """Build a stdin double from scripted lines, terminating with EOF."""
    return io.StringIO("\n".join(lines) + "\n" if lines else "")


# --- Session open / snapshot -----------------------------------------------


def test_open_shell_session_missing_database_is_typed_failure(tmp_path: Path) -> None:
    storage = SQLiteStorage(str(tmp_path / "missing.db"), read_only=True)
    session = open_shell_session(ShellCommandOptions(), storage=storage)
    assert not hasattr(session, "ask")
    assert session.exit_code == ShellExitCode.TYPED_FAILURE_OR_CONFIG_ERROR


def test_open_shell_session_builds_snapshot_from_empty_library(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    session = open_shell_session(ShellCommandOptions(), storage=storage)
    assert session.snapshot.paper_count == 0
    assert session.snapshot.passage_count == 0


def test_open_shell_session_builds_snapshot_from_populated_library(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(ShellCommandOptions(), storage=storage)
    assert session.snapshot.paper_count == 1
    assert session.snapshot.passage_count == 2
    assert session.generator_ready is False


# --- Generator lifecycle -----------------------------------------------


def test_empty_library_never_constructs_generator(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    calls = 0

    def provider() -> Generator:
        nonlocal calls
        calls += 1
        raise AssertionError("generator must not be constructed")

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=provider
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.NO_MATCHES
    assert calls == 0


def test_no_match_question_never_constructs_generator(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    calls = 0

    def provider() -> Generator:
        nonlocal calls
        calls += 1
        raise AssertionError("generator must not be constructed")

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=provider
    )
    result = session.ask("astronomy and astrophysics")
    assert result.outcome is ShellTurnOutcome.NO_MATCHES
    assert calls == 0


def test_first_match_constructs_generator_and_later_matches_reuse_it(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()
    construction_count = 0

    def provider() -> Generator:
        nonlocal construction_count
        construction_count += 1
        return generator

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=provider
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert first.generator_action == "constructed"
    assert construction_count == 1

    second = session.ask("trade policy evidence")
    assert second.outcome is ShellTurnOutcome.ANSWERED
    assert second.generator_action == "reused"
    assert construction_count == 1
    assert len(generator.requests) == 2


def test_failed_construction_does_not_poison_session_for_later_questions(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()
    attempts = 0

    def flaky_provider() -> Generator:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ShellSessionError("simulated readiness failure")
        return generator

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=flaky_provider
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.FAILED
    assert session.generator_ready is False

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.ANSWERED
    assert session.generator_ready is True
    assert attempts == 2


# --- Per-turn independence ---------------------------------------------


def test_each_question_is_independent_with_no_prior_turn_context(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )

    session.ask("trade policy")
    session.ask("trade policy evidence")

    assert len(generator.requests) == 2
    assert generator.requests[0].question == "trade policy"
    assert generator.requests[1].question == "trade policy evidence"
    # Neither request carries the other's question or answer text.
    assert (
        "evidence" not in generator.requests[0].question
        or generator.requests[0].question == "trade policy"
    )
    assert generator.requests[1].question != generator.requests[0].question


# --- Per-turn outcomes ---------------------------------------------------


def test_answered_question_renders_citations(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )
    result = session.ask("trade policy")

    assert result.outcome is ShellTurnOutcome.ANSWERED
    assert result.answer_text == "Answer for: trade policy"
    assert len(result.citations) == 2


def test_abstained_question_reports_reason(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    response = GenerationResponse(
        answer_text="unused",
        citations=(),
        generation_method="fake-generator",
        abstained=True,
        abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
        finding_kinds=(),
    )
    generator = CountingGenerator(response)

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )
    result = session.ask("trade policy")

    assert result.outcome is ShellTurnOutcome.ABSTAINED
    assert result.no_answer_reason == "Generator abstained: insufficient evidence."
    assert generator.call_count == 1


def test_typed_generator_failure_renders_error_and_session_continues(
    tmp_path: Path,
) -> None:
    from econ_paper_cli.adapters.llama_cpp import LlamaCppReadinessError

    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()
    attempts = 0

    def provider() -> Generator:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LlamaCppReadinessError("model not ready")
        return generator

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=provider
    )

    failed = session.ask("trade policy")
    assert failed.outcome is ShellTurnOutcome.FAILED
    assert failed.error_message is not None

    recovered = session.ask("trade policy")
    assert recovered.outcome is ShellTurnOutcome.ANSWERED


def test_malformed_model_output_renders_error_and_session_continues(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class MalformedGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            return GenerationResponse(
                answer_text="",
                citations=(),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(),
            )

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: MalformedGenerator(),
    )
    failed = session.ask("trade policy")
    assert failed.outcome is ShellTurnOutcome.FAILED

    good_generator = AnsweringGenerator()
    session2 = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: good_generator,
    )
    recovered = session2.ask("trade policy")
    assert recovered.outcome is ShellTurnOutcome.ANSWERED


def test_citation_validation_failure_renders_error(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class InvalidCitationGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            return GenerationResponse(
                answer_text="An answer citing a nonexistent passage.",
                citations=(
                    Citation(
                        citation_id="e1",
                        paper_id="does-not-exist",
                        passage_id="does-not-exist:p0",
                    ),
                ),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
            )

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: InvalidCitationGenerator(),
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.FAILED


def test_unexpected_generator_exception_renders_error_and_continues(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class BoomGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            raise RuntimeError("boom")

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: BoomGenerator(),
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.FAILED
    assert "boom" in (result.error_message or "")


# --- Citation rendering parity with one-shot chat -----------------------


def test_citation_rendering_matches_one_shot_chat_formatter(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )
    shell_result = session.ask("trade policy")
    assert shell_result.outcome is ShellTurnOutcome.ANSWERED

    chat_result = ChatCommandResult(
        outcome=ChatTerminalOutcome.ANSWERED,
        exit_code=0,
        question=shell_result.question,
        db_path=session.snapshot.db_path,
        top_k=10,
        answer_text=shell_result.answer_text,
        generation_method=shell_result.generation_method,
        finding_kinds=shell_result.finding_kinds,
        citations=shell_result.citations,
    )
    chat_rendered = format_chat_command_output(chat_result)
    shell_rendered = format_shell_turn_output(shell_result)

    for detail in shell_result.citations:
        block = f"[{detail.citation_id}]"
        assert block in chat_rendered
        assert block in shell_rendered
    # The citations sections themselves are byte-for-byte identical.
    chat_citation_block = chat_rendered.split("--- Citations ---\n", 1)[1]
    shell_citation_block = shell_rendered.split("--- Citations ---\n", 1)[1]
    assert chat_citation_block == shell_citation_block


# --- Full REPL loop -------------------------------------------------------


def test_scripted_session_banner_two_questions_and_exit(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()

    stdin = _scripted_lines("trade policy", "trade policy evidence", "/exit")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: generator,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == ShellExitCode.SUCCESS
    out = stdout.getvalue()
    assert "=== econpapers interactive shell ===" in out
    assert "Database Path:" in out
    assert "Paper Count: 1" in out
    assert "Passage Count: 2" in out
    assert out.count("econpapers> ") == 3
    assert out.count("Outcome: answered") == 2
    assert stderr.getvalue() == ""
    assert len(generator.requests) == 2


def test_multiple_questions_use_one_corpus_load_and_one_retriever(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()
    retriever_construction_count = 0

    class CountingRetriever:
        def __init__(self, corpus: object) -> None:
            nonlocal retriever_construction_count
            retriever_construction_count += 1
            from econ_paper_cli.adapters.bm25 import BM25Retriever

            self._inner = BM25Retriever(corpus)

        def retrieve(self, request: object) -> tuple:
            return self._inner.retrieve(request)

    stdin = _scripted_lines("trade policy", "trade policy evidence", "/exit")
    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: generator,
        retriever_factory=CountingRetriever,
        stdin=stdin,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == ShellExitCode.SUCCESS
    assert retriever_construction_count == 1


def test_blank_line_redisplays_prompt_without_retrieval(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    stdin = _scripted_lines("", "", "/exit")
    stdout = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == ShellExitCode.SUCCESS
    assert stdout.getvalue().count("econpapers> ") == 3


def test_help_command_renders_help_text(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    stdin = _scripted_lines("/help", "/exit")
    stdout = io.StringIO()

    run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    out = stdout.getvalue()
    assert "=== Session Help ===" in out
    assert "/status" in out


def test_status_command_reports_generator_state_transition(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = AnsweringGenerator()
    stdin = _scripted_lines("/status", "trade policy", "/status", "/exit")
    stdout = io.StringIO()

    run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: generator,
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    out = stdout.getvalue()
    assert "not yet constructed" in out
    assert "ready (constructed this session)" in out


def test_quit_command_exits_successfully(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    stdin = _scripted_lines("/quit")

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        stdin=stdin,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert exit_code == ShellExitCode.SUCCESS


def test_eof_exits_successfully(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    stdin = io.StringIO("")  # Immediate EOF.

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        stdin=stdin,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert exit_code == ShellExitCode.SUCCESS


def test_keyboard_interrupt_exits_with_documented_code_and_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")

    class InterruptingStdin:
        def readline(self) -> str:
            raise KeyboardInterrupt

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        stdin=InterruptingStdin(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert exit_code == ShellExitCode.INTERRUPTED
    assert capsys.readouterr().err == ""


def test_failed_question_does_not_prevent_later_valid_question(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class OnceBoomGenerator(Generator):
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first call fails")
            return GenerationResponse(
                answer_text="Recovered answer.",
                citations=tuple(
                    Citation(
                        citation_id=f"e{item.rank}",
                        paper_id=item.passage.paper_id,
                        passage_id=item.passage.passage_id,
                    )
                    for item in request.evidence
                ),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
            )

    generator = OnceBoomGenerator()
    stdin = _scripted_lines("trade policy", "trade policy", "/exit")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: generator,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == ShellExitCode.SUCCESS
    assert "Error:" in stderr.getvalue()
    assert "Outcome: answered" in stdout.getvalue()


# --- Read-only guarantees -------------------------------------------------


def test_session_never_writes_to_config_or_database(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_backend = JSONConfigStorage(config_path)
    config_backend.save(_config())
    before = config_path.read_bytes()
    before_mtime = config_path.stat().st_mtime_ns

    db_path = tmp_path / "chat.db"
    write_storage = SQLiteStorage(db_path)
    write_storage.save_early_section_record(_record(tmp_path))
    write_storage.close()
    before_db_bytes = db_path.read_bytes()

    read_storage = SQLiteStorage(db_path, read_only=True)
    stdin = _scripted_lines("trade policy", "/status", "/help", "/exit")

    run_interactive_shell(
        ShellCommandOptions(),
        storage=read_storage,
        config_backend=config_backend,
        generator_provider=lambda: AnsweringGenerator(),
        stdin=stdin,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert config_path.read_bytes() == before
    assert config_path.stat().st_mtime_ns == before_mtime
    assert db_path.read_bytes() == before_db_bytes


# --- Working-directory independence --------------------------------------


def test_one_configuration_used_from_two_invocation_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "shared.db"
    storage = SQLiteStorage(db_path)
    storage.save_early_section_record(_record(tmp_path))
    storage.close()

    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config(db_path=db_path))

    dir_a = tmp_path / "invocation-a"
    dir_b = tmp_path / "invocation-b"
    dir_a.mkdir()
    dir_b.mkdir()

    monkeypatch.chdir(dir_a)
    session_a = open_shell_session(ShellCommandOptions(), config_backend=config_backend)
    monkeypatch.chdir(dir_b)
    session_b = open_shell_session(ShellCommandOptions(), config_backend=config_backend)

    assert session_a.snapshot.db_path == db_path
    assert session_b.snapshot.db_path == db_path
    assert session_a.snapshot.paper_count == session_b.snapshot.paper_count == 1
