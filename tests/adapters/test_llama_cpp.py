"""Model-free tests for the concrete llama.cpp generation adapter."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from econ_paper_cli.adapters import (
    LlamaCppCancelledError,
    LlamaCppConfig,
    LlamaCppConfigurationError,
    LlamaCppGenerator,
    LlamaCppOutputError,
    LlamaCppOutputLimitError,
    LlamaCppProcessError,
    LlamaCppReadinessError,
    LlamaCppTimeoutError,
    ProcessResult,
    SubprocessRunner,
    render_generation_prompt,
)
from econ_paper_cli.adapters import llama_cpp as llama_cpp_module
from econ_paper_cli.domain import Passage, RetrievalEvidence
from econ_paper_cli.protocols import (
    FindingKind,
    GenerationRequest,
    Generator,
)


def make_request(
    *,
    question: str = "What does the evidence find?",
    text: str = "The synthetic estimate was 4 units.",
) -> GenerationRequest:
    passage = Passage.from_mapping(
        {
            "passage_id": "paper-1:passage-1",
            "paper_id": "paper-1",
            "text": text,
            "section_heading": "Results",
            "page_start": 4,
            "page_end": 4,
            "ordinal_position": 0,
        }
    )
    return GenerationRequest(
        question=question,
        evidence=(
            RetrievalEvidence(
                passage=passage,
                score=1.0,
                rank=1,
                retrieval_method="test-v1",
            ),
        ),
    )


def successful_output(*, citation_ids: list[str] | None = None) -> str:
    return json.dumps(
        {
            "answer_text": "The synthetic estimate was 4 units.",
            "citation_ids": ["e1"] if citation_ids is None else citation_ids,
            "abstained": False,
            "abstention_reason": None,
            "finding_kinds": ["descriptive"],
        }
    )


class RecordingRunner:
    """Return a version then configured model output while recording inputs."""

    def __init__(
        self,
        output: str | None = None,
        *,
        generation_returncode: int = 0,
        version_output: str = "llama.cpp build 10199",
    ) -> None:
        self.output = output if output is not None else successful_output()
        self.generation_returncode = generation_returncode
        self.version_output = version_output
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.prompt_text: str | None = None
        self.schema_text: str | None = None
        self.prompt_mode: int | None = None
        self.schema_mode: int | None = None
        self.prompt_path: Path | None = None
        self.schema_path: Path | None = None

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: object,
        environment: Mapping[str, str],
    ) -> ProcessResult:
        recorded = tuple(command)
        self.commands.append(recorded)
        self.environments.append(dict(environment))
        if "--version" in recorded:
            return ProcessResult(0, self.version_output, "")

        prompt_path = Path(recorded[recorded.index("--file") + 1])
        schema_path = Path(recorded[recorded.index("--json-schema-file") + 1])
        self.prompt_path = prompt_path
        self.schema_path = schema_path
        self.prompt_text = prompt_path.read_text(encoding="utf-8")
        self.schema_text = schema_path.read_text(encoding="utf-8")
        self.prompt_mode = prompt_path.stat().st_mode & 0o777
        self.schema_mode = schema_path.stat().st_mode & 0o777
        return ProcessResult(self.generation_returncode, self.output, "private stderr")


class RaisingRunner:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.commands: list[tuple[str, ...]] = []
        self.prompt_path: Path | None = None
        self.schema_path: Path | None = None

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: object,
        environment: Mapping[str, str],
    ) -> ProcessResult:
        recorded = tuple(command)
        self.commands.append(recorded)
        if "--file" in recorded:
            self.prompt_path = Path(recorded[recorded.index("--file") + 1])
            self.schema_path = Path(recorded[recorded.index("--json-schema-file") + 1])
        if "--version" in recorded:
            return ProcessResult(0, "llama.cpp build 10199", "")
        raise self.error


def make_config(tmp_path: Path, *, model_name: str = "model.gguf") -> LlamaCppConfig:
    executable = tmp_path / "runtime with spaces"
    executable.write_bytes(b"test executable")
    executable.chmod(0o700)
    model = tmp_path / model_name
    model_content = b"synthetic model bytes"
    model.write_bytes(model_content)
    return LlamaCppConfig(
        executable_path=executable,
        model_path=model,
        model_id="synthetic-model",
        model_expected_size_bytes=len(model_content),
        model_sha256=hashlib.sha256(model_content).hexdigest(),
        threads=2,
    )


def test_adapter_implements_generator_and_resolves_authoritative_citation(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    response = generator.generate(make_request())

    assert isinstance(generator, Generator)
    assert response.answer_text == "The synthetic estimate was 4 units."
    assert response.citations[0].to_mapping() == {
        "citation_id": "e1",
        "paper_id": "paper-1",
        "passage_id": "paper-1:passage-1",
    }
    assert response.finding_kinds == (FindingKind.DESCRIPTIVE,)
    assert response.generation_method.startswith(
        "llama.cpp-b10199:synthetic-model@sha256-"
    )
    assert len(runner.commands) == 2


def test_command_keeps_private_text_out_of_arguments_and_forces_offline_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = "PRIVATE QUESTION TOKEN"
    evidence = "PRIVATE EVIDENCE TOKEN"
    monkeypatch.setenv("HF_TOKEN", "secret")
    monkeypatch.setenv("LLAMA_ARG_HF_REPO", "network-model")
    runner = RecordingRunner()
    config = make_config(tmp_path, model_name="model ü with spaces.gguf")
    generator = LlamaCppGenerator(config, process_runner=runner)

    generator.generate(make_request(question=question, text=evidence))

    command = runner.commands[-1]
    joined = " ".join(command)
    assert question not in joined
    assert evidence not in joined
    assert "--file" in command
    assert "--json-schema-file" in command
    assert "--offline" in command
    assert "--no-display-prompt" in command
    assert "--log-disable" in command
    assert "--reasoning" in command
    assert command[command.index("--reasoning") + 1] == "off"
    assert "--no-context-shift" in command
    assert "-hf" not in command
    assert "--hf-repo" not in command
    assert "--model-url" not in command
    assert command[command.index("--model") + 1] == str(config.model_path)
    assert runner.prompt_text is not None
    assert question in runner.prompt_text
    assert evidence in runner.prompt_text
    assert runner.environments[-1]["LLAMA_ARG_OFFLINE"] == "1"
    assert "HF_TOKEN" not in runner.environments[-1]
    assert "LLAMA_ARG_HF_REPO" not in runner.environments[-1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable")
def test_prompt_and_schema_are_private_and_cleaned_after_success(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    generator.generate(make_request())

    assert runner.prompt_mode == 0o600
    assert runner.schema_mode == 0o600
    assert runner.prompt_path is not None
    assert runner.schema_path is not None
    assert not runner.prompt_path.exists()
    assert not runner.schema_path.exists()


def test_temporary_files_are_cleaned_when_process_raises(tmp_path: Path) -> None:
    runner = RaisingRunner(LlamaCppCancelledError("cancelled"))
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    with pytest.raises(LlamaCppCancelledError, match="cancelled"):
        generator.generate(make_request())

    assert runner.prompt_path is not None
    assert runner.schema_path is not None
    assert not runner.prompt_path.exists()
    assert not runner.schema_path.exists()


def test_temporary_files_are_cleaned_when_output_limit_is_exceeded(
    tmp_path: Path,
) -> None:
    runner = RaisingRunner(
        LlamaCppOutputLimitError("Local generation output exceeded the capture limit.")
    )
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    with pytest.raises(LlamaCppOutputLimitError, match="capture limit"):
        generator.generate(make_request())

    assert runner.prompt_path is not None
    assert runner.schema_path is not None
    assert not runner.prompt_path.exists()
    assert not runner.schema_path.exists()


def test_rendered_prompt_is_deterministic_and_marks_evidence_untrusted() -> None:
    request = make_request()

    first = render_generation_prompt(request)
    second = render_generation_prompt(request)

    assert first == second
    assert '"citation_id": "e1"' in first
    assert '"paper_id": "paper-1"' in first
    assert "untrusted quoted material" in first
    assert "never override these rules" in first
    assert "paper title" in first


def test_generation_method_does_not_contain_machine_paths(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    generator = LlamaCppGenerator(config, process_runner=RecordingRunner())

    method = generator.generation_method

    assert str(tmp_path) not in method
    assert config.model_sha256 in method
    assert "generation-v1" in method


def test_readiness_rejects_missing_executable(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.executable_path.unlink()
    generator = LlamaCppGenerator(config, process_runner=RecordingRunner())

    with pytest.raises(LlamaCppReadinessError, match="does not exist"):
        generator.check_readiness()


def test_readiness_rejects_model_checksum_mismatch(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.model_path.write_bytes(b"changed")
    generator = LlamaCppGenerator(config, process_runner=RecordingRunner())

    with pytest.raises(LlamaCppReadinessError, match="size or checksum verification"):
        generator.check_readiness()


def test_readiness_rejects_unexpected_runtime_version(tmp_path: Path) -> None:
    generator = LlamaCppGenerator(
        make_config(tmp_path),
        process_runner=RecordingRunner(version_output="llama.cpp build 999"),
    )

    with pytest.raises(LlamaCppReadinessError, match="version marker"):
        generator.check_readiness()


def test_nonzero_runtime_exit_does_not_include_captured_private_output(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        output="PRIVATE QUESTION TOKEN",
        generation_returncode=7,
    )
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    with pytest.raises(LlamaCppProcessError) as caught:
        generator.generate(make_request(question="PRIVATE QUESTION TOKEN"))

    assert "status 7" in str(caught.value)
    assert "PRIVATE QUESTION TOKEN" not in str(caught.value)
    assert "private stderr" not in str(caught.value)


@pytest.mark.parametrize(
    ("output", "message"),
    (
        ("", "empty output"),
        ("not json", "invalid JSON"),
        (successful_output() + " trailing", "extra content"),
        (json.dumps([]), "one JSON object"),
        (
            json.dumps(
                {
                    "answer_text": "Answer.",
                    "citation_ids": ["e1"],
                    "abstained": False,
                    "abstention_reason": None,
                    "finding_kinds": ["descriptive"],
                    "extra": "field",
                }
            ),
            "exact required fields",
        ),
    ),
)
def test_malformed_or_extra_output_is_rejected_without_echoing_it(
    tmp_path: Path, output: str, message: str
) -> None:
    generator = LlamaCppGenerator(
        make_config(tmp_path), process_runner=RecordingRunner(output)
    )

    with pytest.raises(LlamaCppOutputError, match=message) as caught:
        generator.generate(make_request())

    if output:
        assert output not in str(caught.value)


def test_unknown_citation_id_is_rejected(tmp_path: Path) -> None:
    generator = LlamaCppGenerator(
        make_config(tmp_path),
        process_runner=RecordingRunner(successful_output(citation_ids=["e99"])),
    )

    with pytest.raises(LlamaCppOutputError, match="unknown evidence citation"):
        generator.generate(make_request())


def test_invalid_abstention_contract_is_rejected(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "answer_text": "I cannot answer.",
            "citation_ids": ["e1"],
            "abstained": True,
            "abstention_reason": "insufficient_evidence",
            "finding_kinds": [],
        }
    )
    generator = LlamaCppGenerator(
        make_config(tmp_path), process_runner=RecordingRunner(output)
    )

    with pytest.raises(LlamaCppOutputError, match="response schema"):
        generator.generate(make_request())


def test_out_of_order_citations_fail_existing_response_validator(
    tmp_path: Path,
) -> None:
    first = make_request().evidence[0]
    second_passage = Passage.from_mapping(
        {
            "passage_id": "paper-2:passage-1",
            "paper_id": "paper-2",
            "text": "A second finding.",
            "section_heading": "Results",
            "page_start": 2,
            "page_end": 2,
            "ordinal_position": 0,
        }
    )
    request = GenerationRequest(
        question="What are the findings?",
        evidence=(
            first,
            RetrievalEvidence(
                passage=second_passage,
                score=0.5,
                rank=2,
                retrieval_method="test-v1",
            ),
        ),
    )
    generator = LlamaCppGenerator(
        make_config(tmp_path),
        process_runner=RecordingRunner(successful_output(citation_ids=["e2", "e1"])),
    )

    with pytest.raises(LlamaCppOutputError, match="response contract"):
        generator.generate(request)


def test_empty_evidence_can_produce_contract_valid_abstention(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "answer_text": "The supplied evidence is insufficient.",
            "citation_ids": [],
            "abstained": True,
            "abstention_reason": "insufficient_evidence",
            "finding_kinds": [],
        }
    )
    request = GenerationRequest(question="What is known?", evidence=())
    generator = LlamaCppGenerator(
        make_config(tmp_path), process_runner=RecordingRunner(output)
    )

    response = generator.generate(request)

    assert response.abstained is True
    assert response.citations == ()


def test_config_rejects_invalid_context_and_digest(tmp_path: Path) -> None:
    valid = make_config(tmp_path)

    with pytest.raises(LlamaCppConfigurationError, match="smaller than context"):
        LlamaCppConfig(
            executable_path=valid.executable_path,
            model_path=valid.model_path,
            model_id=valid.model_id,
            model_expected_size_bytes=valid.model_expected_size_bytes,
            model_sha256=valid.model_sha256,
            context_size=32,
            max_output_tokens=32,
        )
    with pytest.raises(LlamaCppConfigurationError, match="64 lowercase"):
        LlamaCppConfig(
            executable_path=valid.executable_path,
            model_path=valid.model_path,
            model_id=valid.model_id,
            model_expected_size_bytes=valid.model_expected_size_bytes,
            model_sha256="not-a-digest",
        )


def test_subprocess_runner_enforces_timeout_without_a_shell() -> None:
    runner = SubprocessRunner()

    with pytest.raises(LlamaCppTimeoutError, match="configured timeout"):
        runner.run(
            (sys.executable, "-c", "import time; time.sleep(2)"),
            timeout_seconds=0.05,
            max_output_bytes=1024,
            cancellation_requested=None,
            environment=os.environ,
        )


def test_subprocess_runner_honors_cancellation() -> None:
    runner = SubprocessRunner()

    with pytest.raises(LlamaCppCancelledError, match="cancelled"):
        runner.run(
            (sys.executable, "-c", "import time; time.sleep(2)"),
            timeout_seconds=2,
            max_output_bytes=1024,
            cancellation_requested=lambda: True,
            environment=os.environ,
        )


def test_subprocess_runner_rejects_output_above_capture_bound() -> None:
    runner = SubprocessRunner()

    with pytest.raises(LlamaCppOutputLimitError, match="capture limit"):
        runner.run(
            (sys.executable, "-c", "print('x' * 2048)"),
            timeout_seconds=2,
            max_output_bytes=128,
            cancellation_requested=None,
            environment=os.environ,
        )


@pytest.mark.parametrize("stream_name", ("stdout", "stderr"))
def test_subprocess_runner_terminates_live_process_and_closes_pipes_on_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream_name: str,
) -> None:
    runner = SubprocessRunner()
    marker = tmp_path / f"{stream_name}-natural-completion"
    processes: list[subprocess.Popen[bytes]] = []
    terminated_process_ids: list[int] = []
    terminate_process = llama_cpp_module._terminate_process

    def record_process(
        command: Sequence[str], *, environment: Mapping[str, str]
    ) -> subprocess.Popen[bytes]:
        process = SubprocessRunner._start_process(
            command,
            environment=environment,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(runner, "_start_process", record_process)

    def record_termination(process: subprocess.Popen[bytes]) -> None:
        terminated_process_ids.append(process.pid)
        terminate_process(process)

    monkeypatch.setattr(llama_cpp_module, "_terminate_process", record_termination)
    write_output = (
        f"sys.{stream_name}.buffer.write(b'x' * 4096);sys.{stream_name}.flush();"
    )
    script = (
        "import pathlib,sys,time;"
        f"{write_output}"
        "time.sleep(5);"
        f"pathlib.Path({str(marker)!r}).write_text('finished')"
    )

    with pytest.raises(LlamaCppOutputLimitError, match="capture limit"):
        runner.run(
            (sys.executable, "-c", script),
            timeout_seconds=3,
            max_output_bytes=128,
            cancellation_requested=None,
            environment=os.environ,
        )

    assert len(processes) == 1
    process = processes[0]
    assert terminated_process_ids == [process.pid]
    assert process.poll() is not None
    assert process.returncode != 0
    assert process.stdout is not None
    assert process.stderr is not None
    assert process.stdout.closed
    assert process.stderr.closed
    assert not marker.exists()
