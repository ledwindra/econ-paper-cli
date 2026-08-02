"""Application service for one-shot cited chat over the local library."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TextIO

from econ_paper_cli.adapters.bm25 import BM25Retriever
from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import (
    LlamaCppConfig,
    LlamaCppConfigurationError,
    LlamaCppGenerator,
    LlamaCppOutputError,
    LlamaCppProcessError,
    LlamaCppReadinessError,
)
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.adapters.storage_paths import get_default_db_path
from econ_paper_cli.domain import Corpus, RetrievalEvidence
from econ_paper_cli.domain.corpora import CorpusValidationError
from econ_paper_cli.domain.early_section_library import EarlySectionLibraryRecord
from econ_paper_cli.domain.errors import CitationValidationError
from econ_paper_cli.protocols import (
    GenerationRequest,
    GenerationRequestValidationError,
    GenerationResponse,
    GenerationResponseValidationError,
    Generator,
    RetrievalRequest,
    RetrievalRequestValidationError,
    RetrievalResultValidationError,
    StorageBackend,
    StorageConnectionError,
    StorageError,
    StorageValidationError,
    validate_generation_response,
)
from econ_paper_cli.protocols.config import ConfigBackend, ConfigError
from econ_paper_cli.protocols.generation import FindingKind
from econ_paper_cli.protocols.retrieval import validate_retrieval_results
from econ_paper_cli.services.config_resolution import (
    ConfigResolutionError,
    LazyConfigLoader,
    RuntimeModelOverrides,
    identity_fully_specified,
    resolve_db_path,
    resolve_runtime_model_config,
    validate_identity_override_shape,
)

CHAT_EVIDENCE_SCOPE = "Evidence scope: stored Abstract and Introduction passages only."
DEFAULT_CHAT_TOP_K = 10


class ChatCommandError(Exception):
    """Base exception for chat command orchestration failures."""


class ChatTypedError(ChatCommandError):
    """Raised for typed CLI, storage, model, or readiness problems."""


class ChatUnexpectedError(ChatCommandError):
    """Raised for unexpected storage, retrieval, or generation failures."""


class ChatTerminalOutcome(str, Enum):
    """Terminal outcomes for one-shot chat execution."""

    ANSWERED = "answered"
    EMPTY_LIBRARY = "empty_library"
    NO_MATCHES = "no_matches"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChatCommandOptions:
    """Parsed options for one-shot chat execution."""

    question: str
    executable_path: Path | None = None
    model_path: Path | None = None
    model_id: str | None = None
    model_bytes: int | None = None
    model_checksum: str | None = None
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | None = None
    config_path: Path | None = None
    top_k: int = DEFAULT_CHAT_TOP_K

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question must be a non-empty string.")
        object.__setattr__(self, "question", self.question.strip())
        if (
            isinstance(self.top_k, bool)
            or not isinstance(self.top_k, int)
            or self.top_k < 1
        ):
            raise ValueError("top_k must be a positive integer (>= 1).")


@dataclass(frozen=True, slots=True)
class ChatCitationDetail:
    """Rendered citation metadata resolved from durable storage."""

    citation_id: str
    paper_title: str
    section_heading: str | None
    page_start: int | None
    page_end: int | None
    paper_id: str
    passage_id: str
    retrieval_rank: int
    retrieval_score: float
    source_path: str


@dataclass(frozen=True, slots=True)
class ChatCommandResult:
    """Immutable terminal result for one-shot chat."""

    outcome: ChatTerminalOutcome
    exit_code: int
    question: str
    db_path: Path
    top_k: int
    answer_text: str | None = None
    no_answer_reason: str | None = None
    generation_method: str | None = None
    finding_kinds: tuple[FindingKind, ...] = ()
    citations: tuple[ChatCitationDetail, ...] = ()
    error_message: str | None = None


def execute_chat_command(
    options: ChatCommandOptions,
    *,
    storage: StorageBackend | None = None,
    config_backend: ConfigBackend | None = None,
    retriever_factory: Callable[[Corpus], object] = BM25Retriever,
    generator_provider: Callable[[ChatCommandOptions], Generator] | None = None,
) -> ChatCommandResult:
    """Run one-shot chat without printing and return a structured terminal result."""
    if not isinstance(options, ChatCommandOptions):
        raise TypeError("options must be a ChatCommandOptions instance.")

    question = options.question
    overrides = RuntimeModelOverrides(
        executable_path=options.executable_path,
        model_path=options.model_path,
        model_id=options.model_id,
        model_bytes=options.model_bytes,
        model_checksum=options.model_checksum,
        threads=options.threads,
        timeout=options.timeout,
        db_path=options.db_path,
    )

    try:
        validate_identity_override_shape(overrides)
    except ConfigResolutionError as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=2,
            question=question,
            db_path=options.db_path or get_default_db_path(),
            top_k=options.top_k,
            error_message=str(error),
        )

    # Durable configuration is read lazily and at most once below; it is
    # never mutated, and EMPTY_LIBRARY/NO_MATCHES/a fully-specified CLI
    # override with an explicit --db-path never force a load.
    lazy_config = LazyConfigLoader(
        config_backend or JSONConfigStorage(options.config_path)
    )
    if overrides.db_path is not None:
        db_path = Path(overrides.db_path)
    else:
        try:
            db_path = resolve_db_path(overrides, lazy_config.get())
        except ConfigError as error:
            return ChatCommandResult(
                outcome=ChatTerminalOutcome.FAILED,
                exit_code=2,
                question=question,
                db_path=options.db_path or get_default_db_path(),
                top_k=options.top_k,
                error_message=str(error),
            )
    storage_backend = storage or SQLiteStorage(db_path, read_only=True)
    owns_storage = storage is None

    try:
        storage_backend.initialize()
        records = storage_backend.list_early_section_records()
        if not records:
            return ChatCommandResult(
                outcome=ChatTerminalOutcome.EMPTY_LIBRARY,
                exit_code=1,
                question=question,
                db_path=db_path,
                top_k=options.top_k,
                no_answer_reason="No stored passages are available.",
            )

        corpus = storage_backend.load_corpus()
        retriever = retriever_factory(corpus)
        if not isinstance(retriever, BM25Retriever) and not hasattr(
            retriever, "retrieve"
        ):
            raise ChatUnexpectedError(
                "retriever_factory must return an object with a retrieve() method."
            )

        retrieval_request = RetrievalRequest(query=question, top_k=options.top_k)
        evidence = validate_retrieval_results(
            retrieval_request, retriever.retrieve(retrieval_request)
        )
        if not evidence:
            return ChatCommandResult(
                outcome=ChatTerminalOutcome.NO_MATCHES,
                exit_code=1,
                question=question,
                db_path=db_path,
                top_k=options.top_k,
                no_answer_reason="BM25 returned no evidence for the question.",
            )

        provider = generator_provider or (
            lambda opts: _build_llama_cpp_generator(overrides, lazy_config)
        )
        generator = provider(options)
        request = GenerationRequest(question=question, evidence=evidence)
        response = validate_generation_response(request, generator.generate(request))
        if response.abstained:
            return ChatCommandResult(
                outcome=ChatTerminalOutcome.ABSTAINED,
                exit_code=1,
                question=question,
                db_path=db_path,
                top_k=options.top_k,
                no_answer_reason="Generator abstained: insufficient evidence.",
                generation_method=response.generation_method,
            )

        cited = _resolve_citations(
            storage_backend.get_early_section_record, corpus, evidence, response
        )
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.ANSWERED,
            exit_code=0,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            answer_text=response.answer_text,
            generation_method=response.generation_method,
            finding_kinds=response.finding_kinds,
            citations=cited,
        )
    except (
        ValueError,
        RetrievalRequestValidationError,
        LlamaCppConfigurationError,
    ) as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=2,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            error_message=str(error),
        )
    except (LlamaCppReadinessError, StorageConnectionError) as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=2,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            error_message=str(error),
        )
    except (
        CorpusValidationError,
        CitationValidationError,
        GenerationRequestValidationError,
        GenerationResponseValidationError,
        LlamaCppOutputError,
        LlamaCppProcessError,
        StorageValidationError,
        RetrievalResultValidationError,
    ) as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=3,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            error_message=str(error),
        )
    except ChatUnexpectedError as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=3,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            error_message=str(error),
        )
    except StorageError as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=3,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            error_message=str(error),
        )
    except Exception as error:
        return ChatCommandResult(
            outcome=ChatTerminalOutcome.FAILED,
            exit_code=3,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            error_message=str(error),
        )
    finally:
        if owns_storage:
            storage_backend.close()


def run_chat_command(
    options: ChatCommandOptions,
    *,
    storage: StorageBackend | None = None,
    config_backend: ConfigBackend | None = None,
    retriever_factory: Callable[[Corpus], object] = BM25Retriever,
    generator_provider: Callable[[ChatCommandOptions], Generator] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Execute chat and render deterministic terminal output."""
    result = execute_chat_command(
        options,
        storage=storage,
        config_backend=config_backend,
        retriever_factory=retriever_factory,
        generator_provider=generator_provider,
    )
    out = stdout
    err = stderr
    if out is None or err is None:
        import sys

        out = sys.stdout if out is None else out
        err = sys.stderr if err is None else err

    rendered = format_chat_command_output(result)
    if result.outcome is ChatTerminalOutcome.FAILED:
        err.write(rendered + "\n")
    else:
        out.write(rendered + "\n")
    return result.exit_code


def format_chat_command_output(result: ChatCommandResult) -> str:
    """Render deterministic, inspectable one-shot chat output."""
    lines = [
        "=== One-Shot Chat Result ===",
        f"Question: {result.question}",
        f"Database Path: {result.db_path}",
        f"Top K: {result.top_k}",
        f"Outcome: {result.outcome.value}",
    ]

    if result.outcome is ChatTerminalOutcome.ANSWERED:
        lines.append(f"Answer: {result.answer_text}")
        if result.generation_method is not None:
            lines.append(f"Generation Method: {result.generation_method}")
        if result.finding_kinds:
            lines.append(
                "Finding Kinds: "
                + ", ".join(kind.value for kind in result.finding_kinds)
            )
        else:
            lines.append("Finding Kinds: N/A")
        lines.append("\n--- Citations ---")
        lines.extend(_render_citation_lines(result.citations))
    elif result.outcome in (
        ChatTerminalOutcome.EMPTY_LIBRARY,
        ChatTerminalOutcome.NO_MATCHES,
        ChatTerminalOutcome.ABSTAINED,
    ):
        lines.append(f"Reason: {result.no_answer_reason}")
        if result.generation_method is not None:
            lines.append(f"Generation Method: {result.generation_method}")
        if result.outcome is ChatTerminalOutcome.ABSTAINED:
            lines.append("Finding Kinds: N/A")
        lines.append(f"\n{CHAT_EVIDENCE_SCOPE}")
    else:
        lines.append(f"Error: {result.error_message}")

    if result.outcome is ChatTerminalOutcome.ANSWERED:
        lines.append(f"\n{CHAT_EVIDENCE_SCOPE}")

    return "\n".join(lines)


def _render_citation_lines(citations: tuple[ChatCitationDetail, ...]) -> list[str]:
    """Render the shared per-citation detail block used by one-shot chat and
    the interactive shell, so both stay byte-for-byte identical."""
    lines: list[str] = []
    for detail in citations:
        lines.append(f"[{detail.citation_id}]")
        lines.append(f"  Paper Title: {detail.paper_title}")
        lines.append(f"  Section Heading: {detail.section_heading or 'N/A'}")
        if detail.page_start is None:
            page_range = "N/A"
        elif detail.page_end is None or detail.page_end == detail.page_start:
            page_range = str(detail.page_start)
        else:
            page_range = f"{detail.page_start}-{detail.page_end}"
        lines.append(f"  Page Range: {page_range}")
        lines.append(f"  Paper ID: {detail.paper_id}")
        lines.append(f"  Passage ID: {detail.passage_id}")
        lines.append(f"  Retrieval Rank: {detail.retrieval_rank}")
        lines.append(f"  Retrieval Score: {format(detail.retrieval_score, '.12g')}")
        lines.append(f"  Source Path: {detail.source_path}")
    return lines


def _build_llama_cpp_generator(
    overrides: RuntimeModelOverrides,
    lazy_config: LazyConfigLoader,
) -> Generator:
    # A fully-specified CLI identity never forces a load. But if a load
    # already happened for another reason (e.g. resolving --db-path), its
    # optional threads/timeout defaults still apply per CLI > config >
    # default precedence — they are not discarded just because identity
    # itself didn't need them.
    durable_config = (
        lazy_config.peek() if identity_fully_specified(overrides) else lazy_config.get()
    )
    resolved = resolve_runtime_model_config(overrides, durable_config)
    config = LlamaCppConfig(
        executable_path=resolved.executable_path,
        model_path=resolved.model_path,
        model_id=resolved.model_id,
        model_expected_size_bytes=resolved.model_bytes,
        model_sha256=resolved.model_checksum,
        threads=resolved.threads,
        timeout_seconds=resolved.timeout_seconds,
    )
    return LlamaCppGenerator(config)


def _resolve_citations(
    record_lookup: Callable[[str], EarlySectionLibraryRecord | None],
    corpus: Corpus,
    evidence: tuple[RetrievalEvidence, ...],
    response: GenerationResponse,
) -> tuple[ChatCitationDetail, ...]:
    """Resolve citation detail from an early-section record lookup.

    Callers pass either a live storage read (one-shot ``chat``) or a fixed
    in-memory snapshot mapping (the interactive shell), so both stay
    byte-for-byte identical in rendering while the shell never re-reads live
    storage per turn.
    """
    paper_by_id = {paper.paper_id: paper for paper in corpus.papers}
    evidence_by_id = {f"e{item.rank}": item for item in evidence}
    details: list[ChatCitationDetail] = []

    for citation in response.citations:
        evidence_item = evidence_by_id[citation.citation_id]
        record = record_lookup(evidence_item.passage.paper_id)
        if record is None:
            raise StorageValidationError(
                f"Missing early-section record for paper_id '{evidence_item.passage.paper_id}'."
            )
        paper = paper_by_id.get(record.paper.paper_id)
        if paper is None or paper != record.paper:
            raise StorageValidationError(
                f"Paper metadata mismatch for paper_id '{record.paper.paper_id}'."
            )
        passage_by_id = {passage.passage_id: passage for passage in record.passages}
        stored_passage = passage_by_id.get(evidence_item.passage.passage_id)
        if stored_passage is None or stored_passage != evidence_item.passage:
            raise StorageValidationError(
                f"Passage metadata mismatch for passage_id '{evidence_item.passage.passage_id}'."
            )
        details.append(
            ChatCitationDetail(
                citation_id=citation.citation_id,
                paper_title=record.paper.title,
                section_heading=stored_passage.section_heading,
                page_start=stored_passage.page_start,
                page_end=stored_passage.page_end,
                paper_id=record.paper.paper_id,
                passage_id=stored_passage.passage_id,
                retrieval_rank=evidence_item.rank,
                retrieval_score=evidence_item.score,
                source_path=record.source_provenance.source_path,
            )
        )

    return tuple(details)
