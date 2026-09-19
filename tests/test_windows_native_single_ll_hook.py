"""AB-35 regression tests: when 3 hotkey backends are registered (main"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def mock_win32(monkeypatch):
    """Provide mocked user32 / kernel32 / winmm DLLs."""
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_winmm = MagicMock()

    mock_user32.RegisterHotKey.return_value = 1  # BOOL TRUE
    mock_user32.UnregisterHotKey.return_value = 1
    mock_user32.PostThreadMessageW.return_value = 1
    mock_user32.GetAsyncKeyState.return_value = 0  # key not pressed
    # SetWindowsHookExW returns a non-zero handle (truthy) → hook installs.
    mock_user32.SetWindowsHookExW.return_value = 0xDEADBEEF
    mock_user32.UnhookWindowsHookEx.return_value = 1
    mock_user32.CallNextHookEx.return_value = 0
    # GetMessageW returns 0 (WM_QUIT) → message loop exits immediately.
    mock_user32.GetMessageW.return_value = 0
    mock_user32.TranslateMessage.return_value = 0
    mock_user32.DispatchMessageW.return_value = 0

    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.Sleep = MagicMock()

    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.winmm = mock_winmm
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)

    return mock_user32, mock_kernel32, mock_winmm


def _make_backend(hotkey_str: str, *, prefer_message_loop: bool = False):
    """Construct a WindowsNativeHotkey with the given preference flag."""
    from voice_typer.server.hotkeys import WindowsNativeHotkey

    backend = WindowsNativeHotkey(hotkey_str)
    backend._prefer_message_loop_first = prefer_message_loop
    return backend


def test_three_backends_install_only_one_ll_hook(mock_win32):
    """AB-35: with the main hotkey using the LL hook and ESC + repaste"""
    mock_user32, _, _ = mock_win32

    backends = []
    try:
        # Main dictation hotkey: default (LL hook preferred).
        main = _make_backend("<f2>", prefer_message_loop=False)
        main.start(MagicMock())
        backends.append(main)

        # ESC cancel hotkey: prefer WM_HOTKEY ().
        esc = _make_backend("<esc>", prefer_message_loop=True)
        esc.start(MagicMock())
        backends.append(esc)

        # Repaste hotkey: prefer WM_HOTKEY ().
        repaste = _make_backend("<f6>", prefer_message_loop=True)
        repaste.start(MagicMock())
        backends.append(repaste)

        # Give the message-loop threads a moment to spin up and either
        for b in backends:
            if b._thread is not None:
                b._thread.join(timeout=2.0)

        set_hook_calls = mock_user32.SetWindowsHookExW.call_count
        assert set_hook_calls == 1, (
            f"AB-35 regression: expected exactly 1 SetWindowsHookExW call "
            f"(main only); got {set_hook_calls}. Before AB-35 this was 3 "
            f"(one per backend, 3× per-keystroke system-wide CPU)."
        )

        # Sanity: RegisterHotKey was called for all 3 backends.
        assert mock_user32.RegisterHotKey.call_count == 3, (
            f"Expected RegisterHotKey called 3 times (once per backend); got {mock_user32.RegisterHotKey.call_count}."
        )

        # The main backend's _hook_handle is set (LL hook installed).
        assert main._hook_handle is not None, (
            "Main backend should have an LL hook installed (prefer_message_loop=False)"
        )
        # ESC and repaste backends have NO LL hook (they use WM_HOTKEY).
        assert esc._hook_handle is None, (
            "ESC backend should NOT install an LL hook (prefer_message_loop=True, RegisterHotKey succeeded), AB-35"
        )
        assert repaste._hook_handle is None, (
            "Repaste backend should NOT install an LL hook (prefer_message_loop=True, RegisterHotKey succeeded), AB-35"
        )
    finally:
        for b in backends:
            b.stop()


def test_main_backend_alone_installs_one_ll_hook(mock_win32):
    """AB-35 baseline: a single main backend (default preference) still"""
    mock_user32, _, _ = mock_win32

    backend = _make_backend("<f2>", prefer_message_loop=False)
    try:
        backend.start(MagicMock())
        if backend._thread is not None:
            backend._thread.join(timeout=2.0)

        assert mock_user32.SetWindowsHookExW.call_count == 1
        assert backend._hook_handle is not None
    finally:
        backend.stop()


def test_prefer_message_loop_with_failed_register_hotkey_falls_back_to_ll_hook(mock_win32):
    """AB-35 fallback: if ``RegisterHotKey`` fails for an ESC/repaste"""
    mock_user32, _, _ = mock_win32
    # Make RegisterHotKey fail (e.g. ESC is already claimed by another app).
    mock_user32.RegisterHotKey.return_value = 0

    backend = _make_backend("<esc>", prefer_message_loop=True)
    try:
        backend.start(MagicMock())
        if backend._thread is not None:
            backend._thread.join(timeout=2.0)

        assert mock_user32.SetWindowsHookExW.call_count == 1, (
            "ESC backend with failed RegisterHotKey should fall back to LL hook "
            "(AB-35: 2 hooks instead of 3, still an improvement)"
        )
        assert backend._hook_handle is not None
        assert not backend._using_polling, "ESC backend should use the LL hook (not polling) when RegisterHotKey fails"
    finally:
        backend.stop()


def test_prefer_message_loop_with_failed_register_hotkey_and_failed_hook_uses_polling(mock_win32):
    """AB-35 last-resort fallback: if BOTH RegisterHotKey AND the LL"""
    mock_user32, _, _ = mock_win32
    mock_user32.RegisterHotKey.return_value = 0
    mock_user32.SetWindowsHookExW.return_value = 0  # hook install fails

    backend = _make_backend("<esc>", prefer_message_loop=True)
    try:
        backend.start(MagicMock())
        if backend._thread is not None:
            backend._thread.join(timeout=2.0)

        # LL hook install was attempted (and failed).
        assert mock_user32.SetWindowsHookExW.call_count == 1
        assert backend._hook_handle is None
        # Polling fallback engaged.
        assert backend._using_polling is True
    finally:
        backend.stop()


def test_caps_lock_hotkey_ignores_prefer_message_loop(mock_win32):
    """AB-35 edge case: Caps Lock hotkey needs the LL hook to swallow"""
    mock_user32, _, _ = mock_win32

    backend = _make_backend("<caps_lock>", prefer_message_loop=True)
    try:
        backend.start(MagicMock())
        if backend._thread is not None:
            backend._thread.join(timeout=2.0)

        # Caps Lock uses the LL hook regardless of prefer_message_loop
        assert mock_user32.SetWindowsHookExW.call_count == 1, (
            "Caps Lock must use LL hook even with prefer_message_loop=True "
            "(the LL hook is required to swallow the OS-level toggle)"
        )
        assert backend._hook_handle is not None
    finally:
        backend.stop()
