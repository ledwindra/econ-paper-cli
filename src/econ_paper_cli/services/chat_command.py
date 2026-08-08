"""Application service for one-shot cited chat over the local library."""

from __future__ import annotations

import os
import textwrap
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
from econ_paper_cli.protocols.generation import (
    FindingKind,
    check_response_grounding,
)
from econ_paper_cli.protocols.retrieval import validate_retrieval_results
from econ_paper_cli.services.config_resolution import (
    ConfigResolutionError,
    LazyConfigLoader,
    RuntimeModelOverrides,
    build_llama_cpp_config_kwargs,
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
    # Distinct from ABSTAINED: the generator did produce an answer, but every
    # claim in it misattributed content across papers and was withheld. The
    # user needs to know an answer existed and was suppressed, not that the
    # library had nothing to say.
    WITHHELD = "withheld"
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
    show_evidence: bool = False

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
    passage_text: str


@dataclass(frozen=True, slots=True)
class ChatClaimDetail:
    """One verified claim with the papers it is attributed to."""

    text: str
    citation_ids: tuple[str, ...]
    paper_titles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WithheldClaimDetail:
    """One claim suppressed for attributing content to the wrong paper."""

    text: str
    citation_ids: tuple[str, ...]
    leaked_terms: tuple[str, ...]


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
    claims: tuple[ChatClaimDetail, ...] = ()
    withheld_claims: tuple[WithheldClaimDetail, ...] = ()
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

        # A claim that uses another paper's distinctive vocabulary is
        # misattributed, so it never reaches the user even though its citation
        # identifiers are valid. Responses without per-claim attribution
        # (v1/v2 backends, injected test generators) yield no verdicts and pass
        # through untouched.
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
            return ChatCommandResult(
                outcome=ChatTerminalOutcome.WITHHELD,
                exit_code=1,
                question=question,
                db_path=db_path,
                top_k=options.top_k,
                no_answer_reason=(
                    "Every generated claim attributed content to a paper that "
                    "does not contain it; nothing could be shown."
                ),
                generation_method=response.generation_method,
                withheld_claims=withheld,
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
            cited = tuple(item for item in cited if item.citation_id in surviving_ids)
        else:
            answer_text = response.answer_text

        # response.finding_kinds describes the whole original response, not
        # any one claim. Once a claim has been withheld, that label can no
        # longer be trusted to describe the surviving answer -- e.g. a
        # withheld claim could have been the only causal one, leaving a
        # purely descriptive answer mislabeled "causal". Reporting nothing
        # is safer than reporting a label that may no longer be true.
        reported_finding_kinds = () if withheld else response.finding_kinds

        return ChatCommandResult(
            outcome=ChatTerminalOutcome.ANSWERED,
            exit_code=0,
            question=question,
            db_path=db_path,
            top_k=options.top_k,
            answer_text=answer_text,
            generation_method=response.generation_method,
            finding_kinds=reported_finding_kinds,
            citations=cited,
            claims=claim_details,
            withheld_claims=withheld,
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

    output_stream = err if result.outcome is ChatTerminalOutcome.FAILED else out
    rendered = format_chat_command_output(
        result,
        show_evidence=options.show_evidence,
        color=_terminal_color_enabled(output_stream),
    )
    if result.outcome is ChatTerminalOutcome.FAILED:
        err.write(rendered + "\n")
    else:
        out.write(rendered + "\n")
    return result.exit_code


def format_chat_command_output(
    result: ChatCommandResult, *, show_evidence: bool = False, color: bool = False
) -> str:
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
        if result.claims:
            # Each sentence is shown beside the paper it came from, so a reader
            # who does not know the corpus can check any single statement
            # without matching opaque identifiers by hand.
            lines.append("\n--- Answer by Source ---")
            for position, claim in enumerate(result.claims, start=1):
                lines.append(f"{position}. {claim.text}")
                sources = ", ".join(claim.paper_titles)
                identifiers = _format_citation_group(claim.citation_ids, color=color)
                lines.append(f"   Source: {sources} {identifiers}")
        if result.withheld_claims:
            # A count, not the full dossier: the answer above is the product,
            # and burying it under per-claim diagnostics reads as failure to
            # someone who just wants the finding. The withheld text stays on
            # the result for callers that want to show it.
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
        if show_evidence and result.citations:
            lines.append("\n--- Evidence ---")
            lines.append(format_evidence_detail(result.citations, color=color))
    elif result.outcome is ChatTerminalOutcome.WITHHELD:
        lines.append(f"Reason: {result.no_answer_reason}")
        lines.extend(_render_withheld_lines(result.withheld_claims, color=color))
        if result.generation_method is not None:
            lines.append(f"Generation Method: {result.generation_method}")
        lines.append(f"\n{CHAT_EVIDENCE_SCOPE}")
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


def _render_withheld_lines(
    withheld: tuple[WithheldClaimDetail, ...],
    *,
    color: bool = False,
) -> list[str]:
    """Render suppressed claims so a withheld answer is visible, not silent."""
    if not withheld:
        return []
    lines = [
        "",
        f"--- Withheld Claims ({len(withheld)}) ---",
        "These claims used wording specific to a paper they did not cite, so "
        "they were not included in the answer.",
    ]
    for position, claim in enumerate(withheld, start=1):
        lines.append(f"{position}. {claim.text}")
        lines.append(
            f"   Cited: {_format_citation_ids(claim.citation_ids, color=color)}"
        )
        lines.append(f"   Unsupported terms: {', '.join(claim.leaked_terms)}")
    return lines


def _format_page_range(detail: ChatCitationDetail) -> str:
    """Render one passage's page span, collapsing a single-page range."""
    if detail.page_start is None:
        return "N/A"
    if detail.page_end is None or detail.page_end == detail.page_start:
        return str(detail.page_start)
    return f"{detail.page_start}-{detail.page_end}"


def _terminal_color_enabled(stream: TextIO) -> bool:
    """Return whether citation styling is appropriate for a terminal stream."""
    return bool(stream.isatty()) and "NO_COLOR" not in os.environ


def _format_citation_ids(citation_ids: tuple[str, ...], *, color: bool) -> str:
    """Render citation IDs, optionally using terminal blue for the references."""
    rendered = ", ".join(citation_ids)
    if not color:
        return rendered
    return f"\033[34m{rendered}\033[0m"


def _format_citation_group(citation_ids: tuple[str, ...], *, color: bool) -> str:
    """Render a citation group in the existing bracketed output format."""
    rendered = f"[{', '.join(citation_ids)}]"
    if not color:
        return rendered
    return f"\033[34m{rendered}\033[0m"


def _render_citation_lines(
    citations: tuple[ChatCitationDetail, ...], *, color: bool = False
) -> list[str]:
    """Render citations grouped by paper, shared by chat and the shell.

    Retrieval routinely returns several passages of one paper at different
    ranks. Rendering each as its own block repeated the title, paper id, and
    source path, which reads as the same paper listed twice — an impression
    that undermines trust in the citation list precisely when a paper is the
    strongest match. Paper-level facts are printed once; the passages that
    paper contributed are listed beneath it, each keeping its own identifier,
    section, pages, rank, and score.

    Papers appear in order of their best-ranked passage, and passages within a
    paper in their own rank order, so the strongest evidence still reads first.
    """
    grouped: dict[str, list[ChatCitationDetail]] = {}
    for detail in citations:
        grouped.setdefault(detail.paper_id, []).append(detail)

    # Sorted explicitly rather than relying on insertion order: citations
    # normally arrive rank-ascending, but ordering the output must not depend
    # on that holding for every caller.
    by_best_rank = sorted(
        grouped.items(),
        key=lambda entry: min(item.retrieval_rank for item in entry[1]),
    )

    lines: list[str] = []
    for paper_id, details in by_best_rank:
        ordered = sorted(details, key=lambda item: item.retrieval_rank)
        identifiers = _format_citation_group(
            tuple(item.citation_id for item in ordered), color=color
        )
        head = ordered[0]
        lines.append(f"{identifiers} {_sanitize_metadata_field(head.paper_title)}")
        lines.append(f"  Paper ID: {paper_id}")
        lines.append(f"  Source Path: {_sanitize_metadata_field(head.source_path)}")
        lines.append(
            f"  Passages: {len(ordered)}" if len(ordered) > 1 else "  Passages: 1"
        )
        for detail in ordered:
            lines.append(
                "    " + _format_citation_group((detail.citation_id,), color=color)
            )
            heading = _sanitize_metadata_field(detail.section_heading or "N/A")
            lines.append(f"      Section Heading: {heading}")
            lines.append(f"      Page Range: {_format_page_range(detail)}")
            lines.append(f"      Passage ID: {detail.passage_id}")
            lines.append(f"      Retrieval Rank: {detail.retrieval_rank}")
            lines.append(
                "      Retrieval Score: " + format(detail.retrieval_score, ".12g")
            )
    return lines


# C0 controls, C1 controls, and DEL are replaced rather than printed
# verbatim: a raw escape sequence embedded in extracted PDF text could
# otherwise move the cursor or clear the terminal when evidence is rendered.
# Passage text additionally allows tab and newline, which shape its
# paragraph structure; single-line metadata fields allow neither, since an
# embedded newline there could inject a fake "Label: value" line into output
# that otherwise looks like one of ours.
_PASSAGE_CONTROL_REPLACEMENTS = {
    code: "�"
    for code in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
    if code not in (0x09, 0x0A)
}
_METADATA_CONTROL_REPLACEMENTS = {
    code: "�" for code in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
}

EVIDENCE_WRAP_WIDTH = 88


def _sanitize_metadata_field(text: str) -> str:
    """Replace control characters in a single-line rendered metadata field."""
    return text.translate(_METADATA_CONTROL_REPLACEMENTS)


def _wrap_passage_text(text: str, *, width: int = EVIDENCE_WRAP_WIDTH) -> list[str]:
    """Wrap passage text to a readable width without truncating any content.

    Blank lines in the source are preserved as paragraph breaks; every other
    line is word-wrapped independently so a passage longer than the default
    passage-size budget still renders in full. CRLF and lone-CR line endings
    are normalized to LF first, so a CR is never mistaken for stray content
    and replaced with a placeholder glyph.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = normalized.translate(_PASSAGE_CONTROL_REPLACEMENTS)
    lines: list[str] = []
    for paragraph in sanitized.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    return lines


def format_evidence_detail(
    citations: tuple[ChatCitationDetail, ...],
    *,
    citation_id: str | None = None,
    color: bool = False,
) -> str:
    """Render full passage text and provenance for one or all citations.

    Shared by one-shot chat (``--show-evidence``) and the interactive shell
    (``/show``) so both surfaces render byte-for-byte identical evidence for
    the same citation details. Returns an empty string when ``citation_id``
    does not match any of ``citations``; callers distinguish "no such
    citation" from "no citations at all" themselves.
    """
    selected = citations
    if citation_id is not None:
        selected = tuple(item for item in citations if item.citation_id == citation_id)

    blocks: list[str] = []
    for detail in selected:
        title = _sanitize_metadata_field(detail.paper_title)
        section_heading = _sanitize_metadata_field(detail.section_heading or "N/A")
        source_path = _sanitize_metadata_field(detail.source_path)
        lines = [
            f"{_format_citation_group((detail.citation_id,), color=color)} {title}",
            f"  Paper ID: {detail.paper_id}",
            f"  Source Path: {source_path}",
            f"  Section Heading: {section_heading}",
            f"  Page Range: {_format_page_range(detail)}",
            f"  Passage ID: {detail.passage_id}",
            f"  Retrieval Rank: {detail.retrieval_rank}",
            "  Retrieval Score: " + format(detail.retrieval_score, ".12g"),
            "",
        ]
        lines.extend(_wrap_passage_text(detail.passage_text))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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
    config = LlamaCppConfig(**build_llama_cpp_config_kwargs(resolved))
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
                passage_text=stored_passage.text,
            )
        )

    return tuple(details)
