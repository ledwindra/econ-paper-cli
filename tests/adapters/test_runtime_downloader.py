"""Adapter tests for the stdlib-only managed-runtime downloader.

Exercises success, size-mismatch/exceeded, checksum-relevant truncation,
network failure, and cleanup entirely against a fake opener/response —
no real socket is used.
"""

from pathlib import Path

import pytest

from econ_paper_cli.adapters.runtime_downloader import UrllibDownloader
from econ_paper_cli.protocols.runtime_provisioning import (
    DownloadNetworkError,
    DownloadSizeExceededError,
    DownloadTimeoutError,
    DownloadTruncatedError,
    InsecureURLError,
    TooManyRedirectsError,
    UntrustedRedirectHostError,
)


class _FakeResponse:
    """A minimal in-memory stand-in for an HTTP response body."""

    def __init__(self, chunks: list[bytes], *, raise_on_read: Exception | None = None):
        self._chunks = list(chunks)
        self._raise_on_read = raise_on_read
        self.closed = False

    def read(self, amount: int) -> bytes:
        if self._raise_on_read is not None and not self._chunks:
            raise self._raise_on_read
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class _FakeOpener:
    def __init__(
        self,
        response: _FakeResponse | None = None,
        *,
        open_error: Exception | None = None,
    ):
        self._response = response
        self._open_error = open_error

    def open(self, url: str, *, timeout: float) -> _FakeResponse:
        if self._open_error is not None:
            raise self._open_error
        assert self._response is not None
        return self._response


def _downloader(opener: _FakeOpener) -> UrllibDownloader:
    return UrllibDownloader(opener_factory=lambda _max_redirects: opener)


def test_successful_download_writes_exact_bytes(tmp_path: Path) -> None:
    payload = b"a" * 100
    response = _FakeResponse([payload[:40], payload[40:70], payload[70:]])
    destination = tmp_path / "artifact.bin"

    _downloader(_FakeOpener(response)).download(
        "https://example.com/artifact.bin", destination, expected_size_bytes=100
    )

    assert destination.read_bytes() == payload
    assert response.closed is True


def test_insecure_url_rejected_before_any_open_attempt() -> None:
    opener = _FakeOpener(open_error=AssertionError("must not be called"))
    destination_marker = Path("unused")

    with pytest.raises(InsecureURLError):
        _downloader(opener).download(
            "http://example.com/artifact.bin",
            destination_marker,
            expected_size_bytes=10,
        )


def test_oversized_stream_aborted_mid_transfer(tmp_path: Path) -> None:
    response = _FakeResponse([b"a" * 60, b"b" * 60])
    destination = tmp_path / "artifact.bin"

    with pytest.raises(DownloadSizeExceededError):
        _downloader(_FakeOpener(response)).download(
            "https://example.com/artifact.bin", destination, expected_size_bytes=100
        )

    assert not destination.exists()


def test_truncated_stream_raises_and_cleans_up(tmp_path: Path) -> None:
    response = _FakeResponse([b"a" * 40])
    destination = tmp_path / "artifact.bin"

    with pytest.raises(DownloadTruncatedError):
        _downloader(_FakeOpener(response)).download(
            "https://example.com/artifact.bin", destination, expected_size_bytes=100
        )

    assert not destination.exists()


def test_network_failure_on_open_raises_and_writes_nothing(tmp_path: Path) -> None:
    import urllib.error

    opener = _FakeOpener(open_error=urllib.error.URLError("connection refused"))
    destination = tmp_path / "artifact.bin"

    with pytest.raises(DownloadNetworkError):
        _downloader(opener).download(
            "https://example.com/artifact.bin", destination, expected_size_bytes=100
        )

    assert not destination.exists()


def test_incomplete_read_http_exception_wrapped_and_cleans_up(tmp_path: Path) -> None:
    """http.client.HTTPException subtypes (e.g. IncompleteRead) are not
    OSError subclasses and must be explicitly wrapped, not left to escape
    the typed download boundary raw."""
    import http.client

    response = _FakeResponse(
        [b"a" * 40],
        raise_on_read=http.client.IncompleteRead(b"a" * 40, expected=60),
    )
    destination = tmp_path / "artifact.bin"

    with pytest.raises(DownloadNetworkError):
        _downloader(_FakeOpener(response)).download(
            "https://example.com/artifact.bin", destination, expected_size_bytes=100
        )

    assert not destination.exists()


def test_http_exception_on_open_wrapped_and_writes_nothing(tmp_path: Path) -> None:
    import http.client

    opener = _FakeOpener(open_error=http.client.BadStatusLine("garbage"))
    destination = tmp_path / "artifact.bin"

    with pytest.raises(DownloadNetworkError):
        _downloader(opener).download(
            "https://example.com/artifact.bin", destination, expected_size_bytes=100
        )

    assert not destination.exists()


def test_timeout_during_read_raises_and_cleans_up(tmp_path: Path) -> None:
    response = _FakeResponse([b"a" * 40], raise_on_read=TimeoutError("timed out"))
    destination = tmp_path / "artifact.bin"

    with pytest.raises(DownloadTimeoutError):
        _downloader(_FakeOpener(response)).download(
            "https://example.com/artifact.bin", destination, expected_size_bytes=100
        )

    assert not destination.exists()


def test_redirect_to_non_https_target_is_rejected() -> None:
    from econ_paper_cli.adapters.runtime_downloader import _BoundedHTTPSRedirectHandler

    handler = _BoundedHTTPSRedirectHandler(
        max_redirects=3, trusted_hosts=frozenset({"example.com"})
    )
    with pytest.raises(InsecureURLError):
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://example.com/evil"
        )


def test_redirect_to_untrusted_host_is_rejected() -> None:
    from econ_paper_cli.adapters.runtime_downloader import _BoundedHTTPSRedirectHandler

    handler = _BoundedHTTPSRedirectHandler(
        max_redirects=3, trusted_hosts=frozenset({"github.com"})
    )
    with pytest.raises(UntrustedRedirectHostError):
        handler.redirect_request(
            None, None, 302, "Found", {}, "https://evil.example.com/payload"
        )


def test_too_many_redirects_is_rejected() -> None:
    from unittest.mock import patch

    from econ_paper_cli.adapters.runtime_downloader import _BoundedHTTPSRedirectHandler

    handler = _BoundedHTTPSRedirectHandler(
        max_redirects=2, trusted_hosts=frozenset({"example.com"})
    )
    with patch(
        "urllib.request.HTTPRedirectHandler.redirect_request",
        return_value=None,
    ):
        handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/1")
        handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/2")
        with pytest.raises(TooManyRedirectsError):
            handler.redirect_request(
                None, None, 302, "Found", {}, "https://example.com/3"
            )


def test_default_downloader_rejects_redirect_to_untrusted_host() -> None:
    """End-to-end (real default opener_factory, no injected fake), proving
    the trusted-host default policy actually applies when a caller doesn't
    override opener_factory at all."""
    from econ_paper_cli.adapters.runtime_downloader import (
        DEFAULT_TRUSTED_REDIRECT_HOSTS,
        _BoundedHTTPSRedirectHandler,
        _default_opener_factory,
    )

    opener = _default_opener_factory(3, DEFAULT_TRUSTED_REDIRECT_HOSTS)
    handler = next(
        h for h in opener.handlers if isinstance(h, _BoundedHTTPSRedirectHandler)
    )
    with pytest.raises(UntrustedRedirectHostError):
        handler.redirect_request(
            None, None, 302, "Found", {}, "https://not-github.example.com/evil"
        )
