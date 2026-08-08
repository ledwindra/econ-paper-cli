"""Model-free tests for the concrete llama.cpp generation adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
    ids = ["e1"] if citation_ids is None else citation_ids
    return json.dumps(
        {
            "claims": [
                {
                    "text": "The synthetic estimate was 4 units.",
                    "citation_ids": ids,
                }
            ],
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
        self.grammar_text: str | None = None
        self.prompt_mode: int | None = None
        self.grammar_mode: int | None = None
        self.prompt_path: Path | None = None
        self.grammar_path: Path | None = None

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
        grammar_path = Path(recorded[recorded.index("--grammar-file") + 1])
        self.prompt_path = prompt_path
        self.grammar_path = grammar_path
        self.prompt_text = prompt_path.read_text(encoding="utf-8")
        self.grammar_text = grammar_path.read_text(encoding="utf-8")
        self.prompt_mode = prompt_path.stat().st_mode & 0o777
        self.grammar_mode = grammar_path.stat().st_mode & 0o777
        return ProcessResult(self.generation_returncode, self.output, "private stderr")


class RaisingRunner:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.commands: list[tuple[str, ...]] = []
        self.prompt_path: Path | None = None
        self.grammar_path: Path | None = None

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
            self.grammar_path = Path(recorded[recorded.index("--grammar-file") + 1])
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
    assert "--grammar-file" in command
    assert "--json-schema-file" not in command
    assert "--offline" in command
    assert "--no-display-prompt" in command
    assert "--log-file" in command
    assert command[command.index("--log-file") + 1] == os.devnull
    assert "--log-disable" not in command
    assert "--single-turn" not in command
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
def test_prompt_and_grammar_are_private_and_cleaned_after_success(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    generator.generate(make_request())

    assert runner.prompt_mode == 0o600
    assert runner.grammar_mode == 0o600
    assert runner.prompt_path is not None
    assert runner.grammar_path is not None
    assert not runner.prompt_path.exists()
    assert not runner.grammar_path.exists()


def test_temporary_files_are_cleaned_when_process_raises(tmp_path: Path) -> None:
    runner = RaisingRunner(LlamaCppCancelledError("cancelled"))
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    with pytest.raises(LlamaCppCancelledError, match="cancelled"):
        generator.generate(make_request())

    assert runner.prompt_path is not None
    assert runner.grammar_path is not None
    assert not runner.prompt_path.exists()
    assert not runner.grammar_path.exists()


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
    assert runner.grammar_path is not None
    assert not runner.prompt_path.exists()
    assert not runner.grammar_path.exists()


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


def test_generation_method_includes_grammar_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    generator = LlamaCppGenerator(config, process_runner=RecordingRunner())

    method = generator.generation_method
    monkeypatch.setattr(llama_cpp_module, "OUTPUT_GRAMMAR_SHA256", "0" * 64)

    assert str(tmp_path) not in method
    assert config.model_sha256 in method
    assert llama_cpp_module.PROMPT_VERSION in method
    assert generator.generation_method != method


def test_packaged_grammar_matches_frozen_fingerprint() -> None:
    grammar = (
        Path(llama_cpp_module.__file__).with_name("resources")
        / f"{llama_cpp_module.PROMPT_VERSION}.gbnf"
    ).read_bytes()

    assert hashlib.sha256(grammar).hexdigest() == llama_cpp_module.OUTPUT_GRAMMAR_SHA256


def test_exact_llama_completion_footer_is_removed_before_parsing(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(successful_output() + "\n> EOF by user\n\n\n")
    generator = LlamaCppGenerator(make_config(tmp_path), process_runner=runner)

    response = generator.generate(make_request())

    assert response.answer_text == "The synthetic estimate was 4 units."


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
                    "claims": [{"text": "Answer.", "citation_ids": ["e1"]}],
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
            "claims": [{"text": "I cannot answer.", "citation_ids": ["e1"]}],
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


def test_claims_citing_out_of_rank_order_are_normalized_not_rejected(
    tmp_path: Path,
) -> None:
    """Under v3 the response's citation list is derived from the claims, so a
    model naming a later-ranked passage first must yield rank-ascending
    citations rather than losing the whole answer to a contract violation.
    The per-claim attribution keeps the model's own ordering."""
    request = _two_evidence_request()
    output = json.dumps(
        {
            "claims": [
                {"text": "A second finding.", "citation_ids": ["e2"]},
                {"text": "The synthetic estimate was 4 units.", "citation_ids": ["e1"]},
            ],
            "abstained": False,
            "abstention_reason": None,
            "finding_kinds": ["descriptive"],
        }
    )
    generator = LlamaCppGenerator(
        make_config(tmp_path), process_runner=RecordingRunner(output)
    )

    response = generator.generate(request)

    assert [item.citation_id for item in response.citations] == ["e1", "e2"]
    assert [claim.citation_ids for claim in response.claims] == [("e2",), ("e1",)]


def test_a_single_claim_may_not_cite_an_identifier_outside_the_evidence(
    tmp_path: Path,
) -> None:
    """A claim citing an unknown identifier is rejected even when the response's
    other claims cite only real evidence, so one bad claim cannot smuggle an
    unresolvable citation past the derived citation list."""
    request = _two_evidence_request()
    output = json.dumps(
        {
            "claims": [
                {"text": "A grounded finding.", "citation_ids": ["e1"]},
                {"text": "An invented finding.", "citation_ids": ["e99"]},
            ],
            "abstained": False,
            "abstention_reason": None,
            "finding_kinds": ["descriptive"],
        }
    )
    generator = LlamaCppGenerator(
        make_config(tmp_path), process_runner=RecordingRunner(output)
    )

    with pytest.raises(LlamaCppOutputError, match="unknown evidence citation"):
        generator.generate(request)


def _two_evidence_request() -> GenerationRequest:
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
    return GenerationRequest(
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


def test_empty_evidence_can_produce_contract_valid_abstention(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "claims": [],
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


def test_launching_a_vanished_executable_raises_a_typed_process_error(
    tmp_path: Path,
) -> None:
    """The first half of the mid-session runtime-repair chain.

    If the executable disappears between readiness and launch — which is what
    a concurrent ``update`` repairing the runtime directory does — ``Popen``
    raises ``OSError`` and the adapter converts it to ``LlamaCppProcessError``.
    The shell then maps *that* to ``INTERNAL_FAILURE``; see
    ``tests/services/test_release_readiness.py`` for the second half.
    """
    missing = tmp_path / "runtime" / "llama-completion"

    with pytest.raises(LlamaCppProcessError) as caught:
        SubprocessRunner._start_process([str(missing)], environment={})

    assert "Unable to start" in str(caught.value)


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


# --- Runtime error surfacing and context sizing ---------------------------


def test_runtime_error_detail_surfaces_tagged_error_line() -> None:
    from econ_paper_cli.adapters.llama_cpp import _runtime_error_detail

    stderr = (
        "0.00.533.533 I system_info: n_threads = 6\n"
        "0.00.545.833 E llama_completion: prompt is too long "
        "(4859 tokens, max 4092)\n"
    )
    assert _runtime_error_detail(stderr) == (
        "prompt is too long (4859 tokens, max 4092)"
    )


def test_runtime_error_detail_ignores_untagged_output() -> None:
    """Only lines the runtime explicitly tagged as errors are surfaced —
    arbitrary stderr can echo prompt-derived material and must never leak
    into a user-facing message."""
    from econ_paper_cli.adapters.llama_cpp import _runtime_error_detail

    assert _runtime_error_detail("") == ""
    assert _runtime_error_detail("0.00.1 I loading model\nplain prose line\n") == ""


def test_runtime_error_detail_is_length_capped() -> None:
    from econ_paper_cli.adapters.llama_cpp import (
        _MAX_RUNTIME_ERROR_DETAIL_CHARS,
        _runtime_error_detail,
    )

    stderr = "0.1 E llama_completion: " + ("x" * 5000)
    assert len(_runtime_error_detail(stderr)) == _MAX_RUNTIME_ERROR_DETAIL_CHARS


def test_default_context_size_admits_typical_real_paper_sections() -> None:
    """Regression for the empirical finding that a 4096 window rejected
    roughly half a real 248-paper economics library, making research-question
    extraction fail on essentially the whole corpus."""
    import dataclasses

    from econ_paper_cli.adapters.llama_cpp import LlamaCppConfig

    defaults = {
        field.name: field.default for field in dataclasses.fields(LlamaCppConfig)
    }
    assert defaults["context_size"] >= 16384
    assert defaults["max_output_tokens"] < defaults["context_size"]


# --- Grammar/contract alignment -------------------------------------------
#
# The GBNF grammar is the only thing standing between a small local model and
# an output the domain contract will reject. Anything the contract forbids
# must be *unrepresentable*, not merely discouraged by the prompt.


def _packaged_grammar_text() -> str:
    return (
        Path(llama_cpp_module.__file__).with_name("resources")
        / f"{llama_cpp_module.PROMPT_VERSION}.gbnf"
    ).read_text(encoding="utf-8")


def test_grammar_binds_abstention_state_to_its_dependent_fields() -> None:
    """A non-abstaining answer carrying an abstention_reason (or an
    abstaining one carrying citations) must not be expressible at all."""
    grammar = _packaged_grammar_text()

    assert "answering-root" in grammar and "abstaining-root" in grammar
    answering = next(
        line for line in grammar.splitlines() if line.startswith("answering-root ::=")
    )
    abstaining = next(
        line for line in grammar.splitlines() if line.startswith("abstaining-root ::=")
    )
    # Answering: abstained false, reason null, at least one claim.
    assert '"false"' in answering and '"null"' in answering
    assert "claims-nonempty" in answering
    # Abstaining: abstained true, the one legal reason, and no claims
    # or finding kinds.
    assert '"true"' in abstaining
    assert "insufficient_evidence" in abstaining
    assert "claims-empty" in abstaining
    assert "finding-kinds-empty" in abstaining


def test_grammar_cannot_express_duplicate_or_runaway_finding_kinds() -> None:
    """Regression: an unbounded repeat rule let a local model loop on
    `"causal", "causal", ...` until the output truncated mid-object, which
    surfaced as "Local model returned invalid JSON" on real papers."""
    grammar = _packaged_grammar_text()
    finding_rule = next(
        line for line in grammar.splitlines() if line.startswith("finding-kinds ::=")
    )

    # No unbounded repetition operators in the finding-kinds rule.
    assert "*" not in finding_rule
    assert "+" not in finding_rule
    # Each legal value appears at most once per alternative.
    for alternative in finding_rule.split("::=", 1)[1].split("|"):
        assert alternative.count('\\"descriptive\\"') <= 1
        assert alternative.count('\\"causal\\"') <= 1


@pytest.mark.parametrize("rule_name", ("citation-ids", "claims-nonempty"))
def test_grammar_bounds_repeated_list_rules(rule_name: str) -> None:
    """Citation ids and claims are unbounded in principle, so both lists are
    length-capped rather than left open — an unbounded repeat is a
    runaway-loop hazard that truncates output mid-object."""
    grammar = _packaged_grammar_text()
    rule = next(
        line for line in grammar.splitlines() if line.startswith(f"{rule_name} ::=")
    )

    assert "*" not in rule
    assert re.search(r"\{0,\d+\}", rule) is not None
