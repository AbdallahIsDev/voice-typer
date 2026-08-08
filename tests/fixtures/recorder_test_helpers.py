"""Shared ``Recorder`` factory for the secure-clear / hot-swap test suite.

This module owns the SINGLE canonical ``_make_recorder`` helper used by
every test file that exercises the secure-clear / hot-swap path on
:class:`voice_typer.server.recording.recorder.Recorder`.

Before this module existed, two byte-for-byte copies of the helper
were sprinkled across the test tree:

- ``tests/test_secure_clear_no_resample_segments.py`` (line 46)
- ``tests/test_secure_clear_array.py`` (line 131)

Both copies construct a real ``Recorder`` with a ``MagicMock`` config
(sensible values for the fields the secure-clear path touches) and
patch ``voice_typer.server.vad.is_available`` to return ``False`` so
the test doesn't pay the ~17s torch-import cost on the sandbox.

The two copies were literally identical (verified by ``diff``) — a
classic copy-paste leak that drifts the next time one is updated and
the other isn't.

Centralising the factory here means future additions to the
``Recorder`` constructor (e.g. a new ``_secure_clear`` field that
tests need to reset between cases) only need to update ONE place —
this module — and every secure-clear test picks up the fix
automatically.

The migration target is documented as Remaining Work:
this module ONLY provides the factory — call sites
(``tests/test_secure_clear_no_resample_segments.py`` and
``tests/test_secure_clear_array.py``) still use their inline
``_make_recorder`` until a follow-up task migrates them.

Worker-lifecycle guard helpers
------------------------------
This module ALSO owns the shared zombie-ref guard used by every test
that hammers a real ``Recorder`` (concurrent ``start()``/``stop()`` /
``discard()``) and then asserts ``_worker_thread is None`` /
``_event_worker_thread is None``. See :func:`reap_stale_worker_refs`
and :func:`wait_for_workers_stopped` — both exist in ONE place so a
future fix cannot drift between the hammer tests that need them.

NOTE: there are OTHER ``_make_recorder`` helpers in the test tree
(e.g. ``tests/test_recorder_mono_and_disconnect_fixes.py``,
``tests/test_hot_swap_secure_clear.py``,
``tests/test_recording_discard.py``,
``tests/test_recorder_device_cache_prewarm.py``,
``tests/test_audio_pipeline_process_chunk.py``,
``tests/test_stream_lifecycle_module.py``). Those have DIFFERENT
shapes (different config field sets, different post-construction
mutations like ``r._recording_event.set()`` / ``r._stream = MagicMock()``)
and are NOT byte-for-byte duplicates — they are documented as
Remaining Work for a separate consolidation pass. This module targets
ONLY the two byte-for-byte identical copies that were explicitly
called out for consolidation.
"""

from __future__ import annotations

import threading
import time
from typing import Any


def make_recorder() -> Any:
    """Build a minimal ``Recorder`` with the fields the secure-clear path touches.

    Avoids spawning real audio threads / sounddevice probes. The VAD
    availability check is mocked out because importing torch takes
    ~17s on this sandbox (see ``voice_typer.server.vad.is_available``),
    which blows the per-test timeout. The secure-clear path doesn't
    depend on VAD, so mocking the check is safe.

    The returned ``Recorder`` is configured with a ``MagicMock`` config
    that pre-populates the fields the secure-clear path reads:

    - ``config.sample_rate = 16000``
    - ``config.microphone = None``
    - ``config.silence_warning_seconds = 20.0``
    - ``config.stop_on_silence_seconds = 120.0``
    - ``config.max_recording_time_seconds = 900``
    - ``config.device = "cpu"``

    Tests that need different values (e.g. ``config.sample_rate = 48000``
    to exercise the resample path) should override the field on the
    returned instance — the constructor has already run, but the
    secure-clear path reads ``config.sample_rate`` lazily so a
    post-construction mutation is honoured.

    Returns
    -------
    voice_typer.server.recording.recorder.Recorder
        A real ``Recorder`` instance with a mocked config. Caller is
        free to mutate any attribute before exercising the secure-clear
        path.
    """
    from unittest.mock import MagicMock, patch

    from voice_typer.server.recording import Recorder

    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    config.max_recording_time_seconds = 900
    config.device = "cpu"
    with patch("voice_typer.server.vad.is_available", return_value=False):
        return Recorder(config)


# Mirrors ``_AUDIO_WORKER_THREAD_NAME`` / ``_EVENT_WORKER_THREAD_NAME``
# and the ``"stream-finished-handler"`` / ``"device-disconnect-handler"``
# spawn-site names in ``voice_typer/server/recording/recorder.py``. Kept
# as literals (NOT imported) so this module stays free of heavy server
# imports — if the source constants are ever renamed, update them here.
WORKER_THREAD_NAMES = frozenset(
    {
        "audio-worker",
        "event-worker",
        "stream-finished-handler",
        "device-disconnect-handler",
    }
)


def reap_stale_worker_refs(recorder: Any) -> None:
    """Clear worker references whose threads have already exited.

    ``stop()`` / ``discard()`` fast-path when the recorder is idle, so
    a worker that exited AFTER a timed-out join (the zombie mitigation
    in ``stop_*_worker_body`` keeps the ref set until the daemon exits)
    leaves a stale reference that nothing else clears. Once the thread
    has actually exited, clear the ref here so the GT-23 load-flake
    guard loops can reach their terminal ``is None`` assertions.
    """
    worker = getattr(recorder, "_worker_thread", None)
    if worker is not None and not worker.is_alive():
        recorder._worker_thread = None
    event_worker = getattr(recorder, "_event_worker_thread", None)
    if event_worker is not None and not event_worker.is_alive():
        recorder._event_worker_thread = None


def wait_for_workers_stopped(
    recorder: Any,
    *,
    stop: Any = None,
    timeout: float = 5.0,
) -> bool:
    """Poll (bounded) until worker refs are None AND no worker-named
    thread is alive.

    This is the GT-23 load-flake guard: under a loaded runner
    (full-suite serial run), a worker started by the last in-flight
    ``start()`` can still be mid-teardown right after ``stop()``
    returns, and a superseded zombie worker (the stale-alive branch in
    ``_start_audio_worker`` replaced its stop/wake events and discarded
    its ref) may not have reached its next loop iteration yet.

    Parameters
    ----------
    recorder:
        The ``Recorder`` (or a close fake) whose worker refs must
        clear. Reads ``_worker_thread`` / ``_event_worker_thread`` via
        ``getattr`` so fakes that lack one of the attributes still
        work.
    stop:
        Callable re-invoked each poll iteration (``stop()`` is
        idempotent) to drive a mid-teardown worker out. Defaults to
        ``recorder.stop`` when the recorder has one — pass ``None`` to
        skip re-invoking (required for fakes without a ``stop``
        method).
    timeout:
        Maximum seconds to poll. A REAL leak keeps the threads alive
        past the deadline, so the caller's terminal ``assert`` on the
        returned ``False`` still fires.

    Returns
    -------
    bool
        ``True`` when the refs are clear and no worker-named thread is
        alive; ``False`` on timeout.
    """
    if stop is None:
        stop = getattr(recorder, "stop", None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reap_stale_worker_refs(recorder)
        live = [t for t in threading.enumerate() if t.name in WORKER_THREAD_NAMES]
        if (
            not live
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
    "make_recorder",
    "reap_stale_worker_refs",
    "wait_for_workers_stopped",
]
