"""Probe a literal IP endpoint and assert the expected connectivity state."""

from __future__ import annotations

import socket
import sys


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"reachable", "blocked"}:
        raise SystemExit("usage: network_probe.py reachable|blocked")

    expected = sys.argv[1]
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=5):
            connected = True
    except OSError as error:
        connected = False
        detail = f"{type(error).__name__}: {error}"
    else:
        detail = "TCP connection succeeded"

    if expected == "reachable" and connected:
        print(f"PASS: preflight reachable ({detail})")
        return 0
    if expected == "blocked" and not connected:
        print(f"PASS: offline probe blocked ({detail})")
        return 0

    state = "reachable" if connected else "blocked"
    print(f"FAIL: expected {expected}, observed {state} ({detail})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
