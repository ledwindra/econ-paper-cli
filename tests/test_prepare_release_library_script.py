"""Regression coverage for the hosted release-library seeding helper."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_seeded_fixture_is_reused_without_runtime_configuration(tmp_path: Path) -> None:
    pdf_path = tmp_path / "release-fixture.pdf"
    environment = os.environ.copy()
    environment["ECONPAPERS_LIBRARY_DIR"] = str(tmp_path / "library")
    environment["ECONPAPERS_CONFIG_DIR"] = str(tmp_path / "config")

    subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / ".github/scripts/create_release_fixture_pdf.py"),
            str(pdf_path),
        ],
        check=True,
        env=environment,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / ".github/scripts/prepare_release_library.py"),
            str(pdf_path),
        ],
        check=True,
        env=environment,
    )

    checksum = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    storage = SQLiteStorage(tmp_path / "library/econpapers.db")
    storage.initialize()
    try:
        assert storage.count_papers() == 1
        assert storage.get_single_paper_analysis_by_checksum(checksum) is not None
    finally:
        storage.close()

    analysis = subprocess.run(
        [
            sys.executable,
            "-c",
            "from econ_paper_cli.cli import main; raise SystemExit(main())",
            "analyze",
            str(pdf_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert analysis.returncode == 0, analysis.stdout + analysis.stderr
    assert "Outcome: reused" in analysis.stdout
    assert "No runtime/model configuration is available" not in analysis.stderr
