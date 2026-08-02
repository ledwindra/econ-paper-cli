"""Application service entrypoints for the CLI commands."""

import sys
from argparse import Namespace
from pathlib import Path

from econ_paper_cli.services.single_paper_analysis_cli import (
    AnalyzeCommandOptions,
    CLIExitCode,
    run_single_paper_analysis_command,
)


def run_setup(args: Namespace | None = None) -> int:
    """Describe the setup placeholder without performing side effects."""
    sys.stdout.write("Setup is not implemented yet. No artifacts were downloaded.\n")
    return 0


def run_status(args: Namespace | None = None) -> int:
    """Describe the status placeholder."""
    sys.stdout.write("Status checks are not implemented yet.\n")
    return 0


def run_chat(args: Namespace | None = None) -> int:
    """Describe the chat placeholder."""
    sys.stdout.write("Chat is not implemented yet.\n")
    return 0


def run_update(args: Namespace | None = None) -> int:
    """Describe the update placeholder without performing side effects."""
    sys.stdout.write("Updates are not implemented yet. No network request was made.\n")
    return 0


def run_analyze(args: Namespace) -> int:
    """Parse options and run PDF research-question analysis."""
    try:
        target_path = Path(args.target_path)
        executable_path = Path(args.llama_cpp_path)
        model_path = Path(args.model_path)
        model_id = str(args.model_id)
        model_bytes = int(args.model_bytes)
        model_checksum = str(args.model_checksum)
        threads = int(args.threads) if args.threads is not None else None
        timeout = float(args.timeout) if args.timeout is not None else None
        db_path = Path(args.db_path) if args.db_path is not None else None
    except (AttributeError, ValueError, TypeError) as err:
        sys.stderr.write(f"Invalid CLI argument values: {err}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

    options = AnalyzeCommandOptions(
        target_path=target_path,
        executable_path=executable_path,
        model_path=model_path,
        model_id=model_id,
        model_bytes=model_bytes,
        model_checksum=model_checksum,
        threads=threads,
        timeout=timeout,
        db_path=db_path,
        quality_policy_version=getattr(args, "quality_policy_version", None),
        section_policy_version=getattr(args, "section_policy_version", None),
        research_question_policy_version=getattr(
            args, "research_question_policy_version", None
        ),
        single_paper_policy_version=getattr(args, "single_paper_policy_version", None),
        conversion_policy_version=getattr(args, "conversion_policy_version", None),
        max_passage_characters=getattr(args, "max_passage_characters", 1200),
    )

    return run_single_paper_analysis_command(options)
