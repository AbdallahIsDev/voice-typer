"""DictationPipeline: extracted from VoiceTyperApp.transcribe_thread.

ARCH-006: the 180-line nested closure in app.py was a god function
that did ALL of: streaming finalize, transcription, text cleanup,
vocabulary correction, template matching, auto-punctuation, LLM
polish, history DB write, crash recovery, clipboard copy, paste,
tray state, notifications, GC, and busy-event clear.

This class breaks the pipeline into testable methods, one per step.
The class holds a reference to the app for accessing config, tray,
history_db, etc. — a full dependency injection refactor is deferred
(ARCH-005's VoiceTyperService is the first step toward that).
"""

import logging
import time
from typing import Optional, Any

from voice_typer.server.tray_types import AppState

log = logging.getLogger("voice_typer")


# ERR-005: raw exception messages from ctranslate2 / torch / faster-whisper
# often leak file paths, CUDA versions, and internal stack details into
# user-facing tray notifications. Map known exception classes to friendly
# messages; fall back to a generic message for unknown errors.
def _friendly_transcription_error(exc: BaseException) -> str:
    """Return a user-friendly message describing a transcription failure."""
    msg = str(exc).lower()
    name = type(exc).__name__
    # GPU / CUDA errors
    if "out of memory" in msg or "cuda" in msg and "memory" in msg:
        return "The GPU ran out of memory while transcribing. Try a smaller model."
    if "cuda" in msg or "cudnn" in msg or "cublas" in msg:
        return "A GPU/CUDA error occurred. The app will fall back to CPU on the next attempt."
    if "device" in msg and ("not available" in msg or "not found" in msg):
        return "The selected audio or compute device is unavailable."
    # Model file errors
    if "model" in msg and ("download" in msg or "load" in msg or "file" in msg):
        return "The speech model could not be loaded. Check your internet connection and try again."
    # Audio errors
    if "audio" in msg and ("empty" in msg or "no speech" in msg):
        return "No speech was detected in the recording."
    if name in {"ConnectionError", "TimeoutError", "URLError"}:
        return "A network error occurred while contacting the transcription service."
    # Permission errors
    if name in {"PermissionError"}:
        return "A file permission error occurred. Check that the app can write to its data directory."
    return f"Transcription failed ({name}). See the log file for technical details."


class DictationPipeline:
    """Transcription pipeline — one method per step.

    The pipeline is run on a background thread by VoiceTyperApp.
    Each method is independently testable and handles its own errors
    without aborting the entire pipeline.
    """

    def __init__(self, app: Any):
        self._app = app
        self._cycle_id = ""
        self._audio = None
        self._duration = 0.0
        self._recorded_rms = 0.0
        self._device_info = ""
        self._watchdog = None
        # NEW-PERF-010: pre-computed (rms, peak, silence_pct) from
        # Recorder.stop(), passed through to the transcription engine
        # so it doesn't recompute the same stats on the same audio.
        self._audio_stats: "tuple[float, float, float] | None" = None

    def run(
        self,
        audio,
        duration: float,
        recorded_rms: float,
        cycle_id: str,
        watchdog,
    ) -> None:
        """Run the full transcription pipeline.

        This is the entry point called from VoiceTyperApp._stop_dictation.
        It runs on the transcription thread.
        """
        self._audio = audio
        self._duration = duration
        self._recorded_rms = recorded_rms
        self._cycle_id = cycle_id
        self._watchdog = watchdog
        # NEW-PERF-010: capture the pre-computed audio stats from the
        # recorder so we can pass them to the transcription engine.
        self._audio_stats = getattr(self._app.recorder, "_last_audio_stats", None)
        _t0 = time.perf_counter()

        try:
            log.info("[TRANSCRIBE] Starting transcription... (cycle=%s)", self._cycle_id)

            # Step 1: Transcribe (streaming finalize or direct)
            text = self._transcribe()

            _elapsed = time.perf_counter() - _t0
            log.info(
                "[TRANSCRIBE] Transcription complete (len=%d, took=%.1fs, cycle=%s)",
                len(text) if text else 0, _elapsed, self._cycle_id,
            )

            # Step 2: Check for empty result
            if not text:
                self._handle_empty_transcription()
                return

            # Step 3: Text cleanup
            text = self._clean_text(text)

            # Step 4: Vocabulary correction
            text = self._apply_vocabulary(text)

            # Step 5: Template matching
            text = self._apply_templates(text)

            # Step 6: Auto-punctuation
            text = self._apply_punctuation(text)

            # Step 7: LLM polish
            text = self._apply_llm_polish(text)

            # Step 8: Store in history + crash recovery
            self._store_result(text)

            # Step 9: Copy to clipboard + paste
            self._copy_and_paste(text)

        except Exception as e:
            log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", self._cycle_id)
            self._app.tray.set_state(AppState.ERROR, "Transcription failed")
            # ERR-005: do NOT leak raw exception text into tray
            # notifications — ctranslate2 / torch errors often contain
            # file paths, CUDA version strings, and internal stack
            # details. Map to a user-friendly message instead.
            self._app.tray.notify(
                "Voice Typer Error",
                _friendly_transcription_error(e),
            )
            self._app._schedule_timer(3.0, lambda: self._app.tray.set_state(AppState.IDLE))

        finally:
            # SEC-audit-008: Zero the audio array after transcription
            # completes to prevent forensic recovery of voice data
            # from process memory.  The audio buffer contains potentially
            # sensitive biometric data (voice recordings) that should not
            # linger in memory longer than necessary.
            try:
                if self._audio is not None and isinstance(self._audio, np.ndarray):
                    self._audio.fill(0)
                    self._audio = None
            except Exception:
                pass
            # RACE-013: reset the persistent watchdog thread (signal
            # that transcription completed normally). Old code used
            # watchdog.cancel() for Timer-based watchdogs; now we
            # signal the Event-based persistent watchdog thread.
            # RACE-016: wrap daemon thread finally block with
            # try/except to prevent exceptions during shutdown.
            try:
                if hasattr(self._app, '_recording_controller') and self._app._recording_controller is not None:
                    self._app._recording_controller._reset_watchdog()
                    self._app._recording_controller._stop_watchdog_thread()
            except Exception:
                pass
            try:
                session = self._app._get_streaming_session()
                if session is not None and not self._app.recorder.recording:
                    self._app._set_streaming_session(None)
            except Exception:
                log.debug("[TRANSCRIBE] finally: session cleanup failed", exc_info=True)
            try:
                self._app._busy_event.set()  # busy = False
            except Exception:
                pass
            # ARCH-016: clear _transcription_thread under the app's
            # state lock so concurrent readers (e.g. _cancel_streaming_session
            # in another thread) don't see a torn None vs Thread object.
            try:
                with self._app._lock:
                    self._app._transcription_thread = None
            except Exception:
                # Defensive: if the lock is unavailable we still want
                # to clear the field — but log the race.
                log.debug(
                    "[TRANSCRIBE] could not acquire app._lock to clear "
                    "_transcription_thread; assigning without lock",
                    exc_info=True,
                )
                try:
                    self._app._transcription_thread = None
                except Exception:
                    pass
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            log.info("[TRANSCRIBE] busy reset to False (cycle=%s)", self._cycle_id)

    # ── Pipeline steps ────────────────────────────────────────────

    def _transcribe(self) -> str:
        """Step 1: Get transcription via streaming finalize or direct."""
        session = self._app._get_streaming_session()
        if session is not None:
            log.info("[STREAMING] Finalizing streaming transcript (cycle=%s)", self._cycle_id)
            text = session.finalize(self._audio)
            self._app._set_streaming_session(None)
        else:
            active = self._app._get_active_transcriber()
            # NEW-PERF-010: pass the pre-computed audio stats so the
            # transcription engine doesn't recompute RMS/peak/silence_pct
            # on the same audio array (saves 1-3 ms + 3× 1.9 MB transient
            # memory per dictation).
            try:
                text = active.transcribe_with_fallback(
                    self._audio, audio_stats=self._audio_stats
                )
            except TypeError:
                # Backend doesn't support the audio_stats kwarg yet
                # (e.g. Qwen/Parakeet/cloud engines that haven't been
                # updated).  Fall back to the old signature.
                text = active.transcribe_with_fallback(self._audio)

        active = self._app._get_active_transcriber()
        self._device_info = (
            active.device_info if active is not None and hasattr(active, "device_info")
            else "Parakeet ASR"
        )
        return text

    def _handle_empty_transcription(self) -> None:
        """Step 2: Handle case where no speech was detected."""
        log.info("[TRANSCRIBE] No speech detected (cycle=%s)", self._cycle_id)
        if self._recorded_rms < 0.005:
            self._app.tray.set_state(AppState.IDLE, "No speech -- check microphone")
            self._app.tray.notify(
                "Voice Typer",
                "No speech was detected and audio was near-silence.\n"
                "Your microphone may not be capturing audio.\n"
                "Check that the correct mic is selected and is active.",
            )
        else:
            self._app.tray.set_state(AppState.IDLE, "No speech detected")
        self._app._busy_event.set()  # busy = False
        self._app._schedule_timer(2.0, lambda: self._app.tray.set_state(AppState.IDLE))

    def _clean_text(self, text: str) -> str:
        """Step 3: Apply text cleanup (spacing, self-corrections, capitalization)."""
        from voice_typer.server.text_cleanup import clean_transcribed_text

        if self._app.config.text_cleanup_enabled:
            vocab_enabled = getattr(self._app.config, "vocabulary_enabled", True)
            raw = text
            text = clean_transcribed_text(
                text, auto_punctuation=False, skip_corrections=vocab_enabled,
            )
            if text != raw:
                log.info("[CLEANUP] Text cleaned: len %d -> %d", len(raw), len(text))
        else:
            log.info("[CLEANUP] Text cleanup disabled (raw mode)")
        return text

    def _apply_vocabulary(self, text: str) -> str:
        """Step 4: Apply vocabulary corrections.

        ERR-014: previously failures here were ``log.debug`` (invisible
        at default log level). User saw wrong text with no clue why.
        Promoted to ``log.warning`` + tray notify on first occurrence.
        """
        try:
            if self._app._vocabulary_manager is None:
                from voice_typer.server.vocabulary import VocabularyManager
                self._app._vocabulary_manager = VocabularyManager()
            text = self._app._vocabulary_manager.apply_to_text(text)
        except Exception:
            log.warning("[PIPELINE] Vocabulary correction failed", exc_info=True)
            if not getattr(self, "_vocab_fail_notified", False):
                self._vocab_fail_notified = True
                try:
                    self._app.tray.notify(
                        "Voice Typer",
                        "Vocabulary correction failed. Check the log file for details.",
                    )
                except Exception:
                    pass
        return text

    def _apply_templates(self, text: str) -> str:
        """Step 5: Apply template matching.

        ERR-014: promoted ``log.debug`` to ``log.warning`` + tray notify.
        """
        try:
            if getattr(self._app.config, "templates_enabled", True):
                if self._app._template_manager is None:
                    from voice_typer.server.templates import TemplateManager
                    self._app._template_manager = TemplateManager()
                expanded = self._app._template_manager.match(text)
                if expanded is not None:
                    log.info("[TEMPLATE] Matched template, expanded %d -> %d chars",
                             len(text), len(expanded))
                    text = expanded
        except Exception:
            log.warning("[PIPELINE] Template matching failed", exc_info=True)
            if not getattr(self, "_template_fail_notified", False):
                self._template_fail_notified = True
                try:
                    self._app.tray.notify(
                        "Voice Typer",
                        "Template matching failed. Check the log file for details.",
                    )
                except Exception:
                    pass
        return text

    def _apply_punctuation(self, text: str) -> str:
        """Step 6: Apply auto-punctuation."""
        if self._app.config.auto_punctuation:
            from voice_typer.server.text_cleanup import _add_safe_terminal_punctuation
            text = _add_safe_terminal_punctuation(text)
        return text

    def _apply_llm_polish(self, text: str) -> str:
        """Step 7: Apply LLM polishing (if consented)."""
        effective_llm_key = (
            self._app.config.llm_api_key
            or getattr(self._app.config, "openai_api_key", "")
        )
        if (
            self._app.config.llm_polish
            and effective_llm_key
            and getattr(self._app.config, "llm_polish_consent", False)
        ):
            try:
                if self._app._llm_polisher is None:
                    from voice_typer.server.llm_polish import LLMPolisher
                    self._app._llm_polisher = LLMPolisher(
                        api_key=effective_llm_key,
                        api_url=self._app.config.llm_api_url or None,
                        model=self._app.config.llm_model or None,
                        preset=self._app.config.llm_preset,
                        enabled=True,
                    )
                text = self._app._llm_polisher.polish(text)
            except Exception as exc:
                log.warning("[LLM_POLISH] Polish failed: %s", exc)
        elif (
            self._app.config.llm_polish
            and effective_llm_key
            and not getattr(self._app.config, "llm_polish_consent", False)
        ):
            if not getattr(self._app, "_llm_consent_warned", False):
                log.info(
                    "[LLM_POLISH] llm_polish is enabled but "
                    "llm_polish_consent is False — skipping polish."
                )
                self._app._llm_consent_warned = True
        return text

    def _store_result(self, text: str) -> None:
        """Step 8: Store in history DB and crash recovery.

        ERR-006: Previously failures here were DEBUG-level (invisible at
        default log level) with no tray notification. We now log at
        ``exception`` level and surface a tray notice the first time
        each failure type occurs so the user knows data is being lost.
        """
        try:
            self._app.history_db.add_transcription(
                text,
                duration=self._duration,
                model=self._app.config.model_size,
                device=self._app.config.device,
            )
        except Exception:
            log.exception("[PIPELINE] History DB add failed")
            if not getattr(self, "_history_fail_notified", False):
                self._history_fail_notified = True
                try:
                    self._app.tray.notify(
                        "Voice Typer",
                        "Could not save the transcription to history. "
                        "Check the log file for details.",
                    )
                except Exception:
                    pass

        if self._app.config.crash_recovery_enabled:
            try:
                self._app._crash_recovery.add(text, pasted=False)
            except Exception:
                log.exception("[PIPELINE] Crash recovery add failed")
                if not getattr(self, "_crash_recovery_fail_notified", False):
                    self._crash_recovery_fail_notified = True
                    try:
                        self._app.tray.notify(
                            "Voice Typer",
                            "Could not save the transcription to the crash-recovery "
                            "buffer. Check the log file for details.",
                        )
                    except Exception:
                        pass

        # Save for repaste / undo
        self._app._last_transcription = text

        # NEW-IPC-002: emit transcription_final push event so the
        # renderer can proactively refresh Home/Dashboard/History
        # without polling.
        try:
            from voice_typer.server.ipc_server import _push_event_now
            _push_event_now({
                "type": "transcription_final",
                "data": {"text": text[:200]}  # truncated for UI preview
            })
        except Exception:
            pass

        if self._app.config.log_transcriptions:
            log.info("[TRANSCRIBE] Transcription: %s", text[:200])
        else:
            log.info("[TRANSCRIBE] Transcription: %d chars", len(text))

    def _copy_and_paste(self, text: str) -> None:
        """Step 9: Copy to clipboard and attempt paste.

        ERR-004: If clipboard.copy() fails, we previously lost the
        transcription silently. We now write the text to the crash
        recovery buffer (which persists to disk) and notify the user
        with the path so they can recover it manually.
        """
        if not self._app.clipboard.copy(text):
            log.error("[CLIPBOARD] Clipboard copy failed (cycle=%s)", self._cycle_id)
            recovery_path: Optional[str] = None
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
            self._app.tray.set_state(AppState.IDLE, "Done -- clipboard unavailable")
            notice = (
                "Transcription complete, but the clipboard was unavailable.\n"
                "Your text was saved to the crash-recovery file so it is not lost."
            )
            if recovery_path:
                notice += f"\nRecovery file: {recovery_path}"
            self._app.tray.notify("Voice Typer", notice)
            self._app._busy_event.set()
            self._app._schedule_timer(
                3.0,
                lambda: self._app.tray.set_state(AppState.IDLE, f"Ready -- {self._device_info}"),
            )
            return

        pasted = False
        if self._app.config.paste_on_stop:
            pasted = self._app.clipboard.paste()

        if pasted and self._app.config.crash_recovery_enabled:
            try:
                self._app._crash_recovery.mark_latest_pasted()
            except Exception:
                pass

        if pasted:
            status = f"Done -- {len(text)} chars (pasted)"
        else:
            status = f"Done -- {len(text)} chars (in clipboard)"

        self._app.tray.set_state(AppState.IDLE, status)
        self._app.tray.notify("Voice Typer", f"Transcribed {len(text)} characters")
        self._app._schedule_timer(
            3.0,
            lambda: self._app.tray.set_state(AppState.IDLE, f"Ready -- {self._device_info}"),
        )
