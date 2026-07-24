"""GT-23 / GT-24 regression tests for the recorder worker & stream lifecycle.

GT-23: Recorder worker thread lifecycle race
--------------------------------------------
``_start_audio_worker`` / ``_stop_audio_worker`` (and the event-worker
pair) previously performed read-modify-write on ``self._worker_thread``
/ ``self._event_worker_thread`` with no synchronization.
``Recorder.start()`` / ``stop()`` / ``discard()`` are reachable from
multiple threads (toggle thread, auto-stop Timer thread, ESC-cancel
thread, device-disconnect handler thread). Concurrent start+stop could
have both readers see ``self._worker_thread is None``, the starter
create+assign+start a fresh worker, and the stopper return early
leaving that fresh worker untracked (a leak).

Fix: a new ``self._worker_lifecycle_lock`` serializes the entire
read-check-create-start (in ``_start_*``) and
read-check-clear-join-unregister (in ``_stop_*``) sequences.
``self._lock`` is intentionally NOT held across ``thread.join()`` —
the worker thread acquires ``self._lock`` inside
``_process_audio_chunk`` for the buffer append, so holding it across
``join()`` would deadlock.

GT-24: Stream-finished callback passes ``_captured_generation=0``
-----------------------------------------------------------------
``_stream_finished_callback`` (PortAudio thread) previously started
the disconnect handler with NO ``_captured_generation`` kwarg — it
defaulted to 0. Before any stop had occurred ``_stop_generation`` is
also 0, so the bouncer ``_captured_generation != self._stop_generation``
was ``0 != 0 == False`` and never bailed out. The fix captures
``gen = self._stop_generation`` at scheduling time and passes it via
``kwargs={'_captured_generation': gen}`` (mirroring the
``_process_audio_chunk`` spawn site). A new
``self._stream_lifecycle_lock`` serializes ``_teardown_stream`` against
the stream-restart block of ``_handle_device_disconnect`` so a
concurrent ``stop()`` cannot mutate ``self._stream`` mid-restart.
"""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Test helpers ───────────────────────────────────────────────────


class _OkStream:
    """No-op InputStream mock for tests that don't touch real audio."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _patch_ok_stream(monkeypatch, recording_mod):
    """Patch sounddevice with a no-op InputStream + permissive device query.

    Mirrors the helper in ``tests/test_audio_callback.py`` so the
    recorder can be ``start()``-ed headless without real audio hardware.
    """
    monkeypatch.setattr(recording_mod.sd, "InputStream", _OkStream)

    def _query_devices(*args, **kwargs):
        device_dict = {
            "max_input_channels": 1,
            "default_samplerate": 16000,
            "hostapi": 0,
            "index": 0,
            "name": "Mock Input",
        }
        if not args and not kwargs:
            return [device_dict]
        return device_dict

    monkeypatch.setattr(recording_mod.sd, "query_devices", _query_devices)
    monkeypatch.setattr(
        recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"}
    )


# ── GT-23: worker lifecycle lock — static & structural checks ──────


class TestGT23WorkerLifecycleLock:
    """GT-23: ``_worker_lifecycle_lock`` serializes the read-modify-write
    sequences in ``_start_audio_worker`` / ``_stop_audio_worker`` /
    ``_start_event_worker`` / ``_stop_event_worker``."""

    def test_lock_attribute_exists(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert isinstance(r._worker_lifecycle_lock, type(threading.Lock())), (
            "GT-23: _worker_lifecycle_lock must be a threading.Lock instance"
        )

    def test_start_audio_worker_holds_lock(self):
        """Source inspection: ``_start_audio_worker`` must acquire
        ``_worker_lifecycle_lock`` across the read-check-create-start
        sequence so a concurrent ``_stop_audio_worker`` cannot observe
        a stale ``None`` mid-create."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._start_audio_worker)
        assert "self._worker_lifecycle_lock" in src, (
            "GT-23: _start_audio_worker must reference _worker_lifecycle_lock"
        )
        assert "with self._worker_lifecycle_lock:" in src, (
            "GT-23: _start_audio_worker must acquire _worker_lifecycle_lock "
            "via a `with` block around the read-check-create-start sequence"
        )

    def test_stop_audio_worker_holds_lock(self):
        """Source inspection: ``_stop_audio_worker`` must acquire
        ``_worker_lifecycle_lock`` across the
        read-check-clear-join-unregister sequence."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._stop_audio_worker)
        assert "with self._worker_lifecycle_lock:" in src, (
            "GT-23: _stop_audio_worker must acquire _worker_lifecycle_lock "
            "via a `with` block around the read-check-clear-join-unregister "
            "sequence"
        )

    def test_stop_audio_worker_does_not_hold_self_lock_across_join(self):
        """GT-23: ``self._lock`` must NOT be held across ``thread.join()``
        in ``_stop_audio_worker`` — the worker thread acquires
        ``self._lock`` inside ``_process_audio_chunk`` for the buffer
        append, so holding it across ``join()`` would deadlock.

        We assert the join happens inside the
        ``_worker_lifecycle_lock`` block (NOT a ``self._lock`` block).
        """
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._stop_audio_worker)
        # The join must be inside the worker_lifecycle_lock block.
        assert "with self._worker_lifecycle_lock:" in src
        # And there must be NO `with self._lock:` wrapping the join.
        assert "with self._lock:" not in src, (
            "GT-23: _stop_audio_worker must NOT acquire self._lock — "
            "holding it across thread.join() would deadlock with "
            "_process_audio_chunk's buffer-append critical section."
        )

    def test_start_event_worker_holds_lock(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._start_event_worker)
        assert "with self._worker_lifecycle_lock:" in src, (
            "GT-23: _start_event_worker must acquire _worker_lifecycle_lock"
        )

    def test_stop_event_worker_holds_lock(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._stop_event_worker)
        assert "with self._worker_lifecycle_lock:" in src, (
            "GT-23: _stop_event_worker must acquire _worker_lifecycle_lock"
        )

    def test_stop_event_worker_does_not_hold_self_lock_across_join(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._stop_event_worker)
        assert "with self._lock:" not in src, (
            "GT-23: _stop_event_worker must NOT acquire self._lock — "
            "holding it across thread.join() would deadlock."
        )


# ── GT-23: concurrent start+stop doesn't leak worker threads ───────


class TestGT23ConcurrentStartStopNoLeak:
    """GT-23: hammer ``start()`` and ``stop()`` from two threads; verify
    no worker thread is left running (leaked) after both threads finish.

    Pre-fix, the read-check-create-start sequence in
    ``_start_audio_worker`` / ``_start_event_worker`` was unsynchronized,
    so a concurrent ``_stop_audio_worker`` could read
    ``self._worker_thread is None`` between the starter's check and
    assignment, return early, and leave the freshly-started worker
    untracked (the worker would run until process exit as a daemon).
    Post-fix, ``_worker_lifecycle_lock`` serializes the sequences so
    either the starter sees the worker already alive (and returns) or
    the stopper sees the new worker (and joins it).
    """

    def test_concurrent_start_stop_no_leak(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        errors: list[Exception] = []
        stop_flag = threading.Event()

        def starter():
            try:
                while not stop_flag.is_set():
                    r.start()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def stopper():
            try:
                while not stop_flag.is_set():
                    r.stop()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=starter, name="gt23-starter")
        t2 = threading.Thread(target=stopper, name="gt23-stopper")
        t1.start()
        t2.start()
        # Hammer for 500ms — long enough to expose the race pre-fix.
        time.sleep(0.5)
        stop_flag.set()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors, f"GT-23: concurrent start()/stop() raised: {errors}"

        # Final cleanup: ensure no worker thread is left running.
        r.stop()
        # Give daemon threads a moment to exit.
        time.sleep(0.1)

        # Assert: no leaked audio-worker / event-worker threads.
        worker_names = {
            "audio-worker",
            "event-worker",
            "stream-finished-handler",
            "device-disconnect-handler",
        }
        # The recorder's tracked worker thread refs must be None after stop().
        assert r._worker_thread is None, (
            "GT-23 regression: r._worker_thread is not None after stop() — "
            "a worker was started but never joined (leaked)."
        )
        assert r._event_worker_thread is None, (
            "GT-23 regression: r._event_worker_thread is not None after "
            "stop() — an event worker was started but never joined (leaked)."
        )

        # Enumerate live threads — none should match the worker names
        # (the recorder's workers are daemons and should have exited).
        live_worker_threads = [
            t for t in threading.enumerate() if t.name in worker_names
        ]
        assert live_worker_threads == [], (
            f"GT-23 regression: {len(live_worker_threads)} worker thread(s) "
            f"still alive after stop(): "
            f"{[(t.name, t.is_alive()) for t in live_worker_threads]}."
        )

    def test_concurrent_start_discard_no_leak(self, monkeypatch):
        """Same as above but with ``discard()`` instead of ``stop()`` —
        ``discard()`` is reachable from the ESC-cancel thread and races
        with ``start()`` from the toggle thread."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        errors: list[Exception] = []
        stop_flag = threading.Event()

        def starter():
            try:
                while not stop_flag.is_set():
                    r.start()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def discarder():
            try:
                while not stop_flag.is_set():
                    r.discard()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=starter, name="gt23-starter-d")
        t2 = threading.Thread(target=discarder, name="gt23-discarder")
        t1.start()
        t2.start()
        time.sleep(0.5)
        stop_flag.set()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors, f"GT-23: concurrent start()/discard() raised: {errors}"

        r.stop()
        time.sleep(0.1)
        assert r._worker_thread is None, (
            "GT-23 regression: worker_thread leaked after start/discard hammer"
        )
        assert r._event_worker_thread is None, (
            "GT-23 regression: event_worker_thread leaked after "
            "start/discard hammer"
        )


# ── GT-24: stream-finished callback captures _captured_generation ──


class TestGT24StreamFinishedCallbackGeneration:
    """GT-24: ``_stream_finished_callback`` must capture
    ``self._stop_generation`` at scheduling time and pass it via
    ``kwargs={'_captured_generation': gen}`` so the spawned
    ``_handle_device_disconnect`` can bail out if a deliberate
    stop/start cycle happened between scheduling and execution.

    Pre-fix, the handler was scheduled with the default
    ``_captured_generation=0``, which matched the initial
    ``_stop_generation=0`` on the first session — defeating the
    bouncer for any stop() that landed between scheduling and execution.
    """

    def test_stream_finished_callback_passes_captured_generation(self):
        """Source inspection: the spawn site in
        ``_stream_finished_callback`` must use the
        ``_captured_generation`` kwarg pattern (mirroring
        ``_process_audio_chunk``'s spawn site)."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._stream_finished_callback)
        assert "_captured_gen" in src or "_captured_generation" in src, (
            "GT-24: _stream_finished_callback must capture the current "
            "stop_generation in a local (_captured_gen) before spawning "
            "the disconnect handler."
        )
        assert "kwargs=" in src, (
            "GT-24: _stream_finished_callback must pass the captured "
            "generation via kwargs={'_captured_generation': gen} so the "
            "handler can bail out if a stop/start cycle happened between "
            "scheduling and execution."
        )
        assert "_captured_generation" in src, (
            "GT-24: the kwargs passed to threading.Thread must include "
            "'_captured_generation' (the keyword argument name expected "
            "by _handle_device_disconnect)."
        )

    def test_stream_finished_callback_does_not_use_default_zero(self):
        """Source inspection: the spawn site must NOT omit the
        ``_captured_generation`` kwarg (which would default to 0 and
        defeat the bouncer on the first session)."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._stream_finished_callback)
        # The threading.Thread(...) call must include kwargs=... — if it
        # doesn't, _captured_generation defaults to 0 in the handler.
        # Find the threading.Thread call and verify kwargs is present.
        assert "threading.Thread(" in src
        assert "kwargs=" in src, (
            "GT-24: _stream_finished_callback must pass kwargs= to "
            "threading.Thread — pre-fix the handler was scheduled with "
            "the default _captured_generation=0 which matched the "
            "initial _stop_generation=0, defeating the bouncer on the "
            "first session."
        )

    def test_handle_device_disconnect_bouncer_intact(self):
        """GT-24: the bouncer at the top of ``_handle_device_disconnect``
        must still compare ``_captured_generation != self._stop_generation``
        and bail out if a stop/start cycle happened between scheduling
        and execution."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._handle_device_disconnect)
        assert "_captured_generation != self._stop_generation" in src, (
            "GT-24: _handle_device_disconnect must retain the "
            "_captured_generation != self._stop_generation bouncer check."
        )

    def test_first_session_handler_bails_when_stop_runs_after_schedule(
        self, monkeypatch
    ):
        """Behavioral: on the first session (``_stop_generation=0``),
        if stop() increments ``_stop_generation`` AFTER the
        ``_stream_finished_callback`` captures the generation, the
        spawned handler MUST bail out. Pre-fix, the captured generation
        was always 0 (the default), and the bouncer
        ``0 != 0 == False`` did NOT bail — so the handler proceeded to
        teardown/restart on top of a stop() that was already in flight.
        """
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            assert r._stop_generation == 0, (
                "first session must start with _stop_generation=0"
            )

            # Simulate the callback capturing gen=0, then stop() running
            # and bumping _stop_generation to 1, then the handler running.
            captured_gen = r._stop_generation  # GT-24: this is what
            # _stream_finished_callback captures at scheduling time.

            # stop() increments _stop_generation. We don't call r.stop()
            # directly because that would tear down the stream and stop
            # the workers — instead we just bump the counter (mirroring
            # the race window where stop() has run partway).
            r._stop_generation += 1

            # The bouncer should bail out — the handler was scheduled
            # with gen=0 but _stop_generation is now 1.
            restarted_flag = {"called": False}
            original_resolve = r._resolve_effective_sample_rate

            def tracking_resolve(*args, **kwargs):
                restarted_flag["called"] = True
                return original_resolve(*args, **kwargs)

            r._resolve_effective_sample_rate = tracking_resolve

            r._handle_device_disconnect(_captured_generation=captured_gen)

            assert not restarted_flag["called"], (
                "GT-24 regression: _handle_device_disconnect proceeded to "
                "the restart block despite _stop_generation changing "
                f"({captured_gen} != {r._stop_generation}). The bouncer "
                "must bail out when a stop/start cycle happened between "
                "scheduling and execution."
            )
        finally:
            # Restore stop_generation to 0 so r.stop() doesn't see a
            # stale counter (defensive — stop() doesn't read the value).
            r._stop_generation = max(r._stop_generation - 1, 0)
            r.stop()


# ── GT-24: _stream_lifecycle_lock structural checks ────────────────


class TestGT24StreamLifecycleLock:
    """GT-24: ``_stream_lifecycle_lock`` serializes stream teardown
    (``_teardown_stream``) against the stream-restart block of
    ``_handle_device_disconnect`` so a concurrent ``stop()`` /
    ``discard()`` cannot mutate ``self._stream`` mid-flight."""

    def test_lock_attribute_exists(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert isinstance(r._stream_lifecycle_lock, type(threading.Lock())), (
            "GT-24: _stream_lifecycle_lock must be a threading.Lock instance"
        )

    def test_teardown_stream_uses_lock(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._teardown_stream)
        assert "_stream_lifecycle_lock" in src, (
            "GT-24: _teardown_stream must reference _stream_lifecycle_lock"
        )

    def test_handle_device_disconnect_restart_uses_lock(self):
        """Source inspection: the restart block of
        ``_handle_device_disconnect`` (the try/except that opens a new
        ``sd.InputStream`` and assigns ``self._stream``) must be wrapped
        in ``with self._stream_lifecycle_lock:`` so a concurrent
        ``stop()`` cannot mutate ``self._stream`` mid-restart."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._handle_device_disconnect)
        assert "with self._stream_lifecycle_lock:" in src, (
            "GT-24: _handle_device_disconnect must acquire "
            "_stream_lifecycle_lock around the stream-restart block"
        )

    def test_handle_device_disconnect_rechecks_bouncer_under_lock(self):
        """GT-24: the restart block must re-check the bouncer conditions
        (``_captured_generation != self._stop_generation`` AND
        ``_recording_event.is_set()``) AFTER acquiring the lock — a
        concurrent ``stop()`` may have run between the
        ``_teardown_stream()`` call (which releases the lock) and the
        re-acquire for the restart block."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._handle_device_disconnect)
        # Find the restart block (after `with self._stream_lifecycle_lock:`).
        lock_idx = src.find("with self._stream_lifecycle_lock:")
        assert lock_idx >= 0, "GT-24: lock block not found"
        restart_src = src[lock_idx:]
        assert "_captured_generation != self._stop_generation" in restart_src, (
            "GT-24: the restart block (under _stream_lifecycle_lock) must "
            "re-check _captured_generation != self._stop_generation to "
            "handle the race where stop() ran between teardown and restart."
        )
        assert "_recording_event.is_set()" in restart_src, (
            "GT-24: the restart block (under _stream_lifecycle_lock) must "
            "re-check _recording_event.is_set() to handle the race where "
            "stop() cleared the event between teardown and restart."
        )

    def test_teardown_stream_returns_without_blocking_when_lock_held(self):
        """GT-24: ``_teardown_stream`` must use non-blocking acquire so
        ``__del__`` (best-effort cleanup) can't block on a long-running
        ``stop()`` / ``discard()`` / disconnect handler holding the lock.
        If another thread holds the lock, ``_teardown_stream`` returns
        immediately (the holder will finish the teardown)."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Hold the lock from this thread.
        r._stream_lifecycle_lock.acquire()
        try:
            # _teardown_stream must NOT block — it should return immediately.
            done_flag = threading.Event()

            def call_teardown():
                r._teardown_stream()
                done_flag.set()

            t = threading.Thread(target=call_teardown, name="gt24-teardown")
            t.start()
            # If _teardown_stream blocks (regression), this assertion
            # fails after 2s.
            acquired = done_flag.wait(timeout=2.0)
            assert acquired, (
                "GT-24 regression: _teardown_stream blocked for >2s "
                "while another thread held _stream_lifecycle_lock. "
                "It must use non-blocking acquire so __del__ can't "
                "deadlock on a long-running stop()/discard()."
            )
        finally:
            r._stream_lifecycle_lock.release()
            t.join(timeout=1.0)

    def test_teardown_stream_idempotent_when_uncontended(self, monkeypatch):
        """GT-24: when the lock is uncontended, ``_teardown_stream``
        must still tear down the stream and remain idempotent (matches
        the pre-fix 17-H-FIX-2 contract)."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._stream = MagicMock()
        # First call tears down.
        r._teardown_stream()
        assert r._stream is None
        # Second call is a no-op (idempotent).
        r._teardown_stream()
        assert r._stream is None


# ── GT-24: stream-finished callback doesn't fire on first session ──


class TestGT24StreamFinishedCallbackFirstSession:
    """GT-24: on the first session (before any ``stop()``), the
    ``_stream_finished_callback`` must NOT spawn a disconnect handler
    that proceeds to teardown/restart the active recording.

    The existing ``_user_stop_pending`` / ``_recording_event.is_set()``
    guards at the top of ``_stream_finished_callback`` already suppress
    the spawn when recording is active. The GT-24 fix adds
    defense-in-depth: even if a handler IS spawned (e.g., a PortAudio
    glitch cleared ``_recording_event`` between the callback firing and
    the check), it carries the captured ``_stop_generation`` and will
    bail out if a deliberate stop/start cycle landed between scheduling
    and execution.
    """

    def test_callback_does_not_spawn_handler_when_recording_active(
        self, monkeypatch
    ):
        """When recording is active (``_recording_event.is_set()``) and
        no stop has been requested (``_user_stop_pending=False``), the
        callback must NOT spawn a disconnect handler — there's nothing
        to recover from. This is the first-session invariant: the
        callback only fires the handler when recording was unexpectedly
        cleared."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # Count stream-finished-handler spawns.
        spawn_count = {"n": 0}
        real_thread_init = threading.Thread.__init__
        real_thread_start = threading.Thread.start

        def counting_init(self, *args, **kwargs):
            real_thread_init(self, *args, **kwargs)
            if self.name == "stream-finished-handler":
                spawn_count["n"] += 1

        def counting_start(self):
            if self.name == "stream-finished-handler":
                # Suppress the real handler — we only want to count
                # spawns, not actually run the disconnect recovery.
                return
            real_thread_start(self)

        monkeypatch.setattr(threading.Thread, "__init__", counting_init)
        monkeypatch.setattr(threading.Thread, "start", counting_start)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # First-session invariant: _stop_generation is 0.
            assert r._stop_generation == 0
            # Recording is active.
            assert r._recording_event.is_set()
            # No stop requested.
            assert r._user_stop_pending is False

            # Fire the callback — it must NOT spawn a handler because
            # recording is still active.
            r._stream_finished_callback()

            assert spawn_count["n"] == 0, (
                "GT-24 regression: _stream_finished_callback spawned a "
                f"disconnect handler ({spawn_count['n']} spawns) on the "
                "first session while recording was active. The callback "
                "must only spawn a handler when recording was unambiguously "
                "stopped (not while it's still active)."
            )
        finally:
            # Restore Thread.__init__/start before r.stop() so the
            # recorder's worker threads can actually start (they were
            # never suppressed by counting_start — only
            # stream-finished-handler was — but stop() needs to be able
            # to spawn its own bookkeeping if any).
            monkeypatch.setattr(threading.Thread, "__init__", real_thread_init)
            monkeypatch.setattr(threading.Thread, "start", real_thread_start)
            r.stop()

    def test_callback_does_not_spawn_handler_when_user_stop_pending(
        self, monkeypatch
    ):
        """When ``_user_stop_pending=True`` (stop() is in flight), the
        callback must NOT spawn a handler — the stream finished because
        the user pressed stop, not because of a disconnect."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        spawn_count = {"n": 0}
        real_thread_init = threading.Thread.__init__
        real_thread_start = threading.Thread.start

        def counting_init(self, *args, **kwargs):
            real_thread_init(self, *args, **kwargs)
            if self.name == "stream-finished-handler":
                spawn_count["n"] += 1

        def counting_start(self):
            if self.name == "stream-finished-handler":
                return
            real_thread_start(self)

        monkeypatch.setattr(threading.Thread, "__init__", counting_init)
        monkeypatch.setattr(threading.Thread, "start", counting_start)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Simulate stop() having set _user_stop_pending but not yet
            # torn down the stream (the race window where
            # _stream_finished_callback fires from PortAudio's thread
            # between stop() setting the flag and _teardown_stream
            # completing).
            r._user_stop_pending = True

            r._stream_finished_callback()

            assert spawn_count["n"] == 0, (
                "GT-24 regression: _stream_finished_callback spawned a "
                f"handler ({spawn_count['n']} spawns) when "
                "_user_stop_pending was True. The callback must suppress "
                "the spawn when stop() is in flight."
            )
        finally:
            monkeypatch.setattr(threading.Thread, "__init__", real_thread_init)
            monkeypatch.setattr(threading.Thread, "start", real_thread_start)
            r._user_stop_pending = False
            r.stop()

    def test_callback_spawns_handler_with_captured_generation_when_recording_cleared(
        self, monkeypatch
    ):
        """When recording was unexpectedly cleared (``_recording_event``
        not set) and no stop is pending, the callback MUST spawn a
        handler — AND that handler must carry the captured
        ``_stop_generation`` (not the default 0). This is the GT-24
        fix: the handler is now scheduled with the captured generation
        so it can bail out if a stop/start cycle landed between
        scheduling and execution."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        captured_kwargs: dict = {}
        real_thread_init = threading.Thread.__init__
        real_thread_start = threading.Thread.start

        def capturing_init(self, *args, **kwargs):
            real_thread_init(self, *args, **kwargs)
            if self.name == "stream-finished-handler":
                # Capture the kwargs passed to the handler target.
                # threading.Thread stores target/args/kwargs as
                # _target, _args, _kwargs after __init__.
                captured_kwargs["kwargs"] = dict(getattr(self, "_kwargs", {}) or {})

        def suppressing_start(self):
            if self.name == "stream-finished-handler":
                return
            real_thread_start(self)

        monkeypatch.setattr(threading.Thread, "__init__", capturing_init)
        monkeypatch.setattr(threading.Thread, "start", suppressing_start)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Bump _stop_generation to a non-zero value so we can verify
            # the callback captures the CURRENT value (not the default 0).
            r._stop_generation = 7

            # Simulate the unexpected-disconnect path: _recording_event
            # is cleared, _user_stop_pending is False, _stream is set.
            r._recording_event.clear()
            r._user_stop_pending = False
            assert r._stream is not None

            r._stream_finished_callback()

            assert "_captured_generation" in captured_kwargs["kwargs"], (
                "GT-24 regression: _stream_finished_callback spawned a "
                "handler without the _captured_generation kwarg. Pre-fix "
                "the handler was scheduled with the default 0, defeating "
                "the bouncer. Captured kwargs: "
                f"{captured_kwargs['kwargs']}"
            )
            assert captured_kwargs["kwargs"]["_captured_generation"] == 7, (
                "GT-24 regression: _stream_finished_callback captured "
                f"_captured_generation="
                f"{captured_kwargs['kwargs']['_captured_generation']} "
                "(expected 7 — the current _stop_generation). Pre-fix the "
                "handler was scheduled with the default 0, which matched "
                "the initial _stop_generation=0 on the first session and "
                "defeated the bouncer."
            )
        finally:
            monkeypatch.setattr(threading.Thread, "__init__", real_thread_init)
            monkeypatch.setattr(threading.Thread, "start", real_thread_start)
            # Restore recording_event so stop() can clean up properly.
            r._recording_event.set()
            r._stop_generation = 0
            r._device_disconnected = False
            r.stop()
