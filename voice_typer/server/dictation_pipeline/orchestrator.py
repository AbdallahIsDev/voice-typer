"""Orchestrator mixin: ``__init__``, ``request_abort``, and the
``run`` god method that drives the 11-stage pipeline.

The orchestrator owns:

  * ``__init__`` — wires the pipeline to the ``VoiceTyperApp`` and
    builds the default stage list (``dictation_stages.build_default_stages``).
  * ``request_abort`` — public entry point for external callers
    (recording controller's ESC cancel path, watchdog's force-recover
    path) to signal the active ASR backend to abort in-flight
    inference. Best-effort: catches every exception so a broken
    engine never propagates a failure to the caller.
  * ``run`` — the entry point called from
    ``VoiceTyperApp._stop_dictation``. Runs on the transcription
    thread. Writes the in-flight sentinel, publishes the cycle_id as
    the correlation id, runs the 11 stages via
    ``dictation_stages.build_default_stages``, handles the
    ``_PipelineAbortEmpty`` / ``_PipelineAbortCancelled`` /
    ``except Exception`` paths, and runs the 7-step finally block
    (sentinel unlink, audio zero, watchdog reset, streaming-session
    cancel, busy-event clear, transcription-thread clear, gc.collect).

Originally inline methods on ``DictationPipeline`` in the 2077-LOC
monolith; extracted as a mixin with NO behavior change. The other
step mixins (transcribe / text / enhancement / storage / paste) are
composed with this one in ``dictation_pipeline/__init__.py`` to form
the final ``DictationPipeline`` class.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

import numpy as np

from voice_typer.server.branding import APP_NAME
from voice_typer.server.dictation_pipeline.helpers import (
    _friendly_transcription_error,
    _timed_stage,
)
from voice_typer.server.dictation_stages import (
    PipelineContext,
    _PipelineAbortCancelled,
    _PipelineAbortEmpty,
    build_default_stages,
)
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class _OrchestratorMixin:
    """Mixin: ``__init__``, ``request_abort``, and ``run`` orchestration.

    Class attributes:
      * ``_LLM_POLISH_PIPELINE_TIMEOUT_S`` — pipeline-side cap on how
        long the dictation thread will wait for the LLM polish
        round-trip. Exposed as a class attribute so tests can
        monkeypatch it to a small value (e.g. 0.1s) to exercise the
        timeout path without waiting 4s in real time.
    """

    # Pipeline-side cap on how long the dictation thread will wait for
    # the LLM polish round-trip. The underlying ``LLMPolisher._call_api``
    # uses a 10s socket timeout (``DEFAULT_TIMEOUT_S`` in
    # ``llm_polish.py``); this pipeline-side cap is intentionally
    # shorter (4s) so a stalled LLM endpoint does not block the
    # pipeline thread for the full 10s. On timeout the original
    # (unpolished) text is returned to the user; the polish thread
    # keeps running in the background (Python cannot cancel a blocking
    # ``urlopen`` call) and self-terminates when the inner 10s socket
    # timeout fires or the LLM responds. Exposed as a class attribute
    # so tests can monkeypatch it to a small value (e.g. 0.1s) to
    # exercise the timeout path without waiting 4s in real time.
    _LLM_POLISH_PIPELINE_TIMEOUT_S: float = 4.0

    # cached 11-stage list shared across all pipeline instances.
    # None until the first __init__ populates it. Stage objects are
    # stateless (each run reads from ctx, not self), so sharing is safe.
    _SHARED_STAGES: list | None = None

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
        # pre-computed (rms, peak, silence_pct) from
        # Recorder.stop(), passed through to the transcription engine
        # so it doesn't recompute the same stats on the same audio.
        self._audio_stats: tuple[float, float, float] | None = None
        #  (defense-in-depth observability): tracks whether
        # ``_apply_templates`` modified the text in this cycle. If it
        # did, the text MAY contain clipboard-substituted content
        # (``{clipboard}`` → ``pyperclip.paste()``), which is a
        # privacy-sensitive surface when LLM polish is enabled. The
        #  fix in ``llm_polish._call_api`` applies
        # ``redact_pii`` before the API send — this flag lets
        # ``_apply_llm_polish`` log a privacy NOTICE so operators can
        # audit when substituted content is flowing toward the LLM
        # redaction gate, and fail-closed if ``redact_pii`` itself is
        # unimportable.
        self._templates_applied: bool = False
        # the 11-stage dictation pipeline. Each stage is a thin
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
        # stage objects are stateless (each run reads from ctx,
        # not self), so a single shared list is reused across all
        # pipeline instances. Lazy-init via the class attribute.
        if _OrchestratorMixin._SHARED_STAGES is None:
            _OrchestratorMixin._SHARED_STAGES = build_default_stages()
        self._stages: list = _OrchestratorMixin._SHARED_STAGES

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

        the ``cycle_id`` is also published as the active correlation
        id via :func:`voice_typer.server.log.set_correlation_id` so every
        log emitted across the pipeline stages (transcribe, clean, LLM
        polish, clipboard, tray) carries ``correlation_id=<cycle_id>`` in
        JSON mode — tying the whole cycle together for triage.  It is
        reset at the end of the method (the ``finally`` block below) so a
        finished cycle can't leak its id into a later, unrelated log line.
        """
        _corr_token: object | None = None  # correlation-id reset token (reset at end of run)
        self._audio = audio
        self._duration = duration
        self._recorded_rms = recorded_rms
        self._cycle_id = cycle_id
        self._watchdog = watchdog
        # Write an in-flight sentinel so crash_recovery can detect
        # interrupted dictations on the next startup and emit a
        # dictation_lost event. The sentinel is cleared in the finally
        # block below — only a hard process crash leaves it behind.
        # Atomic write (temp + os.replace) so a crash mid-write cannot
        # leave a half-truncated sentinel that crash_recovery would
        # misparse as a (truncated) cycle id.
        with contextlib.suppress(Exception):
            from voice_typer.server._paths import config_dir as _config_dir
            from voice_typer.server.secure_file_io import _secure_atomic_write

            _sentinel = _config_dir() / ".dictation-in-flight"
            _secure_atomic_write(_sentinel, str(cycle_id), durability=False)
        # publish cycle_id as the correlation id for this thread's
        # logging context.  Capture the token to reset in the finally block.
        from voice_typer.server.log import set_correlation_id

        if cycle_id:
            _corr_token = set_correlation_id(cycle_id)
        # capture the pre-computed audio stats from the
        # recorder so we can pass them to the transcription engine.
        self._audio_stats = getattr(self._app.recorder, "_last_audio_stats", None)
        _t0 = time.perf_counter()

        # Hoist ``text = ""`` outside the try block so the
        # ``except Exception`` block below can reference it (to save
        # the partial transcription to crash recovery). Pre-fix,
        # ``text`` was assigned inside the try (just before the for
        # loop) — if a stage between ``_transcribe`` and
        # ``_store_result`` raised, the partial text was already
        # assigned by the previous iteration but the except block
        # couldn't see it (the local was technically in scope but
        # the intent wasn't explicit, and an exception before the
        # original assignment would have left ``text`` unbound).
        # Hoisting makes the partial-text contract explicit and
        # ensures ``text`` is always defined in the except block.
        text = ""

        try:
            log.info("[TRANSCRIBE] Starting transcription... (cycle=%s)", self._cycle_id)

            # PRE-FLIGHT: resource health check — provides diagnostic
            # context (RAM, disk, GPU) if a heap corruption crash occurs.
            # Throttle to once every 60s. The values change slowly
            # and are only needed for post-crash triage, not per-utterance
            # decisions. Previously ran every utterance (~2-5ms of system/
            # driver calls each).
            self._check_resources_throttled()

            # per-stage timing instrumentation.
            # Stage durations are collected and logged as a single
            # consolidated line at the end to reduce log verbosity.
            # Individual stage lines are available at DEBUG level.
            #
            # stage timing is recorded via the ``_timed_stage``
            # context manager (one entry per stage in ``_timings``) so
            # adding an 11th stage is a one-line ``with`` instead of a
            # 3-line ``_stage_t0`` / ``_<name>_ms =`` pair AND a
            # hand-edited format string in the consolidated log below.
            #
            # the 11 stages themselves live in
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

            stages = getattr(self, "_stages", None) or build_default_stages()
            ctx = PipelineContext(
                cycle_id=self._cycle_id,
                audio=self._audio,
                app=self._app,
                pipeline=self,
            )
            # ``text = ""`` was hoisted to before the try block
            # so the ``except Exception`` block can reference it for
            # the partial-text crash-recovery save. Do NOT re-initialize
            # here — the hoisted assignment is the single source of truth
            # for the partial-text contract.
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
                    # zero and release both audio references
                    # immediately after TranscribeStage returns. No
                    # stage after this one reads ctx.audio or
                    # self._audio (stages 3-11 operate on text only).
                    # The finally-block zero-and-clear below is kept
                    # as defense-in-depth (becomes a no-op here).
                    try:
                        if self._audio is not None and isinstance(self._audio, np.ndarray):
                            self._audio.fill(0)
                    except Exception:
                        log.debug(
                            "[PIPELINE] post-transcribe audio zero failed",
                            exc_info=True,
                        )
                    self._audio = None
                    ctx.audio = None

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
            # ``EmptyCheckStage`` already called
            # ``_handle_empty_transcription()`` (tray state, "no speech"
            # notification, busy-event clear) and raised this sentinel
            # to abort the pipeline cleanly. Fall through to the finally
            # block (sentinel clear, audio zero, watchdog reset,
            # transcription_thread clear, gc.collect, correlation reset)
            # — same as the original ``return`` after
            # ``_handle_empty_transcription``.
            pass
        except _PipelineAbortCancelled:
            #  ``CancellationGuard`` (wrapping
            # ``PasteStage``) already wrote the late transcription to
            # crash-recovery and tore down the bubble, then raised this
            # sentinel to skip the paste. Fall through to the finally
            # block — same as the original ``return`` after the
            # cancelled-cycle branch.
            pass
        except Exception as e:
            log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", self._cycle_id)
            #  surface the failure in the bubble instead
            # of immediately hiding it. The bubble has an `error` mode that
            # renders a red "⚠ Error" pill plus a retry affordance
            # (). Previously the failure path called `set_state("idle")`
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
                    self._hide_or_idle_bubble("bubble error->idle transition")

                self._app._schedule_timer(3.0, _bubble_error_to_idle)
            except Exception:
                log.debug("[PIPELINE] bubble set_state('error') on failure failed", exc_info=True)
            self._app.tray.set_state(AppState.ERROR, "Transcription failed")
            # do NOT leak raw exception text into tray
            # notifications — ctranslate2 / torch errors often contain
            # file paths, CUDA version strings, and internal stack
            # details. Map to a user-friendly message instead.
            self._app.tray.notify(
                APP_NAME,
                _friendly_transcription_error(e),
            )
            self._app._schedule_timer(3.0, lambda: self._app.tray.set_state(AppState.IDLE))
            # Save the partial transcription to crash recovery
            # before discarding it. Pre-fix, a stage between
            # ``_transcribe`` and ``_store_result`` raising would lose
            # the transcription silently — the user saw "Transcription
            # failed" but the partial text was gone (no clipboard copy,
            # no crash-recovery entry). With this save, the user can
            # recover the partial text from the crash-recovery buffer.
            # Best-effort: any exception is suppressed (via
            # ``contextlib.suppress``) so a crash-recovery failure
            # cannot mask the original transcription error. Gated on
            # ``crash_recovery_enabled`` (the user's opt-in) AND
            # ``text`` being non-empty (don't pollute the buffer with
            # empty strings). The ``text`` local is in scope here
            # because it was hoisted to before the ``try`` block above.
            if text and getattr(self._app.config, "crash_recovery_enabled", False):
                with contextlib.suppress(Exception):
                    self._app._crash_recovery.add(text, pasted=False)
                    self._app._crash_recovery.flush(timeout=0.5)

        finally:
            # Each cleanup step below is wrapped in an explicit
            # try/except with log.debug (NOT contextlib.suppress) so a
            # stuck-busy state is diagnosable from the log. The
            # original exception from the try block above is preserved
            # — the finally block must NOT raise (log.debug, not
            # log.error, to avoid log noise on the normal cleanup path).
            # Clear the in-flight sentinel — dictation completed (success,
            # cancel, or handled exception). Only a hard process crash
            # leaves the sentinel behind for crash_recovery to detect.
            try:
                from voice_typer.server._paths import config_dir as _config_dir

                _sentinel = _config_dir() / ".dictation-in-flight"
                if _sentinel.exists():
                    _sentinel.unlink()
            except Exception:
                log.debug(
                    "[PIPELINE] finally cleanup step sentinel_unlink failed",
                    exc_info=True,
                )
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
                log.debug(
                    "[PIPELINE] finally cleanup step audio_zero failed",
                    exc_info=True,
                )
            # RACE-013: reset the persistent watchdog thread (signal
            # that transcription completed normally). Old code used
            # watchdog.cancel() for Timer-based watchdogs; now we
            # signal the Event-based persistent watchdog thread.
            # RACE-016: wrap daemon thread finally block with
            # try/except to prevent exceptions during shutdown.
            try:
                #  Phase 2: fixed typo — was `_recording_controller`
                # (doesn't exist on VoiceTyperApp). The attribute is `recording`
                # (a RecordingController). Previously the watchdog reset never
                # fired from this finally block — see worklog.md bug note.
                recording = getattr(self._app, "recording", None)
                if recording is not None:
                    recording._reset_watchdog()
                    recording._stop_watchdog_thread()
                    # discard this cycle from the cancelled set so
                    # the set doesn't grow unboundedly across cycles. ``discard``
                    # is a no-op if the cycle wasn't cancelled (the normal path).
                    _cancelled_lock = getattr(recording, "_cancelled_cycle_ids_lock", None)
                    _cancelled_set = getattr(recording, "_cancelled_cycle_ids", None)
                    if _cancelled_lock is not None and _cancelled_set is not None:
                        with _cancelled_lock:
                            _cancelled_set.discard(self._cycle_id)
            except Exception:
                log.debug(
                    "[PIPELINE] finally cleanup step watchdog_reset failed",
                    exc_info=True,
                )
            try:
                #  ( family): use
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
                # and is preserved. Mirrors the  path in
                # ``shutdown_controller._do_cleanup`` and the
                #  path in
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
                # () — it sets the cancel event and returns
                # immediately, matching the finally-block's
                # bounded-latency contract.
                session = self._app.recording.pop_streaming_session()
                if session is not None and not self._app.recorder.recording:
                    try:
                        session.cancel()
                    except Exception:
                        log.debug(
                            "[PIPELINE] finally cleanup step streaming_session_cancel failed",
                            exc_info=True,
                        )
            except Exception:
                log.debug("[TRANSCRIBE] finally: session cleanup failed", exc_info=True)
            try:
                self._app._busy_event.set()  # busy = False
            except Exception:
                log.debug(
                    "[PIPELINE] finally cleanup step busy_event_clear failed",
                    exc_info=True,
                )
            #  H-17: clear ``_transcription_thread`` under
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
            # write directly to RecordingController (was a
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
                try:
                    if _recording is not None:
                        _recording._transcription_thread = None
                except Exception:
                    log.debug(
                        "[PIPELINE] finally cleanup step transcription_thread_clear_unsafe failed",
                        exc_info=True,
                    )
            # Downgrade from full gc.collect() to gc.collect(0).
            # Full GC scans the entire Python heap (gen 0+1+2) — with a
            # loaded Whisper model (500MB-3GB of tensors → millions of
            # wrapper objects), a full pass takes 50-500ms, paid on every
            # single transcription cycle. Generation-0 only (~1-5ms)
            # catches the per-cycle allocations (audio buffers, segment
            # lists, IPC dicts) without scanning long-lived model objects.
            try:
                import gc

                gc.collect(0)
            except Exception:
                log.debug(
                    "[PIPELINE] finally cleanup step gc_collect failed",
                    exc_info=True,
                )
            log.debug("[TRANSCRIBE] busy reset to False (cycle=%s)", self._cycle_id)
            # clear the correlation id published at the top of run().
            # This runs in the finally block so it executes on both the
            # success and the handled-exception paths, so a finished
            # transcription cycle can't leak its id into a later, unrelated
            # log line (e.g. the next cycle, or a background prewarm thread
            # sharing this process).
            if _corr_token is not None:
                from voice_typer.server.log import reset_correlation_id

                reset_correlation_id(_corr_token)
