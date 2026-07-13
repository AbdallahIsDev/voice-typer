"""Tests for IME false-fire scenarios.

TEST-018 / PLAT-020: The IME-composition gate is implemented in
``WindowsNativeHotkey._is_ime_composing()`` (a static method on the
hotkey backend). The dispatcher no longer carries per-hotkey callbacks
or a mutable ``_ime_composing`` flag — instead, the Win32 polling loop
calls ``_is_ime_composing()`` each iteration and skips the callback
while the IME is composing.

These tests verify the actual implementation rather than the legacy
dispatcher API that was removed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _import_ime_check():
    """Import the WindowsNativeHotkey class (and its _is_ime_composing method).

    Returns None if the class cannot be imported.
    """
    try:
        from voice_typer.server.hotkeys import WindowsNativeHotkey
        return WindowsNativeHotkey
    except ImportError:
        return None


class TestIMEFalseFire:
    """Test that hotkeys are suppressed during IME composition."""

    def test_is_ime_composing_returns_false_on_non_windows(self):
        """On non-Windows the IME gate is a no-op (always False)."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        with patch("voice_typer.server.hotkeys.is_windows", return_value=False):
            assert cls._is_ime_composing() is False

    def test_is_ime_composing_true_when_ime_open_with_composition_string(self):
        """When IME is open AND a composition string is present, return True."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")

        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 1
        imm32 = MagicMock()
        imm32.ImmGetContext.return_value = 42
        imm32.ImmGetOpenStatus.return_value = 1
        # ImmGetCompositionStringW returns byte length of the composition string.
        imm32.ImmGetCompositionStringW.return_value = 4  # 4 bytes → composition present

        fake_windll = MagicMock()
        fake_windll.user32 = user32
        fake_windll.imm32 = imm32

        with patch("voice_typer.server.hotkeys.is_windows", return_value=True), \
             patch("ctypes.windll", fake_windll, create=True):
            assert cls._is_ime_composing() is True

    def test_is_ime_composing_false_when_ime_closed(self):
        """When ImmGetOpenStatus is 0 (IME closed), return False."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")

        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 1
        imm32 = MagicMock()
        imm32.ImmGetContext.return_value = 42
        imm32.ImmGetOpenStatus.return_value = 0  # IME closed

        fake_windll = MagicMock()
        fake_windll.user32 = user32
        fake_windll.imm32 = imm32

        with patch("voice_typer.server.hotkeys.is_windows", return_value=True), \
             patch("ctypes.windll", fake_windll, create=True):
            assert cls._is_ime_composing() is False

    def test_is_ime_composing_false_when_no_composition_string(self):
        """When IME is open but composition string is empty, return False."""
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")

        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 1
        imm32 = MagicMock()
        imm32.ImmGetContext.return_value = 42
        imm32.ImmGetOpenStatus.return_value = 1
        imm32.ImmGetCompositionStringW.return_value = 0  # no composition string

        fake_windll = MagicMock()
        fake_windll.user32 = user32
        fake_windll.imm32 = imm32

        with patch("voice_typer.server.hotkeys.is_windows", return_value=True), \
             patch("ctypes.windll", fake_windll, create=True):
            assert cls._is_ime_composing() is False


class TestIMEMarker:
    """Register the ime_composing marker for pytest."""

    @staticmethod
    def pytest_configure(config):
        config.addinivalue_line(
            "markers",
            "ime: test IME composition behavior",
        )
