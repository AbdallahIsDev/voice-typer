"""§8.1 — Partial download resume.

Spec (plan-runtime-pack-split.md §8.1):

  The pack downloader reuses the resume pattern from
  ``service/model.py:_download_whisper_family``. The partial file is
  saved as ``pack-<version>.partial``; on next launch, it continues
  from the byte offset. The partial file is never trusted — only a
  fully downloaded, checksum-verified pack is ever used.

Tested behaviors:

  1. A partial file at ``pack-<version>.partial`` causes the next
     download to send ``Range: bytes=<offset>-`` (the fake transport
     records the request offset).
  2. The resumed download appends to the partial (not truncates).
  3. The final SHA-256 covers the whole file (partial + appended
     bytes) — a wrong SHA-256 → False + partial deleted.
  4. A correct SHA-256 → True + ``pack_download_completed`` event.
  5. A truncated/corrupt partial (re-hash fails) restarts from 0.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack

# ── Fake transport ─────────────────────────────────────────────────────


def _make_fake_transport(full_body: bytes, *, expected_sha256: str):
    """Build a fake ``http_get`` callable for ``download_pack_with_resume``.

    The fake:

    * Records the requested ``offset`` on each call (so the test can
      assert the ``Range:`` header was sent).
    * Returns the body slice starting at ``offset`` (mirrors HTTP 206).
    * Reports ``Content-Length`` as the remaining-body size.
    """
    calls: list[dict] = []

    def fake(url, *, offset=0):
        calls.append({"url": url, "offset": offset})
        if offset > len(full_body):
            raise RuntimeError(f"offset {offset} past EOF {len(full_body)}")
        remaining = full_body[offset:]
        return {
            "content_length": len(remaining),
            "iter_chunks": lambda chunk_bytes: _iter_chunks(remaining, chunk_bytes),
        }

    return fake, calls


def _iter_chunks(buf: bytes, chunk_bytes: int):
    pos = 0
    while pos < len(buf):
        nxt = min(pos + chunk_bytes, len(buf))
        yield buf[pos:nxt]
        pos = nxt


# ── Tests ──────────────────────────────────────────────────────────────


class TestResumeFromPartial:
    """§8.1 — a partial file triggers a resumed download."""

    def test_resume_sends_range_header(self, tmp_path: Path):
        """A partial file at ``pack-v1.partial`` causes the next call to
        request ``offset=<partial size>``."""
        full = b"x" * 4096
        expected = hashlib.sha256(full).hexdigest()
        partial = tmp_path / "pack-v1.partial"
        partial.write_bytes(full[:1024])  # 1024 bytes already on disk
        fake, calls = _make_fake_transport(full, expected_sha256=expected)
        dest = tmp_path / "pack-v1.partial"
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        assert ok
        assert calls[0]["offset"] == 1024

    def test_resume_appends_not_truncates(self, tmp_path: Path):
        """The resumed download appends to the existing partial (not
        overwrites from 0)."""
        full = b"abcdefghijklmnopqrstuvwxyz" * 100
        expected = hashlib.sha256(full).hexdigest()
        partial = tmp_path / "pack-v1.partial"
        prefix = full[:500]
        partial.write_bytes(prefix)
        fake, _ = _make_fake_transport(full, expected_sha256=expected)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        assert ok
        assert partial.read_bytes() == full  # full file present

    def test_wrong_sha256_deletes_partial(self, tmp_path: Path):
        """A wrong SHA-256 → False + partial deleted."""
        full = b"hello world" * 1000
        wrong_sha = "0" * 64  # obviously wrong
        partial = tmp_path / "pack-v1.partial"
        partial.write_bytes(full[:200])  # partial
        fake, _ = _make_fake_transport(full, expected_sha256=wrong_sha)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=wrong_sha,
            version="v1",
            http_get=fake,
        )
        assert ok is False
        assert not partial.exists()

    def test_correct_sha256_publishes_completed(self, tmp_path: Path):
        """A correct SHA-256 → True + ``pack_download_completed`` event."""
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        full = b"good" * 1000
        expected = hashlib.sha256(full).hexdigest()
        dest = tmp_path / "pack-v1.partial"
        fake, _ = _make_fake_transport(full, expected_sha256=expected)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            event_bus=FakeBus(),
            http_get=fake,
        )
        assert ok
        completed = [e for e in events if e["type"] == "offline_pack_download_completed"]
        assert completed
        assert completed[0]["data"]["version"] == "v1"
        assert completed[0]["data"]["sha256"] == expected

    def test_corrupt_partial_restarts_from_zero(self, tmp_path: Path, monkeypatch):
        """A partial whose on-disk bytes are UNREADABLE (corrupt inode,
        permission denied, etc.) MUST be discarded and the download
        restarted from offset 0.

        The implementation tries to re-hash the existing partial; if
        the open/read raises ``OSError``, it deletes the partial and
        starts from 0.
        """
        full = b"abc" * 500
        expected = hashlib.sha256(full).hexdigest()
        partial = tmp_path / "pack-v1.partial"
        partial.write_bytes(b"BAD" * 100)  # garbage on disk
        fake, calls = _make_fake_transport(full, expected_sha256=expected)

        # Make ``partial.open("rb")`` raise OSError on the FIRST call
        # only (the resume-read path). The next open (for "wb"/"ab")
        # must succeed so the download can write the new bytes.
        real_open = Path.open
        call_count = {"n": 0}

        def fake_open(self, mode, *args, **kwargs):
            if self == partial and "b" in mode and "r" in mode:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise OSError("simulated read failure on resume")
            return real_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        # The download should have restarted from offset 0 (since the
        # resume-read failed).
        assert calls[0]["offset"] == 0
        # And the final file should match the full body.
        assert ok
        assert partial.read_bytes() == full


# ── Status-code contract on resume (206 required / 416 = complete) ───────
#
# The status-aware transport (the default ``_http_get_streaming`` and any
# test fake that wants to exercise the contract) reports an explicit
# ``status`` key. Responses WITHOUT the key keep the legacy append
# semantics (they model a well-behaved 206 responder).


def _make_status_transport(body_by_offset: dict, *, statuses: dict):
    """Build a status-aware fake transport.

    ``statuses`` maps the response status to return for a given call
    index (0-based). ``body_by_offset`` maps requested offset → body
    bytes served for that call.
    """

    calls: list[dict] = []

    def fake(url, *, offset=0):
        calls.append({"url": url, "offset": offset})
        idx = len(calls) - 1
        status = statuses.get(idx, 200)
        body = body_by_offset[offset]
        return {
            "status": status,
            "content_length": len(body),
            "iter_chunks": lambda chunk_bytes: _iter_chunks(body, chunk_bytes),
        }

    return fake, calls


class TestResumeStatusContract:
    """Resume requires HTTP 206; a 416 whose total matches the partial
    means the file is already complete."""

    def test_resume_with_206_appends(self, tmp_path: Path):
        """A 206 response (partial content) appends at the offset — the
        normal, well-behaved resume path."""
        full = b"z" * 2048
        expected = hashlib.sha256(full).hexdigest()
        dest = tmp_path / "pack-v1.partial"
        dest.write_bytes(full[:512])
        fake, calls = _make_status_transport(
            {512: full[512:], 0: full},
            statuses={0: 206},
        )

        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )

        assert ok is True
        assert len(calls) == 1
        assert calls[0]["offset"] == 512
        assert dest.read_bytes() == full

    def test_resume_with_200_restarts_from_zero(self, tmp_path: Path):
        """A 200 (server ignored the Range header) MUST NOT be appended
        after the existing partial — that corrupts the file. The download
        restarts from byte 0 with a fresh write."""
        full = b"a" * 3000
        expected = hashlib.sha256(full).hexdigest()
        dest = tmp_path / "pack-v1.partial"
        dest.write_bytes(full[:700])
        # Both calls return the FULL body (a Range-ignoring server).
        fake, calls = _make_status_transport(
            {700: full, 0: full},
            statuses={0: 200, 1: 200},
        )

        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )

        assert ok is True
        # First call resumed, second restarted from 0.
        assert calls[0]["offset"] == 700
        assert calls[1]["offset"] == 0
        # No corrupt append: the final file is exactly the full body.
        assert dest.read_bytes() == full

    def test_416_with_matching_total_is_already_complete(self, tmp_path: Path):
        """A 416 whose Content-Range total equals the partial size means
        the pack is fully downloaded — verify + succeed WITHOUT a second
        request or any body transfer."""
        full = b"q" * 1024
        expected = hashlib.sha256(full).hexdigest()
        dest = tmp_path / "pack-v1.partial"
        dest.write_bytes(full)
        consumed = {"chunks_read": False}

        def fake(url, *, offset=0):
            return {
                "status": 416,
                "content_length": len(full),
                "iter_chunks": lambda chunk_bytes: _boom_iter(consumed),
            }

        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )

        assert ok is True
        # No body was consumed — the file was already complete.
        assert consumed["chunks_read"] is False
        assert dest.read_bytes() == full

    def test_416_complete_but_wrong_sha_deletes_partial(self, tmp_path: Path):
        """Already-complete short-circuit still enforces the SHA-256 —
        a mismatching local file is discarded (next launch re-downloads)."""
        full = b"w" * 512
        dest = tmp_path / "pack-v1.partial"
        dest.write_bytes(full)

        def fake(url, *, offset=0):
            return {"status": 416, "content_length": len(full)}

        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256="0" * 64,  # wrong
            version="v1",
            http_get=fake,
        )

        assert ok is False
        assert not dest.exists()

    def test_416_with_larger_total_restarts(self, tmp_path: Path):
        """A 416 whose total does NOT match the partial (partial larger
        than the file — corrupt) restarts from byte 0."""
        full = b"b" * 400
        expected = hashlib.sha256(full).hexdigest()
        dest = tmp_path / "pack-v1.partial"
        dest.write_bytes(b"garbage" * 200)  # 1400 bytes — larger than the file
        fake, calls = _make_status_transport(
            {1400: full, 0: full},
            statuses={0: 416, 1: 200},
        )

        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )

        assert ok is True
        assert calls[0]["offset"] == 1400
        assert calls[1]["offset"] == 0
        assert dest.read_bytes() == full

    def test_416_at_offset_zero_raises(self, tmp_path: Path):
        """A 416 for a zero-offset request is a server anomaly — surface
        it instead of looping."""

        def fake(url, *, offset=0):
            return {"status": 416, "content_length": 100}

        with pytest.raises(RuntimeError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                tmp_path / "pack-v1.partial",
                expected_sha256="0" * 64,
                version="v1",
                http_get=fake,
            )


def _boom_iter(consumed: dict):
    """A chunk iterator that must never be pulled (marks if it is)."""
    consumed["chunks_read"] = True
    yield b"never"


# ── Default transport: resource + redirect discipline (proactive
# close-out — the response-close/SSRF-redirect-handler half of the
# offline-pack transport hardening)
# ─────────────────────────────────────────────────────────────────────────


class _FakeStreamingResponse:
    """A fake ``urllib`` response object tracking ``close()`` calls."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        import email.message

        self._body = body
        self._status = status
        self.headers = email.message.Message()
        self.headers["Content-Length"] = str(len(body))
        self.close_calls = 0

    def getcode(self) -> int:
        return self._status

    def read(self, size: int = -1) -> bytes:
        if size is None or size <= 0:
            buf, self._body = self._body, b""
        else:
            buf, self._body = self._body[:size], self._body[size:]
        return buf

    def close(self) -> None:
        self.close_calls += 1


class TestHttpStreamingResourceDiscipline:
    """``_http_get_streaming`` — the response is closed on EVERY exit
    path (rate-limit raise, non-200/206 raise, generator exhaustion,
    early generator close), and the opener installs the SSRF-aware
    redirect handler from ``update_check`` (imported — not copied).

    All network I/O is faked via a patched ``urllib.request.build_opener``
    — no real connections are made.
    """

    @staticmethod
    def _patch_build_opener(monkeypatch, response):
        """Patch ``build_opener`` to return an opener serving *response*.

        Returns the list of handler instances each call received (for
        the structural SSRF-handler pin).
        """
        import urllib.request

        handler_args: list[tuple] = []

        class _FakeOpener:
            def open(self, req, timeout=None):  # noqa: ARG002
                return response

        def fake_build_opener(*handlers):
            handler_args.append(handlers)
            return _FakeOpener()

        monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
        return handler_args

    def test_non_200_raise_path_closes_response(self, monkeypatch):
        """A non-200/206 status raises AND closes the response — the
        socket is not left to GC."""
        resp = _FakeStreamingResponse(500)
        self._patch_build_opener(monkeypatch, resp)

        with pytest.raises(RuntimeError, match="unexpected HTTP status"):
            offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")

        assert resp.close_calls == 1, "response was not closed on the non-200 raise path"

    def test_rate_limit_raise_path_closes_response(self, monkeypatch):
        """A 403 raises ``_RateLimitedError`` AND closes the response."""
        resp = _FakeStreamingResponse(403)
        resp.headers["X-RateLimit-Reset"] = "1234"
        self._patch_build_opener(monkeypatch, resp)

        from voice_typer.server.service.offline_pack import _RateLimitedError

        with pytest.raises(_RateLimitedError):
            offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")

        assert resp.close_calls == 1, "response was not closed on the rate-limit raise path"

    def test_open_installs_ssrf_aware_redirect_handler(self, monkeypatch):
        """Structural pin: the opener is built with update_check's
        ``_SSRFAwareRedirectHandler`` (imported — E7, not copied), so a
        3xx hop is re-validated on the body-download path too."""
        from voice_typer.server.service.update_check import _SSRFAwareRedirectHandler

        resp = _FakeStreamingResponse(200, b"body")
        handler_args = self._patch_build_opener(monkeypatch, resp)

        offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")

        assert handler_args, "build_opener was never called"
        assert any(isinstance(h, _SSRFAwareRedirectHandler) for h in handler_args[0]), (
            "the pack body-download opener must install the SSRF-aware redirect handler"
        )

    def test_generator_closes_response_on_exhaustion(self, monkeypatch):
        """Consuming the body to EOF closes the response (the
        generator's ``finally``) — not just the next GC cycle."""
        resp = _FakeStreamingResponse(200, b"chunk-of-bytes")
        self._patch_build_opener(monkeypatch, resp)

        result = offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")
        assert resp.close_calls == 0, "response must stay open until the body is consumed"
        chunks = list(result["iter_chunks"](4))
        assert chunks == [b"chun", b"k-of", b"-byt", b"es"]
        assert resp.close_calls == 1, "response was not closed after body exhaustion"

    def test_generator_closes_response_on_early_close(self, monkeypatch):
        """An abandoned generator (caller ``close()`` — e.g. the disk-full
        path in ``_stream_response_to_disk``) closes the response without
        exhausting the body."""
        resp = _FakeStreamingResponse(200, b"chunk-of-bytes")
        self._patch_build_opener(monkeypatch, resp)

        result = offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")
        chunk_iter = result["iter_chunks"](4)
        assert next(chunk_iter) == b"chun"
        assert resp.close_calls == 0
        chunk_iter.close()
        assert resp.close_calls == 1, "closing the generator must close the response"

    def test_stream_to_disk_closes_the_transport_iterator(self, monkeypatch, tmp_path: Path):
        """``_stream_response_to_disk`` deterministically closes the
        transport iterator when the write loop is abandoned (disk full)
        — the transport generator's ``finally`` runs then, not at GC.

        The write failure is simulated by making ``dest.open`` raise
        after the first chunk is pulled.
        """
        resp = _FakeStreamingResponse(200, b"chunk-of-bytes")
        self._patch_build_opener(monkeypatch, resp)

        result = offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")
        dest = tmp_path / "pack-v1.partial"

        class _BoomAfterFirstWrite:
            def __init__(self) -> None:
                self._real = None
                self._n = 0

            def write(self, data):
                self._n += 1
                if self._n > 1:
                    raise OSError("simulated disk-full on second write")
                return len(data)

            def __enter__(self):
                # ``real_open`` bypasses the patched ``Path.open`` — no
                # recursion into ``fake_open``.
                self._real = real_open(dest, "wb")
                return self

            def __exit__(self, *args):
                if self._real is not None:
                    self._real.close()

        real_open = Path.open

        def fake_open(self, *args, **kwargs):
            if self == dest:
                return _BoomAfterFirstWrite()
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        import hashlib as _hashlib

        with pytest.raises(offline_pack.OfflinePackDiskFullError):
            offline_pack._stream_response_to_disk(
                result,
                dest,
                _hashlib.sha256(),
                offset=0,
                downloaded_bytes=0,
                total_bytes=len(b"chunk-of-bytes"),
                version="v1",
                event_bus=None,
                chunk_bytes=4,
                attempt=1,
            )
        assert resp.close_calls == 1, (
            "the transport response must be closed deterministically when the write loop aborts"
        )

    def test_redirect_to_private_ip_is_rejected(self, monkeypatch):
        """Behavioral pin: a 3xx redirect to a private IP on the
        body-download path raises ``RuntimeError`` (SSRF block) — the
        SSRF-aware handler actually routes, not just installs.

        This test FAILS on revert: with urllib's default redirect
        handler the redirect would be followed (the fake HTTP handler
        raises ``URLError`` instead, and no ``RuntimeError(SSRF)`` is
        ever raised).
        """
        import email.message
        import urllib.error
        import urllib.request

        redirect_target = "http://10.0.0.5/evil"

        class _FakeRedirectResponse:
            def __init__(self, location: str) -> None:
                self._headers = email.message.Message()
                self._headers["Location"] = location
                # ``HTTPErrorProcessor.https_response`` reads ``code`` /
                # ``msg`` / ``info()`` off the response before routing to
                # the redirect handler's ``http_error_302``.
                self.code = 302
                self.msg = "Found"

            def getcode(self) -> int:
                return 302

            def info(self):
                return self._headers

            def read(self, size: int = -1) -> bytes:  # noqa: ARG002
                return b""

            def close(self) -> None:
                pass

        class _FakeHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):  # noqa: ARG002
                return _FakeRedirectResponse(redirect_target)

        class _FakeHTTPHandler(urllib.request.HTTPHandler):
            def http_open(self, req):  # noqa: ARG002
                raise urllib.error.URLError(
                    f"test: refusing to follow redirect to HTTP target {redirect_target!r} (no real network in tests)"
                )

        real_build_opener = urllib.request.build_opener

        def fake_build_opener(*handlers):
            return real_build_opener(_FakeHTTPSHandler(), _FakeHTTPHandler(), *handlers)

        monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

        # The initial URL is allowlisted (HTTPS + GitHub host); only the
        # redirect target is hostile.
        with pytest.raises(RuntimeError, match="SSRF"):
            offline_pack._http_get_streaming("https://github.com/owner/repo/pack.zip")


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
