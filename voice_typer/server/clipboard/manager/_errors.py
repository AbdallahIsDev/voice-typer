"""ClipboardCopyError — distinguishes "copy failed" from "save/restore disabled"."""

from __future__ import annotations


class ClipboardCopyError(RuntimeError):
    """Raised when ``ClipboardManager.copy()`` fails to write text to the
    clipboard after retries.

    ADR-0010 §5.2: distinguishes "copy failed" (caller should write to
    crash recovery) from "save/restore disabled" (caller skips the
    clipboard). The snapshot, if captured, is restored before raising
    so the clipboard is never left torn.
    """
