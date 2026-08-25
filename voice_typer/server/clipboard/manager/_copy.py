"""CopyMixin — snapshot-capture + copy + verify + monitor-exclusion.

Split verbatim out of the pre-split ``clipboard/manager.py`` module.

Design contract preserved from the original monolith: copy() ALWAYS
puts text on the clipboard. All patchable symbols are looked up via the
PACKAGE (``_cb.X``) at call time — NOT via this module's globals — so
test patches like
``patch.object(clip_mod, "is_windows", return_value=True)`` actually
take effect on the code paths in this module.
"""

from __future__ import annotations

import contextlib

from voice_typer.server import clipboard as _cb
from voice_typer.server.clipboard.manager._errors import ClipboardCopyError
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot


class CopyMixin:
    """Snapshot + copy mixin for :class:`ClipboardManager`."""

    def copy(self, text: str) -> ClipboardSnapshot | None:
        """Copy text to the clipboard. Returns a snapshot of the prior content.

        ADR-0010 §5.2 / DP4 / DP7.

        Returns ``None`` if:
          * ``text`` is empty (no-op).
          * ``clipboard_save_restore`` is disabled (no snapshot captured).

        Returns a :class:`ClipboardSnapshot` (which may have an empty
        ``items`` list if the clipboard was empty) when snapshot capture
        succeeds. The caller is responsible for restoring the snapshot
        after the text has been consumed — see :meth:`paste` and
        :meth:`restore_now`.

        Raises :class:`ClipboardCopyError` if the text copy/verify fails
        after retries. The snapshot, if captured, is restored before
        raising so the clipboard is never left torn. The caller should
        write the transcription to crash recovery on this exception.
        """
        if not text:
            return None

        # ① SNAPSHOT (gated by config flag — DP7). The snapshot is a
        # value returned to the caller; it is NOT stored on self. This
        # makes overlapping cycles safe (DP4).
        snapshot: ClipboardSnapshot | None = None
        if self._clipboard_save_restore_enabled:
            snapshot = ClipboardSnapshot.capture()
            # snapshot may be None if capture failed (clipboard locked
            # or empty). That's OK — we just won't restore. Log for
            # debugging.
            if snapshot is None:
                _cb.log.debug("[CLIPBOARD] Snapshot capture returned None (clipboard locked or empty)")

        try:
            # ② WIN32 EMPTY (existing ). On Windows, empty the
            # clipboard before copying to clear rich text artifacts from
            # the previous clipboard content. : uses
            # Win32Clipboard abstraction instead of direct ctypes calls.
            _cb._win32_empty_clipboard()

            # ③ COPY TEXT (existing  retry on ERROR_ACCESS_DENIED).
            # ADR-0020 §6.6: on Linux Wayland, route through _copy_to_clipboard
            # which uses `wl-copy` instead of pyperclip's xclip/xsel (which
            # are X11-only and silently no-op under native Wayland apps).
            for attempt in range(3):
                try:
                    _cb._copy_to_clipboard(text)
                    break
                except OSError as copy_err:
                    # ERROR_ACCESS_DENIED = 5 on Windows. pyperclip wraps
                    # the underlying win32 clipboard error as OSError or
                    # pywintypes.error (which is a subclass of OSError).
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
                        "[CLIPBOARD] Clipboard verification failed (attempt %d/3) — expected %d chars, got %d.",
                        verify_attempt + 1,
                        len(text),
                        len(actual) if actual else 0,
                    )
                    _cb._copy_to_clipboard(text)
                except (ImportError, AttributeError, NotImplementedError, OSError):
                    # narrowed from bare ``except Exception: pass``.
                    # ``_paste_from_clipboard`` may raise ImportError if
                    # pyperclip is missing, AttributeError / NotImplemented
                    # if the platform backend lacks paste support, or
                    # OSError on a Win32 / wl-paste failure. All are
                    # non-fatal — verification is best-effort.
                    _cb.log.debug(
                        "[CLIPBOARD] verify attempt %d failed",
                        verify_attempt,
                        exc_info=True,
                    )
            else:
                _cb.log.error("[CLIPBOARD] Clipboard verification still failed after 3 retries")

            # ④b (Privacy): tag the clipboard with the Windows
            # monitor-exclusion format
            # (``ExcludeClipboardContentFromMonitorProcessing``) so the
            # dictated text is NOT added to the Windows clipboard
            # history (Win+V), NOT synced via Cloud Clipboard, and NOT
            # indexed by conforming third-party clipboard monitors
            # (Microsoft PowerToys' Clipboard Manager, MDM-managed
            # clipboard providers). The helper is best-effort — a
            # failure leaves the dictated text in the clipboard history
            # (the pre-fix behavior), which is the safe degraded mode.
            # The tag is set AFTER the verify loop (which may re-copy
            # and re-empty the clipboard via ``pyperclip.copy``) so the
            # tag is the LAST format set and survives the copy() return.
            if _cb.is_windows():
                with contextlib.suppress(Exception):
                    _cb._win32_exclude_clipboard_from_monitoring()

            # ⑤ STORE METADATA (existing PLAT-CLIPRACE / PLAT-SECURE).
            #
            #  (session-DE, Medium, Privacy): only cache
            # ``_last_copied_text`` when a snapshot was captured (i.e.
            # a restore IS scheduled, whose ``_delayed_restore`` finally
            # block will clear it after the restore-delay window).
            # When ``snapshot is None`` (clipboard_save_restore disabled
            # OR capture failed), no restore will be scheduled, so
            # caching the dictated text here would leak PII (which can
            # be passwords, messages, financial data — anything the
            # user dictated) into process memory for the entire process
            # lifetime. The seq-mismatch re-copy path in ``paste()``
            # threads ``pasted_text`` as a request-scoped value
            # parameter (), so it does not depend on the instance
            # attribute when ``snapshot is None``. The Wayland paste
            # call sites also thread ``pasted_text`` (). Defensive
            # clear of any stale value from a prior cycle.
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
            # leave the clipboard in a torn state, then signal failure.
            if snapshot is not None:
                with contextlib.suppress(Exception):
                    snapshot.restore()
            raise ClipboardCopyError(str(e)) from e
