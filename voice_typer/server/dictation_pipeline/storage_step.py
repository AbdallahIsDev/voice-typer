"""History DB / crash-recovery storage step mixin.

Holds Step 8 of the dictation pipeline:

  * :meth:`_store_result` — persist the transcription to the history
    DB (gated on ``config.history_enabled``), the crash-recovery
    buffer (gated on ``config.crash_recovery_enabled``), and the
    ``_last_transcription`` slot (for repaste / undo). Also publishes
    a ``transcription_final`` push event so the renderer can refresh
    Home / Dashboard / History proactively, and logs a non-reversible
    SHA-256 prefix of the text when ``config.log_transcriptions`` is
    on (so operators can correlate cycles in the log without leaking
    user content).

Failures here use the same notify-once pattern as the text /
enhancement steps: ``log.exception`` + a session-scoped flag on
``self._app`` + tray notification on the FIRST occurrence. The
transcription itself is still delivered to the user (the storage
failure does NOT abort the paste step that runs next).

Originally an inline method on ``DictationPipeline`` in the 2077-LOC
monolith; extracted as a mixin with NO behavior change.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class _StorageStepMixin:
    """Mixin: history DB + crash-recovery storage step."""

    # Set by ``DictationPipeline.__init__`` (``app: Any``). Declared on
    # the mixin so pyrefly doesn't flag every ``self._app.*`` access —
    # the attribute is provided by the composed parent class at runtime.
    _app: Any

    def _store_result(self, text: str) -> None:
        """Step 8: Store in history DB and crash recovery.

        Previously failures here were DEBUG-level (invisible at
        default log level) with no tray notification. We now log at
        ``exception`` level and surface a tray notice the first time
        each failure type occurs so the user knows data is being lost.

        ADR-0010 §6.2: ``history_db.flush()`` is called after
        ``add_transcription()`` to guarantee the row is committed before
        ``repaste_last()`` could fire. ``flush()`` blocks until the
        writer thread processes all queued writes (FIFO no-op with
        ``wait=True``). See ``history_db.py:flush()``.

         (privacy): if ``self._app.config.history_enabled`` is
        ``False``, the ``add_transcription`` call is skipped entirely
        (but the clipboard paste still happens — incognito mode only
        disables persistence, not the dictation flow). ``flush()`` is
        also skipped because there is no queued write to wait for.
        ``history_enabled`` defaults to ``True`` (preserving the
        pre- behavior) so the field is only consulted when P4-A2
        has added it to ``Config``. ``getattr(..., True)`` is used so
        dictation still works on an older Config instance that hasn't
        yet picked up the new field.

         (resilience): when ``add_transcription`` returns ``<= 0``
        (writer thread is dead or schema init failed — see
        ``history_db.add_transcription``'s  guard), we log +
        trigger the notify-once tray message instead of silently
        treating the placeholder as success. Previously the pipeline
        would call ``flush()`` after the failed enqueue and block 30s
        on a future that would never resolve — the  fix in
        ``history_db._submit_write`` makes the failure instant, and
        this check makes it visible to the user.
        """
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
                # thread is dead or schema init failed (see its
                # guard). Surface the failure to the user via the
                # notify-once path instead of silently treating the
                # placeholder as success.
                if row_id <= 0:
                    raise RuntimeError(
                        "history_db.add_transcription returned a non-positive row_id "
                        f"({row_id}) — writer is unavailable; transcription was NOT persisted"
                    )
                # ADR-0010 §6.2: flush to guarantee the row is committed
                # before repaste could fire. flush() blocks until the writer
                # thread processes all queued writes (FIFO no-op with
                # wait=True). See history_db.py:flush().
                self._app.history_db.flush()
            except Exception:
                log.exception("[PIPELINE] History DB add failed")
                # a-review Finding 2: notify-once flag lives on ``self._app``
                # (session-scoped) — see ``_apply_vocabulary`` for rationale.
                if not getattr(self._app, "_history_fail_notified", False):
                    self._app._history_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "Could not save the transcription to history. Check the log file for details.",
                        )

        if self._app.config.crash_recovery_enabled:
            try:
                self._app._crash_recovery.add(text, pasted=False)
                # CRASH-SAFE-GAP-B: flush the crash recovery file immediately
                # so the transcription is on disk before the pipeline function
                # returns. crash_recovery.add() is async (enqueues to a
                # background save thread). If the app crashes in the ~50ms
                # window before the save thread processes the request, the
                # latest transcription is not in the recovery file and would
                # be lost. Flushing with a short (0.5s) timeout ensures it
                # hits disk before we return, at negligible latency cost
                # since the save queue is nearly always empty.
                self._app._crash_recovery.flush(timeout=0.5)
            except Exception:
                log.exception("[PIPELINE] Crash recovery add failed")
                # a-review Finding 2: notify-once flag lives on ``self._app``
                # (session-scoped) — see ``_apply_vocabulary`` for rationale.
                if not getattr(self._app, "_crash_recovery_fail_notified", False):
                    self._app._crash_recovery_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "Could not save the transcription to the crash-recovery "
                            "buffer. Check the log file for details.",
                        )

        # Per-correction usage tracking: count this completed dictation
        # (denominator for the Analytics corrections-applied rate). Runs
        # regardless of ``history_enabled`` — the vocabulary corrections
        # fire even in incognito mode, so the rate's denominator must
        # count every dictation. The tracker lives on the shared
        # ``_vocabulary_manager`` (same config dir) and swallows its own
        # errors, so this can never break the dictation path.
        try:
            self._app.correction_usage.record_dictation()
        except Exception:
            log.warning("[PIPELINE] Failed to record dictation usage", exc_info=True)

        # Save for repaste / undo
        self._app._last_transcription = text

        # emit transcription_final push event so the
        # renderer can proactively refresh Home/Dashboard/History
        # without polling.
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "transcription_final",
                    "data": {"text": text[:200]},  # truncated for UI preview
                }
            )
        except Exception:
            # previously a bare ``except Exception: pass``. If
            # the event bus is broken, the renderer never receives the
            # ``transcription_final`` push event — Home / Dashboard /
            # History pages won't auto-refresh and the user sees stale
            # data. Log at DEBUG (this is non-fatal — the transcription
            # was already pasted; only the proactive refresh is lost)
            # so an issue with the event bus is at least visible in the
            # log file when debugging UI staleness.
            log.debug(
                "[PIPELINE] could not publish transcription_final event",
                exc_info=True,
            )

        if self._app.config.log_transcriptions:
            # Previously the first 200 characters of the
            # transcription text were logged after running through
            # ``redact_pii()``.  ``redact_pii()`` only masks four
            # patterns (email / US-phone / SSN / credit-card-like) —
            # medical dictation, financial narratives, addresses, and
            # names passed through verbatim.  For a voice-typing tool
            # this is the primary PII surface.

            # We now log a non-reversible 12-char SHA-256 prefix of
            # the transcription text.  This preserves log-line
            # correlation (the same transcription produces the same
            # hash, so ``[TRANSCRIBE] Transcription: hash=abc… len=123``
            # can be matched against the downstream ``[HISTORY] insert``
            # log line for the same cycle) without leaking any content.
            # ``len(text)`` is also logged so operators can spot
            # suspiciously short / long transcriptions.
            import hashlib

            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            log.info("[TRANSCRIBE] Transcription: hash=%s len=%d", text_hash, len(text))
        else:
            log.info("[TRANSCRIBE] Transcription: %d chars", len(text))
