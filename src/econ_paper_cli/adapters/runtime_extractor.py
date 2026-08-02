"""Safe archive extraction for managed runtime provisioning.

Rejects absolute paths, parent-directory traversal, symlink/hardlink
members, and duplicate member names before anything is written to disk.
Uses ``tarfile``'s PEP 706 ``filter="data"`` extraction as defense in depth
alongside this module's own explicit member validation (which runs first
and is the primary defense, since it produces the specific
``UnsafeArchiveMemberError`` these tests assert on rather than a generic
``tarfile.FilterError``).

Duplicate-member and path-separator handling is Windows-safe *canonically*,
not just by raw string comparison: two distinct-looking member names that
would collide once actually extracted — case-only variants (case-insensitive
default filesystems on Windows/macOS), ``a/b`` vs. ``a\\b``, a trailing
dot/space Windows silently strips, or an NTFS alternate-data-stream suffix —
are all rejected, so this holds even when the extractor itself runs on a
case-sensitive Linux CI host.
"""

import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from econ_paper_cli.domain.runtime_manifest import ArchiveFormat
from econ_paper_cli.protocols.runtime_provisioning import (
    ExtractionError,
    UnsafeArchiveMemberError,
    UnsupportedArchiveFormatError,
)

_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class SafeArchiveExtractor:
    """Extracts ``tar.gz``/``zip`` archives, rejecting unsafe members."""

    def extract(
        self,
        archive_path: Path,
        archive_format: ArchiveFormat,
        destination_dir: Path,
    ) -> None:
        if archive_format is ArchiveFormat.TAR_GZ:
            self._extract_tar(archive_path, destination_dir)
        elif archive_format is ArchiveFormat.ZIP:
            self._extract_zip(archive_path, destination_dir)
        else:
            raise UnsupportedArchiveFormatError(
                f"Unsupported archive format: {archive_format!r}."
            )

    def _extract_tar(self, archive_path: Path, destination_dir: Path) -> None:
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                members = archive.getmembers()
                seen_names: set[str] = set()
                for member in members:
                    _validate_member_name(member.name, destination_dir, seen_names)
                    if member.issym() or member.islnk():
                        raise UnsafeArchiveMemberError(
                            f"Archive member '{member.name}' is a symlink/hardlink, "
                            "which is not allowed."
                        )
                    if not (member.isfile() or member.isdir()):
                        raise UnsafeArchiveMemberError(
                            f"Archive member '{member.name}' is not a regular "
                            "file or directory."
                        )
                archive.extractall(destination_dir, members=members, filter="data")
        except tarfile.TarError as error:
            raise ExtractionError(
                f"Failed to extract tar archive '{archive_path}': {error}."
            ) from error

    def _extract_zip(self, archive_path: Path, destination_dir: Path) -> None:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                seen_names: set[str] = set()
                for info in infos:
                    _validate_member_name(info.filename, destination_dir, seen_names)
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(unix_mode):
                        raise UnsafeArchiveMemberError(
                            f"Archive member '{info.filename}' is a symlink, "
                            "which is not allowed."
                        )
                archive.extractall(destination_dir, members=infos)
        except zipfile.BadZipFile as error:
            raise ExtractionError(
                f"Failed to extract zip archive '{archive_path}': {error}."
            ) from error


def _validate_member_name(
    name: str,
    destination_dir: Path,
    seen_canonical_keys: set[str],
) -> None:
    if not name:
        raise UnsafeArchiveMemberError("Archive contains an empty member name.")

    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        raise UnsafeArchiveMemberError(
            f"Archive member '{name}' has an absolute or drive-rooted path."
        )
    posix_name = PurePosixPath(normalized)
    if ".." in posix_name.parts:
        raise UnsafeArchiveMemberError(
            f"Archive member '{name}' attempts parent-directory traversal."
        )
    _validate_portable_parts(name, posix_name.parts)

    # Duplicate detection uses a *canonical* key — case-folded (Windows/
    # macOS default filesystems are case-insensitive) and with Windows'
    # trailing dot/space stripped per component — not the raw member name,
    # so distinct-looking members that would collide once actually
    # extracted are caught even on a case-sensitive Linux CI host.
    canonical_key = "/".join(part.rstrip(" .").casefold() for part in posix_name.parts)
    if canonical_key in seen_canonical_keys:
        raise UnsafeArchiveMemberError(
            f"Archive member '{name}' collides with another member once "
            "canonicalized (case, separator, or trailing dot/space)."
        )
    seen_canonical_keys.add(canonical_key)

    destination_root = destination_dir.resolve()
    resolved_target = (destination_root / posix_name).resolve()
    if (
        resolved_target != destination_root
        and destination_root not in resolved_target.parents
    ):
        raise UnsafeArchiveMemberError(
            f"Archive member '{name}' resolves outside the destination directory."
        )


def _validate_portable_parts(name: str, parts: tuple[str, ...]) -> None:
    for part in parts:
        reserved_name = part.split(".", maxsplit=1)[0].upper()
        if (
            any(ord(character) < 32 for character in part)
            or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part)
            or part.endswith((" ", "."))
            or reserved_name in _WINDOWS_RESERVED_NAMES
        ):
            raise UnsafeArchiveMemberError(
                f"Archive member '{name}' contains a path component that is not "
                "portable across Windows, macOS, and Linux (forbidden character, "
                "reserved device name, alternate-data-stream syntax, or a "
                "trailing dot/space)."
            )
