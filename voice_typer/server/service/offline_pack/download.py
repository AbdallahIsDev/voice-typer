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


# ── Download with resume (§8.1, §8.9) ────────────────────────────────────


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
    """Download *url* to *dest*, resuming from a partial file if present.

    §8.1: the partial file is saved at ``dest`` (which the caller
    places at ``pack-<version>.partial``). On the next launch, the
    download continues from the byte offset of the existing partial.
    The partial is NEVER trusted, only a fully-downloaded + SHA-256-
    verified pack is used.

    Resume status contract (enforced when the transport reports a
    ``status`` key, the default :func:`_http_get_streaming` always
    does): a resume request MUST be answered with HTTP 206. A 200
    means the server ignored the ``Range`` header and is sending the
    FULL body, appending it after the existing partial would corrupt
    the file, so the download restarts from byte 0. A 416 whose
    reported total equals the partial size means the pack is already
    fully downloaded, the request short-circuits straight to SHA-256
    verification (no body transfer, no re-hash of the network).

    §8.9: on ``OSError`` (disk full) the partial is deleted and a
    single notification is published. The caller schedules a retry.

    §8.7: GitHub rate-limit (403/429) triggers exponential backoff
    (1s/2s/4s). The ``X-RateLimit-Reset`` header is respected when
    present (caller waits until that timestamp before retrying).

    Parameters
    ----------
    http_get
        Injectable transport, defaults to :func:`_http_get_streaming`.
        Tests substitute a fake to avoid real network I/O. A fake may
        omit the ``status`` key; such responses keep the legacy append
        semantics (they model a well-behaved 206 responder).
    """
    if http_get is None:
        http_get = _http_get_streaming
    assert_offline_pack_url_allowed(url)
    # Ensure the pack directory exists before opening the partial —
    # first launch the directory may not exist yet.
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Phase 1, resume-state probe (existing partial size + a hasher
    # pre-seeded with the bytes already on disk).
    offset, h = _probe_partial_for_resume(dest, chunk_bytes)
    downloaded_bytes = offset
    # Phase 2, rate-limited request/stream loop (§8.7 + §8.1 status
    # contract) until the body lands on disk.
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
            # because the file is exactly that long, the partial IS
            # the complete pack. Short-circuit to verification without
            # transferring a body.
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
            # surface it (restarts would loop forever).
            raise RuntimeError(f"unexpected HTTP status 416 for {url} at offset 0")
        if offset > 0 and status is not None and status != 206:
            # The server answered the resume with a non-206 body (200 —
            # Range ignored). A blind append would corrupt the partial;
            # restart from byte 0 instead.
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
                # length; the full file size is offset + remaining.
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
            # streaming helper has ALREADY closed the partial's file
            # handle by the time this handler runs, so unlink is safe
            # on every platform (Windows raises PermissionError when
            # deleting an open file).
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
        # Phase 3, finalize: verify the complete file's SHA-256.
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
    partial's on-disk bytes so the final digest spans the whole file.
    When the partial cannot be re-read (corrupt inode, permission
    denied) it is discarded and the probe reports offset 0, the
    download restarts from scratch.
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
    """Write the response body to *dest*, appending only when resuming.

     Feeds *h* (the running whole-file hasher) and publishes throttled
     ``offline_pack_download_progress`` events. Raises
     :class:`OfflinePackDiskFullError` on a mid-write ``OSError`` (§8.9)
    , the ``with`` below has already closed the handle by the time the
     caller's handler runs, so the caller's unlink is safe on Windows.

     Returns the updated ``downloaded_bytes`` count.
    """
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
            # the transport generator's ``finally`` (which closes the
            # HTTP response) even when the loop is abandoned mid-body
            # (disk full / IO error), not just at GC. Test fakes whose
            # ``iter_chunks`` returns a plain (non-generator) iterator
            # have no ``close`` and are unaffected.
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
    """Verify the completed download's SHA-256 and publish the outcome.

    A mismatch discards the partial (§8.2 fail-closed, the next
    trigger re-downloads) and publishes ``offline_pack_corrupt``; a
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


# ── HTTP transport (default; tests inject a fake) ────────────────────────


class _RateLimitedError(Exception):
    """Internal sentinel raised by ``_http_get_streaming`` on 403/429."""

    def __init__(self, message: str, *, reset_at: float | None) -> None:
        self.reset_at = reset_at
        super().__init__(message)


def _http_get_streaming(url: str, *, offset: int = 0) -> dict:
    """Default HTTP transport, uses ``urllib.request`` (no extra dep).

     Returns a dict shaped ``{status, content_length,
     iter_chunks(callable)}`` so tests can substitute a fake without
     touching real I/O. Respects ``HTTP_PROXY`` / ``HTTPS_PROXY`` env
     vars (§8.6), ``build_opener`` installs the default ``ProxyHandler``
     built from ``urllib.request.getproxies()``.

     Resource discipline (proactive close-out, response-close half): the
     response object is closed on EVERY exit path, the rate-limit
     (403/429) and non-200/206 raises close it before propagating, and
     ``iter_chunks`` closes it in a ``finally`` so normal exhaustion, an
     early caller break, and an abandoned/closed generator all release
     the socket deterministically instead of at GC.

     Redirect discipline: the opener installs the SAME SSRF-aware
     redirect handler the manifest fetch uses
     (:class:`voice_typer.server.service.update_check._SSRFAwareRedirectHandler`
    , imported, not copied), so every 3xx hop on the body-download path
     is re-validated through
     :func:`assert_offline_pack_url_allowed` instead of being silently
     followed to any target by urllib's default redirect handler.

     The ``status`` key lets the download loop enforce the §8.1 resume
     contract: 200 = full body (never append after a partial), 206 =
     partial body at the requested offset, 416 with ``content_length``
     set = the complete file size (the requested range is unsatisfiable
     because the partial already IS the whole file, ``content_length``
     carries the total parsed from the 416's ``Content-Range`` header,
     and there is no ``iter_chunks`` to consume).
    """
    import urllib.error
    import urllib.request

    from voice_typer.server.service.update_check import _SSRFAwareRedirectHandler

    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/pack-downloader"})
    if offset > 0:
        req.add_header("Range", f"bytes={offset}-")
    # build_opener REPLACES the default redirect handler with the
    # SSRF-aware one (dedup by class hierarchy) while keeping the
    # env-var ProxyHandler, same construction as the manifest fetch in
    # update_check._http_get_manifest.
    opener = urllib.request.build_opener(_SSRFAwareRedirectHandler())
    try:
        resp = opener.open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        # A 416 (Range Not Satisfiable) is the server telling us the
        # partial already covers the whole file. Parse the total out of
        # ``Content-Range: bytes */<total>`` and hand it to the caller
        # as the "already complete" marker instead of an error.
        try:
            if exc.code == 416:
                total = _parse_content_range_total(exc.headers)
                if total is not None:
                    return {"status": 416, "content_length": total}
            raise
        finally:
            # ``HTTPError`` wraps the error response in a file-like
            # object, release it on every path out of this handler
            # (416 short-circuit AND the re-raise).
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
        # these failure paths, so the generator's ``finally`` never
        # runs, close the response here before the raise escapes.
        with contextlib.suppress(OSError):
            resp.close()
        raise
    content_length_hdr = resp.headers.get("Content-Length")
    content_length = int(content_length_hdr) if content_length_hdr and content_length_hdr.isdigit() else None

    def iter_chunks(chunk_bytes: int):
        # ``finally`` closes the response on EVERY generator exit:
        # normal exhaustion, the caller breaking early, or the
        # generator being closed explicitly (see
        # ``_stream_response_to_disk``'s deterministic ``close()``) /
        # collected after an abandoned download.
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


def _parse_content_range_total(headers: object) -> int | None:
    """Parse the total size from a 416's ``Content-Range`` header.

    Unsatisfied-range responses carry ``Content-Range: bytes */<total>``
    (RFC 9110 §14.4). Returns ``None`` when the header is missing or
    malformed, the caller then treats the 416 as a plain error.
    """
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
