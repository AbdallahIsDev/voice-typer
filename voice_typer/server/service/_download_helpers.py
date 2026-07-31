"""Module-level helpers extracted from ``ModelMixin.download_model``.

the 558-LOC ``download_model`` god method (in
``voice_typer/server/service/model.py``) previously defined two nested
closures (``_push_progress`` at line 718 and ``_notify`` at line 755)
and inlined a polling loop with a pause/resume state machine.  Those
helpers are extracted here as plain module-level functions so the
three ``_download_*`` branch methods on :class:`ModelMixin` can share
them and so each branch is independently testable.

The functions are PURE (no ``self`` references): they take explicit
args (``event_bus``, ``tray``, ``model_name``, ...) so they can be
unit-tested in isolation without instantiating a full
:class:`VoiceTyperService`.

Public surface:

* :data:`DownloadOutcome` — the TypedDict returned by every
  ``_download_*`` branch method.  The dispatcher
  (:meth:`ModelMixin.download_model`) converts it to a plain ``dict``
  via ``dict(outcome)`` for IPC serialization, preserving the exact
  runtime shape the renderer expects.
* :func:`push_progress` — extracted from the ``_push_progress``
  closure.  Publishes a ``download_progress`` event to the event bus.
* :func:`notify` — extracted from the ``_notify`` closure.  Forwards a
  tray notification, swallowing errors.
* :func:`poll_download_progress` — extracted from the polling loop.
  Owns the pause/resume state machine.  Returns a ``(outcome,
  last_total_bytes_seen)`` tuple so the caller can log the final byte
  count (preserving the original log message text).
"""

from __future__ import annotations

import logging
import time
from typing import TypedDict

log = logging.getLogger(__name__)


class DownloadOutcome(TypedDict, total=False):
    """Outcome of a model download attempt.

    uniform return-type for the three ``_download_*`` branch
        methods on :class:`ModelMixin` so the
        :meth:`ModelMixin.download_model` dispatcher has a single contract.
        ``total=False`` so each branch only populates the fields it
        actually returns — the runtime dict shape is preserved exactly
        (the dispatcher converts to a plain ``dict`` via ``dict(outcome)``
        for IPC serialization).

        Field reference (mirrors the 10 distinct return shapes the
        original monolithic ``download_model`` produced):

        * ``success`` — always present (bool).
        * ``error`` — present on failure (str).
        * ``model`` — present on most paths (str).  Omitted on the
          cancelled, qwen-not-configured, unknown-model, and
          exception-handler paths (preserving the original shapes).
        * ``message`` — present on the cached-qwen and cancelled paths.
        * ``cancelled`` — present on the user-cancelled path.
        * ``consent_required`` — present on the HuggingFace consent-gate
    path ().
    * ``reason`` — present on the parakeet failure path ().
    """

    success: bool
    error: str
    model: str
    message: str
    cancelled: bool
    consent_required: bool
    reason: str


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
) -> None:
    """Push a ``download_progress`` event with rich metadata.

        Extracted from the ``_push_progress`` closure that lived inside
        :meth:`ModelMixin.download_model` (originally at line 718 of
        ``model.py``).  The closure captured ``model_name`` and
        ``event_bus`` from the enclosing scope; this module-level function
        takes them as explicit args so it can be unit-tested in isolation.

        ``progress`` (0-100) and ``status`` (human-readable) are always
    present (backward compat with  tests).  The remaining fields
        are optional and only included when meaningful (e.g. during active
        transfer, not for "cached" or "cancelled" events).
    """
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
    event_bus.publish({"type": "download_progress", "data": data})


def notify(tray, model_name: str, title: str, message: str) -> None:
    """Forward a tray notification, swallowing errors.

    Extracted from the ``_notify`` closure that lived inside
    :meth:`ModelMixin.download_model` (originally at line 756 of
    ``model.py``).  The closure captured ``self._app.tray``; this
    module-level function takes the tray as an explicit arg.  The
    ``model_name`` is included for log context only (the original
    closure captured it but did not use it in the message).
    """
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
) -> tuple[str, int]:
    """Poll the HF cache directory size while the download thread runs.

        Extracted from the polling loop that lived inside the whisper
        branch of :meth:`ModelMixin.download_model` (originally at lines
        937-1037 of ``model.py``).  Owns the pause/resume state machine
    () and the per-iteration progress event.

        Args:
            thread: the daemon thread running ``_do_download``.  The loop
                polls ``thread.is_alive()`` and ``thread.join(timeout=1.0)``.
            target_bytes: the expected download size in bytes (from the
                model registry's ``download_size_mb`` field, converted to
                bytes by the caller).
            target_mb: the same size in MB (kept as a separate arg so the
                log messages and percentage calc use the same int the
                original code used).
            model_name: the model being downloaded (for log messages and
                progress events).
            repo_id: the HuggingFace repo id (used to construct the
                per-repo cache subdir path).
            cache_dir: the HF hub cache root (``_config_dir() /
                "huggingface" / "hub"``).
    download_id: the per-download cancellation key (
                SERVICE-1).  Passed to ``is_cancelled_fn``.
            event_bus: the event bus module (for ``push_progress`` calls).
            is_cancelled_fn: callable taking ``download_id`` and returning
                ``True`` if the download has been cancelled.  Bound to
                :meth:`ModelMixin._is_download_cancelled` by the caller.

        Returns:
            A ``(outcome, last_total_bytes_seen)`` tuple where ``outcome``
            is ``"cancelled"`` (the user cancelled — caller returns the
            cancelled dict) or ``"complete"`` (the thread exited — caller
            inspects ``download_err`` to decide whether to raise or
            continue), and ``last_total_bytes_seen`` is the last byte
            count observed (for the caller's completion log message).

        The caller is responsible for unregistering the download and
        clearing the pause flag (so the same cleanup runs on every exit
        path: success, failure, cancellation).
    """
    # PERF-21: scope the filesystem walk to the
    # in-progress model's HF cache subdir, NOT the entire HF hub cache
    # root.  Previously ``cache_dir.rglob("*")`` ran once per second
    # and stat'd every file in every cached model dir (thousands of
    # stat() syscalls/s, 10-40% CPU).  Now we only walk the
    # downloading model's own directory.
    from voice_typer.server.asr_setup import is_download_paused, wait_while_paused

    cancelled = False
    # track pause/resume transitions so we only push
    # the event once per state change (not once per 1-second poll
    # iteration).
    last_paused_state = False
    # track timing for speed / ETA.
    last_progress_time = time.monotonic()
    last_total_bytes_seen = 0

    while thread.is_alive():
        # SERVICE-1: check for cancellation via the
        # per-download helper so a sibling download_model call's
        # cancel signal (or cleanup) doesn't bleed into this loop.
        if is_cancelled_fn(download_id):
            cancelled = True
            log.info("[SERVICE] Download of %s cancelled by user", model_name)
            push_progress(event_bus, model_name, 0, "Download cancelled")
            break
        # check for pause.  When paused, block for up
        # to 1s (replacing the normal ``t.join(timeout=1.0)``), then
        # continue the loop.  We push a single ``paused: True`` event
        # on transition and a single ``resumed: True`` event when the
        # pause clears.
        currently_paused = is_download_paused()
        if currently_paused != last_paused_state:
            # State transition — push the event.
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
            last_paused_state = currently_paused
        if currently_paused:
            # Wait for resume (or cancel), then loop.
            wait_while_paused(timeout_s=1.0)
            continue
        thread.join(timeout=1.0)
        try:
            model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
            if model_dir.exists():
                total_bytes_seen = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
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
            # previously pass — silently swallowed per-iteration
            # polling failures. Log at DEBUG (non-fatal) so a transient
            # filesystem error doesn't freeze the progress bar with no log.
            log.debug(
                "[SERVICE] download progress poll failed (non-fatal)",
                exc_info=True,
            )

    return ("cancelled" if cancelled else "complete", last_total_bytes_seen)


__all__ = [
    "DownloadOutcome",
    "push_progress",
    "notify",
    "poll_download_progress",
]
