"""Domain tests for the pinned managed-model catalog.

Mirrors ``test_runtime_manifest.py`` for the sibling type. Provisioning
behavior lives in ``tests/services/test_model_provisioning.py``; this file
covers only what the dataclass validates.
"""

import pytest

from econ_paper_cli.domain.artifacts import (
    PINNED_UPDATE_POLICY,
    ArtifactManifestError,
    RedistributionStatus,
)
from econ_paper_cli.domain.model_manifest import (
    ManagedModelArtifact,
    ManagedModelManifestError,
)

VALID_SHA256 = "a" * 64


def _artifact(**overrides: object) -> ManagedModelArtifact:
    base: dict[str, object] = {
        "model_id": "synthetic-model",
        "display_name": "Synthetic Model",
        "source_url": "https://example.invalid/model.gguf",
        "size_bytes": 1024,
        "sha256": VALID_SHA256,
        "filename": "synthetic-model.gguf",
        "license_name": "Apache-2.0",
        "attribution_text": "Synthetic test artifact.",
        "summary": "A synthetic model used only by tests.",
        "minimum_free_ram_bytes": 1024,
        "redistribution_status": RedistributionStatus.PERMITTED,
        "update_policy": PINNED_UPDATE_POLICY,
        "contains_copyrighted_full_text": False,
    }
    base.update(overrides)
    return ManagedModelArtifact(**base)


def test_valid_artifact_constructs() -> None:
    artifact = _artifact()
    assert artifact.model_id == "synthetic-model"
    assert artifact.redistribution_status is RedistributionStatus.PERMITTED


@pytest.mark.parametrize(
    "field,value",
    [
        ("redistribution_status", "permitted"),
        ("redistribution_status", None),
        ("update_policy", ""),
        ("update_policy", "   "),
        ("contains_copyrighted_full_text", "no"),
    ],
)
def test_invalid_licensing_field_rejected(field: str, value: object) -> None:
    with pytest.raises(ManagedModelManifestError):
        _artifact(**{field: value})


@pytest.mark.parametrize("value", [1, 0])
def test_copyrighted_full_text_rejects_integers_not_just_non_numbers(
    value: object,
) -> None:
    """``isinstance(1, bool)`` is False, and a disclosure must be explicit.

    Pinned separately because a truthy ``1`` is the plausible mistake: it
    would read as "yes" while never having been stated as a boolean.
    """
    with pytest.raises(ManagedModelManifestError):
        _artifact(contains_copyrighted_full_text=value)


def test_licensing_fields_raise_this_module_s_error_not_the_schema_s() -> None:
    """The shared vocabulary must not drag in ``ArtifactManifestError``.

    ``RedistributionStatus`` is imported from ``domain.artifacts``, but this
    type keeps its own error contract; a caller catching
    ``ManagedModelManifestError`` must still see every validation failure.
    """
    for field, value in (
        ("redistribution_status", "permitted"),
        ("update_policy", ""),
        ("contains_copyrighted_full_text", 1),
    ):
        with pytest.raises(ManagedModelManifestError) as error:
            _artifact(**{field: value})
        assert not isinstance(error.value, ArtifactManifestError)
