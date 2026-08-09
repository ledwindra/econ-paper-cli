"""Bind `docs/artifact-licensing.md` to the catalogs it describes.

Three layers, deliberately of different strengths, because they can be:

* the **generated declarations block** is compared byte-for-byte, so every
  typed licensing fact fails the suite if code and document disagree;
* the **narrative** gets containment guards — per artifact for the values
  that differ between artifacts, whole-document for the ones all six share;
* the **overlap** between the managed catalog and the committed
  `ArtifactManifest` evaluation records is checked on the fields that
  identify the bytes, and deliberately not on the ones allowed to diverge.

Nothing here writes. The renderer's `--write` mode is exercised only in
`test_artifact_declarations_script.py`, against temporary copies: a test that
regenerated the file it checks would pass unconditionally.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from econ_paper_cli.domain.artifacts import PINNED_UPDATE_POLICY, RedistributionStatus
from econ_paper_cli.domain.model_manifest import (
    MANAGED_MODEL_CATALOG,
    ManagedModelArtifact,
)
from econ_paper_cli.domain.runtime_manifest import ManagedRuntimeArtifact
from econ_paper_cli.domain.runtime_manifest_data import MANAGED_RUNTIME_MANIFEST

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = REPO_ROOT / "docs" / "artifact-licensing.md"
EVALUATION_MANIFEST_DIR = REPO_ROOT / "artifacts" / "models"

_SCRIPT_PATH = REPO_ROOT / "scripts" / "render_artifact_declarations.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "render_artifact_declarations_for_doc_test", _SCRIPT_PATH
)
if _SCRIPT_SPEC is None or _SCRIPT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("Unable to load the declarations renderer for testing.")
renderer = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(renderer)

_SHA256_TOKEN = re.compile(r"\b[0-9a-f]{64}\b")


def _document_text() -> str:
    return DOCUMENT.read_bytes().decode("utf-8")


def _narrative_text() -> str:
    """The document with the generated block removed, whitespace-normalized.

    The block is excluded so it cannot satisfy an assertion about the prose:
    without this, every narrative guard would pass simply because the block
    below it contains the same value.
    """
    text = _document_text()
    begin = text.index(renderer.BEGIN_MARKER)
    end = text.index(renderer.END_MARKER) + len(renderer.END_MARKER)
    return " ".join((text[:begin] + text[end:]).split())


def _model_artifacts() -> tuple[ManagedModelArtifact, ...]:
    return MANAGED_MODEL_CATALOG.artifacts


def _runtime_artifacts() -> tuple[ManagedRuntimeArtifact, ...]:
    return MANAGED_RUNTIME_MANIFEST.artifacts


def _artifact_level_checksums() -> set[str]:
    """Only the six top-level digests.

    ``bundle_member_checksums`` are intentionally absent from the document, so
    including them here would make the reverse sweep below unsatisfiable.
    """
    return {artifact.sha256 for artifact in _model_artifacts()} | {
        artifact.archive_sha256 for artifact in _runtime_artifacts()
    }


# --- The generated block: byte-exact both ways -------------------------------


def test_generated_block_matches_the_catalogs_byte_for_byte() -> None:
    assert renderer.check_document(DOCUMENT), (
        "The generated declarations block has drifted from the catalogs. "
        "Run: python scripts/render_artifact_declarations.py --write"
    )


def _block_records() -> list[tuple[str, str]]:
    """Parse the rendered block into ``(kind, artifact identifier)`` pairs.

    Returned as a list, not a set, so a duplicated record is visible: a
    renderer that emitted one artifact twice and dropped another would keep
    the record *count* right and the set identical.
    """
    body = renderer.render_declarations()
    body = body[body.index("\n") + 1 : body.rindex("\n```")]
    records: list[tuple[str, str]] = []
    for chunk in body.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in chunk.splitlines() if ": " in line
        )
        records.append((fields["kind"], fields["artifact"]))
    return records


def test_generated_block_covers_exactly_the_downloadable_artifacts() -> None:
    """Byte equality alone cannot catch an omission.

    If the renderer dropped an artifact, `--write` would regenerate the
    document to match and every equality assertion would still pass. Counting
    records is not enough either — omitting one runtime while duplicating
    another preserves the count. So this pins the exact set of six
    ``(kind, identifier)`` pairs, with platform and architecture for the
    runtimes, which all share one ``runtime_id``.
    """
    expected = [("model", artifact.model_id) for artifact in _model_artifacts()]
    expected += [
        (
            "runtime",
            f"{artifact.runtime_id} "
            f"({artifact.platform.value}/{artifact.architecture.value})",
        )
        for artifact in _runtime_artifacts()
    ]

    records = _block_records()

    assert len(records) == len(set(records)), f"Duplicate records: {records}"
    assert sorted(records) == sorted(expected)


# --- The narrative: per-artifact where values differ -------------------------


@pytest.mark.parametrize("model", _model_artifacts(), ids=lambda item: item.model_id)
def test_narrative_records_each_model_s_distinguishing_values(
    model: ManagedModelArtifact,
) -> None:
    narrative = _narrative_text()
    assert model.source_url in narrative
    assert model.sha256 in narrative
    assert f"{model.size_bytes:,}" in narrative


@pytest.mark.parametrize(
    "runtime",
    _runtime_artifacts(),
    ids=lambda item: f"{item.platform.value}-{item.architecture.value}",
)
def test_narrative_records_each_runtime_s_distinguishing_values(
    runtime: ManagedRuntimeArtifact,
) -> None:
    narrative = _narrative_text()
    assert runtime.source_url in narrative
    assert runtime.archive_sha256 in narrative
    assert f"{runtime.archive_size_bytes:,}" in narrative


# --- The narrative: whole-document consistency -------------------------------
#
# All six artifacts currently share a license family, a redistribution status,
# and one policy, so a single occurrence satisfies these for every artifact.
# They detect a wholesale contradiction, not one divergent row; the generated
# block is what binds these fields per artifact.


def test_narrative_uses_the_declared_licenses_and_statuses() -> None:
    narrative = _narrative_text()
    licenses = {artifact.license_name for artifact in _model_artifacts()} | {
        artifact.license_name for artifact in _runtime_artifacts()
    }
    statuses = {
        artifact.redistribution_status.value for artifact in _model_artifacts()
    } | {artifact.redistribution_status.value for artifact in _runtime_artifacts()}
    for value in licenses | statuses:
        assert value in narrative


def test_narrative_makes_no_affirmative_copyrighted_text_disclosure() -> None:
    """Guard for the current all-false invariant, asserted explicitly.

    The narrative has no per-artifact machine-readable structure for this
    field — the model tables say "Contains copyrighted full text | No." and
    the runtime section makes one statement covering all four archives — so
    there is nothing a future ``True`` could be matched against. Rather than
    pretend the guard adapts, it asserts its own precondition and fails with
    a directive message when that stops holding.
    """
    disclosures = {
        artifact.contains_copyrighted_full_text for artifact in _model_artifacts()
    } | {artifact.contains_copyrighted_full_text for artifact in _runtime_artifacts()}
    assert disclosures == {False}, (
        "An artifact now declares contains_copyrighted_full_text=True. This "
        "narrative guard assumes a uniform all-false disclosure and cannot "
        "express a per-artifact one: add machine-readable per-artifact "
        "disclosure to the document, or drop this guard and rely on the "
        "generated block, which is per-artifact and exact."
    )
    assert (
        re.search(
            r"contains copyrighted full text[^.]{0,20}\byes\b",
            _narrative_text(),
            re.IGNORECASE,
        )
        is None
    )


def test_every_checksum_in_the_document_is_a_real_artifact_digest() -> None:
    """Reverse sweep: catches a stale pin left behind after a code change."""
    found = set(_SHA256_TOKEN.findall(_document_text()))
    assert found, "Expected the document to quote artifact checksums."
    assert found <= _artifact_level_checksums()


def test_every_artifact_digest_appears_in_the_document() -> None:
    assert _artifact_level_checksums() <= set(_SHA256_TOKEN.findall(_document_text()))


# --- Code-internal invariants the document asserts in prose ------------------


def test_all_artifacts_share_one_update_policy_constant() -> None:
    """Makes "one policy governs all six artifacts" true of the code."""
    for artifact in (*_model_artifacts(), *_runtime_artifacts()):
        assert artifact.update_policy is PINNED_UPDATE_POLICY


def test_all_artifacts_are_permitted_and_carry_no_paper_text() -> None:
    for artifact in (*_model_artifacts(), *_runtime_artifacts()):
        assert artifact.redistribution_status is RedistributionStatus.PERMITTED
        assert artifact.contains_copyrighted_full_text is False


# --- Overlap with the committed evaluation manifests -------------------------


def _overlapping_records() -> list[tuple[str, dict[str, object]]]:
    catalog_ids = {artifact.model_id for artifact in _model_artifacts()}
    overlaps: list[tuple[str, dict[str, object]]] = []
    for path in sorted(EVALUATION_MANIFEST_DIR.glob("*.manifest.json")):
        data = json.loads(path.read_bytes().decode("utf-8"))
        if data["artifact_id"] in catalog_ids:
            overlaps.append((path.name, data))
    return overlaps


def test_exactly_one_evaluation_manifest_overlaps_the_catalog() -> None:
    """Pinned so a new overlap cannot appear without a deliberate decision."""
    overlaps = _overlapping_records()
    assert [name for name, _ in overlaps] == [
        "qwen2.5-1.5b-instruct-q4-k-m.manifest.json"
    ]


def test_overlapping_records_agree_about_the_bytes() -> None:
    """Only sha256 and size identify the file; a divergence is a defect.

    License, redistribution status, copyrighted-full-text status, and update
    policy are deliberately *not* compared. The two records answer different
    questions — one describes a download pin, the other an Issue-13
    evaluation candidate — and forcing them equal would destroy real
    information. See docs/artifact-licensing.md.
    """
    for name, data in _overlapping_records():
        artifact = MANAGED_MODEL_CATALOG.get(str(data["artifact_id"]))
        assert data["sha256"] == artifact.sha256, name
        assert data["expected_size_bytes"] == artifact.size_bytes, name


def test_the_known_source_url_divergence_is_frozen() -> None:
    """The two records pin the same bytes under different URLs.

    The catalog uses Hugging Face's mutable ``resolve/main/`` ref; the
    evaluation manifest the immutable ``resolve/<commit>/`` one. Fixing that
    needs a fresh download to confirm the pin, so it is tracked separately.
    Freezing both values here means the divergence cannot widen unnoticed,
    and that the eventual fix trips this test rather than slipping through.
    """
    catalog_url = MANAGED_MODEL_CATALOG.get("qwen2.5-1.5b-instruct-q4-k-m").source_url
    assert catalog_url == (
        "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
        "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    )
    data = json.loads(
        (
            EVALUATION_MANIFEST_DIR / "qwen2.5-1.5b-instruct-q4-k-m.manifest.json"
        ).read_bytes()
    )
    assert data["source"] == (
        "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/"
        "dd26da440ef0330c47919d1ecae0966d24022222/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    )
