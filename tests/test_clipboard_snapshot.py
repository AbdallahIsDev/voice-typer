"""Tests for ``voice_typer.server.clipboard_snapshot.ClipboardSnapshot``.

ADR-0010 §10.1: tests for the new multi-format clipboard snapshot module.

These tests cover:

* Windows ``_capture_windows`` failure paths (locked clipboard, empty
  clipboard, builtin format-name lookup).
* Platform dispatch in ``restore()`` (linux-x11 happy path, unknown
  platform failure).
* Linux X11 / Wayland capture-returns-None when the required CLI
  tool (``xclip`` / ``wl-paste``) is missing.
* Linux X11 / Wayland ``_restore_*`` early-return ``True`` when the
  snapshot has no items.

Platform-specific Windows / macOS code paths are guarded by
``pytest.skipif`` so the suite runs on the Linux CI box. The Win32
``_capture_windows`` tests install a ``MagicMock`` for
``ctypes.windll`` so the Windows-only branches execute on Linux.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ClipboardManager is heavy — pynput must be mocked at import time so
# the module loads cleanly on a headless Linux box.
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard_snapshot as snap_mod  # noqa: E402
from voice_typer.server.clipboard_snapshot import (  # noqa: E402
    _BUILTIN_FORMAT_NAMES,
    ClipboardSnapshot,
    _builtin_format_name,
)

# ---------------------------------------------------------------------------
# Windows builtin format-name lookup (pure data, no platform dep)
# ---------------------------------------------------------------------------


class TestBuiltinFormatName:
    """Verify the builtin format ID → name mapping."""

    def test_builtin_format_name(self):
        """``_builtin_format_name`` returns the standard CF_* name."""
        # CF_TEXT = 1
        assert _builtin_format_name(1) == "CF_TEXT"
        # CF_UNICODETEXT = 13
        assert _builtin_format_name(13) == "CF_UNICODETEXT"
        # CF_DIB = 8
        assert _builtin_format_name(8) == "CF_DIB"
        # CF_HDROP = 15 (file list)
        assert _builtin_format_name(15) == "CF_HDROP"
        # Unknown ID → empty string (not None, not raise)
        assert _builtin_format_name(99999) == ""

    def test_builtin_format_name_mapping_covers_all_standard_formats(self):
        """The mapping covers the documented standard clipboard formats."""
        # ADR-0010 §4.2: every ID in _BUILTIN_FORMAT_NAMES has a name.
        assert isinstance(_BUILTIN_FORMAT_NAMES, dict)
        assert all(isinstance(k, int) for k in _BUILTIN_FORMAT_NAMES)
        assert all(isinstance(v, str) and v.startswith("CF_") for v in _BUILTIN_FORMAT_NAMES.values())
        # Spot-check a few important formats are present.
        for required_id in (1, 13, 8, 15, 17):
            assert required_id in _BUILTIN_FORMAT_NAMES


# ---------------------------------------------------------------------------
# Windows _capture_windows failure paths
# ---------------------------------------------------------------------------


_WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32" and "CI_MOCK_WIN32" not in os.environ,
    reason="Windows-only capture path (requires ctypes.windll mock)",
)


def _install_fake_windll(user32: MagicMock) -> MagicMock:
    """Install a fake ``ctypes.windll`` exposing ``user32``/``kernel32``.

    Returns the windll mock so callers can configure ``kernel32`` too.
    """
    windll = MagicMock()
    windll.user32 = user32
    windll.kernel32 = MagicMock()
    return windll


class TestCaptureWindowsFailures:
    """``ClipboardSnapshot._capture_windows`` failure paths.

    These tests run on Linux by mocking ``ctypes.windll`` so the Win32
    ctypes calls resolve to MagicMocks. They install the
    ``CI_MOCK_WIN32`` marker so the skip guard above lets them run.
    """

    @pytest.fixture(autouse=True)
    def _enable_win32_mock(self):
        # Allow the skipif guard to let these tests run on Linux.
        prev = os.environ.get("CI_MOCK_WIN32")
        os.environ["CI_MOCK_WIN32"] = "1"
        # Ensure the snapshot module thinks we're on Windows.
        with patch.object(snap_mod, "is_windows", return_value=True):
            yield
        if prev is None:
            os.environ.pop("CI_MOCK_WIN32", None)
        else:
            os.environ["CI_MOCK_WIN32"] = prev

    def test_capture_returns_none_on_locked_clipboard(self):
        """OpenClipboard returning 0 → capture returns None."""
        user32 = MagicMock()
        user32.OpenClipboard.return_value = 0  # locked / unavailable

        windll = _install_fake_windll(user32)
        with patch("ctypes.windll", windll, create=True), patch("ctypes.create_unicode_buffer"):
            result = ClipboardSnapshot._capture_windows()
        assert result is None
        user32.OpenClipboard.assert_called_once_with(0)
        # CloseClipboard may or may not be called when OpenClipboard fails —
        # the production code's try/finally only closes if open succeeded.
        # So we don't assert on CloseClipboard here.

    def test_capture_returns_none_on_empty_clipboard(self):
        """EnumClipboardFormats returning 0 immediately → empty clipboard → None."""
        user32 = MagicMock()
        user32.OpenClipboard.return_value = 1  # opened
        user32.EnumClipboardFormats.return_value = 0  # no formats

        windll = _install_fake_windll(user32)
        with patch("ctypes.windll", windll, create=True), patch("ctypes.create_unicode_buffer"):
            result = ClipboardSnapshot._capture_windows()
        assert result is None
        # CloseClipboard called once because OpenClipboard succeeded.
        user32.CloseClipboard.assert_called_once()

    def test_capture_skips_format_exceeding_size_cap(self):
        """A format whose GlobalSize exceeds the cap is skipped (no
        ``string_at`` copy), protecting Python heap from pathological
        clipboard payloads (200 MB RTF blob, oversized private data).
        """
        from voice_typer.server.clipboard_snapshot import _MAX_FORMAT_BYTES

        user32 = MagicMock()
        user32.OpenClipboard.return_value = 1
        # First call returns CF_UNICODETEXT (13); second returns 0 (end).
        user32.EnumClipboardFormats.side_effect = [13, 0]
        # GetClipboardFormatNameW returns 0 for builtins (CF_UNICODETEXT
        # is builtin → name lookup falls back to _builtin_format_name).
        user32.GetClipboardFormatNameW.return_value = 0
        user32.GetClipboardData.return_value = 1234  # non-zero handle

        kernel32 = MagicMock()
        # Report a size just over the cap — should be skipped.
        kernel32.GlobalSize.return_value = _MAX_FORMAT_BYTES + 1

        windll = MagicMock()
        windll.user32 = user32
        windll.kernel32 = kernel32

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.create_unicode_buffer"),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = ClipboardSnapshot._capture_windows()

        # No items captured → None (empty clipboard contract).
        assert result is None
        # GlobalLock was NOT called (skipped before locking).
        kernel32.GlobalLock.assert_not_called()
        # A debug log was emitted explaining the skip.
        mock_log.debug.assert_called_once()
        log_msg = mock_log.debug.call_args[0][0]
        assert "exceeds" in log_msg or "cap" in log_msg

    def test_capture_keeps_format_under_size_cap(self):
        """A format whose GlobalSize is under the cap is captured
        normally (GlobalLock + string_at copy runs).
        """
        from voice_typer.server.clipboard_snapshot import _MAX_FORMAT_BYTES

        user32 = MagicMock()
        user32.OpenClipboard.return_value = 1
        user32.EnumClipboardFormats.side_effect = [13, 0]
        user32.GetClipboardFormatNameW.return_value = 0
        user32.GetClipboardData.return_value = 1234

        kernel32 = MagicMock()
        kernel32.GlobalSize.return_value = _MAX_FORMAT_BYTES  # exactly at cap (inclusive)
        kernel32.GlobalLock.return_value = 5678  # non-zero pointer
        kernel32.GlobalUnlock.return_value = 1

        windll = MagicMock()
        windll.user32 = user32
        windll.kernel32 = kernel32

        payload = b"hello world" * 100  # arbitrary bytes under cap
        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.create_unicode_buffer"),
            patch("ctypes.string_at", return_value=payload),
        ):
            result = ClipboardSnapshot._capture_windows()

        # Captured exactly one item (CF_UNICODETEXT under the cap).
        assert result is not None
        assert len(result.items) == 1
        fmt, name, data = result.items[0]
        assert fmt == 13
        assert name == "CF_UNICODETEXT"
        assert data == payload
        # GlobalLock + string_at were both called (normal path).
        kernel32.GlobalLock.assert_called_once_with(1234)
        kernel32.GlobalUnlock.assert_called_once_with(1234)


# ---------------------------------------------------------------------------
# Platform dispatch in restore()
# ---------------------------------------------------------------------------


class TestRestorePlatformDispatch:
    """``ClipboardSnapshot.restore`` dispatches on ``self.platform``."""

    def test_restore_dispatches_on_platform(self):
        """``platform='linux-x11'`` routes to ``_restore_x11``."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain", b"hello")],
            captured_at=0.0,
        )
        with patch.object(snap, "_restore_x11", return_value=True) as mock_x11:
            result = snap.restore()
        assert result is True
        mock_x11.assert_called_once()

    def test_restore_unknown_platform_returns_false(self):
        """An unknown platform tag → restore returns False + warning."""
        snap = ClipboardSnapshot(
            platform="plan9",  # not a real platform tag
            items=[("text/plain", b"hello")],
            captured_at=0.0,
        )
        with patch.object(snap_mod, "log") as mock_log:
            result = snap.restore()
        assert result is False
        # A warning was logged about the unknown platform.
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Linux X11 / Wayland capture failure paths
# ---------------------------------------------------------------------------


class TestCaptureLinuxMissingTool:
    """Capture returns None when xclip / wl-paste is missing.

    Documents ADR-0010 §4.5 / §4.6 limitation: text-only on Linux, and
    requires xclip / wl-paste to be installed. When the tool is absent
    (FileNotFoundError), capture returns None for that target.
    """

    def test_capture_x11_returns_none_when_xclip_missing(self):
        """xclip not installed → _capture_x11 returns None."""
        import subprocess

        with patch.object(subprocess, "run", side_effect=FileNotFoundError):
            result = ClipboardSnapshot._capture_x11()
        assert result is None

    def test_capture_wayland_returns_none_when_wl_paste_missing(self):
        """wl-paste not installed → _capture_wayland returns None."""
        import subprocess

        with patch.object(subprocess, "run", side_effect=FileNotFoundError):
            result = ClipboardSnapshot._capture_wayland()
        assert result is None


# ---------------------------------------------------------------------------
# Linux X11 / Wayland restore with empty items
# ---------------------------------------------------------------------------


class TestRestoreLinuxEmptyItems:
    """``_restore_x11`` / ``_restore_wayland`` short-circuit on empty items.

    ADR-0010 §4.5 / §4.6: when the snapshot has no items (e.g. capture
    returned an empty list), restore returns True without invoking the
    CLI tool. This makes the restore a no-op for snapshots of empty
    clipboards, which is the safe behavior.
    """

    def test_restore_x11_with_empty_items_returns_true(self):
        snap = ClipboardSnapshot(platform="linux-x11", items=[], captured_at=0.0)
        # subprocess.run should NOT be called when items is empty.
        import subprocess

        with patch.object(subprocess, "run") as mock_run:
            result = snap._restore_x11()
        assert result is True
        mock_run.assert_not_called()

    def test_restore_wayland_with_empty_items_returns_true(self):
        snap = ClipboardSnapshot(platform="linux-wayland", items=[], captured_at=0.0)
        import subprocess

        with patch.object(subprocess, "run") as mock_run:
            result = snap._restore_wayland()
        assert result is True
        mock_run.assert_not_called()
