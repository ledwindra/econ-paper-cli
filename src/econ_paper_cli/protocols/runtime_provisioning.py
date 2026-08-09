"""Replaceable download/extraction protocols for managed runtime provisioning.

Kept narrow and separate on purpose: ``Downloader`` lets tests simulate a
network without a real socket (success, size mismatch, checksum mismatch,
network failure, truncation), and ``ArchiveExtractor`` lets extraction-safety
tests run entirely against crafted in-memory archives with no network
involved at all.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from econ_paper_cli.domain.runtime_manifest import ArchiveFormat


class DownloadError(Exception):
    """Base exception for all managed-runtime download operations."""


class InsecureURLError(DownloadError):
    """Raised when a URL (initial or redirect target) is not HTTPS."""


class UntrustedRedirectHostError(DownloadError):
    """Raised when a redirect target's host is not on the trusted allowlist.

    Official GitHub release assets redirect to GitHub-owned asset hosts
    (e.g. ``objects.githubusercontent.com``); an HTTPS redirect to anywhere
    else is refused rather than silently followed.
    """


class TooManyRedirectsError(DownloadError):
    """Raised when a download follows more redirects than permitted."""


class DownloadTimeoutError(DownloadError):
    """Raised when a connection or read exceeds the configured timeout."""


class DownloadSizeExceededError(DownloadError):
    """Raised when a stream exceeds the manifest's expected size mid-transfer."""


class DownloadTruncatedError(DownloadError):
    """Raised when a stream ends before reaching the manifest's expected size."""


class DownloadNetworkError(DownloadError):
    """Raised when a connection fails for a reason other than the above."""


class ExtractionError(Exception):
    """Base exception for all managed-runtime archive extraction operations."""


class UnsafeArchiveMemberError(ExtractionError):
    """Raised for an archive member that is unsafe to extract.

    Covers absolute paths, parent-directory traversal, unsafe link targets,
    and duplicate member names. Relative symlink/hardlink members are
    materialized as regular files.
    """


class UnsupportedArchiveFormatError(ExtractionError):
    """Raised when the archive format is not one this adapter supports."""


@runtime_checkable
class Downloader(Protocol):
    """Streams one HTTPS URL to a local destination file."""

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size_bytes: int,
    ) -> None:
        """Stream ``url`` to ``destination``.

        Implementations must enforce HTTPS (initial URL and every redirect
        target), a bounded redirect count, a connect/read timeout, and an
        incremental byte cap at ``expected_size_bytes`` (aborting mid-stream
        rather than only checking after an unbounded download completes).
        Raises a ``DownloadError`` subtype on any failure; ``destination``
        must not be left containing a partial file on failure.
        """
        ...


@runtime_checkable
class ArchiveExtractor(Protocol):
    """Safely extracts one archive into a destination directory."""

    def extract(
        self,
        archive_path: Path,
        archive_format: ArchiveFormat,
        destination_dir: Path,
    ) -> None:
        """Extract every member of ``archive_path`` into ``destination_dir``.

        Raises ``UnsafeArchiveMemberError`` for any member that is an
        absolute path, attempts parent-directory traversal, contains an
        unsafe link target, or duplicates another member's name; relative
        symlink/hardlink members are materialized as regular files; raises
        ``UnsupportedArchiveFormatError`` for an unrecognized
        ``archive_format``; raises ``ExtractionError`` for any other
        malformed-archive failure.
        """
        ...
