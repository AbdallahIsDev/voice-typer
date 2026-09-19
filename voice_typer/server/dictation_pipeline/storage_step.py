"""History storage stage."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class _StorageStepMixin:
    """Mixin: history DB + crash-recovery storage step."""

    # Set by ``DictationPipeline.__init__`` (``app: Any``). Declared on
    _app: Any
    # Same provision pattern: assigned by ``_OrchestratorMixin.__init__``
    _cycle_id: str
    _duration: float

    def _store_result(self, text: str) -> None:
        """Step 8: Store in history DB and crash recovery."""
        # gate the entire history-DB block on history_enabled.
        history_enabled = getattr(self._app.config, "history_enabled", True)
        if history_enabled:
            try:
                row_id = self._app.history_db.add_transcription(
                    text,
                    duration=self._duration,
                    model=self._app.config.model_size,
                    device=self._app.config.device,
                )
                # add_transcription returns -1 when the writer
                if row_id <= 0:
                    raise RuntimeError(
                        "history_db.add_transcription returned a non-positive row_id "
                        f"({row_id}), writer is unavailable; transcription was NOT persisted"
                    )
                # NO blocking flush on the paste path, the history row
            except Exception:
                log.exception("[PIPELINE] History DB add failed")
                # a-review Finding 2: notify-once flag lives on ``self._app``
                if not getattr(self._app, "_history_fail_notified", False):
                    self._app._history_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "Could not save the transcription to history. Check the log file for details.",
                        )

        if self._app.config.crash_recovery_enabled:
            try:
                # Correlate this entry with the dictation cycle so a
                self._app._crash_recovery.add(text, pasted=False, cycle_id=self._cycle_id)
                # CRASH-SAFE-GAP-B: flush the crash recovery file immediately
                self._app._crash_recovery.flush(timeout=0.5)
            except Exception:
                log.exception("[PIPELINE] Crash recovery add failed")
                # a-review Finding 2: notify-once flag lives on ``self._app``
                if not getattr(self._app, "_crash_recovery_fail_notified", False):
                    self._app._crash_recovery_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "Could not save the transcription to the crash-recovery "
                            "buffer. Check the log file for details.",
                        )

        # Per-correction usage tracking: count this completed dictation
        try:
            self._app.correction_usage.record_dictation()
        except Exception:
            log.warning("[PIPELINE] Failed to record dictation usage", exc_info=True)

        # Save for repaste / undo
        self._app._last_transcription = text

        # Emit transcription_final so renderer can refresh without polling.
        try:
            from voice_typer.server import event_bus

            event_data: dict[str, Any] = {"text": text[:200]}
            quality = getattr(self, "_quality_summary", None)
            if isinstance(quality, dict):
                event_data["quality"] = quality
            event_bus.publish(
                {
                    "type": "transcription_final",
                    "data": event_data,
                }
            )
        except Exception:
            # Non-fatal: paste already succeeded; UI may show stale history.
            log.debug(
                "[PIPELINE] could not publish transcription_final event",
                exc_info=True,
            )

        if self._app.config.log_transcriptions:
            # Previously the first 200 characters of the

            # We now log a non-reversible 12-char SHA-256 prefix of
            import hashlib

            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            log.info("[TRANSCRIBE] Transcription: hash=%s len=%d", text_hash, len(text))
        else:
            log.info("[TRANSCRIBE] Transcription: %d chars", len(text))
