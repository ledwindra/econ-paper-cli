"""Service tests for the bare ``econpapers`` interactive cited-chat shell."""

import io
from pathlib import Path

import pytest

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import LlamaCppOutputError
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
    Citation,
    EarlySectionLibraryRecord,
    ExtractedPDFPage,
    PDFConversionSettings,
    PDFDocumentMetadata,
    PDFExtractionResult,
    PDFSection,
    PDFSectionDetectionMethod,
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
from econ_paper_cli.protocols.generation import GeneratedClaim
from econ_paper_cli.services.chat_command import (
    ChatCommandResult,
    ChatTerminalOutcome,
    format_chat_command_output,
)
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.interactive_shell import (
    SHELL_PROMPT,
    ShellCommandOptions,
    ShellExitCode,
    ShellSessionError,
    ShellTurnOutcome,
    ShellTurnResult,
    _line_editing_available,
    _read_input_line,
    format_shell_show,
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
    source_filename: str = "paper.pdf",
    checksum: str = "c" * 64,
    abstract_text: str = "Abstract trade policy evidence.",
    introduction_text: str = "Introduction trade policy evidence.",
) -> EarlySectionLibraryRecord:
    text = f"{abstract_text}\n\n{introduction_text}"
    extraction = PDFExtractionResult(
        source_path=(tmp_path / source_filename).resolve(),
        pages=(ExtractedPDFPage(1, text),),
        page_count=1,
        metadata=PDFDocumentMetadata(title=title, author_text="Ada Economist"),
        extraction_method="synthetic",
        parser_version="1.0",
    )
    detection = PDFSectionDetectionResult(
        policy_version=DEFAULT_PDF_CONVERSION_SETTINGS.section_policy_version,
        sections=(
            PDFSection(
                kind=PDFSectionKind.ABSTRACT,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Abstract",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, 0, len(abstract_text)),),
                text=abstract_text,
            ),
            PDFSection(
                kind=PDFSectionKind.INTRODUCTION,
                detection_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
                observed_heading_text="Introduction",
                start_page_number=1,
                end_page_number=1,
                spans=(PDFSectionSpan(1, len(abstract_text) + 2, len(text)),),
                text=introduction_text,
            ),
        ),
        candidates=(),
        warnings=(),
    )
    settings = PDFConversionSettings(max_passage_characters=1200)
    conversion = convert_pdf_early_sections(
        extraction,
        detection,
        content_checksum=checksum,
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
    assert first.outcome is ShellTurnOutcome.INTERNAL_FAILURE
    assert first.generator_action is None
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
    assert failed.outcome is ShellTurnOutcome.TYPED_FAILURE
    assert failed.error_message is not None
    assert failed.generator_action is None

    recovered = session.ask("trade policy")
    assert recovered.outcome is ShellTurnOutcome.ANSWERED


def test_typed_failure_after_cached_generator_reuse_preserves_generator_action(
    tmp_path: Path,
) -> None:
    """A typed failure that happens *after* a cached generator was reused
    must still report generator_action == "reused", not discard it."""
    from econ_paper_cli.adapters.llama_cpp import LlamaCppReadinessError

    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class FlakyAfterFirstCallGenerator(Generator):
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            if self.calls == 1:
                return GenerationResponse(
                    answer_text="First answer.",
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
            raise LlamaCppReadinessError("model became unready")

    generator = FlakyAfterFirstCallGenerator()
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert first.generator_action == "constructed"

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.TYPED_FAILURE
    assert second.generator_action == "reused"


def test_internal_failure_after_generator_construction_preserves_generator_action(
    tmp_path: Path,
) -> None:
    """An internal (unexpected) failure right after constructing a fresh
    generator must still report generator_action == "constructed"."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class BoomAfterConstructionGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            raise RuntimeError("boom after construction")

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: BoomAfterConstructionGenerator(),
    )

    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.INTERNAL_FAILURE
    assert result.generator_action == "constructed"


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
    # GenerationResponseValidationError subclasses ValueError, so it is a
    # typed failure here, matching one-shot chat's exit_code=2 for the same
    # error (see test_chat_cli_service.py's citation-validation coverage).
    assert failed.outcome is ShellTurnOutcome.TYPED_FAILURE

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
    # CitationValidationError subclasses ValueError, so it is a typed
    # failure, matching one-shot chat's exit_code=2 for the same error.
    assert result.outcome is ShellTurnOutcome.TYPED_FAILURE


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
    assert result.outcome is ShellTurnOutcome.INTERNAL_FAILURE
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


def test_show_evidence_block_matches_one_shot_chat_show_evidence_flag(
    tmp_path: Path,
) -> None:
    """``/show <id>`` and one-shot ``--show-evidence`` must render the exact
    same evidence block for the same citation, including multiline and
    control-character passage text, since both call ``format_evidence_detail``
    on the same resolved ``ChatCitationDetail`` values."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(
        _record(
            tmp_path,
            abstract_text=(
                "Line one about trade policy.\n\n"
                "Line two about trade policy with a stray \x1b escape."
            ),
            introduction_text="Introduction trade policy evidence.",
        )
    )
    generator = AnsweringGenerator()

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )
    shell_result = session.ask("trade policy")
    assert shell_result.outcome is ShellTurnOutcome.ANSWERED
    citation = next(
        item for item in shell_result.citations if "Line one" in item.passage_text
    )

    shell_evidence = format_shell_show(session, citation.citation_id)

    chat_result = ChatCommandResult(
        outcome=ChatTerminalOutcome.ANSWERED,
        exit_code=0,
        question=shell_result.question,
        db_path=session.snapshot.db_path,
        top_k=10,
        answer_text=shell_result.answer_text,
        generation_method=shell_result.generation_method,
        finding_kinds=shell_result.finding_kinds,
        citations=(citation,),
    )
    chat_rendered = format_chat_command_output(chat_result, show_evidence=True)
    chat_evidence = chat_rendered.split("--- Evidence ---\n", 1)[1].rsplit(
        "\n\nEvidence scope:", 1
    )[0]

    assert shell_evidence == chat_evidence
    assert "Line one about trade policy." in shell_evidence
    assert "\x1b" not in shell_evidence


# --- Snapshot immutability against concurrent writes ----------------------


def test_later_write_to_same_paper_is_invisible_to_open_session_citations(
    tmp_path: Path,
) -> None:
    """A write landing after the session opened (same paper_id, changed
    title) must not change what an open session cites: citation resolution
    reads the immutable session snapshot, never a live re-read of storage."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path, title="Original Title"))
    generator = AnsweringGenerator()

    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=lambda: generator
    )

    # A concurrent write changes the paper's title in durable storage after
    # the session snapshot was already built.
    storage.save_early_section_record(_record(tmp_path, title="Updated Title"))
    assert (
        storage.get_early_section_record(_record(tmp_path).paper.paper_id).paper.title
        == "Updated Title"
    )

    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.ANSWERED
    assert len(result.citations) > 0
    assert all(detail.paper_title == "Original Title" for detail in result.citations)


def test_session_storage_closed_deterministically_on_repl_exit(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    close_calls = 0
    real_close = storage.close

    def tracking_close() -> None:
        nonlocal close_calls
        close_calls += 1
        real_close()

    storage.close = tracking_close  # type: ignore[method-assign]

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: AnsweringGenerator(),
        stdin=_scripted_lines("/exit"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == ShellExitCode.SUCCESS
    assert close_calls == 1


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


def _two_paper_shell_session(tmp_path: Path, generator: Generator) -> object:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(
        _record(
            tmp_path,
            title="Transit Paper",
            source_filename="transit.pdf",
            checksum="a" * 64,
            abstract_text="Abstract trade policy evidence about transit corridors.",
            introduction_text="Introduction trade policy evidence about transit.",
        )
    )
    storage.save_early_section_record(
        _record(
            tmp_path,
            title="Nutrition Paper",
            source_filename="nutrition.pdf",
            checksum="b" * 64,
            abstract_text="Abstract trade policy evidence about grocery nutrition.",
            introduction_text="Introduction trade policy evidence about groceries.",
        )
    )
    return open_shell_session(
        ShellCommandOptions(top_k=4),
        storage=storage,
        generator_provider=lambda: generator,
    )


class _ShellClaimGenerator(Generator):
    """Emit scripted claims citing the passage containing `cite_marker`."""

    def __init__(self, texts: tuple[str, ...], *, cite_marker: str) -> None:
        self.texts = texts
        self.cite_marker = cite_marker

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        chosen = next(
            item for item in request.evidence if self.cite_marker in item.passage.text
        )
        citation_id = f"e{chosen.rank}"
        return GenerationResponse(
            answer_text=" ".join(self.texts),
            citations=(
                Citation(
                    citation_id=citation_id,
                    paper_id=chosen.passage.paper_id,
                    passage_id=chosen.passage.passage_id,
                ),
            ),
            generation_method="fake-generator",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
            claims=tuple(
                GeneratedClaim(text=text, citation_ids=(citation_id,))
                for text in self.texts
            ),
        )


def test_shell_withholds_a_claim_that_misattributes_another_papers_wording(
    tmp_path: Path,
) -> None:
    """The shell is the bare `econpapers` entry point, so it must apply the
    same withholding rule as one-shot chat rather than showing a misattributed
    claim that only the chat path would have caught."""
    session = _two_paper_shell_session(
        tmp_path,
        _ShellClaimGenerator(
            (
                "Transit corridors are described.",
                "Grocery nutrition outcomes are described.",
            ),
            cite_marker="transit corridors",
        ),
    )

    result = session.ask("trade policy")

    assert result.outcome is ShellTurnOutcome.ANSWERED
    assert [claim.text for claim in result.claims] == [
        "Transit corridors are described."
    ]
    assert len(result.withheld_claims) == 1
    assert "grocery" in result.withheld_claims[0].leaked_terms
    assert "Grocery nutrition" not in (result.answer_text or "")
    session.close()


def test_shell_turn_with_every_claim_withheld_is_distinct_from_abstention(
    tmp_path: Path,
) -> None:
    """A withheld turn must keep its own outcome and still report the generator
    action, so a suppressed answer is not misread as an empty library and the
    session's generator lifecycle stays observable."""
    session = _two_paper_shell_session(
        tmp_path,
        _ShellClaimGenerator(
            ("Grocery nutrition outcomes are described.",),
            cite_marker="transit corridors",
        ),
    )

    result = session.ask("trade policy")

    assert result.outcome is ShellTurnOutcome.WITHHELD
    assert result.outcome is not ShellTurnOutcome.ABSTAINED
    assert result.generator_action == "constructed"
    assert result.generation_method == "fake-generator"
    assert result.claims == ()

    output = format_shell_turn_output(result)
    assert "Outcome: withheld" in output
    assert "Withheld Claims (1)" in output
    session.close()


# --- Follow-up questions -----------------------------------------------------


class _ResolvingGenerator(AnsweringGenerator):
    """Answering generator that also records and scripts follow-up rewrites."""

    def __init__(self, rewrite: str | None = "resolved standalone question") -> None:
        super().__init__()
        self.rewrite = rewrite
        self.resolve_calls: list[tuple[str, list[str]]] = []
        self.generated_questions: list[str] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.generated_questions.append(request.question)
        return super().generate(request)

    def resolve_follow_up(self, question: str, prior_questions: list[str]) -> str:
        self.resolve_calls.append((question, list(prior_questions)))
        if self.rewrite is None:
            raise LlamaCppOutputError("rewrite failed")
        return self.rewrite


def _answering_session(tmp_path: Path, generator: Generator):
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    return open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: generator,
    )


def test_the_first_question_is_never_resolved_against_absent_history(
    tmp_path: Path,
) -> None:
    """With no prior turn there is nothing to resolve against, so even a
    pronoun-heavy first question must be taken literally."""
    generator = _ResolvingGenerator()
    session = _answering_session(tmp_path, generator)

    result = session.ask("does it mention trade policy?")

    assert generator.resolve_calls == []
    assert result.resolved_question is None
    assert generator.generated_questions == ["does it mention trade policy?"]
    session.close()


def test_a_follow_up_is_rewritten_before_retrieval_and_generation(
    tmp_path: Path,
) -> None:
    """Both retrieval and generation need the referent, so the rewrite must
    reach the generator rather than only being displayed."""
    generator = _ResolvingGenerator(rewrite="what does the trade policy paper say?")
    session = _answering_session(tmp_path, generator)

    session.ask("trade policy")
    result = session.ask("what about it?")

    assert result.outcome is ShellTurnOutcome.ANSWERED
    assert result.resolved_question == "what does the trade policy paper say?"
    assert generator.generated_questions[-1] == "what does the trade policy paper say?"
    # The user's literal question is preserved alongside the rewrite.
    assert result.question == "what about it?"
    session.close()


def test_a_self_contained_follow_up_question_is_not_rewritten(
    tmp_path: Path,
) -> None:
    generator = _ResolvingGenerator()
    session = _answering_session(tmp_path, generator)

    session.ask("trade policy")
    result = session.ask("what is the effect of trade policy on wages?")

    assert generator.resolve_calls == []
    assert result.resolved_question is None
    session.close()


def test_a_failed_rewrite_answers_the_literal_question_instead_of_losing_the_turn(
    tmp_path: Path,
) -> None:
    """Resolution is best-effort: a rewrite failure must not cost the user
    their question. The follow-up here is independently retrievable, so a
    working fallback reaches the generator with the literal text rather than
    surfacing an internal failure."""
    generator = _ResolvingGenerator(rewrite=None)
    session = _answering_session(tmp_path, generator)

    session.ask("trade policy")
    result = session.ask("what about that trade policy evidence?")

    assert result.outcome is ShellTurnOutcome.ANSWERED
    assert result.resolved_question is None
    assert generator.generated_questions[-1] == "what about that trade policy evidence?"
    session.close()


def test_history_chains_resolve_against_earlier_resolved_text(
    tmp_path: Path,
) -> None:
    """A chain of follow-ups must resolve against fully-specified text, or one
    turn's referring expressions compound into the next."""
    generator = _ResolvingGenerator(rewrite="fully specified trade policy question")
    session = _answering_session(tmp_path, generator)

    session.ask("trade policy")
    session.ask("what about it?")
    session.ask("and that?")

    _question, prior = generator.resolve_calls[-1]
    assert "fully specified trade policy question" in prior
    session.close()


def test_reset_makes_the_next_question_literal_again(tmp_path: Path) -> None:
    generator = _ResolvingGenerator()
    session = _answering_session(tmp_path, generator)

    session.ask("trade policy")
    session.reset_history()
    result = session.ask("what about it?")

    assert generator.resolve_calls == []
    assert result.resolved_question is None
    session.close()


def test_only_answered_turns_become_follow_up_context(tmp_path: Path) -> None:
    """An abstention establishes no referent, so treating it as context would
    resolve the next follow-up against something the user was never told."""

    class _AbstainingGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            return GenerationResponse(
                answer_text="The supplied evidence is insufficient.",
                citations=(),
                generation_method="fake-generator",
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )

    session = _answering_session(tmp_path, _AbstainingGenerator())

    session.ask("trade policy")

    assert session.history.is_empty is True
    session.close()


def test_a_resolved_question_is_always_shown_to_the_user() -> None:
    """A rewrite the user disagrees with must be visible and correctable, not
    silently answered as if they had asked it."""
    rendered = format_shell_turn_output(
        ShellTurnResult(
            question="what about it?",
            outcome=ShellTurnOutcome.ANSWERED,
            answer_text="An answer.",
            resolved_question="what about the trade policy paper?",
        )
    )

    assert "Question: what about it?" in rendered
    assert "Interpreted as: what about the trade policy paper?" in rendered


# --- Terminal line editing ---------------------------------------------------


class _FakeTTY(io.StringIO):
    """In-memory stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def test_injected_streams_never_route_through_the_terminal_reader() -> None:
    """Even a stream that claims to be a TTY must be read directly when it was
    injected: routing it through input() would read the process's real stdin
    and hang the test suite."""
    assert _line_editing_available(_FakeTTY(), _FakeTTY()) is False


def test_a_non_terminal_stream_does_not_use_line_editing() -> None:
    """Piped input has no terminal to edit on."""
    assert _line_editing_available(None, io.StringIO()) is False


def test_a_stream_that_cannot_report_tty_status_falls_back_safely() -> None:
    """A closed or non-file-like stream must never stop the shell starting."""

    class _Broken:
        def isatty(self) -> bool:
            raise ValueError("stream is closed")

    assert _line_editing_available(None, _Broken()) is False


def test_direct_reads_strip_the_newline_and_signal_eof_distinctly() -> None:
    """An empty line and end-of-input are different events: input() returns ""
    for both, so the reader must report EOF as None or the loop would exit on
    a blank line."""
    stream = io.StringIO("a question\n\n")
    out = io.StringIO()

    assert _read_input_line(stream, out, line_editing=False) == "a question"
    assert _read_input_line(stream, out, line_editing=False) == ""
    assert _read_input_line(stream, out, line_editing=False) is None


def test_a_blank_line_does_not_end_the_session(tmp_path: Path) -> None:
    """Regression for the EOF/blank-line distinction, at the loop level."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    out, err = io.StringIO(), io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=lambda: AnsweringGenerator(),
        stdin=io.StringIO("\n\n/exit\n"),
        stdout=out,
        stderr=err,
    )

    assert exit_code == ShellExitCode.SUCCESS
    assert out.getvalue().count(SHELL_PROMPT) == 3


# --- /show evidence inspection ----------------------------------------------


def test_show_before_any_turn_reports_no_evidence(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )

    rendered = format_shell_show(session, None)

    assert "No evidence is available" in rendered


def test_bare_show_lists_available_citation_ids_after_an_answered_turn(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.ANSWERED

    rendered = format_shell_show(session, None)

    for citation in result.citations:
        assert citation.citation_id in rendered


def test_show_with_id_prints_the_full_stored_passage(tmp_path: Path) -> None:
    record = _record(tmp_path)
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(record)
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.ANSWERED
    citation = result.citations[0]

    rendered = format_shell_show(session, citation.citation_id)

    assert citation.passage_text in rendered
    assert citation.paper_title in rendered


def test_show_with_unknown_id_reports_a_plain_message(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.ANSWERED

    rendered = format_shell_show(session, "e99")

    assert "Unknown citation ID" in rendered
    assert "traceback" not in rendered.lower()


def test_show_after_a_non_answered_turn_reports_no_evidence(tmp_path: Path) -> None:
    """A withheld/abstained/no-matches turn carries no citations, so /show
    must not keep showing evidence from an earlier answered turn that no
    longer corresponds to the question just asked."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class AnswerThenAbstainGenerator(Generator):
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.call_count += 1
            if self.call_count == 1:
                return GenerationResponse(
                    answer_text="Answer for: trade policy",
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
            return GenerationResponse(
                answer_text="unused",
                citations=(),
                generation_method="fake-generator",
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=AnswerThenAbstainGenerator,
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert session.last_turn_citations

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.ABSTAINED

    rendered = format_shell_show(session, None)
    assert "No evidence is available" in rendered


def test_show_after_no_matches_reports_no_evidence(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert session.last_turn_citations

    # A query with no lexical overlap with the stored corpus never reaches
    # the generator, so this exercises the pre-generation NO_MATCHES path.
    second = session.ask("astronomy and astrophysics")
    assert second.outcome is ShellTurnOutcome.NO_MATCHES

    assert "No evidence is available" in format_shell_show(session, None)


def test_show_after_typed_failure_reports_no_evidence(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class AnswerThenTypedFailureGenerator(Generator):
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.call_count += 1
            if self.call_count == 1:
                return GenerationResponse(
                    answer_text="Answer for: trade policy",
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
            raise ValueError("typed failure on second call")

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=AnswerThenTypedFailureGenerator,
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert session.last_turn_citations

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.TYPED_FAILURE

    assert "No evidence is available" in format_shell_show(session, None)


def test_show_after_internal_failure_reports_no_evidence(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class AnswerThenInternalFailureGenerator(Generator):
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.call_count += 1
            if self.call_count == 1:
                return GenerationResponse(
                    answer_text="Answer for: trade policy",
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
            raise RuntimeError("internal failure on second call")

    session = open_shell_session(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=AnswerThenInternalFailureGenerator,
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert session.last_turn_citations

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.INTERNAL_FAILURE

    assert "No evidence is available" in format_shell_show(session, None)


def test_show_after_withheld_turn_reports_no_evidence(tmp_path: Path) -> None:
    """A withheld turn produced an answer that was fully suppressed, so it
    must clear evidence state exactly like a turn that never answered."""

    class AnswerThenWithholdGenerator(Generator):
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.call_count += 1
            chosen = next(
                item
                for item in request.evidence
                if "transit corridors" in item.passage.text
            )
            citation_id = f"e{chosen.rank}"
            if self.call_count == 1:
                return GenerationResponse(
                    answer_text="Transit corridors are described.",
                    citations=(
                        Citation(
                            citation_id=citation_id,
                            paper_id=chosen.passage.paper_id,
                            passage_id=chosen.passage.passage_id,
                        ),
                    ),
                    generation_method="fake-generator",
                    abstained=False,
                    abstention_reason=None,
                    finding_kinds=(FindingKind.DESCRIPTIVE,),
                    claims=(
                        GeneratedClaim(
                            text="Transit corridors are described.",
                            citation_ids=(citation_id,),
                        ),
                    ),
                )
            return GenerationResponse(
                answer_text="Grocery nutrition outcomes are described.",
                citations=(
                    Citation(
                        citation_id=citation_id,
                        paper_id=chosen.passage.paper_id,
                        passage_id=chosen.passage.passage_id,
                    ),
                ),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
                claims=(
                    GeneratedClaim(
                        text="Grocery nutrition outcomes are described.",
                        citation_ids=(citation_id,),
                    ),
                ),
            )

    session = _two_paper_shell_session(tmp_path, AnswerThenWithholdGenerator())

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    assert session.last_turn_citations

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.WITHHELD

    assert "No evidence is available" in format_shell_show(session, None)


def test_a_later_answered_turn_replaces_earlier_evidence(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path, title="Paper One"))
    storage.save_early_section_record(
        _record(
            tmp_path,
            title="Paper Two",
            source_filename="paper-two.pdf",
            checksum="d" * 64,
            abstract_text="Abstract trade policy second evidence.",
            introduction_text="Introduction trade policy second evidence.",
        )
    )
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )

    first = session.ask("trade policy")
    assert first.outcome is ShellTurnOutcome.ANSWERED
    first_ids = {citation.citation_id for citation in first.citations}

    second = session.ask("trade policy")
    assert second.outcome is ShellTurnOutcome.ANSWERED

    # last_turn_citations reflects only the latest turn, not an accumulation.
    current_ids = {citation.citation_id for citation in session.last_turn_citations}
    assert current_ids == {citation.citation_id for citation in second.citations}
    if first_ids - {c.citation_id for c in second.citations}:
        stale_id = next(iter(first_ids - {c.citation_id for c in second.citations}))
        assert "Unknown citation ID" in format_shell_show(session, stale_id)


def test_reset_clears_last_turn_citations(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.ANSWERED
    assert session.last_turn_citations

    session.reset_history()

    assert session.last_turn_citations == ()
    assert "No evidence is available" in format_shell_show(session, None)


def test_show_never_reads_storage_after_session_open(tmp_path: Path) -> None:
    """`/show` must render from the already-resolved session snapshot, never
    a live re-read — the same invariant that already governs citation
    resolution for a normal turn."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    session = open_shell_session(
        ShellCommandOptions(), storage=storage, generator_provider=AnsweringGenerator
    )
    result = session.ask("trade policy")
    assert result.outcome is ShellTurnOutcome.ANSWERED

    call_count = 0
    real_get = storage.get_early_section_record

    def tracking_get(paper_id: str) -> object:
        nonlocal call_count
        call_count += 1
        return real_get(paper_id)

    storage.get_early_section_record = tracking_get  # type: ignore[method-assign]

    format_shell_show(session, None)
    format_shell_show(session, result.citations[0].citation_id)

    assert call_count == 0


def test_show_dispatch_at_the_loop_level(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    stdin = _scripted_lines("trade policy", "/show", "/exit")
    stdout = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=AnsweringGenerator,
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == ShellExitCode.SUCCESS
    assert "Available citations:" in stdout.getvalue()


def test_show_with_id_dispatch_at_the_loop_level(tmp_path: Path) -> None:
    record = _record(tmp_path)
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(record)
    stdin = _scripted_lines("trade policy", "/show e1", "/exit")
    stdout = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=AnsweringGenerator,
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == ShellExitCode.SUCCESS
    out = stdout.getvalue()
    assert record.passages[0].text in out or record.passages[1].text in out


def test_show_with_tab_separated_id_dispatch_at_the_loop_level(
    tmp_path: Path,
) -> None:
    """A tab (or any run of whitespace) between "/show" and the ID must be
    recognized as the same command, not misread as an ordinary question that
    would invoke retrieval/generation and silently replace evidence state."""
    record = _record(tmp_path)
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(record)
    stdin = _scripted_lines("trade policy", "/show\te1", "/exit")
    stdout = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(),
        storage=storage,
        generator_provider=AnsweringGenerator,
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == ShellExitCode.SUCCESS
    out = stdout.getvalue()
    assert record.passages[0].text in out or record.passages[1].text in out
    # Only the one real question triggered generation, not "/show\te1".
    assert out.count("Outcome: answered") == 1


def test_help_and_banner_mention_show(tmp_path: Path) -> None:
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
    assert "/show" in out
