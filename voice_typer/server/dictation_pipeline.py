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
import typing
from typing import Any

import numpy as np

from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError
from voice_typer.server.cloud_engines import CloudEngine
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


# AC-49: vocabulary-automation analyzer degrade-gracefully defaults
# used when the transcription engine did not produce per-segment or
# per-word confidence data (e.g. faster-whisper's avg_logprob
# surface). These are module-level sentinels (NOT instance
# attributes) — the previous ``getattr(self, "_segments", None) or []``
# + ``getattr(self, "_confidence", 0.9)`` accidentally fabricated
# a confident empty segment list, which made the analyzer treat
# every word as high-confidence. Now the analyzer sees honest
# empty data and degrades gracefully.
_EMPTY_SEGMENTS: list = []
_NO_TRANSCRIPT_CONFIDENCE: float = 0.0


# ERR-005: raw exception messages from ctranslate2 / torch / faster-whisper
# often leak file paths, CUDA versions, and internal stack details into
# user-facing tray notifications. Map known exception classes to friendly
# messages; fall back to a generic message for unknown errors.
def _friendly_transcription_error(exc: BaseException) -> str:
    """Return a user-friendly message describing a transcription failure."""
    msg = str(exc).lower()
    name = type(exc).__name__
    # Walk the __cause__ chain — cloud_engines.py wraps errors with
    # raise RuntimeError(...) from exc, hiding the original type.
    names = {name}
    c = exc.__cause__
    while c is not None:
        names.add(type(c).__name__)
        c = c.__cause__
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
    if names & {"ConnectionError", "TimeoutError", "URLError"}:
        return "A network error occurred while contacting the transcription service."
    # Permission errors
    if "PermissionError" in names:
        return "A file permission error occurred. Check that the app can write to its data directory."
    return f"Transcription failed ({name}). See the log file for technical details."


def _lookup_local_whisper(app: Any) -> Any:
    """Look up the local Whisper engine from the app's model registry.

    Returns ``None`` when the app has no ``models`` attribute, the models
    object exposes no ``registry``, or the registry has no ``whisper``
    backend registered (cold start, or the user explicitly unloaded whisper).

    Used by :meth:`DictationPipeline._transcribe` to wire the local
    Whisper engine as the fallback for a CloudEngine, so that
    ``CloudEngine.transcribe_with_fallback`` actually has a local engine
    to fall back to when the cloud provider is unreachable.
    """
    try:
        models = getattr(app, "models", None)
    except Exception:  # pragma: no cover — defensive
        return None
    if models is None:
        return None
    registry = getattr(models, "registry", None)
    if registry is None:
        return None
    try:
        return registry.get("whisper")
    except Exception:  # pragma: no cover — defensive
        log.debug("[PIPELINE] _lookup_local_whisper: registry.get raised", exc_info=True)
        return None


@contextlib.contextmanager
def _timed_stage(timings: dict[str, float], name: str) -> typing.Iterator[None]:
    """Context manager that records a single stage's wall-clock duration.

    ZR-64: replaces the 10 inline ``_stage_t0 = time.perf_counter()`` /
    ``_<name>_ms = (time.perf_counter() - _stage_t0) * 1000`` blocks in
    ``DictationPipeline.run`` with a single DRY primitive. Adding an
    11th stage no longer requires hand-copying the 3-line pattern AND
    hand-adding a variable to the consolidated ``[PIPE-PERF]`` log
    format string — just wrap the stage call in
    ``with _timed_stage(_timings, "<name>")`` and the dict entry
    appears automatically.

    Notes:
      * The duration is recorded in *milliseconds* (matching the
        previous inline pattern) so the consolidated log format
        strings are unchanged.
      * On exception, the duration up to the raise is still recorded
        (the ``finally`` runs before the exception propagates) so a
        ``[PIPE-PERF]`` line emitted from the ``except`` block has a
        best-effort timing for the stage that failed.
      * The dict is mutated in-place; callers pass a single dict
        instance and read ``timings["<name>"]`` after the ``with``
        block exits.
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = (time.perf_counter() - t0) * 1000


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
        # Throttle _check_resources to once per 60s. The values
        # change slowly and are only needed for post-crash triage.
        self._last_resources_check_ts: float = 0.0
        self._resources_check_interval: float = 60.0
        # NEW-PERF-010: pre-computed (rms, peak, silence_pct) from
        # Recorder.stop(), passed through to the transcription engine
        # so it doesn't recompute the same stats on the same audio.
        self._audio_stats: tuple[float, float, float] | None = None
        # S3-CR-10 (defense-in-depth observability): tracks whether
        # ``_apply_templates`` modified the text in this cycle. If it
        # did, the text MAY contain clipboard-substituted content
        # (``{clipboard}`` → ``pyperclip.paste()``), which is a
        # privacy-sensitive surface when LLM polish is enabled. The
        # CR-10 fix in ``llm_polish._call_api`` applies
        # ``redact_pii`` before the API send — this flag lets
        # ``_apply_llm_polish`` log a privacy NOTICE so operators can
        # audit when substituted content is flowing toward the LLM
        # redaction gate, and fail-closed if ``redact_pii`` itself is
        # unimportable.
        self._templates_applied: bool = False

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

        RW-13: the ``cycle_id`` is also published as the active correlation
        id via :func:`voice_typer.server.log.set_correlation_id` so every
        log emitted across the pipeline stages (transcribe, clean, LLM
        polish, clipboard, tray) carries ``correlation_id=<cycle_id>`` in
        JSON mode — tying the whole cycle together for triage.  It is
        reset at the end of the method (the ``finally`` block below) so a
        finished cycle can't leak its id into a later, unrelated log line.
        """
        _corr_token: object | None = None  # RW-13: correlation-id reset token (reset at end of run)
        self._audio = audio
        self._duration = duration
        self._recorded_rms = recorded_rms
        self._cycle_id = cycle_id
        self._watchdog = watchdog
        # RW-13: publish cycle_id as the correlation id for this thread's
        # logging context.  Capture the token to reset in the finally block.
        from voice_typer.server.log import set_correlation_id

        if cycle_id:
            _corr_token = set_correlation_id(cycle_id)
        # NEW-PERF-010: capture the pre-computed audio stats from the
        # recorder so we can pass them to the transcription engine.
        self._audio_stats = getattr(self._app.recorder, "_last_audio_stats", None)
        _t0 = time.perf_counter()

        try:
            log.info("[TRANSCRIBE] Starting transcription... (cycle=%s)", self._cycle_id)

            # PRE-FLIGHT: resource health check — provides diagnostic
            # context (RAM, disk, GPU) if a heap corruption crash occurs.
            # Throttle to once every 60s. The values change slowly
            # and are only needed for post-crash triage, not per-utterance
            # decisions. Previously ran every utterance (~2-5ms of system/
            # driver calls each).
            self._check_resources_throttled()

            # PERF-FIX-001: per-stage timing instrumentation.
            # Stage durations are collected and logged as a single
            # consolidated line at the end to reduce log verbosity.
            # Individual stage lines are available at DEBUG level.
            #
            # ZR-64: stage timing is recorded via the ``_timed_stage``
            # context manager (one entry per stage in ``_timings``) so
            # adding an 11th stage is a one-line ``with`` instead of a
            # 3-line ``_stage_t0`` / ``_<name>_ms =`` pair AND a
            # hand-edited format string in the consolidated log below.
            _timings: dict[str, float] = {}

            # Step 1: Transcribe (streaming finalize or direct)
            with _timed_stage(_timings, "transcribe"):
                text = self._transcribe()

            _elapsed = time.perf_counter() - _t0
            log.info(
                "[TRANSCRIBE] Transcription complete (len=%d, took=%.1fs, cycle=%s)",
                len(text) if text else 0,
                _elapsed,
                self._cycle_id,
            )
            log.debug(
                "[PIPE-PERF] transcribe: %.0f ms (cycle=%s)",
                _timings.get("transcribe", 0.0),
                self._cycle_id,
            )

            # Step 2: Check for empty result
            if not text:
                self._handle_empty_transcription()
                return

            # Step 3: Text cleanup
            with _timed_stage(_timings, "clean"):
                text = self._clean_text(text)

            # Step 4: Vocabulary correction
            with _timed_stage(_timings, "vocab"):
                text = self._apply_vocabulary(text)

            # Step 5: Template matching
            with _timed_stage(_timings, "templates"):
                text = self._apply_templates(text)

            # Step 6: Auto-punctuation
            with _timed_stage(_timings, "punct"):
                text = self._apply_punctuation(text)

            # Step 7: LLM polish
            with _timed_stage(_timings, "llm"):
                text = self._apply_llm_polish(text)

            # Step 7b: AI enhancement (P4)
            with _timed_stage(_timings, "ai"):
                text = self._apply_ai_enhancement(text)

            # Step 7c: Vocabulary automation analysis (P5)
            with _timed_stage(_timings, "vocab_auto"):
                self._analyze_vocabulary(text)

            # Step 8: Store in history + crash recovery
            with _timed_stage(_timings, "store"):
                self._store_result(text)

            # CR-006 (IMPROVE-mode run, 2026-07-21): check if this cycle was
            # force-cancelled by the watchdog while the stuck ctranslate2 call
            # was still running. If so, the user has already been notified
            # ("Transcription took too long and was cancelled") and has likely
            # alt-tabbed to another window. Pasting the late transcription
            # now would corrupt whatever window currently has focus. Skip the
            # paste, write the text to crash-recovery (so the user can review
            # it manually), and exit gracefully.

            # The membership check MUST be performed under
            # ``_cancelled_cycle_ids_lock`` — the set is mutated under that
            # lock elsewhere (see ``recording_controller._force_recover``).
            # CPython's GIL makes ``set.__contains__`` atomic in isolation,
            # but the consistent locking discipline avoids the torn-read
            # hazard and keeps the audit story clean. Fall back to
            # "not cancelled" if the lock or set is missing (defensive —
            # the attrs always exist on a real RecordingController).
            _cancelled_set = getattr(self._app.recording, "_cancelled_cycle_ids", None)
            _cancelled_lock = getattr(self._app.recording, "_cancelled_cycle_ids_lock", None)
            if _cancelled_set is not None and _cancelled_lock is not None:
                with _cancelled_lock:
                    _is_cancelled = self._cycle_id in _cancelled_set
            else:
                _is_cancelled = False
            if _is_cancelled:
                log.warning(
                    "[DICTATION] skipping paste of late transcription (cycle %s was force-cancelled by watchdog)",
                    self._cycle_id,
                )
                try:
                    # Persist to crash-recovery so the user can review the
                    # late transcription manually (without auto-pasting it).
                    if hasattr(self._app, "_crash_recovery"):
                        self._app._crash_recovery.add(text, pasted=False)
                except Exception:
                    log.debug("[DICTATION] crash-recovery write for cancelled cycle failed", exc_info=True)
                # Tear down the bubble + tray state — the watchdog already
                # set tray to IDLE, but the bubble may still be showing
                # "Transcribing…" if the watchdog's tray update happened
                # before the bubble wiring was reset.
                try:
                    if self._app.config.bubble_behavior == "always_visible":
                        self._app._waveform_bubble.set_state("idle")
                    else:
                        self._app._waveform_bubble.hide()
                except Exception:
                    log.debug("[DICTATION] bubble hide on cancelled cycle failed", exc_info=True)
                # Skip Step 9 (paste) — the cycle was cancelled.
                return

            # Step 9: Copy to clipboard + paste
            with _timed_stage(_timings, "paste"):
                self._copy_and_paste(text)

            _total_ms = (time.perf_counter() - _t0) * 1000
            log.info(
                "[PIPE-PERF] total=%.0fms, stages: transcribe=%.0f, clean=%.0f, "
                "vocab=%.0f, templates=%.0f, punct=%.0f, store=%.0f, "
                "paste=%.0f (cycle=%s)",
                _total_ms,
                _timings.get("transcribe", 0.0),
                _timings.get("clean", 0.0),
                _timings.get("vocab", 0.0),
                _timings.get("templates", 0.0),
                _timings.get("punct", 0.0),
                _timings.get("store", 0.0),
                _timings.get("paste", 0.0),
                self._cycle_id,
            )
            if _timings.get("llm", 0.0) > 1:
                log.info(
                    "[PIPE-PERF] llm_polish=%.0fms, ai_enhance=%.0fms, vocab_auto=%.0fms (cycle=%s)",
                    _timings.get("llm", 0.0),
                    _timings.get("ai", 0.0),
                    _timings.get("vocab_auto", 0.0),
                    self._cycle_id,
                )

        except Exception as e:
            log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", self._cycle_id)
            # XA-6-3 / XA-6-19: surface the failure in the bubble instead
            # of immediately hiding it. The bubble has an `error` mode that
            # renders a red "⚠ Error" pill plus a retry affordance
            # (XA-6-13). Previously the failure path called `set_state("idle")`
            # or `hide()`, which masked the symptom from the user -- the only
            # signal was the tray icon flipping to ERROR, which the user
            # often does not see (tray is collapsed / on another monitor).
            #
            # We keep the bubble visible in `error` mode for a bounded
            # window (3s, matching the tray ERROR->IDLE timer below) so the
            # user actually sees the failure, then fall back to the
            # always_visible / hide() path so the bubble doesn't stay red
            # forever. The error->idle transition is scheduled on the same
            # `_schedule_timer` facility used by the tray ERROR->IDLE
            # transition so they stay in sync.
            try:
                self._app._waveform_bubble.set_state("error")

                def _bubble_error_to_idle() -> None:
                    try:
                        if self._app.config.bubble_behavior == "always_visible":
                            self._app._waveform_bubble.set_state("idle")
                        else:
                            self._app._waveform_bubble.hide()
                    except Exception:
                        log.debug("[PIPELINE] bubble error->idle transition failed", exc_info=True)

                self._app._schedule_timer(3.0, _bubble_error_to_idle)
            except Exception:
                log.debug("[PIPELINE] bubble set_state('error') on failure failed", exc_info=True)
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
                    # CR-006: discard this cycle from the cancelled set so
                    # the set doesn't grow unboundedly across cycles. ``discard``
                    # is a no-op if the cycle wasn't cancelled (the normal path).
                    _cancelled_lock = getattr(recording, "_cancelled_cycle_ids_lock", None)
                    _cancelled_set = getattr(recording, "_cancelled_cycle_ids", None)
                    if _cancelled_lock is not None and _cancelled_set is not None:
                        with _cancelled_lock:
                            _cancelled_set.discard(self._cycle_id)
            try:
                session = self._app.recording.get_streaming_session()
                if session is not None and not self._app.recorder.recording:
                    self._app.recording.set_streaming_session(None)
            except Exception:
                log.debug("[TRANSCRIBE] finally: session cleanup failed", exc_info=True)
            with contextlib.suppress(Exception):
                self._app._busy_event.set()  # busy = False
            # ARCH-016 / H-17: clear ``_transcription_thread`` under
            # ``RecordingController._watchdog_lock`` — the SAME lock
            # that guards the field's write (``RecordingController._stop_impl``
            # assigns ``self._transcription_thread = threading.Thread(...)``
            # under ``_watchdog_lock``) and read
            # (``_force_recover_from_stuck_transcription`` snapshots
            # ``self._transcription_thread`` under ``_watchdog_lock``).
            #
            # Pre-H-17 this clear used ``self._app._lock`` — a DIFFERENT
            # lock — which provided ZERO mutual exclusion against the
            # write/read in recording_controller.py. The torn-read hazard
            # was real: a concurrent ``_stop_impl`` could be mid-assignment
            # of ``self._transcription_thread`` (Thread object → None or
            # vice versa) when this clear ran, and the watchdog could
            # observe a stale or partially-constructed reference.
            #
            # ARCH-REFAC-003: write directly to RecordingController (was a
            # @property delegate previously).
            #
            # Defensive fallback: if the lock is unavailable (e.g. the
            # recording controller was torn down or a stub app lacks
            # ``_watchdog_lock``), we still want to clear the field —
            # but log the race so the torn-read hazard is observable.
            _recording = getattr(self._app, "recording", None)
            _watchdog_lock = getattr(_recording, "_watchdog_lock", None) if _recording is not None else None
            try:
                if _watchdog_lock is not None:
                    with _watchdog_lock:
                        _recording._transcription_thread = None
                else:
                    # Defensive: very old or stub app without
                    # ``recording._watchdog_lock`` — clear without the
                    # lock and log so the gap is visible.
                    raise AttributeError("recording._watchdog_lock not present")
            except Exception:
                # Defensive: if the lock is unavailable we still want
                # to clear the field — but log the race.
                log.debug(
                    "[TRANSCRIBE] could not acquire recording._watchdog_lock "
                    "to clear _transcription_thread; assigning without lock",
                    exc_info=True,
                )
                with contextlib.suppress(Exception):
                    if _recording is not None:
                        _recording._transcription_thread = None
            # Downgrade from full gc.collect() to gc.collect(0).
            # Full GC scans the entire Python heap (gen 0+1+2) — with a
            # loaded Whisper model (500MB-3GB of tensors → millions of
            # wrapper objects), a full pass takes 50-500ms, paid on every
            # single transcription cycle. Generation-0 only (~1-5ms)
            # catches the per-cycle allocations (audio buffers, segment
            # lists, IPC dicts) without scanning long-lived model objects.
            with contextlib.suppress(Exception):
                import gc

                gc.collect(0)
            log.debug("[TRANSCRIBE] busy reset to False (cycle=%s)", self._cycle_id)
            # RW-13: clear the correlation id published at the top of run().
            # This runs in the finally block so it executes on both the
            # success and the handled-exception paths, so a finished
            # transcription cycle can't leak its id into a later, unrelated
            # log line (e.g. the next cycle, or a background prewarm thread
            # sharing this process).
            if _corr_token is not None:
                from voice_typer.server.log import reset_correlation_id

                reset_correlation_id(_corr_token)

    # ── Pipeline steps ────────────────────────────────────────────

    def _check_resources_throttled(self) -> None:
        """Throttled wrapper around _check_resources.

        Runs the actual check at most once per `_resources_check_interval`
        seconds (default 60s). The values change slowly and are only
        needed for post-crash triage, not per-utterance decisions.
        Previously ran every utterance (~2-5ms of system/driver calls).
        """
        import time as _time

        now = _time.monotonic()
        if now - self._last_resources_check_ts < self._resources_check_interval:
            return
        self._last_resources_check_ts = now
        self._check_resources()

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
        """Step 1: Get transcription via streaming finalize or direct.

        Returns the transcript from the active streaming session (if one
        is open) or the active ASR backend (Whisper / Parakeet / Qwen /
        Cloud) via ``transcribe_with_fallback``.
        """
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

            # a-review Finding 8: previously this call was wrapped in a
            # broad ``try/except TypeError`` to handle backends that
            # didn't yet accept ``audio_stats``. That catch was too
            # broad — a ``TypeError`` raised inside the function body
            # (``None.lower()``, bad indexing, etc.) was also caught
            # and the retry either failed the same way (confusing
            # trace) or masked the original bug. All four backends
            # (Whisper/Parakeet/Qwen/Cloud) now accept ``audio_stats``
            # as a keyword argument, so the fallback is no longer
            # needed.

            # When the active backend is a CloudEngine, look
            # up the local whisper engine from the model registry and
            # pass it as ``local_engine=``.  This makes the cloud→local
            # fallback path actually fire when the cloud provider is
            # unreachable — previously the ``local_engine=`` parameter
            # existed but NO caller passed it, so the fallback was dead
            # code (transcription failed outright when the cloud was
            # down).  When the active backend is already a local engine
            # (Whisper/Parakeet/Qwen), ``local_engine`` is left as None.
            local_engine = None
            if isinstance(active, CloudEngine):
                local_engine = _lookup_local_whisper(self._app)
            text = active.transcribe_with_fallback(
                self._audio,
                audio_stats=self._audio_stats,
                local_engine=local_engine,
            )

        # PERF-015 / HIGH-19: refresh the LRU timestamp for the active backend
        # so it isn't evicted as least-recently-used after a successful
        # transcribe. touch_active_model() is guarded internally and safe to
        # call when no backend is active.
        with contextlib.suppress(Exception):
            self._app.models.touch_active_model()

        active = self._app.models.active_transcriber()
        self._device_info = (
            active.device_info if active is not None and hasattr(active, "device_info") else "Parakeet ASR"
        )

        # Empty-transcription diagnostic: when the engine returns an
        # empty string without raising, the downstream
        # ``_handle_empty_transcription`` will suppress the user-facing
        # notification for short recordings — leaving the user with no
        # feedback at all. Surface a single consolidated log line with
        # every signal we have (duration, RMS, backend type, audio
        # stats, streaming vs batch path) so the empty result is
        # traceable from the log file. This does NOT change behavior;
        # it only makes the existing silent-failure path visible to
        # developers diagnosing the "finish dictation → nothing
        # transcribed" symptom.
        if not text:
            backend_name = type(active).__name__ if active is not None else "<none>"
            stats_repr = (
                "rms={:.4f} peak={:.4f} silence_pct={:.1f}".format(*self._audio_stats)
                if self._audio_stats is not None
                else "<unavailable>"
            )
            log.warning(
                "[TRANSCRIBE] Empty transcription result (cycle=%s, "
                "duration=%.2fs, recorded_rms=%.4f, audio_stats=[%s], "
                "backend=%s, path=%s) — see _handle_empty_transcription",
                self._cycle_id,
                self._duration,
                self._recorded_rms,
                stats_repr,
                backend_name,
                "streaming" if session is not None else "batch",
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

        REFINED-SILENCE-GRACE: the original grace-period suppression fired
        for EVERY short recording, including ones with clear audio (high
        RMS) where the engine returned empty. That hid the
        "finish-dictation-→-nothing-transcribed" failure mode entirely:
        the user saw no clipboard output, no error toast, no tray status
        beyond "No speech detected" — even when their mic was working
        fine and the engine was the real culprit (e.g. a misconfigured
        model, a backend that returns "" without raising). The fix
        narrows the suppression to ONLY the case it was designed for:
        short recordings with NEAR-SILENCE (recorded_rms below the same
        0.005 threshold used in the long-recording branch). Short
        recordings with real audio still suppress the popup notification
        (a 5s clip with no transcription is too ambiguous to be worth a
        modal alert) but the tray status now reflects "transcription
        returned empty" so the user knows something happened, and a
        warning is logged so the failure is traceable.
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
        # Same near-silence threshold used by the long-recording branch
        # below — keeps the "audio was actually captured" detection
        # consistent across both branches.
        _silence_rms_threshold = 0.005
        _audio_was_captured = self._recorded_rms >= _silence_rms_threshold

        if self._duration < _grace_period and not _audio_was_captured:
            # Short recording AND near-silence: the user almost certainly
            # tapped the hotkey by accident or stopped immediately. This
            # is the original UX-SILENCE-GRACE case — suppress the
            # notification entirely.
            log.info(
                "[TRANSCRIBE] No speech detected but recording was only %.1fs "
                "(< %.0fs grace period) and near-silent (rms=%.4f) — suppressing notification",
                self._duration,
                _grace_period,
                self._recorded_rms,
            )
            self._app.tray.set_state(AppState.IDLE, "No speech detected")
        elif self._duration < _grace_period and _audio_was_captured:
            # Short recording BUT real audio was captured: the engine
            # returned empty despite picking up a non-trivial signal.
            # This is the silent-empty-transcription failure mode. Keep
            # the popup suppressed (a short clip is too ambiguous to
            # justify an alert) but surface a distinct tray status so
            # the user sees something happened, and log at WARNING so
            # the failure is traceable in the log file.
            log.warning(
                "[TRANSCRIBE] Short recording (%.1fs) with audio "
                "(rms=%.4f >= %.4f) produced empty transcription — "
                "engine returned no text (cycle=%s)",
                self._duration,
                self._recorded_rms,
                _silence_rms_threshold,
                self._cycle_id,
            )
            self._app.tray.set_state(AppState.IDLE, "Transcription returned empty")
        elif self._recorded_rms < _silence_rms_threshold:
            self._app.tray.set_state(AppState.IDLE, "No speech -- check microphone")
            self._app.tray.notify(
                APP_NAME,
                "No speech was detected and audio was near-silence.\n"
                "Your microphone may not be capturing audio.\n"
                "Check that the correct mic is selected and is active.",
            )
        else:
            # Long recording with real audio but the engine returned
            # empty — this is the unusual case where the model clearly
            # failed (15+ seconds of intelligible audio should produce
            # SOMETHING). Notify the user so they know to retry or
            # check the log file.
            log.warning(
                "[TRANSCRIBE] Long recording (%.1fs) with audio "
                "(rms=%.4f) produced empty transcription — engine "
                "returned no text (cycle=%s)",
                self._duration,
                self._recorded_rms,
                self._cycle_id,
            )
            self._app.tray.set_state(AppState.IDLE, "Transcription returned empty")
            self._app.tray.notify(
                APP_NAME,
                "Audio was recorded but no transcription was produced.\n"
                "This can happen if the model is misconfigured or the "
                "audio is unclear. Try again, or check the log file for "
                "details.",
            )
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
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — a fresh DictationPipeline is built per
            # transcription cycle, so flags on ``self`` reset every cycle
            # and the user got a tray notification on EVERY cycle where
            # the failure occurred. ERR-006/ERR-014's "notify once"
            # design depends on the flag surviving across cycles.
            if not getattr(self._app, "_vocab_fail_notified", False):
                self._app._vocab_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Vocabulary correction failed. Check the log file for details.",
                    )
        return text

    def _apply_templates(self, text: str) -> str:
        """Step 5: Apply template matching.

        ERR-014: promoted ``log.debug`` to ``log.warning`` + tray notify.

        S3-CR-10 (defense-in-depth observability): when a template
        match modifies the text, set ``self._templates_applied = True``
        so the downstream ``_apply_llm_polish`` step can log a privacy
        NOTICE. Templates may substitute ``{clipboard}`` with the
        user's current clipboard content (which can contain passwords,
        2FA codes, private messages) — if LLM polish is then enabled,
        that content would flow toward the third-party LLM API. The
        CR-10 fix in ``llm_polish._call_api`` applies ``redact_pii``
        before the API send; this flag does NOT change that redaction
        behavior — it only makes the substituted-content flow visible
        in the log so operators can audit when template-substituted
        text is reaching the LLM redaction gate, and triggers a
        fail-closed sanity check in ``_apply_llm_polish``.
        """
        try:
            if getattr(self._app.config, "templates_enabled", True):
                if self._app._template_manager is None:
                    from voice_typer.server.templates import TemplateManager

                    self._app._template_manager = TemplateManager()
                expanded = self._app._template_manager.match(text)
                if expanded is not None:
                    log.info("[TEMPLATE] Matched template, expanded %d -> %d chars", len(text), len(expanded))
                    # S3-CR-10: mark that templates modified the text
                    # this cycle. The downstream LLM polish step uses
                    # this flag to log a privacy NOTICE and to gate a
                    # fail-closed sanity check on ``redact_pii`` — it
                    # does NOT gate or modify the polish call itself
                    # (the redaction is already applied by CR-10 inside
                    # ``llm_polish._call_api``).
                    self._templates_applied = True
                    text = expanded
        except Exception:
            log.warning("[PIPELINE] Template matching failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — see ``_apply_vocabulary`` for rationale.
            if not getattr(self._app, "_template_fail_notified", False):
                self._app._template_fail_notified = True
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
        """Step 7: Apply LLM polishing (if consented).

        S3-CR-10 (defense-in-depth observability + fail-closed): if
        templates were applied earlier in this cycle
        (``self._templates_applied``), the text MAY contain
        clipboard-substituted content (passwords, 2FA codes, private
        messages from ``{clipboard}``). When LLM polish is enabled,
        that content would flow to a third-party LLM API. The CR-10
        fix in ``llm_polish._call_api`` applies ``redact_pii`` to the
        user-content before the API send — this method does NOT
        duplicate that redaction (it would change the final pasted
        text on polish-failure paths). Instead, it:

          1. Logs a privacy NOTICE so operators can audit when
             template-substituted content is flowing toward the CR-10
             redaction gate.
          2. Performs a sanity check that ``redact_pii`` is importable
             BEFORE calling ``polish()``. If the import fails AND
             templates were applied this cycle, polish is SKIPPED
             entirely (fail-closed) — without ``redact_pii``, the
             CR-10 gate inside ``_call_api`` would also fail open
             (its try/except falls through to sending the original
             text). Skipping polish preserves the original text on
             the paste path (the user sees their transcription, not a
             leaked LLM payload). When templates were NOT applied,
             the sanity check is skipped — the text is the user's own
             dictation, not substituted content, so the privacy risk
             is much lower and the CR-10 fail-open is acceptable.
        """
        effective_llm_key = self._app.config.llm_api_key or getattr(self._app.config, "openai_api_key", "")
        if self._app.config.llm_polish and effective_llm_key and getattr(self._app.config, "llm_polish_consent", False):
            # S3-CR-10: privacy NOTICE when templates were applied
            # before LLM polish. The CR-10 redaction gate inside
            # ``llm_polish._call_api`` strips common PII patterns
            # (credit cards, SSNs, emails, phone numbers, API keys)
            # before the API send — but operators should be able to
            # audit when template-substituted content is flowing
            # toward that gate. Logged at INFO so it's visible at the
            # default log level without being alarmist (the redaction
            # is in place; this is observability, not a warning).
            if self._templates_applied:
                log.info(
                    "[LLM_POLISH] Templates were applied before LLM polish this cycle — "
                    "text MAY contain substituted content (e.g. {clipboard}). CR-10 "
                    "redact_pii gate in llm_polish._call_api will strip common PII "
                    "patterns (cards/SSNs/emails/phones/API keys) before the API send. "
                    "(cycle=%s)",
                    self._cycle_id,
                )
                # Defense-in-depth sanity check: verify redact_pii is
                # importable BEFORE calling polish(). If the import
                # fails, the CR-10 gate inside _call_api would also
                # fail open (its try/except falls through to sending
                # the original text). Skip polish entirely
                # (fail-closed) so the un-redacted clipboard-
                # substituted text does NOT reach the LLM API.
                try:
                    from voice_typer.server.security import redact_pii as _redact_pii_sanity_check  # noqa: F401
                except ImportError:
                    log.warning(
                        "[LLM_POLISH] redact_pii not importable (security module broken) "
                        "AND templates were applied this cycle — skipping LLM polish to "
                        "prevent potential clipboard-content exfiltration (S3-CR-10 fail-closed). "
                        "(cycle=%s)",
                        self._cycle_id,
                    )
                    return text
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
                # XZ-PII-05: redact the exception message before
                # logging. LLM API errors can echo the request URL +
                # Authorization header (which carries the API key) back
                # in their body; ``redact_secret`` masks ``Bearer …`` /
                # ``sk-…`` / 20+ char bare tokens so the log line is
                # safe to surface in the tray / log file.
                from voice_typer.server._secrets import redact_secret

                log.warning("[LLM_POLISH] Polish failed: %s", redact_secret(str(exc)))
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
            from voice_typer.server import event_bus

            event_bus.publish({"type": "llm_polish_failed"})
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
            # AC-49: the previous ``getattr(self, "_segments", None) or []``
            # and ``getattr(self, "_confidence", 0.9)`` fell back to a
            # fabricated confidence of ``0.9`` when the attributes were
            # absent — that fed vocabulary-automation with a confident
            # empty segment list, causing the analyzer to consider
            # every word as high-confidence. Replaced with explicit
            # module-level sentinels (no ``self.*`` reads, no
            # fabricated confidence). The analyzer's degrade-gracefully
            # path now sees honest empty data.
            segments: list = _EMPTY_SEGMENTS
            confidence: float = _NO_TRANSCRIPT_CONFIDENCE
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
            # CR-93: previously a bare ``except Exception: pass``. If
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
                # NEW-UX-006: surface the paste failure as a renderer
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
