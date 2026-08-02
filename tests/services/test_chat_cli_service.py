"""Service integration tests for one-shot chat command execution and rendering."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from econ_paper_cli.adapters import BM25Retriever
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
from econ_paper_cli.protocols import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.services.chat_command import (
    ChatCommandOptions,
    ChatTerminalOutcome,
    execute_chat_command,
    format_chat_command_output,
)
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections


class FakeGenerator(Generator):
    """Fake generator that counts calls and returns a canonical answer."""

    def __init__(self, response: GenerationResponse) -> None:
        self.call_count = 0
        self.last_request: GenerationRequest | None = None
        self.response = response

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.call_count += 1
        self.last_request = request
        return self.response


class AnsweringGenerator(Generator):
    """Generator that returns citations matching the supplied evidence."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            answer_text="Trade policy evidence supports a descriptive answer.",
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


def _options(tmp_path: Path, question: str = "trade policy") -> ChatCommandOptions:
    return ChatCommandOptions(
        question=question,
        executable_path=tmp_path / "llama-cli",
        model_path=tmp_path / "model.gguf",
        model_id="test-model",
        model_bytes=11,
        model_checksum="b" * 64,
        db_path=tmp_path / "chat.db",
        top_k=2,
    )


def _prepare_chat_database(
    tmp_path: Path, record: EarlySectionLibraryRecord | None = None
) -> Path:
    db_path = tmp_path / "chat.db"
    storage = SQLiteStorage(db_path)
    storage.initialize()
    if record is not None:
        storage.save_early_section_record(record)
    storage.close()
    return db_path


def _database_snapshot(db_path: Path) -> tuple[bytes, tuple[int, int, int], int]:
    storage = SQLiteStorage(db_path)
    schema_version = storage.get_schema_version()
    storage.close()
    stat = db_path.stat()
    return (
        db_path.read_bytes(),
        (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns),
        schema_version,
    )


def test_answered_chat_renders_citations_from_durable_storage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    class EchoGenerator(Generator):
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.call_count += 1
            return GenerationResponse(
                answer_text="Trade policy evidence supports a descriptive answer.",
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

    generator = EchoGenerator()

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: generator,
    )
    assert result.outcome is ChatTerminalOutcome.ANSWERED
    assert generator.call_count == 1
    output = format_chat_command_output(result)
    assert "Outcome: answered" in output
    assert "Generation Method: fake-generator" in output
    assert "Finding Kinds: descriptive" in output
    assert "[e1]" in output and "[e2]" in output
    assert "Evidence scope: stored Abstract and Introduction passages only." in output

    first = result.citations[0]
    expected = (
        "=== One-Shot Chat Result ===\n"
        f"Question: {result.question}\n"
        f"Database Path: {result.db_path}\n"
        "Top K: 2\n"
        "Outcome: answered\n"
        "Answer: Trade policy evidence supports a descriptive answer.\n"
        "Generation Method: fake-generator\n"
        "Finding Kinds: descriptive\n"
        "\n--- Citations ---\n"
        f"[{first.citation_id}]\n"
        f"  Paper Title: {first.paper_title}\n"
        f"  Section Heading: {first.section_heading}\n"
        f"  Page Range: {first.page_start}\n"
        f"  Paper ID: {first.paper_id}\n"
        f"  Passage ID: {first.passage_id}\n"
        f"  Retrieval Rank: {first.retrieval_rank}\n"
        f"  Retrieval Score: {format(first.retrieval_score, '.12g')}\n"
        f"  Source Path: {first.source_path}\n"
        f"[{result.citations[1].citation_id}]\n"
        f"  Paper Title: {result.citations[1].paper_title}\n"
        f"  Section Heading: {result.citations[1].section_heading}\n"
        f"  Page Range: {result.citations[1].page_start}\n"
        f"  Paper ID: {result.citations[1].paper_id}\n"
        f"  Passage ID: {result.citations[1].passage_id}\n"
        f"  Retrieval Rank: {result.citations[1].retrieval_rank}\n"
        f"  Retrieval Score: {format(result.citations[1].retrieval_score, '.12g')}\n"
        f"  Source Path: {result.citations[1].source_path}\n"
        "\nEvidence scope: stored Abstract and Introduction passages only."
    )
    assert output == expected
    assert capsys.readouterr().err == ""
    assert storage.count_papers() == 1
    assert storage.count_passages() == 2


def test_empty_library_never_calls_generator_provider(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    called = 0

    def provider(_: ChatCommandOptions) -> Generator:
        nonlocal called
        called += 1
        raise AssertionError("generator provider must not be called for empty library")

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=provider,
    )

    assert result.outcome is ChatTerminalOutcome.EMPTY_LIBRARY
    assert result.exit_code == 1
    assert called == 0
    assert result.no_answer_reason == "No stored passages are available."


def test_no_matches_never_calls_generator_provider(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    called = 0

    def provider(_: ChatCommandOptions) -> Generator:
        nonlocal called
        called += 1
        raise AssertionError("generator provider must not be called for no matches")

    result = execute_chat_command(
        _options(tmp_path, question="astronomy and astrophysics"),
        storage=storage,
        generator_provider=provider,
    )

    assert result.outcome is ChatTerminalOutcome.NO_MATCHES
    assert result.exit_code == 1
    assert called == 0
    assert result.no_answer_reason == "BM25 returned no evidence for the question."


def test_abstained_chat_invokes_generator_once_and_renders_reason(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    fake = FakeGenerator(
        GenerationResponse(
            answer_text="The evidence is insufficient.",
            citations=(),
            generation_method="fake-generator",
            abstained=True,
            abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
            finding_kinds=(),
        )
    )

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: fake,
    )

    assert result.outcome is ChatTerminalOutcome.ABSTAINED
    assert result.exit_code == 1
    assert fake.call_count == 1
    assert result.generation_method == "fake-generator"
    assert result.citations == ()
    assert "Generator abstained: insufficient evidence." in format_chat_command_output(
        result
    )


def test_corrupt_durable_metadata_surfaces_as_failure(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    record = _record(tmp_path)
    storage.save_early_section_record(record)
    conn = storage._conn
    assert conn is not None
    conn.execute(
        "UPDATE passage_source_fragments SET source_text = 'corrupt' WHERE passage_id = ?",
        (record.passages[0].passage_id,),
    )
    conn.commit()

    generator = FakeGenerator(
        GenerationResponse(
            answer_text="A grounded answer.",
            citations=(
                Citation(
                    citation_id="e1",
                    paper_id=record.paper.paper_id,
                    passage_id=record.passages[0].passage_id,
                ),
            ),
            generation_method="fake-generator",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )
    )

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 3
    assert generator.call_count == 0
    assert "source_text" in (result.error_message or "")


def test_chat_does_not_write_to_storage(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    record = _record(tmp_path)
    storage.save_early_section_record(record)
    before = (storage.count_papers(), storage.count_passages())

    class EchoGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            return GenerationResponse(
                answer_text="A grounded answer.",
                citations=(
                    Citation(
                        citation_id="e1",
                        paper_id=request.evidence[0].passage.paper_id,
                        passage_id=request.evidence[0].passage.passage_id,
                    ),
                ),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
            )

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: EchoGenerator(),
    )

    assert result.outcome is ChatTerminalOutcome.ANSWERED
    assert (storage.count_papers(), storage.count_passages()) == before


def test_missing_chat_database_is_not_created(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"
    called = 0

    def provider(_: ChatCommandOptions) -> Generator:
        nonlocal called
        called += 1
        raise AssertionError("generator provider must not be called when open fails")

    options = ChatCommandOptions(
        question="trade policy",
        executable_path=tmp_path / "llama-cli",
        model_path=tmp_path / "model.gguf",
        model_id="test-model",
        model_bytes=11,
        model_checksum="b" * 64,
        db_path=db_path,
    )
    result = execute_chat_command(options, storage=None, generator_provider=provider)

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 2
    assert called == 0
    assert not db_path.exists()


@pytest.mark.parametrize(
    (
        "scenario",
        "question",
        "expected_outcome",
        "expected_reason",
        "expected_exit_code",
        "generator_factory",
        "retriever_factory",
    ),
    [
        (
            "empty",
            "trade policy",
            ChatTerminalOutcome.EMPTY_LIBRARY,
            "No stored passages are available.",
            1,
            None,
            None,
        ),
        (
            "no_match",
            "astronomy and astrophysics",
            ChatTerminalOutcome.NO_MATCHES,
            "BM25 returned no evidence for the question.",
            1,
            None,
            None,
        ),
        (
            "abstained",
            "trade policy",
            ChatTerminalOutcome.ABSTAINED,
            "Generator abstained: insufficient evidence.",
            1,
            lambda: FakeGenerator(
                GenerationResponse(
                    answer_text="The evidence is insufficient.",
                    citations=(),
                    generation_method="fake-generator",
                    abstained=True,
                    abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                    finding_kinds=(),
                )
            ),
            None,
        ),
        (
            "answered",
            "trade policy",
            ChatTerminalOutcome.ANSWERED,
            None,
            0,
            lambda: AnsweringGenerator(),
            None,
        ),
        (
            "failed_retriever",
            "trade policy",
            ChatTerminalOutcome.FAILED,
            "boom",
            3,
            None,
            lambda _corpus: _BoomRetriever(),
        ),
    ],
)
def test_file_backed_chat_runs_leave_database_unchanged(
    tmp_path: Path,
    scenario: str,
    question: str,
    expected_outcome: ChatTerminalOutcome,
    expected_reason: str | None,
    expected_exit_code: int,
    generator_factory: Callable[[], Generator] | None,
    retriever_factory: Callable[[object], object] | None,
) -> None:
    db_path = _prepare_chat_database(
        tmp_path, None if scenario == "empty" else _record(tmp_path)
    )
    before = _database_snapshot(db_path)
    generator = generator_factory() if generator_factory is not None else None

    def provider(_: ChatCommandOptions) -> Generator:
        if generator is None:
            raise AssertionError("generator provider must not be called")
        return generator

    retriever = retriever_factory or BM25Retriever

    result = execute_chat_command(
        _options(tmp_path, question=question),
        storage=None,
        retriever_factory=retriever,
        generator_provider=provider,
    )

    after = _database_snapshot(db_path)
    assert result.outcome is expected_outcome
    assert result.exit_code == expected_exit_code
    if expected_reason is not None:
        if expected_outcome is ChatTerminalOutcome.FAILED:
            assert result.error_message == expected_reason
        else:
            assert result.no_answer_reason == expected_reason
    assert before == after


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (
            "unknown",
            "[Uu]nknown citation_id 'e3'",
        ),
        (
            "duplicate",
            "Duplicate citation_id 'e1'",
        ),
        (
            "reordered",
            "[Cc]itations must follow supplied-evidence rank order.*e1.*e2",
        ),
        (
            "wrong-paper",
            r"Citation 'e1' has paper_id 'wrong-paper'.*expected 'paper-.*'",
        ),
        (
            "wrong-passage",
            r"Citation 'e1' has passage_id 'paper-1:wrong-passage'.*expected 'passage-.*'",
        ),
    ],
)
def test_chat_rejects_invalid_citations(
    tmp_path: Path, case: str, message: str
) -> None:
    record = _record(tmp_path)
    db_path = _prepare_chat_database(tmp_path, record)
    before = _database_snapshot(db_path)
    paper_id = record.paper.paper_id
    passage_1 = record.passages[0].passage_id
    passage_2 = record.passages[1].passage_id

    if case == "unknown":
        citations = (Citation("e3", paper_id, passage_1),)
    elif case == "duplicate":
        citations = (
            Citation("e1", paper_id, passage_1),
            Citation("e1", paper_id, passage_1),
        )
    elif case == "reordered":
        citations = (
            Citation("e2", paper_id, passage_2),
            Citation("e1", paper_id, passage_1),
        )
    elif case == "wrong-paper":
        citations = (Citation("e1", "wrong-paper", passage_1),)
    elif case == "wrong-passage":
        citations = (Citation("e1", paper_id, "paper-1:wrong-passage"),)
    else:
        raise AssertionError(f"unexpected case {case!r}")

    class InvalidCitationGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            return GenerationResponse(
                answer_text="A grounded answer.",
                citations=citations,
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
            )

    result = execute_chat_command(
        _options(tmp_path),
        storage=None,
        generator_provider=lambda _: InvalidCitationGenerator(),
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 2
    assert re.search(message, result.error_message or "")
    assert before == _database_snapshot(db_path)


def test_corrupt_zero_passage_library_surfaces_as_failure(tmp_path: Path) -> None:
    db_path = _prepare_chat_database(tmp_path, _record(tmp_path))
    storage = SQLiteStorage(db_path)
    storage.initialize()
    conn = storage._conn
    assert conn is not None
    conn.execute(
        "DELETE FROM passages WHERE paper_id = ?",
        (storage.list_paper_ids()[0],),
    )
    conn.commit()
    storage.close()
    before = _database_snapshot(db_path)

    result = execute_chat_command(
        _options(tmp_path),
        storage=None,
        generator_provider=lambda _: FakeGenerator(
            GenerationResponse(
                answer_text="unused",
                citations=(),
                generation_method="fake-generator",
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )
        ),
    )

    after = _database_snapshot(db_path)
    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 3
    assert "Missing passage rows" in (result.error_message or "")
    assert before == after


class _BoomRetriever:
    def retrieve(self, request: object) -> object:
        raise RuntimeError("boom")


def test_unexpected_generator_exception_is_reported_as_failed(
    tmp_path: Path,
) -> None:
    db_path = _prepare_chat_database(tmp_path, _record(tmp_path))
    before = _database_snapshot(db_path)

    class BoomGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            raise RuntimeError("generator boom")

    result = execute_chat_command(
        _options(tmp_path),
        storage=None,
        generator_provider=lambda _: BoomGenerator(),
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 3
    assert "generator boom" in (result.error_message or "")
    assert before == _database_snapshot(db_path)


def test_unexpected_storage_exception_is_reported_as_failed() -> None:
    class BoomStorage:
        def initialize(self) -> None:
            return None

        def close(self) -> None:
            return None

        def list_early_section_records(self) -> tuple[EarlySectionLibraryRecord, ...]:
            raise RuntimeError("storage boom")

        def load_corpus(self) -> object:
            raise AssertionError("load_corpus must not be called after storage failure")

    result = execute_chat_command(
        ChatCommandOptions(
            question="trade policy",
            executable_path=Path("/tmp/llama-cli"),
            model_path=Path("/tmp/model.gguf"),
            model_id="test-model",
            model_bytes=11,
            model_checksum="b" * 64,
            db_path=Path("/tmp/chat.db"),
        ),
        storage=BoomStorage(),  # type: ignore[arg-type]
        generator_provider=lambda _: FakeGenerator(
            GenerationResponse(
                answer_text="unused",
                citations=(),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
            )
        ),
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 3
    assert "storage boom" in (result.error_message or "")
