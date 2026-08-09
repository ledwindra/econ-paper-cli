"""Offline ``llama.cpp`` subprocess adapter for the generation protocol."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol, cast

from econ_paper_cli.adapters.filesystem import VerificationError, verify_local_file
from econ_paper_cli.domain import Citation
from econ_paper_cli.protocols import (
    GenerationRequest,
    GenerationResponse,
    GenerationResponseValidationError,
    validate_generation_response,
)

# v2 makes the abstention contract structurally unrepresentable when
# violated, rather than merely instructing the model to honour it. Under v1
# the grammar allowed any combination of `abstained` and `abstention_reason`,
# so a small local model routinely emitted `"abstained": false` together with
# `"abstention_reason": "insufficient_evidence"` — a self-contradiction that
# `validate_generation_response` correctly rejected, surfacing to the user as
# a bare "output violated the generation response schema" and losing the
# answer. The v2 grammar has two mutually exclusive roots that also bind
# citation_ids and finding_kinds to the abstention state.
#
# v2 additionally bounds the list rules. The domain contract forbids
# duplicate finding_kinds, but a grammar permitting unbounded repeats let a
# small model fall into a degenerate loop emitting
# `"causal", "causal", "causal", ...` until it exhausted the output budget
# and truncated mid-object — reaching the user as "Local model returned
# invalid JSON". finding_kinds is now enumerated to its legal unique
# combinations, and citation_ids is length-capped so no runaway is
# representable.
#
# v3 replaces the single flat `answer_text` with a list of claims, each
# carrying the citation identifiers that support that claim alone. The flat
# field could not express which sentence came from which paper, so a fluent
# small model routinely welded two studies into one description: every
# citation identifier was real, `validate_generation_response` passed, and the
# user was told one paper used another paper's design and reported its
# outcomes. Per-claim binding makes that misattribution both representable and
# checkable — see `domain.claim_grounding`, which flags a claim using terms
# distinctive to a paper it does not cite. Measured on a real 248-paper
# library, the same 1.5B model stopped conflating under v3 with no change of
# model, so this is a format fix rather than a capacity one.
PROMPT_VERSION = "generation-v3"
OUTPUT_GRAMMAR_SHA256 = (
    "fb8ad6c38775587e7a695ad9caf47f0f30cbcece2285f6000793ff361856eacf"
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MODEL_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MODEL_OUTPUT_FIELDS = frozenset(
    {
        "claims",
        "abstained",
        "abstention_reason",
        "finding_kinds",
    }
)
FOLLOW_UP_PROMPT_VERSION = "followup-v1"
FOLLOW_UP_GRAMMAR_SHA256 = (
    "e126e95ed59f0480ca37b863420a15d35261f567e247ac47d7b4c3e5b593875d"
)
_RESOURCE_DIRECTORY = Path(__file__).with_name("resources")
_PROMPT_TEMPLATE_PATH = _RESOURCE_DIRECTORY / f"{PROMPT_VERSION}.txt"
_OUTPUT_GRAMMAR_PATH = _RESOURCE_DIRECTORY / f"{PROMPT_VERSION}.gbnf"
_FOLLOW_UP_PROMPT_PATH = _RESOURCE_DIRECTORY / f"{FOLLOW_UP_PROMPT_VERSION}.txt"
_FOLLOW_UP_GRAMMAR_PATH = _RESOURCE_DIRECTORY / f"{FOLLOW_UP_PROMPT_VERSION}.gbnf"
# A resolved question is one sentence, so the budget is far below the answer
# budget. Capping it also bounds the damage from a model that starts
# elaborating instead of rewriting.
_FOLLOW_UP_MAX_OUTPUT_TOKENS = 128
_COMPLETION_FOOTER = "\n> EOF by user"
# llama.cpp tags severity inline, e.g.
#   "0.00.545.833 E llama_completion: prompt is too long (4859 tokens, max 4092)"
# Only the text after an explicit error tag is ever surfaced to the user.
_RUNTIME_ERROR_LINE_RE = re.compile(r"\bE\s+\w+:\s*")
_MAX_RUNTIME_ERROR_DETAIL_CHARS = 200

CancellationCheck = Callable[[], bool]


class LlamaCppAdapterError(Exception):
    """Base exception for concrete local-generation adapter failures."""


class LlamaCppConfigurationError(LlamaCppAdapterError, ValueError):
    """Raised when adapter configuration is invalid."""


class LlamaCppReadinessError(LlamaCppAdapterError):
    """Raised when the configured runtime or model is not ready."""


class LlamaCppProcessError(LlamaCppAdapterError):
    """Raised when the native runtime cannot complete successfully."""


class LlamaCppTimeoutError(LlamaCppProcessError):
    """Raised when generation exceeds its configured timeout."""


class LlamaCppCancelledError(LlamaCppProcessError):
    """Raised when generation is cancelled."""


class LlamaCppOutputLimitError(LlamaCppProcessError):
    """Raised when captured native-process output exceeds its safety bound."""


class LlamaCppOutputError(LlamaCppAdapterError):
    """Raised when model output is malformed or violates the response contract."""


@dataclass(frozen=True, slots=True)
class LlamaCppConfig:
    """Explicit local runtime, model, and reproducibility configuration."""

    executable_path: Path
    model_path: Path
    model_id: str
    model_expected_size_bytes: int
    model_sha256: str
    runtime_id: str = "llama.cpp-b10199"
    runtime_version_marker: str = "10199"
    # Sized from a real local corpus rather than a round number: across a
    # 248-paper economics library, the Abstract+Introduction text a prompt is
    # built from runs ~3.3k tokens at the median and ~6.5k at the 90th
    # percentile. A 4096 window rejected roughly half of those papers with
    # llama.cpp's "prompt is too long", so research-question extraction failed
    # on essentially the whole corpus while surfacing only an opaque non-zero
    # exit status. 16384 admits ~99% of that corpus and still keeps the KV
    # cache small for a local model.
    context_size: int = 16384
    # The grammar-constrained envelope must be emitted in full: a cap that
    # truncates mid-object yields unparseable JSON, which surfaced as
    # "Local model returned invalid JSON" on real papers whose answer text
    # ran long. Kept well below context_size.
    max_output_tokens: int = 1024
    threads: int | None = None
    seed: int = 42
    temperature: float = 0.2
    top_k: int = 20
    top_p: float = 0.9
    timeout_seconds: float = 300.0
    max_captured_output_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        _validate_path("executable_path", self.executable_path)
        _validate_path("model_path", self.model_path)
        if (
            not isinstance(self.model_id, str)
            or _MODEL_ID_PATTERN.fullmatch(self.model_id) is None
        ):
            raise LlamaCppConfigurationError(
                "model_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
            )
        if (
            not isinstance(self.runtime_id, str)
            or _MODEL_ID_PATTERN.fullmatch(self.runtime_id) is None
        ):
            raise LlamaCppConfigurationError(
                "runtime_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
            )
        _validate_nonempty_text("runtime_version_marker", self.runtime_version_marker)
        _validate_positive_int(
            "model_expected_size_bytes", self.model_expected_size_bytes
        )
        if (
            not isinstance(self.model_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.model_sha256) is None
        ):
            raise LlamaCppConfigurationError(
                "model_sha256 must be 64 lowercase hexadecimal characters."
            )
        _validate_positive_int("context_size", self.context_size)
        _validate_positive_int("max_output_tokens", self.max_output_tokens)
        if self.max_output_tokens >= self.context_size:
            raise LlamaCppConfigurationError(
                "max_output_tokens must be smaller than context_size."
            )
        if self.threads is not None:
            _validate_positive_int("threads", self.threads)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise LlamaCppConfigurationError("seed must be an integer.")
        _validate_nonnegative_float("temperature", self.temperature)
        if self.temperature > 2:
            raise LlamaCppConfigurationError(
                "temperature must be less than or equal to 2."
            )
        _validate_nonnegative_int("top_k", self.top_k)
        _validate_probability("top_p", self.top_p)
        _validate_positive_float("timeout_seconds", self.timeout_seconds)
        _validate_positive_int(
            "max_captured_output_bytes", self.max_captured_output_bytes
        )


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded output from one local native-process invocation."""

    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    """Injectable subprocess boundary used by model-free tests."""

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: CancellationCheck | None,
        environment: Mapping[str, str],
    ) -> ProcessResult:
        """Run one bounded native process without a shell."""
        ...


class SubprocessRunner:
    """Run a native process with bounded capture and cooperative cancellation."""

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: CancellationCheck | None,
        environment: Mapping[str, str],
    ) -> ProcessResult:
        if not command:
            raise LlamaCppProcessError("Native process command must not be empty.")

        process = self._start_process(command, environment=environment)
        if process.stdout is None or process.stderr is None:
            _terminate_process(process)
            raise LlamaCppProcessError(
                "Local generation runtime did not expose output pipes."
            )

        limit_exceeded = threading.Event()
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        capture_threads = (
            _start_bounded_pipe_capture(
                process.stdout,
                chunks=stdout_chunks,
                max_output_bytes=max_output_bytes,
                limit_exceeded=limit_exceeded,
            ),
            _start_bounded_pipe_capture(
                process.stderr,
                chunks=stderr_chunks,
                max_output_bytes=max_output_bytes,
                limit_exceeded=limit_exceeded,
            ),
        )
        try:
            self._wait_for_process(
                process,
                timeout_seconds=timeout_seconds,
                cancellation_requested=cancellation_requested,
                output_limit_exceeded=limit_exceeded,
            )
        finally:
            if process.poll() is None:
                _terminate_process(process)
            for thread in capture_threads:
                thread.join()

        if limit_exceeded.is_set():
            raise LlamaCppOutputLimitError(
                "Local generation output exceeded the configured capture limit."
            )
        return ProcessResult(
            returncode=cast(int, process.returncode),
            stdout=_decode_utf8(b"".join(stdout_chunks)),
            stderr=_decode_utf8(b"".join(stderr_chunks)),
        )

    @staticmethod
    def _start_process(
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
    ) -> subprocess.Popen[bytes]:
        kwargs: dict[str, object] = {
            "args": tuple(command),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": dict(environment),
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            return subprocess.Popen(**kwargs)  # type: ignore[arg-type]
        except OSError as error:
            raise LlamaCppProcessError(
                "Unable to start the configured local generation runtime."
            ) from error

    @staticmethod
    def _wait_for_process(
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
        cancellation_requested: CancellationCheck | None,
        output_limit_exceeded: threading.Event,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if output_limit_exceeded.is_set():
                _terminate_process(process)
                raise LlamaCppOutputLimitError(
                    "Local generation output exceeded the configured capture limit."
                )
            if process.poll() is not None:
                return
            if cancellation_requested is not None and cancellation_requested():
                _terminate_process(process)
                raise LlamaCppCancelledError(
                    "Local generation was cancelled before completion."
                )
            if time.monotonic() >= deadline:
                _terminate_process(process)
                raise LlamaCppTimeoutError(
                    "Local generation exceeded its configured timeout."
                )
            time.sleep(0.05)


class LlamaCppGenerator:
    """Concrete, replaceable ``Generator`` backed by ``llama-completion``."""

    def __init__(
        self,
        config: LlamaCppConfig,
        *,
        process_runner: ProcessRunner | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> None:
        if not isinstance(config, LlamaCppConfig):
            raise LlamaCppConfigurationError(
                "config must be a LlamaCppConfig instance."
            )
        self._config = config
        self._process_runner = process_runner or SubprocessRunner()
        self._cancellation_requested = cancellation_requested
        self._ready = False

    @property
    def generation_method(self) -> str:
        """Return a path-independent identity for the exact generation setup."""
        identity = {
            "context_size": self._config.context_size,
            "max_output_tokens": self._config.max_output_tokens,
            "model_sha256": self._config.model_sha256,
            "output_grammar_sha256": OUTPUT_GRAMMAR_SHA256,
            "prompt_version": PROMPT_VERSION,
            "reasoning": "off",
            "seed": self._config.seed,
            "temperature": self._config.temperature,
            "threads": self._config.threads,
            "top_k": self._config.top_k,
            "top_p": self._config.top_p,
        }
        canonical = json.dumps(identity, separators=(",", ":"), sort_keys=True)
        config_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return (
            f"{self._config.runtime_id}:"
            f"{self._config.model_id}@sha256-{self._config.model_sha256}:"
            f"prompt-{PROMPT_VERSION}:config-sha256-{config_digest}"
        )

    def check_readiness(self) -> None:
        """Validate local paths, the exact model artifact, and runtime identity."""
        executable = self._config.executable_path
        model = self._config.model_path
        if not executable.exists():
            raise LlamaCppReadinessError(
                f"Configured llama.cpp executable does not exist at '{executable}'."
            )
        if not executable.is_file():
            raise LlamaCppReadinessError(
                f"Configured llama.cpp executable is not a regular file: '{executable}'."
            )
        if os.name != "nt" and not os.access(executable, os.X_OK):
            raise LlamaCppReadinessError(
                f"Configured llama.cpp file is not executable: '{executable}'."
            )
        try:
            verify_local_file(
                model,
                expected_size_bytes=self._config.model_expected_size_bytes,
                expected_sha256=self._config.model_sha256,
            )
        except VerificationError as error:
            raise LlamaCppReadinessError(
                "Configured model artifact failed local size or checksum verification."
            ) from error

        result = self._run_process((str(executable), "--version", "--offline"))
        if result.returncode != 0:
            raise LlamaCppReadinessError(
                "Configured llama.cpp executable failed its version readiness check."
            )
        version_output = f"{result.stdout}\n{result.stderr}"
        if self._config.runtime_version_marker not in version_output:
            raise LlamaCppReadinessError(
                "Configured llama.cpp executable does not match the expected "
                "runtime version marker."
            )
        self._ready = True

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate, parse, resolve, and validate one evidence-grounded response."""
        if not isinstance(request, GenerationRequest):
            raise LlamaCppConfigurationError(
                "request must be a GenerationRequest instance."
            )
        if not self._ready:
            self.check_readiness()

        prompt = render_generation_prompt(request)
        grammar = _load_resource_text(_OUTPUT_GRAMMAR_PATH, "output grammar")
        if hashlib.sha256(grammar.encode("utf-8")).hexdigest() != OUTPUT_GRAMMAR_SHA256:
            raise LlamaCppConfigurationError(
                "Packaged output grammar does not match its expected fingerprint."
            )
        with tempfile.TemporaryDirectory(prefix="econpapers-generation-") as directory:
            temporary_directory = Path(directory)
            prompt_path = temporary_directory / "prompt.txt"
            grammar_path = temporary_directory / "grammar.gbnf"
            _write_private_text_file(prompt_path, prompt)
            _write_private_text_file(grammar_path, grammar)
            command = self._generation_command(
                prompt_path=prompt_path,
                grammar_path=grammar_path,
            )
            result = self._run_process(command)

        if result.returncode != 0:
            detail = _runtime_error_detail(result.stderr)
            raise LlamaCppProcessError(
                f"Local generation runtime exited with status {result.returncode}."
                + (f" {detail}" if detail else "")
            )
        response = self._parse_response(request, result.stdout)
        try:
            return validate_generation_response(request, response)
        except GenerationResponseValidationError as error:
            raise LlamaCppOutputError(
                "Local model output violated the generation response contract."
            ) from error

    def resolve_follow_up(self, question: str, prior_questions: Sequence[str]) -> str:
        """Rewrite a context-dependent question into a standalone one.

        Returns ``question`` unchanged when there is no prior context. The
        caller is responsible for deciding that a question *needs* resolution;
        this method performs the rewrite it is asked for.
        """
        if not isinstance(question, str) or not question.strip():
            raise LlamaCppConfigurationError("question must be a non-empty string.")
        if not prior_questions:
            return question.strip()
        if not self._ready:
            self.check_readiness()

        payload = json.dumps(
            {
                "earlier_questions": list(prior_questions),
                "follow_up_question": question.strip(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        template = _load_resource_text(_FOLLOW_UP_PROMPT_PATH, "follow-up prompt")
        prompt = template.replace("{{CONTEXT_JSON}}", payload)
        grammar = _load_resource_text(_FOLLOW_UP_GRAMMAR_PATH, "follow-up grammar")
        if (
            hashlib.sha256(grammar.encode("utf-8")).hexdigest()
            != FOLLOW_UP_GRAMMAR_SHA256
        ):
            raise LlamaCppConfigurationError(
                "Packaged follow-up grammar does not match its expected fingerprint."
            )

        with tempfile.TemporaryDirectory(prefix="econpapers-followup-") as directory:
            temporary_directory = Path(directory)
            prompt_path = temporary_directory / "prompt.txt"
            grammar_path = temporary_directory / "grammar.gbnf"
            _write_private_text_file(prompt_path, prompt)
            _write_private_text_file(grammar_path, grammar)
            command = self._generation_command(
                prompt_path=prompt_path,
                grammar_path=grammar_path,
                max_output_tokens=_FOLLOW_UP_MAX_OUTPUT_TOKENS,
            )
            result = self._run_process(command)

        if result.returncode != 0:
            detail = _runtime_error_detail(result.stderr)
            raise LlamaCppProcessError(
                f"Follow-up resolution runtime exited with status {result.returncode}."
                + (f" {detail}" if detail else "")
            )

        data = _parse_single_json_object(_strip_completion_footer(result.stdout))
        if set(data) != {"resolved_question"}:
            raise LlamaCppOutputError(
                "Follow-up resolution output did not contain the exact required fields."
            )
        resolved = data["resolved_question"]
        if not isinstance(resolved, str) or not resolved.strip():
            raise LlamaCppOutputError(
                "Follow-up resolution returned an empty question."
            )
        return resolved.strip()

    def _generation_command(
        self,
        *,
        prompt_path: Path,
        grammar_path: Path,
        max_output_tokens: int | None = None,
    ) -> tuple[str, ...]:
        command = [
            str(self._config.executable_path),
            "--model",
            str(self._config.model_path),
            "--file",
            str(prompt_path),
            "--grammar-file",
            str(grammar_path),
            "--offline",
            "--device",
            "none",
            "--no-display-prompt",
            "--log-file",
            os.devnull,
            "--color",
            "off",
            "--simple-io",
            "--jinja",
            "--reasoning",
            "off",
            "--no-context-shift",
            "--ctx-size",
            str(self._config.context_size),
            "--predict",
            str(
                self._config.max_output_tokens
                if max_output_tokens is None
                else max_output_tokens
            ),
            "--seed",
            str(self._config.seed),
            "--temp",
            str(self._config.temperature),
            "--top-k",
            str(self._config.top_k),
            "--top-p",
            str(self._config.top_p),
        ]
        if self._config.threads is not None:
            command.extend(("--threads", str(self._config.threads)))
        return tuple(command)

    def _run_process(self, command: Sequence[str]) -> ProcessResult:
        result = self._process_runner.run(
            command,
            timeout_seconds=self._config.timeout_seconds,
            max_output_bytes=self._config.max_captured_output_bytes,
            cancellation_requested=self._cancellation_requested,
            environment=_offline_environment(),
        )
        if not isinstance(result, ProcessResult):
            raise LlamaCppProcessError(
                "Process runner returned an invalid result object."
            )
        return result

    def _parse_response(
        self, request: GenerationRequest, raw_output: str
    ) -> GenerationResponse:
        data = _parse_single_json_object(_strip_completion_footer(raw_output))
        provided = set(data)
        missing = sorted(_MODEL_OUTPUT_FIELDS - provided)
        unknown = sorted(provided - _MODEL_OUTPUT_FIELDS)
        if missing or unknown:
            raise LlamaCppOutputError(
                "Local model output did not contain the exact required fields."
            )

        raw_claims = data["claims"]
        if not isinstance(raw_claims, list):
            raise LlamaCppOutputError("Local model claims must be a JSON array.")
        claims = [_parse_model_claim(item) for item in raw_claims]

        evidence_by_id = {
            f"e{evidence.rank}": evidence for evidence in request.evidence
        }
        # The model no longer emits a separate citation list, so the response's
        # citations are derived from what the claims actually cite. That makes
        # a claim/citation disagreement unrepresentable rather than merely
        # invalid, and it is what supplies the rank ordering the response
        # contract requires.
        cited_ids: list[str] = []
        for claim in claims:
            for citation_id in claim["citation_ids"]:
                if citation_id not in evidence_by_id:
                    raise LlamaCppOutputError(
                        "Local model returned an unknown evidence citation identifier."
                    )
                if citation_id not in cited_ids:
                    cited_ids.append(citation_id)
        cited_ids.sort(key=lambda citation_id: evidence_by_id[citation_id].rank)

        citations: list[dict[str, str]] = []
        for citation_id in cited_ids:
            evidence = evidence_by_id[citation_id]
            citation = Citation(
                citation_id=citation_id,
                paper_id=evidence.passage.paper_id,
                passage_id=evidence.passage.passage_id,
            )
            citations.append(cast(dict[str, str], citation.to_mapping()))

        # `answer_text` stays populated for every consumer that renders a
        # single block of prose; the claims carry the attribution alongside it.
        answer_text = " ".join(claim["text"].strip() for claim in claims).strip()
        if not answer_text:
            answer_text = "No supported claim was produced from the evidence."

        try:
            return GenerationResponse.from_mapping(
                {
                    "answer_text": answer_text,
                    "citations": citations,
                    "generation_method": self.generation_method,
                    "abstained": data["abstained"],
                    "abstention_reason": data["abstention_reason"],
                    "finding_kinds": data["finding_kinds"],
                    "claims": claims,
                }
            )
        except GenerationResponseValidationError as error:
            raise LlamaCppOutputError(
                "Local model output violated the generation response schema."
            ) from error


def _parse_model_claim(item: object) -> dict[str, object]:
    """Validate one raw model claim before it reaches the response contract."""
    if not isinstance(item, dict):
        raise LlamaCppOutputError("Local model claims must be JSON objects.")
    if set(item) != {"text", "citation_ids"}:
        raise LlamaCppOutputError(
            "Local model claim did not contain the exact required fields."
        )
    text = item["text"]
    if not isinstance(text, str) or not text.strip():
        raise LlamaCppOutputError("Local model claim text must be a non-empty string.")
    raw_ids = item["citation_ids"]
    if (
        not isinstance(raw_ids, list)
        or not raw_ids
        or any(not isinstance(entry, str) for entry in raw_ids)
    ):
        raise LlamaCppOutputError(
            "Local model claim citation_ids must be a non-empty array of strings."
        )
    # The grammar cannot express uniqueness within a list, so a repeated
    # identifier is possible output and is normalized here rather than
    # rejected: it costs the user their answer for a harmless duplication.
    unique_ids: list[str] = []
    for entry in cast(list[str], raw_ids):
        if entry not in unique_ids:
            unique_ids.append(entry)
    return {"text": text, "citation_ids": unique_ids}


def render_generation_prompt(request: GenerationRequest) -> str:
    """Render the stable prompt without adding unavailable paper attributes."""
    if not isinstance(request, GenerationRequest):
        raise LlamaCppConfigurationError(
            "request must be a GenerationRequest instance."
        )
    template = _load_resource_text(_PROMPT_TEMPLATE_PATH, "prompt template")
    evidence = [
        {
            "citation_id": f"e{item.rank}",
            "paper_id": item.passage.paper_id,
            "passage_id": item.passage.passage_id,
            "text": item.passage.text,
            "section_heading": item.passage.section_heading,
            "page_start": item.passage.page_start,
            "page_end": item.passage.page_end,
        }
        for item in request.evidence
    ]
    payload = json.dumps(
        {"question": request.question, "evidence": evidence},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return template.replace("{{REQUEST_JSON}}", payload)


def _parse_single_json_object(raw_output: str) -> dict[str, object]:
    if not isinstance(raw_output, str):
        raise LlamaCppOutputError("Local model output must be UTF-8 text.")
    stripped = raw_output.strip()
    if not stripped:
        raise LlamaCppOutputError("Local model returned empty output.")
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as error:
        raise LlamaCppOutputError("Local model returned invalid JSON.") from error
    if stripped[end:].strip():
        raise LlamaCppOutputError(
            "Local model returned extra content outside the JSON object."
        )
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LlamaCppOutputError("Local model output must be one JSON object.")
    return cast(dict[str, object], value)


def _runtime_error_detail(stderr: str) -> str:
    """Return the runtime's own final error line, or "" if none is present.

    Only lines the runtime explicitly tagged as errors are surfaced, never
    arbitrary stderr content: the captured stream can echo prompt-derived
    material, and this project must not leak substantial document excerpts
    into error messages or logs. The result is additionally length-capped.

    Without this, a perfectly diagnosable failure such as llama.cpp's
    "prompt is too long (4859 tokens, max 4092)" reached the user as nothing
    but "exited with status 1".
    """
    if not isinstance(stderr, str):
        return ""
    for line in reversed(stderr.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        marker = _RUNTIME_ERROR_LINE_RE.search(candidate)
        if marker is not None:
            message = candidate[marker.end() :].strip()
            if message:
                return message[:_MAX_RUNTIME_ERROR_DETAIL_CHARS]
    return ""


def _strip_completion_footer(raw_output: str) -> str:
    stripped = raw_output.rstrip()
    if stripped.endswith(_COMPLETION_FOOTER):
        return stripped[: -len(_COMPLETION_FOOTER)].rstrip()
    return raw_output


def _offline_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "LLAMA_ARG_HF_REPO",
        "LLAMA_ARG_HF_FILE",
        "LLAMA_ARG_MODEL_URL",
    ):
        environment.pop(name, None)
    environment["LLAMA_ARG_OFFLINE"] = "1"
    return environment


def _load_resource_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LlamaCppConfigurationError(
            f"Packaged {label} '{path.name}' could not be loaded."
        ) from error


def _write_private_text_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _start_bounded_pipe_capture(
    pipe: BinaryIO,
    *,
    chunks: list[bytes],
    max_output_bytes: int,
    limit_exceeded: threading.Event,
) -> threading.Thread:
    def capture() -> None:
        total_bytes = 0
        try:
            while True:
                remaining_with_sentinel = max_output_bytes - total_bytes + 1
                chunk = pipe.read(min(65_536, remaining_with_sentinel))
                if not chunk:
                    return
                total_bytes += len(chunk)
                if total_bytes > max_output_bytes:
                    limit_exceeded.set()
                    return
                chunks.append(chunk)
        finally:
            pipe.close()

    thread = threading.Thread(target=capture, daemon=True)
    thread.start()
    return thread


def _decode_utf8(output: bytes) -> str:
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LlamaCppProcessError(
            "Local generation runtime returned output that was not valid UTF-8."
        ) from error


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
            process.wait()


def _validate_path(label: str, value: object) -> None:
    if not isinstance(value, Path):
        raise LlamaCppConfigurationError(f"{label} must be a pathlib.Path.")


def _validate_nonempty_text(label: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise LlamaCppConfigurationError(f"{label} must be a non-empty string.")


def _validate_positive_int(label: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LlamaCppConfigurationError(f"{label} must be a positive integer.")


def _validate_nonnegative_int(label: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LlamaCppConfigurationError(f"{label} must be a non-negative integer.")


def _validate_positive_float(label: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not float(value) > 0
        or not float(value) < float("inf")
    ):
        raise LlamaCppConfigurationError(f"{label} must be a finite positive number.")


def _validate_nonnegative_float(label: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) < float("inf")
    ):
        raise LlamaCppConfigurationError(
            f"{label} must be a finite non-negative number."
        )


def _validate_probability(label: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < float(value) <= 1
    ):
        raise LlamaCppConfigurationError(
            f"{label} must be a number greater than 0 and at most 1."
        )
