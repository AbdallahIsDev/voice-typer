"""Tests for ``voice_typer.server.microphone_watcher``.

PERF-MIC-001: verifies that the OS-event-driven microphone cache
invalidation works correctly on Linux (``/dev/snd`` polling) and
that the watcher degrades gracefully on unsupported platforms or
when the callback raises.

The Windows ``WM_DEVICECHANGE`` path is exercised only on Windows
CI runners (these tests don't mock it); the Linux path is tested
here by mocking ``os.listdir`` / ``os.path.isdir`` to simulate
``/dev/snd`` content changes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher


# ── Helpers ──────────────────────────────────────────────────────────


def _make_listdir_mock(state: dict) -> callable:
    """Return a side_effect for ``os.listdir`` that serves /dev/snd from ``state``.

    All other paths fall through to the real ``os.listdir`` so the
    test doesn't break unrelated filesystem access (e.g. pytest's
    own cache writes).
    """
    real_listdir = os.listdir

    def _mock(path):
        if path == "/dev/snd":
            return list(state["entries"])
        return real_listdir(path)

    return _mock


def _isdir_mock(path: str) -> bool:
    """Return True only for ``/dev/snd``."""
    return path == "/dev/snd"


# ── Watcher tests ────────────────────────────────────────────────────


class TestMicrophoneDeviceWatcher:
    """Unit tests for the ``MicrophoneDeviceWatcher`` class."""

    def test_watcher_calls_callback_on_linux_dev_snd_change(self):
        """When ``/dev/snd`` entries change, the callback is invoked."""
        state = {"entries": ["controlC0"]}
        callback_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(
            on_change=callback_event.set, poll_interval=0.05
        )
        # Force Linux platform regardless of the host OS so the
        # _run_linux path is exercised.
        watcher._platform = "linux"

        with patch("os.listdir", side_effect=_make_listdir_mock(state)), \
             patch("os.path.isdir", side_effect=_isdir_mock):
            watcher.start()
            try:
                # Let the watcher read the initial state.
                time.sleep(0.15)
                # Simulate a device plug — entries change.
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # The next poll (within 50ms) should fire the callback.
                assert callback_event.wait(timeout=2.0), (
                    "Callback was not invoked within 2s of /dev/snd change"
                )
            finally:
                watcher.stop()

        assert watcher._thread is None, "stop() should have cleared the thread ref"

    def test_watcher_does_not_crash_when_dev_snd_missing(self):
        """When ``/dev/snd`` doesn't exist, the watcher exits gracefully."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(
            on_change=callback_event.set, poll_interval=0.05
        )
        watcher._platform = "linux"

        with patch("os.path.isdir", return_value=False):
            watcher.start()
            # Give the thread time to run _run_linux and hit the
            # isdir() == False early return.
            time.sleep(0.2)
            watcher.stop()

        # No callback should have fired (no /dev/snd to watch).
        assert not callback_event.is_set()
        assert watcher._thread is None

    def test_watcher_stop_joins_thread(self):
        """``stop()`` joins the watcher thread within the timeout."""
        state = {"entries": ["controlC0"]}
        watcher = MicrophoneDeviceWatcher(
            on_change=lambda: None, poll_interval=0.05
        )
        watcher._platform = "linux"

        with patch("os.listdir", side_effect=_make_listdir_mock(state)), \
             patch("os.path.isdir", side_effect=_isdir_mock):
            watcher.start()
            assert watcher._thread is not None
            assert watcher._thread.is_alive()
            watcher.stop()

        # Thread ref cleared and thread no longer alive.
        assert watcher._thread is None

    def test_watcher_skips_unsupported_platform(self):
        """On macOS, ``start()`` does not spawn a thread."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._platform = "macos"

        watcher.start()
        # No thread should have been started.
        assert watcher._thread is None
        # stop() should be a safe no-op.
        watcher.stop()
        assert watcher._thread is None

    def test_watcher_start_is_idempotent(self):
        """Calling ``start()`` twice does not spawn a second thread."""
        state = {"entries": ["controlC0"]}
        watcher = MicrophoneDeviceWatcher(
            on_change=lambda: None, poll_interval=0.05
        )
        watcher._platform = "linux"

        with patch("os.listdir", side_effect=_make_listdir_mock(state)), \
             patch("os.path.isdir", side_effect=_isdir_mock):
            watcher.start()
            first_thread = watcher._thread
            watcher.start()  # second call — should be a no-op
            assert watcher._thread is first_thread
            watcher.stop()

    def test_watcher_logs_warning_on_callback_exception(self, caplog):
        """If the callback raises, a warning is logged and the thread continues."""
        state = {"entries": ["controlC0"]}
        second_event = threading.Event()

        def raising_callback() -> None:
            # Raise on every call — the watcher should log and continue.
            raise RuntimeError("boom from callback")

        watcher = MicrophoneDeviceWatcher(
            on_change=raising_callback, poll_interval=0.05
        )
        watcher._platform = "linux"

        with patch("os.listdir", side_effect=_make_listdir_mock(state)), \
             patch("os.path.isdir", side_effect=_isdir_mock), \
             caplog.at_level(
                 logging.WARNING,
                 logger="voice_typer.server.microphone_watcher",
             ):
            watcher.start()
            try:
                # Let the initial state be read, then trigger a change.
                time.sleep(0.15)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # Wait long enough for at least one callback invocation
                # to fire and raise.
                time.sleep(0.3)
            finally:
                watcher.stop()

        # The callback exception should have been caught and logged
        # as a WARNING — NOT propagated out of the thread.
        warning_messages = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "Invalidation callback raised" in m for m in warning_messages
        ), (
            f"Expected 'Invalidation callback raised' warning in logs, "
            f"got: {warning_messages}"
        )

    def test_watcher_logs_warning_when_run_method_crashes(self, caplog):
        """If the platform runner raises, ``_run`` logs a warning and exits."""
        watcher = MicrophoneDeviceWatcher(
            on_change=lambda: None, poll_interval=0.05
        )
        watcher._platform = "linux"

        # Patch _run_linux to raise — _run should catch it and log.
        with patch.object(
            watcher, "_run_linux", side_effect=RuntimeError("simulated crash")
        ), caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.microphone_watcher",
        ):
            watcher.start()
            # Wait for the thread to enter _run and crash.
            time.sleep(0.2)
            # Thread should have exited (not alive).
            assert watcher._thread is not None
            watcher._thread.join(timeout=1.0)
            assert not watcher._thread.is_alive(), (
                "Watcher thread should have exited after _run raised"
            )
            watcher._thread = None  # clear so stop() is a no-op

        warning_messages = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "Watcher thread crashed" in m for m in warning_messages
        ), (
            f"Expected 'Watcher thread crashed' warning, got: {warning_messages}"
        )

    def test_watcher_unknown_platform_does_not_start_thread(self):
        """On unknown platforms, ``start()`` is a no-op."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._platform = "unknown"

        watcher.start()
        assert watcher._thread is None
        watcher.stop()  # safe no-op


# ── Recorder integration tests ──────────────────────────────────────


class TestRecorderWatcherIntegration:
    """Verifies that ``Recorder`` creates, starts, and stops the watcher."""

    def test_recorder_invalidates_cache_on_watcher_event(self):
        """``Recorder._invalidate_device_cache`` resets the cache fields."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Populate the cache with stale data.
        r._device_list_cache = [{"id": "0", "name": "stale"}]
        r._device_list_cache_time = 12345.6

        r._invalidate_device_cache()

        assert r._device_list_cache is None
        assert r._device_list_cache_time == 0.0
        # Clean up the watcher.
        r.shutdown_mic_watcher()

    def test_recorder_creates_and_starts_watcher(self):
        """``Recorder.__init__`` creates a ``MicrophoneDeviceWatcher`` and starts it."""
        # Patch the watcher class so no real thread is spawned.
        with patch(
            "voice_typer.server.microphone_watcher.MicrophoneDeviceWatcher"
        ) as MockWatcher:
            mock_instance = MockWatcher.return_value
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            # Watcher was instantiated with the invalidation callback
            # and start() was called.
            MockWatcher.assert_called_once()
            assert mock_instance.start.called
            assert r._mic_watcher is mock_instance
            r.shutdown_mic_watcher()

    def test_recorder_shutdown_stops_watcher(self):
        """``shutdown_mic_watcher`` calls ``stop()`` on the watcher and clears the ref."""
        with patch(
            "voice_typer.server.microphone_watcher.MicrophoneDeviceWatcher"
        ) as MockWatcher:
            mock_instance = MockWatcher.return_value
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            r.shutdown_mic_watcher()
            mock_instance.stop.assert_called_once()
            assert r._mic_watcher is None

            # Idempotent — second call is a no-op.
            r.shutdown_mic_watcher()

    def test_recorder_survives_watcher_import_failure(self):
        """If the watcher import fails, ``Recorder`` still works (TTL fallback)."""
        # Simulate the import failing by patching the module's
        # MicrophoneDeviceWatcher to None and making the import raise.
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "voice_typer.server.microphone_watcher":
                raise ImportError("simulated import failure")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            # Watcher is None — TTL polling covers this case.
            assert r._mic_watcher is None
            # shutdown_mic_watcher is a safe no-op.
            r.shutdown_mic_watcher()

    def test_recorder_del_does_not_raise_when_watcher_present(self):
        """``__del__`` does not raise even when the watcher is active."""
        with patch(
            "voice_typer.server.microphone_watcher.MicrophoneDeviceWatcher"
        ) as MockWatcher:
            mock_instance = MockWatcher.return_value
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            # __del__ should not raise.
            r.__del__()

            # Watcher should have been stopped.
            mock_instance.stop.assert_called_once()
