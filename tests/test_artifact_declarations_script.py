"""Behavioral tests for `scripts/render_artifact_declarations.py`.

This is the only state-changing piece of the artifact-metadata work, so both
CLI modes and every marker-failure branch are covered here rather than left to
`--check` passing in CI.

Two habits run through the file. Every test drives `main()` so argument
parsing and exit codes are covered, not just the helpers underneath. And every
assertion about a failure is about the *file*, not only the exit code: a
rewriter that corrupts a document while failing is worse than one that
refuses, and only a byte comparison can tell those apart.

The real document is never written. `--write` runs against copies under
``tmp_path``; the one test that touches ``docs/artifact-licensing.md`` reads
it.
"""

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DOCUMENT = REPO_ROOT / "docs" / "artifact-licensing.md"

_SCRIPT_PATH = REPO_ROOT / "scripts" / "render_artifact_declarations.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "render_artifact_declarations_script", _SCRIPT_PATH
)
if _SCRIPT_SPEC is None or _SCRIPT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("Unable to load the declarations renderer for testing.")
script = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(script)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def document(tmp_path: Path) -> Path:
    """A byte-for-byte copy of the real document, safe to mutate."""
    copy = tmp_path / "artifact-licensing.md"
    shutil.copyfile(REAL_DOCUMENT, copy)
    return copy


def _drift(document: Path) -> None:
    """Flip one declared value inside the generated block.

    A semantic change, not a whitespace one: whitespace would prove nothing
    about the drift this mechanism exists to catch.
    """
    text = document.read_bytes().decode("utf-8")
    begin = text.index(script.BEGIN_MARKER)
    head, tail = text[:begin], text[begin:]
    document.write_bytes(
        (
            head
            + tail.replace(
                "redistribution_status: permitted", "redistribution_status: unknown", 1
            )
        ).encode("utf-8")
    )


# --- --check -----------------------------------------------------------------


def test_check_passes_against_the_committed_document(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert script.main(["--check", "--document", str(REAL_DOCUMENT)]) == 0
    assert "matches the catalogs" in capsys.readouterr().out


def test_check_reports_drift_with_a_diff_naming_the_changed_line(
    document: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _drift(document)
    before = _digest(document)

    assert script.main(["--check", "--document", str(document)]) == 1

    captured = capsys.readouterr()
    assert "-redistribution_status: unknown" in captured.out
    assert "+redistribution_status: permitted" in captured.out
    assert "drifted" in captured.err
    assert _digest(document) == before, "--check must never write."


# --- --write -----------------------------------------------------------------


def test_write_restores_a_drifted_block(document: Path) -> None:
    _drift(document)

    assert script.main(["--write", "--document", str(document)]) == 0
    assert script.check_document(document)


def test_write_changes_only_the_marker_delimited_interior(document: Path) -> None:
    """ "Replaces only the interior" means the other *bytes* are identical.

    Compared as `bytes`, not decoded text. For valid UTF-8 the two are
    equivalent, but the guarantee being claimed is about what lands on disk,
    and only a byte comparison actually states that.
    """
    begin_marker = script.BEGIN_MARKER.encode("utf-8")
    end_marker = script.END_MARKER.encode("utf-8")

    original = document.read_bytes()
    prefix = original[: original.index(begin_marker) + len(begin_marker)]
    suffix = original[original.index(end_marker) :]

    _drift(document)
    script.main(["--write", "--document", str(document)])

    rewritten = document.read_bytes()
    assert rewritten[: rewritten.index(begin_marker) + len(begin_marker)] == prefix
    assert rewritten[rewritten.index(end_marker) :] == suffix


def test_write_on_an_up_to_date_document_is_a_byte_identical_no_op(
    document: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _digest(document)

    assert script.main(["--write", "--document", str(document)]) == 0

    assert "already up to date" in capsys.readouterr().out
    assert _digest(document) == before


def test_write_emits_no_carriage_returns(document: Path) -> None:
    """Binary I/O, asserted on the bytes.

    Text-mode I/O would translate "\\n" to "\\r\\n" on Windows, producing a
    file git then renormalizes and a byte comparison that fails on exactly one
    platform. Asserting here means such a regression fails on Windows CI
    rather than surfacing as a mystery.
    """
    _drift(document)
    script.main(["--write", "--document", str(document)])

    assert b"\r" not in document.read_bytes()


# --- Marker failures ---------------------------------------------------------
#
# Two markers times two failure modes, plus the ordering case. Each must exit
# non-zero *and* leave the file byte-identical.


def _corrupt(document: Path, transform: str) -> None:
    text = document.read_bytes().decode("utf-8")
    if transform == "opening-absent":
        text = text.replace(script.BEGIN_MARKER, "", 1)
    elif transform == "closing-absent":
        text = text.replace(script.END_MARKER, "", 1)
    elif transform == "opening-duplicated":
        text = text.replace(
            script.BEGIN_MARKER, f"{script.BEGIN_MARKER}\n{script.BEGIN_MARKER}", 1
        )
    elif transform == "closing-duplicated":
        text = text.replace(
            script.END_MARKER, f"{script.END_MARKER}\n{script.END_MARKER}", 1
        )
    elif transform == "reversed":
        begin_at = text.index(script.BEGIN_MARKER)
        end_at = text.index(script.END_MARKER)
        interior = text[begin_at + len(script.BEGIN_MARKER) : end_at]
        text = (
            text[:begin_at]
            + script.END_MARKER
            + interior
            + script.BEGIN_MARKER
            + text[end_at + len(script.END_MARKER) :]
        )
    else:  # pragma: no cover - guards against a typo in the parametrization
        raise AssertionError(f"Unknown transform {transform!r}")
    document.write_bytes(text.encode("utf-8"))


@pytest.mark.parametrize(
    "transform",
    [
        "opening-absent",
        "closing-absent",
        "opening-duplicated",
        "closing-duplicated",
        "reversed",
    ],
)
@pytest.mark.parametrize("mode", ["--check", "--write"])
def test_marker_failures_exit_non_zero_without_modifying_the_document(
    document: Path, transform: str, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _corrupt(document, transform)
    before = _digest(document)

    assert script.main([mode, "--document", str(document)]) != 0

    assert "The document was not modified." in capsys.readouterr().err
    assert _digest(document) == before


def test_marker_failure_is_distinguishable_from_drift(document: Path) -> None:
    """Exit 2 for a broken document, exit 1 for a merely stale one.

    Collapsing them would tell a caller to run --write when --write cannot
    help.
    """
    _corrupt(document, "opening-absent")
    assert script.main(["--check", "--document", str(document)]) == 2
