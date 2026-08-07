"""AB-10: ``change_model`` / ``set_active_backend`` non-blocking tests.

Verifies that:
- ``change_model`` returns immediately (< 100ms) instead of blocking for
  the 5-30s model load.
- ``set_active_backend`` returns immediately (< 100ms) too.
- The model eventually loads and an ``asr_backend_ready`` event fires.
- Concurrent ``change_model`` calls serialize via ``_model_change_lock``.
- ``_change_model_blocking`` (the synchronous variant) still works for
  callers that need to wait (e.g. ``apply_pending_model_change``).

The fix moves the heavy ``_change_model_load_phase`` (which calls
``load_active`` — disk + torch import + weight load, 5-30s on cold boot)
to a background daemon thread. The IPC worker returns immediately with
a "loading" ack; the background thread publishes ``asr_backend_ready``
on completion.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server import event_bus
from voice_typer.server.model_manager import ModelManager


def _make_mm_with_mock_backend(
    *,
    backend_name: str = "whisper",
    model_size: str = "tiny.en",
) -> tuple[ModelManager, MagicMock, MagicMock, MagicMock]:
    """Construct a ModelManager backed by a mock registry + mock engine.

    Mirrors ``tests/test_model_idle_unload._make_mm_with_mock_backend``
    but simplified for AB-10 tests (no idle-unload timer config).
    """
    app = MagicMock(name="app")
    app.config.asr_backend = backend_name
    app.config.model_size = model_size
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.model_idle_unload_minutes = 0
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()
    # Ensure change_model is NOT deferred — ``_change_model_setattr_phase``
    # checks ``recorder.recording`` (must be False) and
    # ``_busy_event.is_set()`` (must return True = not busy).
    app.recorder.recording = False
    app._busy_event = MagicMock()
    app._busy_event.is_set.return_value = True
    # config.save() must return truthy.
    app.config.save.return_value = True

    mm = ModelManager(app)

    engine = MagicMock(name="engine")
    engine.is_loaded = True
    engine.device_info = f"{backend_name}/cpu"

    mock_registry = MagicMock(name="registry")
    mock_registry.active_name = backend_name
    mock_registry.get_active.return_value = engine
    mock_registry.get.return_value = engine
    mock_registry.load_active.return_value = engine
    mock_registry.load_with_fallback.return_value = engine
    mock_registry.available_backends = [backend_name]
    mm._registry = mock_registry

    mm._ensure_engine = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app, engine, mock_registry


# change_model returns immediately ──────────────────────────


class TestChangeModelReturnsImmediately:
    """AB-10: ``change_model`` must return in < 100ms (not 5-30s)."""

    def test_change_model_returns_under_100ms(self):
        """``change_model`` spawns a background thread and returns immediately."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        # Make load_active slow (1s) to prove change_model doesn't block.
        barrier = threading.Event()

        def slow_load(progress_callback=None):
            barrier.wait(timeout=5.0)
            return engine

        mm._registry.load_active.side_effect = slow_load

        start = time.monotonic()
        ack = mm.change_model("parakeet")
        elapsed = time.monotonic() - start

        # Must return in well under 100ms (the 1s load hasn't completed).
        assert elapsed < 0.1, f"change_model should return immediately (<100ms); took {elapsed:.3f}s"
        # Release the background thread so it can complete.
        barrier.set()
        # Join the background ModelChange thread so its
        # ``asr_backend_ready`` publish completes INSIDE this test — a
        # late publish would land in a later test's event_bus
        # subscription window and flake it (see
        # ``test_model_manager_load_races.py::TestBackendLoadFailedEvent``).
        if mm._model_change_thread is not None:
            mm._model_change_thread.join(timeout=5.0)
        # Ack shape.
        assert isinstance(ack, dict)
        assert ack["status"] == "loading"
        assert ack["pending"]["model_size"] == "parakeet"
        assert ack["pending"]["backend"] == "parakeet"
        assert "previous" in ack

    def test_change_model_returns_dict_with_loading_status(self):
        """The ack dict must carry 'status', 'previous', 'pending'."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper", model_size="tiny.en")
        ack = mm.change_model("medium.en")
        assert ack["status"] == "loading"
        assert ack["previous"]["backend"] == "whisper"
        assert ack["previous"]["model_size"] == "tiny.en"
        assert ack["pending"]["backend"] == "whisper"
        assert ack["pending"]["model_size"] == "medium.en"


# set_active_backend returns immediately ────────────────────


class TestSetActiveBackendReturnsImmediately:
    """AB-10: ``set_active_backend`` must return in < 100ms."""

    def test_set_active_backend_returns_under_100ms(self):
        """``set_active_backend`` spawns a background thread and returns immediately."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        barrier = threading.Event()

        def slow_load(progress_callback=None):
            barrier.wait(timeout=5.0)
            return engine

        mm._registry.load_active.side_effect = slow_load

        start = time.monotonic()
        ack = mm.set_active_backend("qwen")
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"set_active_backend should return immediately (<100ms); took {elapsed:.3f}s"
        barrier.set()
        # Join the background BackendChange thread so its
        # ``asr_backend_ready`` publish completes inside this test (see
        # the comment in ``test_change_model_returns_under_100ms``).
        if mm._backend_change_thread is not None:
            mm._backend_change_thread.join(timeout=5.0)
        assert isinstance(ack, dict)
        assert ack["status"] == "loading"
        assert ack["pending"]["backend"] == "qwen"

    def test_set_active_backend_fast_path_noop(self):
        """When backend is already active, return 'ready' without spawning a thread."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        ack = mm.set_active_backend("whisper")
        assert ack["status"] == "ready"
        # load_active should NOT have been called (no background work).
        mm._registry.load_active.assert_not_called()

    def test_set_active_backend_rejects_unknown_synchronously(self):
        """ValueError for unknown backend is raised BEFORE any thread spawn."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        with pytest.raises(ValueError, match="unknown backend"):
            mm.set_active_backend("nonexistent")
        # No background work should have started.
        mm._registry.load_active.assert_not_called()


# asr_backend_ready event fires on completion ───────────────


class TestBackendReadyEventFires:
    """AB-10: the background thread publishes ``asr_backend_ready`` on completion."""

    def test_change_model_publishes_backend_ready_event(self):
        """After ``change_model``, the ``asr_backend_ready`` event fires."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm.change_model("parakeet")
            # Wait for the background thread to publish the event.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not any(e.get("type") == "asr_backend_ready" for e in received):
                time.sleep(0.01)
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        assert len(ready_events) >= 1, f"Expected at least one asr_backend_ready event; got {received}"
        evt = ready_events[0]
        assert evt["data"]["backend"] == "parakeet"
        assert evt["data"]["model_size"] == "parakeet"

    def test_set_active_backend_publishes_backend_ready_event(self):
        """After ``set_active_backend``, the ``asr_backend_ready`` event fires."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm.set_active_backend("qwen")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not any(e.get("type") == "asr_backend_ready" for e in received):
                time.sleep(0.01)
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        assert len(ready_events) >= 1, f"Expected at least one asr_backend_ready event; got {received}"
        evt = ready_events[0]
        assert evt["data"]["backend"] == "qwen"


# concurrent calls serialize via _model_change_lock ─────────


class TestConcurrentCallsSerialize:
    """AB-10: concurrent ``change_model`` calls serialize via ``_model_change_lock``."""

    def test_concurrent_change_model_calls_do_not_interleave(self):
        """Two concurrent ``change_model`` calls must not both run the load phase
        at the same time — ``_model_change_lock`` serializes them."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")

        # Track concurrent load_active calls.
        active_loads = [0]
        max_concurrent_loads = [0]
        load_lock = threading.Lock()

        def tracked_load(progress_callback=None):
            with load_lock:
                active_loads[0] += 1
                max_concurrent_loads[0] = max(max_concurrent_loads[0], active_loads[0])
            try:
                time.sleep(0.1)  # simulate load work
                return engine
            finally:
                with load_lock:
                    active_loads[0] -= 1

        mm._registry.load_active.side_effect = tracked_load

        # Spawn two concurrent change_model calls.
        threads = []
        for _ in range(2):
            t = threading.Thread(target=mm.change_model, args=("parakeet",))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        # Join the background ModelChange threads too. The two change
        # threads serialize on ``_model_change_lock``, so joining the
        # most recently spawned one waits for BOTH to complete their
        # ``asr_backend_ready`` publish before the test ends (a late
        # publish would flake a later test's event_bus subscription).
        if mm._model_change_thread is not None:
            mm._model_change_thread.join(timeout=5.0)

        # The loads must have serialized — never more than 1 concurrent.
        assert max_concurrent_loads[0] <= 1, (
            f"Concurrent load_active calls detected: max={max_concurrent_loads[0]} "
            "(expected ≤1 — _model_change_lock should serialize)"
        )

    def test_concurrent_change_model_and_set_active_backend_serialize(self):
        """A ``change_model`` and a ``set_active_backend`` running concurrently
        must serialize via ``_model_change_lock``."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")

        active_loads = [0]
        max_concurrent_loads = [0]
        load_lock = threading.Lock()

        def tracked_load(progress_callback=None):
            with load_lock:
                active_loads[0] += 1
                max_concurrent_loads[0] = max(max_concurrent_loads[0], active_loads[0])
            try:
                time.sleep(0.1)
                return engine
            finally:
                with load_lock:
                    active_loads[0] -= 1

        mm._registry.load_active.side_effect = tracked_load

        t1 = threading.Thread(target=mm.change_model, args=("parakeet",))
        t2 = threading.Thread(target=mm.set_active_backend, args=("qwen",))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        # Join the background ModelChange + BackendChange threads (they
        # serialize on ``_model_change_lock``) so their publishes
        # complete inside this test — see the comment in
        # ``test_concurrent_change_model_calls_do_not_interleave``.
        if mm._model_change_thread is not None:
            mm._model_change_thread.join(timeout=5.0)
        if mm._backend_change_thread is not None:
            mm._backend_change_thread.join(timeout=5.0)

        assert max_concurrent_loads[0] <= 1, (
            f"Concurrent load_active calls detected: max={max_concurrent_loads[0]} "
            "(expected ≤1 — _model_change_lock should serialize)"
        )


# blocking variant still works for sync callers ─────────────


class TestBlockingVariantStillWorks:
    """AB-10: ``_change_model_blocking`` preserves the original sync behavior."""

    def test_change_model_blocking_loads_synchronously(self):
        """``_change_model_blocking`` runs the full cycle synchronously and
        publishes the ``asr_backend_ready`` event before returning."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._change_model_blocking("parakeet")
        finally:
            event_bus.unsubscribe(subscriber)

        # The event must have been published synchronously (before return).
        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        assert len(ready_events) >= 1, f"_change_model_blocking should publish asr_backend_ready; got {received}"

    def test_set_active_backend_blocking_switches_synchronously(self):
        """``_set_active_backend_blocking`` runs the full cycle synchronously."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._set_active_backend_blocking("qwen")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        assert len(ready_events) >= 1, f"_set_active_backend_blocking should publish asr_backend_ready; got {received}"
        # Config must be updated synchronously.
        assert app.config.asr_backend == "qwen"


# apply_pending_model_change uses blocking variant ──────────


class TestApplyPendingModelChangeUsesBlocking:
    """AB-10: ``apply_pending_model_change`` must use the blocking variant
    so the model is fully loaded before the recorder starts."""

    def test_apply_pending_model_change_loads_synchronously(self):
        """When a pending model change is applied, the load must complete
        before ``apply_pending_model_change`` returns (the recording
        controller depends on this)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(backend_name="whisper")
        # Set up a pending change.
        mm._pending_model_change = "parakeet"
        # Make sure the background path is NOT taken — verify the load
        # completes before return by checking the event was published.
        received: list[dict] = []

        def _subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(_subscriber)
        try:
            result = mm.apply_pending_model_change()
        finally:
            event_bus.unsubscribe(_subscriber)

        assert result is True
        # The asr_backend_ready event must have been published
        # synchronously (proving the blocking variant was used).
        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        assert len(ready_events) >= 1, (
            "apply_pending_model_change should use the blocking variant which publishes asr_backend_ready synchronously"
        )

    def test_apply_pending_model_change_noop_when_none(self):
        """When no pending change, returns False immediately."""
        mm = ModelManager.__new__(ModelManager)
        mm._pending_model_change = None
        result = mm.apply_pending_model_change()
        assert result is False
