"""Adapter tests for safe managed-runtime archive extraction.

Covers path traversal, absolute paths, symlink escape, duplicate members,
and unexpected layouts against crafted in-memory archives — no download or
network involved.
"""

import tarfile
import zipfile
from pathlib import Path

import pytest

from econ_paper_cli.adapters.runtime_extractor import SafeArchiveExtractor
from econ_paper_cli.domain.runtime_manifest import ArchiveFormat
from econ_paper_cli.protocols.runtime_provisioning import (
    ExtractionError,
    UnsafeArchiveMemberError,
    UnsupportedArchiveFormatError,
)


def _make_tar_gz(
    path: Path, members: list[tarfile.TarInfo], contents: dict[str, bytes]
) -> None:
    import io

    with tarfile.open(path, mode="w:gz") as archive:
        for member in members:
            data = contents.get(member.name, b"")
            if member.isfile():
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)


def _tar_file_member(name: str, data: bytes = b"x") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.type = tarfile.REGTYPE
    return info


def _tar_symlink_member(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


def test_extract_valid_tar_gz_writes_expected_files(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    members = [_tar_file_member("bin/tool", b"hello")]
    _make_tar_gz(archive_path, members, {"bin/tool": b"hello"})
    destination = tmp_path / "dest"
    destination.mkdir()

    SafeArchiveExtractor().extract(archive_path, ArchiveFormat.TAR_GZ, destination)

    assert (destination / "bin" / "tool").read_bytes() == b"hello"


def test_tar_absolute_path_member_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    _make_tar_gz(archive_path, [_tar_file_member("/etc/passwd")], {"/etc/passwd": b"x"})
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.TAR_GZ, destination)


def test_tar_parent_traversal_member_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    _make_tar_gz(
        archive_path,
        [_tar_file_member("../../escape")],
        {"../../escape": b"x"},
    )
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.TAR_GZ, destination)


def test_tar_symlink_member_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    _make_tar_gz(archive_path, [_tar_symlink_member("link", "/etc/passwd")], {})
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.TAR_GZ, destination)


def test_tar_duplicate_member_names_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    _make_tar_gz(
        archive_path,
        [_tar_file_member("bin/tool", b"one"), _tar_file_member("bin/tool", b"two")],
        {},
    )
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.TAR_GZ, destination)


def test_tar_malformed_archive_raises_extraction_error(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    archive_path.write_bytes(b"not a real tar.gz")
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(ExtractionError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.TAR_GZ, destination)


# --- Zip ---------------------------------------------------------------


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def test_extract_valid_zip_writes_expected_files(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    _make_zip(archive_path, {"bin/tool.exe": b"hello"})
    destination = tmp_path / "dest"
    destination.mkdir()

    SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)

    assert (destination / "bin" / "tool.exe").read_bytes() == b"hello"


def test_zip_absolute_path_member_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    _make_zip(archive_path, {"/etc/passwd": b"x"})
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)


def test_zip_drive_rooted_path_member_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    _make_zip(archive_path, {"C:/Windows/evil.exe": b"x"})
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)


def test_zip_parent_traversal_member_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    _make_zip(archive_path, {"../../escape.txt": b"x"})
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)


def test_zip_duplicate_member_names_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("bin/tool.exe", b"one")
        archive.writestr("bin/tool.exe", b"two")
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)


def test_zip_symlink_member_rejected(tmp_path: Path) -> None:
    import stat as stat_module

    archive_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat_module.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsafeArchiveMemberError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)


def test_zip_malformed_archive_raises_extraction_error(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(b"not a real zip")
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(ExtractionError):
        SafeArchiveExtractor().extract(archive_path, ArchiveFormat.ZIP, destination)


def test_unsupported_archive_format_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.bin"
    archive_path.write_bytes(b"x")
    destination = tmp_path / "dest"
    destination.mkdir()

    with pytest.raises(UnsupportedArchiveFormatError):
        SafeArchiveExtractor().extract(archive_path, "unknown", destination)  # type: ignore[arg-type]
