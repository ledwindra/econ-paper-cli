"""Application service entrypoints for the CLI commands."""

import sys
from argparse import Namespace
from pathlib import Path

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.services.chat_command import (
    ChatCommandOptions,
    run_chat_command,
)
from econ_paper_cli.services.interactive_shell import (
    ShellCommandOptions,
    run_interactive_shell,
)
from econ_paper_cli.services.setup_command import (
    SetupCommandOptions,
    run_setup_command,
)
from econ_paper_cli.services.single_paper_analysis_cli import (
    AnalyzeCommandOptions,
    CLIExitCode,
    run_single_paper_analysis_command,
)
from econ_paper_cli.services.status_command import (
    StatusCommandOptions,
    run_status_command,
)
from econ_paper_cli.services.update_command import (
    UpdateCommandOptions,
    run_update_command,
)


def _optional_path(value: object) -> Path | None:
    return Path(value) if value is not None else None


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def run_setup(args: Namespace | None = None) -> int:
    """Validate and durably persist local runtime/model configuration."""
    try:
        options = SetupCommandOptions(
            executable_path=_optional_path(args.llama_cpp_path),
            model_path=_optional_path(getattr(args, "model_path", None)),
            model_id=_optional_str(getattr(args, "model_id", None)),
            model_bytes=_optional_int(getattr(args, "model_bytes", None)),
            model_checksum=_optional_str(getattr(args, "model_checksum", None)),
            threads=_optional_int(getattr(args, "threads", None)),
            timeout=_optional_float(getattr(args, "timeout", None)),
            db_path=_optional_path(getattr(args, "db_path", None)),
            offline=bool(getattr(args, "offline", False)),
            managed_model_id=_optional_str(getattr(args, "managed_model_id", None)),
        )
    except (AttributeError, ValueError, TypeError) as err:
        sys.stderr.write(f"Invalid CLI argument values: {err}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

    config_path = _optional_path(getattr(args, "config_path", None))
    config_backend = JSONConfigStorage(config_path) if config_path is not None else None

    return run_setup_command(options, config_backend=config_backend)


def run_status(args: Namespace | None = None) -> int:
    """Report local configuration, runtime readiness, and library state."""
    options = StatusCommandOptions(
        db_path=_optional_path(getattr(args, "db_path", None) if args else None),
    )

    config_path = _optional_path(getattr(args, "config_path", None) if args else None)
    config_backend = JSONConfigStorage(config_path) if config_path is not None else None

    return run_status_command(options, config_backend=config_backend)


def run_chat(args: Namespace | None = None) -> int:
    """Run one-shot cited chat over the local library."""
    try:
        question = str(args.question)
        executable_path = _optional_path(args.llama_cpp_path)
        model_path = _optional_path(args.model_path)
        model_id = str(args.model_id) if args.model_id is not None else None
        model_bytes = _optional_int(args.model_bytes)
        model_checksum = (
            str(args.model_checksum) if args.model_checksum is not None else None
        )
        threads = _optional_int(args.threads)
        timeout = _optional_float(args.timeout)
        db_path = _optional_path(args.db_path)
        config_path = _optional_path(getattr(args, "config_path", None))
        top_k = int(args.top_k) if args.top_k is not None else 10
        show_evidence = bool(getattr(args, "show_evidence", False))
        options = ChatCommandOptions(
            question=question,
            executable_path=executable_path,
            model_path=model_path,
            model_id=model_id,
            model_bytes=model_bytes,
            model_checksum=model_checksum,
            threads=threads,
            timeout=timeout,
            db_path=db_path,
            config_path=config_path,
            top_k=top_k,
            show_evidence=show_evidence,
        )
    except (AttributeError, ValueError, TypeError) as err:
        sys.stderr.write(f"Invalid CLI argument values: {err}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

    return run_chat_command(options)


def run_shell(args: Namespace | None = None) -> int:
    """Enter the bare interactive cited-chat shell over the local library."""
    return run_interactive_shell(ShellCommandOptions())


def run_update(args: Namespace | None = None) -> int:
    """Verify and repair managed local runtime and model artifacts."""
    try:
        offline = bool(getattr(args, "offline", False)) if args else False
        config_path = _optional_path(
            getattr(args, "config_path", None) if args else None
        )
        options = UpdateCommandOptions(offline=offline, config_path=config_path)
    except (AttributeError, ValueError, TypeError) as err:
        sys.stderr.write(f"Invalid CLI argument values: {err}\n")
        return CLIExitCode.TYPED_FAILURE_OR_CONFIG_ERROR

    return run_update_command(options)


def run_analyze(args: Namespace) -> int:
    """Parse options and run PDF research-question analysis."""
    try:
        target_path = Path(args.target_path)
        executable_path = _optional_path(args.llama_cpp_path)
        model_path = _optional_path(args.model_path)
        model_id = str(args.model_id) if args.model_id is not None else None
        model_bytes = _optional_int(args.model_bytes)
        model_checksum = (
            str(args.model_checksum) if args.model_checksum is not None else None
        )
        threads = _optional_int(args.threads)
        timeout = _optional_float(args.timeout)
        db_path = _optional_path(args.db_path)
        config_path = _optional_path(getattr(args, "config_path", None))
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
        config_path=config_path,
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
