"""Transcription step mixin for ``DictationPipeline``.

Originally defined inline as ``DictationPipeline._transcribe`` /
``_handle_empty_transcription`` / ``_hide_or_idle_bubble`` /
``_check_resources_throttled`` / ``_check_resources`` in the
2077-LOC ``dictation_pipeline.py`` monolith. Extracted here as a
mixin so the orchestrator can compose it with the other step mixins
(text / enhancement / storage / paste) into the final
``DictationPipeline`` class.

NO behavior change — the method bodies, signatures, error handling,
and side effects are identical to the pre-split versions. The
``TranscribeStage`` in ``dictation_stages`` calls
``ctx.pipeline._transcribe()``; ``EmptyCheckStage`` calls
``ctx.pipeline._handle_empty_transcription()``. Both reach this
mixin via the composed class's MRO.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any  # noqa: F401  # re-exported for tests (transcribe_step.Any)

from voice_typer.server.branding import APP_NAME
from voice_typer.server.cloud_engines import CloudEngine
from voice_typer.server.dictation_pipeline.helpers import (
    BackendNotLoadedError,
    _lookup_local_whisper,
)
from voice_typer.server.tray_types import AppState

# NOTE: ``_AbortWatcher`` is intentionally NOT imported at module level
# here. Tests monkeypatch ``voice_typer.server.dictation_pipeline._AbortWatcher``
# (the re-exported symbol on the package ``__init__`` namespace); a
# top-level ``from ...helpers import _AbortWatcher`` would bind the
# original class into this module's namespace and bypass the patch.
# ``_transcribe`` resolves ``_AbortWatcher`` lazily through the package
# namespace at call time so the test-time patch takes effect.

log = logging.getLogger(__name__)


class _TranscribeStepMixin:
    """Mixin: transcription + empty-handling + resource-probe step methods.

    Provides the methods consumed by ``TranscribeStage`` and
    ``EmptyCheckStage`` in ``dictation_stages.build_default_stages``:

      * :meth:`_hide_or_idle_bubble` — shared bubble teardown helper
        (also called from the orchestrator's error path and the paste
        step's clipboard-failure path; lives here because it is most
        tightly coupled to the empty-transcription UX path).
      * :meth:`_check_resources_throttled` — throttled wrapper around
        the resource probe; called from the orchestrator's pre-flight.
      * :meth:`_check_resources` — direct (unthrottled) probe; called
        by tests and exposed for parity with the pre-split API.
      * :meth:`_transcribe` — Step 1: get the transcript from the
        active streaming session or ASR backend.
      * :meth:`_handle_empty_transcription` — Step 2: handle the "no
        speech detected" case with the UX-silence-grace logic.
    """

    # ── Pipeline steps ────────────────────────────────────────────

    def _hide_or_idle_bubble(self, log_label: str = "bubble hide/set idle") -> None:
        """Hide the waveform bubble or set it to idle (always_visible mode).

        Centralizes the 4-site pattern of choosing between
        ``set_state("idle")`` (when ``bubble_behavior == "always_visible"``)
        and ``hide()`` (otherwise), wrapped in a best-effort try/except so
        a bubble teardown failure doesn't mask the real transcription
        result. The fallback log message is parameterised so each call
        site can be traced in logs.

        Called from:
        - the error-recovery timer (error → idle transition)
        - the empty-transcription handler
        - the clipboard-failure path (paste failed, text saved to recovery)
        - the success path (paste complete)
        """
        try:
            if self._app.config.bubble_behavior == "always_visible":
                self._app._waveform_bubble.set_state("idle")
            else:
                self._app._waveform_bubble.hide()
        except Exception:
            log.debug("[PIPELINE] %s failed", log_label, exc_info=True)

    def _check_resources_throttled(self) -> None:
        """Throttled wrapper around _check_resources.

        Delegates to ``resource_probe.check_resources_throttled`` (extracted
        to a sibling helper module — the body was self-contained with no
        instance-state dependencies). Preserves the throttle state on
        ``self._last_resources_check_ts`` for backward compat with tests.
        """
        from voice_typer.server.resource_probe import check_resources_throttled

        self._last_resources_check_ts = check_resources_throttled(
            self._last_resources_check_ts,
            self._resources_check_interval,
            logger=log,
        )

    def _check_resources(self) -> None:
        """Pre-flight health check before transcription.

        Delegates to ``resource_probe.check_resources`` (extracted to a
        sibling helper module — the body was a 185-LOC self-contained
        probe with no instance-state dependencies, flagged as a DEFERRED
        refactor by the original docstring).

        Failures in the probe (e.g. ``psutil`` / ``ctypes`` / ``torch``
        not importable, ``shutil.disk_usage`` / ``os.statvfs`` raising)
        are logged at DEBUG level by the delegated ``check_resources``
        and do NOT abort the pipeline — the user may still succeed with
        low resources, and the DEBUG lines aid post-crash triage.
        """
        from voice_typer.server.resource_probe import check_resources

        check_resources(logger=log)

    def _transcribe(self) -> str:
        """Step 1: Get transcription via streaming finalize or direct.

        Returns the transcript from the active streaming session (if one
        is open) or the active ASR backend (Whisper / Parakeet / Qwen /
        Cloud) via ``transcribe_with_fallback``.

        ``active`` is captured ONCE at the top and reused
        for both the transcribe call and the ``device_info`` read
        below. Pre-fix, a second ``active_transcriber()`` call after
        the transcribe was both redundant (the backend rarely changes
        mid-cycle) and racy (a concurrent ``set_active_backend`` could
        swap the backend between the two calls, so ``device_info``
        reported the wrong device for the result just produced).

        ``backend_was_loaded`` is captured BEFORE the transcribe
        call. If the engine returns empty AND ``backend_was_loaded`` is
        False, raise ``BackendNotLoadedError`` — this bypasses
        ``EmptyCheckStage`` (the exception propagates out of
        ``TranscribeStage`` and is caught by ``run()``'s generic
        ``except Exception`` block) so the user sees a friendly
        "model not loaded" message instead of the ambiguous "No speech
        detected" toast that ``_handle_empty_transcription`` would
        produce.
        """
        #  capture the active transcriber ONCE — the
        # previous code made a second ``active_transcriber()`` call
        # after the transcribe to refresh ``device_info`` (redundant +
        # racy vs. a concurrent ``set_active_backend``). Reuse this
        # same local for ``device_info`` below. Also capture
        # ``is_loaded`` BEFORE the transcribe call so the empty-result
        # path can distinguish "engine returned empty" from "engine was
        # never loaded" (a backend that is not loaded can return "" from
        # ``transcribe_with_fallback`` without raising — ).
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
        #
        # Resolve ``_AbortWatcher`` through the package namespace at
        # call time so tests that monkeypatch
        # ``voice_typer.server.dictation_pipeline._AbortWatcher`` (the
        # re-export on the package ``__init__``) take effect — a
        # top-level ``from ...helpers import _AbortWatcher`` would
        # bind the original class into this module's namespace and
        # bypass the patch.
        from voice_typer.server import dictation_pipeline as _dp_pkg

        _abort_watcher_cls = _dp_pkg._AbortWatcher
        abort_watcher: _abort_watcher_cls | None = None  # type: ignore[valid-type]
        if active is not None and hasattr(active, "clear_abort"):
            with contextlib.suppress(Exception):
                active.clear_abort()
            if hasattr(active, "request_abort"):
                abort_watcher = _abort_watcher_cls(self._app, self._cycle_id, active)
                abort_watcher.start()

        try:
            #  sibling: pop_streaming_session() atomically owns the
            # session AND clears the slot under a SINGLE lock acquisition.
            # If finalize() raises below, the slot is already clear — the
            # next dictation cycle starts with a clean slot rather than
            # re-entering the stale session. We never write back to the
            # slot (a concurrent _start_streaming_session_if_enabled could
            # install a NEW session that a set_streaming_session(None) would
            # clobber — see ).
            session = self._app.recording.pop_streaming_session()
            if session is not None:
                log.info("[STREAMING] Finalizing streaming transcript (cycle=%s)", self._cycle_id)
                text = session.finalize(self._audio)
            else:
                # When ``active_transcriber()`` returned None AND
                # there is no streaming session to finalize, the batch
                # path would dereference ``None.transcribe_with_fallback``
                # and raise ``AttributeError`` — masking the real cause
                # (no ASR backend registered, e.g. the model was unloaded
                # mid-cycle by a concurrent ``change_model`` and no
                # streaming session captured the audio). Raise a friendly
                # ``BackendNotLoadedError`` instead so ``run()``'s generic
                # ``except Exception`` block surfaces "model not loaded"
                # via ``_friendly_transcription_error`` (which has an
                # ``isinstance(exc, BackendNotLoadedError)`` branch with a
                # distinct, actionable message (see ). The
                # streaming path above is intentionally NOT guarded:
                # when a streaming session exists, ``session.finalize()``
                # produces the text without needing ``active`` (the
                # streaming worker captured the audio before the backend
                # was unloaded).
                if active is None:
                    raise BackendNotLoadedError(
                        "No ASR backend is registered — wait for the model "
                        "to finish loading, or open Settings to verify a "
                        "backend is available.",
                        engine_name="<none>",
                    )
                # pass the pre-computed audio stats so the
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
                # Route through the registry's busy-flag wrapper so
                # the per-backend busy flag is set/cleared atomically
                # (). Pre-fix, the pipeline called
                # ``active.transcribe_with_fallback(...)`` directly,
                # bypassing ``AsrBackendRegistry.transcribe_with_fallback``
                # (asr_registry.py:951-997) — the  busy flag was dead
                # code in production, so
                # ``ModelManager.ensure_active_engine_loaded`` could not
                # reject new dictation requests when the active backend
                # was stuck in a C-level ctranslate2 call (which can hold
                # GPU + GIL for 5-30 min). When a backend hung, the user's
                # F2 started a new dictation on top of the stuck one.
                #
                # We use ``busy_context`` directly — the same primitive
                # the wrapper uses internally at asr_registry.py:996-997
                # (``with self.busy_context(target): return
                # backend.transcribe_with_fallback(...)``) — because the
                # active backend was already captured above via
                # ``active_transcriber()``; the wrapper's internal lookup
                # would be redundant. ``busy_context`` is the exact
                # primitive that ``ensure_active_engine_loaded`` reads
                # via ``is_busy`` to reject new dictation requests when
                # the active backend is busy.
                registry = self._app.models.registry
                with registry.busy_context(registry.active_name):
                    text = active.transcribe_with_fallback(
                        self._audio,
                        audio_stats=self._audio_stats,
                        local_engine=local_engine,
                    )
        finally:
            if abort_watcher is not None:
                with contextlib.suppress(Exception):
                    abort_watcher.stop()

        # PERF-015: refresh the LRU timestamp for the active backend
        # so it isn't evicted as least-recently-used after a successful
        # transcribe. touch_active_model() is guarded internally and safe to
        # call when no backend is active.
        with contextlib.suppress(Exception):
            self._app.models.touch_active_model()

        # reuse the captured ``active`` local for device_info
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
        # include ``backend_is_loaded`` in the warning so
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
            # if the backend was not loaded when we entered
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
        self._hide_or_idle_bubble("bubble hide/set idle on empty")

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
            #  (observability): publish a ``dictation_suppressed``
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
