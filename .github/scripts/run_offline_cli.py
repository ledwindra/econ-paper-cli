"""Run the real CLI scenarios used by the offline release check."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _emit(text: str, transcript_path: Path | None) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()
    if transcript_path is not None:
        with transcript_path.open("a", encoding="utf-8", newline="\n") as transcript:
            transcript.write(text)


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    transcript_path: Path | None = None,
) -> None:
    _emit(f"$ {' '.join(command)}\n", transcript_path)
    result = subprocess.run(
        command,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=900,
    )
    _emit(result.stdout, transcript_path)
    if result.returncode != 0:
        _emit(
            f"FAIL: command exited with status {result.returncode}\n",
            transcript_path,
        )
        raise SystemExit(result.returncode)


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: run_offline_cli.py PDF_PATH [CLI_PATH]")
    pdf_path = Path(sys.argv[1]).resolve()
    cli_path = sys.argv[2] if len(sys.argv) == 3 else "econpapers"
    transcript_value = os.environ.get("ECONPAPERS_RELEASE_OUTPUT")
    transcript_path = Path(transcript_value) if transcript_value else None
    _run([cli_path, "status"], transcript_path=transcript_path)
    _run(
        [cli_path, "chat", "What is this paper about?"],
        transcript_path=transcript_path,
    )
    _run(
        [cli_path],
        input_text="What is this paper about?\n/exit\n",
        transcript_path=transcript_path,
    )
    _run(
        [cli_path, "analyze", str(pdf_path)],
        transcript_path=transcript_path,
    )
    _emit("PASS: all real CLI scenarios completed offline\n", transcript_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
