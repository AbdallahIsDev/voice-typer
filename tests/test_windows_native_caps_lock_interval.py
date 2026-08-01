"""AB-36 regression tests: WindowsNativeHotkey's periodic caps-lock
backup check must fire every ~200ms (25 iterations × 8ms sleep), NOT
every 1.6s (200 iterations × 8ms sleep).

Before AB-36, the check at ``_run_polling_loop`` was gated on
``_caps_check_iter % 200 == 0`` inside a loop that sleeps 8ms per
iteration. That gave 200 × 8ms = 1600ms = 1.6s between checks — an
8× discrepancy with the documented ~200ms cadence. The fix changes
the modulus to 25 (25 × 8ms = 200ms), matching the comments.

These tests mock ``Sleep`` so each iteration is instantaneous and
count iterations until ``_ensure_caps_lock_off`` is called by the
periodic backup check (NOT the per-press reactive suppression, which
goes through ``_suppress_caps_lock_toggle`` → ``keybd_event``).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def mock_win32(monkeypatch):
    """Provide mocked user32, kernel32, and winmm DLLs.

    Default: RegisterHotKey fails AND the low-level hook fails, forcing
    the backend onto the GetAsyncKeyState polling path (the path that
    contains the AB-36 bug).
    """
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
    """Run the polling loop for exactly ``n`` iterations, then exit.

    Each ``Sleep`` call increments a counter and stops the loop once
    we've reached the requested iteration count. The first iteration
    is iteration 1 (matching the ``_caps_check_iter += 1`` increment
    inside the loop).
    """
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
    """AB-36: with ``% 25`` (fixed), the periodic caps-lock backup
    check fires by iteration 25. With the old ``% 200`` (buggy), it
    would NOT fire within 30 iterations (would need 200 iterations).

    Asserts: at least ONE periodic call (beyond the 2 proactive calls
    from start() and _run_polling_loop) fires within 30 iterations.
    """
    mock_user32, mock_kernel32, _ = mock_win32
    from voice_typer.server.hotkeys import WindowsNativeHotkey

    backend = WindowsNativeHotkey("<caps_lock>")
    # Force the polling path (RegisterHotKey + LL hook both fail).
    mock_user32.RegisterHotKey.return_value = 0
    mock_user32.SetWindowsHookExW.return_value = 0

    # Key never pressed — we're testing the PERIODIC backup check, not
    # the per-press reactive suppression. Return 0 from GetAsyncKeyState
    # so the loop's reactive ``_suppress_caps_lock_toggle`` never fires.
    mock_user32.GetAsyncKeyState.return_value = 0

    # Patch _ensure_caps_lock_off so we can count calls (the real one
    # makes Win32 syscalls we don't want to mock in detail).
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

    # fixed behavior: the periodic check fires at iteration 25.
    # There are 2 proactive calls (one from start(), one from
    # _run_polling_loop's registration-time check) PLUS the periodic
    # call at iteration 25. So with the fix (% 25) we get ≥3 calls in
    # 30 iterations. With the old bug (% 200) we'd get exactly 2 (no
    # periodic — iteration 200 is never reached).
    assert len(ensure_calls) >= 3, (
        f"Periodic caps-lock check should fire at iteration 25 (AB-36: % 25); "
        f"expected ≥3 _ensure_caps_lock_off calls (2 proactive + ≥1 periodic) "
        f"in 30 iterations, got {len(ensure_calls)}. With the old % 200, the "
        f"periodic check would NOT fire within 30 iterations (would get 2)."
    )


def test_caps_lock_check_does_not_fire_before_iteration_25(mock_win32, monkeypatch):
    """AB-36 negative: in 24 iterations, the periodic check must NOT
    fire (it fires at iteration 25). This catches a regression where
    the modulus is too small (e.g. ``% 5`` would fire at iterations
    5, 10, 15, 20 — 4 periodic calls in 24 iterations).

    With the fix (``% 25``) and 24 iterations: 2 proactive calls, 0
    periodic calls (iteration 25 not reached) → 2 total.
    With the old bug (``% 200``) and 24 iterations: same (2 total)
    — this test alone doesn't distinguish, but combined with
    ``test_caps_lock_check_fires_within_30_iterations`` it pins the
    modulus at exactly 25.
    """
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

    # 2 proactive calls only (no periodic — iteration 25 not reached).
    # If the modulus were too small (e.g. % 5), we'd see > 2 calls.
    assert len(ensure_calls) == 2, (
        f"Periodic caps-lock check must NOT fire before iteration 25 "
        f"(AB-36: % 25). Expected 2 calls (2 proactive, 0 periodic) in 24 "
        f"iterations; got {len(ensure_calls)}. If > 2, the modulus is too "
        f"small (e.g. % 5 would fire 4 times in 24 iterations)."
    )


def test_modulus_source_code_uses_25_not_200():
    """AB-36 source-level pin: the polling loop must use ``% 25`` (200ms
    cadence at 8ms/iter), not ``% 200`` (1.6s — the bug). This catches
    a future revert even if the behavior tests above are flaky."""
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
        "Found `_caps_check_iter % 200 == 0` in source — revert to `% 25`."
    )
