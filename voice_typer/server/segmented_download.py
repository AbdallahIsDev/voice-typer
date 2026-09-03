"""Segmented (multi-connection) HTTP Range downloader for large model files.

WHY THIS EXISTS: ``snapshot_download`` fetches each file over a SINGLE
HTTP connection. On high-latency / per-connection-throttled / lossy
links that single stream stalls well below line rate (the classic "starts
at 10 MB/s, drops to 500 kB/s" symptom). This module downloads ONE large
file as N concurrent byte-range segments — the aria2 / ADM approach —
on the standard HTTP path (NOT xet), so the pause/cancel transfer gate
keeps working and no native code is involved.

Design principles (E3/E13 — a reusable subsystem, not a service hack):

- **Pure engine.** No imports from the service layer, tray, or config.
  The caller supplies ``gate_check`` (pause-block / abort-raise, e.g.
  :func:`asr_setup.check_download_gate`), a progress callback, and an
  ``opener_factory`` seam. Everything else is stdlib (``urllib``,
  ``threading``) — no new dependencies, frozen-app safe.
- **Crash-safe resume (ADM semantics).** Per-segment part files + an
  atomic JSON state file record completion. A kill -9 mid-download
  resumes finished segments and re-fetches only the rest — strictly
  better than huggingface_hub 1.26's cache path, which discards
  partial-file progress on failure (process-unique tmp names).
- **Never trusts the network.** The assembled file MUST match
  ``expected_sha256`` (the manifest pin) or nothing is returned —
  poisoned parts are deleted, never installed.
- **Failover, not failure.** Any condition this engine cannot handle
  (unknown size, Range ignored, repeated 429/5xx, sha mismatch) raises
  :class:`SegmentedDownloadError` so the caller falls back to the
  classic single-stream path. A download the classic path could
  complete must never fail because of this module.

Threading model: one worker thread per active segment
(``ThreadPoolExecutor``); a shared lock guards byte counters and state
writes. ``gate_check`` runs on every ~1 MiB wire chunk in every worker,
so pause/cancel engage within milliseconds.

Cross-platform notes: paths are ``pathlib`` throughout; the only
platform-sensitive call is the cache-layout symlink, which degrades to
a copy when symlinks are unavailable (Windows without privilege) —
mirroring ``huggingface_hub``'s own fallback.
"""

from __future__ import annotations

import concurrent.futures
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────

#: Files at/above this size are worth segmenting (below it the extra
#: handshakes cost more than parallelism gains).
SEGMENT_THRESHOLD_BYTES = 200 * 1024 * 1024
#: Target bytes per segment; segment count = ceil(size / target), capped.
SEGMENT_TARGET_BYTES = 256 * 1024 * 1024
#: Upper bound on concurrent Range connections (politeness: HF's own
#: client opens ~8 across files; per-file parallelism stays below that).
MAX_SEGMENTS = 6
#: Wire read size — also the pause/cancel checkpoint granularity.
READ_CHUNK_BYTES = 1024 * 1024
#: Per-request socket timeout (connect + idle read).
REQUEST_TIMEOUT_S = 30
#: Max redirect hops when resolving the download URL.
MAX_REDIRECTS = 5
#: Attempts per segment (initial + retries) before the file fails over.
SEGMENT_ATTEMPTS = 3
#: Backoff between segment attempts (429 honors Retry-After instead).
RETRY_BACKOFF_S = (1.0, 2.0, 4.0)
#: Cap for a server-provided Retry-After delay.
MAX_RETRY_AFTER_S = 60.0

GateCheck = Callable[[], None]
ProgressCb = Callable[[int, int], None]  # (bytes_done, total_size)


class SegmentedDownloadError(Exception):
    """The segmented path cannot complete this file.

    Not a user-facing failure: callers fall back to the classic
    single-stream download, which has no new failure modes. Carries the
    reason for the log.
    """


@dataclass(frozen=True)
class SegmentRange:
    """One byte range [start, end] (both inclusive), zero-based."""

    index: int
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def plan_segments(
    total_size: int,
    *,
    segment_target: int = SEGMENT_TARGET_BYTES,
    max_segments: int = MAX_SEGMENTS,
) -> list[SegmentRange]:
    """Split ``total_size`` bytes into contiguous, gapless ranges.

    Segment count is ``ceil(total / target)`` capped at ``max_segments``
    (the last segment absorbs the remainder). A file smaller than one
    target yields a single whole-file range.
    """
    if total_size <= 0:
        raise SegmentedDownloadError(f"cannot plan segments for size {total_size}")
    uncapped = -(-total_size // segment_target)
    count = max(1, min(max_segments, uncapped))
    ranges: list[SegmentRange] = []
    start = 0
    if count < uncapped:
        # Capped (huge file): split evenly so no single tail segment
        # dwarfs the rest and idles the other workers.
        base, extra = divmod(total_size, count)
        for i in range(count):
            length = base + (1 if i < extra else 0)
            ranges.append(SegmentRange(index=i, start=start, end=start + length - 1))
            start += length
        return ranges
    # Uncapped: full target-sized segments + remainder tail.
    for i in range(count):
        end = start + segment_target - 1 if i < count - 1 else total_size - 1
        ranges.append(SegmentRange(index=i, start=start, end=end))
        start = end + 1
    return ranges


# ── State file (crash-safe resume) ────────────────────────────────────

_STATE_VERSION = 1


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", filename)


def state_path_for(scratch_dir: Path, filename: str) -> Path:
    return scratch_dir / f"{_safe_filename(filename)}.state.json"


def part_path_for(scratch_dir: Path, filename: str, index: int) -> Path:
    return scratch_dir / f"{_safe_filename(filename)}.part{index}"


def write_state(
    state_path: Path,
    *,
    url: str,
    etag: str | None,
    total_size: int,
    expected_sha256: str,
    segments: list[dict[str, Any]],
) -> None:
    """Atomically persist segment completion (tmp + rename)."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _STATE_VERSION,
        "url": url,
        "etag": etag,
        "total_size": total_size,
        "expected_sha256": expected_sha256,
        "segments": segments,
    }
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, state_path)


def read_state(state_path: Path) -> dict[str, Any] | None:
    """Load a state file; ``None`` when absent/corrupt/wrong version."""
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != _STATE_VERSION:
        return None
    return data


def state_matches(
    state: dict[str, Any],
    *,
    url: str,
    etag: str | None,
    total_size: int,
    expected_sha256: str,
) -> bool:
    """True when the on-disk state describes THIS exact download."""
    if (
        state.get("url") != url
        or state.get("total_size") != total_size
        or state.get("expected_sha256") != expected_sha256
    ):
        return False
    # An ETag change means the server-side file changed — stale parts
    # must not be trusted. (Both None counts as a match.)
    return state.get("etag") == etag


# ── Transport seam ────────────────────────────────────────────────────
#
# The engine talks HTTP only through an ``opener`` object with
# ``.open(request, timeout=...)`` (urllib-compatible). Tests inject a
# fake; production builds a urllib opener with proxy support.


def build_opener(
    proxies: dict[str, str] | None = None,
    *,
    user_agent: str,
) -> Any:
    """Build a urllib opener honoring proxies (same convention as the
    update-checker's transport: ``offline_pack.proxy_env()``)."""
    handlers: list[Any] = []
    if proxies:
        handlers.append(urllib.request.ProxyHandler(proxies))
    handlers.append(_NoAutoRedirectHandler())
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", user_agent)]
    return opener


class _NoAutoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable urllib's automatic redirect following.

    The engine resolves redirects MANUALLY (single hop chain) so it can
    strip the Authorization header when the host changes (the HF resolve
    URL 302-redirects to a presigned CDN URL that needs no auth — and
    must never receive our token) and enforce https-only targets.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN202
        return None


def _strip_auth_on_host_change(headers: dict, old_host: str, new_host: str) -> dict:
    if old_host.lower() != new_host.lower():
        return {k: v for k, v in headers.items() if k.lower() != "authorization"}
    return dict(headers)


def resolve_download(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
    opener_factory: Callable[[], Any] | None = None,
) -> tuple[str, int | None, str | None]:
    """Resolve ``url`` to (final_url, total_size, etag).

    Follows up to ``MAX_REDIRECTS`` hops manually (https-only, auth
    stripped on host change). Size comes from HEAD's Content-Length;
    when HEAD is unsupported or lengthless, a ``bytes=0-0`` Range probe
    is used. ``total_size`` is ``None`` only when the server reveals no
    length at all (caller must fall back to single-stream).
    """
    from urllib.parse import urlparse

    opener = opener_factory() if opener_factory else build_opener(None, user_agent=f"{APP_NAME}/segmented-downloader")
    current = url
    base_headers = dict(headers or {})
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme.lower() != "https":
            raise SegmentedDownloadError(f"refusing non-https redirect target: {current}")
        head_req = _make_request(current, base_headers, method="HEAD")
        try:
            with opener.open(head_req, timeout=timeout_s) as resp:
                status = _status_of(resp)
                if status in (301, 302, 303, 307, 308):
                    location = resp.getheader("Location")
                    if not location:
                        raise SegmentedDownloadError("redirect without Location")
                    base_headers = _strip_auth_on_host_change(
                        base_headers, parsed.hostname or "", urlparse(location).hostname or ""
                    )
                    current = location
                    continue
                if status != 200:
                    raise SegmentedDownloadError(f"HEAD returned HTTP {status}")
                length = resp.getheader("Content-Length")
                etag = resp.getheader("ETag")
                if length is not None:
                    return current, int(length), etag
                break  # lengthless HEAD → Range probe below
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                location = e.headers["Location"]
                base_headers = _strip_auth_on_host_change(
                    base_headers, parsed.hostname or "", urlparse(location).hostname or ""
                )
                current = location
                continue
            raise SegmentedDownloadError(f"HEAD failed: HTTP {e.code}") from e
    else:
        raise SegmentedDownloadError("too many redirects resolving download URL")

    # Lengthless server: probe with a 1-byte Range request.
    probe_headers = dict(base_headers)
    probe_headers["Range"] = "bytes=0-0"
    probe_req = _make_request(current, probe_headers, method="GET")
    try:
        with opener.open(probe_req, timeout=timeout_s) as resp:
            if _status_of(resp) != 206:
                return current, None, base_headers.get("ETag")
            content_range = resp.getheader("Content-Range", "")
            total = _parse_total_from_content_range(content_range)
            return current, total, resp.getheader("ETag")
    except urllib.error.HTTPError as e:
        raise SegmentedDownloadError(f"Range probe failed: HTTP {e.code}") from e
    return current, None, None


def _make_request(url: str, headers: dict[str, str], method: str = "GET") -> Any:
    req = urllib.request.Request(url, headers=dict(headers), method=method)
    return req


def _status_of(resp: Any) -> int:
    status = getattr(resp, "status", None)
    if status is not None:
        return int(status)
    return int(resp.getcode())


def _parse_total_from_content_range(content_range: str) -> int | None:
    """Parse ``bytes <s>-<e>/<total>`` → total. ``None`` when unparsable."""
    m = re.search(r"bytes\s+\d+-\d+/(\d+)", content_range)
    return int(m.group(1)) if m else None


def _parse_retry_after(value: str | None) -> float:
    try:
        return max(0.0, min(float(value or 0), MAX_RETRY_AFTER_S))
    except (TypeError, ValueError):
        return 0.0


def _is_transient_http(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


# ── Segment fetch ─────────────────────────────────────────────────────


class _RangeUnsupportedError(SegmentedDownloadError):
    """Server answered 200 to a Range request for a partial segment.

    A subclass of :class:`SegmentedDownloadError` so callers fall back
    to the classic single-stream path, which handles Range-less servers
    natively.
    """


def _body_matches_segment(resp: Any, seg: SegmentRange) -> bool:
    """True when a 200 response body IS the whole requested segment
    (single-segment file served without Range support)."""
    try:
        length = resp.getheader("Content-Length")
        return length is not None and int(length) == seg.length
    except (TypeError, ValueError):
        return False


def _sleep_interruptible(delay_s: float, gate_check: GateCheck | None) -> None:
    """Sleep, but wake promptly for cancel (and park on pause).

    A plain ``time.sleep`` would deafen the transfer to cancel for the
    whole backoff. Looping through the gate keeps abort latency at
    ~0.2 s; on pause the sleep simply extends (correct — nothing should
    happen while paused).
    """
    deadline = time.monotonic() + max(0.0, delay_s)
    while True:
        if gate_check is not None:
            gate_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _fetch_segment(
    *,
    opener: Any,
    url: str,
    seg: SegmentRange,
    part_path: Path,
    headers: dict[str, str],
    timeout_s: float,
    gate_check: GateCheck | None,
    on_bytes: Callable[[int], None],
) -> int:
    """Fetch one segment with resume + retry. Returns bytes written.

    Resumes from the existing part-file size via ``Range``. Retries
    transient failures (429/5xx/timeouts/drops) with backoff; raises
    :class:`_RangeUnsupported` when the server ignores Range (caller
    restarts the whole file single-stream); ENOSPC and other local
    errors propagate unwrapped (never retried).
    """
    offset = part_path.stat().st_size if part_path.exists() else 0
    if offset > seg.length:
        # Torn state (part longer than its segment) — restart it.
        part_path.unlink(missing_ok=True)
        offset = 0
    if offset == seg.length:
        return 0  # already complete (verified by caller)

    last_error: Exception | None = None
    for attempt in range(SEGMENT_ATTEMPTS):
        if gate_check is not None:
            gate_check()
        req_headers = dict(headers)
        req_headers["Range"] = f"bytes={seg.start + offset}-{seg.end}"
        try:
            with opener.open(_make_request(url, req_headers), timeout=timeout_s) as resp:
                status = _status_of(resp)
                if status == 429 or 500 <= status <= 599:
                    delay = _parse_retry_after(resp.getheader("Retry-After"))
                    if delay <= 0 and attempt < len(RETRY_BACKOFF_S):
                        delay = RETRY_BACKOFF_S[attempt]
                    _sleep_interruptible(delay, gate_check)
                    last_error = SegmentedDownloadError(f"HTTP {status}")
                    continue
                if status == 416:
                    # Range unsatisfiable: our offset is likely already
                    # complete (another attempt finished it) — re-check.
                    if part_path.exists() and part_path.stat().st_size >= seg.length:
                        return 0
                    offset = 0
                    part_path.unlink(missing_ok=True)
                    last_error = SegmentedDownloadError("HTTP 416")
                    continue
                if status == 200 and (offset > 0 or not _body_matches_segment(resp, seg)):
                    # Server ignored Range: only acceptable when the body
                    # IS the whole segment (single-segment file). Anything
                    # else cannot be spliced — fail over to classic.
                    raise _RangeUnsupportedError("server ignored Range request (HTTP 200)")
                if status not in (200, 206):
                    raise SegmentedDownloadError(f"unexpected HTTP {status}")
                expected = seg.length - offset
                got = 0
                mode = "ab" if offset > 0 else "wb"
                with open(part_path, mode) as f:
                    while True:
                        if gate_check is not None:
                            gate_check()
                        chunk = resp.read(READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        on_bytes(len(chunk))
                if got < expected:
                    # Truncated stream (dropped connection): resume on
                    # the next attempt from the grown part file.
                    offset = part_path.stat().st_size
                    last_error = SegmentedDownloadError(f"truncated segment {seg.index}: got {got}/{expected}")
                    continue
                return got
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise  # disk-full is fatal, never retried
            last_error = e
            _sleep_interruptible(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)], gate_check)
        except Exception as e:  # noqa: BLE001 — transport errors retried uniformly
            # NOTE: ModelDownloadAborted is a BaseException, so it is NOT
            # caught here — aborts unwind immediately, never retried.
            last_error = e
            _sleep_interruptible(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)], gate_check)
    raise SegmentedDownloadError(f"segment {seg.index} failed after {SEGMENT_ATTEMPTS} attempts: {last_error}")


# ── Orchestrator ──────────────────────────────────────────────────────


def download_file_segmented(
    *,
    url: str,
    filename: str,
    total_size: int,
    etag: str | None,
    expected_sha256: str | None,
    scratch_dir: Path,
    progress_cb: ProgressCb | None = None,
    gate_check: GateCheck | None = None,
    headers: dict[str, str] | None = None,
    num_segments: int | None = None,
    segment_target: int = SEGMENT_TARGET_BYTES,
    max_segments: int = MAX_SEGMENTS,
    opener_factory: Callable[[], Any] | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> Path:
    """Download one file as concurrent Range segments; return the
    assembled, sha-verified temp file path.

    ``expected_sha256`` is REQUIRED (``TypeError``/``ValueError`` when
    missing/empty): only files with a manifest pin may take this path —
    unpinned files stay on the classic download. The returned file is
    byte-exact per the pin; the caller moves it into place.

    Resume: part files + ``<filename>.state.json`` in ``scratch_dir``
    survive kills/cancels; a re-run with identical
    (url, etag, total, sha) skips finished segments and resumes partial
    ones. Mismatched state is discarded (fresh start).

    Raises :class:`SegmentedDownloadError` for anything this engine
    cannot handle (callers fall back to classic); lets
    ``ModelDownloadAborted`` (cancel) and ``OSError`` (disk-full)
    propagate unwrapped.
    """
    if expected_sha256 is None:
        raise TypeError("expected_sha256 is required for segmented downloads")
    if not expected_sha256:
        raise ValueError("expected_sha256 must not be empty")

    scratch_dir.mkdir(parents=True, exist_ok=True)
    opener = opener_factory() if opener_factory else build_opener(None, user_agent=f"{APP_NAME}/segmented-downloader")
    segments = plan_segments(total_size, segment_target=segment_target, max_segments=max_segments)
    if num_segments is not None and total_size >= num_segments:
        # Explicit segment count (tests + callers that already know the
        # right granularity): re-derive the target so the count holds.
        segments = plan_segments(
            total_size,
            segment_target=max(1, -(-total_size // num_segments)),
            max_segments=num_segments,
        )

    state_path = state_path_for(scratch_dir, filename)
    state = read_state(state_path)
    if state is None or not state_matches(
        state,
        url=url,
        etag=etag,
        total_size=total_size,
        expected_sha256=expected_sha256,
    ):
        _discard_resume_state(scratch_dir, filename, state_path)
        done_flags = [False] * len(segments)
    else:
        done_flags = _reconcile_state(scratch_dir, filename, segments, state.get("segments", []))

    lock = threading.Lock()
    bytes_done = [sum(s.length for s, d in zip(segments, done_flags, strict=True) if d)]

    def on_bytes(n: int) -> None:
        with lock:
            bytes_done[0] += n
            total_now = bytes_done[0]
        if progress_cb is not None:
            progress_cb(total_now, total_size)

    def mark_done(index: int) -> None:
        with lock:
            done_flags[index] = True
            snapshot = [
                {
                    "index": s.index,
                    "start": s.start,
                    "end": s.end,
                    "done": done_flags[s.index],
                }
                for s in segments
            ]
        write_state(
            state_path,
            url=url,
            etag=etag,
            total_size=total_size,
            expected_sha256=expected_sha256,
            segments=snapshot,
        )

    active = [s for s, d in zip(segments, done_flags, strict=True) if not d]
    if active:
        base_headers = dict(headers or {})
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(active), thread_name_prefix="segdl") as pool:
            futures = {
                pool.submit(
                    _fetch_segment,
                    opener=opener,
                    url=url,
                    seg=s,
                    part_path=part_path_for(scratch_dir, filename, s.index),
                    headers=base_headers,
                    timeout_s=timeout_s,
                    gate_check=gate_check,
                    on_bytes=on_bytes,
                ): s
                for s in active
            }
            try:
                for fut in concurrent.futures.as_completed(futures):
                    fut.result()  # raises on segment failure
                    mark_done(futures[fut].index)
            except BaseException:
                # Be polite: don't leave not-started work queued behind
                # a failure. Running workers finish/abort on their own
                # (gate raises on cancel; errors fail fast); the
                # executor join then returns promptly.
                for f in futures:
                    f.cancel()
                raise

    assembled = scratch_dir / f"{_safe_filename(filename)}.assembled.tmp"
    _assemble_and_verify(scratch_dir, filename, segments, expected_sha256, assembled)
    # Success: resume state + parts are now redundant — remove them so a
    # later retry cannot trust stale parts. The verified bytes live on
    # in the returned file; the caller moves it into place.
    _discard_resume_state(scratch_dir, filename, state_path)
    return assembled


def _discard_resume_state(scratch_dir: Path, filename: str, state_path: Path) -> None:
    import contextlib

    state_path.unlink(missing_ok=True)
    for part in scratch_dir.glob(f"{_safe_filename(filename)}.part*"):
        with contextlib.suppress(OSError):
            part.unlink()


def _reconcile_state(
    scratch_dir: Path,
    filename: str,
    segments: list[SegmentRange],
    saved: list[dict[str, Any]],
) -> list[bool]:
    """Validate saved completion flags against on-disk part sizes.

    A segment counts as done only when flagged done AND its part file
    holds exactly the segment's bytes; over-long parts are truncated
    away (torn writes), short parts resume.
    """
    by_index = {int(s.get("index", -1)): s for s in saved if isinstance(s, dict)}
    done: list[bool] = []
    for seg in segments:
        part = part_path_for(scratch_dir, filename, seg.index)
        entry = by_index.get(seg.index, {})
        flagged = bool(entry.get("done"))
        size = part.stat().st_size if part.exists() else -1
        if flagged and size == seg.length:
            done.append(True)
            continue
        if size > seg.length and part.exists():
            # Torn write (kill -9 mid-flush): truncate, re-fetch segment.
            with open(part, "r+b") as f:
                f.truncate(seg.length)
        done.append(False)
    return done


def _assemble_parts(scratch_dir: Path, filename: str, segments: list[SegmentRange], dest: Path) -> None:
    with open(dest, "wb") as out:
        for seg in segments:
            part = part_path_for(scratch_dir, filename, seg.index)
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out, length=READ_CHUNK_BYTES)


def _assemble_and_verify(
    scratch_dir: Path,
    filename: str,
    segments: list[SegmentRange],
    expected_sha256: str,
    assembled: Path,
) -> None:
    _assemble_parts(scratch_dir, filename, segments, assembled)
    digest = hashlib.sha256()
    with open(assembled, "rb") as f:
        for chunk in iter(lambda: f.read(READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256.lower():
        # Poisoned parts must not linger for a later retry to trust.
        import contextlib

        _discard_resume_state(scratch_dir, filename, state_path_for(scratch_dir, filename))
        with contextlib.suppress(OSError):
            assembled.unlink()
        raise SegmentedDownloadError(
            f"assembled sha256 mismatch for {filename} (got {digest.hexdigest()[:16]}…, parts discarded)"
        )


# ── File planning (which files take the segmented path) ───────────────


@dataclass(frozen=True)
class PlannedFile:
    """One repo file routed to the segmented engine."""

    filename: str  # repo-relative path
    size: int
    blob_id: str
    sha256: str  # manifest pin (== blob_id for LFS files)


def _matches_any_pattern(path: str, patterns: Any) -> bool:
    import fnmatch

    if patterns is None:
        return True
    if isinstance(patterns, str):
        patterns = [patterns]
    return any(fnmatch.fnmatchcase(path, pat) for pat in patterns)


def _default_list_files(repo_id: str, revision: str) -> list[tuple[str, int | None, str | None]]:
    """List (path, size, blob_id) via the Hub API (one metadata call)."""
    from huggingface_hub import HfApi

    out: list[tuple[str, int | None, str | None]] = []
    for entry in HfApi().list_repo_tree(repo_id, revision=revision, recursive=True):
        path = getattr(entry, "path", None)
        if path is None:
            continue
        out.append((path, getattr(entry, "size", None), getattr(entry, "blob_id", None)))
    return out


def plan_segmented_files(
    *,
    repo_id: str,
    revision: str,
    allow_patterns: Any,
    file_hashes: Any,
    threshold_bytes: int = SEGMENT_THRESHOLD_BYTES,
    list_files: Callable[[], list[tuple[str, int | None, str | None]]] | None = None,
) -> list[PlannedFile] | None:
    """Decide which repo files take the segmented path.

    Returns the list of big, pinned files (empty list = everything is
    small, use classic for all) or ``None`` when planning itself is
    impossible (listing failed, sizes unknown) — the caller then uses
    the classic whole-repo path, i.e. today's behavior.

    A file qualifies iff: it matches ``allow_patterns``, its size is
    known and ≥ ``threshold_bytes``, the manifest pins a sha256 for it,
    a blob id is known, AND pin == blob_id (LFS content hash — guards
    against manifest/tree drift; mismatch falls back to classic).

    NEVER raises.
    """
    try:
        entries = list_files() if list_files is not None else _default_list_files(repo_id, revision)
        planned: list[PlannedFile] = []
        for path, size, blob_id in entries:
            if not _matches_any_pattern(path, allow_patterns):
                continue
            if size is None or size < threshold_bytes:
                continue
            pin = file_hashes.get(path) if isinstance(file_hashes, dict) else None
            if not pin or not blob_id:
                continue
            if str(pin).lower() != str(blob_id).lower():
                log.warning(
                    "[SEGDL] manifest pin != tree blob_id for %s:%s (drift?) — classic path",
                    repo_id,
                    path,
                )
                continue
            planned.append(PlannedFile(filename=path, size=size, blob_id=blob_id, sha256=str(pin).lower()))
        return planned
    except Exception:
        log.debug(
            "[SEGDL] file planning failed for %s — classic path",
            repo_id,
            exc_info=True,
        )
        return None


# ── Multi-file phase runner (service integration) ─────────────────────


def run_segmented_phase(
    *,
    model_name: str,
    repo_id: str,
    commit: str,
    cache_dir: str | Path,
    seg_plan: list[PlannedFile],
    progress_cb: Callable[[int, int], None] | None = None,
    file_cb: Callable[[str, int, int], None] | None = None,
    gate_check: GateCheck | None = None,
    headers: dict[str, str] | None = None,
    proxies: dict[str, str] | None = None,
) -> None:
    """Fetch every planned big file sequentially with live progress.

    Runs AFTER the classic snapshot phase finished the small files (so
    the classic run already wrote the tree cache + refs behavior is
    untouched). Per file: resolve → segmented fetch (crash-safe resume
    from prior attempts) → sha-verified assembly → blob + snapshot-link
    placement. Calls ``progress_cb(cumulative_done, big_total)`` per
    wire chunk and ``file_cb(filename, index, total)`` at each file
    start (either may be ``None``).

    Raises :class:`SegmentedDownloadError` (caller falls back to the
    classic full download) or propagates the gate's
    ``ModelDownloadAborted`` (caller maps to cancelled). Scratch state
    is removed on full success; left in place otherwise for resume.
    """
    from huggingface_hub import hf_hub_url

    big_total = sum(p.size for p in seg_plan)
    cumulative = [0]
    base = [0]
    lock = threading.Lock()

    def aggregate(done: int, _total: int) -> None:
        if progress_cb is None:
            return
        with lock:
            cumulative[0] = base[0] + done
            current = cumulative[0]
        progress_cb(current, big_total)

    scratch_parent = Path(str(cache_dir)).parent.parent / "download-parts"
    repo_scratch = scratch_parent / f"models--{repo_id.replace('/', '--')}"
    opener = build_opener(proxies, user_agent=f"{APP_NAME}/segmented-downloader")
    for index, plan in enumerate(seg_plan):
        if gate_check is not None:
            gate_check()
        if file_cb is not None:
            file_cb(plan.filename, index, len(seg_plan))
        url = hf_hub_url(repo_id, plan.filename, revision=commit)
        final_url, size, etag = resolve_download(url, headers=headers, opener_factory=lambda: opener)
        if size is None or size != plan.size:
            raise SegmentedDownloadError(f"size changed for {plan.filename} (plan {plan.size}, now {size})")
        assembled = download_file_segmented(
            url=final_url,
            filename=plan.filename,
            total_size=size,
            etag=etag,
            expected_sha256=plan.sha256,
            scratch_dir=repo_scratch,
            progress_cb=aggregate,
            gate_check=gate_check,
            headers=headers,
            opener_factory=lambda: opener,
        )
        install_blob_into_hf_cache(
            cache_dir=cache_dir,
            repo_id=repo_id,
            commit=commit,
            filename=plan.filename,
            blob_sha256=plan.sha256,
            assembled_path=assembled,
        )
        with lock:
            base[0] += plan.size
    # Best-effort scratch cleanup (ignore_errors already suppresses;
    # leftovers are harmless — the next run reconciles by part size).
    shutil.rmtree(repo_scratch, ignore_errors=True)


# ── HF cache layout writer ────────────────────────────────────────────


def install_blob_into_hf_cache(
    *,
    cache_dir: str | Path,
    repo_id: str,
    commit: str,
    filename: str,
    blob_sha256: str,
    assembled_path: str | Path,
) -> Path:
    """Place a verified file into the HF hub cache layout and return the
    snapshot file path.

    Writes ``blobs/<sha256>`` (atomic rename) + ``snapshots/<commit>/
    <filename>`` (relative symlink, copied when symlinks are
    unavailable — mirroring huggingface_hub's own fallback). Refs/tree
    bookkeeping stays owned by the classic ``snapshot_download`` run
    that must precede segmented files (it lists the full tree and writes
    the tree cache).

    The caller MUST self-verify afterwards with the local-only snapshot
    probe; on any doubt it falls back to the classic full download.
    """
    cache = Path(cache_dir)
    blobs_dir = cache / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    blob_path = blobs_dir / blob_sha256
    if blob_path.exists():
        Path(assembled_path).unlink(missing_ok=True)
    else:
        shutil.move(str(assembled_path), str(blob_path))

    snap_dir = cache / "snapshots" / commit
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / filename
    try:
        if snap_file.is_symlink() or snap_file.exists():
            snap_file.unlink()
        rel = os.path.relpath(blob_path, snap_dir)
        os.symlink(rel, snap_file)
    except OSError:
        # Windows without symlink privilege (or any symlink failure):
        # duplicate the bytes like huggingface_hub does.
        try:
            if snap_file.is_symlink() or snap_file.exists():
                snap_file.unlink()
        except OSError:
            pass
        shutil.copyfile(blob_path, snap_file)
    return snap_file
