"""Run the real CLI scenarios used by the offline release check."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(command: list[str], *, input_text: str | None = None) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(
        command,
        input=input_text,
        check=True,
        text=True,
        timeout=900,
    )


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: run_offline_cli.py PDF_PATH [CLI_PATH]")
    pdf_path = Path(sys.argv[1]).resolve()
    cli_path = sys.argv[2] if len(sys.argv) == 3 else "econpapers"
    _run([cli_path, "status"])
    _run([cli_path, "chat", "What is this paper about?"])
    _run(
        [cli_path],
        input_text="What is this paper about?\n/exit\n",
    )
    _run([cli_path, "analyze", str(pdf_path)])
    print("PASS: all real CLI scenarios completed offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
