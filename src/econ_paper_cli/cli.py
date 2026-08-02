import sys
from argparse import ArgumentParser
from collections.abc import Callable, Sequence
from pathlib import Path

from econ_paper_cli.services import commands

CommandHandler = Callable[..., int | str]


def build_parser() -> ArgumentParser:
    """Build the command-line parser with analyze subparser and options."""
    parser = ArgumentParser(
        prog="econpapers",
        description="Local-first conversational literature search for economists.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    command_definitions: tuple[tuple[str, str, CommandHandler], ...] = (
        ("update", "Update local artifacts (placeholder).", commands.run_update),
    )
    for name, help_text, handler in command_definitions:
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.set_defaults(handler=handler)

    setup_parser = subparsers.add_parser(
        "setup",
        help="Validate and durably persist local runtime/model configuration.",
    )
    _add_runtime_model_arguments(setup_parser, required=True)
    setup_parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional local configuration file path override.",
    )
    setup_parser.set_defaults(handler=commands.run_setup)

    status_parser = subparsers.add_parser(
        "status",
        help="Report local configuration, runtime readiness, and library state.",
    )
    status_parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional SQLite database path override.",
    )
    status_parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional local configuration file path override.",
    )
    status_parser.set_defaults(handler=commands.run_status)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a local PDF file or directory of PDFs and persist evidence-backed results.",
    )
    analyze_parser.add_argument(
        "target_path",
        metavar="TARGET_PATH",
        type=Path,
        help="Path to a local PDF file or a directory containing PDF files.",
    )
    _add_runtime_model_arguments(analyze_parser, required=False)
    analyze_parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional SQLite database path override.",
    )
    analyze_parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional local configuration file path override.",
    )
    analyze_parser.add_argument(
        "--quality-policy-version",
        type=str,
        default=None,
        help="Optional PDF quality assessment policy version override.",
    )
    analyze_parser.add_argument(
        "--section-policy-version",
        type=str,
        default=None,
        help="Optional section detection policy version override.",
    )
    analyze_parser.add_argument(
        "--research-question-policy-version",
        type=str,
        default=None,
        help="Optional research question extraction policy version override.",
    )
    analyze_parser.add_argument(
        "--single-paper-policy-version",
        type=str,
        default=None,
        help="Optional single paper analysis policy version override.",
    )
    analyze_parser.add_argument(
        "--conversion-policy-version",
        type=str,
        default=None,
        help="Optional early-section conversion policy version override.",
    )
    analyze_parser.add_argument(
        "--max-passage-characters",
        type=int,
        default=1200,
        help="Maximum characters per stored early-section passage (default: 1200).",
    )
    analyze_parser.set_defaults(handler=commands.run_analyze)

    chat_parser = subparsers.add_parser(
        "chat", help="Answer one question from the local library."
    )
    chat_parser.add_argument(
        "question",
        metavar="QUESTION",
        type=str,
        help="One question to answer from the stored local corpus.",
    )
    _add_runtime_model_arguments(chat_parser, required=False)
    chat_parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional SQLite database path override.",
    )
    chat_parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional local configuration file path override.",
    )
    chat_parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Maximum number of evidence passages to retrieve (default: 10).",
    )
    chat_parser.set_defaults(handler=commands.run_chat)

    return parser


def _add_runtime_model_arguments(parser: ArgumentParser, *, required: bool) -> None:
    """Add the shared runtime/model identity and option flags to a subparser.

    When ``required`` is False (``analyze``/``chat``), omitting all five
    identity flags falls back to durable configuration written by
    ``econpapers setup``; supplying any of them requires supplying all five
    together for one coherent, explicitly verified identity.
    """
    identity_help_suffix = (
        "" if required else " (falls back to durable `econpapers setup` configuration)"
    )
    parser.add_argument(
        "--llama-cpp-path",
        "--executable-path",
        dest="llama_cpp_path",
        required=required,
        default=None,
        type=Path,
        help=f"Path to the local llama.cpp executable.{identity_help_suffix}",
    )
    parser.add_argument(
        "--model-path",
        required=required,
        default=None,
        type=Path,
        help=f"Path to the local GGUF model file.{identity_help_suffix}",
    )
    parser.add_argument(
        "--model-id",
        required=required,
        default=None,
        type=str,
        help=f"Identifier for the model.{identity_help_suffix}",
    )
    parser.add_argument(
        "--model-bytes",
        "--expected-model-size-bytes",
        dest="model_bytes",
        required=required,
        default=None,
        type=int,
        help=f"Expected size of the model file in bytes.{identity_help_suffix}",
    )
    parser.add_argument(
        "--model-checksum",
        "--expected-model-sha256",
        dest="model_checksum",
        required=required,
        default=None,
        type=str,
        help=f"Expected SHA-256 checksum of the model file.{identity_help_suffix}",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Optional thread count for llama.cpp execution.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout in seconds for generation.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    try:
        parser = build_parser()
        arguments = parser.parse_args(argv)
        handler: CommandHandler | None = getattr(arguments, "handler", None)

        if handler is None:
            parser.print_help()
            return 0

        code = handler(arguments)
        return code if isinstance(code, int) else 0
    except SystemExit:
        raise
    except Exception as err:
        sys.stderr.write(f"Unexpected internal CLI error: {err}\n")
        return 3
