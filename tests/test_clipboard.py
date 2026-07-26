"""Tests for clipboard copy and paste logic."""

import sys
from unittest.mock import MagicMock

import pytest

mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server.clipboard import ClipboardManager  # noqa: E402


class TestCopy:
    def test_copy_puts_text_on_clipboard(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.pyperclip", MagicMock())
        import voice_typer.server.clipboard as mod

        mod.pyperclip = MagicMock()
        # PLAT-PASTEVR: copy() verifies clipboard content via pyperclip.paste().
        # Make paste() return the same text so verification passes on first try.
        mod.pyperclip.paste.return_value = "hello world"

        cm = ClipboardManager(paste_enabled=False)
        result = cm.copy("hello world")

        # copy() returns a ClipboardSnapshot (or None if snapshot capture
        # was skipped/empty) on success — never the boolean True/False.
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
        # copy() does NOT return False on failure — it raises
        # ClipboardCopyError so the caller can write crash recovery.
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

        mod.time = MagicMock()
        mod.time.monotonic.return_value = 100.0
        # Platform is now centralized in platform_utils; clipboard.py uses
        # is_windows()/is_macos() imported into its namespace. Force the
        # non-Windows, non-macOS keystroke path (Ctrl+V via pynput).
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

    def test_paste_skips_when_rate_limited(self):
        cm = ClipboardManager(paste_enabled=True)
        cm._last_paste_time = 100.0
        cm._keyboard = MagicMock()

        import voice_typer.server.clipboard as mod

        mod.time.monotonic = MagicMock(return_value=100.3)

        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()

    def test_paste_returns_false_on_keyboard_error(self, monkeypatch):
        import voice_typer.server.clipboard as mod

        mod.time = MagicMock()
        mod.time.monotonic.return_value = 100.0
        # Platform is now centralized in platform_utils; force the
        # non-Windows, non-macOS keystroke path (Ctrl+V via pynput).
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


# ─── ADR-0020 §6.6: Wayland wl-copy / wl-paste fallback ────────────────


class TestWaylandFallback:
    """Tests for the Wayland clipboard fallback (ADR-0020 §6.6).

    These tests verify the platform dispatcher routes to `wl-copy` /
    `wl-paste` when on Linux Wayland + wl-clipboard installed, and falls
    back to pyperclip otherwise.
    """

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
        # pyperclip should NOT be called when wl-copy succeeds.
        pyperclip_mock = MagicMock()
        monkeypatch.setattr(mod, "pyperclip", pyperclip_mock)

        mod._linux_copy("hello wayland")

        assert len(captured) == 1
        cmd, kw = captured[0]
        assert cmd[0] == "wl-copy"
        # XZ-CLIP-02 (security): text is piped via stdin (NOT passed as
        # a positional CLI argument) so it isn't visible in
        # /proc/<pid>/cmdline to other local users.
        assert "hello wayland" in kw.get("input", b"").decode("utf-8"), (
            "XZ-CLIP-02: text must be piped via stdin=input=... (not as CLI arg)"
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
