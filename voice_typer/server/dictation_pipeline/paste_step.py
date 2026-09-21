"""Paste stage for the dictation pipeline."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError
from voice_typer.server.i18n import t as _i18n_t
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class _PasteStepMixin:
    """Mixin: clipboard copy + paste step."""

    # Set by the composing orchestrator (``orchestrator.py:92``); declared
    _device_info: str = ""

    # Set by ``_OrchestratorMixin.__init__`` (``app: Any``). Declared on
    _app: Any
    _cycle_id: str

    # Bubble teardown helper owned by ``_TranscribeStepMixin`` and
    _hide_or_idle_bubble: Callable[..., None]

    def _copy_and_paste(self, text: str) -> None:
        """Step 9: Copy to clipboard and attempt paste."""
        # ── OPTIMIZATION (§9.2): skip the clipboard borrow entirely when
        skip_clipboard = not self._app.config.paste_on_stop and self._app.config.clipboard_save_restore

        pasted = False
        snapshot = None
        if not skip_clipboard:
            # ① COPY, returns snapshot (or None when save/restore is
            try:
                snapshot = self._app.clipboard.copy(text)
                paste_seq = self._app.clipboard._clipboard_seq
            except ClipboardCopyError:
                log.exception("[CLIPBOARD] Clipboard copy failed (cycle=%s)", self._cycle_id)
                recovery_path: str | None = None
                try:
                    if self._app.config.crash_recovery_enabled:
                        self._app._crash_recovery.add(text, pasted=False, cycle_id=self._cycle_id)
                        self._app._crash_recovery.flush(timeout=2.0)
                        # Best-effort: surface the recovery file path so the
                        try:
                            recovery_path = str(self._app._crash_recovery._path)
                        except Exception:
                            recovery_path = None
                except Exception:
                    log.exception("[CLIPBOARD] Failed to write transcription to crash recovery")
                # Hide the bubble since the
                self._hide_or_idle_bubble("bubble hide on clipboard fail")
                self._app.tray.set_state(
                    AppState.IDLE,
                    _i18n_t("state.dictation_pipeline.clipboard_unavailable"),
                )
                notice = _i18n_t("notify.app.clipboard_unavailable_body")
                if recovery_path:
                    notice += "\n" + _i18n_t(
                        "notify.app.clipboard_unavailable_recovery_path",
                        path=recovery_path,
                    )
                self._app.tray.notify(APP_NAME, notice)
                # surface the paste failure as a renderer
                try:
                    from voice_typer.server import event_bus

                    event_bus.publish(
                        {
                            "type": "paste_failed",
                            "data": {
                                "recovery_path": recovery_path,
                            },
                        }
                    )
                except Exception:
                    log.debug(
                        "[PIPELINE] could not publish paste_failed event",
                        exc_info=True,
                    )
                # Routed through the BusynessCoordinator.
                self._app._busyness.set_idle()
                self._app._schedule_timer(
                    3.0,
                    lambda: self._app.tray.set_state(
                        AppState.IDLE,
                        _i18n_t(
                            "state.model_manager.ready_whisper",
                            device_info=self._device_info,
                        ),
                    ),
                )
                return
            # ② PASTE (if enabled), paste() schedules the restore thread
            if self._app.config.paste_on_stop:
                pasted = self._app.clipboard.paste(snapshot, pasted_text=text, pasted_seq=paste_seq)
            else:
                # paste_on_stop is False + save/restore OFF: leave the
                log.info(
                    "[CLIPBOARD-AUDIT] paste_on_stop=False + save/restore off, "
                    "transcription left on clipboard for manual paste"
                )
        else:
            log.info(
                "[CLIPBOARD-AUDIT] paste_on_stop=False + save/restore on, "
                "clipboard untouched; transcription persisted to DB"
            )

        # ③ Mark crash recovery as pasted (if applicable)
        if pasted and self._app.config.crash_recovery_enabled:
            with contextlib.suppress(Exception):
                self._app._crash_recovery.mark_latest_pasted()

        # ④ Status + tray + bubble, localized via the
        if pasted:
            status = _i18n_t(
                "state.dictation_pipeline.done_pasted",
                count=len(text),
            )
        elif skip_clipboard:
            status = _i18n_t(
                "state.dictation_pipeline.done_in_db",
                count=len(text),
            )
        else:
            # paste_on_stop=False + save/restore off: legacy "left on clipboard"
            status = _i18n_t(
                "state.dictation_pipeline.done_in_clipboard",
                count=len(text),
            )

        # Transcription + paste complete, hide the
        self._hide_or_idle_bubble("bubble hide/set idle")

        self._app.tray.set_state(AppState.IDLE, status)
        self._app._schedule_timer(
            3.0,
            lambda: self._app.tray.set_state(
                AppState.IDLE,
                _i18n_t(
                    "state.model_manager.ready_whisper",
                    device_info=self._device_info,
                ),
            ),
        )
