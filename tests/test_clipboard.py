"""Tests for clipboard copy and paste logic."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import (  # noqa: E402
    clipboard as clip_mod,
    clipboard_snapshot as snap_mod,
)
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402


class TestCopy:
    def test_copy_puts_text_on_clipboard(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.pyperclip", MagicMock())
        import voice_typer.server.clipboard as mod

        mod.pyperclip = MagicMock()
        # PLAT-PASTEVR: copy() verifies clipboard content via pyperclip.paste().
        mod.pyperclip.paste.return_value = "hello world"

        cm = ClipboardManager(paste_enabled=False)
        result = cm.copy("hello world")

        assert result is None or isinstance(result, mod.ClipboardSnapshot)
        mod.pyperclip.copy.assert_called_with("hello world")
        # PLAT-PASTEVR: with working verification, copy is called exactly once
        assert mod.pyperclip.copy.call_count == 1

    def test_copy_returns_false_for_empty_text(self):
        cm = ClipboardManager(paste_enabled=False)
        assert cm.copy("") is None
        assert cm.copy(None) is None

    def test_copy_returns_false_on_exception(self, monkeypatch):
        import voice_typer.server.clipboard as mod

        mod.pyperclip = MagicMock()
        mod.pyperclip.copy.side_effect = Exception("clipboard locked")

        cm = ClipboardManager(paste_enabled=False)
        with pytest.raises(mod.ClipboardCopyError):
            cm.copy("test")


class TestPaste:
    def _make_cm(self, **kwargs) -> ClipboardManager:
        """Helper to create ClipboardManager with rate-limit bypassed."""
        cm = ClipboardManager(**kwargs)
        cm._last_paste_time = -999.0  # well before any rate-limit window
        cm._keyboard = MagicMock()
        return cm

    def test_paste_sends_keystroke(self, monkeypatch):
        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod, "time", MagicMock())
        mod.time.monotonic.return_value = 100.0
        # Platform is now centralized in platform_utils; clipboard.py uses
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.clipboard.is_macos", lambda: False)

        cm = self._make_cm(paste_enabled=True)
        result = cm.paste()

        assert result is True
        cm._keyboard.press.assert_called()
        cm._keyboard.release.assert_called()

    def test_paste_skips_when_disabled(self):
        cm = self._make_cm(paste_enabled=False)
        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()

    def test_paste_skips_when_rate_limited(self, monkeypatch):
        cm = ClipboardManager(paste_enabled=True)
        cm._last_paste_time = 100.0
        cm._keyboard = MagicMock()

        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod.time, "monotonic", MagicMock(return_value=100.3))

        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()

    def test_paste_returns_false_on_keyboard_error(self, monkeypatch):
        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod, "time", MagicMock())
        mod.time.monotonic.return_value = 100.0
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.clipboard.is_macos", lambda: False)

        cm = self._make_cm(paste_enabled=True)
        cm._keyboard = MagicMock()
        cm._keyboard.press.side_effect = Exception("keyboard error")

        result = cm.paste()

        assert result is False

    def test_is_terminal_process(self):
        assert ClipboardManager._is_terminal_process("windowsterminal.exe") is True
        assert ClipboardManager._is_terminal_process("cmd.exe") is True
        assert ClipboardManager._is_terminal_process("notepad.exe") is False
        assert ClipboardManager._is_terminal_process(None) is False
        assert ClipboardManager._is_terminal_process("") is False


class TestWaylandFallback:
    """Tests for the Wayland clipboard fallback (ADR-0020 §6.6)."""

    def test_is_wayland_session_false_without_env(self, monkeypatch):
        """No WAYLAND_DISPLAY → not a Wayland session."""
        import voice_typer.server.clipboard as mod

        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        # Force is_linux() True so the WAYLAND_DISPLAY check is reached.
        monkeypatch.setattr(mod, "is_linux", lambda: True)
        assert mod._is_wayland_session() is False

    def test_is_wayland_session_true_with_env(self, monkeypatch):
        """WAYLAND_DISPLAY set + Linux → Wayland session."""
        import voice_typer.server.clipboard as mod

        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setattr(mod, "is_linux", lambda: True)
        assert mod._is_wayland_session() is True

    def test_is_wayland_session_false_on_non_linux(self, monkeypatch):
        """WAYLAND_DISPLAY set but non-Linux → not a Wayland session."""
        import voice_typer.server.clipboard as mod

        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setattr(mod, "is_linux", lambda: False)
        assert mod._is_wayland_session() is False

    def test_linux_copy_uses_wl_copy_on_wayland(self, monkeypatch):
        """On Wayland with wl-clipboard installed, _linux_copy calls wl-copy."""
        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod, "_is_wayland_session", lambda: True)
        monkeypatch.setattr(mod, "_have_wl_clipboard", lambda: True)

        captured = []

        class FakeProc:
            returncode = 0
            stderr = b""

        def fake_run(cmd, **kw):
            captured.append((cmd, kw))
            return FakeProc()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(mod, "pyperclip", pyperclip_mock)

        mod._linux_copy("hello wayland")

        assert len(captured) == 1
        cmd, kw = captured[0]
        assert cmd[0] == "wl-copy"
        assert "hello wayland" in kw.get("input", b"").decode("utf-8"), (
            "text must be piped via stdin=input=... (not as CLI arg)"
        )
        pyperclip_mock.copy.assert_not_called()

    def test_linux_copy_falls_back_to_pyperclip_on_wl_copy_failure(self, monkeypatch):
        """If wl-copy fails, _linux_copy falls back to pyperclip.copy."""
        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod, "_is_wayland_session", lambda: True)
        monkeypatch.setattr(mod, "_have_wl_clipboard", lambda: True)

        class FakeProc:
            returncode = 1
            stderr = b"wl-copy: failed to connect to wayland"

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: FakeProc())
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(mod, "pyperclip", pyperclip_mock)

        mod._linux_copy("hello wayland")

        pyperclip_mock.copy.assert_called_once_with("hello wayland")

    def test_linux_copy_uses_pyperclip_when_not_wayland(self, monkeypatch):
        """On X11 (no Wayland), _linux_copy calls pyperclip.copy directly."""
        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod, "_is_wayland_session", lambda: False)
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(mod, "pyperclip", pyperclip_mock)

        mod._linux_copy("hello x11")

        pyperclip_mock.copy.assert_called_once_with("hello x11")

    def test_linux_paste_uses_wl_paste_on_wayland(self, monkeypatch):
        """On Wayland with wl-clipboard installed, _linux_paste calls wl-paste."""
        import voice_typer.server.clipboard as mod

        monkeypatch.setattr(mod, "_is_wayland_session", lambda: True)
        monkeypatch.setattr(mod, "_have_wl_clipboard", lambda: True)

        class FakeProc:
            returncode = 0
            stdout = b"pasted from wayland"
            stderr = b""

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: FakeProc())
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(mod, "pyperclip", pyperclip_mock)

        result = mod._linux_paste()

        assert result == "pasted from wayland"
        pyperclip_mock.paste.assert_not_called()

    def test_linux_wayland_copy_noop_on_empty_text(self, monkeypatch):
        """Empty text is a no-op (no wl-copy subprocess)."""
        import voice_typer.server.clipboard as mod

        called = []
        monkeypatch.setattr(
            mod.subprocess, "run", lambda *a, **kw: called.append(a) or MagicMock(returncode=0, stderr=b"")
        )
        mod._linux_wayland_copy("")
        assert called == []

    def test_copy_to_clipboard_dispatches_per_platform(self, monkeypatch):
        """_copy_to_clipboard routes to _linux_copy on Linux, pyperclip elsewhere."""
        import voice_typer.server.clipboard as mod

        # Linux path
        monkeypatch.setattr(mod, "is_linux", lambda: True)
        called = []
        monkeypatch.setattr(mod, "_linux_copy", lambda t: called.append(("linux", t)))
        monkeypatch.setattr(mod, "pyperclip", MagicMock())
        mod._copy_to_clipboard("a")
        assert called == [("linux", "a")]

        # Non-Linux path
        monkeypatch.setattr(mod, "is_linux", lambda: False)
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(mod, "pyperclip", pyperclip_mock)
        mod._copy_to_clipboard("b")
        pyperclip_mock.copy.assert_called_once_with("b")


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_pending_restores():
    """Each test starts and ends with an empty ``_pending_restores`` list."""
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()
    yield
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()


def _make_cm(
    *,
    paste_enabled: bool = True,
    save_restore: bool = True,
    restore_delay_ms: int = 150,
) -> ClipboardManager:
    """Build a ClipboardManager with mocked keyboard and cached flags set."""
    from tests.fixtures.clipboard_helpers import make_clipboard_manager

    return make_clipboard_manager(
        paste_enabled=paste_enabled,
        save_restore=save_restore,
        restore_delay_ms=restore_delay_ms,
    )


def _make_snapshot(platform: str = "linux-x11") -> ClipboardSnapshot:
    """Build a fake ClipboardSnapshot for tests that need a non-None value."""
    from tests.fixtures.clipboard_helpers import make_clipboard_snapshot

    return make_clipboard_snapshot(
        platform=platform,
        content_type="text/plain;charset=utf-8",
    )


class TestLastCopiedTextRetention:
    """DE-59: ``_last_copied_text`` is cleared on every code path."""

    def test_copy_does_not_cache_text_when_snapshot_is_none(self):
        """DE-59 path 2/3: when save_restore is disabled, ``copy()``"""
        cm = _make_cm(save_restore=False)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "super-secret-password"  # verify match
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
        ):
            # Pre-seed with stale value to prove the clear runs.
            cm._last_copied_text = "stale-from-prior-cycle"
            cm.copy("super-secret-password")
        # text must NOT be cached when snapshot is None.
        assert cm._last_copied_text == "", (
            f"_last_copied_text should be cleared when snapshot is None; got {cm._last_copied_text!r} (PII leak)"
        )

    def test_copy_does_cache_text_when_snapshot_captured(self):
        """DE-59 sanity: when save_restore IS enabled, ``copy()`` still"""
        cm = _make_cm(save_restore=True)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "the dictation"
        sentinel = _make_snapshot()
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
            patch.object(cm, "_get_clipboard_sequence_number", return_value=42),
        ):
            cm.copy("the dictation")
        # Text IS cached because a restore IS scheduled.
        assert cm._last_copied_text == "the dictation"

    def test_restore_now_clears_last_copied_text(self):
        """DE-59 path 1: ``restore_now()`` must clear"""
        cm = _make_cm()
        cm._last_copied_text = "dictated-secret"
        snap = _make_snapshot()
        with (
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            cm.restore_now(snap)
        mock_restore.assert_called_once()
        # _last_copied_text must be cleared by restore_now().
        assert cm._last_copied_text == "", (
            f"restore_now() should clear _last_copied_text; got {cm._last_copied_text!r} (PII leak)"
        )

    def test_restore_now_clears_last_copied_text_even_if_restore_raises(self):
        """DE-59 path 1 (exception): the clear must run even if"""
        cm = _make_cm()
        cm._last_copied_text = "dictated-secret"
        snap = _make_snapshot()
        with (
            patch.object(snap, "restore", side_effect=RuntimeError("restore blew up")),
            patch.object(clip_mod, "log"),
        ):
            # Must not raise, restore_now() catches + logs.
            cm.restore_now(snap)
        assert cm._last_copied_text == ""

    def test_restore_now_with_none_snapshot_is_noop_for_clear(self):
        """DE-59 sanity: ``restore_now(None)`` does not raise and does"""
        cm = _make_cm()
        cm._last_copied_text = "pre-existing-value"
        with patch.object(clip_mod, "log"):
            cm.restore_now(None)
        # The important contract is that restore_now(None) doesn't raise.
        assert cm._last_copied_text == "pre-existing-value"


class TestSeqMismatchRecopyUsesPastedText:
    """DE-60: the seq-mismatch re-copy path threads ``pasted_text``."""

    def test_seq_mismatch_recopy_uses_pasted_text_not_instance_attr(self):
        """When the seq mismatches, the re-copy uses ``pasted_text``,"""
        cm = _make_cm(save_restore=False)
        cm._last_copied_text = "TEXT_B_FROM_CONCURRENT_COPY"
        cm._clipboard_seq = 100  # what copy() recorded for THIS cycle
        # ``pasted_text`` is what THIS cycle copied (text_A). The fix
        pasted_text_for_this_cycle = "TEXT_A_FROM_THIS_CYCLE"

        captured_inputs: list[str] = []

        def _capture_copy(text):
            captured_inputs.append(text)

        # We need is_windows() to return True so the seq-mismatch path
        with (
            patch.object(clip_mod, "is_windows", return_value=True),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "_Key", MagicMock()),
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
            patch.object(
                ClipboardManager,
                "_release_stuck_modifiers",
                lambda self: None,
            ),
            patch.object(
                ClipboardManager,
                "_send_ctrl_v_win32",
                return_value=True,
            ),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_copy_to_clipboard", side_effect=_capture_copy),
            patch.object(
                ClipboardManager,
                "_get_clipboard_sequence_number",
                # First call (during paste's seq check) returns 999 (mismatch
                side_effect=[999, 1000],
            ),
            patch.object(clip_mod, "log"),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            result = cm.paste(
                snapshot=None,
                pasted_text=pasted_text_for_this_cycle,
                pasted_seq=100,
            )
        assert result is True
        assert captured_inputs == [pasted_text_for_this_cycle], (
            f"seq-mismatch re-copy should use pasted_text={pasted_text_for_this_cycle!r}; "
            f"got _copy_to_clipboard called with {captured_inputs!r}"
        )

    def test_seq_mismatch_recopy_falls_back_to_instance_attr_when_pasted_text_none(self):
        """DE-60: backward-compat, when ``pasted_text is None`` (legacy"""
        cm = _make_cm(save_restore=False)
        cm._last_copied_text = "FALLBACK_TEXT"
        cm._clipboard_seq = 100

        captured_inputs: list[str] = []

        def _capture_copy(text):
            captured_inputs.append(text)

        with (
            patch.object(clip_mod, "is_windows", return_value=True),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "_Key", MagicMock()),
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
            patch.object(
                ClipboardManager,
                "_release_stuck_modifiers",
                lambda self: None,
            ),
            patch.object(
                ClipboardManager,
                "_send_ctrl_v_win32",
                return_value=True,
            ),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_copy_to_clipboard", side_effect=_capture_copy),
            patch.object(
                ClipboardManager,
                "_get_clipboard_sequence_number",
                side_effect=[999, 1000],
            ),
            patch.object(clip_mod, "log"),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            cm.paste(snapshot=None, pasted_text=None, pasted_seq=100)
        # Fallback used the instance attribute.
        assert captured_inputs == ["FALLBACK_TEXT"]


class TestLinuxRestoreReturnsFalseOnNonZeroExit:
    """DE-61: ``_restore_x11`` / ``_restore_wayland`` return False when"""

    def test_restore_x11_returns_false_on_xclip_nonzero_exit(self):
        """xclip exits non-zero (e.g. no DISPLAY) → restore returns False."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            # With check=True ( fix), subprocess.run raises
            raise subprocess.CalledProcessError(returncode=1, cmd=args[0])

        with (
            patch("subprocess.run", side_effect=_fake_run) as mock_run,
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_x11()
        assert result is False, (
            "DE-61: xclip non-zero exit must return False, not True (silent data loss with false-success signal)"
        )
        mock_run.assert_called_once()
        mock_log.warning.assert_called_once()
        # The warning must mention  for traceability.
        warning_args = mock_log.warning.call_args
        assert "DE-61" in str(warning_args), f"DE-61 warning must reference the finding ID; got {warning_args!r}"

    def test_restore_x11_returns_true_on_success(self):
        """DE-61 sanity: xclip exits 0 → restore returns True (no regression)."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_x11()
        assert result is True

    def test_restore_x11_still_returns_false_on_timeout(self):
        """DE-61 regression: TimeoutExpired is still caught (was caught"""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="xclip", timeout=2.0),
            ),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_x11()
        assert result is False

    def test_restore_x11_still_returns_false_on_missing_binary(self):
        """DE-61 regression: FileNotFoundError (xclip not installed) is"""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        with (
            patch(
                "subprocess.run",
                side_effect=FileNotFoundError("xclip not found"),
            ),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_x11()
        assert result is False

    def test_restore_wayland_returns_false_on_wl_copy_nonzero_exit(self):
        """wl-copy exits non-zero → restore returns False."""
        snap = ClipboardSnapshot(
            platform="linux-wayland",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            # With check=True ( fix), subprocess.run raises
            raise subprocess.CalledProcessError(returncode=1, cmd=args[0])

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_wayland()
        assert result is False, "DE-61: wl-copy non-zero exit must return False, not True"
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        assert "DE-61" in str(warning_args)

    def test_restore_wayland_returns_true_on_success(self):
        """DE-61 sanity: wl-copy exits 0 → restore returns True."""
        snap = ClipboardSnapshot(
            platform="linux-wayland",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_wayland()
        assert result is True

    def test_restore_x11_passes_check_true_to_subprocess_run(self):
        """DE-63 source-string pin: the production code MUST pass"""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            # Verify check=True was passed.
            assert kwargs.get("check") is True, (
                f"DE-61: subprocess.run must be called with check=True; got kwargs={kwargs!r}"
            )
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            snap._restore_x11()

    def test_restore_wayland_passes_check_true_to_subprocess_run(self):
        """DE-61 source-string pin: ``check=True`` on the Wayland path."""
        snap = ClipboardSnapshot(
            platform="linux-wayland",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            assert kwargs.get("check") is True, (
                f"DE-61: subprocess.run must be called with check=True; got kwargs={kwargs!r}"
            )
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            snap._restore_wayland()


def _install_fake_windll_for_restore(*, set_clipboard_data_returns: int = 0):
    """Install a fake ``ctypes.windll`` for the Windows restore path."""
    user32 = MagicMock()
    user32.OpenClipboard.return_value = 1  # opened
    user32.EmptyClipboard.return_value = 1
    user32.SetClipboardData.return_value = set_clipboard_data_returns
    user32.CloseClipboard.return_value = 1
    # RegisterClipboardFormatW returns a non-zero format ID on success.
    user32.RegisterClipboardFormatW.return_value = 1

    kernel32 = MagicMock()
    kernel32.GlobalAlloc.return_value = 0xDEADBEEF  # non-NULL handle
    kernel32.GlobalLock.return_value = 0xCAFEBABE  # non-NULL ptr
    kernel32.GlobalUnlock.return_value = 1
    kernel32.GlobalFree.return_value = 0

    windll = MagicMock()
    windll.user32 = user32
    windll.kernel32 = kernel32
    return windll, user32, kernel32


class TestWindowsRestoreReturnsFalseOnAllFailures:
    """DE-62: ``_restore_windows`` returns ``False`` when zero items are"""

    def test_restore_windows_returns_false_when_all_setclipboarddata_fail(self):
        """DE-62: all SetClipboardData calls fail → return False."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[(_cf_unicodetext := 13, "CF_UNICODETEXT", b"hello\0")],
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore(
            set_clipboard_data_returns=0,  # SetClipboardData fails
        )

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.memmove"),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_windows()
        assert result is False, (
            "DE-62: when all SetClipboardData calls fail, _restore_windows "
            "must return False (not True, false success with empty clipboard)"
        )
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        assert "DE-62" in str(warning_args), f"DE-62 warning must reference the finding ID; got {warning_args!r}"
        user32.EmptyClipboard.assert_called_once()
        user32.SetClipboardData.assert_called()

    def test_restore_windows_returns_true_when_at_least_one_item_set(self):
        """DE-62 sanity: at least one SetClipboardData succeeds → return True."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[
                (13, "CF_UNICODETEXT", b"hello\0"),
                (1, "CF_TEXT", b"hello\0"),
            ],
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore(
            set_clipboard_data_returns=0xDEADBEEF,  # non-NULL = success
        )

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.memmove"),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_windows()
        assert result is True
        user32.SetClipboardData.assert_called()

    def test_restore_windows_returns_false_when_openclipboard_fails(self):
        """DE-62 regression: OpenClipboard failure still returns False"""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[(13, "CF_UNICODETEXT", b"hello\0")],
            captured_at=0.0,
        )
        user32 = MagicMock()
        user32.OpenClipboard.return_value = 0  # locked
        user32.CloseClipboard.return_value = 1
        windll = MagicMock()
        windll.user32 = user32
        windll.kernel32 = MagicMock()

        with (
            patch("ctypes.windll", windll, create=True),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_windows()
        assert result is False

    def test_restore_windows_empty_items_does_not_call_emptyclipboard(self):
        """success-count check (OpenClipboard + EmptyClipboard still run,"""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[],  # empty
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore()

        with (
            patch("ctypes.windll", windll, create=True),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_windows()
        assert result is False
        # The warning was logged.
        mock_log.warning.assert_called_once()


class TestWindowsRestoreFormatIdSelection:
    """Clipboard restore must address builtin formats by NUMBER.

    Builtin formats (``CF_UNICODETEXT``=13, ``CF_TEXT``=1, ...) are
    predefined ids, they have no ``RegisterClipboardFormat`` name. Asking
    the API to look one up by its display name returns a NEW custom id, so
    the restored bytes land under that id and the builtin lookup finds
    nothing: the user's clipboard content does not come back.
    """

    def test_builtin_format_id_is_not_re_registered_by_name(self):
        """A CF_UNICODETEXT item must be written back as format 13."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[(13, "CF_UNICODETEXT", b"hello\0")],
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore(
            set_clipboard_data_returns=0xDEADBEEF,  # non-NULL = success
        )

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.memmove"),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_windows()

        assert result is True
        user32.RegisterClipboardFormatW.assert_not_called()
        user32.SetClipboardData.assert_called_once()
        target_fmt = user32.SetClipboardData.call_args.args[0]
        assert target_fmt == 13, (
            f"SetClipboardData must receive the builtin CF_UNICODETEXT id (13); got {target_fmt!r}. "
            "Re-registering the display name would create a bogus custom id and lose the user's text."
        )

    def test_registered_format_is_still_re_registered_by_name(self):
        """Custom formats (id >= 0xC000) keep the dynamic-id re-registration."""
        name = "ExcludeClipboardContentFromMonitorProcessing"
        snap = ClipboardSnapshot(
            platform="windows",
            items=[(0xC001, name, b"payload")],
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore(
            set_clipboard_data_returns=0xDEADBEEF,
        )
        # Windows hands out a different custom id this session.
        user32.RegisterClipboardFormatW.return_value = 0xC042

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.memmove"),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_windows()

        assert result is True
        user32.RegisterClipboardFormatW.assert_called_once_with(name)
        assert user32.SetClipboardData.call_args.args[0] == 0xC042, (
            "A registered format must be written back under its CURRENT id, not the captured one"
        )


def _install_fake_appkit_for_restore(
    *,
    set_data_returns: bool = True,
    write_objects_returns: bool = True,
):
    """Install fake ``AppKit`` and ``Foundation`` modules into ``sys.modules``"""
    item_mock = MagicMock(name="ns_pasteboard_item")
    item_mock.setData_forType_.return_value = set_data_returns

    alloc_mock = MagicMock(name="ns_pasteboard_item_alloc")
    alloc_mock.init.return_value = item_mock

    pasteboard_item_cls = MagicMock(name="NSPasteboardItem")
    pasteboard_item_cls.alloc.return_value = alloc_mock

    pb = MagicMock(name="ns_pasteboard")
    pb.clearContents.return_value = None
    pb.writeObjects_.return_value = write_objects_returns

    ns_pasteboard_cls = MagicMock(name="NSPasteboard")
    ns_pasteboard_cls.generalPasteboard.return_value = pb

    appkit = MagicMock(name="AppKit")
    appkit.NSPasteboard = ns_pasteboard_cls
    appkit.NSPasteboardItem = pasteboard_item_cls

    foundation = MagicMock(name="Foundation")
    foundation.NSData.dataWithBytes_length_.return_value = MagicMock(name="nsdata_bytes")
    foundation.NSData.data.return_value = MagicMock(name="nsdata_empty")

    return appkit, foundation, pb, item_mock


class TestMacosRestoreReturnsFalseOnAllFailures:
    """macOS ``_restore_macos`` returns ``False`` when zero items are"""

    def test_restore_macos_returns_false_when_all_setdata_fail(self):
        """All ``setData_forType_`` calls fail → ``success_count == 0``"""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[(0, "public.utf8-plain-text", b"hello")],
            captured_at=0.0,
        )
        appkit, foundation, pb, item_mock = _install_fake_appkit_for_restore(
            set_data_returns=False,  # setData_forType_ fails
            write_objects_returns=True,
        )

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        assert result is False, (
            "When all setData_forType_ calls fail, _restore_macos must "
            "return False (not True, false success with empty pasteboard "
            "after clearContents)"
        )
        # The warning must mention the per-item failure mode.
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        # The format string reports per-item success and writeObjects_.
        fmt_str = warning_args.args[0]
        assert "items set" in fmt_str and "writeObjects_" in fmt_str, (
            f"WARNING format string must report items-set count and writeObjects_; got {fmt_str!r}"
        )
        assert warning_args.args[1:] == (0, 1, True), (
            f"WARNING args must be (success_count, total, write_ok); got {warning_args.args[1:]!r}"
        )
        pb.clearContents.assert_called_once()
        item_mock.setData_forType_.assert_called()

    def test_restore_macos_returns_true_when_at_least_one_item_set(self):
        """At least one ``setData_forType_`` succeeds AND"""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[
                (0, "public.utf8-plain-text", b"hello"),
                (0, "public.rtf", b"{\\rtf1 hello}"),
            ],
            captured_at=0.0,
        )
        appkit, foundation, pb, _ = _install_fake_appkit_for_restore(
            set_data_returns=True,
            write_objects_returns=True,
        )

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_macos()

        assert result is True
        pb.writeObjects_.assert_called_once()
        ns_items_arg = pb.writeObjects_.call_args.args[0]
        assert isinstance(ns_items_arg, list) and len(ns_items_arg) >= 1

    def test_restore_macos_returns_false_when_writeobjects_returns_false(self):
        """``setData_forType_`` succeeds (so ``success_count > 0``) but"""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[(0, "public.utf8-plain-text", b"hello")],
            captured_at=0.0,
        )
        appkit, foundation, pb, item_mock = _install_fake_appkit_for_restore(
            set_data_returns=True,  # setData_forType_ succeeds
            write_objects_returns=False,  # but writeObjects_ rejects
        )

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        assert result is False, (
            "When writeObjects_ returns False, _restore_macos must return "
            "False (not True, pasteboard rejected every item, clipboard is "
            "empty after clearContents)"
        )
        # WARNING was logged (not DEBUG).
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        # The format string references "items set" and "writeObjects_".
        fmt_str = warning_args.args[0]
        assert "items set" in fmt_str and "writeObjects_" in fmt_str, (
            f"WARNING format string must report items-set count and writeObjects_; got {fmt_str!r}"
        )
        assert warning_args.args[1:] == (1, 1, False), (
            f"WARNING args must be (success_count, total, write_ok) with write_ok=False; got {warning_args.args[1:]!r}"
        )
        item_mock.setData_forType_.assert_called()
        pb.writeObjects_.assert_called_once()

    def test_restore_macos_returns_false_when_no_items(self):
        """A snapshot with zero items must NOT call ``writeObjects_``"""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[],  # empty
            captured_at=0.0,
        )
        appkit, foundation, pb, _ = _install_fake_appkit_for_restore()

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        assert result is False
        # writeObjects_ must NOT be called when ns_items is empty
        pb.writeObjects_.assert_not_called()
        # WARNING was logged for the 0/0 items-set case.
        mock_log.warning.assert_called_once()

    def test_restore_macos_multi_item_mixed_success_returns_true(self):
        """Multi-item / multi-type pasteboard where one ``setData_forType_``"""
        # Two pasteboard items (idx=0 and idx=1), each with one type.
        snap = ClipboardSnapshot(
            platform="macos",
            items=[
                (0, "public.utf8-plain-text", b"item-0-text"),
                (1, "public.utf8-plain-text", b"item-1-text"),
            ],
            captured_at=0.0,
        )
        appkit, foundation, pb, item_mock = _install_fake_appkit_for_restore(
            set_data_returns=True,  # all setData_forType_ succeed
            write_objects_returns=True,
        )
        # Make the FIRST setData_forType_ call fail and the SECOND
        item_mock.setData_forType_.side_effect = [False, True]

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        assert result is True
        # No WARNING was logged, the per-item failure was DEBUG-only.
        mock_log.warning.assert_not_called()
        # The DEBUG log for the per-item failure was emitted.
        debug_calls = [str(c) for c in mock_log.debug.call_args_list]
        assert any("setData_forType_ failed" in c for c in debug_calls), (
            f"Per-item setData_forType_ failure must be logged at DEBUG; got debug calls: {debug_calls!r}"
        )
        pb.writeObjects_.assert_called_once()
        ns_items_arg = pb.writeObjects_.call_args.args[0]
        assert len(ns_items_arg) == 2, (
            f"Multi-item pasteboard must write 2 NSPasteboardItem objects; got {len(ns_items_arg)}"
        )


class TestAtexitDoesNotRaceDaemonRestore:
    """DE-63: the daemon's ``_pending_restores.remove(pending_entry)``"""

    def test_daemon_claims_entry_before_restore(self):
        """The daemon removes its entry from ``_pending_restores`` BEFORE"""
        cm = _make_cm()
        snap = _make_snapshot()
        snap_restore_call_count = {"count": 0}
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        def _spy_restore(*args, **kwargs):
            snap_restore_call_count["count"] += 1
            # At the time snapshot.restore() is called, the entry must
            with clip_mod._pending_restores_lock:
                assert entry not in clip_mod._pending_restores, (
                    "DE-63: daemon must claim (remove) the pending_entry "
                    "BEFORE calling snapshot.restore(), atexit could fire "
                    "during snapshot.restore() and double-restore"
                )
            return True

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", side_effect=_spy_restore),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        assert snap_restore_call_count["count"] == 1

    def test_daemon_short_circuits_when_atexit_already_claimed(self):
        """If atexit has already cleared ``_pending_restores`` (claimed"""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # Do NOT register the entry, simulate atexit having already
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore") as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        # daemon must NOT restore, atexit will do it synchronously.
        mock_restore.assert_not_called()

    def test_daemon_short_circuit_logs_at_debug(self):
        """The atexit-claimed short-circuit path logs at DEBUG (not"""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore"),
            patch.object(clip_mod, "log") as mock_log,
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        # A debug log was emitted for the short-circuit.
        mock_log.debug.assert_called()
        # The debug call names the atexit-claimed race resolution.
        debug_calls = [str(c) for c in mock_log.debug.call_args_list]
        assert any("already claimed by atexit" in c for c in debug_calls), (
            f"atexit-claimed short-circuit must log at DEBUG with the reason; got debug calls: {debug_calls!r}"
        )

    def test_atexit_handler_skips_entries_claimed_by_daemon(self):
        """clears it. If the daemon has already claimed (removed) its"""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        # Daemon claims the entry (removes it under the lock), this
        with clip_mod._pending_restores_lock, contextlib.suppress(ValueError):
            clip_mod._pending_restores.remove(entry)

        # Now atexit fires. It should see an EMPTY list (daemon already
        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore") as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            clip_mod._force_restore_pending_at_exit()
        mock_restore.assert_not_called()

    def test_atexit_still_restores_entries_daemon_has_not_claimed(self):
        """DE-63 regression: atexit still restores entries the daemon"""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # Entry is still in _pending_restores, daemon hasn't claimed it.
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            clip_mod._force_restore_pending_at_exit()
        mock_restore.assert_called_once()
        # The list was cleared.
        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == []

    def test_daemon_clears_last_copied_text_even_on_short_circuit(self):
        """short-circuit path."""
        cm = _make_cm()
        cm._last_copied_text = "secret-pii"
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore"),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        assert cm._last_copied_text == "", (
            f"_last_copied_text should be cleared by _delayed_restore's "
            f"finally block even on the short-circuit path; got "
            f"{cm._last_copied_text!r} (PII leak)"
        )

    def test_no_concurrent_restore_when_atexit_fires_during_daemon_restore(self):
        """DE-63 end-to-end: simulate atexit firing WHILE the daemon is"""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        restore_call_count = {"count": 0}
        atexit_call_count = {"count": 0}

        def _spy_restore(*args, **kwargs):
            restore_call_count["count"] += 1
            # Simulate atexit firing DURING snapshot.restore(). At this
            with (
                patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
                patch.object(clip_mod, "log"),
            ):
                clip_mod._force_restore_pending_at_exit()
            atexit_call_count["count"] += 1
            return True

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", side_effect=_spy_restore),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        # Daemon called restore exactly once.
        assert restore_call_count["count"] == 1
        # Atexit fired during the daemon's restore call.
        assert atexit_call_count["count"] == 1
        # No second restore call from atexit (it saw an empty list).


class TestSourceStringPin:
    """``paste()`` must reference ``pasted_text`` (the threaded"""

    def test_seq_mismatch_recopy_reads_pasted_text_not_instance_attr(self):
        """The seq-mismatch re-copy block must compute ``recopy_text``"""
        import inspect
        import re

        from voice_typer.server.clipboard.manager import PasteMixin

        manager_src = "\n".join(inspect.getsource(c) for c in (ClipboardManager, PasteMixin))
        # The source MUST contain a ``recopy_text`` local that prefers
        m = re.search(
            r"recopy_text\s*=\s*pasted_text\s+if\s+pasted_text\s+is\s+not\s+None\s+else\s+self\._last_copied_text",
            manager_src,
        )
        assert m is not None, (
            "DE-60: seq-mismatch re-copy path must compute recopy_text from "
            "pasted_text (request-scoped parameter), with fallback to "
            "self._last_copied_text only when pasted_text is None. "
            "Expected pattern not found in manager.py source."
        )

    def test_wayland_paste_call_sites_use_wtype_text(self):
        """``pasted_text``), NOT ``self._last_copied_text`` directly."""
        import inspect
        import re

        from voice_typer.server.clipboard.manager import PasteMixin

        manager_src = "\n".join(inspect.getsource(c) for c in (ClipboardManager, PasteMixin))
        # The wtype_text local must be computed from pasted_text.
        m = re.search(
            r"wtype_text\s*=\s*pasted_text\s+if\s+pasted_text\s+is\s+not\s+None\s+else\s+self\._last_copied_text",
            manager_src,
        )
        assert m is not None, (
            "DE-60: Wayland paste call sites must compute wtype_text from "
            "pasted_text (with fallback to self._last_copied_text)."
        )
        # The two call sites must use wtype_text, not self._last_copied_text.
        bad_pattern = r"_linux_paste_via_wtype\(\s*self\._last_copied_text\s*\)"
        bad_matches = re.findall(bad_pattern, manager_src)
        assert not bad_matches, (
            f"DE-60: _linux_paste_via_wtype must not be called with "
            f"self._last_copied_text directly; found {len(bad_matches)} "
            f"occurrences. Should use wtype_text instead."
        )
