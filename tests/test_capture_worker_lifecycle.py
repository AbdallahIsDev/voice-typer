"""S3-CR-17 / Phase 4.5 — focused unit tests for the four worker-
lifecycle body methods on
``voice_typer.server.recording.capture.AudioCallbackDispatcher``.

These tests exercise ``start_audio_worker_body`` /
``stop_audio_worker_body`` / ``start_event_worker_body`` /
``stop_event_worker_body`` with a mocked ``recorder`` instance (a
small ``_FakeRecorder`` helper class). No real audio hardware is
touched — no PortAudio, no subprocess. Each test sets up the fake's
state, calls one of the four body methods, and asserts on the
observable side-effects (thread started/joined, registry
register/unregister calls, ring-buffer / queue contents, event
states).

The fake's ``_audio_worker_loop`` and ``_event_worker_loop`` mimic
the real ``Recorder`` worker loops closely enough (wait for work /
drain queue / exit on stop sentinel) so the start/stop/join/unregister
sequences work end-to-end without pulling in the full ``Recorder``
class (which would require sounddevice + numpy + the full filter
chain).
"""

from __future__ import annotations

import collections
import inspect
import logging
import queue
import re
import threading
import time

import pytest
from voice_typer.server.recording.capture import AudioCallbackDispatcher
from voice_typer.server.recording.recorder import (
    _AUDIO_WORKER_JOIN_TIMEOUT_S,
    _AUDIO_WORKER_THREAD_NAME,
    _EVENT_WORKER_JOIN_TIMEOUT_S,
    _EVENT_WORKER_STOP_SENTINEL,
    _EVENT_WORKER_THREAD_NAME,
)

# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeThreadRegistry:
    """Minimal stand-in for the real ``ThreadRegistry``. Records
    ``register`` / ``unregister`` calls so tests can assert on them.
    """

    def __init__(self) -> None:
        self.register_calls: list[dict] = []
        self.unregister_calls: list[str] = []

    def register(
        self, *, name: str, thread: threading.Thread, stop_event: threading.Event, join_timeout: float
    ) -> None:
        self.register_calls.append(
            {
                "name": name,
                "thread": thread,
                "stop_event": stop_event,
                "join_timeout": join_timeout,
            }
        )

    def unregister(self, name: str) -> None:
        self.unregister_calls.append(name)


class _FakeRecorder:
    """Minimal stand-in for :class:`Recorder` that owns the shared state
    touched by the four worker-lifecycle body methods.

    Real ``threading.Event`` / ``collections.deque`` / ``queue.Queue``
    instances are used so the dispatcher's synchronization assumptions
    are exercised faithfully. ``_audio_worker_loop`` and
    ``_event_worker_loop`` mimic the real ``Recorder`` worker loops
    (wait for work / drain queue / exit on stop sentinel) so the
    start/stop/join sequences work end-to-end without the full
    ``Recorder`` class.
    """

    def __init__(
        self,
        *,
        thread_registry: _FakeThreadRegistry | None = None,
        ring_maxlen: int = 64,
        event_queue_maxsize: int = 1000,
    ) -> None:
        # Audio worker state.
        self._worker_thread: threading.Thread | None = None
        self._worker_stop_event = threading.Event()
        self._worker_wake_event = threading.Event()
        self._ring_buffer: collections.deque = collections.deque(maxlen=ring_maxlen)
        # Event worker state.
        self._event_worker_thread: threading.Thread | None = None
        self._event_stop_event = threading.Event()
        self._event_queue: queue.Queue = queue.Queue(maxsize=event_queue_maxsize)
        # Shared.
        self._thread_registry = thread_registry

    def _audio_worker_loop(self) -> None:
        """Fake audio worker loop — wait for work or stop, drain ring
        buffer (no real processing), exit on stop."""
        while True:
            if not self._worker_stop_event.is_set():
                self._worker_wake_event.wait(timeout=0.05)
            self._worker_wake_event.clear()
            # Drain ring buffer (no real processing — just pop).
            while True:
                try:
                    self._ring_buffer.popleft()
                except IndexError:
                    break
            if self._worker_stop_event.is_set():
                return

    def _event_worker_loop(self) -> None:
        """Fake event worker loop — mimic the real loop's queue.get +
        sentinel-exit pattern (without actually publishing)."""
        while True:
            if not self._event_stop_event.is_set():
                try:
                    event = self._event_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
            else:
                try:
                    event = self._event_queue.get_nowait()
                except queue.Empty:
                    return
            if event is _EVENT_WORKER_STOP_SENTINEL:
                return
            # (don't actually publish — just consume)


# ── start_audio_worker_body ──────────────────────────────────────────────


class TestStartAudioWorkerBody:
    """``AudioCallbackDispatcher.start_audio_worker_body`` — starts the
    audio worker thread, registers with the registry, clears stale
    state. Idempotent: if the worker is already running, returns
    early."""

    def test_starts_new_daemon_thread_with_correct_name(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        try:
            assert fake._worker_thread is not None
            assert isinstance(fake._worker_thread, threading.Thread)
            assert fake._worker_thread.name == _AUDIO_WORKER_THREAD_NAME
            assert fake._worker_thread.daemon is True
        finally:
            dispatcher.stop_audio_worker_body(fake, timeout=1.0)

    def test_clears_stale_state_events_and_ring_buffer(self):
        fake = _FakeRecorder()
        # Pre-set stale state.
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()
        fake._ring_buffer.append(("stale", 4, "t", "s", 0.0))
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        try:
            # After start, the stop/wake events were cleared.
            assert not fake._worker_stop_event.is_set()
            assert not fake._worker_wake_event.is_set()
            # Ring buffer was cleared.
            assert len(fake._ring_buffer) == 0
        finally:
            dispatcher.stop_audio_worker_body(fake, timeout=1.0)

    def test_idempotent_when_worker_already_alive(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        try:
            first_thread = fake._worker_thread
            assert first_thread is not None
            assert first_thread.is_alive()
            # Call start again — should be a no-op (the worker is alive).
            dispatcher.start_audio_worker_body(fake)
            assert fake._worker_thread is first_thread, "must not replace an alive worker"
        finally:
            dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        assert fake._worker_thread is None

    def test_registers_with_thread_registry(self):
        registry = _FakeThreadRegistry()
        fake = _FakeRecorder(thread_registry=registry)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        try:
            # Registry was called once with the right kwargs.
            assert len(registry.register_calls) == 1
            call = registry.register_calls[0]
            assert call["name"] == _AUDIO_WORKER_THREAD_NAME
            assert call["thread"] is fake._worker_thread
            assert call["stop_event"] is fake._worker_stop_event
            assert call["join_timeout"] == _AUDIO_WORKER_JOIN_TIMEOUT_S
        finally:
            dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        # After stop, unregister was called once with the right name.
        assert len(registry.unregister_calls) == 1
        assert registry.unregister_calls[0] == _AUDIO_WORKER_THREAD_NAME

    def test_skips_registry_when_none(self):
        fake = _FakeRecorder(thread_registry=None)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        try:
            # Just verify it doesn't crash; no registry to assert on.
            assert fake._worker_thread is not None
        finally:
            dispatcher.stop_audio_worker_body(fake, timeout=1.0)


# ── stop_audio_worker_body ───────────────────────────────────────────────


class TestStopAudioWorkerBody:
    """``AudioCallbackDispatcher.stop_audio_worker_body`` — signals stop,
    wakes the worker, joins, unregisters, clears state."""

    def test_no_op_when_worker_thread_is_none(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        # Stop event pre-set to test the "still reset" branch.
        fake._worker_stop_event.set()
        dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        # The stop event was cleared (so the next start is clean).
        assert not fake._worker_stop_event.is_set()
        # Worker thread is still None.
        assert fake._worker_thread is None

    def test_drain_true_signals_stop_wakes_and_joins(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        thread_ref = fake._worker_thread
        assert thread_ref is not None
        # Stop with drain=True (default).
        dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        # Thread joined and was cleared.
        assert fake._worker_thread is None
        assert not thread_ref.is_alive(), "thread should have exited"
        # Stop/wake events were cleared.
        assert not fake._worker_stop_event.is_set()
        assert not fake._worker_wake_event.is_set()

    def test_drain_false_clears_ring_buffer_before_stop(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        # Enqueue some chunks.
        for i in range(3):
            fake._ring_buffer.append((f"chunk-{i}", 4, "t", "s", 0.0))
        # Stop with drain=False — ring buffer should be cleared first.
        dispatcher.stop_audio_worker_body(fake, timeout=1.0, drain=False)
        # Ring buffer was cleared by the stop path.
        assert len(fake._ring_buffer) == 0
        # Thread was joined.
        assert fake._worker_thread is None

    def test_unregisters_with_thread_registry(self):
        registry = _FakeThreadRegistry()
        fake = _FakeRecorder(thread_registry=registry)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        assert len(registry.register_calls) == 1
        dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        # Registry was unregistered once.
        assert len(registry.unregister_calls) == 1
        assert registry.unregister_calls[0] == _AUDIO_WORKER_THREAD_NAME

    def test_skips_unregister_when_registry_none(self):
        fake = _FakeRecorder(thread_registry=None)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_audio_worker_body(fake)
        # Just verify stop doesn't crash with registry=None.
        dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        assert fake._worker_thread is None

    def test_logs_warning_when_thread_does_not_exit_in_time(self, caplog):
        """If the worker doesn't exit within timeout, a warning is
        logged and the thread reference is still cleared (the thread
        is a daemon and will exit on its next iteration boundary)."""
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)

        # Inject a slow loop that ignores the stop event for a while.
        def slow_loop() -> None:
            time.sleep(0.2)  # much longer than the join timeout

        fake._audio_worker_loop = slow_loop  # type: ignore[method-assign]

        dispatcher.start_audio_worker_body(fake)
        thread_ref = fake._worker_thread
        assert thread_ref is not None

        # Stop with a tiny timeout — the worker won't exit in time.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            dispatcher.stop_audio_worker_body(fake, timeout=0.02)

        # Warning was logged by the body.
        assert any(
            "did not exit within" in rec.getMessage() and rec.name == "voice_typer.server.recording"
            for rec in caplog.records
            if rec.levelname == "WARNING"
        ), (
            f"expected a WARNING about worker not exiting in time; "
            f"got: {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]}"
        )
        # The thread reference was cleared regardless (the body nulls it
        # unconditionally after the join attempt).
        assert fake._worker_thread is None
        # The thread is still alive (it will exit eventually as a daemon).
        # Wait for it to actually finish so we don't leak a thread.
        thread_ref.join(timeout=2.0)
        assert not thread_ref.is_alive(), "slow_loop should have exited by now"


# ── start_event_worker_body ──────────────────────────────────────────────


class TestStartEventWorkerBody:
    """``AudioCallbackDispatcher.start_event_worker_body`` — starts the
    event worker thread, drains stale events, registers."""

    def test_starts_new_daemon_thread_with_correct_name(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            assert fake._event_worker_thread is not None
            assert isinstance(fake._event_worker_thread, threading.Thread)
            assert fake._event_worker_thread.name == _EVENT_WORKER_THREAD_NAME
            assert fake._event_worker_thread.daemon is True
        finally:
            dispatcher.stop_event_worker_body(fake, timeout=1.0)

    def test_clears_stale_stop_event(self):
        fake = _FakeRecorder()
        fake._event_stop_event.set()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            assert not fake._event_stop_event.is_set()
        finally:
            dispatcher.stop_event_worker_body(fake, timeout=1.0)

    def test_drains_stale_events_from_queue(self):
        fake = _FakeRecorder()
        # Pre-populate the queue with stale events.
        for i in range(3):
            fake._event_queue.put_nowait({"event": i})
        assert fake._event_queue.qsize() == 3
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            # After start, the queue should be empty (drained).
            assert fake._event_queue.qsize() == 0
        finally:
            dispatcher.stop_event_worker_body(fake, timeout=1.0)

    def test_idempotent_when_worker_already_alive(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            first_thread = fake._event_worker_thread
            assert first_thread is not None
            assert first_thread.is_alive()
            dispatcher.start_event_worker_body(fake)
            assert fake._event_worker_thread is first_thread
        finally:
            dispatcher.stop_event_worker_body(fake, timeout=1.0)
        assert fake._event_worker_thread is None

    def test_registers_with_thread_registry(self):
        registry = _FakeThreadRegistry()
        fake = _FakeRecorder(thread_registry=registry)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            assert len(registry.register_calls) == 1
            call = registry.register_calls[0]
            assert call["name"] == _EVENT_WORKER_THREAD_NAME
            assert call["thread"] is fake._event_worker_thread
            assert call["stop_event"] is fake._event_stop_event
            assert call["join_timeout"] == _EVENT_WORKER_JOIN_TIMEOUT_S
        finally:
            dispatcher.stop_event_worker_body(fake, timeout=1.0)
        assert len(registry.unregister_calls) == 1
        assert registry.unregister_calls[0] == _EVENT_WORKER_THREAD_NAME

    def test_skips_registry_when_none(self):
        fake = _FakeRecorder(thread_registry=None)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            assert fake._event_worker_thread is not None
        finally:
            dispatcher.stop_event_worker_body(fake, timeout=1.0)


# ── stop_event_worker_body ────────────────────────────────────────────────


class TestStopEventWorkerBody:
    """``AudioCallbackDispatcher.stop_event_worker_body`` — signals stop,
    pushes sentinel, joins, unregisters."""

    def test_no_op_when_worker_thread_is_none(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        fake._event_stop_event.set()
        dispatcher.stop_event_worker_body(fake, timeout=1.0)
        assert not fake._event_stop_event.is_set()
        assert fake._event_worker_thread is None

    def test_drain_true_pushes_sentinel_and_joins(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        thread_ref = fake._event_worker_thread
        assert thread_ref is not None
        dispatcher.stop_event_worker_body(fake, timeout=1.0)
        assert fake._event_worker_thread is None
        assert not thread_ref.is_alive(), "thread should have exited"
        assert not fake._event_stop_event.is_set()

    def test_drain_false_clears_queue_before_stop(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        # Enqueue events that should be discarded by the drain=False path.
        for i in range(3):
            fake._event_queue.put_nowait({"event": i})
        assert fake._event_queue.qsize() == 3
        # Stop with drain=False — queue should be cleared first by the
        # stop path, then the worker exits on the pushed sentinel.
        dispatcher.stop_event_worker_body(fake, timeout=1.0, drain=False)
        # Queue should be empty after the discard path (cleared by the
        # body + sentinel consumed by the worker).
        assert fake._event_queue.qsize() == 0
        # Thread was joined.
        assert fake._event_worker_thread is None

    def test_drain_true_drains_remaining_events_before_exit(self):
        """With drain=True, the worker should drain all queued events
        before exiting (the stop path does NOT clear the queue — the
        worker drains it itself). The fake loop just consumes (doesn't
        publish), so we assert the queue is empty after stop."""
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        # Enqueue events.
        for i in range(3):
            fake._event_queue.put_nowait({"event": i})
        # Stop with drain=True — the worker should drain all 3 events
        # before exiting on the sentinel pushed by the stop path.
        dispatcher.stop_event_worker_body(fake, timeout=1.0)
        # All events were consumed (drained) by the worker before exit.
        assert fake._event_queue.qsize() == 0
        assert fake._event_worker_thread is None

    def test_unregisters_with_thread_registry(self):
        registry = _FakeThreadRegistry()
        fake = _FakeRecorder(thread_registry=registry)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        assert len(registry.register_calls) == 1
        dispatcher.stop_event_worker_body(fake, timeout=1.0)
        assert len(registry.unregister_calls) == 1
        assert registry.unregister_calls[0] == _EVENT_WORKER_THREAD_NAME

    def test_skips_unregister_when_registry_none(self):
        fake = _FakeRecorder(thread_registry=None)
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.start_event_worker_body(fake)
        dispatcher.stop_event_worker_body(fake, timeout=1.0)
        assert fake._event_worker_thread is None


# Source-inspection contracts ( negative) ──────────────────────────


def _strip_docstring(src: str) -> str:
    """Return ``src`` with the leading ``\"\"\"``-delimited docstring removed.

    Mirrors the helper in ``tests/test_capture_module.py`` so the helper
    docstrings (which reference the forbidden literals to explain the
    Option C contract) do not trip the negative assertions on the body.
    """
    start = src.find('"""')
    if start == -1:
        return src
    end = src.find('"""', start + 3)
    if end == -1:
        return src
    return src[end + 3 :]


class TestWorkerLifecycleBodySourceContracts:
    """GT-23: the four body methods must NOT contain the lock-acquire
    literals — those stay on the Recorder methods (Option C).

    The Recorder-side source-inspection contracts (in
    ``tests/test_recorder_worker_lifecycle.py``) check
    ``inspect.getsource(Recorder._start_audio_worker)`` etc. — those
    see the Recorder's delegator source, NOT this helper's source. But
    if the helper's body contained ``with self._worker_lifecycle_lock:``
    or ``with self._lock:`` as actual ``with`` statements, the literal
    would propagate to the Recorder source via the delegator (the
    primary agent's delegate would just call the helper, but the test
    uses ``inspect.getsource`` which returns the Recorder's source
    only — so this is a defensive guard against accidental
    re-introduction in the helper).
    """

    def test_start_audio_worker_body_no_worker_lifecycle_lock_literal(self):
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.start_audio_worker_body))
        assert "with self._worker_lifecycle_lock:" not in src
        assert "with recorder._worker_lifecycle_lock:" not in src

    def test_stop_audio_worker_body_no_worker_lifecycle_lock_literal(self):
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.stop_audio_worker_body))
        assert "with self._worker_lifecycle_lock:" not in src
        assert "with recorder._worker_lifecycle_lock:" not in src

    def test_start_event_worker_body_no_worker_lifecycle_lock_literal(self):
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.start_event_worker_body))
        assert "with self._worker_lifecycle_lock:" not in src
        assert "with recorder._worker_lifecycle_lock:" not in src

    def test_stop_event_worker_body_no_worker_lifecycle_lock_literal(self):
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.stop_event_worker_body))
        assert "with self._worker_lifecycle_lock:" not in src
        assert "with recorder._worker_lifecycle_lock:" not in src

    def test_stop_audio_worker_body_no_self_lock_literal(self):
        """GT-23 negative contract: ``stop_audio_worker_body`` must NOT
        acquire ``self._lock`` — would propagate the literal to the
        Recorder source via the delegator and break the
        ``test_stop_audio_worker_does_not_hold_self_lock_across_join``
        contract."""
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.stop_audio_worker_body))
        assert "with self._lock:" not in src, (
            "GT-23 negative contract: stop_audio_worker_body must NOT "
            "contain `with self._lock:` — would propagate to Recorder source"
        )
        assert "with recorder._lock:" not in src

    def test_stop_event_worker_body_no_self_lock_literal(self):
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.stop_event_worker_body))
        assert "with self._lock:" not in src, (
            "GT-23 negative contract: stop_event_worker_body must NOT "
            "contain `with self._lock:` — would propagate to Recorder source"
        )
        assert "with recorder._lock:" not in src


# ── Module-level source checks ────────────────────────────────────────────


class TestCaptureModuleSourceContracts:
    """Module-level source-inspection contracts for ``capture.py``.

    These check the WHOLE module source (not just one method's body) so
    the cleanup of unused imports and the GT-23 negative contract can be
    verified at the module level too.
    """

    def test_no_worker_lifecycle_lock_with_block_in_module(self):
        """The literal ``with self._worker_lifecycle_lock:`` (or
        ``with recorder._worker_lifecycle_lock:``) must NOT appear
        anywhere in ``capture.py`` — the lock stays on Recorder."""
        from voice_typer.server.recording import capture

        src = inspect.getsource(capture)
        assert "with self._worker_lifecycle_lock:" not in src, (
            "GT-23: capture.py must NOT contain `with self._worker_lifecycle_lock:` "
            "— the lock acquisition stays on Recorder (Option C)"
        )
        assert "with recorder._worker_lifecycle_lock:" not in src

    def test_no_self_lock_with_block_in_module(self):
        """The literal ``with self._lock:`` (or ``with recorder._lock:``)
        must NOT appear anywhere in ``capture.py`` — would propagate to
        the Recorder source via the delegator (GT-23 negative contract).
        """
        from voice_typer.server.recording import capture

        src = inspect.getsource(capture)
        assert "with self._lock:" not in src, (
            "GT-23 negative contract: capture.py must NOT contain "
            "`with self._lock:` — would propagate to Recorder source"
        )
        assert "with recorder._lock:" not in src

    def test_removed_unused_imports(self):
        """The unused imports removed by S3-CR-17 are gone."""
        from voice_typer.server.recording import capture

        src = inspect.getsource(capture)
        assert "noqa: F401" not in src, (
            "the `# noqa: F401` comment on `import threading` should be "
            "removed (threading is now actually used by the new bodies)"
        )
        assert "lazy_module" not in src, "lazy_module import should be removed (no `sd` proxy in capture.py)"
        # No top-level `sd = ...` assignment in the module source.
        assert not re.search(r"^sd\s*=\s*", src, re.MULTILINE), "sd = ... assignment should be removed from capture.py"

    def test_kept_used_imports(self):
        """The imports actually used by the new methods are present."""
        from voice_typer.server.recording import capture

        src = inspect.getsource(capture)
        # `import threading` is now used (threading.Thread in start_*_body)
        assert "import threading" in src
        # `import contextlib` is used (contextlib.suppress in *_event_worker_body)
        assert "import contextlib" in src
        # `import queue` is used (queue.Empty / queue.Full)
        assert "import queue" in src
        # `import time` is used (time.perf_counter in dispatch_callback_body)
        assert "import time" in src
        # `import logging` is used (the package-level logger).
        assert "import logging" in src

    def test_module_docstring_mentions_worker_lifecycle_bodies(self):
        """The module docstring mentions the four new worker-lifecycle
        body methods so the S3-CR-17 extraction is documented at the
        module level."""
        from voice_typer.server.recording import capture

        assert capture.__doc__ is not None
        doc = capture.__doc__
        assert "start_audio_worker_body" in doc
        assert "stop_audio_worker_body" in doc
        assert "start_event_worker_body" in doc
        assert "stop_event_worker_body" in doc


# ── Integration: a start→stop cycle round-trip ──────────────────────────


class TestStartStopRoundTrip:
    """End-to-end: start the worker, do some work, stop it, verify
    cleanup. Exercises both the audio and event worker pairs."""

    def test_audio_worker_start_process_stop_cycle(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        # Start.
        dispatcher.start_audio_worker_body(fake)
        assert fake._worker_thread is not None
        assert fake._worker_thread.is_alive()
        # Push some chunks (the worker drains them in the background).
        for i in range(5):
            fake._ring_buffer.append((f"chunk-{i}", 4, "t", "s", 0.0))
        # Give the worker a moment to drain.
        time.sleep(0.05)
        # Stop.
        dispatcher.stop_audio_worker_body(fake, timeout=1.0)
        # After stop: thread is None, events cleared, ring buffer empty.
        assert fake._worker_thread is None
        assert not fake._worker_stop_event.is_set()
        assert not fake._worker_wake_event.is_set()
        assert len(fake._ring_buffer) == 0

    def test_event_worker_start_consume_stop_cycle(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        # Start.
        dispatcher.start_event_worker_body(fake)
        assert fake._event_worker_thread is not None
        assert fake._event_worker_thread.is_alive()
        # Push some events (the worker consumes them in the background).
        for i in range(5):
            fake._event_queue.put_nowait({"event": i})
        # Give the worker a moment to drain.
        time.sleep(0.05)
        # Stop with drain=True (default).
        dispatcher.stop_event_worker_body(fake, timeout=1.0)
        # After stop: thread is None, stop event cleared, queue empty.
        assert fake._event_worker_thread is None
        assert not fake._event_stop_event.is_set()
        assert fake._event_queue.qsize() == 0

    def test_both_workers_can_run_concurrently(self):
        """Both the audio and event workers can be started and stopped
        concurrently without interfering with each other (they share
        the ``_thread_registry`` but have separate thread refs / stop
        events / queues)."""
        registry = _FakeThreadRegistry()
        fake = _FakeRecorder(thread_registry=registry)
        dispatcher = AudioCallbackDispatcher(fake)
        # Start both.
        dispatcher.start_audio_worker_body(fake)
        dispatcher.start_event_worker_body(fake)
        try:
            assert fake._worker_thread is not None
            assert fake._event_worker_thread is not None
            assert fake._worker_thread is not fake._event_worker_thread
            # Both registered.
            assert len(registry.register_calls) == 2
            names = {c["name"] for c in registry.register_calls}
            assert names == {_AUDIO_WORKER_THREAD_NAME, _EVENT_WORKER_THREAD_NAME}
        finally:
            dispatcher.stop_audio_worker_body(fake, timeout=1.0)
            dispatcher.stop_event_worker_body(fake, timeout=1.0)
        # Both unregistered.
        assert fake._worker_thread is None
        assert fake._event_worker_thread is None
        assert len(registry.unregister_calls) == 2
        unreg_names = set(registry.unregister_calls)
        assert unreg_names == {_AUDIO_WORKER_THREAD_NAME, _EVENT_WORKER_THREAD_NAME}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--timeout=30"]))
