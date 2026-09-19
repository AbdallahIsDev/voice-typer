"""AB-36 regression tests: WindowsNativeHotkey's periodic caps-lock"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def mock_win32(monkeypatch):
    """Provide mocked user32, kernel32, and winmm DLLs."""
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_winmm = MagicMock()

    mock_user32.RegisterHotKey.return_value = 0  # RegisterHotKey fails
    mock_user32.UnregisterHotKey.return_value = 1
    mock_user32.PostThreadMessageW.return_value = 1
    mock_user32.GetAsyncKeyState.return_value = 0  # key not pressed
    mock_user32.SetWindowsHookExW.return_value = 0  # hook install fails

    mock_kernel32.GetLastError.return_value = 1409
    mock_kernel32.Sleep = MagicMock()

    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.winmm = mock_winmm
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)

    return mock_user32, mock_kernel32, mock_winmm


def _drive_n_iterations(backend, mock_kernel32, n):
    """Run the polling loop for exactly ``n`` iterations, then exit."""
    counter = {"i": 0}

    def _sleep(_ms):
        counter["i"] += 1
        if counter["i"] >= n:
            backend._stop_event.is_set.return_value = True

    mock_kernel32.Sleep.side_effect = _sleep
    backend._stop_event = MagicMock()
    backend._stop_event.is_set.return_value = False
    return counter


def test_caps_lock_check_fires_within_30_iterations(mock_win32, monkeypatch):
    """AB-36: with ``% 25`` (fixed), the periodic caps-lock backup"""
    mock_user32, mock_kernel32, _ = mock_win32
    from voice_typer.server.hotkeys import WindowsNativeHotkey

    backend = WindowsNativeHotkey("<caps_lock>")
    # Force the polling path (RegisterHotKey + LL hook both fail).
    mock_user32.RegisterHotKey.return_value = 0
    mock_user32.SetWindowsHookExW.return_value = 0

    # Key never pressed, we're testing the PERIODIC backup check, not
    mock_user32.GetAsyncKeyState.return_value = 0

    # Patch _ensure_caps_lock_off so we can count calls (the real one
    ensure_calls: list[int] = []
    orig_ensure = backend._ensure_caps_lock_off
    backend._ensure_caps_lock_off = lambda: ensure_calls.append(1)  # type: ignore[assignment]

    # Drive 30 iterations (past the 25-iteration mark, well short of 200).
    _drive_n_iterations(backend, mock_kernel32, 30)
    try:
        backend.start(MagicMock())
        if backend._thread is not None:
            backend._thread.join(timeout=2.0)
    finally:
        backend._ensure_caps_lock_off = orig_ensure  # type: ignore[assignment]
        backend.stop()

    assert len(ensure_calls) >= 3, (
        f"Periodic caps-lock check should fire at iteration 25 (AB-36: % 25); "
        f"expected ≥3 _ensure_caps_lock_off calls (2 proactive + ≥1 periodic) "
        f"in 30 iterations, got {len(ensure_calls)}. With the old % 200, the "
        f"periodic check would NOT fire within 30 iterations (would get 2)."
    )


def test_caps_lock_check_does_not_fire_before_iteration_25(mock_win32, monkeypatch):
    """AB-36 negative: in 24 iterations, the periodic check must NOT"""
    mock_user32, mock_kernel32, _ = mock_win32
    from voice_typer.server.hotkeys import WindowsNativeHotkey

    backend = WindowsNativeHotkey("<caps_lock>")
    mock_user32.RegisterHotKey.return_value = 0
    mock_user32.SetWindowsHookExW.return_value = 0
    mock_user32.GetAsyncKeyState.return_value = 0

    ensure_calls: list[int] = []
    orig_ensure = backend._ensure_caps_lock_off
    backend._ensure_caps_lock_off = lambda: ensure_calls.append(1)  # type: ignore[assignment]

    # Drive 24 iterations (just BEFORE the 25-iteration mark).
    _drive_n_iterations(backend, mock_kernel32, 24)
    try:
        backend.start(MagicMock())
        if backend._thread is not None:
            backend._thread.join(timeout=2.0)
    finally:
        backend._ensure_caps_lock_off = orig_ensure  # type: ignore[assignment]
        backend.stop()

    # 2 proactive calls only (no periodic, iteration 25 not reached).
    assert len(ensure_calls) == 2, (
        f"Periodic caps-lock check must NOT fire before iteration 25 "
        f"(AB-36: % 25). Expected 2 calls (2 proactive, 0 periodic) in 24 "
        f"iterations; got {len(ensure_calls)}. If > 2, the modulus is too "
        f"small (e.g. % 5 would fire 4 times in 24 iterations)."
    )


def test_modulus_source_code_uses_25_not_200():
    """AB-36 source-level pin: the polling loop must use ``% 25`` (200ms"""
    import inspect

    from voice_typer.server.hotkeys import windows_native

    source = inspect.getsource(windows_native.WindowsNativeHotkey._run_polling_loop)
    # The periodic check must use % 25.
    assert "_caps_check_iter % 25 == 0" in source, (
        "AB-36 regression: polling loop must use `% 25` (200ms cadence at "
        "8ms/iter), not `% 200` (1.6s). Found source did not contain "
        "`_caps_check_iter % 25 == 0`."
    )
    # The buggy modulus must NOT be present.
    assert "_caps_check_iter % 200 == 0" not in source, (
        "AB-36 regression: polling loop must NOT use `% 200` (1.6s cadence). "
        "Found `_caps_check_iter % 200 == 0` in source, revert to `% 25`."
    )
