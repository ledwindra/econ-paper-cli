"""Domain tests for the managed-runtime install receipt."""

from pathlib import PurePosixPath

import pytest

from econ_paper_cli.domain import (
    InstallReceipt,
    InstallReceiptError,
    SupportedArchitecture,
    SupportedPlatform,
)

VALID_SHA = "a" * 64
EXE_PATH = PurePosixPath("bin/llama-completion")


def _receipt(**overrides: object) -> InstallReceipt:
    base: dict[str, object] = {
        "schema_version": 1,
        "runtime_id": "llama.cpp-b10199",
        "version_marker": "10199",
        "platform": SupportedPlatform.LINUX,
        "architecture": SupportedArchitecture.X86_64,
        "source_asset_identity": "b10199/llama-b10199-bin-ubuntu-x64.tar.gz",
        "archive_size_bytes": 1024,
        "archive_sha256": VALID_SHA,
        "executable_relative_path": EXE_PATH,
        "executable_sha256": VALID_SHA,
        "member_checksums": (
            (EXE_PATH, VALID_SHA),
            (PurePosixPath("lib/libggml.so"), "b" * 64),
        ),
    }
    base.update(overrides)
    return InstallReceipt(**base)


def test_valid_receipt_constructs() -> None:
    receipt = _receipt()
    assert receipt.runtime_id == "llama.cpp-b10199"
    assert receipt.member_checksum_map()[EXE_PATH] == VALID_SHA


def test_round_trip_through_mapping() -> None:
    receipt = _receipt()
    restored = InstallReceipt.from_mapping(receipt.to_mapping())
    assert restored == receipt


def test_from_mapping_rejects_missing_fields() -> None:
    data = _receipt().to_mapping()
    del data["executable_sha256"]
    with pytest.raises(InstallReceiptError):
        InstallReceipt.from_mapping(data)


def test_from_mapping_rejects_unknown_fields() -> None:
    data = _receipt().to_mapping()
    data["extra_field"] = "surprise"
    with pytest.raises(InstallReceiptError):
        InstallReceipt.from_mapping(data)


def test_from_mapping_rejects_non_dict() -> None:
    with pytest.raises(InstallReceiptError):
        InstallReceipt.from_mapping(["not", "a", "dict"])


def test_executable_must_appear_in_member_checksums() -> None:
    with pytest.raises(InstallReceiptError):
        _receipt(
            executable_relative_path=PurePosixPath("bin/other-binary"),
        )


def test_executable_sha256_must_match_its_member_checksums_entry() -> None:
    """A hand-crafted or tampered receipt where the top-level
    executable_sha256 disagrees with the (also present) member_checksums
    entry for the same path must never validate."""
    with pytest.raises(InstallReceiptError):
        _receipt(
            executable_sha256="c" * 64,
            member_checksums=((EXE_PATH, VALID_SHA),),  # "a" * 64, not "c" * 64
        )


def test_member_checksums_must_be_nonempty() -> None:
    with pytest.raises(InstallReceiptError):
        _receipt(member_checksums=())


def test_member_checksums_rejects_duplicate_paths() -> None:
    with pytest.raises(InstallReceiptError):
        _receipt(member_checksums=((EXE_PATH, VALID_SHA), (EXE_PATH, "c" * 64)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("runtime_id", "Bad Id"),
        ("version_marker", ""),
        ("platform", "linux"),
        ("architecture", "x86_64"),
        ("source_asset_identity", ""),
        ("archive_size_bytes", 0),
        ("archive_size_bytes", -1),
        ("archive_sha256", "not-hex"),
        ("executable_relative_path", PurePosixPath("/abs/path")),
        ("executable_relative_path", PurePosixPath("../escape")),
        ("executable_sha256", "A" * 64),
    ],
)
def test_invalid_field_rejected(field: str, value: object) -> None:
    with pytest.raises(InstallReceiptError):
        _receipt(**{field: value})


def test_tampered_member_checksum_detected_via_recomputation() -> None:
    """A receipt itself validates structurally; detecting *tampered on-disk
    content* against a valid receipt is the provisioning service's job
    (recomputing and comparing against member_checksums), not this domain
    type's — this test documents that boundary."""
    receipt = _receipt()
    tampered_map = receipt.member_checksum_map()
    tampered_map[EXE_PATH] = "f" * 64
    assert tampered_map[EXE_PATH] != receipt.member_checksum_map()[EXE_PATH]
