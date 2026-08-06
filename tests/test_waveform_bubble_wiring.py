"""regression tests for the ``WaveformBubbleWiring`` extraction.

The ``_wire_waveform_bubble`` method (and its associated bubble-level-pusher
background worker) was extracted from ``VoiceTyperApp`` to
``voice_typer/server/waveform_bubble_wiring.py``. ``VoiceTyperApp`` keeps
a thin delegate method so existing callers (and tests that call
``app._wire_waveform_bubble()``) keep working unchanged.

These tests pin the contract of the extraction:

1. ``WaveformBubbleWiring`` is constructable with a back-reference to
   the app and exposes ``_wire_waveform_bubble`` / ``stop`` methods.
2. ``_wire_waveform_bubble`` registers 4 callbacks on the
   ``WaveformBubble`` (``on_show``, ``on_hide``, ``on_level``,
   ``on_set_state``).
3. Each callback publishes the correct event type to ``event_bus``.
4. The bubble-level-pusher worker thread starts and is alive after
   wiring.
5. The worker stops cleanly via ``stop()``.
6. ``stop()`` is idempotent and safe to call before wiring (defensive).
7. The worker respects the bounded queue: when the queue is full,
   ``_push_bubble_level`` drops the sample (``queue.Full`` is suppressed).
8. The worker exits when a ``None`` sentinel is put on the queue.
9. ``_wire_waveform_bubble`` is idempotent — calling it twice reuses the
   existing queue / worker (doesn't spawn a second worker).
10. ``_wire_waveform_bubble`` registers the worker on
    ``app._thread_registry`` so it's tracked for shutdown.
11. ``on_level`` publishes a ``bubble_level`` event (async, drained by
    the worker).
12. Integration point: ``stop()`` mirrors the inline shutdown block in
    ``VoiceTyperApp._do_cleanup`` (app.py:1469-1480). The primary agent
    will replace that block with ``self.waveform_wiring.stop()``.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.waveform import WaveformBubble
from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_event_bus():
    """Snapshot/restore the event_bus subscriber set between tests.

    Mirrors the pattern in tests/test_waveform_bubble.py so a subscriber
    registered in one test doesn't leak into the next.
    """
    from voice_typer.server import event_bus

    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


@pytest.fixture
def bubble() -> WaveformBubble:
    return WaveformBubble()


@pytest.fixture
def thread_registry() -> MagicMock:
    """A mock ThreadRegistry — captures ``register()`` calls."""
    reg = MagicMock()
    return reg


@pytest.fixture
def app(bubble, thread_registry) -> MagicMock:
    """Minimal app mock with the two attributes the wiring touches.

    - ``app._waveform_bubble`` — the ``WaveformBubble`` coordinator
    - ``app._thread_registry`` — the central ``ThreadRegistry``

    The real ``VoiceTyperApp.__init__`` creates both before calling
    ``_wire_waveform_bubble``; the mock mirrors that setup.
    """
    app = MagicMock()
    app._waveform_bubble = bubble
    app._thread_registry = thread_registry
    return app


@pytest.fixture
def wiring(app) -> WaveformBubbleWiring:
    return WaveformBubbleWiring(app)


# ─── Construction / API surface ────────────────────────────────────────


class TestWaveformBubbleWiringConstruction:
    """Verify ``WaveformBubbleWiring.__init__`` sets up the migrated state."""

    def test_constructable_with_app_back_reference(self, app):
        wiring = WaveformBubbleWiring(app)
        assert wiring._app is app

    def test_methods_exist_and_are_callable(self, wiring):
        assert callable(wiring._wire_waveform_bubble)
        assert callable(wiring.stop)

    def test_state_starts_unwired(self, wiring):
        """Before ``_wire_waveform_bubble`` is called, the worker / queue /
        stop_event are all ``None`` (lazy creation)."""
        assert wiring._bubble_level_queue is None
        assert wiring._bubble_level_worker_stop is None
        assert wiring._bubble_level_worker is None
        assert wiring._last_bubble_level_push_ts == 0.0


# ─── Wiring registers 4 callbacks on the bubble ────────────────────────


class TestWireRegistersCallbacks:
    """``_wire_waveform_bubble`` must set the 4 callback slots on the bubble."""

    def test_wires_four_callbacks(self, wiring, bubble):
        assert bubble.on_show is None
        assert bubble.on_hide is None
        assert bubble.on_level is None
        assert bubble.on_set_state is None

        wiring._wire_waveform_bubble()

        assert callable(bubble.on_show)
        assert callable(bubble.on_hide)
        assert callable(bubble.on_level)
        assert callable(bubble.on_set_state)

    def test_on_show_publishes_bubble_show_event(self, wiring, bubble):
        from voice_typer.server import event_bus

        sent: list = []
        event_bus.subscribe(sent.append)

        wiring._wire_waveform_bubble()
        bubble.show()

        assert {"type": "bubble_show"} in sent

    def test_on_hide_publishes_bubble_hide_event(self, wiring, bubble):
        from voice_typer.server import event_bus

        sent: list = []
        event_bus.subscribe(sent.append)

        wiring._wire_waveform_bubble()
        # ``show()`` first because hide() is a no-op when not visible.
        bubble.show()
        bubble.hide()

        assert {"type": "bubble_hide"} in sent

    def test_on_set_state_publishes_bubble_set_state_event(self, wiring, bubble):
        from voice_typer.server import event_bus

        sent: list = []
        event_bus.subscribe(sent.append)

        wiring._wire_waveform_bubble()
        bubble.set_state("transcribing")

        matching = [m for m in sent if m.get("type") == "bubble_set_state"]
        assert len(matching) == 1
        assert matching[0]["data"] == {"state": "transcribing"}


# ─── Worker lifecycle (start / stop / sentinel) ────────────────────────


class TestWorkerLifecycle:
    """The bubble-level-pusher worker must start, stop, and exit on sentinel."""

    def test_worker_starts_after_wiring(self, wiring):
        assert wiring._bubble_level_worker is None
        wiring._wire_waveform_bubble()
        assert wiring._bubble_level_worker is not None
        assert wiring._bubble_level_worker.is_alive()
        assert wiring._bubble_level_worker.name == "bubble-level-pusher"
        assert wiring._bubble_level_worker.daemon is True
        # Cleanup
        wiring.stop()
        assert not wiring._bubble_level_worker.is_alive()

    def test_stop_joins_worker_cleanly(self, wiring):
        wiring._wire_waveform_bubble()
        worker = wiring._bubble_level_worker
        assert worker is not None and worker.is_alive()

        wiring.stop()

        # The worker should have exited within the 1.0s join timeout.
        assert not worker.is_alive()

    def test_stop_is_noop_before_wiring(self, wiring):
        """``stop()`` must be safe to call when the worker was never started.

        Mirrors the defensive ``hasattr`` / ``is not None`` guards in
        the original ``_do_cleanup`` block.
        """
        # Must not raise.
        wiring.stop()
        # And calling again after wiring still works.
        wiring._wire_waveform_bubble()
        wiring.stop()

    def test_stop_is_idempotent(self, wiring):
        wiring._wire_waveform_bubble()
        worker = wiring._bubble_level_worker
        assert worker is not None

        wiring.stop()
        wiring.stop()  # second call must be a no-op, not raise
        wiring.stop()  # third for good measure

        assert not worker.is_alive()

    def test_sentinel_shuts_down_worker(self, wiring):
        """Putting ``None`` on the queue must cause the worker to exit.

        This is the sentinel shutdown path documented in
        ``_bubble_level_worker`` — the worker breaks out of its drain
        loop when it dequeues ``None``.
        """
        wiring._wire_waveform_bubble()
        worker = wiring._bubble_level_worker
        assert worker is not None and worker.is_alive()

        # Signal + sentinel (mirrors what ``stop()`` does internally).
        wiring._bubble_level_worker_stop.set()
        with contextlib.suppress(queue.Full):
            wiring._bubble_level_queue.put_nowait(None)

        worker.join(timeout=2.0)
        assert not worker.is_alive(), "worker did not exit within 2s after sentinel was enqueued"


# ─── Bounded queue backpressure ────────────────────────────────────────


class TestBoundedQueue:
    """The ``on_level`` callback must drop samples when the queue is full."""

    def test_on_level_drops_when_queue_full(self, wiring):
        """Fill the queue to maxsize, then verify the next push is dropped.

        The queue has ``maxsize=64``. We fill it with dummy items, then
        call ``on_level`` once more. The drop is silent (``queue.Full``
        is suppressed by ``contextlib.suppress``); we verify the drop
        by asserting the queue size doesn't grow past 64.
        """
        wiring._wire_waveform_bubble()
        q: queue.Queue = wiring._bubble_level_queue
        assert q.maxsize == 64

        # Stop the worker so it doesn't drain the queue while we fill it.
        wiring._bubble_level_worker_stop.set()
        with contextlib.suppress(queue.Full):
            q.put_nowait(None)  # sentinel — worker will exit
        wiring._bubble_level_worker.join(timeout=1.0)
        assert not wiring._bubble_level_worker.is_alive()

        # Drain any straggler so we start with an empty queue.
        while not q.empty():
            q.get_nowait()

        # Reset the throttle so the first push isn't dropped by the
        # 16 ms / ~60 Hz gate.
        wiring._last_bubble_level_push_ts = 0.0

        # Fill the queue to maxsize with sentinel-like dummy items.
        for i in range(64):
            q.put_nowait({"type": "bubble_level", "data": {"rms": float(i), "peak": 0.0}})
        assert q.full()

        # Now invoke the ``on_level`` callback directly. The queue is
        # full, so ``put_nowait`` raises ``queue.Full`` — which the
        # callback suppresses. The callback must NOT raise.
        on_level = wiring._app._waveform_bubble.on_level
        # Reset throttle so the push attempt isn't skipped by the gate.
        wiring._last_bubble_level_push_ts = 0.0
        on_level(0.05, 0.10)  # must not raise

        # The queue is still at maxsize — the new sample was dropped.
        assert q.qsize() == 64, f"queue should still be at maxsize=64 after a drop, got {q.qsize()}"


# ─── Idempotent wiring (call twice → reuse existing worker) ────────────


class TestIdempotentWiring:
    """Calling ``_wire_waveform_bubble`` twice must NOT spawn a second worker."""

    def test_double_wire_reuses_existing_worker(self, wiring):
        wiring._wire_waveform_bubble()
        first_worker = wiring._bubble_level_worker
        first_queue = wiring._bubble_level_queue
        first_stop = wiring._bubble_level_worker_stop

        assert first_worker is not None and first_worker.is_alive()

        # Call again — must reuse, not create a new worker.
        wiring._wire_waveform_bubble()
        assert wiring._bubble_level_worker is first_worker, (
            "second _wire_waveform_bubble call must reuse the existing worker thread"
        )
        assert wiring._bubble_level_queue is first_queue
        assert wiring._bubble_level_worker_stop is first_stop

        # Cleanup
        wiring.stop()


# ─── Thread-registry registration ──────────────────────────────────────


class TestThreadRegistryRegistration:
    """The worker must be registered on ``app._thread_registry`` for shutdown."""

    def test_worker_registered_on_thread_registry(self, wiring, thread_registry):
        wiring._wire_waveform_bubble()

        thread_registry.register.assert_called_once()
        call_kwargs = thread_registry.register.call_args
        assert call_kwargs.kwargs["name"] == "bubble-level-pusher"
        assert call_kwargs.kwargs["thread"] is wiring._bubble_level_worker
        assert call_kwargs.kwargs["stop_event"] is wiring._bubble_level_worker_stop
        assert call_kwargs.kwargs["join_timeout"] == 1.0

        wiring.stop()


# ─── on_level publishes bubble_level event (async via worker) ──────────


class TestOnLevelPublishes:
    """The ``on_level`` callback publishes ``bubble_level`` via the worker queue."""

    def test_on_level_publishes_bubble_level_event(self, wiring, bubble):
        from voice_typer.server import event_bus

        sent: list = []
        event_bus.subscribe(sent.append)

        wiring._wire_waveform_bubble()
        # Reset throttle so the first push isn't dropped by the 16ms gate.
        wiring._last_bubble_level_push_ts = 0.0
        bubble.update_level(0.05, 0.12)

        # The push is async (drained by the worker). Wait briefly for
        # the worker to drain — the queue has maxsize=64 so a single
        # item drains in well under 100 ms.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(m.get("type") == "bubble_level" for m in sent):
                break
            time.sleep(0.02)

        levels = [m for m in sent if m.get("type") == "bubble_level"]
        assert len(levels) >= 1, f"expected >= 1 bubble_level event, got {len(levels)}; sent={sent}"
        last = levels[-1]["data"]
        assert "rms" in last and "peak" in last
        assert 0.0 < last["rms"] <= 1.0

        # Cleanup
        wiring.stop()


# ─── Integration point: _do_cleanup delegates to stop() ────────────────


class TestDoCleanupIntegration:
    """Document the integration point with ``VoiceTyperApp._do_cleanup``.

    The original ``_do_cleanup`` (app.py:1469-1480) contained an inline
    bubble-worker-stop block:

        if hasattr(self, "_bubble_level_worker_stop") and self._bubble_level_worker_stop is not None:
            self._bubble_level_worker_stop.set()
            if hasattr(self, "_bubble_level_queue") and self._bubble_level_queue is not None:
                with contextlib.suppress(queue.Full):
                    self._bubble_level_queue.put_nowait(None)  # sentinel
            if hasattr(self, "_bubble_level_worker") and self._bubble_level_worker is not None:
                self._bubble_level_worker.join(timeout=1.0)

    After extraction, the attributes live on ``WaveformBubbleWiring`` (not
    on the app), so that block would be a no-op. The primary agent should
    replace it with a single call to ``self.waveform_wiring.stop()``.

    These tests verify ``stop()`` reproduces the original semantics:
    sets the stop event, enqueues the sentinel, joins with a 1.0s timeout.
    """

    def test_stop_sets_stop_event(self, wiring):
        wiring._wire_waveform_bubble()
        stop_event = wiring._bubble_level_worker_stop
        assert stop_event is not None and not stop_event.is_set()

        wiring.stop()

        assert stop_event.is_set(), "stop() must set the worker_stop event"

    def test_stop_enqueues_sentinel(self, wiring):
        wiring._wire_waveform_bubble()
        q: queue.Queue = wiring._bubble_level_queue
        assert q is not None

        # Consume any items already in the queue (e.g. from worker startup)
        # so we can cleanly observe the sentinel being enqueued by stop().
        # Stop the worker first so it doesn't drain the sentinel before we
        # see it; we use the stop_event directly so the worker stays alive
        # long enough to read the sentinel and exit.
        # Actually, simplest: call stop() and verify the worker exited
        # (which can only happen if it dequeued the None sentinel).
        worker = wiring._bubble_level_worker
        assert worker is not None and worker.is_alive()

        wiring.stop()

        # The worker exits only by breaking out of its drain loop on
        # seeing ``None``. So a clean exit within the 1.0s join timeout
        # is direct evidence the sentinel was enqueued AND processed.
        assert not worker.is_alive(), "worker should have exited after stop() enqueued the sentinel"

    def test_stop_joins_with_one_second_timeout(self, wiring, monkeypatch):
        """``stop()`` must call ``worker.join(timeout=1.0)`` to mirror
        the original ``_do_cleanup`` join timeout."""
        wiring._wire_waveform_bubble()
        worker = wiring._bubble_level_worker
        assert worker is not None

        join_calls: list = []
        original_join = worker.join

        def spy_join(timeout=None):
            join_calls.append(timeout)
            return original_join(timeout)

        monkeypatch.setattr(worker, "join", spy_join)

        wiring.stop()

        assert len(join_calls) == 1, f"expected 1 join call, got {join_calls}"
        assert join_calls[0] == 1.0, f"join timeout must be 1.0 (mirrors _do_cleanup), got {join_calls[0]}"


# ─── PERF-3: worker coalesces stale bubble_level items ─────────────────


class TestWorkerCoalescesStaleLevels:
    """PERF-3: when ``event_bus.publish`` blocks (slow subscriber), the
    worker must coalesce stale ``bubble_level`` items in its queue,
    publishing only the LATEST one.

    Without coalescing, a 0.1s slow publish × N queued items = N×0.1s
    freeze (and N stale frames painted in sequence — e.g. 64 queued
    items = ~6.4s freeze, RW-9 PERF-3 finding). With coalescing, only
    the latest level is published — the visualizer jumps directly to
    the current smoothed level.
    """

    def test_coalesces_multiple_bubble_levels_to_one_publish(self, wiring, monkeypatch):
        from voice_typer.server import event_bus

        # Pre-create the queue + stop event so we can populate the queue
        # BEFORE the worker starts. This eliminates the race where the
        # worker drains the first item before the test enqueues the rest
        # (which would result in 2 publishes instead of 1).
        wiring._bubble_level_queue = queue.Queue(maxsize=64)
        wiring._bubble_level_worker_stop = threading.Event()

        # Stub publish with a Mock-like callable that blocks briefly
        # (simulating a slow subscriber like a stalled IPC send).
        # Records each call so we can assert on count + payload.
        published_calls: list = []

        def slow_publish(event):
            published_calls.append(event)
            time.sleep(0.1)
            return True

        monkeypatch.setattr(event_bus, "publish", slow_publish)

        # Put 5 bubble_level items rapidly (before worker starts).
        items = [
            {
                "type": "bubble_level",
                "data": {"rms": float(i) / 10.0, "peak": float(i) / 20.0},
            }
            for i in range(5)
        ]
        for it in items:
            wiring._bubble_level_queue.put_nowait(it)
        assert wiring._bubble_level_queue.qsize() == 5

        # Now wire — this creates + starts the worker on the
        # pre-populated queue. The worker's coalescing loop should
        # drain items 2-5 and keep only item 5 (the latest).
        wiring._wire_waveform_bubble()

        # Wait for the worker to publish at least once.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(published_calls) >= 1:
                break
            time.sleep(0.02)

        # Settle: one slow_publish cycle (0.1s) + slack. If a second
        # publish were going to happen, it would land in this window.
        time.sleep(0.25)

        # Cleanup: stop the worker.
        wiring.stop()

        # Assert: publish was called exactly 1 time, with the LATEST item.
        assert len(published_calls) == 1, (
            f"expected exactly 1 publish call (coalesced), got {len(published_calls)}: {published_calls}"
        )
        assert published_calls[0] is items[-1], f"expected the latest item (rms=0.4), got {published_calls[0]}"
