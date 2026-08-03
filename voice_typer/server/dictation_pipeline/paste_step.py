"""Clipboard copy + paste step mixin.

Holds Step 9 of the dictation pipeline:

  * :meth:`_copy_and_paste` — copy the transcription to the clipboard
    (snapshot/restore cycle for save/restore mode), attempt paste if
    ``paste_on_stop`` is on, mark the crash-recovery entry as pasted,
    and tear down the bubble + tray status. Includes the
    clipboard-failure recovery path (write to crash-recovery + surface
    a ``paste_failed`` event so the renderer can show a toast with an
    "Open recovery file" action).

The snapshot/restore cycle is explicit at the call site (not hidden
inside ``copy()`` / ``paste()``) so the borrow/restore pairing is
visible. Optimization (ADR-0010 §9.2): when ``paste_on_stop`` is OFF
AND ``clipboard_save_restore`` is ON, the clipboard borrow is skipped
entirely — the transcription is already in the DB and reachable via
the repaste hotkey.

Originally an inline method on ``DictationPipeline`` in the 2077-LOC
monolith; extracted as a mixin with NO behavior change.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class _PasteStepMixin:
    """Mixin: clipboard copy + paste step."""

    def _copy_and_paste(self, text: str) -> None:
        """Step 9: Copy to clipboard and attempt paste.

        ADR-0010 §6.1 / DP1 / DP2 / DP4.

        The snapshot/restore cycle is explicit here (not hidden inside
        copy()/paste()) so the borrow/restore pairing is visible at the
        call site. This is the single place that orchestrates the
        clipboard borrow lifecycle.

        If clipboard.copy() fails, we previously lost the
        transcription silently. We now write the text to the crash
        recovery buffer (which persists to disk) and notify the user
        with the path so they can recover it manually.

        Optimization (ADR-0010 §6.1 / §9.2): if ``paste_on_stop`` is OFF
        and ``clipboard_save_restore`` is ON, we would copy the
        transcription and instantly restore the user's clipboard — a
        redundant clipboard lock round-trip (and its error surface) for
        zero benefit. Skip the clipboard entirely; the transcription is
        already persisted to the DB by ``_store_result()`` and reachable
        via the repaste hotkey. We only skip the clipboard borrow here
        — the UI teardown below (bubble/tray/timer) still runs.
        """
        # ── OPTIMIZATION (§9.2): skip the clipboard borrow entirely when
        #    paste_on_stop is OFF and save/restore is ON. The
        #    transcription is already in the DB; touching the clipboard
        #    would only add a redundant lock round-trip.
        skip_clipboard = not self._app.config.paste_on_stop and self._app.config.clipboard_save_restore

        pasted = False
        snapshot = None
        if not skip_clipboard:
            # ① COPY — returns snapshot (or None when save/restore is
            #    disabled). Raises ClipboardCopyError on genuine copy
            #    failure (caller writes to crash recovery).
            try:
                snapshot = self._app.clipboard.copy(text)
                paste_seq = self._app.clipboard._clipboard_seq
            except ClipboardCopyError:
                log.error("[CLIPBOARD] Clipboard copy failed (cycle=%s)", self._cycle_id)
                recovery_path: str | None = None
                try:
                    if self._app.config.crash_recovery_enabled:
                        self._app._crash_recovery.add(text, pasted=False)
                        self._app._crash_recovery.flush(timeout=2.0)
                        # Best-effort: surface the recovery file path so the
                        # user can locate the saved transcription.
                        try:
                            recovery_path = str(self._app._crash_recovery._path)
                        except Exception:
                            recovery_path = None
                except Exception:
                    log.exception("[CLIPBOARD] Failed to write transcription to crash recovery")
                # NEW-BUBBLE-TRANSCRIBING: Hide the bubble since the
                # transcription is done (even though paste failed).
                self._hide_or_idle_bubble("bubble hide on clipboard fail")
                self._app.tray.set_state(AppState.IDLE, "Done -- clipboard unavailable")
                notice = (
                    "Transcription complete, but the clipboard was unavailable.\n"
                    "Your text was saved to the crash-recovery file so it is not lost."
                )
                if recovery_path:
                    notice += f"\nRecovery file: {recovery_path}"
                self._app.tray.notify(APP_NAME, notice)
                # surface the paste failure as a renderer
                # toast in ADDITION to the tray notification (keep both
                # for redundancy — the tray icon tooltip is visible when
                # the user is on another app; the toast is visible when
                # the renderer has focus). The renderer subscribes to
                # the ``paste_failed`` event via usePythonEvent and shows
                # a sonner toast with an "Open recovery file" action
                # button when ``recovery_path`` is present. Wrapped in
                # try/except so a broken event bus never aborts the
                # clipboard-failure recovery path (existing tray notify
                # + crash-recovery write must still complete).
                try:
                    from voice_typer.server import event_bus

                    event_bus.publish(
                        {
                            "type": "paste_failed",
                            "data": {
                                "message": notice,
                                "recovery_path": recovery_path,
                            },
                        }
                    )
                except Exception:
                    log.debug(
                        "[PIPELINE] could not publish paste_failed event",
                        exc_info=True,
                    )
                self._app._busy_event.set()
                self._app._schedule_timer(
                    3.0,
                    lambda: self._app.tray.set_state(AppState.IDLE, f"Ready -- {self._device_info}"),
                )
                return
            # ② PASTE (if enabled) — paste() schedules the restore thread
            #    at its top, before any early return (DP1). pasted_text
            #    is passed as a value so overlapping cycles stay isolated (DP4).
            if self._app.config.paste_on_stop:
                pasted = self._app.clipboard.paste(snapshot, pasted_text=text, pasted_seq=paste_seq)
            else:
                # paste_on_stop is False + save/restore OFF: leave the
                # transcription on the clipboard for the user to paste
                # manually (legacy behavior). copy() returned None (no
                # snapshot captured), so there is nothing to restore —
                # the user's original content was never captured.
                log.info(
                    "[CLIPBOARD-AUDIT] paste_on_stop=False + save/restore off — "
                    "transcription left on clipboard for manual paste"
                )
        else:
            log.info(
                "[CLIPBOARD-AUDIT] paste_on_stop=False + save/restore on — "
                "clipboard untouched; transcription persisted to DB"
            )

        # ③ Mark crash recovery as pasted (if applicable)
        if pasted and self._app.config.crash_recovery_enabled:
            with contextlib.suppress(Exception):
                self._app._crash_recovery.mark_latest_pasted()

        # ④ Status + tray + bubble (existing lines 675–692, unchanged)
        if pasted:
            status = f"Done -- {len(text)} chars (pasted)"
        elif skip_clipboard:
            status = f"Done -- {len(text)} chars (in DB, use repaste hotkey)"
        else:
            # paste_on_stop=False + save/restore off: legacy "left on clipboard"
            status = f"Done -- {len(text)} chars (in clipboard)"

        # NEW-BUBBLE-TRANSCRIBING: Transcription + paste complete — hide the
        # bubble (or set it to idle for always_visible mode) so the overlay
        # doesn't persist on screen after the user has their result.
        self._hide_or_idle_bubble("bubble hide/set idle")

        self._app.tray.set_state(AppState.IDLE, status)
        self._app._schedule_timer(
            3.0,
            lambda: self._app.tray.set_state(AppState.IDLE, f"Ready -- {self._device_info}"),
        )
