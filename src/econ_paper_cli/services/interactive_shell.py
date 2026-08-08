"""Application service for the bare ``econpapers`` interactive cited-chat shell.

Each submitted question is one independent cited query over the durable
Abstract/Introduction library, reusing one session snapshot (storage,
corpus, retriever) and one lazily constructed, reused generator. No
conversation history, follow-up rewriting, or persistence is implemented
here; the library snapshot is fixed for the life of the process.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
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
from econ_paper_cli.domain.conversation import (
    ConversationHistory,
    ConversationTurn,
    needs_context_resolution,
)
from econ_paper_cli.domain.corpora import CorpusValidationError
from econ_paper_cli.domain.early_section_library import EarlySectionLibraryRecord
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
    check_response_grounding,
    validate_generation_response,
)
from econ_paper_cli.protocols.config import ConfigBackend, ConfigError
from econ_paper_cli.protocols.generation import FindingKind
from econ_paper_cli.protocols.retrieval import validate_retrieval_results
from econ_paper_cli.services.chat_command import (
    CHAT_EVIDENCE_SCOPE,
    ChatCitationDetail,
    ChatClaimDetail,
    WithheldClaimDetail,
    _build_llama_cpp_generator,
    _format_citation_group,
    _format_citation_ids,
    _render_citation_lines,
    _render_withheld_lines,
    _resolve_citations,
    _terminal_color_enabled,
    format_evidence_detail,
)
from econ_paper_cli.services.config_resolution import (
    ConfigResolutionError,
    LazyConfigLoader,
    RuntimeModelOverrides,
    resolve_db_path,
    validate_identity_override_shape,
)

SHELL_PROMPT = "econpapers> "
# Importing `readline` is what gives the builtin `input()` line editing and
# history on a real terminal: arrow keys, Ctrl-A/E, and Up/Down recall. Without
# it a terminal sends raw escape sequences that appear as literal "^[[D" in the
# typed question. Absent on some Windows Pythons, where the shell simply falls
# back to unedited input rather than failing to start.
try:  # pragma: no cover - availability is platform-dependent
    import readline as _readline
except ImportError:  # pragma: no cover
    _readline = None
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
    # Distinct from ABSTAINED: the generator answered, but every claim
    # misattributed content across papers and was suppressed.
    WITHHELD = "withheld"
    TYPED_FAILURE = "typed_failure"
    INTERNAL_FAILURE = "internal_failure"


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
    opens become visible only after restarting ``econpapers``. This
    includes ``early_section_records``, the exact citation/provenance data
    used to render citations — turns resolve citations from this in-memory
    mapping, never from a live re-read of storage, so a concurrent
    ``analyze``/``update`` cannot change or break a citation mid-session.
    """

    db_path: Path
    paper_count: int
    passage_count: int
    corpus: Corpus | None
    early_section_records: Mapping[str, EarlySectionLibraryRecord]


@dataclass(frozen=True, slots=True)
class ShellTurnResult:
    """Immutable per-turn result for one interactive-shell question."""

    question: str
    outcome: ShellTurnOutcome
    answer_text: str | None = None
    generation_method: str | None = None
    finding_kinds: tuple[FindingKind, ...] = ()
    citations: tuple[ChatCitationDetail, ...] = ()
    claims: tuple[ChatClaimDetail, ...] = ()
    withheld_claims: tuple[WithheldClaimDetail, ...] = ()
    # Populated only when the question was rewritten against earlier turns.
    # Shown to the user so a resolution they disagree with is visible rather
    # than silently answered.
    resolved_question: str | None = None
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
        self._history = ConversationHistory()
        self._last_citations: tuple[ChatCitationDetail, ...] = ()

    @property
    def generator_ready(self) -> bool:
        """Whether a generator has already been constructed this session."""
        return self._cached_generator is not None

    @property
    def history(self) -> ConversationHistory:
        """The bounded record of answered turns available to follow-ups."""
        return self._history

    @property
    def last_turn_citations(self) -> tuple[ChatCitationDetail, ...]:
        """Citations available to ``/show`` for the most recent turn.

        Populated only by an ``answered`` turn and replaced (not merged) by
        every later turn, whatever its outcome — so a citation ID from a
        turn that is no longer the latest one is never accepted. A turn that
        did not answer (``withheld``/``abstained``/``no_matches``/failures)
        clears this rather than leaving the previous turn's evidence visible,
        since that evidence no longer corresponds to the question just asked.
        """
        return self._last_citations

    def reset_history(self) -> None:
        """Forget prior turns and the last turn's evidence.

        Both are "context from earlier in the session" from the user's point
        of view, so ``/reset`` clears them together rather than leaving
        ``/show`` pointing at evidence for a conversation that was just
        forgotten.
        """
        self._history = self._history.cleared()
        self._last_citations = ()

    def close(self) -> None:
        """Close the session-owned storage connection deterministically."""
        self._storage.close()

    def _resolve_follow_up(self, question: str) -> str | None:
        """Return a standalone rewrite of a context-dependent question.

        Returns None when the question is already self-contained, when there is
        no history, or when resolution fails. Resolution is best-effort by
        design: losing the turn to a rewrite failure would be a worse outcome
        than answering the question as typed.
        """
        if not needs_context_resolution(question, self._history):
            return None
        generator = self._cached_generator
        resolver = getattr(generator, "resolve_follow_up", None)
        if resolver is None:
            return None
        prior = [turn.resolved_question for turn in self._history.turns]
        try:
            candidate = resolver(question, prior)
        except (LlamaCppOutputError, LlamaCppProcessError, ValueError):
            return None
        if not isinstance(candidate, str):
            return None
        candidate = candidate.strip()
        if not candidate or candidate == question.strip():
            return None
        return candidate

    def ask(self, question: str) -> ShellTurnResult:
        """Answer one independent question and update ``/show`` evidence state.

        This is the single choke point that updates ``last_turn_citations``,
        so every one of ``_ask_impl``'s several return paths stays correct by
        construction rather than each having to remember to set it.
        """
        result = self._ask_impl(question)
        self._last_citations = (
            result.citations if result.outcome is ShellTurnOutcome.ANSWERED else ()
        )
        return result

    def _ask_impl(self, question: str) -> ShellTurnResult:
        """Answer one independent question against the fixed session snapshot."""
        normalized = question.strip()
        generator_action: str | None = None
        try:
            if self._retriever is None:
                return ShellTurnResult(
                    question=normalized,
                    outcome=ShellTurnOutcome.NO_MATCHES,
                    no_answer_reason="No stored passages are available.",
                )

            # A follow-up is resolved before retrieval, because both retrieval
            # and generation need the referent: "what about wages?" retrieves
            # nothing useful and gives the generator no subject. Resolution
            # never constructs a generator, since history only gains entries on
            # answered turns, which already required one.
            resolved_question = self._resolve_follow_up(normalized)
            query = resolved_question or normalized

            retrieval_request = RetrievalRequest(query=query, top_k=self._top_k)
            evidence = validate_retrieval_results(
                retrieval_request, self._retriever.retrieve(retrieval_request)
            )
            if not evidence:
                return ShellTurnResult(
                    question=normalized,
                    outcome=ShellTurnOutcome.NO_MATCHES,
                    no_answer_reason="BM25 returned no evidence for the question.",
                    resolved_question=resolved_question,
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

            request = GenerationRequest(question=query, evidence=evidence)
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
                    resolved_question=resolved_question,
                )

            if self.snapshot.corpus is None:
                raise ShellSessionError("No corpus is available to resolve citations.")
            cited = _resolve_citations(
                self.snapshot.early_section_records.get,
                self.snapshot.corpus,
                evidence,
                response,
            )

            # The shell runs the same withholding rule as one-shot chat: a
            # claim using another paper's distinctive wording is misattributed
            # and never reaches the user, whichever entry point asked.
            verdicts = check_response_grounding(request, response)
            grounded_claims = tuple(
                claim
                for claim, verdict in zip(response.claims, verdicts, strict=True)
                if verdict.grounded
            )
            withheld = tuple(
                WithheldClaimDetail(
                    text=claim.text,
                    citation_ids=claim.citation_ids,
                    leaked_terms=verdict.leaked_terms,
                )
                for claim, verdict in zip(response.claims, verdicts, strict=True)
                if not verdict.grounded
            )

            if verdicts and not grounded_claims:
                return ShellTurnResult(
                    question=normalized,
                    outcome=ShellTurnOutcome.WITHHELD,
                    no_answer_reason=(
                        "Every generated claim attributed content to a paper "
                        "that does not contain it; nothing could be shown."
                    ),
                    generation_method=response.generation_method,
                    withheld_claims=withheld,
                    generator_action=generator_action,
                    resolved_question=resolved_question,
                )

            title_by_citation = {item.citation_id: item.paper_title for item in cited}
            claim_details = tuple(
                ChatClaimDetail(
                    text=claim.text,
                    citation_ids=claim.citation_ids,
                    paper_titles=tuple(
                        dict.fromkeys(
                            title_by_citation[citation_id]
                            for citation_id in claim.citation_ids
                        )
                    ),
                )
                for claim in grounded_claims
            )
            if grounded_claims:
                answer_text = " ".join(claim.text for claim in grounded_claims)
                surviving_ids = {
                    citation_id
                    for claim in grounded_claims
                    for citation_id in claim.citation_ids
                }
                cited = tuple(
                    item for item in cited if item.citation_id in surviving_ids
                )
            else:
                answer_text = response.answer_text

            # response.finding_kinds describes the whole original response,
            # not any one claim. Once a claim has been withheld, that label
            # can no longer be trusted to describe the surviving answer, so
            # reporting nothing is safer than reporting a stale label.
            reported_finding_kinds = () if withheld else response.finding_kinds

            # Only an answered turn enters history: an abstention or a
            # withheld answer carries no established referent, so treating it
            # as context would resolve the next follow-up against something the
            # user was never actually told.
            self._history = self._history.appended(
                ConversationTurn(
                    question=normalized,
                    resolved_question=query,
                    answer_text=answer_text,
                    cited_paper_titles=tuple(
                        dict.fromkeys(item.paper_title for item in cited)
                    ),
                )
            )
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.ANSWERED,
                answer_text=answer_text,
                generation_method=response.generation_method,
                finding_kinds=reported_finding_kinds,
                citations=cited,
                claims=claim_details,
                withheld_claims=withheld,
                generator_action=generator_action,
                resolved_question=resolved_question,
            )
        except (
            ValueError,
            RetrievalRequestValidationError,
            LlamaCppConfigurationError,
        ) as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.TYPED_FAILURE,
                error_message=str(error),
                generator_action=generator_action,
            )
        except (LlamaCppReadinessError, StorageConnectionError, ConfigError) as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.TYPED_FAILURE,
                error_message=str(error),
                generator_action=generator_action,
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
                outcome=ShellTurnOutcome.INTERNAL_FAILURE,
                error_message=str(error),
                generator_action=generator_action,
            )
        except (ShellSessionError, StorageError) as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.INTERNAL_FAILURE,
                error_message=str(error),
                generator_action=generator_action,
            )
        except Exception as error:
            return ShellTurnResult(
                question=normalized,
                outcome=ShellTurnOutcome.INTERNAL_FAILURE,
                error_message=str(error),
                generator_action=generator_action,
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
        # Loaded once, here, into the immutable snapshot: every later turn
        # resolves citations from this fixed mapping, never a live re-read,
        # so a concurrent analyze/update cannot change or break a citation
        # mid-session.
        early_section_records = MappingProxyType(
            {
                record.paper.paper_id: record
                for record in storage_backend.list_early_section_records()
            }
        )
    except StorageConnectionError as error:
        storage_backend.close()
        return ShellStartupFailure(
            exit_code=ShellExitCode.TYPED_FAILURE_OR_CONFIG_ERROR,
            error_message=str(error),
        )
    except (CorpusValidationError, StorageValidationError, StorageError) as error:
        storage_backend.close()
        return ShellStartupFailure(
            exit_code=ShellExitCode.UNEXPECTED_ERROR, error_message=str(error)
        )

    snapshot = SessionSnapshot(
        db_path=db_path,
        paper_count=paper_count,
        passage_count=passage_count,
        corpus=corpus,
        early_section_records=early_section_records,
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
            "Commands: /help, /status, /show, /reset, /exit, /quit",
        )
    )


def _line_editing_available(injected_stdin: TextIO | None, stream: TextIO) -> bool:
    """Whether this session can use ``readline`` line editing.

    Requires the library, an un-injected stream, and a real terminal. Injected
    streams are excluded even when they happen to be a TTY: tests drive the
    loop through in-memory streams, and routing those through ``input()`` would
    read the process's real stdin instead.
    """
    if _readline is None or injected_stdin is not None:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        # A closed or non-file-like stream reports nothing useful; unedited
        # input still works, so this must never prevent the shell starting.
        return False


def _read_input_line(stream: TextIO, out: TextIO, *, line_editing: bool) -> str | None:
    """Read one line without its newline, or None at end of input.

    None distinguishes EOF from an empty line, which ``input()`` returns
    identically as ``""``.
    """
    if line_editing:
        try:
            # `input()` writes the prompt itself; readline needs to own it to
            # redraw the line correctly during editing.
            return input(SHELL_PROMPT)
        except EOFError:
            return None
    out.write(SHELL_PROMPT)
    out.flush()
    line = stream.readline()
    if line == "":
        return None
    return line.rstrip("\n")


def format_shell_help() -> str:
    """Render concise session help for the ``/help`` command."""
    return "\n".join(
        (
            "=== Session Help ===",
            "Type a question to search the stored local library.",
            "/help    Show this help.",
            "/status  Show database path, paper/passage counts, and generator state.",
            "/show    List citation IDs available for the last answered turn.",
            "/show ID Print the full stored passage text for that citation.",
            "/reset   Forget earlier turns; the next question is taken literally.",
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


def format_shell_show(
    session: InteractiveShellSession, citation_id: str | None, *, color: bool = False
) -> str:
    """Render the ``/show`` command: list available IDs, or one full passage.

    Reads only ``session.last_turn_citations`` — the immutable evidence
    already resolved for the latest turn — never a live storage read, so a
    concurrent ``analyze``/``update`` cannot change what ``/show`` renders.
    """
    citations = session.last_turn_citations
    if not citations:
        return (
            "No evidence is available yet. Ask a question that gets "
            "answered, then run /show."
        )
    if citation_id is None:
        available = _format_citation_ids(
            tuple(item.citation_id for item in citations), color=color
        )
        return (
            f"Available citations: {available}\n"
            "Run /show ID to view one, e.g. /show "
            f"{_format_citation_ids((citations[0].citation_id,), color=color)}."
        )
    if citation_id not in {item.citation_id for item in citations}:
        available = _format_citation_ids(
            tuple(item.citation_id for item in citations), color=color
        )
        return f"Unknown citation ID '{citation_id}'. Available: {available}"
    return format_evidence_detail(citations, citation_id=citation_id, color=color)


def format_shell_turn_output(result: ShellTurnResult, *, color: bool = False) -> str:
    """Render one question's result with the same fields as one-shot chat."""
    lines = [f"Question: {result.question}"]
    if result.resolved_question is not None:
        # Always visible: a rewrite the user disagrees with must be
        # correctable, not silently answered as if they had asked it.
        lines.append(f"Interpreted as: {result.resolved_question}")
    lines.append(f"Outcome: {result.outcome.value}")

    if result.outcome is ShellTurnOutcome.ANSWERED:
        lines.append(f"Answer: {result.answer_text}")
        if result.claims:
            lines.append("\n--- Answer by Source ---")
            for position, claim in enumerate(result.claims, start=1):
                lines.append(f"{position}. {claim.text}")
                sources = ", ".join(claim.paper_titles)
                identifiers = _format_citation_group(claim.citation_ids, color=color)
                lines.append(f"   Source: {sources} {identifiers}")
        if result.withheld_claims:
            total = len(result.claims) + len(result.withheld_claims)
            lines.append(
                f"\nWithheld: {len(result.withheld_claims)} of {total} generated "
                "claims attributed content to a paper that does not contain it."
            )
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
        lines.extend(_render_citation_lines(result.citations, color=color))
        lines.append(f"\n{CHAT_EVIDENCE_SCOPE}")
    elif result.outcome is ShellTurnOutcome.WITHHELD:
        lines.append(f"Reason: {result.no_answer_reason}")
        lines.extend(_render_withheld_lines(result.withheld_claims, color=color))
        if result.generation_method is not None:
            lines.append(f"Generation Method: {result.generation_method}")
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

    On a real terminal, input is read through the builtin ``input()`` so the
    ``readline`` library provides line editing and history; without it, arrow
    keys reach the question as raw escape sequences like ``^[[D``. Injected or
    non-TTY streams are read with ``stdin.readline()`` instead, keeping the
    loop free of terminal globals and fully testable with in-memory streams.

    EOF and ``/exit``/``/quit`` exit successfully; ``KeyboardInterrupt`` while
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

    line_editing = _line_editing_available(stdin, in_)

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

    try:
        while True:
            try:
                line = _read_input_line(in_, out, line_editing=line_editing)
            except KeyboardInterrupt:
                out.write("\n")
                return ShellExitCode.INTERRUPTED

            if line is None:
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
            # split(None, ...) splits on any whitespace run (space, tab, ...)
            # and collapses it, so "/show\te1" and "/show   e1" both parse
            # the same way a literal space does.
            command_token = stripped.split(None, 1)[0]
            if command_token == "/show":
                remainder = stripped.split(None, 1)[1:]
                citation_id = remainder[0].strip() if remainder else None
                out.write(
                    format_shell_show(
                        session,
                        citation_id or None,
                        color=_terminal_color_enabled(out),
                    )
                    + "\n"
                )
                continue
            if stripped == "/reset":
                session.reset_history()
                out.write(
                    "Conversation context cleared. The next question is taken "
                    "literally.\n"
                )
                continue

            result = session.ask(stripped)
            output_stream = (
                err
                if result.outcome
                in (ShellTurnOutcome.TYPED_FAILURE, ShellTurnOutcome.INTERNAL_FAILURE)
                else out
            )
            rendered = format_shell_turn_output(
                result, color=_terminal_color_enabled(output_stream)
            )
            if result.outcome in (
                ShellTurnOutcome.TYPED_FAILURE,
                ShellTurnOutcome.INTERNAL_FAILURE,
            ):
                err.write(rendered + "\n")
            else:
                out.write(rendered + "\n")
    finally:
        session.close()
