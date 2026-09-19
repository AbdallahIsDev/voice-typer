"""CopyMixin, snapshot-capture + copy + verify + monitor-exclusion."""

from __future__ import annotations

import contextlib

from voice_typer.server import clipboard as _cb
from voice_typer.server.clipboard.manager._errors import ClipboardCopyError
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot


class CopyMixin:
    """Snapshot + copy mixin for :class:`ClipboardManager`."""

    def copy(self, text: str) -> ClipboardSnapshot | None:
        """Copy text to the clipboard. Returns a snapshot of the prior content.

        Returns ``None`` if:
        """
        if not text:
            return None

        # ① SNAPSHOT (gated by config flag. DP7). The snapshot is a
        snapshot: ClipboardSnapshot | None = None
        if self._clipboard_save_restore_enabled:
            snapshot = ClipboardSnapshot.capture()
            # snapshot may be None if capture failed (clipboard locked
            if snapshot is None:
                _cb.log.debug("[CLIPBOARD] Snapshot capture returned None (clipboard locked or empty)")

        try:
            # ② WIN32 EMPTY (existing ). On Windows, empty the
            _cb._win32_empty_clipboard()

            # ③ COPY TEXT (existing  retry on ERROR_ACCESS_DENIED).
            for attempt in range(3):
                try:
                    _cb._copy_to_clipboard(text)
                    break
                except OSError as copy_err:
                    # ERROR_ACCESS_DENIED = 5 on Windows. pyperclip wraps
                    winerror = getattr(copy_err, "winerror", None)
                    if winerror == 5 and attempt < 2:
                        _cb.time.sleep(0.05 * (attempt + 1))
                        continue
                    raise copy_err

            # ④ VERIFY (existing PLAT-PASTEVR).
            for verify_attempt in range(3):
                try:
                    actual = _cb._paste_from_clipboard()
                    if actual == text:
                        break
                    _cb.log.warning(
                        "[CLIPBOARD] Clipboard verification failed (attempt %d/3), expected %d chars, got %d.",
                        verify_attempt + 1,
                        len(text),
                        len(actual) if actual else 0,
                    )
                    _cb._copy_to_clipboard(text)
                except (ImportError, AttributeError, NotImplementedError, OSError):
                    # narrowed from bare ``except Exception: pass``.
                    _cb.log.debug(
                        "[CLIPBOARD] verify attempt %d failed",
                        verify_attempt,
                        exc_info=True,
                    )
            else:
                _cb.log.error("[CLIPBOARD] Clipboard verification still failed after 3 retries")

            # ④b (Privacy): tag the clipboard with the Windows
            if _cb.is_windows():
                with contextlib.suppress(Exception):
                    _cb._win32_exclude_clipboard_from_monitoring()

            # ⑤ STORE METADATA (existing PLAT-CLIPRACE / PLAT-SECURE).
            if snapshot is not None:
                self._last_copied_text = text
            else:
                self._last_copied_text = ""
            self._clipboard_seq = self._get_clipboard_sequence_number()
            _cb.log.info(
                "[CLIPBOARD-AUDIT] Copied %d chars to clipboard (seq=%d, snapshot=%s)",
                len(text),
                self._clipboard_seq,
                "captured" if snapshot is not None else "none",
            )
            return snapshot

        except Exception as e:
            _cb.log.error("[CLIPBOARD] Failed to copy to clipboard: %s", e)
            # If copy failed, restore the snapshot immediately so we don't
            if snapshot is not None:
                with contextlib.suppress(Exception):
                    snapshot.restore()
            raise ClipboardCopyError(str(e)) from e
