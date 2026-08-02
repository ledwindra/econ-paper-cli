"""Application service for the bare ``econpapers`` interactive cited-chat shell.

Each submitted question is one independent cited query over the durable
Abstract/Introduction library, reusing one session snapshot (storage,
corpus, retriever) and one lazily constructed, reused generator. No
conversation history, follow-up rewriting, or persistence is implemented
here; the library snapshot is fixed for the life of the process.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TextIO

from econ_paper_cli.adapters.bm25 import BM25Retriever
from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import (
    LlamaCppConfigurationError,
    LlamaCppOutputError,
    LlamaCppProcessError,
    LlamaCppReadinessError,
)
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import Corpus
from econ_paper_cli.domain.corpora import CorpusValidationError
from econ_paper_cli.domain.errors import CitationValidationError
from econ_paper_cli.protocols import (
    GenerationRequest,
    GenerationRequestValidationError,
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
from econ_paper_cli.services.chat_command import (
    CHAT_EVIDENCE_SCOPE,
    ChatCitationDetail,
    _build_llama_cpp_generator,
    _render_citation_lines,
    _resolve_citations,
)
from econ_paper_cli.services.config_resolution import (
    ConfigResolutionError,
    LazyConfigLoader,
    RuntimeModelOverrides,
    resolve_db_path,
    validate_identity_override_shape,
)

SHELL_PROMPT = "econpapers> "
DEFAULT_SHELL_TOP_K = 10
SESSION_NAME = "econpapers interactive shell"


class ShellExitCode:
    """Stable process exit codes for the interactive shell."""

    SUCCESS = 0
    TYPED_FAILURE_OR_CONFIG_ERROR = 2
    UNEXPECTED_ERROR = 3
    INTERRUPTED = 130


class ShellSessionError(Exception):
    """Raised for interactive-shell orchestration failures independent of any
    single question (e.g. a misconfigured generator provider)."""


class ShellTurnOutcome(str, Enum):
    """Terminal outcome of one interactive-shell question."""

    ANSWERED = "answered"
    NO_MATCHES = "no_matches"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ShellCommandOptions:
    """Parsed options for opening an interactive shell session.

    Runtime/model identity fields are optional per-invocation overrides,
    resolved with the same CLI > durable configuration > default precedence
    as ``analyze``/``chat``; omitting all five falls back to durable
    configuration written by ``econpapers setup``.
    """

    executable_path: Path | None = None
    model_path: Path | None = None
    model_id: str | None = None
    model_bytes: int | None = None
    model_checksum: str | None = None
    threads: int | None = None
    timeout: float | None = None
    db_path: Path | None = None
    config_path: Path | None = None
    top_k: int = DEFAULT_SHELL_TOP_K

    def __post_init__(self) -> None:
        if (
            isinstance(self.top_k, bool)
            or not isinstance(self.top_k, int)
            or self.top_k < 1
        ):
            raise ValueError("top_k must be a positive integer (>= 1).")


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Immutable, restart-safe snapshot of the durable library for one session.

    Fixed for the life of the process: papers analyzed after the session
    opens become visible only after restarting ``econpapers``.
    """

    db_path: Path
    paper_count: int
    passage_count: int
    corpus: Corpus | None


@dataclass(frozen=True, slots=True)
class ShellTurnResult:
    """Immutable per-turn result for one interactive-shell question."""

    question: str
    outcome: ShellTurnOutcome
    answer_text: str | None = None
    generation_method: str | None = None
    finding_kinds: tuple[FindingKind, ...] = ()
    citations: tuple[ChatCitationDetail, ...] = ()
    no_answer_reason: str | None = None
    error_message: str | None = None
    generator_action: str | None = None  # "constructed" | "reused" | None


@dataclass(frozen=True, slots=True)
class ShellStartupFailure:
    """Typed failure opening a session, before any question is asked."""

    exit_code: int
    error_message: str


class InteractiveShellSession:
    """One reusable, in-process session over one durable library snapshot.

    The storage connection, corpus, and retriever are constructed once, at
    session open, and reused for every question. The local generator is
    constructed lazily on the first question whose retrieval finds evidence,
    then reused for later matched questions in the same session. A failed
    construction attempt never poisons the session: the next matched
    question retries construction from scratch.
    """

    def __init__(
        self,
        storage: StorageBackend,
        snapshot: SessionSnapshot,
        *,
        retriever_factory: Callable[[Corpus], object] = BM25Retriever,
        generator_provider: Callable[[], Generator] | None = None,
        top_k: int = DEFAULT_SHELL_TOP_K,
    ) -> None:
        self._storage = storage
        self.snapshot = snapshot
        self._retriever = (
            retriever_factory(snapshot.corpus) if snapshot.corpus is not None else None
        )
        self._generator_provider = generator_provider
        self._top_k = top_k
        self._cached_generator: Generator | None = None

    @property
    def generator_ready(self) -> bool:
        """Whether a generator has already been constructed this session."""
        return self._cached_generator is not None

    def ask(self, question: str) -> ShellTurnResult:
        """Answer one independent question against the fixed session snapshot."""
        normalized = question.strip()
        try:
            if self._retriever is None:
                return ShellTurnResult(
                    question=normalized,
                    outcome=ShellTurnOutcome.NO_MATCHES,
                    no_answer_reason="No stored passages are available.",
                )

            retrieval_request = RetrievalRequest(query=normalized, top_k=self._top_k)
            evidence = validate_retrieval_results(
                retrieval_request, self._retriever.retrieve(retrieval_request)
            )
            if not evidence:
                return ShellTurnResult(
                    question=normalized,
                    outcome=ShellTurnOutcome.NO_MATCHES,
                    no_answer_reason="BM25 returned no evidence for the question.",
                )

            if self._cached_generator is not None:
                generator = self._cached_generator
                generator_action = "reused"
            else:
                if self._generator_provider is None:
                    raise ShellSessionError("No generator provider is configured.")
                generator = self._generator_provider()
                self._cached_generator = generator
                generator_action = "constructed"

            request = GenerationRequest(question=normalized, evidence=evidence)
            response = validate_generation_response(
                request, generator.generate(request)
            )
            if response.abstained:
                return ShellTurnResult(
                    question=normalized,
                    outcome=ShellTurnOutcome.ABSTAINED,
                    no_answer_reason="Generator abstained: insufficient evidence.",
                    generation_method=response.generation_method,
                    generator_action=generator_action,
                )

            cited = _resolve_citations(
                self._storage, self.snapshot.corpus, evidence, response
            )
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.ANSWERED,
                answer_text=response.answer_text,
                generation_method=response.generation_method,
                finding_kinds=response.finding_kinds,
                citations=cited,
                generator_action=generator_action,
            )
        except (
            ValueError,
            RetrievalRequestValidationError,
            LlamaCppConfigurationError,
        ) as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.FAILED,
                error_message=str(error),
            )
        except (LlamaCppReadinessError, StorageConnectionError, ConfigError) as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.FAILED,
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
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.FAILED,
                error_message=str(error),
            )
        except (ShellSessionError, StorageError) as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.FAILED,
                error_message=str(error),
            )
        except Exception as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.FAILED,
                error_message=str(error),
            )


def open_shell_session(
    options: ShellCommandOptions,
    *,
    storage: StorageBackend | None = None,
    config_backend: ConfigBackend | None = None,
    retriever_factory: Callable[[Corpus], object] = BM25Retriever,
    generator_provider: Callable[[], Generator] | None = None,
) -> InteractiveShellSession | ShellStartupFailure:
    """Resolve configuration, open the durable library read-only, and build
    one session snapshot. Never mutates configuration or storage."""
    if not isinstance(options, ShellCommandOptions):
        raise TypeError("options must be a ShellCommandOptions instance.")

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
        return ShellStartupFailure(
            exit_code=ShellExitCode.TYPED_FAILURE_OR_CONFIG_ERROR,
            error_message=str(error),
        )

    lazy_config = LazyConfigLoader(
        config_backend or JSONConfigStorage(options.config_path)
    )
    if overrides.db_path is not None:
        db_path = Path(overrides.db_path)
    else:
        try:
            db_path = resolve_db_path(overrides, lazy_config.get())
        except ConfigError as error:
            return ShellStartupFailure(
                exit_code=ShellExitCode.TYPED_FAILURE_OR_CONFIG_ERROR,
                error_message=str(error),
            )

    storage_backend = storage or SQLiteStorage(db_path, read_only=True)
    try:
        storage_backend.initialize()
        paper_count = storage_backend.count_papers()
        passage_count = storage_backend.count_passages()
        # An empty library has no valid Corpus to construct (Corpus requires
        # at least one paper); questions short-circuit to NO_MATCHES instead.
        corpus = storage_backend.load_corpus() if paper_count > 0 else None
    except StorageConnectionError as error:
        return ShellStartupFailure(
            exit_code=ShellExitCode.TYPED_FAILURE_OR_CONFIG_ERROR,
            error_message=str(error),
        )
    except (CorpusValidationError, StorageValidationError, StorageError) as error:
        return ShellStartupFailure(
            exit_code=ShellExitCode.UNEXPECTED_ERROR, error_message=str(error)
        )

    snapshot = SessionSnapshot(
        db_path=db_path,
        paper_count=paper_count,
        passage_count=passage_count,
        corpus=corpus,
    )

    provider = generator_provider or (
        lambda: _build_llama_cpp_generator(overrides, lazy_config)
    )

    return InteractiveShellSession(
        storage_backend,
        snapshot,
        retriever_factory=retriever_factory,
        generator_provider=provider,
        top_k=options.top_k,
    )


def format_shell_banner(snapshot: SessionSnapshot) -> str:
    """Render the deterministic startup banner shown once per session."""
    return "\n".join(
        (
            f"=== {SESSION_NAME} ===",
            f"Database Path: {snapshot.db_path}",
            f"Paper Count: {snapshot.paper_count}",
            f"Passage Count: {snapshot.passage_count}",
            CHAT_EVIDENCE_SCOPE,
            "Commands: /help, /status, /exit, /quit",
        )
    )


def format_shell_help() -> str:
    """Render concise session help for the ``/help`` command."""
    return "\n".join(
        (
            "=== Session Help ===",
            "Type a question to search the stored local library.",
            "/help    Show this help.",
            "/status  Show database path, paper/passage counts, and generator state.",
            "/exit    Exit the session.",
            "/quit    Exit the session.",
        )
    )


def format_shell_status(session: InteractiveShellSession) -> str:
    """Render the read-only ``/status`` report for the current session."""
    snapshot = session.snapshot
    generator_state = (
        "ready (constructed this session)"
        if session.generator_ready
        else "not yet constructed"
    )
    return "\n".join(
        (
            "=== Session Status ===",
            f"Database Path: {snapshot.db_path}",
            f"Paper Count: {snapshot.paper_count}",
            f"Passage Count: {snapshot.passage_count}",
            f"Generator: {generator_state}",
            CHAT_EVIDENCE_SCOPE,
        )
    )


def format_shell_turn_output(result: ShellTurnResult) -> str:
    """Render one question's result with the same fields as one-shot chat."""
    lines = [f"Question: {result.question}", f"Outcome: {result.outcome.value}"]

    if result.outcome is ShellTurnOutcome.ANSWERED:
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
        lines.append(f"\n{CHAT_EVIDENCE_SCOPE}")
    elif result.outcome in (ShellTurnOutcome.NO_MATCHES, ShellTurnOutcome.ABSTAINED):
        lines.append(f"Reason: {result.no_answer_reason}")
        if result.generation_method is not None:
            lines.append(f"Generation Method: {result.generation_method}")
        if result.outcome is ShellTurnOutcome.ABSTAINED:
            lines.append("Finding Kinds: N/A")
        lines.append(f"\n{CHAT_EVIDENCE_SCOPE}")
    else:
        lines.append(f"Error: {result.error_message}")

    return "\n".join(lines)


def run_interactive_shell(
    options: ShellCommandOptions,
    *,
    storage: StorageBackend | None = None,
    config_backend: ConfigBackend | None = None,
    retriever_factory: Callable[[Corpus], object] = BM25Retriever,
    generator_provider: Callable[[], Generator] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the bare interactive shell's read-eval-print loop.

    Reads lines from ``stdin`` (falling back to ``sys.stdin``/``stdout``/
    ``stderr`` when not injected) rather than the builtin ``input()``, so the
    loop has no direct dependency on terminal globals and is fully testable
    with in-memory streams. EOF (an empty ``readline()`` result) and
    ``/exit``/``/quit`` exit successfully; ``KeyboardInterrupt`` while
    waiting for input exits with ``ShellExitCode.INTERRUPTED`` and no
    traceback. A per-question failure is rendered and the loop continues.
    """
    if stdin is not None and stdout is not None and stderr is not None:
        in_, out, err = stdin, stdout, stderr
    else:
        import sys

        in_ = stdin if stdin is not None else sys.stdin
        out = stdout if stdout is not None else sys.stdout
        err = stderr if stderr is not None else sys.stderr

    session = open_shell_session(
        options,
        storage=storage,
        config_backend=config_backend,
        retriever_factory=retriever_factory,
        generator_provider=generator_provider,
    )
    if isinstance(session, ShellStartupFailure):
        err.write(f"{session.error_message}\n")
        return session.exit_code

    out.write(format_shell_banner(session.snapshot) + "\n")

    while True:
        out.write(SHELL_PROMPT)
        out.flush()
        try:
            line = in_.readline()
        except KeyboardInterrupt:
            out.write("\n")
            return ShellExitCode.INTERRUPTED

        if line == "":
            out.write("\n")
            return ShellExitCode.SUCCESS

        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("/exit", "/quit"):
            return ShellExitCode.SUCCESS
        if stripped == "/help":
            out.write(format_shell_help() + "\n")
            continue
        if stripped == "/status":
            out.write(format_shell_status(session) + "\n")
            continue

        result = session.ask(stripped)
        rendered = format_shell_turn_output(result)
        if result.outcome is ShellTurnOutcome.FAILED:
            err.write(rendered + "\n")
        else:
            out.write(rendered + "\n")
