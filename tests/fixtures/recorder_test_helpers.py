"""Shared ``Recorder`` factory for the secure-clear / hot-swap test suite."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any


def make_recorder(config: Any = None, **config_fields: Any) -> Any:
    """Build a minimal ``Recorder`` with the fields the secure-clear path touches."""
    from unittest.mock import MagicMock, patch

    from voice_typer.server.recording import Recorder

    if config is not None:
        if config_fields:
            raise TypeError(
                "make_recorder(config=...) cannot be combined with config-field overrides; build the config yourself."
            )
        with patch("voice_typer.server.vad.is_available", return_value=False):
            return Recorder(config)

    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    config.max_recording_time_seconds = 900
    config.device = "cpu"
    for name, value in config_fields.items():
        setattr(config, name, value)
    with patch("voice_typer.server.vad.is_available", return_value=False):
        return Recorder(config)


# Mirrors ``_AUDIO_WORKER_THREAD_NAME`` / ``_EVENT_WORKER_THREAD_NAME``
WORKER_THREAD_NAMES = frozenset(
    {
        "audio-worker",
        "event-worker",
        "stream-finished-handler",
        "device-disconnect-handler",
    }
)


def _owner_tag(name: str) -> str:
    """Owner tag for the given base thread name."""
    return f"{name}|rec-{uuid.uuid4().hex[:8]}"


def thread_base_name(thread: Any) -> str:
    """Return the canonical worker base name for a (possibly tagged) thread."""
    name = getattr(thread, "name", "")
    if not isinstance(name, str):
        return ""
    return name.split("|", 1)[0]


def is_worker_thread(thread: Any) -> bool:
    """True when the thread's base name matches a recorder worker name."""
    return thread_base_name(thread) in WORKER_THREAD_NAMES


def reap_stale_worker_refs(recorder: Any) -> None:
    """Clear worker references whose threads have already exited."""
    worker = getattr(recorder, "_worker_thread", None)
    if worker is not None and not worker.is_alive():
        recorder._worker_thread = None
    event_worker = getattr(recorder, "_event_worker_thread", None)
    if event_worker is not None and not event_worker.is_alive():
        recorder._event_worker_thread = None


def snapshot_worker_threads() -> frozenset:
    """Snapshot the currently-live recorder worker threads (by identity)."""
    return frozenset(t for t in threading.enumerate() if is_worker_thread(t))


def _worker_threads_in(live: frozenset) -> list:
    """Return the live worker threads from a snapshot set (alive now)."""
    return [t for t in live if t.is_alive()]


def wait_for_workers_stopped(
    recorder: Any,
    *,
    stop: Any = None,
    timeout: float = 15.0,
    baseline: frozenset | None = None,
) -> bool:
    """Poll (bounded) until worker refs are None AND no worker thread"""
    if stop is None:
        stop = getattr(recorder, "stop", None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reap_stale_worker_refs(recorder)
        live_all = threading.enumerate()
        if baseline is None:
            live_workers = [t for t in live_all if is_worker_thread(t)]
        else:
            # Delta wait: a thread counts only if it was NOT live at
            live_workers = [t for t in live_all if is_worker_thread(t) and t not in baseline]
        if (
            not live_workers
            and getattr(recorder, "_worker_thread", None) is None
            and getattr(recorder, "_event_worker_thread", None) is None
        ):
            return True
        if stop is not None:
            stop()
        time.sleep(0.01)
    return False


__all__ = [
    "WORKER_THREAD_NAMES",
    "is_worker_thread",
    "make_recorder",
    "reap_stale_worker_refs",
    "snapshot_worker_threads",
    "thread_base_name",
    "wait_for_workers_stopped",
]
