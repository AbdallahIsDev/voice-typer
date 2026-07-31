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
import threading
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


# UE-47: distinct error for the "active ASR backend was never loaded"
# failure mode. Pre-fix, ``_transcribe`` always returned the empty
# string when ``transcribe_with_fallback`` produced no text — the
# downstream ``EmptyCheckStage`` then ran ``_handle_empty_transcription``
# which shows the ambiguous "No speech detected" toast regardless of
# whether the user was silent or the model was unloaded. Raising this
# sentinel from ``_transcribe`` (only when ``active.is_loaded`` is
# False AND the engine returned empty) bypasses ``EmptyCheckStage``
# entirely (the exception propagates out of ``TranscribeStage`` and is
# caught by ``run()``'s generic ``except Exception`` block) so the user
# sees a friendly "model not loaded" message instead of the misleading
# "no speech" toast. Subclass of ``RuntimeError`` so existing
# ``except RuntimeError`` / ``except Exception`` clauses still catch it.
class BackendNotLoadedError(RuntimeError):
    """Raised when the active ASR backend is not loaded at transcribe time.

    UE-47: an unloaded backend (``is_loaded is False``) can return ``""``
    from ``transcribe_with_fallback`` without raising — making the empty-
    transcription path indistinguishable from genuine silence. This
    sentinel is raised by ``DictationPipeline._transcribe`` ONLY when
    ``active.is_loaded`` was False BEFORE the transcribe call AND the
    engine returned empty output, so the run() generic ``except
    Exception`` block surfaces a friendly "model not loaded" message
    instead of falling through to ``_handle_empty_transcription``.

    The ``engine_name`` kwarg captures the backend type for telemetry /
    IPC ``isinstance`` narrowing — mirrors the pattern used by
    ``ConsentRequiredError`` in ``asr_errors.py``.
    """

    def __init__(self, message: str = "", *, engine_name: str | None = None) -> None:
        super().__init__(message)
        self.engine_name = engine_name


# ERR-005: raw exception messages from ctranslate2 / torch / faster-whisper
# often leak file paths, CUDA versions, and internal stack details into
# user-facing tray notifications. Map known exception classes to friendly
# messages; fall back to a generic message for unknown errors.
def _friendly_transcription_error(exc: BaseException) -> str:
    """Return a user-friendly message describing a transcription failure."""
    # UE-47: BackendNotLoadedError has a distinct, friendly message —
    # do NOT fall through to the generic "model could not be loaded"
    # branch (which is about download/load-time failures, not an
    # unloaded backend at transcribe time). The user needs a different
    # recovery hint: "wait for the model to finish loading" vs.
    # "check your internet connection".
    if isinstance(exc, BackendNotLoadedError):
        return (
            "The speech model was not loaded when dictation finished. "
            "Wait for it to finish loading, or open Settings to verify "
            "the model is available."
        )
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


class _AbortWatcher:
    """Lightweight daemon thread that bridges the recording controller's
    cancel set to the active ASR engine's abort API.

    The recording controller's cancel path (ESC hotkey, watchdog
    force-recover) adds the current ``cycle_id`` to
    ``recording._cancelled_cycle_ids`` under
    ``_cancelled_cycle_ids_lock``. Pre-fix, that was the END of the
    abort story — the transcription thread kept running ctranslate2 /
    transformers / cloud-HTTP inference to completion (potentially
    10-30s for Whisper, 30s+ for cloud), then the late result was
    dropped by the pipeline's ``CancellationGuard`` before paste.

    This watcher polls ``_cancelled_cycle_ids`` every 100ms while
    inference is running; when the cycle appears in the set, it calls
    ``engine.request_abort()`` which sets the engine's ``_abort_event``
    so:

      * **Whisper** (``transcription.py``) — the segment loop breaks
        early on the next iteration; ``ctranslate2.Translator.interrupt()``
        is also best-effort called to unblock the current C-level call.
      * **Parakeet** (``parakeet_engine.py``) — the
        ``_AbortStoppingCriteria`` returns True on the next generated
        token, so ``model.generate()`` returns early. Long-audio chunk
        loops also break after the current chunk.
      * **Cloud** (``cloud_engines.py``) — the retry loop checks the
        event at the top of each iteration and bails out instead of
        issuing another 10s HTTP call.

    Polling is used (rather than a callback / condition variable)
    because the cancel path lives in ``recording_controller.py`` (a
    module this pipeline does not own) and the cancelled-set is the
    existing coordination point. 100ms granularity is a deliberate
    trade-off: short enough that the user perceives near-instant
    compute release, long enough that the polling overhead (one lock
    acquire + set lookup, ~1us) is negligible vs. the inference cost
    per iteration (~0.5-3s for Whisper, ~1-2s for Parakeet, ~1-2s for
    cloud). The watcher is a daemon thread so it never blocks process
    exit.

    Lifetime: started in ``DictationPipeline._transcribe`` before the
    transcribe call, stopped (via ``stop()``) in a ``finally`` block
    after the call returns or raises. The stop method sets the
    watcher's own stop event and joins with a 1s timeout — if the
    watcher is mid-poll it exits within 100ms; the 1s ceiling is
    defense-in-depth.
    """

    _POLL_INTERVAL_SECONDS: float = 0.1

    def __init__(self, app: Any, cycle_id: str, engine: Any) -> None:
        self._app = app
        self._cycle_id = cycle_id
        self._engine = engine
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._abort_signalled: bool = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="DictationAbortWatcher",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self._POLL_INTERVAL_SECONDS):
            try:
                recording = getattr(self._app, "recording", None)
                if recording is None:
                    continue
                cancelled_set = getattr(recording, "_cancelled_cycle_ids", None)
                cancelled_lock = getattr(recording, "_cancelled_cycle_ids_lock", None)
                if cancelled_set is None or cancelled_lock is None:
                    continue
                with cancelled_lock:
                    is_cancelled = self._cycle_id in cancelled_set
                if is_cancelled:
                    log.info(
                        "[PIPELINE] abort watcher detected cancel for cycle %s — signalling engine.request_abort()",
                        self._cycle_id,
                    )
                    try:
                        self._engine.request_abort()
                    except Exception:
                        log.debug(
                            "[PIPELINE] engine.request_abort() raised (non-fatal)",
                            exc_info=True,
                        )
                    self._abort_signalled = True
                    return
            except Exception:
                log.debug(
                    "[PIPELINE] abort watcher poll failed (non-fatal)",
                    exc_info=True,
                )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)


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
        # DR-18: the 11-stage dictation pipeline. Each stage is a thin
        # delegator that calls the corresponding ``_<step>`` method on
        # this pipeline — see ``dictation_stages.build_default_stages``
        # for the full ordering and per-stage documentation. The run
        # loop iterates over this list (see ``run`` below) instead of
        # inlining each stage call, so adding an 12th stage is a
        # one-line list edit rather than a copy-paste of the
        # 3-line ``with _timed_stage`` pattern plus a hand-edited
        # consolidated-log format string.
        #
        # Built lazily on first access if missing — tests that bypass
        # ``__init__`` via ``__new__`` (see
        # ``test_dictation_pipeline_h17_and_s3_cr10_fixes.py``) don't
        # set this attribute, and ``run`` rebuilds the default list
        # when that happens so the finally-block teardown still
        # exercises the production code paths.
        from voice_typer.server.dictation_stages import build_default_stages

        self._stages: list = build_default_stages()

    def request_abort(self) -> None:
        """Signal the active ASR backend to abort in-flight inference.

        Public entry point for external callers (e.g. the recording
        controller's ESC cancel path / the watchdog's force-recover
        path) to request that the current ``transcribe_with_fallback``
        call return as soon as possible. Delegates to the active
        engine's ``request_abort()`` which sets an ``_abort_event``
        consumed by:

          * ``TranscriptionEngine._transcribe_unlocked`` — breaks the
            segment loop and best-effort calls
            ``ctranslate2.Translator.interrupt()``.
          * ``ParakeetEngine._transcribe_segment`` /
            ``_transcribe_batch`` — the ``_AbortStoppingCriteria``
            returns True on the next generated token, stopping
            ``model.generate()``.
          * ``CloudEngine._send_openai_compatible`` /
            ``_send_deepgram`` — the retry loop checks the event at
            the top of each iteration and bails out.

        The internal ``_AbortWatcher`` (started in ``_transcribe``)
        already calls this method when ``recording._cancelled_cycle_ids``
        contains the current cycle, so external callers that already
        add to that set do NOT need to also call this method — the
        watcher will pick it up within 100ms. This method is the
        direct, lower-latency path for callers that want to skip the
        polling delay (e.g. the watchdog's force-recover path, which
        has already decided the cycle is unrecoverable).

        Best-effort: catches every exception so a broken engine never
        propagates a failure to the caller. The abort token is a
        ``threading.Event`` — even if ``request_abort()`` raises, the
        engine's existing inference loop will continue (just without
        the early-exit signal). The caller's recovery path (e.g. the
        watchdog's ``_busy_event.set()``) is independent.
        """
        try:
            active = self._app.models.active_transcriber()
        except Exception:
            log.debug("[PIPELINE] request_abort: could not read active transcriber", exc_info=True)
            return
        if active is None:
            return
        if not hasattr(active, "request_abort"):
            return
        try:
            active.request_abort()
        except Exception:
            log.debug("[PIPELINE] request_abort: engine.request_abort() raised (non-fatal)", exc_info=True)

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
        # Write an in-flight sentinel so crash_recovery can detect
        # interrupted dictations on the next startup and emit a
        # dictation_lost event. The sentinel is cleared in the finally
        # block below — only a hard process crash leaves it behind.
        with contextlib.suppress(Exception):
            from voice_typer.server._paths import config_dir as _config_dir

            _sentinel = _config_dir() / ".dictation-in-flight"
            _sentinel.write_text(str(cycle_id), encoding="utf-8")
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
            #
            # DR-18: the 11 stages themselves live in
            # ``voice_typer.server.dictation_stages`` as a list of small
            # single-responsibility objects (``TranscribeStage``,
            # ``EmptyCheckStage``, …, ``PasteStage``). The run loop
            # iterates over ``self._stages`` and delegates each call to
            # the stage's ``run(text, ctx)`` method, which in turn calls
            # the corresponding ``_<step>`` method on this pipeline.
            # Cross-cutting concerns (cancellation check before paste,
            # empty-transcription early-exit) are handled by stage
            # wrappers / sentinel exceptions so the loop body stays
            # uniform. See ``dictation_stages.build_default_stages`` for
            # the full stage ordering and per-stage documentation.
            _timings: dict[str, float] = {}

            # Lazily rebuild the stage list if a test bypassed
            # ``__init__`` (e.g. ``DictationPipeline.__new__`` in
            # ``test_dictation_pipeline_h17_and_s3_cr10_fixes.py``).
            # Production code always has ``self._stages`` set by
            # ``__init__``.
            from voice_typer.server.dictation_stages import (
                PipelineContext,
                _PipelineAbortCancelled,
                _PipelineAbortEmpty,
                build_default_stages,
            )

            stages = getattr(self, "_stages", None) or build_default_stages()
            ctx = PipelineContext(
                cycle_id=self._cycle_id,
                audio=self._audio,
                app=self._app,
                pipeline=self,
            )
            text = ""
            for stage in stages:
                if getattr(stage, "timed", True):
                    with _timed_stage(_timings, stage.name):
                        text = stage.run(text, ctx)
                else:
                    text = stage.run(text, ctx)
                # Step 1's post-stage logging is unique (it reports the
                # total elapsed time since run-entry, not just the
                # stage's own duration). Kept inline here to preserve
                # the exact log format and timing reference.
                if stage.name == "transcribe":
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

        except _PipelineAbortEmpty:
            # DR-18: ``EmptyCheckStage`` already called
            # ``_handle_empty_transcription()`` (tray state, "no speech"
            # notification, busy-event clear) and raised this sentinel
            # to abort the pipeline cleanly. Fall through to the finally
            # block (sentinel clear, audio zero, watchdog reset,
            # transcription_thread clear, gc.collect, correlation reset)
            # — same as the original ``return`` after
            # ``_handle_empty_transcription``.
            pass
        except _PipelineAbortCancelled:
            # DR-18 / CR-006: ``CancellationGuard`` (wrapping
            # ``PasteStage``) already wrote the late transcription to
            # crash-recovery and tore down the bubble, then raised this
            # sentinel to skip the paste. Fall through to the finally
            # block — same as the original ``return`` after the
            # cancelled-cycle branch.
            pass
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
            # Clear the in-flight sentinel — dictation completed (success,
            # cancel, or handled exception). Only a hard process crash
            # leaves the sentinel behind for crash_recovery to detect.
            with contextlib.suppress(Exception):
                from voice_typer.server._paths import config_dir as _config_dir

                _sentinel = _config_dir() / ".dictation-in-flight"
                if _sentinel.exists():
                    _sentinel.unlink()
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
                # UE-10 / UE-9-F2 (FT-5 family): use
                # ``pop_streaming_session()`` (atomic get-and-clear
                # under the recording controller's
                # ``_streaming_session_lock``) instead of the racy
                # get-then-set pair. Pre-fix, between
                # ``get_streaming_session()`` (lock #1) and
                # ``set_streaming_session(None)`` (lock #2), a
                # concurrent ``_start_streaming_session_if_enabled``
                # could install a NEW session that the subsequent
                # ``set_streaming_session(None)`` would clobber —
                # silently killing an active streaming worker thread.
                # After rapid stop→start (user double-tap hotkey, or
                # auto-stop Timer immediately followed by hotkey),
                # the new recording's streaming session was killed
                # silently and streaming transcriptions stopped
                # appearing until the next restart.
                #
                # ``pop_streaming_session()`` owns the session AND
                # clears the slot under a SINGLE lock acquisition —
                # we never write back to the slot. If a new session
                # is installed concurrently, it lands AFTER our pop
                # and is preserved. Mirrors the DE-7 path in
                # ``shutdown_controller._do_cleanup`` and the
                # ARCH-018 path in
                # ``recording_controller._cancel_streaming_session``.
                #
                # If we popped a non-None session AND the recorder
                # is no longer recording (i.e. this session belongs
                # to a dictation cycle that has now ended — not to
                # a fresh recording that started during the run),
                # signal its cancel event so the background
                # streaming worker thread exits cleanly instead of
                # leaking until the next process shutdown.
                # ``session.cancel()`` is non-blocking by default
                # (ARCH-025) — it sets the cancel event and returns
                # immediately, matching the finally-block's
                # bounded-latency contract.
                session = self._app.recording.pop_streaming_session()
                if session is not None and not self._app.recorder.recording:
                    with contextlib.suppress(Exception):
                        session.cancel()
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

        UE-10-F9 (deferred refactor — spaghetti/monolith detection):
        This 185-line method is a self-contained resource probe
        (RAM / disk / GPU) inlined in the pipeline. It has NO
        dependencies on ``DictationPipeline`` instance state — only
        module-level imports (``os``, ``pathlib``, ``psutil``,
        ``torch``) and the module-level ``log`` logger. The full
        extraction to a dedicated ``resource_probe.py`` module is
        DEFERRED to the monolith-split phase: the mechanical
        extraction is to move the body to a
        ``ResourceProbe.check()`` classmethod (or
        ``resource_probe.check_resources()`` module function) and
        call it from ``_check_resources_throttled``. Coordinate
        with the broader pipeline-decomposition work (the DR-18
        stage extraction already pulled the *stage execution* into
        ``dictation_stages.py``; ``_check_resources`` is the next
        candidate but it's pre-flight, not a stage, so it belongs
        in a sibling helper module rather than ``dictation_stages``).
        For now, this method stays inlined — adding the comment so
        the next maintainer doesn't waste time wondering why a
        185-line system probe is living inside the dictation
        pipeline class.
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
                # XZ-EH-008 / XZ-EH-022: previously a bare ``except
                # Exception: pass`` — the docstring at the top of
                # ``_check_resources`` promises "failures are logged at
                # DEBUG level", but this branch silently swallowed the
                # ctypes fallback failure (e.g. ``GlobalMemoryStatusEx``
                # returning an error code on a stripped-down Windows
                # IoT build), leaving operators with no clue why the
                # RAM INFO line was missing. Emit a DEBUG line with the
                # traceback so the docstring's promise is honored.
                log.debug(
                    "[RESOURCE] RAM check (ctypes fallback) failed (non-fatal)",
                    exc_info=True,
                )

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
        except Exception:
            # XZ-EH-008 / XZ-EH-022: previously ``except (ImportError,
            # Exception): pass``. ``ImportError`` was redundant (Exception
            # already covers it) and the bare ``pass`` contradicted the
            # docstring's promise that "failures are logged at DEBUG
            # level". Emit a DEBUG line with the traceback so an
            # operator looking at voice-typer.log sees why the GPU
            # INFO line is absent (e.g. torch installed but CUDA
            # driver mismatch, ``torch.cuda.get_device_properties``
            # raising on a headless CI runner).
            log.debug(
                "[RESOURCE] GPU check failed (non-fatal)",
                exc_info=True,
            )

        log.debug("[RESOURCE] Pre-flight health check complete")

    def _transcribe(self) -> str:
        """Step 1: Get transcription via streaming finalize or direct.

        Returns the transcript from the active streaming session (if one
        is open) or the active ASR backend (Whisper / Parakeet / Qwen /
        Cloud) via ``transcribe_with_fallback``.

        UE-10 sibling: the streaming-session slot is now popped
        atomically via ``pop_streaming_session()`` BEFORE
        ``session.finalize()`` runs. Pre-fix, the slot was cleared
        AFTER finalize — an exception in finalize() leaked the stale
        session reference into the next dictation cycle's _transcribe,
        which would re-call finalize() on the already-torn-down
        session and crash. Atomic pop also eliminates the FT-5 family
        TOCTOU documented in UE-10 (concurrent
        ``_start_streaming_session_if_enabled`` could install a NEW
        session between get and set).

        UE-10-F6: ``active`` is captured ONCE at the top and reused
        for both the transcribe call and the ``device_info`` read
        below. Pre-fix, a second ``active_transcriber()`` call after
        the transcribe was both redundant (the backend rarely changes
        mid-cycle) and racy (a concurrent ``set_active_backend`` could
        swap the backend between the two calls, so ``device_info``
        reported the wrong device for the result just produced).

        UE-47: ``backend_was_loaded`` is captured BEFORE the transcribe
        call. If the engine returns empty AND ``backend_was_loaded`` is
        False, raise ``BackendNotLoadedError`` — this bypasses
        ``EmptyCheckStage`` (the exception propagates out of
        ``TranscribeStage`` and is caught by ``run()``'s generic
        ``except Exception`` block) so the user sees a friendly
        "model not loaded" message instead of the ambiguous "No speech
        detected" toast that ``_handle_empty_transcription`` would
        produce.
        """
        # UE-10-F6 / UE-47: capture the active transcriber ONCE — the
        # previous code made a second ``active_transcriber()`` call
        # after the transcribe to refresh ``device_info`` (redundant +
        # racy vs. a concurrent ``set_active_backend``). Reuse this
        # same local for ``device_info`` below. Also capture
        # ``is_loaded`` BEFORE the transcribe call so the empty-result
        # path can distinguish "engine returned empty" from "engine was
        # never loaded" (a backend that is not loaded can return "" from
        # ``transcribe_with_fallback`` without raising — UE-47).
        active = self._app.models.active_transcriber()
        backend_was_loaded = bool(getattr(active, "is_loaded", False))

        # Clear any stale abort from a previous cycle before starting
        # inference. ``clear_abort()`` is a no-op on engines that
        # don't expose the abort API (e.g. a test stub); the
        # ``hasattr`` guard makes this safe. After clearing, install
        # an ``_AbortWatcher`` that polls ``recording._cancelled_cycle_ids``
        # every 100ms and calls ``active.request_abort()`` when the
        # cycle is cancelled. The watcher bridges the recording
        # controller's cancel path (ESC / watchdog) to the engine's
        # abort API so inference actually stops instead of running to
        # completion while the late result is dropped by the paste
        # guard. The watcher is stopped in the ``finally`` block below.
        abort_watcher: _AbortWatcher | None = None
        if active is not None and hasattr(active, "clear_abort"):
            with contextlib.suppress(Exception):
                active.clear_abort()
            if hasattr(active, "request_abort"):
                abort_watcher = _AbortWatcher(self._app, self._cycle_id, active)
                abort_watcher.start()

        try:
            # UE-10 sibling: pop_streaming_session() atomically owns the
            # session AND clears the slot under a SINGLE lock acquisition.
            # If finalize() raises below, the slot is already clear — the
            # next dictation cycle starts with a clean slot rather than
            # re-entering the stale session. We never write back to the
            # slot (a concurrent _start_streaming_session_if_enabled could
            # install a NEW session that a set_streaming_session(None) would
            # clobber — see UE-10).
            session = self._app.recording.pop_streaming_session()
            if session is not None:
                log.info("[STREAMING] Finalizing streaming transcript (cycle=%s)", self._cycle_id)
                text = session.finalize(self._audio)
            else:
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
        finally:
            if abort_watcher is not None:
                with contextlib.suppress(Exception):
                    abort_watcher.stop()

        # PERF-015 / HIGH-19: refresh the LRU timestamp for the active backend
        # so it isn't evicted as least-recently-used after a successful
        # transcribe. touch_active_model() is guarded internally and safe to
        # call when no backend is active.
        with contextlib.suppress(Exception):
            self._app.models.touch_active_model()

        # UE-10-F6: reuse the captured ``active`` local for device_info
        # instead of calling ``active_transcriber()`` a second time. If
        # ``active`` is None (backend was unloaded mid-cycle by a
        # concurrent ``set_active_backend`` / ``change_model``), fall
        # back to the literal "Parakeet ASR" string — matching the
        # pre-fix behavior for the ``active is None`` edge case.
        self._device_info = (
            active.device_info if active is not None and hasattr(active, "device_info") else "Parakeet ASR"
        )

        # Empty-transcription diagnostic: when the engine returns an
        # empty string without raising, the downstream
        # ``_handle_empty_transcription`` will suppress the user-facing
        # notification for short recordings — leaving the user with no
        # feedback at all. Surface a single consolidated log line with
        # every signal we have (duration, RMS, backend type, audio
        # stats, streaming vs batch path, ``is_loaded`` state) so the
        # empty result is traceable from the log file. This does NOT
        # change behavior; it only makes the existing silent-failure
        # path visible to developers diagnosing the "finish dictation
        # → nothing transcribed" symptom.
        #
        # UE-47: include ``backend_is_loaded`` in the warning so
        # operators can distinguish the three failure modes that all
        # collapse to empty output: (1) genuine silence, (2) unloaded
        # backend returned "", (3) cloud provider returned 200 with
        # empty body. Pre-fix all three were indistinguishable from the
        # log — the only signal was "backend was empty". The
        # ``backend_is_loaded`` field makes case (2) traceable.
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
                "backend=%s, backend_is_loaded=%s, path=%s) — see _handle_empty_transcription",
                self._cycle_id,
                self._duration,
                self._recorded_rms,
                stats_repr,
                backend_name,
                backend_was_loaded,
                "streaming" if session is not None else "batch",
            )
            # UE-47: if the backend was not loaded when we entered
            # ``_transcribe``, the empty output is overwhelmingly likely
            # caused by the unloaded backend (``transcribe_with_fallback``
            # on an unloaded Whisper/Parakeet/Qwen typically returns ""
            # without raising). Raise a distinct error so the run()'s
            # generic ``except Exception`` block surfaces a friendly
            # "model not loaded" message instead of falling through to
            # ``_handle_empty_transcription`` (which would show the
            # ambiguous "No speech detected" toast — same as the user
            # who said nothing). This is the intended observability
            # improvement: the user can now distinguish "my mic is
            # broken" from "the model didn't load" from "I was silent".
            #
            # NOTE: this raise bypasses ``EmptyCheckStage`` entirely
            # because the exception propagates out of ``TranscribeStage``
            # (which calls ``self._transcribe()``) before
            # ``EmptyCheckStage`` runs. The run() ``except Exception``
            # block then surfaces the friendly message via
            # ``_friendly_transcription_error`` (which has an
            # isinstance branch for ``BackendNotLoadedError``).
            if not backend_was_loaded:
                raise BackendNotLoadedError(
                    "Active ASR backend is not loaded — "
                    "transcribe_with_fallback returned empty output. "
                    "Check that the model finished loading and that no "
                    "set_active_backend call unloaded it mid-cycle.",
                    engine_name=backend_name,
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
            # UE-10-F4 (observability): publish a ``dictation_suppressed``
            # event so the renderer can show a subtle inline bubble
            # ("recording too short — try again") instead of giving the
            # user zero feedback. Pre-fix, this branch silently
            # swallowed ALL user feedback for short near-silent
            # recordings — the user saw nothing and had no way to tell
            # their tap registered. The suppression threshold is NOT
            # lowered (that's a separate UX decision); we only add an
            # observability/UX channel for the suppressed branch. The
            # event payload is intentionally minimal (duration, RMS,
            # reason) so the renderer can decide whether to show the
            # bubble based on its own UX rules. Wrapped in
            # ``contextlib.suppress`` so a broken event bus (or an
            # unregistered event type under ``VOICE_TYPER_DEBUG_EVENTS=1``)
            # never aborts the suppression path — the tray state set
            # above is the source of truth; this event is purely
            # additive UX feedback.
            with contextlib.suppress(Exception):
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "dictation_suppressed",
                        "data": {
                            "duration": self._duration,
                            "recorded_rms": self._recorded_rms,
                            "reason": "short_silence",
                        },
                    }
                )
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
        """Step 3: Apply text cleanup (spacing, self-corrections, capitalization).

        XZ-R18-02: previously the only two middle-pipeline steps NOT
        wrapped in try/except (this method and ``_apply_punctuation``).
        If either threw, the exception propagated to the outer
        ``run()`` ``except Exception`` block — the tray flipped to
        ERROR, the dictation was aborted, and the transcription was
        NEVER saved to crash recovery because ``_store_result()``
        runs AFTER these steps. Wrap in try/except matching the
        ``_apply_vocabulary`` pattern: ``log.warning(...)`` + notify-once
        + return the original text so the user sees their (uncleaned)
        transcription and the cycle completes normally.
        """
        try:
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
        except Exception:
            log.warning("[PIPELINE] Text cleanup failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — see ``_apply_vocabulary`` for rationale.
            if not getattr(self._app, "_clean_text_fail_notified", False):
                self._app._clean_text_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Text cleanup failed. Check the log file for details.",
                    )
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
        """Step 6: Apply auto-punctuation.

        XZ-R18-02: previously NOT wrapped in try/except — see
        ``_clean_text`` for the rationale. ``_add_safe_terminal_punctuation``
        is a pure string operation but can still raise on malformed
        input (e.g. a ``text`` containing a surrogate that breaks
        ``str.endswith``). Return the original text on failure so the
        dictation completes.
        """
        try:
            if self._app.config.auto_punctuation:
                from voice_typer.server.text_cleanup import _add_safe_terminal_punctuation

                text = _add_safe_terminal_punctuation(text)
        except Exception:
            log.warning("[PIPELINE] Auto-punctuation failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — see ``_apply_vocabulary`` for rationale.
            if not getattr(self._app, "_punct_fail_notified", False):
                self._app._punct_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Auto-punctuation failed. Check the log file for details.",
                    )
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
                # XZ-R18-05: previously this except block only logged a
                # WARNING — the user paid for an LLM API call that never
                # produced output (or believed the feature was broken)
                # with NO diagnostic. Mirror the ``_apply_vocabulary``
                # notify-once pattern (tray notification on the FIRST
                # failure per session) AND publish a ``llm_polish_failed``
                # event to the in-process event bus so the renderer can
                # surface a one-time toast. The push event shape is a
                # bare ``{"type": "llm_polish_failed"}`` frame (no
                # payload) — see ``LLMPolishFailedEvent`` in
                # ``voice_typer/client/src/renderer/src/types/ipc/push_events.ts``.
                # The transcription itself is still delivered to the
                # user UN-polished (the original ``text`` is returned
                # below), so the event is purely informational.
                if not getattr(self._app, "_llm_polish_fail_notified", False):
                    self._app._llm_polish_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "LLM polish failed. Transcription shown raw; check the log file for details.",
                        )
                with contextlib.suppress(Exception):
                    from voice_typer.server import event_bus

                    event_bus.publish({"type": "llm_polish_failed"})
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

        FR-28 (privacy): if ``self._app.config.history_enabled`` is
        ``False``, the ``add_transcription`` call is skipped entirely
        (but the clipboard paste still happens — incognito mode only
        disables persistence, not the dictation flow). ``flush()`` is
        also skipped because there is no queued write to wait for.
        ``history_enabled`` defaults to ``True`` (preserving the
        pre-FR-28 behavior) so the field is only consulted when P4-A2
        has added it to ``Config``. ``getattr(..., True)`` is used so
        dictation still works on an older Config instance that hasn't
        yet picked up the new field.

        FR-10 (resilience): when ``add_transcription`` returns ``<= 0``
        (writer thread is dead or schema init failed — see
        ``history_db.add_transcription``'s FR-10 guard), we log +
        trigger the notify-once tray message instead of silently
        treating the placeholder as success. Previously the pipeline
        would call ``flush()`` after the failed enqueue and block 30s
        on a future that would never resolve — the FR-10 fix in
        ``history_db._submit_write`` makes the failure instant, and
        this check makes it visible to the user.
        """
        # FR-28: gate the entire history-DB block on history_enabled.
        history_enabled = getattr(self._app.config, "history_enabled", True)
        if history_enabled:
            try:
                row_id = self._app.history_db.add_transcription(
                    text,
                    duration=self._duration,
                    model=self._app.config.model_size,
                    device=self._app.config.device,
                )
                # FR-10: add_transcription returns -1 when the writer
                # thread is dead or schema init failed (see its FR-10
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
