"""Offline ``llama.cpp`` subprocess adapter for the generation protocol."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from econ_paper_cli.adapters.filesystem import VerificationError, verify_local_file
from econ_paper_cli.domain import Citation
from econ_paper_cli.protocols import (
    GenerationRequest,
    GenerationResponse,
    GenerationResponseValidationError,
    validate_generation_response,
)

PROMPT_VERSION = "generation-v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MODEL_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MODEL_OUTPUT_FIELDS = frozenset(
    {
        "answer_text",
        "citation_ids",
        "abstained",
        "abstention_reason",
        "finding_kinds",
    }
)
_RESOURCE_DIRECTORY = Path(__file__).with_name("resources")
_PROMPT_TEMPLATE_PATH = _RESOURCE_DIRECTORY / f"{PROMPT_VERSION}.txt"
_OUTPUT_SCHEMA_PATH = _RESOURCE_DIRECTORY / f"{PROMPT_VERSION}.schema.json"

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
    context_size: int = 4096
    max_output_tokens: int = 512
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

        with tempfile.TemporaryDirectory(prefix="econpapers-process-") as directory:
            capture_directory = Path(directory)
            stdout_path = capture_directory / "stdout"
            stderr_path = capture_directory / "stderr"
            stdout_file = _open_private_binary_file(stdout_path)
            stderr_file = _open_private_binary_file(stderr_path)
            try:
                process = self._start_process(
                    command,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    environment=environment,
                )
                self._wait_for_process(
                    process,
                    timeout_seconds=timeout_seconds,
                    cancellation_requested=cancellation_requested,
                )
            finally:
                stdout_file.close()
                stderr_file.close()

            stdout = _read_bounded_utf8(stdout_path, max_output_bytes=max_output_bytes)
            stderr = _read_bounded_utf8(stderr_path, max_output_bytes=max_output_bytes)
            return ProcessResult(
                returncode=cast(int, process.returncode),
                stdout=stdout,
                stderr=stderr,
            )

    @staticmethod
    def _start_process(
        command: Sequence[str],
        *,
        stdout_file: object,
        stderr_file: object,
        environment: Mapping[str, str],
    ) -> subprocess.Popen[bytes]:
        kwargs: dict[str, object] = {
            "args": tuple(command),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_file,
            "stderr": stderr_file,
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
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
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
    """Concrete, replaceable ``Generator`` backed by a local ``llama-cli``."""

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
        schema = _load_resource_text(_OUTPUT_SCHEMA_PATH, "output schema")
        with tempfile.TemporaryDirectory(prefix="econpapers-generation-") as directory:
            temporary_directory = Path(directory)
            prompt_path = temporary_directory / "prompt.txt"
            schema_path = temporary_directory / "schema.json"
            _write_private_text_file(prompt_path, prompt)
            _write_private_text_file(schema_path, schema)
            command = self._generation_command(
                prompt_path=prompt_path,
                schema_path=schema_path,
            )
            result = self._run_process(command)

        if result.returncode != 0:
            raise LlamaCppProcessError(
                f"Local generation runtime exited with status {result.returncode}."
            )
        response = self._parse_response(request, result.stdout)
        try:
            return validate_generation_response(request, response)
        except GenerationResponseValidationError as error:
            raise LlamaCppOutputError(
                "Local model output violated the generation response contract."
            ) from error

    def _generation_command(
        self, *, prompt_path: Path, schema_path: Path
    ) -> tuple[str, ...]:
        command = [
            str(self._config.executable_path),
            "--model",
            str(self._config.model_path),
            "--file",
            str(prompt_path),
            "--json-schema-file",
            str(schema_path),
            "--offline",
            "--no-display-prompt",
            "--log-disable",
            "--no-show-timings",
            "--color",
            "off",
            "--single-turn",
            "--simple-io",
            "--jinja",
            "--reasoning",
            "off",
            "--no-context-shift",
            "--ctx-size",
            str(self._config.context_size),
            "--predict",
            str(self._config.max_output_tokens),
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
        data = _parse_single_json_object(raw_output)
        provided = set(data)
        missing = sorted(_MODEL_OUTPUT_FIELDS - provided)
        unknown = sorted(provided - _MODEL_OUTPUT_FIELDS)
        if missing or unknown:
            raise LlamaCppOutputError(
                "Local model output did not contain the exact required fields."
            )

        raw_citation_ids = data["citation_ids"]
        if not isinstance(raw_citation_ids, list) or any(
            not isinstance(item, str) for item in raw_citation_ids
        ):
            raise LlamaCppOutputError(
                "Local model citation_ids must be a JSON array of strings."
            )
        citation_ids = cast(list[str], raw_citation_ids)
        evidence_by_id = {
            f"e{evidence.rank}": evidence for evidence in request.evidence
        }
        citations: list[dict[str, str]] = []
        for citation_id in citation_ids:
            evidence = evidence_by_id.get(citation_id)
            if evidence is None:
                raise LlamaCppOutputError(
                    "Local model returned an unknown evidence citation identifier."
                )
            citation = Citation(
                citation_id=citation_id,
                paper_id=evidence.passage.paper_id,
                passage_id=evidence.passage.passage_id,
            )
            citations.append(cast(dict[str, str], citation.to_mapping()))

        try:
            return GenerationResponse.from_mapping(
                {
                    "answer_text": data["answer_text"],
                    "citations": citations,
                    "generation_method": self.generation_method,
                    "abstained": data["abstained"],
                    "abstention_reason": data["abstention_reason"],
                    "finding_kinds": data["finding_kinds"],
                }
            )
        except GenerationResponseValidationError as error:
            raise LlamaCppOutputError(
                "Local model output violated the generation response schema."
            ) from error


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


def _open_private_binary_file(path: Path) -> object:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def _read_bounded_utf8(path: Path, *, max_output_bytes: int) -> str:
    size = path.stat().st_size
    if size > max_output_bytes:
        raise LlamaCppOutputLimitError(
            "Local generation output exceeded the configured capture limit."
        )
    try:
        return path.read_text(encoding="utf-8")
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
