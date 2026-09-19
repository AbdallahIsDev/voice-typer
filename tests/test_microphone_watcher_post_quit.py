"""Tests for ``MicrophoneDeviceWatcher._post_quit_to_windows`` (SI-18)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

_WM_QUIT = 0x0012


@pytest.fixture
def fake_windll_for_post_quit():
    """Mock ``ctypes.windll`` + ``ctypes.get_last_error`` so"""
    mock_user32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32

    # Default: PostMessageW succeeds (returns nonzero).
    mock_user32.PostMessageW.return_value = 1

    with (
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.get_last_error", return_value=0, create=True),
    ):
        yield {
            "user32": mock_user32,
            "windll": mock_windll,
        }


class TestPostQuitToWindowsArgtypes:
    """``_post_quit_to_windows`` must set argtypes/restype"""

    def test_post_quit_sets_restype_to_bool(self, fake_windll_for_post_quit):
        """``PostMessageW.restype`` is set to ``wintypes.BOOL`` before"""
        from ctypes import wintypes

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x0000000A_00001234  # 64-bit hwnd

        watcher._post_quit_to_windows()

        assert fake_windll_for_post_quit["user32"].PostMessageW.restype is wintypes.BOOL, (
            "PostMessageW.restype must be wintypes.BOOL, without this, the "
            "BOOL return is read as c_int and may truncate on 64-bit Windows"
        )

    def test_post_quit_sets_argtypes_to_hwnd_uint_wparam_lparam(self, fake_windll_for_post_quit):
        """``[HWND, UINT, WPARAM, LPARAM]`` before the call."""
        from ctypes import wintypes

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x0000000B_00005678  # 64-bit hwnd

        watcher._post_quit_to_windows()

        argtypes = fake_windll_for_post_quit["user32"].PostMessageW.argtypes
        assert argtypes == [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ], (
            "PostMessageW.argtypes must be [HWND, UINT, WPARAM, LPARAM], "
            f"got {argtypes!r}. Without HWND in argtypes, the 64-bit handle "
            "is truncated to c_int and PostMessageW returns 0."
        )

    def test_post_quit_calls_post_message_with_hwnd_and_wm_quit(self, fake_windll_for_post_quit):
        """``WM_QUIT`` (0x0012), the message that wakes the blocking"""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        hwnd = 0x1234_ABCD
        watcher._windows_hwnd = hwnd

        watcher._post_quit_to_windows()

        fake_windll_for_post_quit["user32"].PostMessageW.assert_called_once()
        call_args = fake_windll_for_post_quit["user32"].PostMessageW.call_args
        # ``PostMessageW(hwnd, WM_QUIT, 0, 0)``, wparam/lparam are 0.
        assert call_args.args[0] == hwnd, f"PostMessageW called with hwnd={call_args.args[0]!r}, expected {hwnd!r}"
        assert call_args.args[1] == _WM_QUIT, (
            f"PostMessageW called with msg={call_args.args[1]!r}, expected WM_QUIT (0x0012)"
        )

    def test_post_quit_sets_argtypes_before_call(self, fake_windll_for_post_quit):
        """``PostMessageW`` call, not after. This is the actual SI-18"""
        from ctypes import wintypes

        captured = {}

        def snapshot_then_call(*args, **kwargs):
            captured["restype_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.restype
            captured["argtypes_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.argtypes
            return 1  # success

        fake_windll_for_post_quit["user32"].PostMessageW.side_effect = snapshot_then_call

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0xDEAD_BEEF

        watcher._post_quit_to_windows()

        assert captured.get("restype_at_call") is wintypes.BOOL, (
            "restype was not set to wintypes.BOOL BEFORE the PostMessageW call, "
            "the 64-bit HWND truncation happens during ctypes' argument "
            "conversion, which runs BEFORE the function pointer is invoked. "
            f"Captured restype at call time: {captured.get('restype_at_call')!r}"
        )
        assert captured.get("argtypes_at_call") == [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ], (
            "argtypes was not set to [HWND, UINT, WPARAM, LPARAM] BEFORE the "
            "PostMessageW call. Captured argtypes at call time: "
            f"{captured.get('argtypes_at_call')!r}"
        )

    def test_post_quit_returns_without_raising_on_failure(self, fake_windll_for_post_quit, caplog):
        """If ``PostMessageW`` returns 0 (failure, e.g. window"""
        fake_windll_for_post_quit["user32"].PostMessageW.return_value = 0  # failure

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x10000

        with caplog.at_level(
            logging.DEBUG,
            logger="voice_typer.server.microphone_watcher",
        ):
            # Must not raise.
            watcher._post_quit_to_windows()

        # A debug log was emitted (the failure is traceable but silent).
        debug_msgs = [r.message for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("PostMessageW(WM_QUIT) returned 0" in m for m in debug_msgs), (
            f"Expected 'PostMessageW(WM_QUIT) returned 0' debug log on failure, got: {debug_msgs}"
        )

    def test_post_quit_noop_when_hwnd_unset(self, fake_windll_for_post_quit):
        """already cleaned up), ``PostMessageW`` is NOT called."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert getattr(watcher, "_windows_hwnd", None) is None

        watcher._post_quit_to_windows()

        fake_windll_for_post_quit["user32"].PostMessageW.assert_not_called()

    def test_post_quit_noop_when_hwnd_falsy(self, fake_windll_for_post_quit):
        """attribute was set to a falsy value), ``PostMessageW`` is NOT"""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0

        watcher._post_quit_to_windows()

        fake_windll_for_post_quit["user32"].PostMessageW.assert_not_called()

    def test_post_quit_noop_when_windll_unavailable(self, caplog):
        """``ctypes`` failed to expose it), the method logs and returns"""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x1234  # would normally trigger the call

        with (
            caplog.at_level(
                logging.DEBUG,
                logger="voice_typer.server.microphone_watcher",
            ),
            patch("ctypes.windll", _NoUser32Windll(), create=True),
        ):
            watcher._post_quit_to_windows()

        debug_msgs = [r.message for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("windll unavailable" in m for m in debug_msgs), (
            f"Expected 'windll unavailable' debug log on non-Windows, got: {debug_msgs}"
        )


class _NoUser32Windll:
    """Stand-in for ``ctypes.windll`` whose ``user32`` lookup fails."""

    @property
    def user32(self) -> None:
        raise AttributeError("mock: user32 unavailable")


class TestStopTriggersPostQuit:
    """Verify that ``stop()`` actually invokes"""

    def test_stop_invokes_post_quit_with_argtypes_set(self, fake_windll_for_post_quit):
        """``stop()`` → ``_post_quit_to_windows`` → ``PostMessageW``"""
        from ctypes import wintypes

        captured = {}

        def snapshot(*args, **kwargs):
            captured["argtypes_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.argtypes
            captured["restype_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.restype
            return 1

        fake_windll_for_post_quit["user32"].PostMessageW.side_effect = snapshot

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._platform = "windows"
        # Simulate the watcher thread having created a window.
        watcher._windows_hwnd = 0xCAFE_F00D
        # Provide a fake non-None thread so ``stop()`` doesn't early-return
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = False
        watcher._thread = fake_thread

        watcher.stop()

        assert captured.get("restype_at_call") is wintypes.BOOL, (
            "stop() → _post_quit_to_windows did not set restype = wintypes.BOOL before PostMessageW was invoked"
        )
        assert captured.get("argtypes_at_call") == [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ], (
            "stop() → _post_quit_to_windows did not set argtypes = "
            "[HWND, UINT, WPARAM, LPARAM] before PostMessageW was invoked"
        )
