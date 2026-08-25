"""S2- regression test: ``_send_ctrl_v_win32`` returns bool, ``paste()`` returns True on success.

Bug (review.md S2-, severity High):
    ``_send_ctrl_v_win32`` was annotated ``-> None`` (never returned a
    bool), but the caller in ``paste()`` assigned its result and checked
    ``if not paste_succeeded:``. Since the function always returned
    ``None`` (falsy), the warning ALWAYS fired and ``paste()`` ALWAYS
    returned ``False`` on Windows — even when SendInput returned 4 (full
    success).

Fix (Option A from the proposed fix in review.md):
    ``_send_ctrl_v_win32`` is now annotated ``-> bool`` and returns:
      * ``True``  when SendInput returns 4   (full Ctrl+V sequence delivered)
      * ``True``  when SendInput returns 0 and the pynput fallback is invoked
        (best-effort success — pynput raises on failure)
      * ``False`` when SendInput returns 1..3 (partial success — paste did
        NOT complete cleanly; do not double-paste)

Files verified (post-fix):
  * ``voice_typer/server/clipboard/windows.py:218-350``  — implementation
  * ``voice_typer/server/clipboard/manager.py:1196-1218`` — wrapper
    (``ClipboardManager._send_ctrl_v_win32`` -> ``_cb._send_ctrl_v_win32``)
  * ``voice_typer/server/clipboard/manager.py:945-1015``  — caller in ``paste()``

Test layout:
  1. ``TestSendCtrlVWin32ReturnValue`` — cross-platform (runs on Linux CI).
     Uses the ``fake_win32`` fixture-style mocking pattern (mirrors
     ``tests/clipboard/win32/ (split files)``) to mock ``ctypes.windll``
     and exercises the real ``ClipboardManager._send_ctrl_v_win32`` /
     ``_cb._send_ctrl_v_win32`` call chain. Asserts the bool return value
     on full success (4), partial success (1..3), and zero+fallback paths.
  2. ``test_paste_returns_true_on_sendinput_full_success_win32`` — Windows-
     only sentinel (skipped on non-Windows). Mocks the REAL
     ``ctypes.windll.user32.SendInput`` attribute and asserts ``paste()``
     returns ``True`` without logging the spurious "Auto-paste failed
     (SendInput partial success)" warning. Validates the fix against the
     real Windows ctypes surface; cross-platform coverage is provided by
     the tests above (which use the same mocking pattern as the existing
     test suite).
"""

from __future__ import annotations

import ctypes
import sys
from unittest.mock import MagicMock, patch

import pytest

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402

# ─── Cross-platform fixture (mirrors fake_win32 in test_win32_copy_paste (fake_win32 fixture)) ──


@pytest.fixture
def fake_win32_for_return_value():
    """Mock ``ctypes.windll`` so the Win32 code path runs on Linux.

    Same shape as the ``fake_win32`` fixture in
    ``tests/clipboard/win32/ (split files)`` (lines 118-162), local to
    this file so we don't cross-import a private fixture.
    """
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.advapi32 = MagicMock()
    # Sane defaults for the few Win32 calls paste() may make.
    mock_user32.GetForegroundWindow.return_value = 0x12345
    mock_kernel32.GetLastError.return_value = 0
    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        # Pin is_macos() False too: manager._dispatch_keystroke checks
        # is_macos() BEFORE is_windows(), so on a macOS host the real
        # darwin predicate would otherwise route paste() into the
        # Cmd+V branch instead of the Win32 SendInput path under test.
        patch.object(clip_mod, "is_macos", return_value=False),
        patch("ctypes.windll", mock_windll, create=True),
    ):
        yield {"user32": mock_user32, "windll": mock_windll}


# ===========================================================================
# Cross-platform regression: _send_ctrl_v_win32 returns bool (not None)
# ===========================================================================


class TestSendCtrlVWin32ReturnValue:
    """S2-assert ``_send_ctrl_v_win32`` returns an explicit bool."""

    def _make_cm(self):
        """Delegate to the shared canonical factory (XS-42 helper dedup).

        ``_send_ctrl_v_win32`` only reads ``self._keyboard`` (via
        ``_safe_key_press``); the factory's other pre-populated cached
        flags are inert for this suite.
        """
        from tests.fixtures.clipboard_helpers import make_clipboard_manager

        return make_clipboard_manager()

    def test_returns_true_on_full_success(self, fake_win32_for_return_value):
        """S2-SendInput returning 4 → _send_ctrl_v_win32 returns True.

        Before the fix, the function returned ``None`` (falsy) — the
        caller's ``if not paste_succeeded:`` then ALWAYS fired the
        warning and ``paste()`` returned False, even on full success.
        """
        cm = self._make_cm()
        fake_win32_for_return_value["user32"].SendInput.return_value = 4
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            result = cm._send_ctrl_v_win32()
        assert result is True, (
            "S2- regression: _send_ctrl_v_win32() must return True when "
            f"SendInput returns 4 (full success); got {result!r}."
        )

    def test_returns_false_on_partial_success(self, fake_win32_for_return_value):
        """S2-SendInput returning 1..3 → _send_ctrl_v_win32 returns False.

        The function MUST return an explicit bool — never None — so the
        caller's ``if not paste_succeeded:`` branch fires correctly only
        on partial success (not on every call).
        """
        cm = self._make_cm()
        # First SendInput (4-event Ctrl+V batch) returns 2 (partial).
        # Second SendInput (2-event KEYUP cleanup) return value ignored.
        fake_win32_for_return_value["user32"].SendInput.side_effect = [2, 2]
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            result = cm._send_ctrl_v_win32()
        assert result is False, (
            "S2- regression: _send_ctrl_v_win32() must return False on "
            f"partial success (SendInput returned 1..3); got {result!r}."
        )

    def test_returns_true_on_zero_with_fallback(self, fake_win32_for_return_value):
        """S2-SendInput returning 0 → fallback invoked, return True.

        The function returns True (best-effort success) when the pynput
        fallback is invoked. This path must not return None.
        """
        cm = self._make_cm()
        fake_win32_for_return_value["user32"].SendInput.return_value = 0
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            result = cm._send_ctrl_v_win32()
        assert result is True, (
            "S2- regression: _send_ctrl_v_win32() must return True when "
            "the pynput fallback is invoked (SendInput returned 0); got "
            f"{result!r}."
        )


# ===========================================================================
# Cross-platform regression: paste() returns True on full success
# ===========================================================================


class TestPasteReturnsTrueOnFullSuccess:
    """S2-``paste()`` returns True (not False) on full success.

    Before the fix, ``paste()`` ALWAYS returned False on Windows because
    ``_send_ctrl_v_win32()`` returned ``None`` (falsy) →
    ``if not paste_succeeded:`` fired → log warning + return False.
    """

    def _make_cm(self):
        """Delegate to the shared canonical factory (XS-42 helper dedup),
        keeping this suite's save-restore-disabled / "test" sentinel
        arrangement."""
        from tests.fixtures.clipboard_helpers import make_clipboard_manager

        return make_clipboard_manager(save_restore=False, last_copied_text="test")

    def test_paste_returns_true_on_sendinput_full_success(self, fake_win32_for_return_value):
        """S2-paste() returns True when SendInput returns 4.

        After the fix, ``_send_ctrl_v_win32()`` returns True on full
        success → ``if not paste_succeeded:`` does NOT fire → ``paste()``
        returns True. NO spurious "Auto-paste failed (SendInput partial
        success)" warning should fire on full success.
        """
        cm = self._make_cm()
        fake_win32_for_return_value["user32"].SendInput.return_value = 4
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_Key") as mock_key,
            patch.object(clip_mod, "log") as mock_log,
            patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
            patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            mock_key.ctrl = "ctrl_key"
            result = cm.paste()
        assert result is True, (
            f"S2- regression: paste() must return True when SendInput returns 4 (full success); got {result!r}."
        )
        # specifically: NO spurious "Auto-paste failed" warning
        # should fire on full success.
        warning_msgs = [str(c) for c in mock_log.warning.call_args_list]
        spurious = [m for m in warning_msgs if "Auto-paste failed" in m and "partial success" in m]
        assert not spurious, (
            "S2- regression: paste() logged spurious 'Auto-paste failed' "
            "warning on full success (should only fire on partial success): "
            f"{spurious}"
        )

    def test_paste_returns_false_on_partial_success(self, fake_win32_for_return_value):
        """S2-paste() returns False on partial success (1..3).

        After the fix, ``_send_ctrl_v_win32()`` returns False on partial
        success → ``if not paste_succeeded:`` fires → log warning +
        return False. This is the CORRECT behavior (the warning should
        fire only on actual partial success, not on every call).
        """
        cm = self._make_cm()
        # First SendInput (4-event batch) returns 2 (partial); second
        # SendInput (2-event KEYUP cleanup) return value ignored.
        fake_win32_for_return_value["user32"].SendInput.side_effect = [2, 2]
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_Key") as mock_key,
            patch.object(clip_mod, "log") as mock_log,
            patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
            patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            mock_key.ctrl = "ctrl_key"
            result = cm.paste()
        assert result is False, (
            f"S2- regression: paste() must return False on partial success (SendInput returned 1..3); got {result!r}."
        )
        # The "Auto-paste failed (SendInput partial success)" warning
        # SHOULD fire on partial success (this is the correct behavior
        # after the fix — not a regression).
        warning_msgs = [str(c) for c in mock_log.warning.call_args_list]
        expected_warning = [m for m in warning_msgs if "Auto-paste failed" in m and "partial success" in m]
        assert expected_warning, (
            "S2-paste() should log 'Auto-paste failed (SendInput "
            "partial success)' warning on partial success; got warnings: "
            f"{warning_msgs}"
        )


# ===========================================================================
# Windows-only sentinel (skipped on non-Windows)
# ===========================================================================
# Per the  fix task instructions: a Windows-only sentinel test
# that mocks the REAL ``ctypes.windll.user32.SendInput`` attribute (only
# present on Windows) and verifies ``paste()`` returns True. This
# validates the fix against the real Windows ctypes surface; cross-
# platform coverage is provided by the
# ``fake_win32_for_return_value`` tests above.


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="S2- sentinel: requires real ctypes.windll.user32 (Windows-only).",
)
def test_paste_returns_true_on_sendinput_full_success_win32():
    """S2- Windows-only sentinel: paste() returns True on full success.

    Mocks the real ``ctypes.windll.user32.SendInput`` function pointer
    (only available on Windows) to return 4 — full Ctrl+V sequence
    delivered. Verifies ``ClipboardManager.paste()`` returns True and
    does NOT log the spurious "Auto-paste failed (SendInput partial
    success)" warning that the bug produced.

    Skipped on non-Windows. The cross-platform tests above (using the
    ``fake_win32_for_return_value`` fixture) provide Linux CI coverage
    of the same regression.
    """
    cm = ClipboardManager.__new__(ClipboardManager)
    cm.paste_enabled = True
    cm._keyboard = MagicMock()
    cm._last_paste_time = 0.0
    cm._clipboard_seq = 0
    cm._clipboard_save_restore_enabled = False
    cm._last_copied_text = "test"
    cm._restore_delay_ms = 150

    with (
        patch.object(clip_mod, "time") as mock_time,
        patch.object(clip_mod, "_Key") as mock_key,
        patch.object(clip_mod, "log") as mock_log,
        patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
        patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        patch.object(ctypes.windll.user32, "SendInput", MagicMock(return_value=4)),
    ):
        mock_time.monotonic.return_value = 100.0
        mock_time.sleep = MagicMock()
        mock_key.ctrl = "ctrl_key"
        result = cm.paste()

    assert result is True
    warning_msgs = [str(c) for c in mock_log.warning.call_args_list]
    spurious = [m for m in warning_msgs if "Auto-paste failed" in m and "partial success" in m]
    assert not spurious, f"S2- regression on Windows: paste() logged spurious warning: {spurious}"
