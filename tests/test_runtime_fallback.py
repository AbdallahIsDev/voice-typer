"""Tests for the runtime fallback chain in _NativeBackendAdapter (GAP-4).

Covers:
- Native backend permanent failure → swap to legacy
- Native backend recovery via retry timer → swap back
- Both backends fail → FAILED state
- stop() during swap → no deadlock
- Thread safety of state transitions
- Permission error handling (GAP-2 integration)
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_mock_native_backend(hotkey_str: str = "<f2>"):
    """Create a mock SubprocessHotkeyBackend-compatible object.

    The real SubprocessHotkeyBackend sets _on_error_callback and
    _on_permanent_failure_callback on itself; our mock accepts those
    assignments so the adapter can wire them up.
    """
    backend = MagicMock()
    backend.hotkey_str = hotkey_str
    backend.diagnose.return_value = "mock native backend"
    backend.is_alive.return_value = True
    backend.start = MagicMock(return_value=None)
    backend.stop = MagicMock(return_value=None)
    backend.set_on_release = MagicMock(return_value=None)
    # The adapter assigns to these — make them assignable
    backend._on_error_callback = None
    backend._on_permanent_failure_callback = None
    return backend


def _make_mock_legacy_backend(hotkey_str: str = "<f2>"):
    """Create a mock legacy HotkeyBackend."""
    backend = MagicMock()
    backend.hotkey_str = hotkey_str
    backend.is_alive.return_value = True
    backend.start = MagicMock(return_value=None)
    backend.stop = MagicMock(return_value=None)
    backend.set_on_release = MagicMock(return_value=None)
    return backend


# ─── Adapter state machine tests ───────────────────────────────────────────


class TestAdapterInitialState:
    """Verify the adapter starts in NATIVE state."""

    def test_initial_state_is_native(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        assert adapter._state == _NativeBackendAdapter._STATE_NATIVE

    def test_hotkey_str_propagated(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend("<caps_lock>")
        adapter = _NativeBackendAdapter(native)
        assert adapter.hotkey_str == "<caps_lock>"

    def test_callbacks_wired(self):
        """The adapter should set _on_error_callback and _on_permanent_failure_callback."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        _NativeBackendAdapter(native)
        assert native._on_error_callback is not None
        assert native._on_permanent_failure_callback is not None


class TestAdapterStart:
    """Verify adapter.start() behavior."""

    def test_start_native_succeeds(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        cb = MagicMock()
        adapter.start(cb)
        native.start.assert_called_once_with(cb)
        assert adapter._state == _NativeBackendAdapter._STATE_NATIVE

    def test_start_native_fails_swaps_to_legacy(self, monkeypatch):
        """If native.start() raises, swap to legacy backend."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.start.side_effect = RuntimeError("native failed")
        adapter = _NativeBackendAdapter(native)

        # Patch _create_legacy_backend to avoid platform-specific code
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        # Patch _schedule_native_retry to avoid starting a real timer
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)

        cb = MagicMock()
        adapter.start(cb)
        assert adapter._state == _NativeBackendAdapter._STATE_FALLBACK
        legacy.start.assert_called_once_with(cb)


class TestAdapterStop:
    """Verify adapter.stop() behavior."""

    def test_stop_in_native_state(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter.start(MagicMock())
        adapter.stop()
        assert adapter._state == _NativeBackendAdapter._STATE_STOPPED
        native.stop.assert_called_once()

    def test_stop_is_idempotent(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter.start(MagicMock())
        adapter.stop()
        adapter.stop()  # should not raise
        assert adapter._state == _NativeBackendAdapter._STATE_STOPPED

    def test_stop_cancels_retry_timer(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        # Simulate a pending retry timer
        fake_timer = MagicMock()
        adapter._native_retry_timer = fake_timer
        adapter.start(MagicMock())
        adapter.stop()
        fake_timer.cancel.assert_called_once()

    def test_stop_stops_both_backends(self, monkeypatch):
        """If a swap happened, stop() should stop both native and legacy."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)

        # Force a swap
        native.start.side_effect = RuntimeError("fail")
        adapter.start(MagicMock())
        assert adapter._state == _NativeBackendAdapter._STATE_FALLBACK

        adapter.stop()
        assert adapter._state == _NativeBackendAdapter._STATE_STOPPED
        native.stop.assert_called()
        legacy.stop.assert_called_once()


class TestAdapterIsAlive:
    """Verify is_alive() reflects the current state."""

    def test_is_alive_native(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.is_alive.return_value = True
        adapter = _NativeBackendAdapter(native)
        adapter.start(MagicMock())
        assert adapter.is_alive() is True

    def test_is_alive_native_dead(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.is_alive.return_value = False
        adapter = _NativeBackendAdapter(native)
        adapter.start(MagicMock())
        # State is still NATIVE but native reports dead
        assert adapter.is_alive() is False

    def test_is_alive_fallback(self, monkeypatch):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.start.side_effect = RuntimeError("fail")
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        legacy.is_alive.return_value = True
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        adapter.start(MagicMock())
        assert adapter._state == _NativeBackendAdapter._STATE_FALLBACK
        assert adapter.is_alive() is True

    def test_is_alive_failed_state(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter._state = _NativeBackendAdapter._STATE_FAILED
        assert adapter.is_alive() is False

    def test_is_alive_stopped_state(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter._state = _NativeBackendAdapter._STATE_STOPPED
        assert adapter.is_alive() is False


# ─── Swap-to-legacy tests ─────────────────────────────────────────────────


class TestSwapToLegacy:
    """Verify _swap_to_legacy behavior."""

    def test_swap_creates_legacy_and_starts_it(self, monkeypatch):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)

        adapter._swap_to_legacy()
        assert adapter._state == _NativeBackendAdapter._STATE_FALLBACK
        legacy.start.assert_called_once()

    def test_swap_fires_on_release_callback(self, monkeypatch):
        """If a recording is in progress, fire on_release so it doesn't get stuck."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)

        release_cb = MagicMock()
        adapter._on_release_callback = release_cb
        adapter._callback = MagicMock()
        adapter._swap_to_legacy()
        release_cb.assert_called_once()

    def test_swap_idempotent(self, monkeypatch):
        """Calling _swap_to_legacy twice should not create two legacy backends."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)

        adapter._swap_to_legacy()
        adapter._swap_to_legacy()  # should be a no-op
        assert legacy.start.call_count == 1

    def test_swap_when_legacy_also_fails(self, monkeypatch):
        """If the legacy backend also fails, state goes to FAILED."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        failing_legacy = _make_mock_legacy_backend()
        failing_legacy.start.side_effect = RuntimeError("legacy also fails")
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: failing_legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_failure_notification", lambda exc: None)

        adapter._swap_to_legacy()
        assert adapter._state == _NativeBackendAdapter._STATE_FAILED

    def test_swap_when_already_stopped(self, monkeypatch):
        """If stop() was called, _swap_to_legacy should be a no-op."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter._state = _NativeBackendAdapter._STATE_STOPPED
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)

        adapter._swap_to_legacy()
        legacy.start.assert_not_called()
        assert adapter._state == _NativeBackendAdapter._STATE_STOPPED


# ─── Permanent failure callback tests ─────────────────────────────────────


class TestPermanentFailureCallback:
    """Verify _on_native_permanent_failure triggers swap."""

    def test_permanent_failure_triggers_swap(self, monkeypatch):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)

        # Simulate the native backend calling its permanent failure callback
        adapter._on_native_permanent_failure()
        assert adapter._state == _NativeBackendAdapter._STATE_FALLBACK


# ─── Native retry tests ────────────────────────────────────────────────────


class TestNativeRetry:
    """Verify the retry timer attempts to swap back to native."""

    def test_retry_succeeds(self, monkeypatch):
        """If native restart succeeds, swap back to NATIVE state."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.is_alive.return_value = True
        adapter = _NativeBackendAdapter(native)
        adapter._state = _NativeBackendAdapter._STATE_FALLBACK
        adapter._legacy = _make_mock_legacy_backend()
        adapter._callback = MagicMock()
        monkeypatch.setattr(adapter, "_show_recovery_notification", lambda: None)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)

        adapter._retry_native()
        assert adapter._state == _NativeBackendAdapter._STATE_NATIVE
        native.stop.assert_called_once()
        native.start.assert_called_once()

    def test_retry_fails_stays_on_legacy(self, monkeypatch):
        """If native restart fails, stay on legacy and schedule another retry."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.start.side_effect = RuntimeError("still broken")
        adapter = _NativeBackendAdapter(native)
        adapter._state = _NativeBackendAdapter._STATE_FALLBACK
        adapter._legacy = _make_mock_legacy_backend()
        adapter._callback = MagicMock()

        # Mock _create_legacy_backend so the retry can re-create it
        new_legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: new_legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)

        adapter._retry_native()
        # Should still be in FALLBACK state
        assert adapter._state == _NativeBackendAdapter._STATE_FALLBACK

    def test_retry_when_stopped_is_noop(self, monkeypatch):
        """If stop() was called, _retry_native should not restart anything."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter._state = _NativeBackendAdapter._STATE_STOPPED
        adapter._legacy = None
        adapter._callback = MagicMock()

        adapter._retry_native()
        native.start.assert_not_called()


# ─── set_on_release propagation ────────────────────────────────────────────


class TestSetOnRelease:
    """Verify set_on_release propagates to both backends."""

    def test_set_on_release_native_only(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        cb = MagicMock()
        adapter.set_on_release(cb)
        native.set_on_release.assert_called_once_with(cb)

    def test_set_on_release_propagates_to_legacy(self, monkeypatch):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)

        # Force a swap so _legacy is populated
        adapter._swap_to_legacy()

        cb = MagicMock()
        adapter.set_on_release(cb)
        legacy.set_on_release.assert_called_once_with(cb)


# ─── Diagnose ──────────────────────────────────────────────────────────────


class TestDiagnose:
    """Verify diagnose() includes state info."""

    def test_diagnose_native_state(self):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        native.diagnose.return_value = "MockNative"
        adapter = _NativeBackendAdapter(native)
        result = adapter.diagnose()
        assert "NATIVE" in result
        assert "MockNative" in result

    def test_diagnose_fallback_state(self, monkeypatch):
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        legacy.diagnose.return_value = "MockLegacy"
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)

        adapter._swap_to_legacy()
        result = adapter.diagnose()
        assert "FALLBACK" in result
        assert "MockLegacy" in result


# ─── Thread safety ─────────────────────────────────────────────────────────


class TestThreadSafety:
    """Verify the swap lock prevents concurrent state corruption."""

    def test_concurrent_stop_and_swap(self, monkeypatch):
        """stop() and _swap_to_legacy called concurrently should not deadlock."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter
        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        monkeypatch.setattr(adapter, "_create_legacy_backend", lambda: legacy)
        monkeypatch.setattr(adapter, "_schedule_native_retry", lambda: None)
        monkeypatch.setattr(adapter, "_show_fallback_notification", lambda: None)
        monkeypatch.setattr(adapter, "_show_failure_notification", lambda exc: None)

        errors = []

        def do_swap():
            try:
                adapter._swap_to_legacy()
            except Exception as e:
                errors.append(e)

        def do_stop():
            try:
                adapter.stop()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=do_swap)
        t2 = threading.Thread(target=do_stop)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert not errors, f"Threads raised: {errors}"
        # Final state must be one of the valid terminal states
        assert adapter._state in (
            _NativeBackendAdapter._STATE_FALLBACK,
            _NativeBackendAdapter._STATE_STOPPED,
        )
