"""Render the typed licensing declarations block in `docs/artifact-licensing.md`.

The document's human narrative paraphrases: it wraps sentences, rewrites the
llama.cpp copyright notice in its own words, and explains the update policy
across several paragraphs. Paraphrase is legitimate for prose and fatal for a
consistency check, so the machine-checkable facts live in one generated block
instead, compared byte-exactly against this renderer.

Authority runs catalogs -> generated block -> narrative. The catalogs
(`domain.model_manifest`, `domain.runtime_manifest_data`) are what `setup` and
`update` act on; this block is a mechanical projection of them; the narrative
explains and is authoritative for nothing the block covers.

Usage::

    python scripts/render_artifact_declarations.py --check
    python scripts/render_artifact_declarations.py --write
    python scripts/render_artifact_declarations.py --check --document PATH

All file access is binary. `.gitattributes` normalizes line endings at
checkout, but that says nothing about what *this* program writes: Python's
text mode would translate "\\n" to "\\r\\n" on Windows, producing a file git
then renormalizes and a byte comparison that fails on one platform only.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from econ_paper_cli.domain.model_manifest import (
    MANAGED_MODEL_CATALOG,
    ManagedModelArtifact,
)
from econ_paper_cli.domain.runtime_manifest import ManagedRuntimeArtifact
from econ_paper_cli.domain.runtime_manifest_data import MANAGED_RUNTIME_MANIFEST

BEGIN_MARKER = "<!-- BEGIN GENERATED: artifact-declarations -->"
END_MARKER = "<!-- END GENERATED: artifact-declarations -->"

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMENT = REPOSITORY_ROOT / "docs" / "artifact-licensing.md"

_EXIT_OK = 0
_EXIT_DRIFT = 1
_EXIT_MARKER_ERROR = 2


class MarkerError(RuntimeError):
    """Raised when the document's generated-block markers are unusable.

    Every path that raises this leaves the document untouched. A rewriter that
    corrupts a file while failing is worse than one that refuses.
    """


def _collapse(text: str) -> str:
    """Flatten free text to one line so the block stays line-oriented.

    This is the only whitespace normalization anywhere in the mechanism, and
    it happens here, in producing canonical output — never in comparing it.
    """
    return " ".join(text.split())


def _field_lines(pairs: Sequence[tuple[str, str]]) -> list[str]:
    return [f"{key}: {value}" for key, value in pairs]


def _model_record(artifact: ManagedModelArtifact) -> list[str]:
    return _field_lines(
        (
            ("artifact", artifact.model_id),
            ("kind", "model"),
            ("source", artifact.source_url),
            ("license", artifact.license_name),
            ("redistribution_status", artifact.redistribution_status.value),
            ("size_bytes", str(artifact.size_bytes)),
            ("sha256", artifact.sha256),
            ("update_policy", _collapse(artifact.update_policy)),
            (
                "contains_copyrighted_full_text",
                "true" if artifact.contains_copyrighted_full_text else "false",
            ),
            ("attribution", _collapse(artifact.attribution_text)),
        )
    )


def _runtime_record(artifact: ManagedRuntimeArtifact) -> list[str]:
    # All four archives share a runtime_id, so the identifier has to carry
    # platform and architecture to be unique and to sort deterministically.
    identifier = (
        f"{artifact.runtime_id} "
        f"({artifact.platform.value}/{artifact.architecture.value})"
    )
    return _field_lines(
        (
            ("artifact", identifier),
            ("kind", "runtime"),
            ("source", artifact.source_url),
            ("license", artifact.license_name),
            ("redistribution_status", artifact.redistribution_status.value),
            ("size_bytes", str(artifact.archive_size_bytes)),
            ("sha256", artifact.archive_sha256),
            ("update_policy", _collapse(artifact.update_policy)),
            (
                "contains_copyrighted_full_text",
                "true" if artifact.contains_copyrighted_full_text else "false",
            ),
            ("attribution", _collapse(artifact.attribution_text)),
        )
    )


def render_declarations() -> str:
    """Return the canonical block interior for the current catalogs.

    Pure: reads the two catalogs and nothing else. Models come before
    runtimes, each group sorted by its artifact identifier, so the output is
    stable across runs and platforms.
    """
    records: list[list[str]] = []
    records.extend(
        _model_record(artifact)
        for artifact in sorted(
            MANAGED_MODEL_CATALOG.artifacts, key=lambda item: item.model_id
        )
    )
    records.extend(
        _runtime_record(artifact)
        for artifact in sorted(
            MANAGED_RUNTIME_MANIFEST.artifacts,
            key=lambda item: (
                item.runtime_id,
                item.platform.value,
                item.architecture.value,
            ),
        )
    )
    body = "\n\n".join("\n".join(record) for record in records)
    return f"```text\n{body}\n```"


def _split_on_markers(text: str) -> tuple[str, str, str]:
    """Return (prefix, interior, suffix), validating the markers first.

    Five ways this can fail, and all five leave the caller with an exception
    rather than a partially rewritten document: either marker missing, either
    marker duplicated, or the closing marker preceding the opening one.
    """
    for name, marker in (("opening", BEGIN_MARKER), ("closing", END_MARKER)):
        count = text.count(marker)
        if count == 0:
            raise MarkerError(f"The {name} marker {marker!r} is missing.")
        if count > 1:
            raise MarkerError(
                f"The {name} marker {marker!r} appears {count} times; "
                "exactly one is required."
            )

    begin = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER)
    if end < begin:
        raise MarkerError(
            "The closing marker precedes the opening marker; the generated "
            "block boundaries are reversed."
        )

    interior_start = begin + len(BEGIN_MARKER)
    return text[:interior_start], text[interior_start:end], text[end:]


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _rendered_document(path: Path) -> tuple[str, str]:
    """Return the document as committed and as it should be."""
    current = _read(path)
    prefix, _, suffix = _split_on_markers(current)
    expected = f"{prefix}\n{render_declarations()}\n{suffix}"
    return current, expected


def check_document(path: Path, *, stream: TextIO | None = None) -> bool:
    """Return True when the committed block matches the catalogs.

    Prints a unified diff when it does not, so a failing run says which line
    drifted rather than only that something did.
    """
    out = sys.stdout if stream is None else stream
    current, expected = _rendered_document(path)
    if current == expected:
        return True
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{path} (committed)",
        tofile=f"{path} (rendered from the catalogs)",
    )
    print("".join(diff), end="", file=out)
    return False


def write_document(path: Path) -> bool:
    """Rewrite only the text between the markers. Returns True if changed.

    Binary write, so the bytes on disk are exactly the bytes rendered here on
    every platform.
    """
    current, expected = _rendered_document(path)
    if current == expected:
        return False
    path.write_bytes(expected.encode("utf-8"))
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render or verify the typed licensing declarations block in "
            "docs/artifact-licensing.md."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero and print a diff when the block has drifted.",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the generated block in place.",
    )
    parser.add_argument(
        "--document",
        type=Path,
        default=DEFAULT_DOCUMENT,
        help="Document to operate on (default: docs/artifact-licensing.md).",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            if check_document(args.document):
                print(f"{args.document}: generated block matches the catalogs.")
                return _EXIT_OK
            print(
                f"{args.document}: generated block has drifted; "
                "run with --write to regenerate.",
                file=sys.stderr,
            )
            return _EXIT_DRIFT
        if write_document(args.document):
            print(f"{args.document}: generated block rewritten.")
        else:
            print(f"{args.document}: generated block already up to date.")
        return _EXIT_OK
    except MarkerError as error:
        print(
            f"{args.document}: {error} The document was not modified.", file=sys.stderr
        )
        return _EXIT_MARKER_ERROR


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
