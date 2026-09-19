"""DJ-28: WaveformBubbleWiring closure reference cycle; stop() must null callbacks."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.waveform import WaveformBubble
from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring


@pytest.fixture(autouse=True)
def _reset_event_bus():
    """Snapshot/restore the event_bus subscriber set between tests."""
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
    return MagicMock()


@pytest.fixture
def app(bubble, thread_registry) -> MagicMock:
    app = MagicMock()
    app._waveform_bubble = bubble
    app._thread_registry = thread_registry
    return app


@pytest.fixture
def wiring(app) -> WaveformBubbleWiring:
    return WaveformBubbleWiring(app)


class TestStopNullsCallbacks:
    """DJ-28: ``stop()`` nulls the 5 callbacks on ``app._waveform_bubble``"""

    _CALLBACK_ATTRS = ("on_show", "on_hide", "on_level", "on_set_state", "on_config")

    def test_stop_nulls_all_five_callbacks_after_wiring(self, wiring, bubble):
        """DJ-28: after ``stop()``, all 5 callbacks on the bubble are ``None``."""
        # Wire, sets all 5 callbacks to closures.
        wiring._wire_waveform_bubble()
        for attr in self._CALLBACK_ATTRS:
            assert callable(getattr(bubble, attr)), f"precondition: {attr} must be callable after wiring"

        wiring.stop()

        for attr in self._CALLBACK_ATTRS:
            assert getattr(bubble, attr) is None, (
                f"DJ-28: bubble.{attr} must be None after stop() (was "
                f"{getattr(bubble, attr)!r}), breaks the closure → self "
                "reference cycle deterministically"
            )

    def test_stop_is_idempotent(self, wiring, bubble):
        """DJ-28: calling ``stop()`` twice doesn't raise and the callbacks"""
        wiring._wire_waveform_bubble()
        wiring.stop()
        # Second call must not raise.
        wiring.stop()
        wiring.stop()
        for attr in self._CALLBACK_ATTRS:
            assert getattr(bubble, attr) is None

    def test_stop_is_safe_before_wiring(self, wiring, bubble):
        """DJ-28: calling ``stop()`` before ``_wire_waveform_bubble`` has"""
        # Pre-wiring state: callbacks are None (set by WaveformBubble.__init__).
        for attr in self._CALLBACK_ATTRS:
            assert getattr(bubble, attr) is None

        # Must not raise.
        wiring.stop()

        for attr in self._CALLBACK_ATTRS:
            assert getattr(bubble, attr) is None

    def test_stop_is_safe_when_bubble_missing(self, app):
        """DJ-28: ``stop()`` is safe to call when ``app._waveform_bubble``"""

        # An app mock WITHOUT the _waveform_bubble attribute.
        class _AppNoBubble:
            _thread_registry = MagicMock()

        wiring = WaveformBubbleWiring(_AppNoBubble())
        wiring.stop()

    def test_stop_breaks_closure_reference_cycle(self, wiring, bubble, app):
        """DJ-28: the closure reference cycle is"""

        wiring._wire_waveform_bubble()
        # Sanity: the on_level closure captures ``self`` (wiring).
        on_level_closure = bubble.on_level
        assert on_level_closure is not None
        # The closure's __closure__ tuple contains cells. At least one
        closure_cells = on_level_closure.__closure__ or ()
        captured_self = any(cell.cell_contents is wiring for cell in closure_cells)
        assert captured_self, "precondition: on_level closure must capture the wiring instance (self)"

        wiring.stop()

        assert bubble.on_level is None

    def test_stop_does_not_break_subsequent_rewire(self, wiring, bubble):
        """subsequent ``_wire_waveform_bubble()`` call can re-wire them."""
        wiring._wire_waveform_bubble()
        first_worker = wiring._bubble_level_worker
        assert first_worker is not None and first_worker.is_alive()

        wiring.stop()
        # All callbacks nulled ().
        for attr in self._CALLBACK_ATTRS:
            assert getattr(bubble, attr) is None

        # Re-wire, must re-create the worker and re-set callbacks.
        wiring._wire_waveform_bubble()
        for attr in self._CALLBACK_ATTRS:
            assert callable(getattr(bubble, attr)), f"DJ-28: re-wire after stop() must re-set {attr}"
        # Cleanup.
        wiring.stop()
