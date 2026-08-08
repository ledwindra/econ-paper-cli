"""Service integration tests for one-shot chat command execution and rendering."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from econ_paper_cli.adapters import BM25Retriever
from econ_paper_cli.adapters.config_storage import JSONConfigStorage
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
    ChatCitationDetail,
    ChatCommandOptions,
    ChatTerminalOutcome,
    _build_llama_cpp_generator,
    _render_citation_lines,
    execute_chat_command,
    format_chat_command_output,
    format_evidence_detail,
)
from econ_paper_cli.services.config_resolution import (
    LazyConfigLoader,
    RuntimeModelOverrides,
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
    checksum: str = "c" * 64,
    source_filename: str = "paper.pdf",
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


def _options(
    tmp_path: Path, question: str = "trade policy", *, top_k: int = 2
) -> ChatCommandOptions:
    return ChatCommandOptions(
        question=question,
        executable_path=tmp_path / "llama-cli",
        model_path=tmp_path / "model.gguf",
        model_id="test-model",
        model_bytes=11,
        model_checksum="b" * 64,
        db_path=tmp_path / "chat.db",
        top_k=top_k,
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

    colored_output = format_chat_command_output(result, color=True)
    assert "\033[34m[e1, e2]\033[0m" in colored_output
    assert "\033[34m[e1]\033[0m" in colored_output
    assert "\033[34m" not in output

    first = result.citations[0]
    second = result.citations[1]
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
        # Both passages belong to one paper, so the paper-level facts appear
        # exactly once and the two passages are nested beneath them.
        f"[{first.citation_id}, {second.citation_id}] {first.paper_title}\n"
        f"  Paper ID: {first.paper_id}\n"
        f"  Source Path: {first.source_path}\n"
        "  Passages: 2\n"
        f"    [{first.citation_id}]\n"
        f"      Section Heading: {first.section_heading}\n"
        f"      Page Range: {first.page_start}\n"
        f"      Passage ID: {first.passage_id}\n"
        f"      Retrieval Rank: {first.retrieval_rank}\n"
        f"      Retrieval Score: {format(first.retrieval_score, '.12g')}\n"
        f"    [{second.citation_id}]\n"
        f"      Section Heading: {second.section_heading}\n"
        f"      Page Range: {second.page_start}\n"
        f"      Passage ID: {second.passage_id}\n"
        f"      Retrieval Rank: {second.retrieval_rank}\n"
        f"      Retrieval Score: {format(second.retrieval_score, '.12g')}\n"
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


def test_one_corrupt_record_does_not_fail_chat_when_a_healthy_record_exists(
    tmp_path: Path,
) -> None:
    """A single stale/corrupt early-section record (e.g. one written under a
    since-changed conversion/section-policy fingerprint formula) must not
    take down chat for the rest of an otherwise-healthy library — only a
    library where *every* record is unreadable should surface as FAILED."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    # Deliberately unrelated to the "trade policy" question below, so BM25
    # never retrieves (and therefore never has to cite) this record — this
    # isolates the test to the actual observed bug (list_early_section_
    # records() aborting the whole library) rather than the separate,
    # narrower question of citing evidence from a record that fails
    # reconstruction only when its own citation is resolved.
    corrupt_record = _record(
        tmp_path,
        checksum="d" * 64,
        source_filename="corrupt.pdf",
        abstract_text="Weather patterns in coastal regions vary widely.",
        introduction_text="This unrelated topic never matches the query below.",
    )
    healthy_record = _record(tmp_path, checksum="e" * 64, source_filename="healthy.pdf")
    storage.save_early_section_record(corrupt_record)
    storage.save_early_section_record(healthy_record)
    conn = storage._conn
    assert conn is not None
    conn.execute(
        "UPDATE passage_source_fragments SET source_text = 'corrupt' WHERE passage_id = ?",
        (corrupt_record.passages[0].passage_id,),
    )
    conn.commit()

    generator = AnsweringGenerator()
    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.ANSWERED
    assert result.citations
    assert all(
        citation.paper_id == healthy_record.paper.paper_id
        for citation in result.citations
    )


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
    # e1/e2 reach the generator in *retrieval rank* order, which tie-breaks
    # on ascending passage_id — not in the record's own passage order.
    # Deriving them from record.passages[0]/[1] would silently depend on the
    # settings fingerprint, since passage IDs are derived from it.
    passage_1, passage_2 = sorted(passage.passage_id for passage in record.passages[:2])

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


# --- Issue 54: durable runtime/model configuration resolution -------------


def _no_identity_options(
    tmp_path: Path, question: str = "trade policy"
) -> ChatCommandOptions:
    return ChatCommandOptions(
        question=question,
        db_path=tmp_path / "chat.db",
        top_k=2,
    )


def test_empty_library_does_not_require_configuration(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")
    missing_config = JSONConfigStorage(tmp_path / "no-such-dir" / "config.json")

    result = execute_chat_command(
        _no_identity_options(tmp_path),
        storage=storage,
        config_backend=missing_config,
    )

    assert result.outcome is ChatTerminalOutcome.EMPTY_LIBRARY
    assert missing_config.load() is None


def test_no_matches_does_not_require_configuration(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    missing_config = JSONConfigStorage(tmp_path / "no-such-dir" / "config.json")

    result = execute_chat_command(
        _no_identity_options(tmp_path, question="astronomy and astrophysics"),
        storage=storage,
        config_backend=missing_config,
    )

    assert result.outcome is ChatTerminalOutcome.NO_MATCHES
    assert missing_config.load() is None


def test_matched_chat_resolves_generator_from_durable_config(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        LocalRuntimeModelConfig(
            executable_path=tmp_path / "configured-exe",
            model_path=tmp_path / "configured-model.gguf",
            model_id="configured-model",
            model_bytes=42,
            model_checksum="d" * 64,
        )
    )
    generator = FakeGenerator(
        GenerationResponse(
            answer_text="Trade policy evidence supports a descriptive answer.",
            citations=(),
            generation_method="fake-generator",
            abstained=True,
            abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
            finding_kinds=(),
        )
    )

    result = execute_chat_command(
        _no_identity_options(tmp_path),
        storage=storage,
        config_backend=config_backend,
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.ABSTAINED
    assert generator.call_count == 1


def test_partial_cli_override_returns_failed_with_typed_exit_code(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        LocalRuntimeModelConfig(
            executable_path=tmp_path / "configured-exe",
            model_path=tmp_path / "configured-model.gguf",
            model_id="configured-model",
            model_bytes=42,
            model_checksum="d" * 64,
        )
    )
    options = ChatCommandOptions(
        question="trade policy",
        db_path=tmp_path / "chat.db",
        model_id="cli-only-model-id",
    )

    result = execute_chat_command(
        options,
        storage=storage,
        config_backend=config_backend,
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 2
    assert "Partial runtime/model override" in (result.error_message or "")


def test_no_cli_and_no_config_returns_failed_with_typed_exit_code(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    missing_config = JSONConfigStorage(tmp_path / "absent" / "config.json")

    result = execute_chat_command(
        _no_identity_options(tmp_path),
        storage=storage,
        config_backend=missing_config,
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 2
    assert "No runtime/model configuration is available" in (result.error_message or "")


def test_db_path_resolution_prefers_cli_then_config_then_default(
    tmp_path: Path,
) -> None:
    config_db_path = tmp_path / "config-chosen.db"
    storage = SQLiteStorage(config_db_path)
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        LocalRuntimeModelConfig(
            executable_path=tmp_path / "configured-exe",
            model_path=tmp_path / "configured-model.gguf",
            model_id="configured-model",
            model_bytes=42,
            model_checksum="d" * 64,
            db_path=config_db_path,
        )
    )
    options = ChatCommandOptions(question="trade policy")

    result = execute_chat_command(
        options,
        storage=storage,
        config_backend=config_backend,
    )

    assert result.db_path == config_db_path


class RaisingConfigBackend:
    """Config backend double that fails the test if load() is ever called."""

    @property
    def config_path(self) -> Path:
        return Path("/unused/config.json")

    def exists(self) -> bool:
        return False

    def load(self) -> LocalRuntimeModelConfig | None:
        raise AssertionError("config load() must not be called on this path")

    def save(self, config: LocalRuntimeModelConfig) -> None:
        raise AssertionError("save() must not be called on this path")


def test_empty_library_never_calls_config_load_with_explicit_db_path(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "empty.db")

    result = execute_chat_command(
        ChatCommandOptions(question="trade policy", db_path=tmp_path / "empty.db"),
        storage=storage,
        config_backend=RaisingConfigBackend(),
    )

    assert result.outcome is ChatTerminalOutcome.EMPTY_LIBRARY


def test_no_matches_never_calls_config_load_with_explicit_db_path(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    result = execute_chat_command(
        ChatCommandOptions(
            question="astronomy and astrophysics", db_path=tmp_path / "chat.db"
        ),
        storage=storage,
        config_backend=RaisingConfigBackend(),
    )

    assert result.outcome is ChatTerminalOutcome.NO_MATCHES


def test_fully_specified_cli_override_with_explicit_db_path_never_calls_config_load(
    tmp_path: Path,
) -> None:
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    generator = FakeGenerator(
        GenerationResponse(
            answer_text="unused",
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
        config_backend=RaisingConfigBackend(),
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.ABSTAINED
    assert generator.call_count == 1


def test_partial_override_on_no_matches_path_is_rejected_eagerly(
    tmp_path: Path,
) -> None:
    """A partial CLI identity override must be rejected before chat ever
    decides whether it would need a generator."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))
    options = ChatCommandOptions(
        question="astronomy and astrophysics",
        db_path=tmp_path / "chat.db",
        model_id="cli-only-model-id",
    )

    result = execute_chat_command(
        options,
        storage=storage,
        config_backend=RaisingConfigBackend(),
    )

    assert result.outcome is ChatTerminalOutcome.FAILED
    assert result.exit_code == 2
    assert "Partial runtime/model override" in (result.error_message or "")


def _config_for_threads_timeout(
    tmp_path: Path, **overrides: object
) -> LocalRuntimeModelConfig:
    base: dict[str, object] = {
        "executable_path": tmp_path / "config-exe",
        "model_path": tmp_path / "config-model.gguf",
        "model_id": "config-model",
        "model_bytes": 10,
        "model_checksum": "a" * 64,
        "threads": 8,
        "timeout_seconds": 60.0,
    }
    base.update(overrides)
    return LocalRuntimeModelConfig(**base)


def test_build_generator_uses_persisted_managed_runtime_identity_after_restart(
    tmp_path: Path,
) -> None:
    """Issue #58: a config-sourced identity recorded by managed-runtime
    provisioning must reach the generator actually constructed at restart —
    not silently fall back to LlamaCppConfig's hard-coded adapter default."""
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(
        _config_for_threads_timeout(
            tmp_path,
            runtime_id="llama.cpp-b99999",
            runtime_version_marker="99999",
        )
    )
    lazy_config = LazyConfigLoader(config_backend)

    generator = _build_llama_cpp_generator(RuntimeModelOverrides(), lazy_config)

    # A distinct, non-default value proves the persisted identity was
    # actually threaded through, not merely coinciding with the adapter's
    # own default (which happens to also be "llama.cpp-b10199"/"10199").
    assert generator._config.runtime_id == "llama.cpp-b99999"
    assert generator._config.runtime_version_marker == "99999"


def test_build_generator_falls_back_to_adapter_default_identity_when_unset(
    tmp_path: Path,
) -> None:
    """A durable config from an explicit --llama-cpp-path setup (no recorded
    identity) must still resolve to LlamaCppConfig's own default, unchanged."""
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config_for_threads_timeout(tmp_path))
    lazy_config = LazyConfigLoader(config_backend)

    generator = _build_llama_cpp_generator(RuntimeModelOverrides(), lazy_config)

    assert generator._config.runtime_id == "llama.cpp-b10199"
    assert generator._config.runtime_version_marker == "10199"


def test_build_generator_honors_durable_threads_and_timeout_when_config_loaded(
    tmp_path: Path,
) -> None:
    """A fully-specified CLI identity must still pick up durable threads/timeout
    when the config was already loaded for another reason (db-path fallback)."""
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config_for_threads_timeout(tmp_path))
    lazy_config = LazyConfigLoader(config_backend)
    lazy_config.get()  # Simulate an earlier load for --db-path resolution.

    overrides = RuntimeModelOverrides(
        executable_path=tmp_path / "cli-exe",
        model_path=tmp_path / "cli-model.gguf",
        model_id="cli-model",
        model_bytes=99,
        model_checksum="b" * 64,
    )
    generator = _build_llama_cpp_generator(overrides, lazy_config)

    assert generator._config.threads == 8
    assert generator._config.timeout_seconds == 60.0


def test_build_generator_explicit_cli_threads_and_timeout_override_durable_config(
    tmp_path: Path,
) -> None:
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(_config_for_threads_timeout(tmp_path))
    lazy_config = LazyConfigLoader(config_backend)
    lazy_config.get()

    overrides = RuntimeModelOverrides(
        executable_path=tmp_path / "cli-exe",
        model_path=tmp_path / "cli-model.gguf",
        model_id="cli-model",
        model_bytes=99,
        model_checksum="b" * 64,
        threads=2,
        timeout=15.0,
    )
    generator = _build_llama_cpp_generator(overrides, lazy_config)

    assert generator._config.threads == 2
    assert generator._config.timeout_seconds == 15.0


def test_build_generator_no_config_loaded_retains_documented_defaults(
    tmp_path: Path,
) -> None:
    """When lazy_config was never loaded (peek() is used, not get()), a fully
    specified CLI identity falls back to documented defaults, not durable
    config values that were never actually read."""
    lazy_config = LazyConfigLoader(RaisingConfigBackend())

    overrides = RuntimeModelOverrides(
        executable_path=tmp_path / "cli-exe",
        model_path=tmp_path / "cli-model.gguf",
        model_id="cli-model",
        model_bytes=99,
        model_checksum="b" * 64,
    )
    generator = _build_llama_cpp_generator(overrides, lazy_config)

    assert generator._config.threads is None
    assert generator._config.timeout_seconds == 300.0


def _two_paper_storage(tmp_path: Path) -> SQLiteStorage:
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
    return storage


class _ClaimGenerator(Generator):
    """Emit scripted claims all citing the passage containing `cite_marker`.

    Binding by content rather than by rank keeps the fixture independent of
    BM25 ordering: which paper lands at rank 1 is not part of this contract,
    but which paper a claim is attributed to is exactly what is under test.
    """

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


def test_claim_misattributing_another_papers_wording_is_withheld_from_the_answer(
    tmp_path: Path,
) -> None:
    """A claim whose wording belongs to a paper it does not cite never reaches
    the user, and the surviving claims still answer the question."""
    storage = _two_paper_storage(tmp_path)
    generator = _ClaimGenerator(
        (
            "Transit corridors are described.",
            "Grocery nutrition outcomes are described.",
        ),
        cite_marker="transit corridors",
    )

    result = execute_chat_command(
        _options(tmp_path, top_k=4),
        storage=storage,
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.ANSWERED
    assert [claim.text for claim in result.claims] == [
        "Transit corridors are described."
    ]
    assert len(result.withheld_claims) == 1
    assert "grocery" in result.withheld_claims[0].leaked_terms
    assert result.answer_text == "Transit corridors are described."
    assert "Grocery nutrition" not in (result.answer_text or "")
    # response.finding_kinds described the whole original two-claim response,
    # which is no longer an accurate label once one claim was withheld --
    # e.g. the withheld claim could have been the only causal one. Reporting
    # nothing is safer than reporting a label that may no longer hold for
    # the surviving answer.
    assert result.finding_kinds == ()


def test_a_response_whose_every_claim_is_withheld_is_not_reported_as_abstention(
    tmp_path: Path,
) -> None:
    """The generator did produce an answer, so collapsing this into ABSTAINED
    would tell the user the library had nothing to say, which is false."""
    storage = _two_paper_storage(tmp_path)
    generator = _ClaimGenerator(
        ("Grocery nutrition outcomes are described.",),
        cite_marker="transit corridors",
    )

    result = execute_chat_command(
        _options(tmp_path, top_k=4),
        storage=storage,
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.WITHHELD
    assert result.outcome is not ChatTerminalOutcome.ABSTAINED
    assert result.exit_code == 1
    assert result.claims == ()
    assert len(result.withheld_claims) == 1
    # The generation method survives the withholding path: a user reporting a
    # suppressed answer needs to say which runtime produced it.
    assert result.generation_method == "fake-generator"

    output = format_chat_command_output(result)
    assert "Outcome: withheld" in output
    assert "Withheld Claims (1)" in output
    assert "grocery" in output


def test_default_output_shows_answer_by_source_without_show_evidence(
    tmp_path: Path,
) -> None:
    """The per-claim '--- Answer by Source ---' breakdown is part of the
    default rendering contract (independent of --show-evidence), so a reader
    can check any one sentence against the paper it came from without
    matching opaque citation identifiers by hand."""
    storage = _two_paper_storage(tmp_path)
    generator = _ClaimGenerator(
        ("Transit corridors are described.",),
        cite_marker="transit corridors",
    )

    result = execute_chat_command(
        _options(tmp_path, top_k=4),
        storage=storage,
        generator_provider=lambda _: generator,
    )

    assert result.outcome is ChatTerminalOutcome.ANSWERED
    assert result.claims

    output = format_chat_command_output(result)
    assert "--- Answer by Source ---" in output
    assert "1. Transit corridors are described." in output
    assert "Source:" in output


def test_withholding_drops_citations_no_surviving_claim_relies_on(
    tmp_path: Path,
) -> None:
    """Citations are the user's audit trail, so one left behind by a withheld
    claim would point at a paper nothing in the answer came from."""
    storage = _two_paper_storage(tmp_path)

    class _SplitGenerator(Generator):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            first = next(
                item
                for item in request.evidence
                if "transit corridors" in item.passage.text
            )
            second = next(
                item
                for item in request.evidence
                if "grocery nutrition" in item.passage.text
            )
            return GenerationResponse(
                answer_text="Two claims.",
                citations=tuple(
                    Citation(
                        citation_id=f"e{item.rank}",
                        paper_id=item.passage.paper_id,
                        passage_id=item.passage.passage_id,
                    )
                    for item in sorted((first, second), key=lambda e: e.rank)
                ),
                generation_method="fake-generator",
                abstained=False,
                abstention_reason=None,
                finding_kinds=(FindingKind.DESCRIPTIVE,),
                claims=(
                    GeneratedClaim(
                        text="Transit corridors are described.",
                        citation_ids=(f"e{first.rank}",),
                    ),
                    GeneratedClaim(
                        text="Grocery nutrition outcomes are described.",
                        citation_ids=(f"e{first.rank}",),
                    ),
                ),
            )

    result = execute_chat_command(
        _options(tmp_path, top_k=4),
        storage=storage,
        generator_provider=lambda _: _SplitGenerator(),
    )

    assert len(result.withheld_claims) == 1
    assert len(result.claims) == 1
    surviving = {
        citation_id for claim in result.claims for citation_id in claim.citation_ids
    }
    assert {item.citation_id for item in result.citations} == surviving
    # The response cited two passages; only the one a surviving claim relies on
    # is still shown.
    assert len(result.citations) == 1


def test_a_generator_without_claims_is_unaffected_by_grounding(
    tmp_path: Path,
) -> None:
    """A flat answer_text carries no claim-to-evidence binding, so grounding
    must leave v1/v2-style responses exactly as they were rather than
    withholding an answer it cannot actually check."""
    storage = _two_paper_storage(tmp_path)

    result = execute_chat_command(
        _options(tmp_path, top_k=4),
        storage=storage,
        generator_provider=lambda _: AnsweringGenerator(),
    )

    assert result.outcome is ChatTerminalOutcome.ANSWERED
    assert result.claims == ()
    assert result.withheld_claims == ()
    assert result.answer_text == "Trade policy evidence supports a descriptive answer."
    assert len(result.citations) > 0


def _citation(
    citation_id: str,
    *,
    paper_id: str,
    rank: int,
    title: str = "A Paper",
    passage_id: str | None = None,
    page_start: int = 1,
    page_end: int | None = None,
    passage_text: str = "Passage text.",
) -> ChatCitationDetail:
    return ChatCitationDetail(
        citation_id=citation_id,
        paper_title=title,
        section_heading="Introduction",
        page_start=page_start,
        page_end=page_end,
        paper_id=paper_id,
        passage_id=passage_id or f"passage-{citation_id}",
        retrieval_rank=rank,
        retrieval_score=10.0 - rank,
        source_path=f"/papers/{paper_id}.pdf",
        passage_text=passage_text,
    )


def test_several_passages_of_one_paper_render_as_a_single_paper_block() -> None:
    """Retrieval routinely returns several passages of one paper. Repeating the
    title, paper id, and source path for each reads as the same paper listed
    twice, which undermines trust in the citation list exactly when a paper is
    the strongest match."""
    citations = (
        _citation("e1", paper_id="paper-a", rank=1, title="Transit Paper"),
        _citation(
            "e4",
            paper_id="paper-a",
            rank=4,
            title="Transit Paper",
            page_start=2,
            page_end=3,
        ),
    )

    rendered = "\n".join(_render_citation_lines(citations))

    assert rendered.count("Transit Paper") == 1
    assert rendered.count("Paper ID: paper-a") == 1
    assert rendered.count("Source Path:") == 1
    # Both passages remain individually identifiable and auditable.
    assert "[e1, e4] Transit Paper" in rendered
    assert "Passages: 2" in rendered
    assert "      Passage ID: passage-e1" in rendered
    assert "      Passage ID: passage-e4" in rendered
    assert "      Page Range: 2-3" in rendered


def test_distinct_papers_still_render_as_separate_blocks() -> None:
    citations = (
        _citation("e1", paper_id="paper-a", rank=1, title="First Paper"),
        _citation("e2", paper_id="paper-b", rank=2, title="Second Paper"),
    )

    rendered = "\n".join(_render_citation_lines(citations))

    assert "[e1] First Paper" in rendered
    assert "[e2] Second Paper" in rendered
    assert rendered.count("Paper ID:") == 2


def test_papers_and_their_passages_keep_best_rank_ordering() -> None:
    """The strongest evidence must still read first, both across papers and
    within a paper, whatever order citations arrive in."""
    citations = (
        _citation("e5", paper_id="paper-b", rank=5, title="Weaker Paper"),
        _citation("e3", paper_id="paper-a", rank=3, title="Stronger Paper"),
        _citation("e1", paper_id="paper-a", rank=1, title="Stronger Paper"),
    )

    rendered = "\n".join(_render_citation_lines(citations))

    assert rendered.index("Weaker Paper") > rendered.index("Stronger Paper")
    assert "[e1, e3] Stronger Paper" in rendered
    assert rendered.index("Passage ID: passage-e1") < rendered.index(
        "Passage ID: passage-e3"
    )


def test_default_citation_rendering_sanitizes_control_characters_in_metadata() -> None:
    """The default citation block (no --show-evidence needed) renders paper
    title, source path, and section heading straight from stored/extracted
    metadata. A malicious or corrupted PDF's title or filename must not be
    able to inject terminal control sequences into ordinary chat output."""
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            title="Injected\x1b[2JTitle",
        ),
    )

    rendered = "\n".join(_render_citation_lines(citations))

    assert "\x1b" not in rendered
    assert "Injected" in rendered and "Title" in rendered


def test_passage_text_resolved_from_citation_matches_stored_passage(
    tmp_path: Path,
) -> None:
    """`_resolve_citations` already validates the retrieved passage against the
    durable record; `passage_text` must come from that same validated
    passage, not from a second, unvalidated lookup."""
    record = _record(
        tmp_path,
        abstract_text="Abstract trade policy sentence about tariffs.",
        introduction_text="Introduction trade policy sentence about tariffs.",
    )
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(record)

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: AnsweringGenerator(),
    )

    assert result.outcome is ChatTerminalOutcome.ANSWERED
    stored_passages = {passage.passage_id: passage.text for passage in record.passages}
    assert result.citations
    for citation in result.citations:
        assert citation.passage_text == stored_passages[citation.passage_id]


def test_show_evidence_flag_appends_full_passage_text(tmp_path: Path) -> None:
    record = _record(tmp_path)
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(record)
    options = ChatCommandOptions(
        question="trade policy",
        executable_path=tmp_path / "llama-cli",
        model_path=tmp_path / "model.gguf",
        model_id="test-model",
        model_bytes=11,
        model_checksum="b" * 64,
        db_path=tmp_path / "chat.db",
        show_evidence=True,
    )

    result = execute_chat_command(
        options, storage=storage, generator_provider=lambda _: AnsweringGenerator()
    )
    without_flag = format_chat_command_output(result, show_evidence=False)
    with_flag = format_chat_command_output(result, show_evidence=True)

    assert "--- Evidence ---" not in without_flag
    assert "--- Evidence ---" in with_flag
    for citation in result.citations:
        assert citation.passage_text in with_flag


def test_default_chat_output_is_unchanged_without_show_evidence(
    tmp_path: Path,
) -> None:
    """Pinned against the exact pre-M1 golden output (the same shape asserted
    by ``test_answered_chat_renders_citations_from_durable_storage``), not
    merely against another call to the same formatter -- a formatter that
    always ignored ``show_evidence`` would otherwise still pass."""
    storage = SQLiteStorage(tmp_path / "chat.db")
    storage.save_early_section_record(_record(tmp_path))

    result = execute_chat_command(
        _options(tmp_path),
        storage=storage,
        generator_provider=lambda _: AnsweringGenerator(),
    )

    first = result.citations[0]
    second = result.citations[1]
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
        f"[{first.citation_id}, {second.citation_id}] {first.paper_title}\n"
        f"  Paper ID: {first.paper_id}\n"
        f"  Source Path: {first.source_path}\n"
        "  Passages: 2\n"
        f"    [{first.citation_id}]\n"
        f"      Section Heading: {first.section_heading}\n"
        f"      Page Range: {first.page_start}\n"
        f"      Passage ID: {first.passage_id}\n"
        f"      Retrieval Rank: {first.retrieval_rank}\n"
        f"      Retrieval Score: {format(first.retrieval_score, '.12g')}\n"
        f"    [{second.citation_id}]\n"
        f"      Section Heading: {second.section_heading}\n"
        f"      Page Range: {second.page_start}\n"
        f"      Passage ID: {second.passage_id}\n"
        f"      Retrieval Rank: {second.retrieval_rank}\n"
        f"      Retrieval Score: {format(second.retrieval_score, '.12g')}\n"
        "\nEvidence scope: stored Abstract and Introduction passages only."
    )

    default_rendered = format_chat_command_output(result)
    explicit_false_rendered = format_chat_command_output(result, show_evidence=False)
    assert default_rendered == expected
    assert explicit_false_rendered == expected
    assert "--- Evidence ---" not in default_rendered


def test_format_evidence_detail_renders_all_citations_by_default() -> None:
    citations = (
        _citation("e1", paper_id="paper-a", rank=1, passage_text="First passage."),
        _citation("e2", paper_id="paper-b", rank=2, passage_text="Second passage."),
    )

    rendered = format_evidence_detail(citations)

    assert "First passage." in rendered
    assert "Second passage." in rendered
    assert "[e1]" in rendered and "[e2]" in rendered


def test_format_evidence_detail_filters_to_one_citation_id() -> None:
    citations = (
        _citation("e1", paper_id="paper-a", rank=1, passage_text="First passage."),
        _citation("e2", paper_id="paper-b", rank=2, passage_text="Second passage."),
    )

    rendered = format_evidence_detail(citations, citation_id="e1")

    assert "First passage." in rendered
    assert "Second passage." not in rendered


def test_format_evidence_detail_returns_empty_string_for_unknown_id() -> None:
    citations = (_citation("e1", paper_id="paper-a", rank=1),)

    assert format_evidence_detail(citations, citation_id="e99") == ""


def test_format_evidence_detail_preserves_blank_line_paragraph_breaks() -> None:
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            passage_text="First paragraph.\n\nSecond paragraph.",
        ),
    )

    rendered = format_evidence_detail(citations)

    assert "First paragraph.\n\nSecond paragraph." in rendered


def test_format_evidence_detail_wraps_long_lines_without_truncating_text() -> None:
    words = [f"word{i}" for i in range(200)]
    long_word_sentence = " ".join(words)
    citations = (
        _citation("e1", paper_id="paper-a", rank=1, passage_text=long_word_sentence),
    )

    rendered = format_evidence_detail(citations)

    # A full round trip, not just the first and last words: every word from
    # the original passage survives wrapping, in order, with nothing dropped
    # from the middle.
    body = rendered.rsplit("\n\n", 1)[1]
    assert body.split() == words
    body_lines = body.splitlines()
    assert all(len(line) <= 88 for line in body_lines)


def test_format_evidence_detail_replaces_control_characters() -> None:
    """A raw escape sequence embedded in extracted PDF text must not reach the
    terminal verbatim, where it could move the cursor or clear the screen."""
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            passage_text="Before\x1b[2Jafter",
        ),
    )

    rendered = format_evidence_detail(citations)

    assert "\x1b" not in rendered
    assert "Before" in rendered and "after" in rendered


def test_format_evidence_detail_replaces_c1_control_characters() -> None:
    """C1 controls (0x80-0x9F) are as terminal-dangerous as C0 ones in a
    UTF-8 or Latin-1-derived terminal and must be replaced the same way."""
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            passage_text="Before\x9bafter",
        ),
    )

    rendered = format_evidence_detail(citations)

    assert "\x9b" not in rendered
    assert "Before" in rendered and "after" in rendered


def test_format_evidence_detail_normalizes_crlf_and_lone_cr_line_endings() -> None:
    """CRLF and lone-CR line endings must become the same paragraph break as
    a bare LF, not a visible replacement-character artifact."""
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            passage_text="Windows line.\r\nMac line.\rUnix line.",
        ),
    )

    rendered = format_evidence_detail(citations)

    assert "\r" not in rendered
    assert "�" not in rendered
    body = rendered.rsplit("\n\n", 1)[1]
    assert body == "Windows line.\nMac line.\nUnix line."


def test_format_evidence_detail_preserves_unicode_in_passage_text() -> None:
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            passage_text="Café results are 5% higher (β = 0.05).",
        ),
    )

    rendered = format_evidence_detail(citations)

    body = rendered.rsplit("\n\n", 1)[1]
    assert body == "Café results are 5% higher (β = 0.05)."


def test_format_evidence_detail_sanitizes_control_characters_in_metadata_fields() -> (
    None
):
    """A single-line metadata field (title, section heading, source path) must
    not let an embedded newline inject a fake extra line into the rendered
    block, the same protection already given to the passage body."""
    citations = (
        _citation(
            "e1",
            paper_id="paper-a",
            rank=1,
            title="Injected\n  Fake Field: gotcha",
            passage_text="Normal passage text.",
        ),
    )

    rendered = format_evidence_detail(citations)

    assert "\n  Fake Field:" not in rendered
    assert "Injected" in rendered
