"""ClipboardCopyError: distinguishes "copy failed" from "save/restore disabled"."""

from __future__ import annotations


class ClipboardCopyError(RuntimeError):
    """Raised when ``ClipboardManager.copy()`` fails to write text to the"""
