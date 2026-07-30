from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path, PureWindowsPath
from typing import cast

import pytest

from econ_paper_cli.domain import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactManifestError,
    DomainError,
    RedistributionStatus,
)

SHA256 = "0" * 64


def test_artifact_manifest_error_inherits_from_domain_error() -> None:
    """Verify that ArtifactManifestError inherits from DomainError."""
    assert issubclass(ArtifactManifestError, DomainError)
    err = ArtifactManifestError("schema violation")
    assert isinstance(err, DomainError)
    assert isinstance(err, ValueError)


def valid_mapping(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": 1,
        "artifact_id": "synthetic-fixture-index",
        "kind": "index",
        "version": "1.0.0",
        "source": "https://example.invalid/synthetic-fixture-index",
        "license": "CC0-1.0",
        "redistribution_status": "permitted",
        "expected_size_bytes": 128,
        "sha256": SHA256,
        "update_policy": "Pinned test fixture; update manually.",
        "contains_copyrighted_full_text": False,
        "local_path": "indexes/synthetic-fixture-index.bin",
    }
    data.update(overrides)
    return data


def test_manifest_round_trips_canonical_mapping() -> None:
    data = valid_mapping()

    manifest = ArtifactManifest.from_mapping(data)

    assert manifest.schema_version == 1
    assert manifest.kind is ArtifactKind.INDEX
    assert manifest.license_name == "CC0-1.0"
    assert manifest.redistribution_status is RedistributionStatus.PERMITTED
    assert manifest.local_path == Path("indexes/synthetic-fixture-index.bin")
    assert manifest.to_mapping() == data


@pytest.mark.parametrize("value", [None, [], "not-a-mapping"])
def test_manifest_requires_a_mapping(value: object) -> None:
    with pytest.raises(ArtifactManifestError, match="mapping"):
        ArtifactManifest.from_mapping(cast(Mapping[str, object], value))


@pytest.mark.parametrize("kind", list(ArtifactKind))
def test_manifest_supports_each_artifact_kind(kind: ArtifactKind) -> None:
    manifest = ArtifactManifest.from_mapping(valid_mapping(kind=kind.value))

    assert manifest.kind is kind


@pytest.mark.parametrize("status", list(RedistributionStatus))
def test_manifest_supports_each_redistribution_status(
    status: RedistributionStatus,
) -> None:
    manifest = ArtifactManifest.from_mapping(
        valid_mapping(redistribution_status=status.value)
    )

    assert manifest.redistribution_status is status


def test_manifest_is_frozen() -> None:
    manifest = ArtifactManifest.from_mapping(valid_mapping())

    with pytest.raises(FrozenInstanceError):
        manifest.version = "2.0.0"


def test_direct_construction_uses_the_same_validation() -> None:
    with pytest.raises(ArtifactManifestError, match="expected_size_bytes"):
        ArtifactManifest(
            schema_version=1,
            artifact_id="synthetic-fixture-index",
            kind=ArtifactKind.INDEX,
            version="1.0.0",
            source="https://example.invalid/synthetic-fixture-index",
            license_name="CC0-1.0",
            redistribution_status=RedistributionStatus.PERMITTED,
            expected_size_bytes=0,
            sha256=SHA256,
            update_policy="Pinned test fixture; update manually.",
            contains_copyrighted_full_text=False,
            local_path=Path("indexes/synthetic-fixture-index.bin"),
        )


def test_missing_fields_are_reported_together() -> None:
    data = valid_mapping()
    del data["license"]
    del data["sha256"]

    with pytest.raises(ArtifactManifestError) as error:
        ArtifactManifest.from_mapping(data)

    assert "missing required fields" in str(error.value)
    assert "license" in str(error.value)
    assert "sha256" in str(error.value)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ArtifactManifestError, match="unknown fields.*surprise"):
        ArtifactManifest.from_mapping(valid_mapping(surprise="value"))


def test_non_string_field_names_are_rejected() -> None:
    data = valid_mapping()
    data[cast(str, 1)] = "value"

    with pytest.raises(ArtifactManifestError, match="field names must be strings"):
        ArtifactManifest.from_mapping(data)


@pytest.mark.parametrize("schema_version", [0, 2, True, "1"])
def test_schema_version_must_be_integer_one(schema_version: object) -> None:
    with pytest.raises(ArtifactManifestError, match="schema_version.*1"):
        ArtifactManifest.from_mapping(valid_mapping(schema_version=schema_version))


@pytest.mark.parametrize(
    "artifact_id",
    ["", "Uppercase", "two words", "-leading", "trailing-", "double--dash"],
)
def test_artifact_id_uses_canonical_grammar(artifact_id: str) -> None:
    with pytest.raises(ArtifactManifestError, match="artifact_id"):
        ArtifactManifest.from_mapping(valid_mapping(artifact_id=artifact_id))


@pytest.mark.parametrize(
    "field",
    ["version", "source", "license", "update_policy"],
)
@pytest.mark.parametrize("value", ["", "   ", 3, None])
def test_text_fields_must_be_nonempty_strings(field: str, value: object) -> None:
    with pytest.raises(ArtifactManifestError, match=field):
        ArtifactManifest.from_mapping(valid_mapping(**{field: value}))


def test_invalid_artifact_kind_lists_allowed_values() -> None:
    with pytest.raises(ArtifactManifestError) as error:
        ArtifactManifest.from_mapping(valid_mapping(kind="archive"))

    message = str(error.value)
    assert "kind" in message
    for value in ("corpus", "index", "model"):
        assert value in message


@pytest.mark.parametrize("value", [None, 0])
@pytest.mark.parametrize(
    ("field", "allowed_values"),
    [
        ("kind", ("corpus", "index", "model")),
        ("redistribution_status", ("permitted", "prohibited", "unknown")),
    ],
)
def test_enum_fields_reject_non_string_values_with_allowed_values(
    field: str,
    allowed_values: tuple[str, ...],
    value: object,
) -> None:
    with pytest.raises(ArtifactManifestError) as error:
        ArtifactManifest.from_mapping(valid_mapping(**{field: value}))

    message = str(error.value)
    assert field in message
    for allowed_value in allowed_values:
        assert allowed_value in message


def test_invalid_redistribution_status_lists_allowed_values() -> None:
    with pytest.raises(ArtifactManifestError) as error:
        ArtifactManifest.from_mapping(valid_mapping(redistribution_status="maybe"))

    message = str(error.value)
    assert "redistribution_status" in message
    for value in ("permitted", "prohibited", "unknown"):
        assert value in message


@pytest.mark.parametrize("size", [0, -1, True, 1.5, "128"])
def test_expected_size_must_be_a_positive_integer(size: object) -> None:
    with pytest.raises(ArtifactManifestError, match="expected_size_bytes"):
        ArtifactManifest.from_mapping(valid_mapping(expected_size_bytes=size))


@pytest.mark.parametrize(
    "sha256",
    ["0" * 63, "0" * 65, "A" * 64, "g" * 64, 123, None],
)
def test_sha256_must_be_canonical_lowercase_hex(sha256: object) -> None:
    with pytest.raises(ArtifactManifestError, match="sha256.*64 lowercase"):
        ArtifactManifest.from_mapping(valid_mapping(sha256=sha256))


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_copyrighted_full_text_flag_must_be_boolean(value: object) -> None:
    with pytest.raises(
        ArtifactManifestError,
        match="contains_copyrighted_full_text.*boolean",
    ):
        ArtifactManifest.from_mapping(
            valid_mapping(contains_copyrighted_full_text=value)
        )


@pytest.mark.parametrize(
    "local_path",
    [
        "",
        "/indexes/file.bin",
        "C:/indexes/file.bin",
        "C:\\indexes\\file.bin",
        "\\\\server\\share\\file.bin",
        "../file.bin",
        "indexes/../file.bin",
    ],
)
def test_local_path_must_be_a_safe_relative_path(local_path: str) -> None:
    with pytest.raises(ArtifactManifestError, match="local_path"):
        ArtifactManifest.from_mapping(valid_mapping(local_path=local_path))


@pytest.mark.parametrize(
    "local_path",
    [
        "models/bad:name.gguf",
        "models/bad\x00name.gguf",
        "models/CON",
        "models/con.txt",
        "models/COM1.bin",
        "models/trailing.",
        "models/trailing ",
    ],
)
def test_local_path_rejects_nonportable_components(local_path: str) -> None:
    with pytest.raises(ArtifactManifestError, match="portable"):
        ArtifactManifest.from_mapping(valid_mapping(local_path=local_path))


@pytest.mark.parametrize("field", ["artifact_id", "local_path"])
@pytest.mark.parametrize("value", [None, 0])
def test_string_identifiers_reject_non_string_values(field: str, value: object) -> None:
    with pytest.raises(ArtifactManifestError, match=field):
        ArtifactManifest.from_mapping(valid_mapping(**{field: value}))


def test_local_path_serializes_with_forward_slashes() -> None:
    manifest = ArtifactManifest(
        schema_version=1,
        artifact_id="synthetic-fixture-index",
        kind=ArtifactKind.INDEX,
        version="1.0.0",
        source="https://example.invalid/synthetic-fixture-index",
        license_name="CC0-1.0",
        redistribution_status=RedistributionStatus.PERMITTED,
        expected_size_bytes=128,
        sha256=SHA256,
        update_policy="Pinned test fixture; update manually.",
        contains_copyrighted_full_text=False,
        local_path=Path("indexes") / "nested" / "fixture.bin",
    )

    assert manifest.to_mapping()["local_path"] == "indexes/nested/fixture.bin"


def test_direct_posix_path_rejects_a_windows_separator() -> None:
    if isinstance(Path("."), PureWindowsPath):
        pytest.skip("Backslash is the native separator on Windows.")

    with pytest.raises(ArtifactManifestError, match="local_path"):
        ArtifactManifest(
            schema_version=1,
            artifact_id="synthetic-fixture-index",
            kind=ArtifactKind.INDEX,
            version="1.0.0",
            source="https://example.invalid/synthetic-fixture-index",
            license_name="CC0-1.0",
            redistribution_status=RedistributionStatus.PERMITTED,
            expected_size_bytes=128,
            sha256=SHA256,
            update_policy="Pinned test fixture; update manually.",
            contains_copyrighted_full_text=False,
            local_path=Path(r"indexes\fixture.bin"),
        )


def test_manifest_accepts_a_path_without_inspecting_the_filesystem() -> None:
    manifest = ArtifactManifest.from_mapping(
        valid_mapping(local_path="not-created.bin")
    )

    assert manifest.local_path == Path("not-created.bin")
