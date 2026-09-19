"""Shared ClipboardManager / ClipboardSnapshot test factories."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from voice_typer.server.clipboard import ClipboardManager
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

__all__ = ["make_clipboard_manager", "make_clipboard_snapshot"]


def make_clipboard_manager(
    *,
    paste_enabled: bool = True,
    save_restore: bool = True,
    restore_delay_ms: int = 150,
    last_copied_text: str = "",
) -> ClipboardManager:
    """Build a ClipboardManager with mocked keyboard and cached flags set."""
    cm = ClipboardManager.__new__(ClipboardManager)
    cm.paste_enabled = paste_enabled
    cm._keyboard = MagicMock()
    cm._last_paste_time = 0.0  # not rate-limited
    cm._clipboard_seq = 0
    cm._last_copied_text = last_copied_text
    cm._clipboard_save_restore_enabled = save_restore
    cm._restore_delay_ms = restore_delay_ms
    return cm


def make_clipboard_snapshot(
    *,
    platform: str = "linux-x11",
    content_type: str = "text/plain",
    payload: bytes = b"prior clipboard content",
) -> ClipboardSnapshot:
    """Build a fake ClipboardSnapshot for tests that need a non-None value."""
    return ClipboardSnapshot(
        platform=platform,
        items=[(content_type, payload)],
        captured_at=time.monotonic(),
    )
