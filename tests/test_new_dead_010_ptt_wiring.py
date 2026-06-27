"""Regression tests for NEW-DEAD-010: PTT (push-to-talk) mode must be
fully wired.

Previously ``HotkeyBackend.set_on_release`` was half-wired — the
config UI exposed PTT mode but key-release did not stop recording.
NEW-CQ-029 fixed this by:
1. Adding key-up transition detection to the Win32 polling backend.
2. Wiring ``hotkey_dispatcher._register()`` to call
   ``set_on_release(app._stop_dictation)`` when
   ``config.recording_mode == "push_to_talk"``.

These tests verify the wiring is in place and the key-up callback
fires the stop-dictation path.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch, call  # TEST-033: unified mock import

import pytest

from voice_typer.server import hotkeys
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher


class TestPttWiring:
    """NEW-DEAD-010: PTT mode must wire set_on_release to _stop_dictation."""

    def test_dispatcher_sets_on_release_in_ptt_mode(self):
        """When recording_mode is 'push_to_talk', the dispatcher must
        call ``set_on_release(app._stop_dictation)``.
        """
        source = inspect.getsource(HotkeyDispatcher.register)
        assert "set_on_release" in source, (
            "HotkeyDispatcher.register must call set_on_release for PTT mode"
        )
        assert "push_to_talk" in source, (
            "HotkeyDispatcher.register must check recording_mode == 'push_to_talk'"
        )
        assert "_stop_dictation" in source, (
            "HotkeyDispatcher.register must wire set_on_release to app._stop_dictation"
        )

    def test_win32_backend_fires_on_release_on_key_up(self):
        """The Win32 polling backend must detect key-up transitions and
        fire ``_on_release_callback``.
        """
        source = inspect.getsource(hotkeys.WindowsNativeHotkey._run_polling_loop)
        assert "_on_release_callback" in source, (
            "WindowsNativeHotkey._run_polling_loop must reference _on_release_callback"
        )
        # The key-up detection logic: "not is_pressed and was_pressed".
        assert "not is_pressed and was_pressed" in source, (
            "WindowsNativeHotkey._run_polling_loop must detect key-up transitions"
        )

    def test_pynput_backend_fires_on_release(self):
        """The pynput backend must fire _on_release_callback in its
        on_release handler.
        """
        # Find the PynputHotkey class's on_release closure.
        source = inspect.getsource(hotkeys.PynputHotkey._start_fallback)
        assert "_on_release_callback" in source, (
            "PynputHotkey._start_fallback must reference _on_release_callback"
        )
        assert "on_release" in source, (
            "PynputHotkey._start_fallback must register an on_release handler"
        )

    def test_set_on_release_stores_callback(self):
        """``HotkeyBackend.set_on_release`` must store the callback in
        ``self._on_release_callback``.
        """
        # Create a minimal backend instance to test set_on_release.
        backend = hotkeys.PynputHotkey.__new__(hotkeys.PynputHotkey)
        backend._on_release_callback = None

        callback_called = []
        def my_callback():
            callback_called.append(True)

        backend.set_on_release(my_callback)
        assert backend._on_release_callback is my_callback

        # Fire it.
        backend._on_release_callback()
        assert callback_called == [True]

    def test_set_on_release_accepts_none(self):
        """``set_on_release(None)`` must clear the callback (allowing
        toggle mode to override a previous PTT setting)."""
        backend = hotkeys.PynputHotkey.__new__(hotkeys.PynputHotkey)
        backend._on_release_callback = lambda: None

        backend.set_on_release(None)
        assert backend._on_release_callback is None


class TestPttFunctionalFlow:
    """Functional test: simulate the PTT wiring end-to-end."""

    def test_ptt_wiring_calls_stop_dictation_on_key_release(self):
        """When the dispatcher registers a hotkey in PTT mode, the
        backend's ``_on_release_callback`` must point to
        ``app._stop_dictation``.
        """
        app = MagicMock()
        app.config.hotkey = "<f2>"
        app.config.recording_mode = "push_to_talk"
        app.config.esc_cancel_enabled = False
        app.config.repaste_hotkey = ""
        app.toggle_dictation = MagicMock()
        app._stop_dictation = MagicMock()

        dispatcher = HotkeyDispatcher(app)

        # Mock the backend so we can capture the set_on_release call.
        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True

        with patch(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            return_value=fake_backend,
        ):
            dispatcher.register()

        # set_on_release must have been called with app._stop_dictation.
        fake_backend.set_on_release.assert_called_once_with(app._stop_dictation)

    def test_toggle_mode_does_not_set_on_release(self):
        """In toggle mode (not push_to_talk), set_on_release must NOT
        be called — the hotkey press toggles recording on/off.
        """
        app = MagicMock()
        app.config.hotkey = "<f2>"
        app.config.recording_mode = "toggle"
        app.config.esc_cancel_enabled = False
        app.config.repaste_hotkey = ""
        app.toggle_dictation = MagicMock()
        app._stop_dictation = MagicMock()

        dispatcher = HotkeyDispatcher(app)

        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True

        with patch(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            return_value=fake_backend,
        ):
            dispatcher.register()

        # set_on_release must NOT have been called.
        fake_backend.set_on_release.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
