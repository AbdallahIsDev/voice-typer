"""Tests for ``voice_typer.server.microphone_watcher``."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

from tests.fixtures.wait_for import wait_for


def _make_listdir_mock(state: dict) -> callable:
    """Return a side_effect for ``os.listdir`` that serves /dev/snd from ``state``."""
    real_listdir = os.listdir

    def _mock(path):
        if path == "/dev/snd":
            return list(state["entries"])
        return real_listdir(path)

    return _mock


def _isdir_mock(path: str) -> bool:
    """Return True only for ``/dev/snd``."""
    return path == "/dev/snd"


class TestMicrophoneDeviceWatcher:
    """Unit tests for the ``MicrophoneDeviceWatcher`` class."""

    def test_watcher_calls_callback_on_linux_dev_snd_change(self):
        """When ``/dev/snd`` entries change, the callback is invoked."""
        state = {"entries": ["controlC0"]}
        callback_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Let the watcher read the initial state, wait until at
                wait_for(lambda: callback_event.is_set(), timeout=2.0)
                # Simulate a device plug, entries change.
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
            # Wait for the worker thread to enter _run_linux, see
            assert watcher._thread is not None
            watcher._thread.join(timeout=2.0)
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
        watcher._platform = "freebsd"

        watcher.start()
        # No thread should have been started.
        assert watcher._thread is None
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
            watcher.start()  # second call, should be a no-op
            assert watcher._thread is first_thread
            watcher.stop()

    def test_watcher_logs_warning_on_callback_exception(self, caplog):
        """If the callback raises, a warning is logged and the thread continues."""
        state = {"entries": ["controlC0"]}
        listdir_calls = {"count": 0}

        def raising_callback() -> None:
            # Raise on every call, the watcher should log and continue.
            raise RuntimeError("boom from callback")

        real_make_listdir_mock = _make_listdir_mock

        def _instrumented_listdir(state_dict):
            underlying = real_make_listdir_mock(state_dict)

            def _wrapper(path):
                listdir_calls["count"] += 1
                return underlying(path)

            return _wrapper

        watcher = MicrophoneDeviceWatcher(on_change=raising_callback, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_instrumented_listdir(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
            caplog.at_level(
                logging.WARNING,
                logger="voice_typer.server.microphone_watcher",
            ),
        ):
            watcher.start()
            try:

                def _warning_seen() -> bool:
                    return any(
                        "Invalidation callback raised" in r.message
                        for r in caplog.records
                        if r.levelno >= logging.WARNING
                    )

                # Step 1: wait for the watcher's initial listdir call
                wait_for(lambda: listdir_calls["count"] >= 1, timeout=2.0)

                # Step 2: trigger a state change so the next poll
                state["entries"] = ["controlC0", "pcmC0D0c"]

                # Step 3: adaptively poll for the warning to appear
                wait_for(_warning_seen, timeout=2.0)
            finally:
                watcher.stop()

        # The callback exception should have been caught and logged
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Invalidation callback raised" in m for m in warning_messages), (
            f"Expected 'Invalidation callback raised' warning in logs, got: {warning_messages}"
        )

    def test_watcher_logs_warning_when_run_method_crashes(self, caplog):
        """If the platform runner raises, ``_run`` logs a warning and exits."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        # Patch _run_linux to raise, _run should catch it and log.
        with (
            patch.object(watcher, "_run_linux", side_effect=RuntimeError("simulated crash")),
            caplog.at_level(
                logging.WARNING,
                logger="voice_typer.server.microphone_watcher",
            ),
        ):
            watcher.start()
            # Wait for the thread to enter _run and crash. Poll until
            wait_for(
                lambda: watcher._thread is not None and not watcher._thread.is_alive(),
                timeout=2.0,
            )
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


class TestRecorderWatcherIntegration:
    """Verifies that ``Recorder`` creates, starts, and stops the watcher."""

    def test_recorder_invalidates_cache_on_watcher_event(self):
        """``DeviceManager._invalidate_device_cache`` resets the cache fields."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Populate the cache with stale data.
        r._devices._device_list_cache = [{"id": "0", "name": "stale"}]
        r._devices._device_list_cache_time = 12345.6

        r._devices._invalidate_device_cache()

        assert r._devices._device_list_cache is None
        assert r._devices._device_list_cache_time == 0.0
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
            mock_watcher.assert_called_once()
            assert mock_instance.start.called
            assert r._devices._mic_watcher is mock_instance
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
            assert r._devices._mic_watcher is None

            # Idempotent, second call is a no-op.
            r.shutdown_mic_watcher()

    def test_recorder_survives_watcher_import_failure(self):
        """If the watcher import fails, ``Recorder`` still works (TTL fallback)."""
        # Simulate the import failing by patching the module's
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "voice_typer.server.microphone_watcher":
                raise ImportError("simulated import failure")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            from voice_typer.server.recording import Recorder

            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            # Watcher is None. TTL polling covers this case.
            assert r._devices._mic_watcher is None
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


class TestMicrophoneDeviceWatcherMacOS:
    """Unit tests for the ``_run_macos()`` polling implementation."""

    @pytest.fixture(autouse=True)
    def _force_polling_path(self):
        """sounddevice-polling implementation they were written for."""
        with patch.object(
            MicrophoneDeviceWatcher,
            "_try_create_coreaudio_watcher",
            return_value=None,
        ):
            yield

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
                # Let the watcher capture the baseline (2 devices). Wait
                wait_for(lambda: mock_sd.query_devices.called, timeout=2.0)
                # Simulate a device plug, count changes to 3.
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
            callback_event.wait(timeout=0.6)
            watcher.stop()

        # No callback should have fired, count never changed.
        assert not callback_event.is_set(), "Callback fired during baseline/stable polling"
        assert watcher._thread is None

    def test_macos_watcher_handles_sounddevice_import_error(self):
        """If ``sounddevice`` can't be imported, the watcher exits gracefully."""
        callback_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=callback_event.set, poll_interval=0.05)
        watcher._platform = "macos"

        # Setting sys.modules[name] = None makes `import name` raise
        with patch.dict(sys.modules, {"sounddevice": None}):
            watcher.start()
            wait_for(
                lambda: watcher._thread is not None and not watcher._thread.is_alive(),
                timeout=2.0,
            )
            watcher.stop()

        # No callback should have fired, the watcher exited early.
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
                raise OSError("PortAudio transient error")
            # After that, return a stable 1-device list.
            return [{"name": "dev1"}]

        mock_sd = MagicMock()
        mock_sd.query_devices.side_effect = flaky_query

        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            watcher.start()
            # Let several poll cycles pass, the watcher should
            wait_for(lambda: call_count["n"] > 2, timeout=2.0)
            watcher.stop()

        # No callback should have fired (count went from None-baseline
        assert not callback_event.is_set()
        # The thread should have exited cleanly (not crashed).
        assert watcher._thread is None


# WM_DEVICECHANGE = 0x0219, broadcast when a device is added/removed.
_WM_DEVICECHANGE = 0x0219
# WM_QUIT = 0x0012, posted by stop() to wake the message pump.
_WM_QUIT = 0x0012
# PM_REMOVE = 1. PeekMessage flag: remove message from the queue.
_PM_REMOVE = 1
# WS_EX_TOOLWINDOW = 0x00000080, creates a tool window (no taskbar button).
_WS_EX_TOOLWINDOW = 0x00000080


@pytest.fixture
def fake_windows_windll():
    """Mock ``ctypes.windll`` and ``ctypes.WINFUNCTYPE`` so ``_run_windows``"""
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32

    mock_kernel32.GetModuleHandleW.return_value = 0x10000
    mock_user32.RegisterClassExW.return_value = 1  # non-zero atom
    mock_user32.CreateWindowExW.return_value = 0x20000  # non-zero hwnd
    mock_user32.GetMessageW.return_value = 0  # WM_QUIT by default
    mock_user32.TranslateMessage.return_value = 1
    mock_user32.DispatchMessageW.return_value = 0
    mock_user32.DefWindowProcW.return_value = 0
    mock_user32.DestroyWindow.return_value = 1
    mock_user32.UnregisterClassW.return_value = 1
    mock_user32.PostMessageW.return_value = 1

    # CFUNCTYPE is a stand-in for WINFUNCTYPE on non-Windows. It
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
    """Fill a ``wintypes.MSG`` wrapped by ``ctypes.byref`` with the given fields."""
    msg = byref_obj._obj
    msg.hWnd = hwnd
    msg.message = message
    msg.wParam = wparam
    msg.lParam = lparam


class TestMicrophoneDeviceWatcherWindows:
    """Mock-based unit tests for ``_run_windows()``."""

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
        fake_windows_windll["user32"].CreateWindowExW.return_value = 0

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"
        watcher.start()
        # The thread should exit quickly (CreateWindowExW failed).
        watcher._thread.join(timeout=1.0)

        assert captured.get("class_name") == "VoiceTyperMicWatcherWnd", (
            f"Expected class name 'VoiceTyperMicWatcherWnd', got {captured.get('class_name')!r}"
        )
        assert captured.get("cbSize", 0) > 0, f"Expected cbSize > 0, got {captured.get('cbSize')!r}"

    def test_windows_run_creates_message_window(self, fake_windows_windll):
        """``CreateWindowExW`` is called with ``WS_EX_TOOLWINDOW`` ex-style."""
        # Make CreateWindowExW fail (return 0) so the function returns
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

        captured = {"wnd_proc": None, "get_count": 0}

        def capture_register(wc_byref):
            # Grab the WNDPROC-wrapped _wnd_proc closure so we can
            captured["wnd_proc"] = wc_byref._obj.lpfnWndProc
            return 1

        def fake_get(msg_byref, hwnd_filter, msg_min, msg_max):
            # GetMessageW signature: (LPMSG, HWND, UINT, UINT), 4 args
            captured["get_count"] += 1
            if captured["get_count"] == 1:
                _set_msg(msg_byref, _WM_DEVICECHANGE)
                return 1  # message available
            return 0

        def fake_dispatch(msg_byref):
            msg = msg_byref._obj
            if captured["wnd_proc"] is not None:
                # Invoke the real _wnd_proc, it calls _invoke_callback
                captured["wnd_proc"](msg.hWnd, msg.message, msg.wParam, msg.lParam)
            return 0

        fake_windows_windll["user32"].RegisterClassExW.side_effect = capture_register
        fake_windows_windll["user32"].GetMessageW.side_effect = fake_get
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

        def fake_get(msg_byref, hwnd_filter, msg_min, msg_max):
            # GetMessageW returning 0 == WM_QUIT retrieved → pump exits.
            _set_msg(msg_byref, _WM_QUIT)
            return 0

        fake_windows_windll["user32"].GetMessageW.side_effect = fake_get

        watcher.start()
        # The thread should exit on its own (WM_QUIT → return) without
        assert watcher._thread is not None
        watcher._thread.join(timeout=2.0)
        assert not watcher._thread.is_alive(), "Watcher thread should have exited after receiving WM_QUIT"
        # Clear the thread ref so stop() is a no-op (the thread already
        watcher._thread = None

    def test_windows_run_cleans_up_on_exit(self, fake_windows_windll):
        """``DestroyWindow`` and ``UnregisterClassW`` are called on exit."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "windows"

        # Default mocks: RegisterClassExW → 1, CreateWindowExW → 0x20000,
        watcher.start()
        # Let the pump enter the message loop. Wait until GetMessageW
        wait_for(lambda: fake_windows_windll["user32"].GetMessageW.called, timeout=2.0)
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
        assert not fake_windows_windll["user32"].GetMessageW.called, (
            "GetMessageW should not be called when CreateWindowExW fails"
        )
        watcher._thread = None  # already exited


class TestMicrophoneWatcherActiveMicLost:
    """G4-M-41: when the active mic disappears from the device list,"""

    def test_microphone_watcher_invokes_on_active_mic_lost(self):
        """When the device list changes AND the active mic is no longer"""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()
        lost_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        # Register the active-mic-lost hooks.
        watcher.set_active_mic_id("the-active-mic")
        watcher.set_on_active_mic_lost(lost_event.set)
        # Provider returns a list WITHOUT "the-active-mic", simulating
        watcher.set_device_id_provider(lambda: ["other-mic-1", "other-mic-2"])

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Let the watcher read the initial state. Wait until
                wait_for(lambda: change_event.is_set(), timeout=2.0)
                # Simulate a device change, entries change.  This
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # The on_change event should fire first (cache
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
        """If the active mic is STILL in the device list after a"""
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
                # Wait for the baseline to be captured (the on_change
                wait_for(lambda: change_event.is_set(), timeout=2.0)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                assert change_event.wait(timeout=2.0), "on_change should still fire on device change"
                # outcome here (the lost callback must NOT fire when
                lost_event.wait(timeout=1.0)
                assert not lost_event.is_set(), (
                    "on_active_mic_lost must NOT fire when the active mic is still in the device list (false positive)"
                )
            finally:
                watcher.stop()

    def test_active_mic_lost_does_not_fire_when_hooks_not_registered(self):
        """watcher's behavior is unchanged (no AttributeError, no"""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"
        # Intentionally do NOT call set_active_mic_id /

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Wait for the baseline to be captured.
                wait_for(lambda: change_event.is_set(), timeout=2.0)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                assert change_event.wait(timeout=2.0), "on_change should fire even without active-mic-lost hooks"
                # The check method must be a no-op without the hooks.
                watcher._check_active_mic_lost()  # must not raise
            finally:
                watcher.stop()

    def test_active_mic_lost_clears_when_mic_id_set_to_none(self):
        """``set_active_mic_id(None)`` disables the check (e.g. after"""
        state = {"entries": ["controlC0"]}
        change_event = threading.Event()
        lost_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=change_event.set, poll_interval=0.05)
        watcher._platform = "linux"

        watcher.set_active_mic_id("the-active-mic")
        watcher.set_on_active_mic_lost(lost_event.set)
        watcher.set_device_id_provider(lambda: ["other-mic"])

        # Now clear the active mic, simulating recording stop.
        watcher.set_active_mic_id(None)

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            try:
                # Wait for the baseline to be captured.
                wait_for(lambda: change_event.is_set(), timeout=2.0)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                assert change_event.wait(timeout=2.0)
                # outcome here (the lost callback must NOT fire after
                lost_event.wait(timeout=1.0)
                assert not lost_event.is_set(), (
                    "on_active_mic_lost must NOT fire after set_active_mic_id(None) (recording stopped)"
                )
            finally:
                watcher.stop()

    def test_active_mic_lost_swallows_callback_exception(self, caplog):
        """If ``on_active_mic_lost`` raises, the watcher logs a warning"""
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
                # Wait for the baseline to be captured.
                wait_for(lambda: change_event.is_set(), timeout=2.0)
                state["entries"] = ["controlC0", "pcmC0D0c"]
                # Wait for the on_change callback to fire (prereq).
                assert change_event.wait(timeout=2.0)
                # Wait for the watcher to call _check_active_mic_lost
                wait_for(
                    lambda: any(
                        "on_active_mic_lost callback raised" in r.message
                        for r in caplog.records
                        if r.levelno >= logging.WARNING
                    ),
                    timeout=2.0,
                )
            finally:
                watcher.stop()

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("on_active_mic_lost callback raised" in m for m in warning_messages), (
            f"Expected 'on_active_mic_lost callback raised' warning, got: {warning_messages}"
        )
        # The watcher thread must have exited cleanly via stop()
        assert watcher._thread is None


class TestDefaultPollIntervalBumped:
    """reduce idle CPU/battery drain (the watcher runs for the entire app"""

    def test_default_poll_interval_is_5_seconds(self):
        """When ``poll_interval`` is not passed, the default is 5.0 (not 1.0)."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert watcher._poll_interval == 5.0, (
            "DJ-48: default poll_interval must be 5.0 (was 1.0 pre-fix). "
            "A 1 Hz idle wakeup on a lifetime daemon thread is wasteful."
        )

    def test_explicit_poll_interval_is_respected(self):
        """Callers can still override the default (tests pass 0.05)."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        assert watcher._poll_interval == 0.05


class TestDefaultDeviceChangeDetection:
    """DJ-66: when the OS default input device changes, the watcher fires"""

    def test_default_change_callback_fires_on_index_change(self):
        """When the default input index changes between two checks, the"""
        fired_event = threading.Event()

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"
        watcher.set_on_default_device_changed(fired_event.set)

        with patch.object(watcher, "_query_default_input_device", return_value=MagicMock(index=0)):
            watcher._check_default_device_changed()
        assert watcher._last_default_input_index == 0
        assert not fired_event.is_set(), "first capture must NOT fire the callback"
        # Change the default index, the next check should fire.
        with patch.object(watcher, "_query_default_input_device", return_value=MagicMock(index=3)):
            watcher._check_default_device_changed()
        assert fired_event.is_set(), "DJ-66: on_default_device_changed must fire on index change"
        assert watcher._last_default_input_index == 3

    def test_default_change_callback_does_not_fire_on_first_capture(self):
        """The first check captures the baseline without firing the callback"""
        fired_event = threading.Event()
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"
        watcher.set_on_default_device_changed(fired_event.set)

        with patch.object(watcher, "_query_default_input_device", return_value=MagicMock(index=5)):
            watcher._check_default_device_changed()

        assert watcher._last_default_input_index == 5
        assert not fired_event.is_set(), "DJ-66: first capture must NOT fire on_default_device_changed"

    def test_default_change_callback_noop_when_not_registered(self):
        """When no callback is registered, ``_check_default_device_changed``"""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"
        # No set_on_default_device_changed call.
        assert watcher._on_default_device_changed is None
        # Direct invocation must not raise and must not change state.
        with patch.object(watcher, "_query_default_input_device", return_value=MagicMock(index=0)):
            watcher._check_default_device_changed()
        assert watcher._last_default_input_index is None  # never captured

    def test_default_change_callback_swallows_exceptions(self, caplog):
        """If the callback raises, the watcher logs a warning and continues."""
        fired = {"count": 0}

        def raising_callback() -> None:
            fired["count"] += 1
            raise RuntimeError("boom from on_default_device_changed")

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"
        watcher.set_on_default_device_changed(raising_callback)

        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.microphone_watcher",
        ):
            # Capture baseline.
            with patch.object(watcher, "_query_default_input_device", return_value=MagicMock(index=0)):
                watcher._check_default_device_changed()
            # Trigger a change.
            with patch.object(watcher, "_query_default_input_device", return_value=MagicMock(index=2)):
                watcher._check_default_device_changed()

        assert fired["count"] >= 1, "callback must have been invoked"
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("on_default_device_changed callback raised" in m for m in warning_messages), (
            f"Expected warning about callback raising, got: {warning_messages}"
        )


class TestMicrophoneWatcherLifecycleLock:
    """UE-12-F2: ``MicrophoneDeviceWatcher.start()``/``stop()`` are"""

    def test_start_lock_serializes_concurrent_starts(self):
        """Eight concurrent ``start()`` calls spawn exactly one thread."""
        state = {"entries": ["controlC0"]}
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            threads = [threading.Thread(target=watcher.start) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Only one thread should have won the race, _thread is
            assert watcher._thread is not None, "start() should have spawned a thread"
            first_thread = watcher._thread
            # A follow-up start() from the main thread is a no-op.
            watcher.start()
            assert watcher._thread is first_thread, "start() after concurrent starts must return the SAME thread"
            watcher.stop()

        assert watcher._thread is None

    def test_stop_lock_serializes_concurrent_stops(self):
        """Eight concurrent ``stop()`` calls don't raise and clear _thread once."""
        state = {"entries": ["controlC0"]}
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        with (
            patch("os.listdir", side_effect=_make_listdir_mock(state)),
            patch("os.path.isdir", side_effect=_isdir_mock),
        ):
            watcher.start()
            assert watcher._thread is not None

            threads = [threading.Thread(target=watcher.stop) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All stop() callers returned without raising; _thread is
            assert watcher._thread is None

    def test_lifecycle_lock_attribute_exists(self):
        """UE-12-F2: ``self._lock`` is a threading lock."""
        import threading as _threading

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert watcher._lock is not None
        assert callable(getattr(watcher._lock, "acquire", None))
        assert callable(getattr(watcher._lock, "release", None))
        # Not held initially.
        acquired = watcher._lock.acquire(blocking=False)
        try:
            assert acquired, "_lock should not be held when no start/stop is running"
        finally:
            watcher._lock.release()
        # Sanity: the lock is a real lock (not a no-op dummy).
        assert isinstance(watcher._lock, type(_threading.Lock()))


class TestMicrophoneWatcherHooksLock:
    """UE-12-F14: ``_check_active_mic_lost`` snapshots"""

    def test_hooks_lock_attribute_exists(self):
        """UE-12-F14: ``self._hooks_lock`` is a threading lock."""
        import threading as _threading

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert watcher._hooks_lock is not None
        assert callable(getattr(watcher._hooks_lock, "acquire", None))
        assert callable(getattr(watcher._hooks_lock, "release", None))
        assert isinstance(watcher._hooks_lock, type(_threading.Lock()))

    def test_check_active_mic_lost_uses_snapshot_not_live_value(self):
        """The snapshot is taken before the provider runs."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        lost_event = threading.Event()
        watcher.set_active_mic_id("mic-1")
        watcher.set_on_active_mic_lost(lost_event.set)

        def provider():
            # Mutate active_mic_id mid-check. Without the snapshot,
            watcher.set_active_mic_id("mic-2")
            return ["mic-2"]

        watcher.set_device_id_provider(provider)

        # Direct invocation, no watcher thread needed.
        watcher._check_active_mic_lost()

        assert lost_event.is_set(), (
            "UE-12-F14: _check_active_mic_lost must use the snapshotted "
            "active_mic_id ('mic-1'), not the live value ('mic-2' set by "
            "the provider). Without the snapshot, the check would see "
            "'mic-2' in the device list and skip the lost callback."
        )

    def test_check_active_mic_lost_clears_id_during_provider_skips_via_snapshot(self):
        """A concurrent ``set_active_mic_id(None)`` during the provider"""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)
        watcher._platform = "linux"

        lost_event = threading.Event()
        watcher.set_active_mic_id("mic-1")
        watcher.set_on_active_mic_lost(lost_event.set)

        def provider():
            watcher.set_active_mic_id(None)
            return ["other-mic"]

        watcher.set_device_id_provider(provider)

        watcher._check_active_mic_lost()

        assert lost_event.is_set(), (
            "The snapshot was 'mic-1' (not in ['other-mic']); the lost "
            "callback must fire even though a concurrent set_active_mic_id(None) "
            "landed during the provider call."
        )
        # After the check, the live value reflects the concurrent clear.
        assert watcher._active_mic_id is None

    def test_set_methods_are_thread_safe_under_concurrent_calls(self):
        """Concurrent ``set_*`` calls don't corrupt the attributes."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None, poll_interval=0.05)

        def setter_active():
            for _i in range(200):
                watcher.set_active_mic_id(f"mic-{_i}")

        def setter_lost():
            for _i in range(200):
                watcher.set_on_active_mic_lost(lambda: None)

        def setter_provider():
            for _i in range(200):
                watcher.set_device_id_provider(lambda: [])

        threads = [
            threading.Thread(target=setter_active),
            threading.Thread(target=setter_lost),
            threading.Thread(target=setter_provider),
            threading.Thread(target=setter_active),
            threading.Thread(target=setter_lost),
            threading.Thread(target=setter_provider),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All setters completed without deadlock. The final values
        assert watcher._active_mic_id is None or isinstance(watcher._active_mic_id, str)
        assert callable(watcher._on_active_mic_lost)
        assert callable(watcher._device_id_provider)
