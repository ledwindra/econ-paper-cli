import io
from importlib import metadata
from pathlib import Path

import pytest

from econ_paper_cli.cli import build_parser, main
from econ_paper_cli.domain.model_manifest import MANAGED_MODEL_CATALOG
from econ_paper_cli.services import commands
from econ_paper_cli.services.interactive_shell import ShellExitCode
from econ_paper_cli.services.setup_command import (
    SetupCommandOptions,
    SetupOptionsError,
)


def test_update_command_dispatches_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_update(args: object) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(commands, "run_update", fake_run_update)
    assert main(["update", "--offline", "--config-path", "/custom/config.json"]) == 0
    assert getattr(captured["args"], "offline") is True
    assert getattr(captured["args"], "config_path") == Path("/custom/config.json")


def test_status_command_dispatches_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_status(args: object) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(commands, "run_status", fake_run_status)
    assert main(["status"]) == 0
    assert "args" in captured


def test_status_command_real_handler_does_not_raise(tmp_path: Path) -> None:
    """Regression test: the real ``commands.run_status`` (not monkeypatched)
    must handle a genuine parsed argparse Namespace without raising. This
    caught a real bug where ``StatusCommandOptions`` lost its ``@dataclass``
    decorator during an unrelated refactor -- every dispatch test above
    replaces ``commands.run_status`` with a fake, so none of them exercise
    the real function's call into ``StatusCommandOptions(db_path=...)``."""
    args = build_parser().parse_args(
        [
            "status",
            "--db-path",
            str(tmp_path / "library.db"),
            "--config-path",
            str(tmp_path / "config.json"),
        ]
    )
    exit_code = commands.run_status(args)
    assert isinstance(exit_code, int)


def test_setup_command_dispatches_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_setup(args: object) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(commands, "run_setup", fake_run_setup)
    assert (
        main(
            [
                "setup",
                "--llama-cpp-path",
                "llama-completion",
                "--model-path",
                "model.gguf",
                "--model-id",
                "local-model",
                "--model-bytes",
                "100",
                "--model-checksum",
                "a" * 64,
            ]
        )
        == 0
    )
    assert getattr(captured["args"], "model_id") == "local-model"


def test_help_lists_available_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for command in ("setup", "status", "chat", "update", "analyze"):
        assert command in output


def test_chat_help_lists_all_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["chat", "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "QUESTION",
        "--llama-cpp-path",
        "--model-path",
        "--model-id",
        "--model-bytes",
        "--model-checksum",
        "--threads",
        "--timeout",
        "--db-path",
        "--top-k",
        "--show-evidence",
    ):
        assert flag in output


def test_analyze_help_lists_all_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["analyze", "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "TARGET_PATH",
        "--llama-cpp-path",
        "--model-path",
        "--model-id",
        "--model-bytes",
        "--model-checksum",
        "--threads",
        "--timeout",
        "--db-path",
        "--conversion-policy-version",
        "--max-passage-characters",
    ):
        assert flag in output
    assert "PDF file or a directory" in output


@pytest.mark.parametrize("target", ["paper.pdf", "papers"])
def test_analyze_parser_accepts_file_or_directory_target(target: str) -> None:
    arguments = build_parser().parse_args(
        [
            "analyze",
            target,
            "--llama-cpp-path",
            "llama-completion",
            "--model-path",
            "model.gguf",
            "--model-id",
            "local-model",
            "--model-bytes",
            "100",
            "--model-checksum",
            "a" * 64,
        ]
    )

    assert arguments.target_path == Path(target)
    assert arguments.max_passage_characters == 1200


def test_chat_parser_accepts_question_and_configuration() -> None:
    arguments = build_parser().parse_args(
        [
            "chat",
            "What is the research question?",
            "--llama-cpp-path",
            "llama-completion",
            "--model-path",
            "model.gguf",
            "--model-id",
            "local-model",
            "--model-bytes",
            "100",
            "--model-checksum",
            "a" * 64,
            "--top-k",
            "3",
        ]
    )

    assert arguments.question == "What is the research question?"
    assert arguments.top_k == 3
    assert arguments.show_evidence is False


def test_chat_parser_show_evidence_flag() -> None:
    arguments = build_parser().parse_args(
        ["chat", "What is the research question?", "--show-evidence"]
    )

    assert arguments.show_evidence is True


def test_chat_command_dispatches_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_chat(args: object) -> int:
        captured["args"] = args
        return 7

    monkeypatch.setattr(commands, "run_chat", fake_run_chat)
    assert (
        main(
            [
                "chat",
                "What is the research question?",
                "--llama-cpp-path",
                "llama-completion",
                "--model-path",
                "model.gguf",
                "--model-id",
                "local-model",
                "--model-bytes",
                "100",
                "--model-checksum",
                "a" * 64,
            ]
        )
        == 7
    )
    assert getattr(captured["args"], "question") == "What is the research question?"


def test_analyze_parser_accepts_conversion_settings() -> None:
    arguments = build_parser().parse_args(
        [
            "analyze",
            "paper.pdf",
            "--llama-cpp-path",
            "llama-completion",
            "--model-path",
            "model.gguf",
            "--model-id",
            "local-model",
            "--model-bytes",
            "100",
            "--model-checksum",
            "a" * 64,
            "--conversion-policy-version",
            "early-section-markdown-v1",
            "--max-passage-characters",
            "800",
        ]
    )

    assert arguments.conversion_policy_version == "early-section-markdown-v1"
    assert arguments.max_passage_characters == 800


def test_analyze_missing_required_arguments_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["analyze"])

    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "the following arguments are required" in err


def test_analyze_parser_accepts_no_runtime_model_arguments() -> None:
    arguments = build_parser().parse_args(["analyze", "paper.pdf"])

    assert arguments.target_path == Path("paper.pdf")
    assert arguments.llama_cpp_path is None
    assert arguments.model_path is None
    assert arguments.model_id is None
    assert arguments.model_bytes is None
    assert arguments.model_checksum is None


def test_chat_parser_accepts_no_runtime_model_arguments() -> None:
    arguments = build_parser().parse_args(["chat", "What is the effect?"])

    assert arguments.question == "What is the effect?"
    assert arguments.llama_cpp_path is None
    assert arguments.model_path is None
    assert arguments.model_id is None
    assert arguments.model_bytes is None
    assert arguments.model_checksum is None


def test_bare_setup_parses_and_leaves_every_identity_flag_unset() -> None:
    """A fresh user runs `econpapers setup` with no flags at all: argparse must
    accept it and leave both runtime and model identity unset, so the command
    layer can auto-provision each. Requiring any flag here would restore the
    manual-GGUF onboarding wall this replaced."""
    arguments = build_parser().parse_args(["setup"])

    assert arguments.llama_cpp_path is None
    assert arguments.model_path is None
    assert arguments.model_id is None
    assert arguments.model_bytes is None
    assert arguments.model_checksum is None
    assert arguments.managed_model_id is None


def test_setup_model_choice_is_restricted_to_the_pinned_catalog(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--model names a pinned artifact, so an unknown id must fail at parse
    time rather than reaching the network."""
    default_id = MANAGED_MODEL_CATALOG.default_model_id
    accepted = build_parser().parse_args(["setup", "--model", default_id])
    assert accepted.managed_model_id == default_id

    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["setup", "--model", "not-a-real-model"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_setup_help_lists_all_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["setup", "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--llama-cpp-path",
        "--model-path",
        "--model-id",
        "--model-bytes",
        "--model-checksum",
        "--threads",
        "--timeout",
        "--db-path",
        "--config-path",
    ):
        assert flag in output


def test_setup_parser_accepts_db_path() -> None:
    arguments = build_parser().parse_args(
        [
            "setup",
            "--llama-cpp-path",
            "llama-completion",
            "--model-path",
            "model.gguf",
            "--model-id",
            "local-model",
            "--model-bytes",
            "100",
            "--model-checksum",
            "a" * 64,
            "--db-path",
            "/custom/econpapers.db",
        ]
    )

    assert arguments.db_path == Path("/custom/econpapers.db")


def test_setup_command_maps_db_path_into_setup_command_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full parser-to-service path: --db-path must reach
    SetupCommandOptions.db_path, not just the raw argparse namespace."""
    captured: dict[str, object] = {}

    def fake_run_setup_command(options: object, **kwargs: object) -> int:
        captured["options"] = options
        return 0

    monkeypatch.setattr(commands, "run_setup_command", fake_run_setup_command)
    assert (
        main(
            [
                "setup",
                "--llama-cpp-path",
                "llama-completion",
                "--model-path",
                "model.gguf",
                "--model-id",
                "local-model",
                "--model-bytes",
                "100",
                "--model-checksum",
                "a" * 64,
                "--db-path",
                "/custom/econpapers.db",
            ]
        )
        == 0
    )
    assert getattr(captured["options"], "db_path") == Path("/custom/econpapers.db")


def test_setup_parser_allows_omitting_llama_cpp_path() -> None:
    """Issue #58: --llama-cpp-path becomes optional (triggers managed
    provisioning); the other four model-identity flags stay required."""
    arguments = build_parser().parse_args(
        [
            "setup",
            "--model-path",
            "model.gguf",
            "--model-id",
            "local-model",
            "--model-bytes",
            "100",
            "--model-checksum",
            "a" * 64,
        ]
    )
    assert arguments.llama_cpp_path is None


def test_partial_model_identity_is_rejected_by_the_options_contract() -> None:
    """The four model-identity flags describe one artifact. A subset is now
    accepted by argparse (so that omitting all four can mean "provision one"),
    which moves the all-or-nothing rule to the options layer -- it must still
    be enforced there, or a user could verify a downloaded model against a
    checksum meant for a different file."""
    arguments = build_parser().parse_args(["setup", "--model-path", "model.gguf"])
    assert arguments.model_path == Path("model.gguf")

    with pytest.raises(SetupOptionsError, match="together"):
        SetupCommandOptions(executable_path=None, model_path=Path("model.gguf"))


def test_explicit_model_path_cannot_be_combined_with_a_catalog_choice() -> None:
    """--model selects what to provision; an explicit path means nothing will
    be provisioned. Accepting both would silently ignore one of them."""
    with pytest.raises(SetupOptionsError, match="cannot be combined"):
        SetupCommandOptions(
            executable_path=None,
            model_path=Path("model.gguf"),
            model_id="local-model",
            model_bytes=100,
            model_checksum="a" * 64,
            managed_model_id=MANAGED_MODEL_CATALOG.default_model_id,
        )


def test_setup_help_lists_offline_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["setup", "--help"])

    assert exit_info.value.code == 0
    assert "--offline" in capsys.readouterr().out


def _collapse_whitespace(text: str) -> str:
    """Undo argparse's help-text line wrapping for substring assertions."""
    return " ".join(text.split())


def test_setup_help_mentions_auto_provisioning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["setup", "--help"])

    assert exit_info.value.code == 0
    out = _collapse_whitespace(capsys.readouterr().out)
    assert "provision a managed llama.cpp runtime" in out


def test_analyze_help_never_claims_auto_provisioning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Issue #58 review: analyze/chat only fall back to durable
    configuration and must remain network-free; their help text must never
    claim omitting --llama-cpp-path triggers a download."""
    with pytest.raises(SystemExit) as exit_info:
        main(["analyze", "--help"])

    assert exit_info.value.code == 0
    out = _collapse_whitespace(capsys.readouterr().out)
    assert "auto-provision" not in out
    assert "provision" not in out


def test_chat_help_never_claims_auto_provisioning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["chat", "--help"])

    assert exit_info.value.code == 0
    out = _collapse_whitespace(capsys.readouterr().out)
    assert "auto-provision" not in out
    assert "provision" not in out


def test_setup_command_maps_offline_flag_into_setup_command_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_setup_command(options: object, **kwargs: object) -> int:
        captured["options"] = options
        return 0

    monkeypatch.setattr(commands, "run_setup_command", fake_run_setup_command)
    assert (
        main(
            [
                "setup",
                "--model-path",
                "model.gguf",
                "--model-id",
                "local-model",
                "--model-bytes",
                "100",
                "--model-checksum",
                "a" * 64,
                "--offline",
            ]
        )
        == 0
    )
    assert getattr(captured["options"], "offline") is True
    assert getattr(captured["options"], "executable_path") is None


def test_status_help_lists_all_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["status", "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for flag in ("--db-path", "--config-path"):
        assert flag in output


def test_no_command_dispatches_to_shell_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_shell(args: object) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(commands, "run_shell", fake_run_shell)
    assert main([]) == 0
    assert "args" in captured


def test_no_command_enters_shell_and_reports_missing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: bare invocation with an isolated, empty environment opens
    the shell and exits with a typed failure for a missing database, rather
    than hanging on real stdin or touching a real default database."""
    monkeypatch.setenv("ECONPAPERS_LIBRARY_DIR", str(tmp_path / "lib"))
    monkeypatch.setenv("ECONPAPERS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main([]) == ShellExitCode.TYPED_FAILURE_OR_CONFIG_ERROR
    assert capsys.readouterr().err.strip() != ""


def test_unknown_command_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["unknown"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_chat_command_maps_show_evidence_flag_into_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from econ_paper_cli.services.chat_command import ChatCommandOptions

    captured: dict[str, object] = {}

    def fake_run_chat_command(options: ChatCommandOptions, **_kwargs: object) -> int:
        captured["options"] = options
        return 0

    monkeypatch.setattr(commands, "run_chat_command", fake_run_chat_command)
    assert main(["chat", "a question", "--show-evidence"]) == 0

    options = captured["options"]
    assert isinstance(options, ChatCommandOptions)
    assert options.show_evidence is True


def test_chat_command_defaults_show_evidence_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from econ_paper_cli.services.chat_command import ChatCommandOptions

    captured: dict[str, object] = {}

    def fake_run_chat_command(options: ChatCommandOptions, **_kwargs: object) -> int:
        captured["options"] = options
        return 0

    monkeypatch.setattr(commands, "run_chat_command", fake_run_chat_command)
    assert main(["chat", "a question"]) == 0

    options = captured["options"]
    assert isinstance(options, ChatCommandOptions)
    assert options.show_evidence is False


def test_distribution_exposes_econpapers_entry_point() -> None:
    entry_points = metadata.entry_points(group="console_scripts")
    econpapers = [
        entry_point for entry_point in entry_points if entry_point.name == "econpapers"
    ]

    assert len(econpapers) == 1
    assert econpapers[0].value == "econ_paper_cli.cli:main"
