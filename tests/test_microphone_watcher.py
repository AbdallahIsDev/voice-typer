"""Tests for ``voice_typer.server.microphone_watcher``.

PERF-MIC-001: verifies that the OS-event-driven microphone cache
invalidation works correctly on Linux (``/dev/snd`` polling), macOS
(``sounddevice.query_devices()`` polling), and Windows
(``WM_DEVICECHANGE`` via a hidden window + message pump).

The Windows ``WM_DEVICECHANGE`` path is exercised on Linux CI by
mocking ``ctypes.windll`` and ``ctypes.WINFUNCTYPE`` (which don't
exist on non-Windows) — following the same pattern as
``tests/test_clipboard_win32_coverage.py``. The macOS path is
exercised by mocking ``sounddevice`` in ``sys.modules``.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
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

        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        # Force Linux platform regardless of the host OS so the
        # _run_linux path is exercised.
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Let the watcher read the initial state — poll until at
                # least one listdir call has occurred (confirms the
                # baseline was captured).
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if callback_event.is_set():
                        break
                    time.sleep(0.02)
                # Simulate a device plug — entries change.
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # The next poll (within 50ms) should fire the callback.
                assert callback_event.wait(timeout=2.0), "Callback was not invoked within 2s of /dev/snd change"
            finally:
                watcher.stop()

        assert watcher._thread is None, "stop() should have cleared the thread ref"

    def test_watcher_does_not_crash_when_dev_snd_missing(self):
        """When ``/dev/snd`` doesn't exist, the watcher exits gracefully."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
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
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            assert watcher._thread is not None
            assert watcher._thread.is_alive()
            watcher.stop()

        # Thread ref cleared and thread no longer alive.
        assert watcher._thread is None

    def test_watcher_skips_unsupported_platform(self):
        """On an unsupported platform, ``start()`` does not spawn a thread."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        # "freebsd" is not in (windows, linux, macos) — exercises the
        # unsupported-platform skip path. (macOS is now supported via
        # _run_macos, so it can no longer be used here.)
        watcher._platform = "freebsd"

        watcher.start()
        # No thread should have been started.
        assert watcher._thread is None
        # stop() should be a safe no-op.
        watcher.stop()
        assert watcher._thread is None

    def test_watcher_start_is_idempotent(self):
        """Calling ``start()`` twice does not spawn a second thread."""
        state = {"entries": ["controlC0"]}
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            first_thread = watcher._thread
            watcher.start()  # second call — should be a no-op
            assert watcher._thread is first_thread
            watcher.stop()

    def test_watcher_logs_warning_on_callback_exception(self, caplog):
        """If the callback raises, a warning is logged and the thread continues."""
        state = {"entries": ["controlC0"]}

        def raising_callback() -> None:
            # Raise on every call — the watcher should log and continue.
            raise RuntimeError("boom from callback")

        watcher = MicrophoneDeviceWatcher(on_change=raising_callback, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
            caplog.at_level(
                logging.WARNING,
                logger="voice_typer.server.microphone_watcher",
            ),
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
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Invalidation callback raised" in m for m in warning_messages), (
            f"Expected 'Invalidation callback raised' warning in logs, got: {warning_messages}"
        )

    def test_watcher_logs_warning_when_run_method_crashes(self, caplog):
        """If the platform runner raises, ``_run`` logs a warning and exits."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        # Patch _run_linux to raise — _run should catch it and log.
        with (
            patch.object(watcher, "_run_linux", side_effect=RuntimeError("simulated crash")),
            caplog.at_level(
                logging.WARNING,
                logger="voice_typer.server.microphone_watcher",
            ),
        ):
            watcher.start()
            # Wait for the thread to enter _run and crash. Poll until
            # the thread is no longer alive.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if watcher._thread is not None and not watcher._thread.is_alive():
                    break
                time.sleep(0.02)
            # Thread should have exited (not alive).
            assert watcher._thread is not None
            watcher._thread.join(timeout=1.0)
            assert not watcher._thread.is_alive(), "Watcher thread should have exited after _run raised"
            watcher._thread = None  # clear so stop() is a no-op

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Watcher thread crashed" in m for m in warning_messages), (
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
        with patch("voice_typer.server.microphone_watcher.MicrophoneDeviceWatcher") as mock_watcher:
            mock_instance = mock_watcher.return_value
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            # Watcher was instantiated with the invalidation callback
            # and start() was called.
            mock_watcher.assert_called_once()
            assert mock_instance.start.called
            assert r._mic_watcher is mock_instance
            r.shutdown_mic_watcher()

    def test_recorder_shutdown_stops_watcher(self):
        """``shutdown_mic_watcher`` calls ``stop()`` on the watcher and clears the ref."""
        with patch("voice_typer.server.microphone_watcher.MicrophoneDeviceWatcher") as mock_watcher:
            mock_instance = mock_watcher.return_value
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
        with patch("voice_typer.server.microphone_watcher.MicrophoneDeviceWatcher") as mock_watcher:
            mock_instance = mock_watcher.return_value
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            # __del__ should not raise.
            r.__del__()

            # Watcher should have been stopped.
            mock_instance.stop.assert_called_once()


# ── macOS watcher tests ─────────────────────────────────────────────


class TestMicrophoneDeviceWatcherMacOS:
    """Unit tests for the ``_run_macos()`` polling implementation.

    These tests mock ``sounddevice`` in ``sys.modules`` so they run
    on any platform (Linux CI included). They verify that:

    - The callback fires when the device count changes.
    - The callback does NOT fire on the first poll (baseline capture).
    - An ``ImportError`` for ``sounddevice`` is handled gracefully.
    - A transient exception from ``query_devices`` doesn't kill the
      watcher thread.
    """

    def test_macos_watcher_polls_sounddevice_device_count(self):
        """When ``sounddevice``'s device count changes, the callback fires."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "macos"

        mock_sd = MagicMock()
        # Start with 2 devices.
        mock_sd.query_devices.return_value = [
            {"name": "dev1"},
            {"name": "dev2"},
        ]

        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            watcher.start()
            try:
                # Let the watcher capture the baseline (2 devices). Poll
                # until at least one query_devices call has occurred.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if mock_sd.query_devices.called:
                        break
                    time.sleep(0.02)
                # Simulate a device plug — count changes to 3.
                mock_sd.query_devices.return_value = [
                    {"name": "dev1"},
                    {"name": "dev2"},
                    {"name": "dev3"},
                ]
                # The next poll (within 50ms) should fire the callback.
                assert callback_event.wait(timeout=2.0), (
                    "Callback was not invoked within 2s of device count change on macOS"
                )
            finally:
                watcher.stop()

        assert watcher._thread is None, "stop() should have cleared the thread ref"

    def test_macos_watcher_does_not_fire_on_baseline_poll(self):
        """The first successful poll (baseline capture) does not fire the callback."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "macos"

        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [{"name": "dev1"}]

        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            watcher.start()
            # Let several poll cycles pass with a STABLE device count.
            # Poll for ~3 poll intervals (150ms) then assert no fire.
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                if callback_event.is_set():
                    break
                time.sleep(0.02)
            watcher.stop()

        # No callback should have fired — count never changed.
        assert not callback_event.is_set(), "Callback fired during baseline/stable polling"
        assert watcher._thread is None

    def test_macos_watcher_handles_sounddevice_import_error(self):
        """If ``sounddevice`` can't be imported, the watcher exits gracefully."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "macos"

        # Setting sys.modules[name] = None makes `import name` raise
        # ImportError ("import of name halted; None in sys.modules").
        with patch.dict(sys.modules, {"sounddevice": None}):
            watcher.start()
            # Wait for the thread to run _run_macos and hit the
            # ImportError early return. Poll until thread has exited.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if watcher._thread is None:
                    break
                time.sleep(0.02)
            watcher.stop()

        # No callback should have fired — the watcher exited early.
        assert not callback_event.is_set()
        assert watcher._thread is None

    def test_macos_watcher_handles_query_devices_exception(self):
        """A transient exception from ``query_devices`` doesn't kill the thread."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "macos"

        call_count = {"n": 0}

        def flaky_query():
            call_count["n"] += 1
            if call_count["n"] <= 2:
                # Simulate a PortAudioError on the first two calls
                # (baseline capture + first poll).
                raise OSError("PortAudio transient error")
            # After that, return a stable 1-device list.
            return [{"name": "dev1"}]

        mock_sd = MagicMock()
        mock_sd.query_devices.side_effect = flaky_query

        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            watcher.start()
            # Let several poll cycles pass — the watcher should
            # recover from the transient errors and NOT crash. Poll
            # until call_count > 2 (past the flaky calls) and the
            # thread is still alive.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if call_count["n"] > 2:
                    break
                time.sleep(0.02)
            watcher.stop()

        # No callback should have fired (count went from None-baseline
        # to 1, but the None guard suppresses the first change).
        assert not callback_event.is_set()
        # The thread should have exited cleanly (not crashed).
        assert watcher._thread is None


# ── Windows WM_DEVICECHANGE mock tests ──────────────────────────────


# WM_DEVICECHANGE = 0x0219 — broadcast when a device is added/removed.
_WM_DEVICECHANGE = 0x0219
# WM_QUIT = 0x0012 — posted by stop() to wake the message pump.
_WM_QUIT = 0x0012
# PM_REMOVE = 1 — PeekMessage flag: remove message from the queue.
_PM_REMOVE = 1
# WS_EX_TOOLWINDOW = 0x00000080 — creates a tool window (no taskbar button).
_WS_EX_TOOLWINDOW = 0x00000080


@pytest.fixture
def fake_windows_windll():
    """Mock ``ctypes.windll`` and ``ctypes.WINFUNCTYPE`` so ``_run_windows``
    executes on Linux.

    ``ctypes.windll`` and ``ctypes.WINFUNCTYPE`` only exist on Windows.
    We patch them with ``create=True`` so the Windows code path runs
    on any platform. ``WINFUNCTYPE`` is replaced with ``CFUNCTYPE``
    (same signature, different calling convention — irrelevant for
    mocked API calls).

    Yields a dict with ``user32`` and ``kernel32`` MagicMock objects
    that tests can configure per-case.
    """
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32

    # Default, sane return values for a happy-path message pump that
    # receives no messages (PeekMessageW returns 0 → inner loop exits
    # immediately → outer loop waits on _stop_event).
    mock_kernel32.GetModuleHandleW.return_value = 0x10000
    mock_user32.RegisterClassExW.return_value = 1  # non-zero atom
    mock_user32.CreateWindowExW.return_value = 0x20000  # non-zero hwnd
    mock_user32.PeekMessageW.return_value = 0  # no messages by default
    mock_user32.TranslateMessage.return_value = 1
    mock_user32.DispatchMessageW.return_value = 0
    mock_user32.DefWindowProcW.return_value = 0
    mock_user32.DestroyWindow.return_value = 1
    mock_user32.UnregisterClassW.return_value = 1
    mock_user32.PostMessageW.return_value = 1

    # CFUNCTYPE is a stand-in for WINFUNCTYPE on non-Windows. It
    # produces a compatible function-pointer type that works with
    # ctypes.Structure fields and callable wrapping.
    #
    # ctypes.get_last_error() / WinError() only exist on Windows;
    # the production code calls get_last_error() when RegisterClassExW
    # or CreateWindowExW fails. Patch them with create=True so the
    # failure paths run on Linux (returning 0 = ERROR_SUCCESS).
    with (
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.WINFUNCTYPE", ctypes.CFUNCTYPE, create=True),
        patch("ctypes.get_last_error", return_value=0, create=True),
        patch("ctypes.WinError", return_value=OSError(0, "mock"), create=True),
    ):
        yield {
            "user32": mock_user32,
            "kernel32": mock_kernel32,
            "windll": mock_windll,
        }


def _set_msg(byref_obj, message: int, hwnd: int = 1, wparam: int = 0, lparam: int = 0) -> None:
    """Fill a ``wintypes.MSG`` wrapped by ``ctypes.byref`` with the given fields.

    Mirrors what the real ``PeekMessageW`` would write into the MSG
    structure. ``byref_obj._obj`` is the underlying ``wintypes.MSG``
    instance.
    """
    msg = byref_obj._obj
    msg.hWnd = hwnd
    msg.message = message
    msg.wParam = wparam
    msg.lParam = lparam


class TestMicrophoneDeviceWatcherWindows:
    """Mock-based unit tests for ``_run_windows()``.

    These tests run on Linux by mocking ``ctypes.windll`` (which
    doesn't exist on non-Windows). They verify:

    - Window class registration with the correct class name.
    - Hidden window creation with ``WS_EX_TOOLWINDOW``.
    - ``WM_DEVICECHANGE`` dispatch invokes the callback (via the real
      ``_wnd_proc`` closure, captured from the ``WNDCLASSEXW`` struct).
    - ``WM_QUIT`` exits the message pump.
    - Cleanup (``DestroyWindow`` / ``UnregisterClassW``) runs on exit.
    - Register/create failures log warnings and fall back to TTL.
    """

    def test_windows_run_registers_window_class(self, fake_windows_windll):
        """``RegisterClassExW`` is called with class name ``VoiceTyperMicWatcherWnd``."""
        captured = {}

        def capture_register(wc_byref):
            wc = wc_byref._obj
            captured["class_name"] = wc.lpszClassName
            captured["cbSize"] = wc.cbSize
            return 1  # non-zero atom

        fake_windows_windll["user32"].RegisterClassExW.side_effect = capture_register
        # Make CreateWindowExW fail so the function returns early
        # right after registration — we only care about the register
        # call here.
        fake_windows_windll["user32"].CreateWindowExW.return_value = 0

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"
        watcher.start()
        # The thread should exit quickly (CreateWindowExW failed).
        watcher._thread.join(timeout=1.0)

        assert captured.get("class_name") == "VoiceTyperMicWatcherWnd", (
            f"Expected class name 'VoiceTyperMicWatcherWnd', got {captured.get('class_name')!r}"
        )
        # cbSize should be sizeof(WNDCLASSEXW) — a positive value
        # (the exact size depends on pointer width; we just check it
        # was set to a sane non-zero value).
        assert captured.get("cbSize", 0) > 0, f"Expected cbSize > 0, got {captured.get('cbSize')!r}"

    def test_windows_run_creates_message_window(self, fake_windows_windll):
        """``CreateWindowExW`` is called with ``WS_EX_TOOLWINDOW`` ex-style."""
        # Make CreateWindowExW fail (return 0) so the function returns
        # early — we only need to inspect the call args.
        fake_windows_windll["user32"].CreateWindowExW.return_value = 0

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"
        watcher.start()
        watcher._thread.join(timeout=1.0)

        call_args = fake_windows_windll["user32"].CreateWindowExW.call_args
        assert call_args is not None, "CreateWindowExW was never called"
        args, _ = call_args
        # First positional arg is dwExStyle.
        assert args[0] == _WS_EX_TOOLWINDOW, (
            f"Expected dwExStyle=WS_EX_TOOLWINDOW (0x{_WS_EX_TOOLWINDOW:x}), got 0x{args[0]:x}"
        )
        # Second positional arg is lpClassName.
        assert args[1] == "VoiceTyperMicWatcherWnd"

    def test_windows_run_dispatchs_wm_devicechange(self, fake_windows_windll):
        """A ``WM_DEVICECHANGE`` message triggers ``_invoke_callback``."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "windows"

        captured = {"wnd_proc": None, "peek_count": 0}

        def capture_register(wc_byref):
            # Grab the WNDPROC-wrapped _wnd_proc closure so we can
            # invoke it from the mocked DispatchMessageW — this
            # exercises the REAL _wnd_proc code path.
            captured["wnd_proc"] = wc_byref._obj.lpfnWndProc
            return 1

        def fake_peek(msg_byref, hwnd_filter, msg_min, msg_max, remove):
            captured["peek_count"] += 1
            if captured["peek_count"] == 1:
                _set_msg(msg_byref, _WM_DEVICECHANGE)
                return 1  # message available
            # After the first message, return 0 so the inner loop
            # exits and the outer loop re-checks _stop_event.
            return 0

        def fake_dispatch(msg_byref):
            msg = msg_byref._obj
            if captured["wnd_proc"] is not None:
                # Invoke the real _wnd_proc — it calls _invoke_callback
                # when msg == WM_DEVICECHANGE.
                captured["wnd_proc"](msg.hWnd, msg.message, msg.wParam, msg.lParam)
            return 0

        fake_windows_windll["user32"].RegisterClassExW.side_effect = capture_register
        fake_windows_windll["user32"].PeekMessageW.side_effect = fake_peek
        fake_windows_windll["user32"].DispatchMessageW.side_effect = fake_dispatch

        watcher.start()
        try:
            assert callback_event.wait(timeout=2.0), "Callback was not invoked within 2s of WM_DEVICECHANGE"
        finally:
            watcher.stop()

        # DispatchMessageW should have been called at least once.
        assert fake_windows_windll["user32"].DispatchMessageW.called

    def test_windows_run_stops_on_wm_quit(self, fake_windows_windll):
        """A ``WM_QUIT`` message causes ``_run_windows`` to return immediately."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"

        def fake_peek(msg_byref, hwnd_filter, msg_min, msg_max, remove):
            _set_msg(msg_byref, _WM_QUIT)
            return 1  # message available

        fake_windows_windll["user32"].PeekMessageW.side_effect = fake_peek

        watcher.start()
        # The thread should exit on its own (WM_QUIT → return) without
        # needing stop() to set _stop_event. Join with a timeout to
        # verify it exited.
        assert watcher._thread is not None
        watcher._thread.join(timeout=2.0)
        assert not watcher._thread.is_alive(), "Watcher thread should have exited after receiving WM_QUIT"
        # Clear the thread ref so stop() is a no-op (the thread already
        # exited; stop() would just join a dead thread, which is safe,
        # but we clear it to match the post-stop invariant).
        watcher._thread = None

    def test_windows_run_cleans_up_on_exit(self, fake_windows_windll):
        """``DestroyWindow`` and ``UnregisterClassW`` are called on exit."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"

        # Default mocks: RegisterClassExW → 1, CreateWindowExW → 0x20000,
        # PeekMessageW → 0 (no messages). The pump loops on _stop_event.
        watcher.start()
        # Let the pump enter the message loop. Poll until PeekMessageW
        # has been called (confirms the pump is running).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if fake_windows_windll["user32"].PeekMessageW.called:
                break
            time.sleep(0.02)
        watcher.stop()

        user32 = fake_windows_windll["user32"]
        kernel32 = fake_windows_windll["kernel32"]
        # DestroyWindow called with the hwnd returned by CreateWindowExW.
        user32.DestroyWindow.assert_called_once_with(0x20000)
        # UnregisterClassW called with (class_name, hInstance).
        h_instance = kernel32.GetModuleHandleW.return_value
        user32.UnregisterClassW.assert_called_once_with("VoiceTyperMicWatcherWnd", h_instance)

    def test_windows_run_logs_warning_on_register_failure(self, fake_windows_windll, caplog):
        """When ``RegisterClassExW`` returns 0, a warning is logged."""
        fake_windows_windll["user32"].RegisterClassExW.return_value = 0

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"

        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.microphone_watcher",
        ):
            watcher.start()
            watcher._thread.join(timeout=1.0)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("RegisterClassExW failed" in m for m in warning_messages), (
            f"Expected 'RegisterClassExW failed' warning, got: {warning_messages}"
        )
        # CreateWindowExW should NOT have been called (early return).
        assert not fake_windows_windll["user32"].CreateWindowExW.called, (
            "CreateWindowExW should not be called when RegisterClassExW fails"
        )
        watcher._thread = None  # already exited

    def test_windows_run_logs_warning_on_create_window_failure(self, fake_windows_windll, caplog):
        """When ``CreateWindowExW`` returns 0, a warning is logged."""
        # RegisterClassExW succeeds (returns atom), CreateWindowExW fails.
        fake_windows_windll["user32"].RegisterClassExW.return_value = 1
        fake_windows_windll["user32"].CreateWindowExW.return_value = 0

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"

        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.microphone_watcher",
        ):
            watcher.start()
            watcher._thread.join(timeout=1.0)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("CreateWindowExW failed" in m for m in warning_messages), (
            f"Expected 'CreateWindowExW failed' warning, got: {warning_messages}"
        )
        # The message pump should NOT have started (early return).
        assert not fake_windows_windll["user32"].PeekMessageW.called, (
            "PeekMessageW should not be called when CreateWindowExW fails"
        )
        watcher._thread = None  # already exited


# ── G4-M-41: active-mic-lost detection ──────────────────────────────


class TestMicrophoneWatcherActiveMicLost:
    """G4-M-41: when the active mic disappears from the device list,
    the watcher fires ``on_active_mic_lost`` so ``RecordingController``
    can cancel the in-flight recording instead of letting it stall on
    a dead input.

    These tests exercise the registration mechanism (``set_active_mic_id``,
    ``set_on_active_mic_lost``, ``set_device_id_provider``) and the
    check inside ``_invoke_callback``.  They run on Linux CI by forcing
    the platform to ``linux`` and mocking ``/dev/snd`` + the
    device-id-provider callable.
    """

    def test_microphone_watcher_invokes_on_active_mic_lost(self):
        """When the device list changes AND the active mic is no longer
        in the new list, ``on_active_mic_lost`` fires.

        Scenario:
        - Watcher is started on Linux with ``/dev/snd`` containing one
          entry (``controlC0``).
        - ``set_active_mic_id("the-active-mic")`` is called to simulate
          an in-flight recording on that mic.
        - ``set_device_id_provider`` returns a list that does NOT
          contain ``"the-active-mic"`` (simulating that the mic was
          unplugged — even though /dev/snd still changed, the active
          mic is gone from the queried list).
        - ``set_on_active_mic_lost`` registers a ``threading.Event``.
        - The test triggers a /dev/snd change and asserts the
          ``on_active_mic_lost`` event fires within 2 seconds.
        """
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()
        lost_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        # Register the active-mic-lost hooks.
        watcher.set_active_mic_id("the-active-mic")
        watcher.set_on_active_mic_lost(lost_event.set)
        # Provider returns a list WITHOUT "the-active-mic" — simulating
        # that the mic was unplugged (sounddevice would no longer
        # return it).
        watcher.set_device_id_provider(lambda: ["other-mic-1", "other-mic-2"])

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Let the watcher read the initial state. Poll until at
                # least one listdir call has occurred.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if change_event.is_set():
                        break
                    time.sleep(0.02)
                # Simulate a device change — entries change.  This
                # triggers _invoke_callback, which (after on_change)
                # calls _check_active_mic_lost.  The provider still
                # returns the "no the-active-mic" list, so
                # on_active_mic_lost fires.
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # The on_change event should fire first (cache
                # invalidation), then on_active_mic_lost.
                assert change_event.wait(timeout=2.0), (
                    "on_change was not invoked within 2s of /dev/snd change (active-mic-lost test prerequisite)"
                )
                assert lost_event.wait(timeout=2.0), (
                    "on_active_mic_lost was not invoked within 2s of "
                    "the device change even though the active mic is "
                    "no longer in the device_id_provider's list"
                )
            finally:
                watcher.stop()

        assert watcher._thread is None, "stop() should have cleared the thread ref"

    def test_active_mic_lost_does_not_fire_when_mic_still_present(self):
        """If the active mic is STILL in the device list after a
        change, ``on_active_mic_lost`` does NOT fire (no false positive)."""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()
        lost_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        watcher.set_active_mic_id("the-active-mic")
        watcher.set_on_active_mic_lost(lost_event.set)
        # Provider returns a list that DOES contain the active mic.
        watcher.set_device_id_provider(lambda: ["the-active-mic", "other"])

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Poll until baseline is captured.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if change_event.is_set():
                        break
                    time.sleep(0.02)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                assert change_event.wait(timeout=2.0), "on_change should still fire on device change"
                # Give the watcher a moment to (not) fire the lost cb.
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if lost_event.is_set():
                        break
                    time.sleep(0.02)
                assert not lost_event.is_set(), (
                    "on_active_mic_lost must NOT fire when the active mic is still in the device list (false positive)"
                )
            finally:
                watcher.stop()

    def test_active_mic_lost_does_not_fire_when_hooks_not_registered(self):
        """Backward compat: if no caller registers the hooks, the
        watcher's behavior is unchanged (no AttributeError, no
        spurious callback)."""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"
        # Intentionally do NOT call set_active_mic_id /
        # set_on_active_mic_lost / set_device_id_provider.

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Poll until baseline is captured.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if change_event.is_set():
                        break
                    time.sleep(0.02)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                assert change_event.wait(timeout=2.0), "on_change should fire even without active-mic-lost hooks"
                # The check method must be a no-op without the hooks.
                watcher._check_active_mic_lost()  # must not raise
            finally:
                watcher.stop()

    def test_active_mic_lost_clears_when_mic_id_set_to_none(self):
        """``set_active_mic_id(None)`` disables the check (e.g. after
        the recording stops, the watcher must not fire the callback
        even if the device list changes)."""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()
        lost_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        watcher.set_active_mic_id("the-active-mic")
        watcher.set_on_active_mic_lost(lost_event.set)
        watcher.set_device_id_provider(lambda: ["other-mic"])

        # Now clear the active mic — simulating recording stop.
        watcher.set_active_mic_id(None)

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Poll until baseline is captured.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if change_event.is_set():
                        break
                    time.sleep(0.02)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                assert change_event.wait(timeout=2.0)
                # Poll for the lost_event (should NOT fire).
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if lost_event.is_set():
                        break
                    time.sleep(0.02)
                assert not lost_event.is_set(), (
                    "on_active_mic_lost must NOT fire after set_active_mic_id(None) (recording stopped)"
                )
            finally:
                watcher.stop()

    def test_active_mic_lost_swallows_callback_exception(self, caplog):
        """If ``on_active_mic_lost`` raises, the watcher logs a warning
        and continues (the watcher thread must not die)."""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()

        def raising_lost_callback() -> None:
            raise RuntimeError("boom from on_active_mic_lost")

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        watcher.set_active_mic_id("the-active-mic")
        watcher.set_on_active_mic_lost(raising_lost_callback)
        watcher.set_device_id_provider(lambda: ["other-mic"])

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
            caplog.at_level(
                logging.WARNING,
                logger="voice_typer.server.microphone_watcher",
            ),
        ):
            watcher.start()
            try:
                # Poll until baseline is captured.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if change_event.is_set():
                        break
                    time.sleep(0.02)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # Wait for the on_change callback to fire (prereq).
                assert change_event.wait(timeout=2.0)
                # Give the watcher time to call _check_active_mic_lost
                # and run the raising callback. Poll for the warning log.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if any(
                        "on_active_mic_lost callback raised" in r.message
                        for r in caplog.records
                        if r.levelno >= logging.WARNING
                    ):
                        break
                    time.sleep(0.02)
            finally:
                watcher.stop()

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("on_active_mic_lost callback raised" in m for m in warning_messages), (
            f"Expected 'on_active_mic_lost callback raised' warning, got: {warning_messages}"
        )
        # The watcher thread must have exited cleanly via stop()
        # (not crashed mid-loop).
        assert watcher._thread is None
