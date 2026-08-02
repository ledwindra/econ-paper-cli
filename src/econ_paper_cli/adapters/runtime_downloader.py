"""Stdlib-only HTTPS downloader for managed runtime artifacts.

No third-party HTTP client dependency is introduced — ``urllib.request`` is
sufficient and keeps this project's zero-network-library footprint intact
outside of this one explicit provisioning boundary.
"""

import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from econ_paper_cli.protocols.runtime_provisioning import (
    DownloadNetworkError,
    DownloadSizeExceededError,
    DownloadTimeoutError,
    DownloadTruncatedError,
    InsecureURLError,
    TooManyRedirectsError,
    UntrustedRedirectHostError,
)

_DEFAULT_CHUNK_SIZE = 65536
_DEFAULT_MAX_REDIRECTS = 3
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Official GitHub release assets (the only source pinned artifacts are
# downloaded from) redirect to GitHub-owned asset hosts. A redirect
# anywhere else is refused rather than silently followed, per the approved
# issue #58 plan.
DEFAULT_TRUSTED_REDIRECT_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


class _StreamResponse(Protocol):
    """The minimal surface this adapter needs from an HTTP response."""

    def read(self, amount: int) -> bytes: ...
    def close(self) -> None: ...
    def __enter__(self) -> "_StreamResponse": ...
    def __exit__(self, *args: object) -> None: ...


class _Opener(Protocol):
    """The minimal surface this adapter needs from an opener."""

    def open(self, url: str, *, timeout: float) -> _StreamResponse: ...


class _BoundedHTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows only a bounded number of redirects, all HTTPS and host-trusted.

    Counts independently of urllib's own (harder-to-introspect) redirect
    bookkeeping so ``TooManyRedirectsError`` is raised deterministically.
    """

    def __init__(self, max_redirects: int, trusted_hosts: frozenset[str]) -> None:
        self._max_redirects = max_redirects
        self._trusted_hosts = trusted_hosts
        self._redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not newurl.lower().startswith("https://"):
            raise InsecureURLError(
                f"Refusing to follow redirect to a non-HTTPS URL: '{newurl}'."
            )
        host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
        if host not in self._trusted_hosts:
            raise UntrustedRedirectHostError(
                f"Refusing to follow redirect to untrusted host '{host}': '{newurl}'."
            )
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            raise TooManyRedirectsError(
                f"Exceeded the maximum of {self._max_redirects} redirects "
                f"while downloading."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener_factory(
    max_redirects: int,
    trusted_hosts: frozenset[str] = DEFAULT_TRUSTED_REDIRECT_HOSTS,
) -> _Opener:
    return urllib.request.build_opener(
        _BoundedHTTPSRedirectHandler(max_redirects, trusted_hosts)
    )


class UrllibDownloader:
    """Streams one HTTPS URL to a local file, enforcing size/redirect/timeout limits."""

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        max_redirects: int = _DEFAULT_MAX_REDIRECTS,
        trusted_redirect_hosts: frozenset[str] = DEFAULT_TRUSTED_REDIRECT_HOSTS,
        opener_factory: Callable[[int], _Opener] | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._chunk_size = chunk_size
        self._max_redirects = max_redirects
        self._opener_factory = opener_factory or (
            lambda max_redirects: _default_opener_factory(
                max_redirects, trusted_redirect_hosts
            )
        )

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size_bytes: int,
    ) -> None:
        if not url.lower().startswith("https://"):
            raise InsecureURLError(
                f"Refusing to download from a non-HTTPS URL: '{url}'."
            )

        opener = self._opener_factory(self._max_redirects)
        try:
            response = opener.open(url, timeout=self._timeout_seconds)
        except (InsecureURLError, TooManyRedirectsError):
            raise
        except urllib.error.HTTPError as error:
            raise DownloadNetworkError(
                f"HTTP error downloading '{url}': {error}."
            ) from error
        except (TimeoutError, socket.timeout) as error:
            raise DownloadTimeoutError(f"Timed out downloading '{url}'.") from error
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            raise DownloadNetworkError(
                f"Network error downloading '{url}': {error}."
            ) from error

        bytes_written = 0
        try:
            with response, open(destination, "wb") as out_file:
                while True:
                    chunk = response.read(self._chunk_size)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > expected_size_bytes:
                        raise DownloadSizeExceededError(
                            f"Download of '{url}' exceeded the expected size of "
                            f"{expected_size_bytes} bytes mid-transfer."
                        )
                    out_file.write(chunk)
            if bytes_written != expected_size_bytes:
                raise DownloadTruncatedError(
                    f"Download of '{url}' produced {bytes_written} bytes; "
                    f"expected exactly {expected_size_bytes}."
                )
        except (
            DownloadSizeExceededError,
            DownloadTimeoutError,
            DownloadTruncatedError,
        ):
            _cleanup(destination)
            raise
        except (TimeoutError, socket.timeout) as error:
            _cleanup(destination)
            raise DownloadTimeoutError(f"Timed out reading '{url}'.") from error
        except (OSError, http.client.HTTPException) as error:
            _cleanup(destination)
            raise DownloadNetworkError(
                f"Network or OS error downloading '{url}': {error}."
            ) from error


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
