"""HTTP download with resume + rate-limit + disk-full handling (§8.1, §8.7, §8.9)."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from voice_typer.server.branding import APP_NAME

from .core import (
    OFFLINE_PACK_RATE_LIMIT_BACKOFF_S,
    OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS,
    OfflinePackDiskFullError,
    OfflinePackRateLimitError,
)
from .events import _publish_event
from .gates import assert_offline_pack_url_allowed

log = logging.getLogger(__name__)


def download_offline_pack_with_resume(
    url: str,
    dest: Path,
    *,
    expected_sha256: str,
    version: str,
    event_bus: ModuleType | None = None,
    http_get: Callable[..., dict] | None = None,
    chunk_bytes: int = 1 << 20,
) -> bool:
    """Download *url* to *dest*, resuming from a partial file if present."""
    if http_get is None:
        http_get = _http_get_streaming
    assert_offline_pack_url_allowed(url)
    # Ensure the pack directory exists before opening the partial —
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Step 1, resume-state probe (existing partial size + a hasher
    offset, h = _probe_partial_for_resume(dest, chunk_bytes)
    downloaded_bytes = offset
    # Step 2, rate-limited request/stream loop (§8.7 + §8.1 status
    backoff_iter = iter(OFFLINE_PACK_RATE_LIMIT_BACKOFF_S)
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = http_get(url, offset=offset)
        except _RateLimitedError as exc:
            reset_at = exc.reset_at
            if attempt > OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS:
                _publish_event(
                    event_bus,
                    "offline_pack_download_failed",
                    {"version": version, "reason": "rate_limited", "attempts": attempt},
                )
                raise OfflinePackRateLimitError(
                    f"GitHub rate limit exhausted for pack {version}",
                    version=version,
                    reset_at=reset_at,
                ) from exc
            wait_s = next(backoff_iter, OFFLINE_PACK_RATE_LIMIT_BACKOFF_S[-1])
            if reset_at is not None:
                wait_s = max(wait_s, reset_at - time.time())
            log.warning(
                "[PACK] rate-limited on %s (attempt %d), sleeping %.1fs",
                version,
                attempt,
                wait_s,
            )
            time.sleep(max(0.0, wait_s))
            continue
        status = resp.get("status")
        if status == 416 and offset > 0 and resp.get("content_length") == offset:
            # The server cannot satisfy ``Range: bytes=<offset>-``
            _publish_event(
                event_bus,
                "offline_pack_download_started",
                {
                    "version": version,
                    "url": url,
                    "total_bytes": offset,
                    "resumed": True,
                },
            )
            return _finalize_download(
                h,
                dest,
                expected_sha256,
                version=version,
                event_bus=event_bus,
            )
        if status == 416 and offset == 0:
            # A 416 for a zero-offset request is a server anomaly —
            raise RuntimeError(f"unexpected HTTP status 416 for {url} at offset 0")
        if offset > 0 and status is not None and status != 206:
            # The server answered the resume with a non-206 body (200 —
            log.warning(
                "[PACK] server rejected resume at offset %d (status %s) for %s, restarting download from byte 0",
                offset,
                status,
                version,
            )
            offset = 0
            downloaded_bytes = 0
            h = hashlib.sha256()
            continue
        # Stream the response body to disk (append only when resuming).
        try:
            total_bytes = resp.get("content_length")
            if offset > 0 and total_bytes is not None:
                # When resuming, the server returns the remaining
                total_bytes = offset + total_bytes
            _publish_event(
                event_bus,
                "offline_pack_download_started",
                {
                    "version": version,
                    "url": url,
                    "total_bytes": total_bytes,
                    "resumed": offset > 0,
                },
            )
            downloaded_bytes = _stream_response_to_disk(
                resp,
                dest,
                h,
                offset=offset,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                version=version,
                event_bus=event_bus,
                chunk_bytes=chunk_bytes,
                attempt=attempt,
            )
        except OfflinePackDiskFullError:
            # §8.9: the ``with dest.open(...)`` block inside the
            with contextlib.suppress(OSError):
                dest.unlink()
            raise
        except OSError as exc:
            log.exception("[PACK] download error: %s", exc)
            _publish_event(
                event_bus,
                "offline_pack_download_failed",
                {"version": version, "reason": "io_error", "attempts": attempt},
            )
            raise
        # Step 3, finalize: verify the complete file's SHA-256.
        return _finalize_download(
            h,
            dest,
            expected_sha256,
            version=version,
            event_bus=event_bus,
        )


def _probe_partial_for_resume(dest: Path, chunk_bytes: int) -> tuple[int, hashlib._Hash]:
    """Resume-state probe (§8.1): existing partial size + pre-seeded hasher.

    Returns ``(offset, hasher)`` where *hasher* already covers the
    """
    offset = 0
    h: hashlib._Hash = hashlib.sha256()
    if dest.exists():
        try:
            offset = dest.stat().st_size
        except OSError:
            offset = 0
    if offset > 0:
        try:
            with dest.open("rb") as fh_existing:
                while True:
                    buf = fh_existing.read(chunk_bytes)
                    if not buf:
                        break
                    h.update(buf)
        except OSError as exc:
            log.warning(
                "[PACK] resume: cannot re-hash partial %s (%s), restarting from 0",
                dest,
                exc,
            )
            with contextlib.suppress(OSError):
                dest.unlink()
            return 0, hashlib.sha256()
    return offset, h


def _stream_response_to_disk(
    resp: dict,
    dest: Path,
    h: hashlib._Hash,
    *,
    offset: int,
    downloaded_bytes: int,
    total_bytes: int | None,
    version: str,
    event_bus: ModuleType | None,
    chunk_bytes: int,
    attempt: int,
) -> int:
    """``offline_pack_download_progress`` events. Raises"""
    with dest.open("ab" if offset > 0 else "wb") as fh:
        last_progress = time.monotonic()
        last_bytes = downloaded_bytes
        chunk_iter = resp["iter_chunks"](chunk_bytes)
        try:
            for chunk in chunk_iter:
                try:
                    fh.write(chunk)
                except OSError as exc:
                    log.exception("[PACK] disk-full mid-download of %s: %s", version, exc)
                    _publish_event(
                        event_bus,
                        "offline_pack_download_failed",
                        {"version": version, "reason": "disk_full", "attempts": attempt},
                    )
                    raise OfflinePackDiskFullError(
                        f"Disk full while downloading pack {version}",
                        version=version,
                        path=str(dest),
                    ) from exc
                h.update(chunk)
                downloaded_bytes += len(chunk)
                now = time.monotonic()
                if now - last_progress >= 0.5:
                    speed = (downloaded_bytes - last_bytes) / max(1e-9, now - last_progress)
                    eta = (total_bytes - downloaded_bytes) / speed if speed > 0 and total_bytes is not None else None
                    pct = int(100 * downloaded_bytes / total_bytes) if total_bytes else 0
                    _publish_event(
                        event_bus,
                        "offline_pack_download_progress",
                        {
                            "version": version,
                            "progress": pct,
                            "downloaded_bytes": downloaded_bytes,
                            "total_bytes": total_bytes,
                            "speed_bytes_per_sec": speed,
                            "eta_seconds": eta,
                        },
                    )
                    last_progress = now
                    last_bytes = downloaded_bytes
        finally:
            # Deterministic transport cleanup: closing the iterator runs
            close = getattr(chunk_iter, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()
    return downloaded_bytes


def _finalize_download(
    h: hashlib._Hash,
    dest: Path,
    expected_sha256: str,
    *,
    version: str,
    event_bus: ModuleType | None,
) -> bool:
    """trigger re-downloads) and publishes ``offline_pack_corrupt``; a
    match publishes ``offline_pack_download_completed``.
    """
    actual = h.hexdigest()
    if not hmac.compare_digest(actual, expected_sha256):
        log.error(
            "[PACK] SHA-256 mismatch for %s (expected %s, got %s)",
            version,
            expected_sha256,
            actual,
        )
        with contextlib.suppress(OSError):
            dest.unlink()
        _publish_event(
            event_bus,
            "offline_pack_corrupt",
            {"version": version, "path": str(dest), "reason": "sha256_mismatch"},
        )
        return False
    _publish_event(
        event_bus,
        "offline_pack_download_completed",
        {"version": version, "sha256": actual},
    )
    return True


class _RateLimitedError(Exception):
    """Internal sentinel raised by ``_http_get_streaming`` on 403/429."""

    def __init__(self, message: str, *, reset_at: float | None) -> None:
        self.reset_at = reset_at
        super().__init__(message)


def _http_get_streaming(url: str, *, offset: int = 0) -> dict:
    """:func:`assert_offline_pack_url_allowed` instead of being silently"""
    import urllib.error
    import urllib.request

    from voice_typer.server.service.update_check import _SSRFAwareRedirectHandler

    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/pack-downloader"})
    if offset > 0:
        req.add_header("Range", f"bytes={offset}-")
    # build_opener REPLACES the default redirect handler with the
    opener = urllib.request.build_opener(_SSRFAwareRedirectHandler())
    try:
        resp = opener.open(req, timeout=60)
        _verify_pack_peer(url, resp)
    except urllib.error.HTTPError as exc:
        # A 416 (Range Not Satisfiable) is the server telling us the
        try:
            if exc.code == 416:
                total = _parse_content_range_total(exc.headers)
                if total is not None:
                    return {"status": 416, "content_length": total}
            raise
        finally:
            # ``HTTPError`` wraps the error response in a file-like
            with contextlib.suppress(OSError):
                exc.close()
    status = resp.getcode()
    try:
        if status in (403, 429):
            reset_hdr = resp.headers.get("X-RateLimit-Reset")
            reset_at: float | None = None
            if reset_hdr and reset_hdr.isdigit():
                reset_at = float(reset_hdr)
            raise _RateLimitedError(f"GitHub rate limit (status {status})", reset_at=reset_at)
        if status not in (200, 206):
            raise RuntimeError(f"unexpected HTTP status {status} for {url}")
    except BaseException:
        # Resource discipline: the caller never sees ``iter_chunks`` on
        with contextlib.suppress(OSError):
            resp.close()
        raise
    content_length_hdr = resp.headers.get("Content-Length")
    content_length = int(content_length_hdr) if content_length_hdr and content_length_hdr.isdigit() else None

    def iter_chunks(chunk_bytes: int):
        # ``finally`` closes the response on EVERY generator exit:
        try:
            while True:
                buf = resp.read(chunk_bytes)
                if not buf:
                    break
                yield buf
        finally:
            with contextlib.suppress(OSError):
                resp.close()

    return {"status": status, "content_length": content_length, "iter_chunks": iter_chunks}


def _verify_pack_peer(url: str, resp: object) -> None:
    """Verify the pack peer IP matches the validated URL IPs."""
    from voice_typer.server.security.url_allowlist import resolve_allowed_url_ips, verify_peer_ip_allowed
    from voice_typer.server.service.offline_pack.gates import assert_offline_pack_url_allowed

    try:
        expected = resolve_allowed_url_ips(url, field_name="pack_url", client_name="pack_downloader")
    except Exception:
        assert_offline_pack_url_allowed(url)
        log.debug("[PACK] peer-IP pin lookup failed", exc_info=True)
        return
    try:
        raw = getattr(resp, "fp", None)
        sock = getattr(raw, "raw", None)
        sock = getattr(sock, "_sock", sock)
        peer = sock.getpeername()[0] if hasattr(sock, "getpeername") else None
    except Exception:
        log.debug("[PACK] peer-IP read failed", exc_info=True)
        return
    if peer is None:
        return
    from urllib.parse import urlparse as _urlparse

    try:
        verify_peer_ip_allowed(peer, expected, host=(_urlparse(url).hostname or ""))
    except ValueError as exc:
        log.exception("[PACK] peer IP %r outside validated set, refusing", peer)
        raise RuntimeError("pack peer IP outside validated set") from exc


def _parse_content_range_total(headers: object) -> int | None:
    """Parse the total size from a 416's ``Content-Range`` header."""
    get = getattr(headers, "get", None)
    if get is None:
        return None
    raw = get("Content-Range")
    if not raw or "/" not in raw:
        return None
    total = raw.rsplit("/", 1)[1].strip()
    if not total.isdigit():
        return None
    return int(total)
