"""Win32 code-path coverage for ``voice_typer.server.clipboard``.

These tests mock ``ctypes.windll`` (and friends) so the Windows-only
branches of clipboard.py execute on Linux.  Brings clipboard.py from
~40% to >=75% coverage.

The strategy:

1. Patch ``voice_typer.server.clipboard.is_windows`` to return ``True``
   so the ``if not is_windows(): return ...`` early-exits are skipped.
2. Patch ``ctypes.windll`` with a ``MagicMock`` exposing ``user32``,
   ``kernel32``, and ``advapi32`` attributes.  Each Win32 API call
   becomes a mock call whose return value we control.
3. For functions that use ``ctypes.byref(dword)`` to receive an output
   value (e.g. ``GetWindowThreadProcessId``), we install ``side_effect``
   callbacks that mutate ``byref_obj._obj.value`` — the underlying
   ``c_ulong`` instance — to fake the kernel writing into the buffer.
4. For ``_send_ctrl_v_win32``, we provide *real* ``ctypes.Structure``
   subclasses (``INPUT``, ``KEYBDINPUT``, ``INPUT_union``) so the
   ``(INPUT * 4)(...)`` array-construction syntax and
   ``ctypes.sizeof(INPUT)`` work natively.  ``SendInput`` itself is a
   ``MagicMock``.
5. For ``_is_password_field`` and ``_is_content_editable``, we mock
   ``comtypes`` / ``comtypes.client`` in ``sys.modules`` so the
   ``import comtypes.client`` line resolves to our mock.
"""

from __future__ import annotations

import ctypes
import sys
import types
from ctypes import wintypes
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
# ---------------------------------------------------------------------------
# UIA singleton moved to clipboard_target_safety; reset it there.
from voice_typer.server import (
    clipboard as clip_mod,  # noqa: E402
)
from voice_typer.server.clipboard import (  # noqa: E402
    ClipboardCopyError,
    ClipboardManager,
)
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402


# ---------------------------------------------------------------------------
# Real ctypes structures for _send_ctrl_v_win32 testing.
#
# pynput._util.win32 exposes INPUT / KEYBDINPUT / INPUT_union / SendInput.
# We define minimal ctypes-compatible versions so the array-construction
# and sizeof() calls in _send_ctrl_v_win32 work on Linux.
# ---------------------------------------------------------------------------
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        # ULONG_PTR — accepts int 0
        ("dwExtraInfo", wintypes.WPARAM),
    )
    KEYUP = 0x0002


class _InputUnion(ctypes.Union):
    _fields_ = (("ki", _KEYBDINPUT),)


class _INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("ii", _InputUnion),
    )
    KEYBOARD = 1


def _make_pynput_win32_module(sendinput_return: int = 4) -> types.ModuleType:
    """Build a fake ``pynput._util.win32`` module with real ctypes types.

    ``SendInput`` is a MagicMock so the test can configure the return
    value (4 = success, 0 = total failure, 1..3 = partial success).
    """
    mod = types.ModuleType("pynput._util.win32")
    mod.INPUT = _INPUT
    mod.KEYBDINPUT = _KEYBDINPUT
    mod.INPUT_union = _InputUnion
    mod.SendInput = MagicMock(return_value=sendinput_return)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_win32():
    """Mock ``ctypes.windll`` so Windows-only code runs on Linux.

    Yields a dict with ``user32``, ``kernel32``, and ``advapi32`` mocks
    that tests can configure per-case.
    """
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_advapi32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.advapi32 = mock_advapi32

    # Default, sane return values for the most common calls.
    mock_user32.OpenClipboard.return_value = 1  # success
    mock_user32.CloseClipboard.return_value = 1
    mock_user32.EmptyClipboard.return_value = 1
    mock_user32.GetClipboardSequenceNumber.return_value = 42
    mock_user32.GetForegroundWindow.return_value = 0x12345
    mock_user32.GetClassNameW.return_value = 0  # filled per-test
    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.GetCurrentProcess.return_value = 0xABCD
    mock_kernel32.OpenProcess.return_value = 0x1000  # non-zero handle
    mock_kernel32.CloseHandle.return_value = 1
    mock_advapi32.OpenProcessToken.return_value = 1  # success
    mock_advapi32.GetTokenInformation.return_value = 1  # success

    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        # Pin is_macos() False too: the dispatch in manager.py checks
        # is_macos() BEFORE is_windows(), so on a macOS host the real
        # darwin predicate would otherwise hijack these simulated-
        # Windows tests into the Cmd+V branch.
        patch.object(clip_mod, "is_macos", return_value=False),
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.create_unicode_buffer") as mock_buf,
    ):
        # Default buffer returns "Edit" — a benign window class.
        buf_instance = MagicMock()
        buf_instance.value = "Edit"
        mock_buf.return_value = buf_instance
        yield {
            "user32": mock_user32,
            "kernel32": mock_kernel32,
            "advapi32": mock_advapi32,
            "windll": mock_windll,
            "buf": mock_buf,
        }


def _set_byref_value(byref_obj, value):
    """Helper: mutate the c_ulong instance wrapped by ``ctypes.byref``.

    ``ctypes.byref(obj)`` returns a ``CArgObject`` whose ``_obj``
    attribute is the underlying object.  We use this to fake the kernel
    writing an output value into a ``wintypes.DWORD`` passed by-ref.
    """
    byref_obj._obj.value = value


class TestCopyWindowsBranches:
    def _make_cm(self):
        """Build a ClipboardManager with mocked pynput controller.

        ADR-0010 §5.6: ``_clear_thread`` / ``_saved_clipboard`` were
        deleted from the class. We set ``_restore_delay_ms`` (new in
        §5.3) so ``paste()``'s restore-delay lookup doesn't blow up if
        a test triggers the restore path.
        """
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0
        cm._clipboard_seq = 0
        cm._last_copied_text = ""
        cm._clipboard_save_restore_enabled = True
        cm._restore_delay_ms = 150
        return cm

    def test_copy_saves_clipboard_before_overwrite_on_windows(self, fake_win32):
        """copy() captures a ClipboardSnapshot of the prior clipboard.

        ADR-0010 §5.2: ``copy()`` now returns a ``ClipboardSnapshot``
        (or ``None``) instead of ``bool``. The snapshot is captured via
        ``ClipboardSnapshot.capture()`` and returned to the caller — it
        is NOT stored on ``self``.
        """
        cm = self._make_cm()
        sentinel_snapshot = ClipboardSnapshot(
            platform="windows",
            items=[(1, "CF_TEXT", b"old clipboard")],
            captured_at=0.0,
        )
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"  # verification match
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=sentinel_snapshot,
            ) as mock_capture,
        ):
            result = cm.copy("new text")
        assert result is sentinel_snapshot
        mock_capture.assert_called_once()

    def test_copy_handles_pyperclip_paste_exception_during_save(self, fake_win32):
        """If ClipboardSnapshot.capture() returns None, copy() still succeeds.

        ADR-0010 §5.2: snapshot capture may return None (clipboard
        locked or empty). copy() treats None as "no snapshot to
        restore" — degraded but safe mode — and still returns None
        (the snapshot value) while succeeding the actual text copy.
        """
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=None,
            ),
        ):
            result = cm.copy("new text")
        assert result is None  # capture failed → no snapshot returned

    def test_copy_skips_save_when_save_restore_disabled(self, fake_win32):
        """When _clipboard_save_restore_enabled=False, no snapshot is captured.

        ADR-0010 §5.2 / DP7: the config flag actually gates snapshot
        capture. ``copy()`` returns ``None`` because no snapshot was
        captured — the text copy itself still succeeds.
        """
        cm = self._make_cm()
        cm._clipboard_save_restore_enabled = False
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=None,
            ) as mock_capture,
        ):
            result = cm.copy("new text")
        assert result is None  # save_restore disabled → no snapshot
        # Snapshot capture must NOT be attempted when the flag is off.
        mock_capture.assert_not_called()
        # paste() called only for verification, not for save
        assert mock_pyper.paste.call_count >= 1

    def test_copy_retries_on_access_denied(self, fake_win32):
        """pyperclip.copy raising OSError(winerror=5) is retried 3 times.

        ADR-0010 §5.2: after the third failure, copy() raises
        ``ClipboardCopyError`` (instead of returning ``False``).
        """
        cm = self._make_cm()
        mock_pyper = MagicMock()
        # First two copy() calls raise ACCESS_DENIED, third succeeds.
        copy_err = OSError("denied")
        copy_err.winerror = 5
        mock_pyper.copy.side_effect = [copy_err, copy_err, None]
        # paste() during verification returns the text → success.
        mock_pyper.paste.return_value = "hello"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time"),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
        ):
            result = cm.copy("hello")
        # copy() succeeded → returns the snapshot (None because capture was mocked None).
        assert result is None
        assert mock_pyper.copy.call_count == 3

    def test_copy_raises_after_three_access_denied(self, fake_win32):
        """After 3 ACCESS_DENIED attempts, copy() raises ClipboardCopyError."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        copy_err = OSError("denied")
        copy_err.winerror = 5
        mock_pyper.copy.side_effect = copy_err  # always raises
        mock_pyper.paste.return_value = "hello"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time"),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")
        assert mock_pyper.copy.call_count == 3

    def test_copy_propagates_non_access_denied_oserror(self, fake_win32):
        """OSError without winerror=5 propagates as ClipboardCopyError."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = OSError("other error")
        mock_pyper.paste.return_value = "hello"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")
        # Only the first attempt — no retry.
        assert mock_pyper.copy.call_count == 1

    def test_copy_verification_retries_on_mismatch(self, fake_win32):
        """pyperclip.paste() returning wrong text triggers verify-retry."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.return_value = None
        # All 3 paste() calls return mismatched text → loop runs to end.
        mock_pyper.paste.return_value = "wrong"
        sentinel = ClipboardSnapshot(platform="windows", items=[], captured_at=0.0)
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log") as mock_log,
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            result = cm.copy("expected")
        assert result is sentinel  # copy returns the snapshot (best-effort)
        # Verify logged the verification-failed warning.
        warning_calls = [c for c in mock_log.warning.call_args_list if "verification" in str(c).lower()]
        assert len(warning_calls) >= 1
        # Final error log after 3 retries fail.
        error_calls = [c for c in mock_log.error.call_args_list if "verification" in str(c).lower()]
        assert len(error_calls) >= 1

    def test_copy_verification_succeeds_after_one_mismatch(self, fake_win32):
        """First paste() mismatches, second matches → break out of verify loop."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.return_value = None
        mock_pyper.paste.side_effect = ["wrong", "expected"]
        sentinel = ClipboardSnapshot(platform="windows", items=[], captured_at=0.0)
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            result = cm.copy("expected")
        assert result is sentinel

    def test_copy_verification_swallows_paste_exception(self, fake_win32):
        """pyperclip.paste() raising during verification is swallowed."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.return_value = None
        # All paste() calls during verification raise → swallowed.
        mock_pyper.paste.side_effect = [OSError("boom"), OSError("boom"), OSError("boom")]
        sentinel = ClipboardSnapshot(platform="windows", items=[], captured_at=0.0)
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            result = cm.copy("expected")
        assert result is sentinel


# ===========================================================================
# ClipboardManager.paste — Windows branches
# ===========================================================================


class TestPasteWindowsBranches:
    def _make_cm(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0  # not rate-limited
        cm._clipboard_seq = 0
        cm._clipboard_save_restore_enabled = False
        cm._last_copied_text = "test"
        cm._restore_delay_ms = 150
        return cm

    def test_paste_recopies_when_seq_changes(self, fake_win32):
        """If clipboard seq changed between copy and paste, re-copy."""
        cm = self._make_cm()
        cm._clipboard_seq = 10  # was 10 at copy time
        # _get_clipboard_sequence_number returns a different value now.
        mock_pyper = MagicMock()
        with patch.object(clip_mod, "pyperclip", mock_pyper), patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(
                    ClipboardManager,
                    "_get_clipboard_sequence_number",
                    return_value=99,
                ),
                patch.object(
                    ClipboardManager,
                    "_is_safe_paste_target",
                    return_value=True,
                ),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
            ):
                result = cm.paste()
        assert result is True
        # Re-copied _last_copied_text ("test")
        mock_pyper.copy.assert_any_call("test")
        mock_send.assert_called_once()

    def test_paste_logs_error_when_recopy_fails(self, fake_win32):
        """If re-copy raises, error is logged and paste continues."""
        cm = self._make_cm()
        cm._clipboard_seq = 10
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = OSError("re-copy failed")
        with patch.object(clip_mod, "pyperclip", mock_pyper), patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(
                    ClipboardManager,
                    "_get_clipboard_sequence_number",
                    return_value=99,
                ),
                patch.object(
                    ClipboardManager,
                    "_is_safe_paste_target",
                    return_value=True,
                ),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is True
        # An error was logged for the re-copy failure.
        error_calls = [
            c for c in mock_log.error.call_args_list if "re-copy" in str(c).lower() or "Failed to re-copy" in str(c)
        ]
        assert len(error_calls) >= 1

    def test_paste_skips_when_seq_zero(self, fake_win32):
        """When _clipboard_seq is 0, seq-check branch is skipped."""
        cm = self._make_cm()
        cm._clipboard_seq = 0
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
                patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
            ):
                result = cm.paste()
        assert result is True
        mock_send.assert_called_once()

    def test_paste_uses_send_ctrl_v_win32_on_windows(self, fake_win32):
        """On Windows with a non-terminal target, _send_ctrl_v_win32 is called."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value="notepad.exe",
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
            ):
                result = cm.paste()
        assert result is True
        mock_send.assert_called_once()

    def test_paste_uses_shift_insert_for_terminal_on_windows(self, fake_win32):
        """For terminal processes on Windows, Shift+Insert is used."""
        cm = self._make_cm()
        # _Key must be set for _safe_key_press to work.
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.shift = "shift_key"
            mock_key.insert = "insert_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                with (
                    patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="cmd.exe",
                    ),
                    patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
                ):
                    result = cm.paste()
        assert result is True
        # _send_ctrl_v_win32 NOT called — terminal path used instead.
        mock_send.assert_not_called()
        # Shift+Insert pressed via _safe_key_press.
        cm._keyboard.press.assert_any_call("shift_key")
        cm._keyboard.press.assert_any_call("insert_key")

    def test_paste_uses_cmd_v_for_terminal_on_macos(self, fake_win32):
        """For terminal processes on macOS, Cmd+V is used (line 879)."""
        cm = self._make_cm()
        # ADR-0010 §5.3: production paste() now checks ``_Controller is None``
        # (was ``_Key is None``). Patch both so the early-return guard
        # doesn't fire on the macOS branch (which runs is_windows=False).
        with patch.object(clip_mod, "_Key") as mock_key, patch.object(clip_mod, "_Controller", MagicMock()):
            mock_key.cmd = "cmd_key"
            mock_key.shift = "shift_key"
            mock_key.insert = "insert_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                # Override the is_windows=True from fake_win32 with
                # is_macos=True so the macOS branch is taken.  We keep
                # is_windows=False to avoid the Windows seq-check path.
                with (
                    patch.object(clip_mod, "is_windows", return_value=False),
                    patch.object(clip_mod, "is_macos", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_is_safe_paste_target",
                        return_value=True,
                    ),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="cmd.exe",
                    ),
                ):
                    result = cm.paste()
        assert result is True
        # Cmd+V pressed via _safe_key_press.
        cm._keyboard.press.assert_any_call("cmd_key")
        cm._keyboard.press.assert_any_call("v")

    def test_paste_uses_cmd_v_for_non_terminal_on_macos(self, fake_win32):
        """For non-terminal processes on macOS, Cmd+V is used (line 883)."""
        cm = self._make_cm()
        # ADR-0010 §5.3: patch ``_Controller`` too (see above).
        with patch.object(clip_mod, "_Key") as mock_key, patch.object(clip_mod, "_Controller", MagicMock()):
            mock_key.cmd = "cmd_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                with (
                    patch.object(clip_mod, "is_windows", return_value=False),
                    patch.object(clip_mod, "is_macos", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_is_safe_paste_target",
                        return_value=True,
                    ),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="notepad.exe",
                    ),
                ):
                    result = cm.paste()
        assert result is True
        cm._keyboard.press.assert_any_call("cmd_key")
        cm._keyboard.press.assert_any_call("v")

    def test_paste_aborts_on_macos_toctou_pid_change(self, fake_win32):
        """macOS paste aborts if frontmost app PID changes.

        When the frontmost app PID captured right after the safety
        check differs from the PID captured right before the Cmd+V
        keystroke, paste() must return False and NOT send the
        keystroke (TOCTOU defense: user Cmd-Tabbed to a credential
        prompt in the ~5ms between the safety check and the send).
        """
        cm = self._make_cm()
        with patch.object(clip_mod, "_Key") as mock_key, patch.object(clip_mod, "_Controller", MagicMock()):
            mock_key.cmd = "cmd_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                with (
                    patch.object(clip_mod, "is_windows", return_value=False),
                    patch.object(clip_mod, "is_macos", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_is_safe_paste_target",
                        return_value=True,
                    ),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="notepad.exe",
                    ),
                    patch.object(
                        ClipboardManager,
                        "_get_frontmost_pid_macos",
                        side_effect=[4242, 9999],
                    ),
                ):
                    result = cm.paste()
        assert result is False
        assert not any("cmd_key" in str(c) for c in cm._keyboard.press.call_args_list)

    def test_paste_proceeds_when_macos_pid_unavailable(self, fake_win32):
        """macOS paste proceeds (fail-open) when PID unavailable."""
        cm = self._make_cm()
        with patch.object(clip_mod, "_Key") as mock_key, patch.object(clip_mod, "_Controller", MagicMock()):
            mock_key.cmd = "cmd_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                with (
                    patch.object(clip_mod, "is_windows", return_value=False),
                    patch.object(clip_mod, "is_macos", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_is_safe_paste_target",
                        return_value=True,
                    ),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="notepad.exe",
                    ),
                    patch.object(
                        ClipboardManager,
                        "_get_frontmost_pid_macos",
                        return_value=None,
                    ),
                ):
                    result = cm.paste()
        assert result is True
        cm._keyboard.press.assert_any_call("cmd_key")

    def test_paste_logs_rdp_session(self, fake_win32):
        """When is_remote_session() returns True, paste_delay is increased."""
        cm = self._make_cm()
        # Build a fake server_platform.is_remote_session that returns True.
        fake_platform = MagicMock()
        fake_platform.is_remote_session.return_value = True
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.dict(
                    sys.modules,
                    {"voice_typer.server.server_platform": fake_platform},
                ),
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is True
        # Verify RDP info log was emitted.
        info_calls = [c for c in mock_log.info.call_args_list if "RDP" in str(c)]
        assert len(info_calls) >= 1

    def test_paste_logs_rdp_check_exception(self, fake_win32):
        """If is_remote_session import fails, paste continues with default delay."""
        cm = self._make_cm()
        # Make the server_platform module raise on attribute access.
        fake_platform = MagicMock()
        type(fake_platform).is_remote_session = PropertyMock(side_effect=RuntimeError("boom"))
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.dict(
                    sys.modules,
                    {"voice_typer.server.server_platform": fake_platform},
                ),
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
            ):
                result = cm.paste()
        assert result is True

    def test_paste_returns_false_when_unsafe_target(self, fake_win32):
        """_is_safe_paste_target returning False → paste returns False."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=False),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is False
        info_calls = [
            c for c in mock_log.info.call_args_list if "security-sensitive" in str(c) or "Paste blocked" in str(c)
        ]
        assert len(info_calls) >= 1

    def test_paste_logs_rich_editor_info(self, fake_win32):
        """Pasting into a known rich editor logs an info message."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value="winword.exe",
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is True
        rich_calls = [c for c in mock_log.info.call_args_list if "rich editor" in str(c).lower()]
        assert len(rich_calls) >= 1

    def test_paste_returns_false_on_send_ctrl_v_exception(self, fake_win32):
        """If _send_ctrl_v_win32 raises, paste() catches and returns False."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(
                    ClipboardManager,
                    "_send_ctrl_v_win32",
                    side_effect=RuntimeError("sendinput broken"),
                ),
                patch.object(clip_mod, "log"),
            ):
                result = cm.paste()
        assert result is False


# ===========================================================================
# ClipboardManager._send_ctrl_v_win32
# ===========================================================================
