"""Safe archive extraction for managed runtime provisioning.

Rejects absolute paths, parent-directory traversal, and duplicate member
names before anything is written to disk. Relative symlink/hardlink members
are validated and materialized as regular files, so installed bundles never
contain links that could later be redirected outside the bundle.
Validates every member before writing and extracts tar members explicitly;
this avoids relying on platform-specific link handling and ensures the
installed staging tree contains regular files rather than links.

Duplicate-member and path-separator handling is Windows-safe *canonically*,
not just by raw string comparison: two distinct-looking member names that
would collide once actually extracted — case-only variants (case-insensitive
default filesystems on Windows/macOS), ``a/b`` vs. ``a\\b``, a trailing
dot/space Windows silently strips, or an NTFS alternate-data-stream suffix —
are all rejected, so this holds even when the extractor itself runs on a
case-sensitive Linux CI host.
"""

import posixpath
import shutil
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
                    if not (
                        member.isfile()
                        or member.isdir()
                        or member.issym()
                        or member.islnk()
                    ):
                        raise UnsafeArchiveMemberError(
                            f"Archive member '{member.name}' is not a regular "
                            "file, directory, symlink, or hardlink."
                        )
                normalized_members = {
                    _normalized_member_name(member.name): member for member in members
                }
                _validate_tar_links(normalized_members)
                _extract_tar_members(
                    archive, members, normalized_members, destination_dir
                )
        except tarfile.TarError as error:
            raise ExtractionError(
                f"Failed to extract tar archive '{archive_path}': {error}."
            ) from error
        except OSError as error:
            raise ExtractionError(
                f"Filesystem error extracting tar archive '{archive_path}': {error}."
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
        except OSError as error:
            raise ExtractionError(
                f"Filesystem error extracting zip archive '{archive_path}': {error}."
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


def _normalized_member_name(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).as_posix()


def _extract_tar_members(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    normalized_members: dict[str, tarfile.TarInfo],
    destination_dir: Path,
) -> None:
    for member in members:
        target_path = destination_dir / _normalized_member_name(member.name)
        if member.isdir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_member = member
        if member.issym() or member.islnk():
            source_name = _resolve_link_target(member, normalized_members)
            source_member = normalized_members[source_name]
        if not source_member.isfile():
            raise UnsafeArchiveMemberError(
                f"Archive link '{member.name}' does not resolve to a regular file."
            )

        extracted = archive.extractfile(source_member)
        if extracted is None:
            raise ExtractionError(
                f"Archive member '{source_member.name}' has no readable content."
            )
        with extracted, target_path.open("wb") as output:
            shutil.copyfileobj(extracted, output)
        target_path.chmod(source_member.mode & 0o7777)


def _validate_tar_links(
    normalized_members: dict[str, tarfile.TarInfo],
) -> None:
    """Resolve every tar link before extraction can write any member."""
    for member in normalized_members.values():
        if member.issym() or member.islnk():
            _resolve_link_target(member, normalized_members)


def _resolve_link_target(
    member: tarfile.TarInfo,
    normalized_members: dict[str, tarfile.TarInfo],
) -> str:
    current_name = _normalized_member_name(member.name)
    seen: set[str] = set()
    while True:
        if current_name in seen:
            raise UnsafeArchiveMemberError(
                f"Archive link '{member.name}' contains a link cycle."
            )
        seen.add(current_name)
        current_member = normalized_members.get(current_name)
        if current_member is None:
            raise UnsafeArchiveMemberError(
                f"Archive link '{member.name}' targets missing member '{current_name}'."
            )
        if current_member.isfile():
            return current_name
        if not (current_member.issym() or current_member.islnk()):
            raise UnsafeArchiveMemberError(
                f"Archive link '{member.name}' does not target a regular file."
            )

        link_name = current_member.linkname.replace("\\", "/")
        if link_name.startswith("/") or (len(link_name) >= 2 and link_name[1] == ":"):
            raise UnsafeArchiveMemberError(
                f"Archive link '{member.name}' has an absolute target."
            )
        candidate = posixpath.normpath(
            posixpath.join(posixpath.dirname(current_name), link_name)
        )
        if candidate == ".." or candidate.startswith("../"):
            raise UnsafeArchiveMemberError(
                f"Archive link '{member.name}' targets outside the archive."
            )
        current_name = candidate


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
