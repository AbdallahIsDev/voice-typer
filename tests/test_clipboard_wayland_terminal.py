"""Tests for the clipboard injection fixes.

Covers the Wayland terminal-paste key sequence, the new terminal
process names, the Win32 SendInput Shift+Insert helper, the Win32
clipboard-monitor exclusion tag, and the macOS Secure Input detection
helper. All tests are cross-platform (they mock ``ctypes.windll`` /
``subprocess.run`` so they run on Linux CI) — the patterns mirror
``tests/test_clipboard_win32_return_value.py`` and
``tests/test_clipboard.py``.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402
from voice_typer.server.clipboard_target_safety import (  # noqa: E402
    _is_secure_input_enabled,
)

# ─── new terminal process names ────────────────────────────


class TestNewTerminalProcessNames:
    """Verify the new terminal names added to ``_TERMINAL_PROCESS_NAMES``."""

    @pytest.mark.parametrize(
        "name",
        [
            "gnome-terminal-server",
            "ptyxis",
            "ptyxis-agent",
            "blackbox",
            "tabby",
            "cosmic-term",
        ],
    )
    def test_new_terminal_name_recognized(self, name: str):
        """Each new terminal name must be in ``_TERMINAL_PROCESS_NAMES``."""
        assert name in clip_mod._TERMINAL_PROCESS_NAMES, (
            f" regression: {name!r} must be in _TERMINAL_PROCESS_NAMES"
        )

    def test_existing_terminal_names_preserved(self):
        """Existing entries must not be removed by the new additions."""
        for name in ("gnome-terminal", "konsole", "kitty", "cmd.exe", "pwsh.exe"):
            assert name in clip_mod._TERMINAL_PROCESS_NAMES

    def test_is_terminal_process_handles_new_names(self):
        """``ClipboardManager._is_terminal_process`` accepts new names."""
        for name in ("gnome-terminal-server", "ptyxis", "cosmic-term"):
            assert ClipboardManager._is_terminal_process(name) is True, (
                f"_is_terminal_process({name!r}) must return True"
            )

    def test_is_terminal_process_rejects_non_terminal(self):
        assert ClipboardManager._is_terminal_process("notepad.exe") is False
        assert ClipboardManager._is_terminal_process("firefox") is False


# ───  + Wayland terminal paste key sequence ──────────


class TestWaylandTerminalPaste:
    """Verify ``_linux_paste_via_wtype`` sends the right keystroke."""

    def _fake_proc(self, returncode: int = 0, stderr: bytes = b"") -> MagicMock:
        proc = MagicMock()
        proc.returncode = returncode
        proc.stderr = stderr
        return proc

    def test_terminal_paste_sends_ctrl_shift_v(self, monkeypatch):
        """``is_terminal=True`` → wtype gets ``ctrl+shift+v``."""
        captured: list[list[str]] = []

        def fake_run(cmd, **kw):
            captured.append(list(cmd))
            return self._fake_proc()

        monkeypatch.setattr(clip_mod.subprocess, "run", fake_run)
        # Patch ``_cb.time.sleep`` so the settle delay is a no-op.
        monkeypatch.setattr(clip_mod.time, "sleep", lambda *_a, **_kw: None)

        clip_mod._linux_paste_via_wtype("hello", is_terminal=True)

        assert len(captured) == 1, f"expected one wtype call, got {captured}"
        cmd = captured[0]
        assert cmd[:3] == ["wtype", "-k", "ctrl+shift+v"], (
            f"terminal paste must send 'ctrl+shift+v'; got {cmd!r}"
        )

    def test_non_terminal_paste_sends_ctrl_v(self, monkeypatch):
        """default ``is_terminal=False`` → wtype gets ``ctrl+v``."""
        captured: list[list[str]] = []

        def fake_run(cmd, **kw):
            captured.append(list(cmd))
            return self._fake_proc()

        monkeypatch.setattr(clip_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(clip_mod.time, "sleep", lambda *_a, **_kw: None)

        clip_mod._linux_paste_via_wtype("hello")

        assert len(captured) == 1
        cmd = captured[0]
        assert cmd[:3] == ["wtype", "-k", "ctrl+v"], (
            f"non-terminal paste must send 'ctrl+v'; got {cmd!r}"
        )

    def test_settle_delay_invoked_before_wtype(self, monkeypatch):
        """``time.sleep`` is called before the wtype subprocess."""
        sleep_calls: list[float] = []
        run_order: list[str] = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            run_order.append("sleep")

        def fake_run(cmd, **kw):
            run_order.append("run")
            return self._fake_proc()

        monkeypatch.setattr(clip_mod.time, "sleep", fake_sleep)
        monkeypatch.setattr(clip_mod.subprocess, "run", fake_run)

        clip_mod._linux_paste_via_wtype("hello", is_terminal=True)

        # The settle delay must run BEFORE the wtype subprocess.
        assert run_order == ["sleep", "run"], (
            f"sleep must precede wtype; got order={run_order!r}"
        )
        # 10-20 ms range per the finding.
        assert 0.010 <= sleep_calls[0] <= 0.020, (
            f"settle delay must be 10-20 ms; got {sleep_calls[0]!r}"
        )

    def test_paste_raises_on_wtype_nonzero_exit(self, monkeypatch):
        """Non-zero wtype exit raises RuntimeError."""
        monkeypatch.setattr(
            clip_mod.subprocess,
            "run",
            lambda *a, **kw: self._fake_proc(returncode=2, stderr=b"connection refused"),
        )
        monkeypatch.setattr(clip_mod.time, "sleep", lambda *_a, **_kw: None)

        with pytest.raises(RuntimeError, match="wtype exited with 2"):
            clip_mod._linux_paste_via_wtype("hello")

    def test_paste_propagates_timeout_expired(self, monkeypatch):
        """``subprocess.TimeoutExpired`` is re-raised, not swallowed."""
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

        monkeypatch.setattr(clip_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(clip_mod.time, "sleep", lambda *_a, **_kw: None)

        with pytest.raises(subprocess.TimeoutExpired):
            clip_mod._linux_paste_via_wtype("hello")


# ─── Win32 SendInput Shift+Insert helper ───────────────────


@pytest.fixture
def fake_win32_shift_insert():
    """Mock ``ctypes.windll`` so the Win32 code path runs on Linux.

    Same shape as the ``fake_win32`` fixture in
    ``tests/test_clipboard_win32_return_value.py`` (lines 63-84),
    local to this file so we don't cross-import a private fixture.
    """
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    # Sane defaults.
    mock_user32.GetForegroundWindow.return_value = 0x12345
    mock_kernel32.GetLastError.return_value = 0
    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        patch("ctypes.windll", mock_windll, create=True),
    ):
        yield {"user32": mock_user32, "windll": mock_windll, "kernel32": mock_kernel32}


class TestSendShiftInsertWin32:
    """``_send_shift_insert_win32`` mirrors ``_send_ctrl_v_win32``."""

    def test_returns_true_on_full_success(self, fake_win32_shift_insert):
        """SendInput returning 4 → ``_send_shift_insert_win32`` returns True."""
        fake_win32_shift_insert["user32"].SendInput.return_value = 4
        result = clip_mod._send_shift_insert_win32()
        assert result is True, (
            f"must return True on full success; got {result!r}"
        )

    def test_returns_false_on_partial_success(self, fake_win32_shift_insert):
        """SendInput returning 1..3 → returns False (no double-paste)."""
        # First SendInput (4-event batch) returns 2 (partial); second
        # SendInput (2-event KEYUP cleanup) return value ignored.
        fake_win32_shift_insert["user32"].SendInput.side_effect = [2, 2]
        result = clip_mod._send_shift_insert_win32()
        assert result is False, (
            f"must return False on partial success; got {result!r}"
        )

    def test_returns_true_on_zero_with_fallback(self, fake_win32_shift_insert):
        """SendInput returning 0 → fallback invoked, returns True."""
        fake_win32_shift_insert["user32"].SendInput.return_value = 0
        fallback = MagicMock()
        result = clip_mod._send_shift_insert_win32(fallback=fallback)
        assert result is True
        fallback.assert_called_once()

    def test_clipboard_manager_method_delegates_to_package(self, fake_win32_shift_insert):
        """``ClipboardManager._send_shift_insert_win32`` delegates correctly."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = MagicMock()
        fake_win32_shift_insert["user32"].SendInput.return_value = 4
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.shift = "shift_key"
            mock_key.insert = "insert_key"
            result = cm._send_shift_insert_win32()
        assert result is True


# ─── Win32 clipboard-monitor exclusion tag ─────────────────


class TestWin32ExcludeClipboardFromMonitoring:
    """clipboard-monitor exclusion tag is set on Windows copy."""

    def test_returns_false_on_non_windows(self, monkeypatch):
        """On non-Windows, the helper is a no-op returning False."""
        monkeypatch.setattr(clip_mod, "is_windows", lambda: False)
        assert clip_mod._win32_exclude_clipboard_from_monitoring() is False

    def test_sets_exclusion_format_on_windows(self, monkeypatch):
        """On Windows, RegisterClipboardFormatW + SetClipboardData succeed."""
        # Mock ctypes.windll + Win32Clipboard.
        mock_user32 = MagicMock()
        mock_kernel32 = MagicMock()
        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32

        # RegisterClipboardFormatW returns a positive format id.
        mock_user32.RegisterClipboardFormatW.return_value = 0xC001
        # GlobalAlloc returns a non-zero HGLOBAL.
        mock_kernel32.GlobalAlloc.return_value = 0xDEADBEEF
        # SetClipboardData returns non-zero (success).
        mock_user32.SetClipboardData.return_value = 0xBEEF

        # Win32Clipboard context manager — _opened is True.
        fake_clip = MagicMock()
        fake_clip._opened = True
        fake_clip.__enter__ = lambda self: self
        fake_clip.__exit__ = lambda self, *args: False

        monkeypatch.setattr(clip_mod, "is_windows", lambda: True)
        monkeypatch.setattr(clip_mod, "Win32Clipboard", lambda *a, **kw: fake_clip)

        with patch("ctypes.windll", mock_windll, create=True):
            result = clip_mod._win32_exclude_clipboard_from_monitoring()

        assert result is True, (
            f"must return True when SetClipboardData succeeds; got {result!r}"
        )
        # Verify the format name passed to RegisterClipboardFormatW.
        mock_user32.RegisterClipboardFormatW.assert_called_once_with(
            "ExcludeClipboardContentFromMonitorProcessing"
        )
        # Verify GlobalAlloc was called with a 1-byte payload.
        mock_kernel32.GlobalAlloc.assert_called_once()
        alloc_args = mock_kernel32.GlobalAlloc.call_args
        assert alloc_args.args[1] == 1, (
            f"GlobalAlloc payload must be 1 byte; got {alloc_args.args[1]!r}"
        )

    def test_returns_false_when_setclipboarddata_fails(self, monkeypatch):
        """SetClipboardData returning NULL → False, and GlobalFree is called."""
        mock_user32 = MagicMock()
        mock_kernel32 = MagicMock()
        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32
        mock_user32.RegisterClipboardFormatW.return_value = 0xC001
        mock_kernel32.GlobalAlloc.return_value = 0xDEADBEEF
        # SetClipboardData returns NULL (0) → failure.
        mock_user32.SetClipboardData.return_value = 0

        fake_clip = MagicMock()
        fake_clip._opened = True
        fake_clip.__enter__ = lambda self: self
        fake_clip.__exit__ = lambda self, *args: False

        monkeypatch.setattr(clip_mod, "is_windows", lambda: True)
        monkeypatch.setattr(clip_mod, "Win32Clipboard", lambda *a, **kw: fake_clip)

        with patch("ctypes.windll", mock_windll, create=True):
            result = clip_mod._win32_exclude_clipboard_from_monitoring()

        assert result is False
        # We must free the HGLOBAL on failure (avoid handle leak).
        mock_kernel32.GlobalFree.assert_called_once_with(0xDEADBEEF)

    def test_copy_invokes_exclusion_helper_on_windows(self, monkeypatch):
        """``ClipboardManager.copy()`` calls the exclusion helper on Windows."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._clipboard_save_restore_enabled = False
        cm._last_copied_text = ""
        cm._clipboard_seq = 0

        # Mock pyperclip + clipboard read-back so copy() succeeds.
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(clip_mod, "pyperclip", pyperclip_mock)
        monkeypatch.setattr(clip_mod, "_paste_from_clipboard", lambda: "hello world")
        monkeypatch.setattr(clip_mod, "_copy_to_clipboard", lambda text: None)
        monkeypatch.setattr(clip_mod, "_win32_empty_clipboard", lambda: None)
        monkeypatch.setattr(clip_mod, "is_windows", lambda: True)

        # Spy on the exclusion helper.
        called: list[bool] = []
        monkeypatch.setattr(
            clip_mod,
            "_win32_exclude_clipboard_from_monitoring",
            lambda: called.append(True) or True,
        )

        result = cm.copy("hello world")

        assert called == [True], (
            "copy() must call _win32_exclude_clipboard_from_monitoring on Windows"
        )
        # copy() returns None or a snapshot on success.
        assert result is None or hasattr(result, "restore")


# ─── IME composition guard ─────────────────────────────────


class TestImeCompositionGuard:
    """paste is deferred when IME composition is in progress."""

    def _make_cm(self) -> ClipboardManager:
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0
        cm._clipboard_seq = 0
        cm._clipboard_save_restore_enabled = False
        cm._last_copied_text = "test"
        cm._restore_delay_ms = 150
        return cm

    def test_paste_returns_false_when_ime_composing(self, monkeypatch):
        """When ``is_ime_composing()`` returns True, paste returns False."""
        cm = self._make_cm()

        # Force Windows + bypass safety check.
        monkeypatch.setattr(clip_mod, "is_windows", lambda: True)
        monkeypatch.setattr(clip_mod, "is_macos", lambda: False)
        monkeypatch.setattr(clip_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            ClipboardManager, "_is_safe_paste_target", staticmethod(lambda: True)
        )
        monkeypatch.setattr(
            ClipboardManager, "_detect_focused_process", staticmethod(lambda: None)
        )

        # Mock is_ime_composing() to return True.
        fake_ime_module = MagicMock()
        fake_ime_module.is_ime_composing.return_value = True
        monkeypatch.setitem(
            sys.modules, "voice_typer.server.hotkeys.windows.ime_guard", fake_ime_module
        )

        # Mock event_bus so the toast publish doesn't fail.
        fake_event_bus = MagicMock()
        monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)
        # The lazy import inside paste() does
        # ``from voice_typer.server import event_bus`` — patch the
        # attribute on the parent package too.
        import voice_typer.server as server_pkg  # noqa: WPS433

        monkeypatch.setattr(server_pkg, "event_bus", fake_event_bus, raising=False)

        # Mock time + _Key.
        mock_time = MagicMock()
        mock_time.monotonic.return_value = 100.0
        mock_time.sleep = MagicMock()
        monkeypatch.setattr(clip_mod, "time", mock_time)
        mock_key = MagicMock()
        monkeypatch.setattr(clip_mod, "_Key", mock_key)

        result = cm.paste()

        assert result is False, (
            f"paste must return False when IME is composing; got {result!r}"
        )
        # The toast event must have been published.
        fake_event_bus.publish.assert_called_once()
        event = fake_event_bus.publish.call_args.args[0]
        assert event["type"] == "paste_deferred"
        assert event["data"]["reason"] == "ime_composition"

    def test_paste_proceeds_when_ime_not_composing(self, monkeypatch):
        """When ``is_ime_composing()`` returns False, paste proceeds."""
        cm = self._make_cm()

        monkeypatch.setattr(clip_mod, "is_windows", lambda: True)
        monkeypatch.setattr(clip_mod, "is_macos", lambda: False)
        monkeypatch.setattr(clip_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            ClipboardManager, "_is_safe_paste_target", staticmethod(lambda: True)
        )
        monkeypatch.setattr(
            ClipboardManager, "_detect_focused_process", staticmethod(lambda: None)
        )

        fake_ime_module = MagicMock()
        fake_ime_module.is_ime_composing.return_value = False
        monkeypatch.setitem(
            sys.modules, "voice_typer.server.hotkeys.windows.ime_guard", fake_ime_module
        )

        mock_time = MagicMock()
        mock_time.monotonic.return_value = 100.0
        mock_time.sleep = MagicMock()
        monkeypatch.setattr(clip_mod, "time", mock_time)
        mock_key = MagicMock()
        monkeypatch.setattr(clip_mod, "_Key", mock_key)

        # Stub _send_ctrl_v_win32 (non-terminal Windows path) so the
        # paste "succeeds" without sending real keystrokes.
        monkeypatch.setattr(cm, "_send_ctrl_v_win32", lambda: True)

        result = cm.paste()

        assert result is True, (
            f"paste must proceed when IME is NOT composing; got {result!r}"
        )


# ─── macOS Secure Input detection ──────────────────────────


class TestSecureInputDetection:
    """``_is_secure_input_enabled`` detects macOS Secure Input."""

    def test_returns_false_on_non_macos(self, monkeypatch):
        """On non-macOS, the helper short-circuits to False."""
        # The default test platform is Linux, so is_macos() returns False.
        # Force the safety_mod.is_macos to return False for clarity.
        import voice_typer.server.clipboard_target_safety as safety_mod

        monkeypatch.setattr(safety_mod, "is_macos", lambda: False)
        assert _is_secure_input_enabled() is False

    def test_returns_true_when_ioreg_reports_secure_input(self, monkeypatch):
        """When ``ioreg`` output contains ``SecureInput``, returns True."""
        import voice_typer.server.clipboard_target_safety as safety_mod

        # Reset the once-only warning flag so this test sees the first
        # detection (which publishes the tray toast).
        monkeypatch.setattr(safety_mod, "_MACOS_SECURE_INPUT_WARNED", False)
        monkeypatch.setattr(safety_mod, "is_macos", lambda: True)

        # Stub ioreg subprocess.
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = b'"SecureInput" = Yes\n'
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: fake_proc,
        )

        # Stub event_bus so the toast publish doesn't fail.
        fake_event_bus = MagicMock()
        monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)
        import voice_typer.server as server_pkg  # noqa: WPS433

        monkeypatch.setattr(server_pkg, "event_bus", fake_event_bus, raising=False)

        result = _is_secure_input_enabled()

        assert result is True, (
            f"must return True when ioreg reports SecureInput; got {result!r}"
        )
        # The toast event must have been published.
        fake_event_bus.publish.assert_called_once()
        event = fake_event_bus.publish.call_args.args[0]
        assert event["type"] == "paste_deferred"
        assert event["data"]["reason"] == "secure_input"
        # The once-only warning flag must flip to True.
        assert safety_mod._MACOS_SECURE_INPUT_WARNED is True

    def test_returns_false_when_ioreg_does_not_report_secure_input(self, monkeypatch):
        """When ``ioreg`` output has no ``SecureInput``, returns False."""
        import voice_typer.server.clipboard_target_safety as safety_mod

        monkeypatch.setattr(safety_mod, "is_macos", lambda: True)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = b'"SomeOtherProperty" = Yes\n'
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: fake_proc,
        )

        assert _is_secure_input_enabled() is False

    def test_returns_false_on_ioreg_failure(self, monkeypatch):
        """When ``ioreg`` is missing / fails, returns False (fail-open)."""
        import voice_typer.server.clipboard_target_safety as safety_mod

        monkeypatch.setattr(safety_mod, "is_macos", lambda: True)

        def raise_filenotfound(*a, **kw):
            raise FileNotFoundError("ioreg not on PATH")

        monkeypatch.setattr("subprocess.run", raise_filenotfound)

        assert _is_secure_input_enabled() is False

    def test_warning_deduped_across_calls(self, monkeypatch):
        """Successive detections log at DEBUG (not WARNING) after the first."""
        import voice_typer.server.clipboard_target_safety as safety_mod

        monkeypatch.setattr(safety_mod, "_MACOS_SECURE_INPUT_WARNED", False)
        monkeypatch.setattr(safety_mod, "is_macos", lambda: True)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = b'"SecureInput" = Yes\n'
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: fake_proc,
        )

        fake_event_bus = MagicMock()
        monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)
        import voice_typer.server as server_pkg  # noqa: WPS433

        monkeypatch.setattr(server_pkg, "event_bus", fake_event_bus, raising=False)

        # First call: publishes the toast event.
        assert _is_secure_input_enabled() is True
        first_call_count = fake_event_bus.publish.call_count
        assert first_call_count == 1

        # Second call: must NOT publish again (deduped).
        assert _is_secure_input_enabled() is True
        assert fake_event_bus.publish.call_count == first_call_count, (
            "subsequent detections must not re-publish the toast"
        )
