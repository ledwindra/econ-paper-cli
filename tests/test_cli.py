from importlib import metadata
from pathlib import Path

import pytest

from econ_paper_cli.cli import build_parser, main


@pytest.mark.parametrize(
    ("command", "expected_output"),
    [
        ("setup", "Setup is not implemented yet. No artifacts were downloaded."),
        ("status", "Status checks are not implemented yet."),
        ("chat", "Chat is not implemented yet."),
        ("update", "Updates are not implemented yet. No network request was made."),
    ],
)
def test_placeholder_commands_succeed(
    command: str,
    expected_output: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([command]) == 0
    assert capsys.readouterr().out.strip() == expected_output


def test_help_lists_available_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for command in ("setup", "status", "chat", "update", "analyze"):
        assert command in output


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


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: econpapers" in capsys.readouterr().out


def test_unknown_command_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["unknown"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_distribution_exposes_econpapers_entry_point() -> None:
    entry_points = metadata.entry_points(group="console_scripts")
    econpapers = [
        entry_point for entry_point in entry_points if entry_point.name == "econpapers"
    ]

    assert len(econpapers) == 1
    assert econpapers[0].value == "econ_paper_cli.cli:main"
