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
import uuid
from typing import Any


def make_recorder(config: Any = None, **config_fields: Any) -> Any:
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

    Parameters
    ----------
    config : optional
        Fully-formed config object to construct the ``Recorder`` with,
        bypassing the default ``MagicMock`` (e.g. a real ``Config()``
        for tests that exercise real dataclass semantics). When given,
        ``**config_fields`` is rejected (the caller owns the config).
    **config_fields:
        Extra config overrides, applied to the ``MagicMock`` config
        BEFORE the ``Recorder`` constructor runs — so fields the
        constructor itself reads (e.g. ``pre_roll_buffer_seconds``,
        which sizes ``_preroll_buffer.maxlen`` during ``__init__``) are
        honoured, as are lazily-read fields. Overrides replace the
        defaults above when the names collide (e.g.
        ``make_recorder(sample_rate=48000)``).

    Returns
    -------
    voice_typer.server.recording.recorder.Recorder
        A real ``Recorder`` instance with a mocked config. Caller is
        free to mutate any attribute before exercising the secure-clear
        path.
    """
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


def _owner_tag(name: str) -> str:
    """Owner tag for the given base thread name.

    Recorder worker threads carry a ``base|owner`` two-part name where
    ``base`` is the canonical worker name (one of ``WORKER_THREAD_NAMES``)
    and ``owner`` is a unique per-recorder instance id (e.g.
    ``audio-worker|rec-3f2a19c4``). The canonical name stays the
    ``name.split("|")[0]`` prefix, so every consumer that matches
    threads by worker identity (log filters, thread-name diagnostics,
    ``threading.enumerate()`` scans) reads the same identity from the
    prefix.
    """
    return f"{name}|rec-{uuid.uuid4().hex[:8]}"


def thread_base_name(thread: Any) -> str:
    """Return the canonical worker base name for a (possibly tagged) thread.

    Untagged threads are returned unchanged; tagged threads (``base|owner``)
    are split at the first ``|``. Non-string names return ``""``.
    """
    name = getattr(thread, "name", "")
    if not isinstance(name, str):
        return ""
    return name.split("|", 1)[0]


def is_worker_thread(thread: Any) -> bool:
    """True when the thread's base name matches a recorder worker name."""
    return thread_base_name(thread) in WORKER_THREAD_NAMES


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


def snapshot_worker_threads() -> frozenset:
    """Snapshot the currently-live recorder worker threads (by identity).

    Used by the guard helpers to scope a wait to the DELTA of threads a
    test spawned: under full-suite xdist load, worker threads leaked by
    EARLIER test files in the same xdist worker process stay alive
    globally, so a global name-based wait would block (and flake) on
    threads this test never created. Identity-scoping via
    ``threading.enumerate()`` keeps the wait exact: only threads that
    were not live at snapshot time (or whose identity changed) count.
    """
    return frozenset(t for t in threading.enumerate() if is_worker_thread(t))


def _worker_threads_in(live: frozenset) -> list:
    """Return the live worker threads from a snapshot set (alive now)."""
    return [t for t in live if t.is_alive()]


def wait_for_workers_stopped(
    recorder: Any,
    *,
    stop: Any = None,
    # 15s (was 5s): the poll is CONDITION-BASED — this bound only
    # absorbs scheduler contention under ``-n auto`` xdist (a worker
    # thread mid-teardown just needs CPU). A REAL leak keeps the
    # threads alive past ANY deadline, so the caller's terminal
    # ``assert`` on the returned ``False`` still fires (no assertion is
    # loosened; the 4/4-isolation pass contract is unchanged).
    timeout: float = 15.0,
    baseline: frozenset | None = None,
) -> bool:
    """Poll (bounded) until worker refs are None AND no worker thread
    is alive beyond the ``baseline`` snapshot.

    This is the GT-23 load-flake guard: under a loaded runner
    (full-suite serial run), a worker started by the last in-flight
    ``start()`` can still be mid-teardown right after ``stop()``
    returns, and a superseded zombie worker (the stale-alive branch in
    ``_start_audio_worker`` replaced its stop/wake events and discarded
    its ref) may not have reached its next loop iteration yet.

    Thread-ownership scoping (the S5 fix): when ``baseline`` is given
    (a :func:`snapshot_worker_threads` result taken BEFORE the test
    spawned any worker), the wait only requires the DELTA to drain —
    worker threads leaked by earlier test files in the same xdist
    worker process no longer block (or flake) this test's wait. The
    recorder's own spawn sites tag each worker thread with a unique
    owner id (``base|owner`` names), so threads this test spawned are
    always outside the baseline and remain FULLY waited on — a real
    leak spawned by the test itself still fails the caller's terminal
    assert exactly as strongly as before. When ``baseline`` is ``None``
    (the default), the wait is global over all worker-named threads —
    the pre-S5 behavior, kept for callers that want the strict global
    drain guarantee.

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
        Maximum seconds to poll (15s default: generous headroom for
        xdist load; the poll exits as soon as the condition holds, so
        the bound is only paid under real scheduler contention). A REAL
        leak keeps the threads alive past the deadline, so the caller's
        terminal ``assert`` on the returned ``False`` still fires.
    baseline:
        Pre-test :func:`snapshot_worker_threads` result. When given,
        only worker threads NOT in the baseline must drain before
        ``True`` is returned.

    Returns
    -------
    bool
        ``True`` when the refs are clear and no (non-baseline) worker
        thread is alive; ``False`` on timeout.
    """
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
            # baseline time. Identity-based (thread objects compare by
            # identity), so a tagged thread spawned after the snapshot
            # is always outside the baseline. Threads leaked by earlier
            # tests keep churning in the background and are ignored.
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
