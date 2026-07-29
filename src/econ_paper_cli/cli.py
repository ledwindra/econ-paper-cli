"""Command-line interface for the project scaffold."""

from argparse import ArgumentParser
from collections.abc import Callable, Sequence

from econ_paper_cli.services import commands

CommandHandler = Callable[[], str]


def build_parser() -> ArgumentParser:
    """Build the command-line parser."""
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    handler: CommandHandler | None = getattr(arguments, "handler", None)

    if handler is None:
        parser.print_help()
        return 0

    print(handler())
    return 0
