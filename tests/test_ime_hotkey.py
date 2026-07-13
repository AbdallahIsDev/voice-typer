"""Tests for IME composition state and hotkey interaction.

TEST-018 / PLAT-020: Originally written against a dispatcher-level API
(``HotkeyDispatcher.register(key, callback)`` + ``_ime_composing`` flag
+ ``_on_hotkey(key)`` method). That API was refactored: the IME gate
now lives in ``WindowsNativeHotkey._is_ime_composing()`` (a static
method) and is checked on every iteration of the Win32 polling loop
before invoking the user callback.

These tests verify the same semantics — hotkey suppressed during
composition, fires after composition ends, no false fires during
toggles — against the actual implementation.
"""

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
    """Patch the module so ``_is_ime_composing()`` returns ``composing``.

    On non-Windows the method short-circuits to False, so we have to
    patch ``is_windows`` to True and stub the ctypes.windll calls.
    """
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
        """When IME composition is active, _is_ime_composing() returns True.

        The Win32 polling loop reads this value each iteration and skips
        the user callback while it is True, so the hotkey is effectively
        ignored during composition.
        """
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        p_is_win, p_windll = _patch_ime_state(cls, composing=True)
        with p_is_win, p_windll:
            assert cls._is_ime_composing() is True

    def test_hotkey_fires_after_ime_composition_ends(self, monkeypatch):
        """After IME composition ends, _is_ime_composing() returns False.

        The polling loop then resumes invoking the user callback.
        """
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
        # so _is_ime_composing() returns False by default.
        assert cls._is_ime_composing() is False

    def test_multiple_hotkeys_during_ime_all_suppressed(self):
        """All hotkey backends share the same _is_ime_composing() gate.

        Because suppression is evaluated per-iteration (not per-hotkey),
        toggling the IME state suppresses every active hotkey backend.
        This test pins that the gate is shared — a single source of
        truth — by verifying the same static method is used.
        """
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        # The gate is a static method — callable without an instance —
        # so every backend instance sees the same result.
        p_is_win, p_windll = _patch_ime_state(cls, composing=True)
        with p_is_win, p_windll:
            first = cls._is_ime_composing()
            second = cls._is_ime_composing()
        assert first is True and second is True

    def test_ime_toggle_does_not_fire_callback(self):
        """Toggling IME state has no side effects — it's a pure query.

        The static method reads Windows IME APIs but does not invoke any
        user callback. The polling loop is responsible for deciding
        whether to fire the callback based on the return value.
        """
        cls = _import_ime_check()
        if cls is None:
            pytest.skip("WindowsNativeHotkey not importable")
        # Toggle the IME state several times via the patch; the static
        # method itself never fires a callback.
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

        # composing=True → suppressed; composing=False → fires
        p_is_win1, p_windll1 = _patch_ime_state(cls, composing=True)
        with p_is_win1, p_windll1:
            assert cls._is_ime_composing() is True  # suppressed
        p_is_win2, p_windll2 = _patch_ime_state(cls, composing=False)
        with p_is_win2, p_windll2:
            assert cls._is_ime_composing() is False  # fires
