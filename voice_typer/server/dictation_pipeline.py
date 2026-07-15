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

import contextlib
import logging
import time
from typing import Any

import numpy as np

from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


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
        self._audio_stats: tuple[float, float, float] | None = None

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

            # PRE-FLIGHT: resource health check — provides diagnostic
            # context (RAM, disk, GPU) if a heap corruption crash occurs.
            self._check_resources()

            # PERF-FIX-001: per-stage timing instrumentation.
            # Stage durations are collected and logged as a single
            # consolidated line at the end to reduce log verbosity.
            # Individual stage lines are available at DEBUG level.
            _stage_t0 = time.perf_counter()

            # Step 1: Transcribe (streaming finalize or direct)
            text = self._transcribe()

            _transcribe_ms = (time.perf_counter() - _stage_t0) * 1000
            _elapsed = time.perf_counter() - _t0
            log.info(
                "[TRANSCRIBE] Transcription complete (len=%d, took=%.1fs, cycle=%s)",
                len(text) if text else 0,
                _elapsed,
                self._cycle_id,
            )
            log.debug(
                "[PIPE-PERF] transcribe: %.0f ms (cycle=%s)",
                _transcribe_ms,
                self._cycle_id,
            )

            # Step 2: Check for empty result
            if not text:
                self._handle_empty_transcription()
                return

            # Step 3: Text cleanup
            _stage_t0 = time.perf_counter()
            text = self._clean_text(text)
            _clean_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 4: Vocabulary correction
            _stage_t0 = time.perf_counter()
            text = self._apply_vocabulary(text)
            _vocab_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 5: Template matching
            _stage_t0 = time.perf_counter()
            text = self._apply_templates(text)
            _tmpl_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 6: Auto-punctuation
            _stage_t0 = time.perf_counter()
            text = self._apply_punctuation(text)
            _punct_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 7: LLM polish
            _stage_t0 = time.perf_counter()
            text = self._apply_llm_polish(text)
            _llm_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 7b: AI enhancement (P4)
            _stage_t0 = time.perf_counter()
            text = self._apply_ai_enhancement(text)
            _ai_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 7c: Vocabulary automation analysis (P5)
            _stage_t0 = time.perf_counter()
            self._analyze_vocabulary(text)
            _va_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 8: Store in history + crash recovery
            _stage_t0 = time.perf_counter()
            self._store_result(text)
            _store_ms = (time.perf_counter() - _stage_t0) * 1000

            # Step 9: Copy to clipboard + paste
            _stage_t0 = time.perf_counter()
            self._copy_and_paste(text)
            _paste_ms = (time.perf_counter() - _stage_t0) * 1000

            _total_ms = (time.perf_counter() - _t0) * 1000
            log.info(
                "[PIPE-PERF] total=%.0fms, stages: transcribe=%.0f, clean=%.0f, "
                "vocab=%.0f, templates=%.0f, punct=%.0f, store=%.0f, "
                "paste=%.0f (cycle=%s)",
                _total_ms,
                _transcribe_ms,
                _clean_ms,
                _vocab_ms,
                _tmpl_ms,
                _punct_ms,
                _store_ms,
                _paste_ms,
                self._cycle_id,
            )
            if _llm_ms > 1:
                log.info(
                    "[PIPE-PERF] llm_polish=%.0fms, ai_enhance=%.0fms, vocab_auto=%.0fms (cycle=%s)",
                    _llm_ms,
                    _ai_ms,
                    _va_ms,
                    self._cycle_id,
                )

        except Exception as e:
            log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", self._cycle_id)
            # NEW-BUBBLE-TRANSCRIBING: Hide the bubble on transcription failure
            # so the overlay doesn't stay stuck showing "Transcribing…".
            try:
                if self._app.config.bubble_behavior == "always_visible":
                    self._app._waveform_bubble.set_state("idle")
                else:
                    self._app._waveform_bubble.hide()
            except Exception:
                log.debug("[PIPELINE] bubble hide on error failed", exc_info=True)
            self._app.tray.set_state(AppState.ERROR, "Transcription failed")
            # ERR-005: do NOT leak raw exception text into tray
            # notifications — ctranslate2 / torch errors often contain
            # file paths, CUDA version strings, and internal stack
            # details. Map to a user-friendly message instead.
            self._app.tray.notify(
                APP_NAME,
                _friendly_transcription_error(e),
            )
            self._app._schedule_timer(3.0, lambda: self._app.tray.set_state(AppState.IDLE))

        finally:
            # SEC-audit-008: Zero the audio array after transcription
            # completes to prevent forensic recovery of voice data
            # from process memory.  The audio buffer contains potentially
            # sensitive biometric data (voice recordings) that should not
            # linger in memory longer than necessary.
            with contextlib.suppress(Exception):
                if self._audio is not None and isinstance(self._audio, np.ndarray):
                    self._audio.fill(0)
                    self._audio = None
            # RACE-013: reset the persistent watchdog thread (signal
            # that transcription completed normally). Old code used
            # watchdog.cancel() for Timer-based watchdogs; now we
            # signal the Event-based persistent watchdog thread.
            # RACE-016: wrap daemon thread finally block with
            # try/except to prevent exceptions during shutdown.
            with contextlib.suppress(Exception):
                # RW-9 Phase 2: fixed typo — was `_recording_controller`
                # (doesn't exist on VoiceTyperApp). The attribute is `recording`
                # (a RecordingController). Previously the watchdog reset never
                # fired from this finally block — see worklog.md bug note.
                recording = getattr(self._app, "recording", None)
                if recording is not None:
                    recording._reset_watchdog()
                    recording._stop_watchdog_thread()
            try:
                session = self._app.recording.get_streaming_session()
                if session is not None and not self._app.recorder.recording:
                    self._app.recording.set_streaming_session(None)
            except Exception:
                log.debug("[TRANSCRIBE] finally: session cleanup failed", exc_info=True)
            with contextlib.suppress(Exception):
                self._app._busy_event.set()  # busy = False
            # ARCH-016: clear _transcription_thread under the app's
            # state lock so concurrent readers (e.g. _cancel_streaming_session
            # in another thread) don't see a torn None vs Thread object.
            # ARCH-REFAC-003: write directly to RecordingController (was a
            # @property delegate previously).
            try:
                with self._app._lock:
                    self._app.recording._transcription_thread = None
            except Exception:
                # Defensive: if the lock is unavailable we still want
                # to clear the field — but log the race.
                log.debug(
                    "[TRANSCRIBE] could not acquire app._lock to clear _transcription_thread; assigning without lock",
                    exc_info=True,
                )
                with contextlib.suppress(Exception):
                    self._app.recording._transcription_thread = None
            with contextlib.suppress(Exception):
                import gc

                gc.collect()
            log.debug("[TRANSCRIBE] busy reset to False (cycle=%s)", self._cycle_id)

    # ── Pipeline steps ────────────────────────────────────────────

    def _check_resources(self) -> None:
        """Pre-flight health check before transcription.

        Checks available RAM, disk space, and GPU memory (if CUDA)
        and logs warnings when resources are critically low.  The
        check is best-effort — failures are logged at DEBUG level
        and do NOT abort the pipeline (the user may still succeed
        even with low resources).

        Exit code 0xC0000374 (STATUS_HEAP_CORRUPTION) during
        transcription is often caused by low memory (RAM) or
        insufficient disk space (affecting pagefile/swap).  These
        logs help diagnose the root cause when paired with a crash.
        """
        import os as _os
        from pathlib import Path as _Path

        # ── RAM check ───────────────────────────────────────────────
        free_mb: float | None = None
        try:
            import psutil

            free_mb = psutil.virtual_memory().available / (1024 * 1024)
        except ImportError:
            try:
                import ctypes

                if _os.name == "nt":

                    class _MEMORYSTATUSEX(ctypes.Structure):
                        _fields_ = [
                            ("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                        ]

                    stat = _MEMORYSTATUSEX()
                    stat.dwLength = ctypes.sizeof(stat)
                    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                    free_mb = stat.ullAvailPhys / (1024 * 1024)
            except Exception:
                pass

        if free_mb is not None:
            log.info(
                "[RESOURCE] Available RAM: %.0f MB",
                free_mb,
            )
            if free_mb < 1024:
                log.warning(
                    "[RESOURCE] Low RAM (%.0f MB < 1024 MB) — "
                    "heap corruption (0xC0000374) is possible during "
                    "model inference.  Close other apps or try a "
                    "smaller transcription model.",
                    free_mb,
                )
            elif free_mb < 2048:
                log.info(
                    "[RESOURCE] RAM is moderate (%.0f MB) — large models may struggle.",
                    free_mb,
                )
        else:
            log.debug("[RESOURCE] Could not query available RAM")

        # ── Disk space check ────────────────────────────────────────
        # Check both the system drive (for pagefile) and the model
        # cache drive (for model downloads).
        drives_to_check: list[_Path] = []
        try:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
            drives_to_check.append(config_dir)
            drives_to_check.append(_Path.home())
            # Add the drive where the model cache lives (HF_HOME)
            hf_home = _os.environ.get("HF_HOME")
            if hf_home:
                drives_to_check.append(_Path(hf_home))
        except Exception:
            drives_to_check.append(_Path.home())

        seen_drives: set[str] = set()
        for path in drives_to_check:
            try:
                drive_info = _os.statvfs(path) if hasattr(_os, "statvfs") else None
            except Exception:
                continue
            if drive_info is None:
                # Windows: use shutil.disk_usage
                try:
                    import shutil

                    usage = shutil.disk_usage(path)
                    free_gb = usage.free / (1024**3)
                    # Deduplicate by mount point (same drive may appear
                    # via multiple paths like home dir + config dir)
                    drive_key = str(path.resolve())
                    if drive_key in seen_drives:
                        continue
                    seen_drives.add(drive_key)
                    log.info(
                        "[RESOURCE] Disk free on %s: %.1f GB",
                        path,
                        free_gb,
                    )
                    if free_gb < 1.0:
                        log.warning(
                            "[RESOURCE] Critically low disk space on %s "
                            "(%.1f GB < 1 GB) — heap corruption is possible "
                            "if the system pagefile cannot grow.  Free up "
                            "disk space or move the model cache to a "
                            "drive with more free space.",
                            path,
                            free_gb,
                        )
                except Exception:
                    continue
            else:
                # POSIX: use statvfs
                free_gb = (drive_info.f_bavail * drive_info.f_frsize) / (1024**3)
                log.info(
                    "[RESOURCE] Disk free: %.1f GB",
                    free_gb,
                )
                if free_gb < 1.0:
                    log.warning(
                        "[RESOURCE] Critically low disk space (%.1f GB) — heap corruption risk for pagefile.",
                        free_gb,
                    )

        # ── GPU memory check (if CUDA) ──────────────────────────────
        try:
            import torch

            if torch.cuda.is_available():
                allocated = torch.cuda.memory_allocated() / (1024**2)
                reserved = torch.cuda.memory_reserved() / (1024**2)
                total = torch.cuda.get_device_properties(0).total_memory / (1024**2)
                free_gpu = total - allocated
                log.info(
                    "[RESOURCE] GPU memory: %.0f MB allocated, %.0f MB reserved, %.0f MB free (total %.0f MB)",
                    allocated,
                    reserved,
                    free_gpu,
                    total,
                )
                if free_gpu < 512:
                    log.warning(
                        "[RESOURCE] Low GPU memory (%.0f MB free) — CUDA out-of-memory errors are likely.",
                        free_gpu,
                    )
        except (ImportError, Exception):
            pass

        log.debug("[RESOURCE] Pre-flight health check complete")

    def _transcribe(self) -> str:
        """Step 1: Get transcription via streaming finalize or direct."""
        session = self._app.recording.get_streaming_session()
        if session is not None:
            log.info("[STREAMING] Finalizing streaming transcript (cycle=%s)", self._cycle_id)
            text = session.finalize(self._audio)
            self._app.recording.set_streaming_session(None)
        else:
            active = self._app.models.active_transcriber()
            # NEW-PERF-010: pass the pre-computed audio stats so the
            # transcription engine doesn't recompute RMS/peak/silence_pct
            # on the same audio array (saves 1-3 ms + 3× 1.9 MB transient
            # memory per dictation).
            try:
                text = active.transcribe_with_fallback(self._audio, audio_stats=self._audio_stats)
            except TypeError:
                # Backend doesn't support the audio_stats kwarg yet
                # (e.g. Qwen/Parakeet/cloud engines that haven't been
                # updated).  Fall back to the old signature.
                text = active.transcribe_with_fallback(self._audio)

        active = self._app.models.active_transcriber()
        self._device_info = (
            active.device_info if active is not None and hasattr(active, "device_info") else "Parakeet ASR"
        )
        return text

    def _handle_empty_transcription(self) -> None:
        """Step 2: Handle case where no speech was detected.

        UX-SILENCE-GRACE: If the recording duration is less than the 15-second
        grace period, the "no speech detected" tray notification is suppressed.
        This prevents an annoying warning when the user briefly taps the hotkey
        (start recording, stop immediately) — the recording is too short to
        make a meaningful speech assessment. The notification only fires when
        the user records for 15+ seconds with no detectable speech, which
        genuinely suggests a microphone issue.
        """
        log.info("[TRANSCRIBE] No speech detected (cycle=%s)", self._cycle_id)
        # NEW-BUBBLE-TRANSCRIBING: Hide the bubble since there's nothing to
        # transcribe — no need to keep the overlay visible.
        try:
            if self._app.config.bubble_behavior == "always_visible":
                self._app._waveform_bubble.set_state("idle")
            else:
                self._app._waveform_bubble.hide()
        except Exception:
            log.debug("[PIPELINE] bubble hide/set idle on empty failed", exc_info=True)

        # UX-SILENCE-GRACE: Suppress the notification for short recordings (< 15s).
        # A brief tap of the hotkey does not warrant a microphone warning.
        _grace_period = 15.0
        if self._duration < _grace_period:
            log.info(
                "[TRANSCRIBE] No speech detected but recording was only %.1fs "
                "(< %.0fs grace period) — suppressing notification",
                self._duration,
                _grace_period,
            )
            self._app.tray.set_state(AppState.IDLE, "No speech detected")
        elif self._recorded_rms < 0.005:
            self._app.tray.set_state(AppState.IDLE, "No speech -- check microphone")
            self._app.tray.notify(
                APP_NAME,
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
                text,
                auto_punctuation=False,
                skip_corrections=vocab_enabled,
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
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Vocabulary correction failed. Check the log file for details.",
                    )
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
                    log.info("[TEMPLATE] Matched template, expanded %d -> %d chars", len(text), len(expanded))
                    text = expanded
        except Exception:
            log.warning("[PIPELINE] Template matching failed", exc_info=True)
            if not getattr(self, "_template_fail_notified", False):
                self._template_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Template matching failed. Check the log file for details.",
                    )
        return text

    def _apply_punctuation(self, text: str) -> str:
        """Step 6: Apply auto-punctuation."""
        if self._app.config.auto_punctuation:
            from voice_typer.server.text_cleanup import _add_safe_terminal_punctuation

            text = _add_safe_terminal_punctuation(text)
        return text

    def _apply_llm_polish(self, text: str) -> str:
        """Step 7: Apply LLM polishing (if consented)."""
        effective_llm_key = self._app.config.llm_api_key or getattr(self._app.config, "openai_api_key", "")
        if self._app.config.llm_polish and effective_llm_key and getattr(self._app.config, "llm_polish_consent", False):
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
        ) and not getattr(self._app, "_llm_consent_warned", False):
            log.info("[LLM_POLISH] llm_polish is enabled but llm_polish_consent is False — skipping polish.")
            self._app._llm_consent_warned = True
        return text

    def _apply_ai_enhancement(self, text: str) -> str:
        """Step 7b: Apply rule-based AI enhancement (P4).

        Delegates to ``voice_typer.server.ai_enhancement.enhance_transcription``,
        which reads the four ``ai_enhancement_*`` / ``auto_*`` /
        ``fix_grammar_basics`` flags off the config. The master
        toggle (``ai_enhancement_enabled``) defaults OFF — when off,
        ``enhance_transcription`` returns the text unchanged.

        ERR-014-style hardening: failures here are logged at WARNING
        level but do NOT abort the pipeline. The original text is
        returned so the dictation completes and the user sees their
        (un-enhanced) transcription rather than an error.
        """
        try:
            from voice_typer.server.ai_enhancement import enhance_transcription

            return enhance_transcription(text, self._app.config)
        except Exception:
            log.warning("[AI_ENHANCE] Enhancement failed", exc_info=True)
            return text

    def _analyze_vocabulary(self, text: str) -> None:
        """Step 7c: Analyze transcription for vocabulary suggestions (P5).

        Delegates to the app's ``VocabularyAutomation`` instance. The
        master toggle (``vocabulary_automation_enabled``) defaults
        OFF — when off, this method is a no-op.

        Suggestions above ``vocabulary_auto_apply_threshold`` are
        auto-applied (added to the user's vocabulary); the rest are
        queued for the user to review via the IPC handlers in
        ``vocabulary_automation_handlers.py``.

        ERR-014-style hardening: failures here are logged at WARNING
        level but do NOT abort the pipeline. The transcription is
        already complete; vocabulary suggestions are a side-channel
        for future improvements.
        """
        if not getattr(self._app.config, "vocabulary_automation_enabled", False):
            return
        try:
            automation = getattr(self._app, "_vocabulary_automation", None)
            if automation is None:
                # Lazy-init on first use. The VocabularyAutomation
                # constructor needs the existing VocabularyManager
                # (so it can read the user's current vocabulary and
                # apply suggestions to it) and the config (for the
                # thresholds).
                from voice_typer.server.vocabulary_automation import VocabularyAutomation

                vm = self._app._vocabulary_manager
                if vm is None:
                    from voice_typer.server.vocabulary import VocabularyManager

                    vm = VocabularyManager()
                    self._app._vocabulary_manager = vm
                automation = VocabularyAutomation(vm, self._app.config)
                self._app._vocabulary_automation = automation

            # Faster-whisper exposes segment-level avg_logprob, not
            # per-word confidence. We pass an empty segment list and
            # a sentinel confidence; the analyzer degrades gracefully
            # (treats the whole text as one segment with the given
            # confidence). When the transcription engine exposes
            # richer segment data in the future, we can plumb it
            # through here without changing the analyzer's API.
            segments = getattr(self, "_segments", None) or []
            confidence = getattr(self, "_confidence", 0.9)
            suggestions = automation.analyze_transcription(
                text,
                segments,
                confidence,
            )
            if not suggestions:
                return

            # Auto-apply high-confidence suggestions.
            auto_threshold = getattr(
                self._app.config,
                "vocabulary_auto_apply_threshold",
                0.95,
            )
            applied = automation.auto_apply_high_confidence_suggestions(auto_threshold)
            if applied > 0:
                log.info("[VOCAB_AUTO] Auto-applied %d high-confidence suggestion(s)", applied)

            # Push any remaining (pending) suggestions to the frontend.
            pending = automation.get_pending_suggestions()
            if pending:
                try:
                    from voice_typer.server import event_bus

                    event_bus.publish(
                        {
                            "type": "vocabulary_suggestion",
                            "data": {
                                "suggestions": [
                                    {
                                        "original": s.original,
                                        "corrected": s.corrected,
                                        "confidence": s.confidence,
                                        "context": s.context,
                                        "timestamp": s.timestamp,
                                    }
                                    for s in pending
                                ],
                            },
                        }
                    )
                except Exception:
                    log.debug(
                        "[VOCAB_AUTO] could not push vocabulary_suggestion event",
                        exc_info=True,
                    )
        except Exception:
            log.warning("[VOCAB_AUTO] Analysis failed", exc_info=True)

    def _store_result(self, text: str) -> None:
        """Step 8: Store in history DB and crash recovery.

        ERR-006: Previously failures here were DEBUG-level (invisible at
        default log level) with no tray notification. We now log at
        ``exception`` level and surface a tray notice the first time
        each failure type occurs so the user knows data is being lost.

        ADR-0010 §6.2: ``history_db.flush()`` is called after
        ``add_transcription()`` to guarantee the row is committed before
        ``repaste_last()`` could fire. ``flush()`` blocks until the
        writer thread processes all queued writes (FIFO no-op with
        ``wait=True``). See ``history_db.py:flush()``.
        """
        try:
            self._app.history_db.add_transcription(
                text,
                duration=self._duration,
                model=self._app.config.model_size,
                device=self._app.config.device,
            )
            # ADR-0010 §6.2: flush to guarantee the row is committed
            # before repaste could fire. flush() blocks until the writer
            # thread processes all queued writes (FIFO no-op with
            # wait=True). See history_db.py:flush().
            self._app.history_db.flush()
        except Exception:
            log.exception("[PIPELINE] History DB add failed")
            if not getattr(self, "_history_fail_notified", False):
                self._history_fail_notified = True
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
                if not getattr(self, "_crash_recovery_fail_notified", False):
                    self._crash_recovery_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "Could not save the transcription to the crash-recovery "
                            "buffer. Check the log file for details.",
                        )

        # Save for repaste / undo
        self._app._last_transcription = text

        # NEW-IPC-002: emit transcription_final push event so the
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
            pass

        if self._app.config.log_transcriptions:
            # SEC-009: run transcription text through ``redact_pii()``
            # before logging so emails, phone numbers, SSNs, and credit-
            # card-like patterns are masked even when the user has opted
            # into verbose transcription logging. Pre-fix this used the
            # raw text, which would land PII in the log file.
            # ``PIIRedactionFilter`` already redacts log records at the
            # logging.Handler level, but defence-in-depth: redact here
            # too so a future change to the filter can't accidentally
            # expose PII from this high-volume logging path.
            from voice_typer.server.security import redact_pii

            log.info("[TRANSCRIBE] Transcription: %s", redact_pii(text[:200]))
        else:
            log.info("[TRANSCRIBE] Transcription: %d chars", len(text))

    def _copy_and_paste(self, text: str) -> None:
        """Step 9: Copy to clipboard and attempt paste.

        ADR-0010 §6.1 / DP1 / DP2 / DP4.

        The snapshot/restore cycle is explicit here (not hidden inside
        copy()/paste()) so the borrow/restore pairing is visible at the
        call site. This is the single place that orchestrates the
        clipboard borrow lifecycle.

        ERR-004: If clipboard.copy() fails, we previously lost the
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
                try:
                    if self._app.config.bubble_behavior == "always_visible":
                        self._app._waveform_bubble.set_state("idle")
                    else:
                        self._app._waveform_bubble.hide()
                except Exception:
                    log.debug("[PIPELINE] bubble hide on clipboard fail failed", exc_info=True)
                self._app.tray.set_state(AppState.IDLE, "Done -- clipboard unavailable")
                notice = (
                    "Transcription complete, but the clipboard was unavailable.\n"
                    "Your text was saved to the crash-recovery file so it is not lost."
                )
                if recovery_path:
                    notice += f"\nRecovery file: {recovery_path}"
                self._app.tray.notify(APP_NAME, notice)
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
                pasted = self._app.clipboard.paste(snapshot, pasted_text=text)
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
        try:
            if self._app.config.bubble_behavior == "always_visible":
                self._app._waveform_bubble.set_state("idle")
            else:
                self._app._waveform_bubble.hide()
        except Exception:
            log.debug("[PIPELINE] bubble hide/set idle failed", exc_info=True)

        self._app.tray.set_state(AppState.IDLE, status)
        self._app._schedule_timer(
            3.0,
            lambda: self._app.tray.set_state(AppState.IDLE, f"Ready -- {self._device_info}"),
        )
