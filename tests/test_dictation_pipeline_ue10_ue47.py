"""Tests for the UE-10 / UE-47 / UE-10-F4 / UE-10-F6 fixes in
``dictation_pipeline.py`` and ``dictation_stages.py``.

Covers:

* **UE-10 / UE-9-F2 (High, FT-5 family)** — the pipeline ``run()``
  finally block and ``_transcribe`` used the racy
  ``get_streaming_session()`` + ``set_streaming_session(None)`` pair
  (the exact TOCTOU that ``pop_streaming_session()`` was introduced to
  eliminate). Between the get and the set, a concurrent
  ``_start_streaming_session_if_enabled`` could install a NEW session
  that the subsequent ``set_streaming_session(None)`` would clobber —
  silently killing an active streaming worker thread. After rapid
  stop→start (user double-tap hotkey, or auto-stop Timer immediately
  followed by hotkey), the new recording's streaming session was
  killed silently and streaming transcriptions stopped appearing until
  the next restart. Both call sites now use ``pop_streaming_session()``
  (atomic own-and-clear under a single lock acquisition).

* **UE-10-F6 (Medium)** — ``_transcribe`` made two
  ``active_transcriber()`` calls (one before the transcribe, one after
  to refresh ``device_info``). The second call was both redundant (the
  backend rarely changes mid-cycle) and racy (a concurrent
  ``set_active_backend`` could swap the backend between the two calls,
  so ``device_info`` reported the wrong device for the result just
  produced). The fix captures ``active`` ONCE at the top and reuses
  the local for both the transcribe call and ``device_info``.

* **UE-47 (Medium, observability)** — empty ASR output treated as
  "no speech" masks misconfiguration / unloaded-backend. The fix
  captures ``active.is_loaded`` BEFORE the transcribe call; if the
  engine returns empty AND ``is_loaded`` is False, raises a distinct
  ``BackendNotLoadedError`` (subclass of ``RuntimeError``) so the
  run()'s generic ``except Exception`` block surfaces a friendly
  "model not loaded" message instead of falling through to
  ``_handle_empty_transcription`` (which would show the ambiguous
  "No speech detected" toast).

* **UE-10-F4 (Medium, observability)** —
  ``_handle_empty_transcription`` silently suppressed ALL user
  feedback for short (<15s) near-silent recordings. The fix publishes
  a ``dictation_suppressed`` event with ``{duration, recorded_rms,
  reason: "short_silence"}`` via ``event_bus.publish`` so the renderer
  can show a subtle inline bubble. The suppression threshold is NOT
  lowered (that's a separate UX decision).

See ``review.md`` entries UE-10, UE-47, UE-10-F4, UE-10-F6 for the
full root-cause / user-impact analysis.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.dictation_pipeline import (
    BackendNotLoadedError,
    DictationPipeline,
    _friendly_transcription_error,
)

# ─── Test helpers ───────────────────────────────────────────────────────


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Mirrors the pattern in ``test_dictation_pipeline_review_fixes.py``
    and ``test_dictation_pipeline_h17_and_s3_cr10_fixes.py``: a custom
    class (instead of ``MagicMock``) so the four notify-once flag
    attributes correctly default to ``False`` via
    ``getattr(..., False)`` — MagicMock would auto-create truthy
    children for any attribute access.
    """

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        # bubble_behavior is read in _handle_empty_transcription
        self.config.bubble_behavior = "show_on_record"
        self.config.crash_recovery_enabled = False
        self.config.templates_enabled = True
        self.config.log_transcriptions = False
        self.config.model_size = "tiny.en"
        self.config.device = "cpu"
        self.config.llm_polish = False
        self.config.llm_api_key = ""
        self.config.llm_polish_consent = False
        self.config.llm_api_url = ""
        self.config.llm_model = ""
        self.config.llm_preset = "professional"
        self.history_db = MagicMock()
        self._vocabulary_manager: object = None
        self._template_manager: object = None
        self._llm_polisher: object = None
        self._crash_recovery = MagicMock()
        self._last_transcription: object = None
        self.models = MagicMock()
        self.recording = MagicMock()
        # ``recorder`` is read by the finally block in run() — make
        # it a MagicMock with ``recording = False`` so the session
        # cleanup branch is exercised by default.
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

    # Auto-mock unknown attributes (like MagicMock) but DO NOT
    # auto-create the notify-once flag names — they must default to
    # False via getattr-with-default.
    def __getattr__(self, name: str) -> MagicMock:
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
            "_llm_consent_warned",
            "_llm_polish_fail_notified",
        }:
            raise AttributeError(name)
        mock = MagicMock()
        object.__setattr__(self, name, mock)
        return mock


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``.

    Mirrors how ``RecordingController._stop_impl`` constructs a new
    pipeline per transcription cycle. Uses ``__new__`` to bypass
    ``__init__`` (which expects a real VoiceTyperApp) and manually
    sets the attributes the pipeline methods read.
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
    # ``_check_resources_throttled`` reads these — they're normally
    # set by ``__init__``. Initialize them so ``run()`` doesn't crash
    # on the resource-check fast-path.
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


# ─── UE-10 / UE-9-F2: atomic pop_streaming_session in run() finally ─────


class TestUE10FinallyBlockUsesAtomicPop:
    """UE-10: ``DictationPipeline.run``'s finally block must call
    ``pop_streaming_session()`` (atomic get-and-clear) instead of the
    racy ``get_streaming_session()`` + ``set_streaming_session(None)``
    pair.

    Pre-fix, between the get (lock #1) and the set (lock #2), a
    concurrent ``_start_streaming_session_if_enabled`` could install a
    NEW session that the subsequent ``set_streaming_session(None)``
    would clobber — silently killing an active streaming worker thread
    (FT-5 family race: finish dictation → nothing transcribed on the
    next cycle).
    """

    def test_finally_calls_pop_streaming_session_not_get_plus_set(self):
        """The finally block must call ``pop_streaming_session()`` and
        must NOT call ``get_streaming_session()`` or
        ``set_streaming_session(None)``.
        """
        app = _TestApp()
        # Pre-populate so the pop returns a real session object (so
        # we exercise the cancel path too).
        fake_session = MagicMock()
        app.recording.pop_streaming_session.return_value = fake_session
        # ``recorder.recording`` is False by default in _TestApp, so
        # the cancel branch fires.
        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()
        app.recording._watchdog_lock = threading.Lock()
        app.recording._transcription_thread = MagicMock(name="old-thread")

        # Force the pipeline body to fail early so we land in the
        # finally block quickly.
        app.models.active_transcriber.side_effect = RuntimeError("boom")
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )

        # UE-10: pop_streaming_session must be called from the finally
        # block (atomic own-and-clear).
        assert app.recording.pop_streaming_session.called, (
            "UE-10: run() finally block must call pop_streaming_session() "
            "(atomic) to own-and-clear the streaming session slot."
        )
        # UE-10: get_streaming_session and set_streaming_session must
        # NOT be called — they form the racy get+set pair that the
        # pop replaces.
        assert not app.recording.get_streaming_session.called, (
            "UE-10: run() finally block must NOT call get_streaming_session() "
            "(replaced by atomic pop_streaming_session)."
        )
        assert not app.recording.set_streaming_session.called, (
            "UE-10: run() finally block must NOT call set_streaming_session() "
            "(replaced by atomic pop_streaming_session — never write back to "
            "the slot)."
        )

    def test_finally_cancels_popped_session_when_not_recording(self):
        """When ``pop_streaming_session()`` returns a non-None session
        AND the recorder is NOT recording, the finally block must call
        ``session.cancel()`` so the background streaming worker thread
        exits cleanly instead of leaking.
        """
        app = _TestApp()
        fake_session = MagicMock()
        app.recording.pop_streaming_session.return_value = fake_session
        # recorder.recording is False by default in _TestApp.
        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()
        app.recording._watchdog_lock = threading.Lock()
        app.recording._transcription_thread = MagicMock(name="old-thread")

        app.models.active_transcriber.side_effect = RuntimeError("boom")
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )

        (
            fake_session.cancel.assert_called_once_with(),
            (
                "UE-10: finally block must call session.cancel() on the popped "
                "session when recorder is not recording — so the background "
                "streaming worker thread exits cleanly instead of leaking."
            ),
        )

    def test_finally_does_not_cancel_when_recorder_is_recording(self):
        """When the recorder IS recording (a new dictation has started
        concurrently), the popped session's worker thread will exit on
        its own when it sees no more audio chunks. The finally block
        must NOT call ``session.cancel()`` — the new recording owns
        the streaming flow.
        """
        app = _TestApp()
        fake_session = MagicMock()
        app.recording.pop_streaming_session.return_value = fake_session
        # A new recording is in progress — the popped session belongs
        # to the cycle that just ended, but cancelling it would
        # interfere with the new recording's streaming.
        app.recorder.recording = True
        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()
        app.recording._watchdog_lock = threading.Lock()
        app.recording._transcription_thread = MagicMock(name="old-thread")

        app.models.active_transcriber.side_effect = RuntimeError("boom")
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )

        (
            fake_session.cancel.assert_not_called(),
            (
                "UE-10: finally block must NOT call session.cancel() when "
                "recorder.recording is True — a new dictation is in progress "
                "and the popped session's worker will exit on its own."
            ),
        )

    def test_finally_pop_is_atomic_single_lock_acquisition(self):
        """UE-10 / ARCH-018: ``pop_streaming_session`` must acquire
        the streaming-session lock exactly ONCE (atomic get-and-clear).
        This is a source-text guard against regressing back to the
        get+set pair (which acquires the lock twice and has a TOCTOU
        window between them).
        """
        from voice_typer.server.recording_controller import RecordingController

        src = inspect.getsource(RecordingController.pop_streaming_session)
        # The atomic pop must acquire the lock exactly once — the
        # get+set pair acquires it twice (once for get, once for set).
        assert src.count("with self._streaming_session_lock") == 1, (
            "ARCH-018 / UE-10: pop_streaming_session must acquire the "
            "streaming-session lock exactly once (atomic get-and-clear). "
            "Acquiring it twice would re-introduce the TOCTOU race that "
            "pop_streaming_session was introduced to eliminate."
        )

    def test_finally_does_not_write_back_to_slot(self):
        """UE-10: the finally block must never call
        ``set_streaming_session`` — the popped session is owned by us,
        and writing back to the slot would clobber a NEW session
        installed concurrently by
        ``_start_streaming_session_if_enabled``.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = MagicMock()
        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()
        app.recording._watchdog_lock = threading.Lock()
        app.recording._transcription_thread = MagicMock(name="old-thread")

        app.models.active_transcriber.side_effect = RuntimeError("boom")
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )

        assert not app.recording.set_streaming_session.called, (
            "UE-10: finally block must NEVER call set_streaming_session — "
            "the popped session is owned by us, and writing back to the "
            "slot would clobber a NEW session installed concurrently."
        )


# ─── UE-10 sibling: atomic pop in _transcribe before finalize ───────────


class TestUE10TranscribeUsesAtomicPopBeforeFinalize:
    """UE-10 sibling: ``_transcribe`` must pop the streaming session
    BEFORE calling ``session.finalize()``, not after. Pre-fix, the
    ``set_streaming_session(None)`` ran AFTER finalize — an exception
    in finalize() leaked the stale session reference into the next
    dictation cycle's _transcribe, which would re-call finalize() on
    the already-torn-down session and crash.
    """

    def test_transcribe_pops_session_before_finalize(self):
        """``pop_streaming_session()`` must be called BEFORE
        ``session.finalize()`` — so the slot is cleared even if
        finalize raises.
        """
        app = _TestApp()
        fake_session = MagicMock()
        fake_session.finalize.return_value = "hello"
        app.recording.pop_streaming_session.return_value = fake_session
        active = MagicMock()
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._transcribe()

        # pop_streaming_session must be called (UE-10 sibling).
        app.recording.pop_streaming_session.assert_called_once_with()
        # And the slot is NEVER written back (no set_streaming_session).
        assert not app.recording.set_streaming_session.called, (
            "UE-10 sibling: _transcribe must NEVER call "
            "set_streaming_session — pop_streaming_session owns-and-clears "
            "the slot atomically. Writing back would clobber a NEW session "
            "installed concurrently."
        )

    def test_transcribe_pop_clears_slot_even_if_finalize_raises(self):
        """If ``session.finalize()`` raises, the slot must already be
        clear (the pop ran before finalize). Pre-fix, the
        ``set_streaming_session(None)`` ran AFTER finalize — an
        exception in finalize() leaked the stale session reference
        into the next dictation cycle.
        """
        app = _TestApp()
        fake_session = MagicMock()
        fake_session.finalize.side_effect = RuntimeError("finalize boom")
        app.recording.pop_streaming_session.return_value = fake_session
        active = MagicMock()
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        # The RuntimeError from finalize must propagate.
        with pytest.raises(RuntimeError, match="finalize boom"):
            pipeline._transcribe()

        # The pop must have been called (clearing the slot) BEFORE
        # finalize raised.
        app.recording.pop_streaming_session.assert_called_once_with()
        # And the slot was NEVER written back to None — the pop is
        # the single source of truth for clearing.
        assert not app.recording.set_streaming_session.called, (
            "UE-10 sibling: _transcribe must clear the streaming-session "
            "slot via pop_streaming_session BEFORE finalize. If finalize "
            "raises, the slot must already be clear — never write back."
        )

    def test_transcribe_does_not_call_get_streaming_session(self):
        """UE-10 sibling: ``_transcribe`` must NOT call
        ``get_streaming_session()`` — replaced by atomic
        ``pop_streaming_session()``.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._transcribe()

        assert not app.recording.get_streaming_session.called, (
            "UE-10 sibling: _transcribe must NOT call get_streaming_session() "
            "(replaced by atomic pop_streaming_session)."
        )


# ─── UE-10-F6: capture active_transcriber once, reuse for device_info ───


class TestUE10F6CaptureActiveOnce:
    """UE-10-F6: ``_transcribe`` must capture ``active`` ONCE at the
    top and reuse the local for both the transcribe call and
    ``device_info``. Pre-fix, a second ``active_transcriber()`` call
    after the transcribe was both redundant (the backend rarely
    changes mid-cycle) and racy (a concurrent ``set_active_backend``
    could swap the backend between the two calls, so ``device_info``
    reported the wrong device for the result just produced).
    """

    def test_active_transcriber_called_exactly_once(self):
        """``active_transcriber()`` must be called exactly ONCE —
        pre-fix, two calls were made (one before transcribe, one
        after for device_info).
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock-device"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._transcribe()

        assert app.models.active_transcriber.call_count == 1, (
            "UE-10-F6: _transcribe must call active_transcriber() exactly "
            "ONCE — the pre-fix second call (for device_info) was redundant "
            "and racy vs. a concurrent set_active_backend. Got call_count="
            f"{app.models.active_transcriber.call_count}."
        )

    def test_device_info_uses_captured_active(self):
        """``device_info`` must come from the SAME ``active`` local
        that was used for the transcribe call — not a fresh
        ``active_transcriber()`` call after the transcribe.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "captured-device-info"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._transcribe()

        assert pipeline._device_info == "captured-device-info", (
            "UE-10-F6: _device_info must come from the SAME `active` local "
            "captured before the transcribe call — not a second "
            "active_transcriber() call that races with set_active_backend."
        )

    def test_device_info_falls_back_to_parakeet_when_active_is_none(self):
        """When ``active_transcriber()`` returns None (backend was
        unloaded mid-cycle by a concurrent ``set_active_backend`` /
        ``change_model``), ``device_info`` must fall back to the
        literal ``"Parakeet ASR"`` string — matching the pre-fix
        behavior for the ``active is None`` edge case.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        # active_transcriber returns None — backend was unloaded.
        app.models.active_transcriber.return_value = None

        pipeline = _new_pipeline(app)
        # active is None — the batch path can't call
        # active.transcribe_with_fallback (NoneType has no such method).
        # The pipeline must surface this as an AttributeError that
        # propagates to run()'s except Exception block. We only care
        # that device_info was set to the fallback BEFORE the
        # AttributeError fires... actually no — the AttributeError
        # fires inside the batch path BEFORE device_info is set.
        # So we need to test the fallback via the streaming path:
        # session is non-None, finalize returns text, active is None.
        fake_session = MagicMock()
        fake_session.finalize.return_value = "hello"
        app.recording.pop_streaming_session.return_value = fake_session

        result = pipeline._transcribe()
        assert result == "hello"
        assert pipeline._device_info == "Parakeet ASR", (
            "UE-10-F6: when active is None, _device_info must fall back to the literal 'Parakeet ASR' string."
        )

    def test_device_info_falls_back_when_active_lacks_device_info_attr(self):
        """When ``active`` is non-None but lacks the ``device_info``
        attribute, ``_device_info`` must fall back to ``"Parakeet ASR"``
        — matching the pre-fix behavior.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        # Use a bare object (no device_info attr) wrapped in a
        # MagicMock that explicitly removes the attribute.
        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        del active.device_info
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._transcribe()

        assert pipeline._device_info == "Parakeet ASR", (
            "UE-10-F6: when active lacks device_info attr, _device_info must fall back to 'Parakeet ASR'."
        )


# ─── UE-47: BackendNotLoadedError on empty + unloaded backend ───────────


class TestBackendNotLoadedErrorHierarchy:
    """UE-47: ``BackendNotLoadedError`` must be a subclass of
    ``RuntimeError`` so existing ``except RuntimeError`` /
    ``except Exception`` clauses still catch it (mirrors the
    ``ConsentRequiredError`` pattern in ``asr_errors.py``).
    """

    def test_is_subclass_of_runtime_error(self):
        assert issubclass(BackendNotLoadedError, RuntimeError), (
            "UE-47: BackendNotLoadedError must subclass RuntimeError so the "
            "run() generic `except Exception` block catches it (existing "
            "`except RuntimeError` clauses also still work)."
        )

    def test_is_subclass_of_exception(self):
        assert issubclass(BackendNotLoadedError, Exception)

    def test_engine_name_kwarg_captured(self):
        """The optional ``engine_name`` kwarg captures the backend
        type for telemetry / IPC isinstance narrowing — mirrors the
        pattern used by ``ConsentRequiredError``.
        """
        exc = BackendNotLoadedError("msg", engine_name="ParakeetEngine")
        assert exc.engine_name == "ParakeetEngine"
        assert str(exc) == "msg"

    def test_engine_name_defaults_to_none(self):
        exc = BackendNotLoadedError("msg")
        assert exc.engine_name is None


class TestUE47TranscribeRaisesWhenBackendNotLoaded:
    """UE-47: when the active ASR backend is not loaded AND the engine
    returns empty output, ``_transcribe`` must raise
    ``BackendNotLoadedError`` instead of returning the empty string.
    The raise bypasses ``EmptyCheckStage`` (the exception propagates
    out of ``TranscribeStage``) so the run()'s generic ``except
    Exception`` block surfaces a friendly "model not loaded" message
    instead of falling through to ``_handle_empty_transcription``
    (which would show the ambiguous "No speech detected" toast).
    """

    def test_raises_when_is_loaded_false_and_text_empty(self):
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = False  # UE-47: backend NOT loaded
        active.transcribe_with_fallback.return_value = ""  # empty!
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        with pytest.raises(BackendNotLoadedError) as exc_info:
            pipeline._transcribe()

        # engine_name kwarg captures the backend type.
        assert exc_info.value.engine_name == type(active).__name__, (
            "UE-47: BackendNotLoadedError.engine_name must capture the "
            "backend type (type(active).__name__) for telemetry."
        )

    def test_does_not_raise_when_is_loaded_true_and_text_empty(self):
        """When the backend IS loaded but the engine still returns
        empty (genuine silence or a cloud 200-with-empty-body), the
        empty string must propagate unchanged so ``EmptyCheckStage``
        can run ``_handle_empty_transcription`` (the "No speech
        detected" toast — the correct UX for this case).
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True  # backend IS loaded
        active.transcribe_with_fallback.return_value = ""  # empty!
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        # Must NOT raise — the empty string propagates so
        # EmptyCheckStage can run _handle_empty_transcription.
        result = pipeline._transcribe()
        assert result == "", (
            "UE-47: when backend IS loaded and engine returns empty, the "
            "empty string must propagate so EmptyCheckStage runs the "
            "'No speech detected' path (the correct UX for genuine silence "
            "or cloud 200-with-empty-body)."
        )

    def test_does_not_raise_when_text_nonempty(self):
        """When the engine returns non-empty text, no
        ``BackendNotLoadedError`` fires (regardless of is_loaded).
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = False  # not loaded, but...
        active.transcribe_with_fallback.return_value = "hello"  # ...got text!
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        result = pipeline._transcribe()
        assert result == "hello", (
            "UE-47: when the engine returns non-empty text, no "
            "BackendNotLoadedError fires — the text propagates unchanged "
            "(the user got a result, even if is_loaded was False)."
        )

    def test_is_loaded_captured_before_transcribe_call(self):
        """UE-47: ``is_loaded`` must be captured BEFORE the transcribe
        call (not after) — a concurrent ``set_active_backend`` could
        load the backend between the transcribe and the is_loaded
        read, making the empty result look like "loaded backend
        returned empty" when it was actually "unloaded backend
        returned empty". This is a source-text guard against
        regressing back to the post-transcribe is_loaded read.
        """
        from voice_typer.server import dictation_pipeline

        src = inspect.getsource(dictation_pipeline.DictationPipeline._transcribe)
        # The is_loaded capture (backend_was_loaded) must appear
        # BEFORE the transcribe_with_fallback call in the source.
        is_loaded_idx = src.find("backend_was_loaded = bool")
        transcribe_idx = src.find("active.transcribe_with_fallback")
        assert is_loaded_idx != -1, (
            "UE-47: _transcribe source must capture backend_was_loaded before the transcribe call."
        )
        assert transcribe_idx != -1, "UE-47: _transcribe source must call active.transcribe_with_fallback."
        assert is_loaded_idx < transcribe_idx, (
            "UE-47: backend_was_loaded must be captured BEFORE the "
            "transcribe_with_fallback call — a concurrent set_active_backend "
            "could load the backend between the transcribe and the is_loaded "
            "read, making the empty result look like 'loaded backend returned "
            "empty' when it was actually 'unloaded backend returned empty'."
        )


class TestUE47EmptyWarningIncludesBackendIsLoaded:
    """UE-47: the empty-result warning log must include the
    ``backend_is_loaded`` field so operators can distinguish the three
    failure modes that all collapse to empty output:
    (1) genuine silence, (2) unloaded backend returned "",
    (3) cloud provider returned 200 with empty body.
    """

    def test_warning_includes_backend_is_loaded_field(self, caplog):
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True  # backend IS loaded → no raise
        active.transcribe_with_fallback.return_value = ""  # empty!
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._duration = 5.0
        pipeline._recorded_rms = 0.15
        pipeline._audio_stats = (0.15, 0.5, 25.0)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._transcribe()

        empty_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Empty transcription result" in r.getMessage()
        ]
        assert empty_warnings, "Empty transcription must emit a warning log with diagnostic context"
        msg = empty_warnings[0].getMessage()
        assert "backend_is_loaded=" in msg, (
            "UE-47: empty-result warning must include backend_is_loaded field "
            "so operators can distinguish 'unloaded backend returned empty' "
            "from 'loaded backend returned empty'. Got: " + msg
        )
        assert "backend_is_loaded=True" in msg, (
            "UE-47: backend_is_loaded must be formatted as the captured bool value. Got: " + msg
        )


class TestUE47FriendlyErrorForBackendNotLoaded:
    """UE-47: ``_friendly_transcription_error`` must return a friendly
    "model not loaded" message for ``BackendNotLoadedError`` — distinct
    from the generic "model could not be loaded" message (which is
    about download/load-time failures, not an unloaded backend at
    transcribe time).
    """

    def test_friendly_message_for_backend_not_loaded_error(self):
        exc = BackendNotLoadedError("active ASR backend is not loaded")
        msg = _friendly_transcription_error(exc)
        assert "model was not loaded" in msg.lower(), (
            "UE-47: _friendly_transcription_error must return a friendly "
            "'model not loaded' message for BackendNotLoadedError. Got: " + msg
        )
        # Must NOT be the generic "could not be loaded" message (which
        # is about download/load-time failures — different recovery hint).
        assert "internet connection" not in msg.lower(), (
            "UE-47: BackendNotLoadedError message must NOT be the generic "
            "'check your internet connection' message — the recovery hint "
            "for an unloaded backend at transcribe time is 'wait for the "
            "model to finish loading', not 'check your internet connection'."
        )

    def test_friendly_message_for_generic_runtime_error_unchanged(self):
        """Regression guard: the generic RuntimeError path must still
        return its pre-fix message — the isinstance branch for
        BackendNotLoadedError must NOT short-circuit generic exceptions.
        """
        exc = RuntimeError("some other runtime error")
        msg = _friendly_transcription_error(exc)
        # Must NOT contain the BackendNotLoadedError message.
        assert "model was not loaded" not in msg.lower(), (
            "UE-47 regression: _friendly_transcription_error must NOT "
            "return the BackendNotLoadedError message for a generic "
            "RuntimeError — the isinstance branch must be narrow."
        )


# ─── UE-47: run() generic except catches BackendNotLoadedError ──────────


class TestUE47RunCatchesBackendNotLoadedError:
    """UE-47: when ``_transcribe`` raises ``BackendNotLoadedError``,
    ``run()``'s generic ``except Exception`` block must catch it and
    surface a friendly tray notification (via
    ``_friendly_transcription_error``) instead of crashing the
    transcription thread.
    """

    def test_run_catches_backend_not_loaded_error_and_notifies(self):
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        # ``run()`` overwrites ``pipeline._audio_stats`` with
        # ``app.recorder._last_audio_stats`` — set it to None so the
        # empty-result warning's ``stats_repr`` falls back to
        # ``"<unavailable>"`` instead of trying to format a MagicMock
        # (which raises IndexError when unpacked).
        app.recorder._last_audio_stats = None
        # Configure _transcribe to raise BackendNotLoadedError by
        # making active_transcriber return an unloaded backend that
        # produces empty output.
        active = MagicMock()
        active.is_loaded = False
        active.transcribe_with_fallback.return_value = ""
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()
        app.recording._watchdog_lock = threading.Lock()
        app.recording._transcription_thread = MagicMock(name="old-thread")

        # Run the pipeline — the BackendNotLoadedError from _transcribe
        # must be caught by run()'s generic except Exception block.
        pipeline.run(
            audio=None,
            duration=0.0,
            recorded_rms=0.0,
            cycle_id="test-cycle",
            watchdog=None,
        )

        # The tray must have been notified with the friendly "model
        # not loaded" message.
        notify_calls = app.tray.notify.call_args_list
        assert any("model was not loaded" in str(c.args).lower() for c in notify_calls), (
            "UE-47: when _transcribe raises BackendNotLoadedError, run()'s "
            "generic except Exception block must catch it and surface a "
            "friendly 'model not loaded' tray notification. Got notify "
            f"calls: {[str(c.args) for c in notify_calls]}"
        )
        # Tray must have been set to ERROR state.
        set_state_calls = [c.args for c in app.tray.set_state.call_args_list]
        # AppState.ERROR is the first arg of the failure-path call.
        # We just check that "Transcription failed" appears in the
        # status text of some set_state call.
        assert any("Transcription failed" in str(args) for args in set_state_calls), (
            "UE-47: run()'s except Exception block must set tray to ERROR "
            "state with 'Transcription failed' status. Got set_state "
            f"calls: {[str(args) for args in set_state_calls]}"
        )

    def test_run_does_not_call_handle_empty_transcription_when_backend_unloaded(self):
        """UE-47: when ``_transcribe`` raises
        ``BackendNotLoadedError``, ``_handle_empty_transcription``
        must NOT be called (the exception bypasses EmptyCheckStage
        entirely). The user sees the friendly "model not loaded"
        message, NOT the ambiguous "No speech detected" toast.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        # See note in test_run_catches_backend_not_loaded_error_and_notifies
        # — run() overwrites _audio_stats from app.recorder.
        app.recorder._last_audio_stats = None
        active = MagicMock()
        active.is_loaded = False
        active.transcribe_with_fallback.return_value = ""
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        # Spy on _handle_empty_transcription — if it's called, the
        # BackendNotLoadedError did NOT bypass EmptyCheckStage.
        called_empty_handler = []
        original = pipeline._handle_empty_transcription

        def _spy():
            called_empty_handler.append(True)
            return original()

        pipeline._handle_empty_transcription = _spy

        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()
        app.recording._watchdog_lock = threading.Lock()
        app.recording._transcription_thread = MagicMock(name="old-thread")

        pipeline.run(
            audio=None,
            duration=0.0,
            recorded_rms=0.0,
            cycle_id="test-cycle",
            watchdog=None,
        )

        assert not called_empty_handler, (
            "UE-47: when _transcribe raises BackendNotLoadedError, "
            "_handle_empty_transcription must NOT be called — the "
            "exception bypasses EmptyCheckStage entirely. The user "
            "sees the friendly 'model not loaded' message, NOT the "
            "ambiguous 'No speech detected' toast."
        )


# ─── UE-10-F4: dictation_suppressed event for short silence ─────────────


class TestUE10F4DictationSuppressedEvent:
    """UE-10-F4 (observability): ``_handle_empty_transcription`` must
    publish a ``dictation_suppressed`` event when it suppresses user
    feedback for short (<15s) near-silent recordings. Pre-fix, this
    branch silently swallowed ALL user feedback — the user saw
    nothing and had no way to tell their tap registered.

    The event payload is ``{duration, recorded_rms, reason:
    "short_silence"}`` so the renderer can show a subtle inline
    bubble. The suppression threshold is NOT lowered (separate UX
    decision); we only add an observability/UX channel.
    """

    def test_publishes_dictation_suppressed_for_short_silence(self):
        """Short recording (<15s) AND near-silence (rms<0.005) must
        publish a ``dictation_suppressed`` event with
        ``reason="short_silence"``.
        """
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 2.0  # < 15s grace
        pipeline._recorded_rms = 0.001  # < 0.005 threshold (near silence)

        # Track event_bus.publish calls.
        published_events: list[dict] = []

        def _fake_publish(event, *args, **kwargs):
            published_events.append(event)
            return True

        import voice_typer.server.event_bus as event_bus_module

        original_publish = event_bus_module.publish
        event_bus_module.publish = _fake_publish
        try:
            pipeline._handle_empty_transcription()
        finally:
            event_bus_module.publish = original_publish

        suppressed_events = [e for e in published_events if e.get("type") == "dictation_suppressed"]
        assert suppressed_events, (
            "UE-10-F4: short near-silent recording must publish a "
            "'dictation_suppressed' event so the renderer can show a "
            "subtle inline bubble. Got published events: " + str(published_events)
        )
        event = suppressed_events[0]
        # Payload must include duration, recorded_rms, reason.
        assert event["data"]["reason"] == "short_silence", (
            "UE-10-F4: dictation_suppressed event reason must be 'short_silence' for the short+near-silence branch."
        )
        assert event["data"]["duration"] == 2.0, (
            "UE-10-F4: dictation_suppressed event must include the "
            "recording duration so the renderer can decide whether to "
            "show the bubble."
        )
        assert event["data"]["recorded_rms"] == 0.001, (
            "UE-10-F4: dictation_suppressed event must include the "
            "recorded_rms so the renderer can decide whether to show "
            "the bubble."
        )

    def test_does_not_publish_for_short_recording_with_real_audio(self):
        """Short recording (<15s) BUT real audio (rms>=0.005) must
        NOT publish a ``dictation_suppressed`` event — this is the
        silent-empty-transcription failure mode (engine returned
        empty despite picking up signal), which surfaces a distinct
        tray status ("Transcription returned empty") and a WARNING
        log. Only the short+near-silence branch publishes the event.
        """
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 5.0  # < 15s grace
        pipeline._recorded_rms = 0.15  # >= 0.005 threshold (real audio)

        published_events: list[dict] = []

        def _fake_publish(event, *args, **kwargs):
            published_events.append(event)
            return True

        import voice_typer.server.event_bus as event_bus_module

        original_publish = event_bus_module.publish
        event_bus_module.publish = _fake_publish
        try:
            pipeline._handle_empty_transcription()
        finally:
            event_bus_module.publish = original_publish

        suppressed_events = [e for e in published_events if e.get("type") == "dictation_suppressed"]
        assert not suppressed_events, (
            "UE-10-F4: short recording with REAL audio must NOT publish a "
            "'dictation_suppressed' event — this is the silent-empty-"
            "transcription failure mode (distinct tray status + WARNING "
            "log), not the suppression case."
        )

    def test_does_not_publish_for_long_recording(self):
        """Long recording (>=15s) must NOT publish a
        ``dictation_suppressed`` event — the long-recording branches
        surface a popup notification (microphone check or "no
        transcription produced"), not a subtle suppression bubble.
        """
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 20.0  # >= 15s grace
        pipeline._recorded_rms = 0.001  # near-silence

        published_events: list[dict] = []

        def _fake_publish(event, *args, **kwargs):
            published_events.append(event)
            return True

        import voice_typer.server.event_bus as event_bus_module

        original_publish = event_bus_module.publish
        event_bus_module.publish = _fake_publish
        try:
            pipeline._handle_empty_transcription()
        finally:
            event_bus_module.publish = original_publish

        suppressed_events = [e for e in published_events if e.get("type") == "dictation_suppressed"]
        assert not suppressed_events, (
            "UE-10-F4: long recording must NOT publish a 'dictation_suppressed' "
            "event — the long-recording branches surface a popup notification, "
            "not a subtle suppression bubble."
        )

    def test_publish_does_not_raise_when_event_bus_broken(self):
        """If ``event_bus.publish`` raises (e.g. broken event bus, or
        unregistered event type under ``VOICE_TYPER_DEBUG_EVENTS=1``),
        the suppression path must NOT abort — the tray state set
        earlier is the source of truth; this event is purely
        additive UX feedback.
        """
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 2.0  # short
        pipeline._recorded_rms = 0.001  # near-silence

        import voice_typer.server.event_bus as event_bus_module

        original_publish = event_bus_module.publish

        def _exploding_publish(event, *args, **kwargs):
            raise RuntimeError("event bus is broken")

        event_bus_module.publish = _exploding_publish
        try:
            # Must NOT raise — the suppression path is wrapped in
            # contextlib.suppress(Exception).
            pipeline._handle_empty_transcription()
        finally:
            event_bus_module.publish = original_publish

        # Tray state must still be set to IDLE with "No speech detected"
        # (the source-of-truth UX signal — the event is purely additive).
        statuses = [c.args[1] for c in app.tray.set_state.call_args_list]
        assert "No speech detected" in statuses, (
            "UE-10-F4: even when event_bus.publish raises, the suppression "
            "path must still set the tray state to 'No speech detected' — "
            "the event is purely additive UX feedback."
        )


# ─── UE-10-F9: _check_resources deferred extraction comment ─────────────


class TestUE10F9CheckResourcesDeferredExtractionComment:
    """UE-10-F9 (Low, spaghetti): ``_check_resources`` (185 lines) is
    a self-contained resource probe inlined in the pipeline. The full
    extraction to a ``resource_probe.py`` module is DEFERRED to the
    monolith-split phase — for now, a code comment marks it for
    extraction so the next maintainer doesn't waste time wondering
    why a 185-line system probe is living inside the dictation
    pipeline class.
    """

    def test_check_resources_docstring_mentions_deferred_extraction(self):
        from voice_typer.server import dictation_pipeline

        src = inspect.getsource(dictation_pipeline.DictationPipeline._check_resources)
        # The docstring must mention UE-10-F9 and the deferred
        # extraction to resource_probe.py.
        assert "UE-10-F9" in src, (
            "UE-10-F9: _check_resources docstring must mention the deferred extraction (UE-10-F9 marker)."
        )
        assert "resource_probe" in src, (
            "UE-10-F9: _check_resources docstring must mention the "
            "target module name (resource_probe.py) for the deferred "
            "extraction."
        )
        assert "DEFERRED" in src or "deferred" in src, (
            "UE-10-F9: _check_resources docstring must mark the extraction "
            "as DEFERRED so the next maintainer doesn't waste time wondering "
            "why a 185-line system probe is living inside the dictation "
            "pipeline class."
        )


# ─── UE-47 / dictation_stages: EmptyCheckStage bypass note ──────────────


class TestUE47EmptyCheckStageBypassNote:
    """UE-47: ``EmptyCheckStage`` must document that it only fires
    when the backend WAS loaded — when the backend was NOT loaded,
    ``_transcribe`` raises ``BackendNotLoadedError`` BEFORE returning
    text, so this stage never runs in the unloaded-backend case.
    """

    def test_empty_check_stage_docstring_documents_bypass(self):
        from voice_typer.server import dictation_stages

        src = inspect.getsource(dictation_stages.EmptyCheckStage)
        # The docstring must mention BackendNotLoadedError and the
        # bypass behavior.
        assert "BackendNotLoadedError" in src, (
            "UE-47: EmptyCheckStage docstring must mention "
            "BackendNotLoadedError so maintainers understand why this "
            "stage doesn't fire when the backend is unloaded."
        )
        assert "UE-47" in src, "UE-47: EmptyCheckStage docstring must reference the UE-47 finding for traceability."
