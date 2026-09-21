"""Module-level helpers extracted from ``ModelMixin.download_model``."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import NotRequired, TypedDict

log = logging.getLogger(__name__)


class DownloadOutcome(TypedDict, total=False):
    """* ``consent_required``: present on the HuggingFace consent-gate"""

    success: bool
    error: str
    model: str
    message: str
    cancelled: bool
    consent_required: bool
    reason: str
    download_already_active: NotRequired[bool]
    queued: NotRequired[bool]
    queue_position: NotRequired[int]


def push_progress(
    event_bus,
    model_name: str,
    progress: int,
    status: str,
    *,
    downloaded_bytes: int | None = None,
    total_bytes: int | None = None,
    speed_bytes_per_sec: float | None = None,
    eta_seconds: float | None = None,
    paused: bool | None = None,
    resumed: bool | None = None,
    queue_position: int | None = None,
) -> None:
    """Push a ``download_progress`` event with rich metadata."""
    data: dict = {
        "model": model_name,
        "progress": max(0, min(100, int(progress))),
        "status": status,
    }
    if downloaded_bytes is not None:
        data["downloaded_bytes"] = int(downloaded_bytes)
    if total_bytes is not None:
        data["total_bytes"] = int(total_bytes)
    if speed_bytes_per_sec is not None:
        data["speed_bytes_per_sec"] = float(speed_bytes_per_sec)
    if eta_seconds is not None:
        data["eta_seconds"] = float(eta_seconds)
    if paused is not None:
        data["paused"] = bool(paused)
    if resumed is not None:
        data["resumed"] = bool(resumed)
    if queue_position is not None:
        data["queue_position"] = int(queue_position)
    event_bus.publish({"type": "download_progress", "data": data})


def notify(tray, model_name: str, title: str, message: str) -> None:
    """Forward a tray notification, swallowing errors."""
    try:
        tray.notify(title, message)
    except Exception:
        log.debug("[SERVICE] tray notify failed for model '%s'", model_name, exc_info=True)


def poll_download_progress(
    *,
    thread,
    target_bytes: int,
    target_mb: int,
    model_name: str,
    repo_id: str,
    cache_dir,
    download_id: str,
    event_bus,
    is_cancelled_fn,
    max_duration_s: float = 1800.0,
    max_stall_s: float = 60.0,
) -> tuple[str, int]:
    """Poll the HF cache directory size while the download thread runs.

    Returns:
    """
    # PERF-21: scope the filesystem walk to the
    from voice_typer.server.asr_setup import is_download_paused, wait_while_paused

    cancelled = False
    # track pause/resume transitions so we only push
    last_paused_state = False
    # track timing for speed / ETA.
    last_progress_time = time.monotonic()
    last_total_bytes_seen = 0
    # Track loop start for the max-duration guard, and the last
    loop_start_time = time.monotonic()
    last_byte_change_time = time.monotonic()
    # Track accumulated paused time so the max-duration guard
    accumulated_paused_s = 0.0
    pause_started_at: float | None = None

    while thread.is_alive():
        # Check for cancellation via the
        if is_cancelled_fn(download_id):
            cancelled = True
            log.info("[SERVICE] Download of %s cancelled by user", model_name)
            push_progress(event_bus, model_name, 0, "Download cancelled")
            break
        # check for pause.  When paused, block for up
        currently_paused = is_download_paused()
        if currently_paused != last_paused_state:
            # State transition, push the event.
            transition_pct = max(
                0,
                min(
                    95,
                    int(10 + (last_total_bytes_seen / max(1, target_bytes)) * 85),
                ),
            )
            if currently_paused:
                push_progress(
                    event_bus,
                    model_name,
                    transition_pct,
                    f"Download of {model_name} paused",
                    downloaded_bytes=last_total_bytes_seen,
                    total_bytes=target_bytes,
                    paused=True,
                )
                # Start the pause timer so the max-duration
                pause_started_at = time.monotonic()
            else:
                push_progress(
                    event_bus,
                    model_name,
                    transition_pct,
                    f"Download of {model_name} resumed",
                    downloaded_bytes=last_total_bytes_seen,
                    total_bytes=target_bytes,
                    resumed=True,
                )
                # Stop the pause timer and accumulate.
                if pause_started_at is not None:
                    accumulated_paused_s += time.monotonic() - pause_started_at
                    pause_started_at = None
                # Reset the stall timer on resume so the
                last_byte_change_time = time.monotonic()
            last_paused_state = currently_paused
        if currently_paused:
            # Wait for resume (or cancel), then loop.
            wait_while_paused(timeout_s=1.0)
            continue
        # Max-duration + stall guards.  Skipped while paused
        now_for_guard = time.monotonic()
        effective_elapsed = (now_for_guard - loop_start_time) - accumulated_paused_s
        if effective_elapsed > max_duration_s:
            log.warning(
                "[SERVICE] Download of '%s' exceeded max duration %.0fs (elapsed %.1fs, bytes=%d), aborting",
                model_name,
                max_duration_s,
                effective_elapsed,
                last_total_bytes_seen,
            )
            raise TimeoutError(
                f"Download of {model_name} exceeded max duration "
                f"{max_duration_s}s (elapsed {effective_elapsed:.1f}s, "
                f"bytes={last_total_bytes_seen})"
            )
        stall_elapsed = now_for_guard - last_byte_change_time
        if stall_elapsed > max_stall_s:
            log.warning(
                "[SERVICE] Download of '%s' stalled, no progress for %.0fs (bytes=%d), aborting",
                model_name,
                max_stall_s,
                last_total_bytes_seen,
            )
            raise TimeoutError(
                f"Download of {model_name} stalled, no progress for "
                f"{max_stall_s}s (last bytes seen: {last_total_bytes_seen})"
            )
        thread.join(timeout=1.0)
        try:
            model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
            if model_dir.exists():
                total_bytes_seen = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
                # Update ``last_byte_change_time`` ONLY when
                if total_bytes_seen != last_total_bytes_seen:
                    last_byte_change_time = time.monotonic()
                total_mb_seen = total_bytes_seen // (1024 * 1024)
                pct = min(95, int(10 + (total_mb_seen / target_mb) * 85))
                # Log progress at whole-number percentage thresholds
                if pct >= 25 and pct % 25 == 0:
                    log.info(
                        "[SERVICE] Download of '%s': %d%% (%d MB / ~%d MB)",
                        model_name,
                        pct,
                        total_mb_seen,
                        target_mb,
                    )
                # compute speed & ETA.
                now = time.monotonic()
                elapsed = now - last_progress_time
                delta_bytes = total_bytes_seen - last_total_bytes_seen
                speed_bps: float | None = None
                eta_s: float | None = None
                if elapsed > 0 and delta_bytes >= 0:
                    speed_bps = delta_bytes / elapsed
                    if speed_bps > 0:
                        eta_s = max(
                            0.0,
                            (target_bytes - total_bytes_seen) / speed_bps,
                        )
                last_progress_time = now
                last_total_bytes_seen = total_bytes_seen
                push_progress(
                    event_bus,
                    model_name,
                    pct,
                    f"Downloading {model_name}: {total_mb_seen} MB / ~{target_mb} MB",
                    downloaded_bytes=total_bytes_seen,
                    total_bytes=target_bytes,
                    speed_bytes_per_sec=speed_bps,
                    eta_seconds=eta_s,
                )
        except Exception:
            # previously pass, silently swallowed per-iteration
            log.debug(
                "[SERVICE] download progress poll failed (non-fatal)",
                exc_info=True,
            )

    return ("cancelled" if cancelled else "complete", last_total_bytes_seen)


def make_segmented_progress_tracker(
    *,
    event_bus,
    model_name: str,
    target_mb: int,
    target_bytes: int,
    phase_total_bytes: int,
    is_paused_fn: Callable[[], bool],
) -> Callable[[int, int], None]:
    """Build the Phase-B (segmented fast lane) progress callback."""
    lock = threading.Lock()
    last_push_at = [0.0]
    last_done = [0]
    last_push_time = [time.monotonic()]
    last_paused = [bool(is_paused_fn())]

    def on_progress(done: int, _total: int) -> None:
        paused = bool(is_paused_fn())
        if paused != last_paused[0]:
            last_paused[0] = paused
            pct = min(95, int(10 + (done / max(1, phase_total_bytes)) * 85))
            if paused:
                push_progress(
                    event_bus,
                    model_name,
                    pct,
                    f"Download of {model_name} paused",
                    downloaded_bytes=done,
                    total_bytes=target_bytes,
                    paused=True,
                )
            else:
                push_progress(
                    event_bus,
                    model_name,
                    pct,
                    f"Download of {model_name} resumed",
                    downloaded_bytes=done,
                    total_bytes=target_bytes,
                    resumed=True,
                )
                # Fresh speed baseline so the first post-resume push
                last_done[0] = done
                last_push_time[0] = time.monotonic()
            return
        if paused:
            return
        now = time.monotonic()
        with lock:
            if now - last_push_at[0] < 0.25 and done < phase_total_bytes:
                return
            last_push_at[0] = now
        elapsed = now - last_push_time[0]
        delta_bytes = done - last_done[0]
        speed_bps: float | None = None
        eta_s: float | None = None
        if elapsed > 0 and delta_bytes >= 0:
            speed_bps = delta_bytes / elapsed
            if speed_bps > 0:
                eta_s = max(0.0, (phase_total_bytes - done) / speed_bps)
        last_done[0] = done
        last_push_time[0] = now
        pct = min(95, int(10 + (done / max(1, phase_total_bytes)) * 85))
        mb = done // (1024 * 1024)
        push_progress(
            event_bus,
            model_name,
            pct,
            f"Downloading {model_name}: {mb} MB / ~{target_mb} MB",
            downloaded_bytes=done,
            total_bytes=target_bytes,
            speed_bytes_per_sec=speed_bps,
            eta_seconds=eta_s,
        )

    return on_progress


__all__ = [
    "DownloadOutcome",
    "make_segmented_progress_tracker",
    "push_progress",
    "notify",
    "poll_download_progress",
]
