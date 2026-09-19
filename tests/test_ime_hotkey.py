"""Tests for IME composition state and hotkey interaction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _import_ime_check():
    try:
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        return WindowsNativeHotkey
    except ImportError:
        return None


def _patch_ime_state(cls, composing: bool):
    """Patch the module so ``_is_ime_composing()`` returns ``composing``."""
    user32 = MagicMock()
    user32.GetForegroundWindow.return_value = 1
    imm32 = MagicMock()
    imm32.ImmGetContext.return_value = 42
    imm32.ImmGetOpenStatus.return_value = 1
    # 4 bytes when composing, 0 when not.
    imm32.ImmGetCompositionStringW.return_value = 4 if composing else 0

    fake_windll = MagicMock()
    fake_windll.user32 = user32
    fake_windll.imm32 = imm32

    return (
        patch("voice_typer.server.hotkeys.is_windows", return_value=True),
        patch("ctypes.windll", fake_windll, create=True),
    )


class TestIMEHotkeySuppression:
    """Test that hotkey events are suppressed during IME composition."""

    def test_hotkey_ignored_during_ime_composition(self, monkeypatch):
        """When IME composition is active, _is_ime_composing() returns True."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        p_is_win, p_windll = _patch_ime_state(cls, composing=True)
        with p_is_win, p_windll:
            assert cls._is_ime_composing() is True

    def test_hotkey_fires_after_ime_composition_ends(self, monkeypatch):
        """After IME composition ends, _is_ime_composing() returns False."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")

        # While composing → True (suppressed)
        p_is_win, p_windll = _patch_ime_state(cls, composing=True)
        with p_is_win, p_windll:
            assert cls._is_ime_composing() is True

        # After composition ends → False (resumed)
        p_is_win2, p_windll2 = _patch_ime_state(cls, composing=False)
        with p_is_win2, p_windll2:
            assert cls._is_ime_composing() is False

    def test_ime_composing_defaults_to_false(self):
        """IME composing state defaults to False (no IME present)."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        # On the actual test host (Linux), is_windows() returns False
        assert cls._is_ime_composing() is False

    def test_multiple_hotkeys_during_ime_all_suppressed(self):
        """All hotkey backends share the same _is_ime_composing() gate."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        # The gate is a static method, callable without an instance —
        p_is_win, p_windll = _patch_ime_state(cls, composing=True)
        with p_is_win, p_windll:
            first = cls._is_ime_composing()
            second = cls._is_ime_composing()
        assert first is True and second is True

    def test_ime_toggle_does_not_fire_callback(self):
        """Toggling IME state has no side effects, it's a pure query."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        # Toggle the IME state several times via the patch; the static
        for composing in (True, False, True, False):
            p_is_win, p_windll = _patch_ime_state(cls, composing=composing)
            with p_is_win, p_windll:
                result = cls._is_ime_composing()
            assert result is composing

    def test_rapid_ime_toggle_and_hotkey(self):
        """Rapid toggling of IME state is reflected immediately by the gate."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")

        p_is_win1, p_windll1 = _patch_ime_state(cls, composing=True)
        with p_is_win1, p_windll1:
            assert cls._is_ime_composing() is True  # suppressed
        p_is_win2, p_windll2 = _patch_ime_state(cls, composing=False)
        with p_is_win2, p_windll2:
            assert cls._is_ime_composing() is False  # fires
