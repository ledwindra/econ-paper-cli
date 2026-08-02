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
        ("setup", "Prepare local artifacts (placeholder).", commands.run_setup),
        ("status", "Report local readiness (placeholder).", commands.run_status),
        ("chat", "Start a literature conversation (placeholder).", commands.run_chat),
        ("update", "Update local artifacts (placeholder).", commands.run_update),
    )
    for name, help_text, handler in command_definitions:
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.set_defaults(handler=handler)

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
    analyze_parser.add_argument(
        "--llama-cpp-path",
        "--executable-path",
        dest="llama_cpp_path",
        required=True,
        type=Path,
        help="Path to the local llama.cpp executable.",
    )
    analyze_parser.add_argument(
        "--model-path",
        required=True,
        type=Path,
        help="Path to the local GGUF model file.",
    )
    analyze_parser.add_argument(
        "--model-id",
        required=True,
        type=str,
        help="Identifier for the model.",
    )
    analyze_parser.add_argument(
        "--model-bytes",
        "--expected-model-size-bytes",
        dest="model_bytes",
        required=True,
        type=int,
        help="Expected size of the model file in bytes.",
    )
    analyze_parser.add_argument(
        "--model-checksum",
        "--expected-model-sha256",
        dest="model_checksum",
        required=True,
        type=str,
        help="Expected SHA-256 checksum of the model file.",
    )
    analyze_parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Optional thread count for llama.cpp execution.",
    )
    analyze_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout in seconds for generation.",
    )
    analyze_parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional SQLite database path override.",
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

    return parser


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
