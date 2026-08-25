"""Regression tests for FIX-J (FR-14, FR-15, FR-18) in
``voice_typer/server/dictation_pipeline.py``.

FIX-J addresses three findings from the dictation-pipeline review:

* **FR-14 (High)** — ``_transcribe`` called
  ``active.transcribe_with_fallback(...)`` directly, bypassing
  ``AsrBackendRegistry.transcribe_with_fallback`` (the wrapper at
  ``asr_registry.py:951-997`` that sets/clears the per-backend busy
  flag via ``busy_context``). The UE-48 busy flag was dead code in
  production — ``ModelManager.ensure_active_engine_loaded`` reads
  ``registry.is_busy(name)`` to reject new dictation requests when the
  active backend is stuck in a C-level ctranslate2 call, but the flag
  was never set. When a backend hung, the user's F2 started a new
  dictation on top of the stuck one. The fix wraps the direct call
  with ``registry.busy_context(registry.active_name)`` — the same
  primitive the wrapper uses internally — so the busy flag is
  set/cleared atomically.

* **FR-15 (Medium)** — ``_transcribe`` dereferenced ``active``
  unconditionally on the batch path. When ``active_transcriber()``
  returned ``None`` AND there was no streaming session, the code
  raised ``AttributeError`` (``None.transcribe_with_fallback``)
  instead of a friendly ``BackendNotLoadedError``. The fix adds an
  explicit ``None`` check on the batch path (the streaming path is
  intentionally NOT guarded — ``session.finalize()`` doesn't need
  ``active``).

* **FR-18 (Low)** — ``run()``'s generic ``except Exception`` block did
  NOT save the partial transcription to crash recovery. If a stage
  between ``_transcribe`` and ``_store_result`` raised, the text was
  lost. The fix (a) hoists ``text = ""`` to before the ``try`` block
  so the except block can reference it, and (b) adds a best-effort
  ``crash_recovery.add(text, pasted=False)`` + ``flush(timeout=0.5)``
  in the except block, gated on ``crash_recovery_enabled`` AND
  ``text`` being non-empty.

These tests are deliberately focused on the FIX-J changes — they
don't re-test the existing UE-10 / UE-47 behavior (covered in
``test_dictation_pipeline_backend_fixes.py``).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.dictation_pipeline import (
    BackendNotLoadedError,
    DictationPipeline,
)

# ─── Test helpers (mirror test_dictation_pipeline_backend_fixes.py) ──────────


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Mirrors the pattern in ``test_dictation_pipeline_backend_fixes.py``
    and ``tests/fixtures/dictation_pipeline_helpers.py`` (the canonical
    shared ``_TestApp`` factory): a custom class (instead of
    ``MagicMock``) so the notify-once flag attributes correctly default
    to ``False`` via ``getattr(..., False)`` — MagicMock would
    auto-create truthy children for any attribute access.
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
    pipeline._cycle_id = "fix-j-cycle"
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


def _wire_run_finally_block(app: _TestApp) -> None:
    """Set up the MagicMock attributes that ``run()``'s finally block
    touches (so the cleanup doesn't raise during test teardown).

    Mirrors the setup in
    ``test_dictation_pipeline_backend_fixes.py::test_run_catches_backend_not_loaded_error_and_notifies``.
    """
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = MagicMock(name="old-thread")


# registry busy_context entered during _transcribe ────────────


class TestBusyContextEntered:
    """FR-14: ``_transcribe`` must enter the registry's ``busy_context``
    so the per-backend busy flag is set/cleared atomically (UE-48).

    Pre-fix, the pipeline called ``active.transcribe_with_fallback(...)``
    directly, bypassing ``AsrBackendRegistry.transcribe_with_fallback``
    (the wrapper at ``asr_registry.py:951-997``). The UE-48 busy flag
    was dead code in production — ``ModelManager.ensure_active_engine_loaded``
    reads ``registry.is_busy(name)`` to reject new dictation requests
    when the active backend is stuck, but the flag was never set.
    """

    def test_busy_flag_is_true_mid_call(self):
        """``registry.is_busy(active_name)`` must return ``True`` while
        ``active.transcribe_with_fallback`` is executing. Uses a real
        ``AsrBackendRegistry`` (not a MagicMock) so the busy flag is
        actually set/cleared by ``busy_context``.
        """
        app = _TestApp()
        # Use a real AsrBackendRegistry so the busy flag is real
        # (MagicMock would just return a truthy MagicMock from
        # ``is_busy`` without actually tracking set/clear state).
        config = MagicMock()
        config.asr_backend = "whisper"
        config.disabled_backends = []
        registry = AsrBackendRegistry(config)
        app.models.registry = registry

        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.device_info = "mock"

        # Coordinate: the transcribe thread signals when it's inside
        # active.transcribe_with_fallback, then waits for the main
        # thread to finish its busy-flag check before returning.
        inside_call = threading.Event()
        ok_to_proceed = threading.Event()
        busy_snapshot: list[bool] = []

        def _fake_transcribe(audio, *args, **kwargs):
            inside_call.set()
            # Wait for the main thread to check is_busy, then proceed.
            ok_to_proceed.wait(timeout=2.0)
            return "hello"

        active.transcribe_with_fallback.side_effect = _fake_transcribe
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)

        result_holder: list[str] = []
        exception_holder: list[BaseException] = []

        def _run_transcribe() -> None:
            try:
                result_holder.append(pipeline._transcribe())
            except BaseException as e:  # noqa: BLE001 — capture for assertion
                exception_holder.append(e)

        transcribe_thread = threading.Thread(target=_run_transcribe, name="fix-j-transcribe")
        transcribe_thread.start()
        try:
            # Wait for the transcribe thread to enter active.transcribe_with_fallback.
            assert inside_call.wait(timeout=2.0), (
                "FR-14: transcribe thread did not enter "
                "active.transcribe_with_fallback within 2s — the busy_context "
                "wrapper may not be calling the engine at all."
            )
            # While the engine is executing, the busy flag must be True.
            busy_snapshot.append(registry.is_busy("whisper"))
            # Let the transcribe thread proceed.
            ok_to_proceed.set()
        finally:
            transcribe_thread.join(timeout=2.0)

        assert not exception_holder, f"FR-14: _transcribe raised unexpectedly: {exception_holder}"
        assert busy_snapshot == [True], (
            "FR-14: registry.is_busy('whisper') must return True while "
            "active.transcribe_with_fallback is executing — the busy flag "
            "is the signal ModelManager.ensure_active_engine_loaded reads "
            "to reject new dictation requests when the backend is stuck. "
            f"Got: {busy_snapshot}"
        )
        # And the flag must be cleared after the call returns.
        assert registry.is_busy("whisper") is False, (
            "FR-14: registry.is_busy must return False after the transcribe "
            "call returns — busy_context's finally block must clear the flag "
            "so the next dictation isn't rejected."
        )
        assert result_holder == ["hello"], (
            "FR-14: _transcribe must still return the transcript text — "
            "wrapping with busy_context must not change the return value. "
            f"Got: {result_holder}"
        )

    def test_busy_flag_cleared_on_exception(self):
        """FR-14: the busy flag must be cleared even if
        ``active.transcribe_with_fallback`` raises — ``busy_context``'s
        ``finally`` block ensures the flag never gets stuck set (a
        stuck busy flag would block all future dictation requests).
        """
        app = _TestApp()
        config = MagicMock()
        config.asr_backend = "whisper"
        config.disabled_backends = []
        registry = AsrBackendRegistry(config)
        app.models.registry = registry

        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.device_info = "mock"
        active.transcribe_with_fallback.side_effect = RuntimeError("engine boom")
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        with pytest.raises(RuntimeError, match="engine boom"):
            pipeline._transcribe()

        assert registry.is_busy("whisper") is False, (
            "FR-14: registry.is_busy must return False after the transcribe "
            "call raised — busy_context's finally block must clear the flag "
            "on the exception path too (else a stuck busy flag would block "
            "all future dictation requests)."
        )

    def test_transcribe_still_calls_active_directly(self):
        """FR-14 (compatibility guard): the busy_context wrapper must
        still call ``active.transcribe_with_fallback`` directly (with
        the same kwargs as before) so existing call-site assertions on
        ``active.transcribe_with_fallback.call_args`` continue to hold.

        This guards against a future refactor that swaps the direct
        call for ``registry.transcribe_with_fallback(...)`` (which
        would bypass ``active.transcribe_with_fallback`` entirely and
        break the test coverage in this file).
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._audio_stats = (0.123, 0.456, 25.0)
        result = pipeline._transcribe()

        assert result == "hello"
        # active.transcribe_with_fallback must still be called exactly
        # once (the busy_context wrapper doesn't add a second call).
        assert active.transcribe_with_fallback.call_count == 1, (
            "FR-14: the busy_context wrapper must still call "
            "active.transcribe_with_fallback exactly once — wrapping "
            "with busy_context must NOT replace the direct call (existing "
            "tests assert on active.transcribe_with_fallback.call_args)."
        )
        # And the kwargs must be unchanged.
        _, kwargs = active.transcribe_with_fallback.call_args
        assert kwargs.get("audio_stats") == (0.123, 0.456, 25.0), (
            "FR-14: the busy_context wrapper must forward audio_stats unchanged to active.transcribe_with_fallback."
        )


# BackendNotLoadedError when active is None on batch path ─────


class TestNoneCheckOnBatchPath:
    """FR-15: when ``active_transcriber()`` returns ``None`` AND there
    is no streaming session, the batch path must raise
    ``BackendNotLoadedError`` (not ``AttributeError``) so ``run()``'s
    generic ``except Exception`` block surfaces a friendly "model not
    loaded" message via ``_friendly_transcription_error``.

    Pre-fix, ``active.transcribe_with_fallback`` dereferenced ``None``
    and raised ``AttributeError``, which fell through to the generic
    "Transcription failed (AttributeError)" message — masking the real
    cause (no ASR backend registered).
    """

    def test_raises_backend_not_loaded_when_active_none_and_no_session(self):
        """Batch path with ``active=None`` AND no streaming session must
        raise ``BackendNotLoadedError`` (not ``AttributeError``).
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        app.models.active_transcriber.return_value = None

        pipeline = _new_pipeline(app)
        with pytest.raises(BackendNotLoadedError) as exc_info:
            pipeline._transcribe()

        assert exc_info.value.engine_name == "<none>", (
            "FR-15: BackendNotLoadedError.engine_name must be '<none>' when "
            "active_transcriber() returned None (no backend to name)."
        )
        # The message must mention "wait for the model to finish loading"
        # so _friendly_transcription_error can identify it (the
        # isinstance branch handles this — but the message is the
        # user-facing text and must be actionable).
        assert "wait for the model" in str(exc_info.value).lower(), (
            "FR-15: BackendNotLoadedError message must tell the user to "
            "wait for the model to finish loading. Got: " + str(exc_info.value)
        )

    def test_does_not_raise_attribute_error_when_active_none(self):
        """The pre-fix behavior raised ``AttributeError`` (from
        ``None.transcribe_with_fallback``). Post-fix, the explicit
        None-check raises ``BackendNotLoadedError`` first —
        ``AttributeError`` must NOT propagate.
        """
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None
        app.models.active_transcriber.return_value = None

        pipeline = _new_pipeline(app)
        with pytest.raises(BackendNotLoadedError):
            # Must NOT raise AttributeError.
            pipeline._transcribe()

    def test_streaming_path_still_works_when_active_none(self):
        """FR-15 doesn't break the streaming path: when a streaming
        session exists, ``active`` is not needed (``session.finalize()``
        produces the text). The None-check is on the batch path only
        — the streaming path must continue to work with ``active=None``.

        This mirrors the existing
        ``test_device_info_falls_back_to_parakeet_when_active_is_none``
        in ``test_dictation_pipeline_backend_fixes.py``: the streaming
        worker captured the audio before the backend was unloaded, so
        ``session.finalize()`` can produce the text without ``active``.
        """
        app = _TestApp()
        app.models.active_transcriber.return_value = None
        fake_session = MagicMock()
        fake_session.finalize.return_value = "streaming text"
        app.recording.pop_streaming_session.return_value = fake_session

        pipeline = _new_pipeline(app)
        result = pipeline._transcribe()
        assert result == "streaming text", (
            "FR-15: streaming path must still complete when active is None — "
            "the None-check is on the batch path only (session.finalize() "
            "doesn't need active)."
        )
        # device_info must fall back to the literal "Parakeet ASR" string
        # (mirrors  behavior for the active=None edge case).
        assert pipeline._device_info == "Parakeet ASR", (
            "FR-15: when active is None on the streaming path, _device_info "
            "must fall back to 'Parakeet ASR' (UE-10-F6)."
        )


# partial text saved to crash recovery on exception ──────────


class TestPartialSavedToCrashRecovery:
    """FR-18: when ``run()``'s generic ``except Exception`` block fires,
    the partial transcription (captured before the exception) must be
    saved to crash recovery. Pre-fix, the exception path discarded the
    text — a stage between ``_transcribe`` and ``_store_result``
    raising would lose the transcription silently.
    """

    def test_partial_text_saved_when_post_transcribe_stage_raises(self):
        """When a stage AFTER ``TranscribeStage`` raises, the partial
        transcription must be saved to crash recovery before the
        exception is re-surfaced as a tray notification.
        """
        app = _TestApp()
        app.config.crash_recovery_enabled = True
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = "partial transcription"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        # Inject a stage between TranscribeStage and StoreResultStage
        # that raises, simulating a stage failure after transcription
        # but before the result is stored / pasted.
        from voice_typer.server.dictation_stages import build_default_stages

        class _BoomStage:
            name = "boom"
            timed = True

            def run(self, text, ctx):
                raise RuntimeError("post-transcribe stage boom")

        original_stages = build_default_stages()
        boom_stages = []
        for stage in original_stages:
            boom_stages.append(stage)
            if stage.name == "transcribe":
                boom_stages.append(_BoomStage())
        pipeline._stages = boom_stages

        # Run — the boom stage must trigger the generic except Exception
        # block, which must save "partial transcription" to crash recovery.
        pipeline.run(
            audio=None,
            duration=0.0,
            recorded_rms=0.0,
            cycle_id="fix-j-cycle",
            watchdog=None,
        )

        # The partial text must have been saved to crash recovery.
        add_calls = app._crash_recovery.add.call_args_list
        assert any("partial transcription" in str(c.args) for c in add_calls), (
            "FR-18: when a post-transcribe stage raises, run()'s except "
            "Exception block must save the partial transcription to crash "
            "recovery before discarding it. Got add() calls: " + str([str(c.args) for c in add_calls])
        )
        # And flush must have been called to persist it to disk.
        assert app._crash_recovery.flush.called, (
            "FR-18: crash_recovery.flush must be called after add() so the "
            "partial transcription hits disk before run() returns (the save "
            "thread is async — flush forces a synchronous write)."
        )

    def test_no_save_when_crash_recovery_disabled(self):
        """When ``crash_recovery_enabled`` is ``False``, the except
        block must NOT call ``crash_recovery.add`` — the partial-text
        save is gated on the user's crash-recovery opt-in.
        """
        app = _TestApp()
        app.config.crash_recovery_enabled = False
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = "partial text"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        from voice_typer.server.dictation_stages import build_default_stages

        class _BoomStage:
            name = "boom"
            timed = True

            def run(self, text, ctx):
                raise RuntimeError("post-transcribe stage boom")

        original_stages = build_default_stages()
        boom_stages = []
        for stage in original_stages:
            boom_stages.append(stage)
            if stage.name == "transcribe":
                boom_stages.append(_BoomStage())
        pipeline._stages = boom_stages

        pipeline.run(
            audio=None,
            duration=0.0,
            recorded_rms=0.0,
            cycle_id="fix-j-cycle",
            watchdog=None,
        )

        (
            app._crash_recovery.add.assert_not_called(),
            (
                "FR-18: when crash_recovery_enabled is False, run()'s except "
                "Exception block must NOT call crash_recovery.add — the save "
                "is gated on the user's opt-in."
            ),
        )

    def test_no_save_when_text_empty(self):
        """When the partial text is empty (e.g. transcription returned
        empty AND a later stage raised), the except block must NOT call
        ``crash_recovery.add`` — saving an empty string would pollute
        the recovery buffer with no useful content.
        """
        app = _TestApp()
        app.config.crash_recovery_enabled = True
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        # active.is_loaded = True so BackendNotLoadedError is NOT raised
        # (we want the boom stage to raise, not the empty-result path).
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = ""  # empty!
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        # Insert the boom stage BEFORE EmptyCheckStage so the empty-
        # result path doesn't fire (we want the generic except path
        # with text == "").
        from voice_typer.server.dictation_stages import build_default_stages

        class _BoomStage:
            name = "boom"
            timed = True

            def run(self, text, ctx):
                raise RuntimeError("boom with empty text")

        original_stages = build_default_stages()
        boom_stages = []
        for stage in original_stages:
            boom_stages.append(stage)
            if stage.name == "transcribe":
                boom_stages.append(_BoomStage())
        pipeline._stages = boom_stages

        pipeline.run(
            audio=None,
            duration=0.0,
            recorded_rms=0.0,
            cycle_id="fix-j-cycle",
            watchdog=None,
        )

        # text was "" when the boom fired → no save (gated on `if text`).
        (
            app._crash_recovery.add.assert_not_called(),
            (
                "FR-18: when the partial text is empty, run()'s except Exception "
                "block must NOT call crash_recovery.add — saving an empty string "
                "pollutes the recovery buffer with no useful content."
            ),
        )

    def test_text_hoisted_outside_try_block(self):
        """FR-18 (source-text guard): ``text = ""`` must be initialized
        BEFORE the ``try:`` block in ``run()`` so the ``except
        Exception`` block can reference it. Pre-fix, ``text`` was
        initialized inside the try — an exception before the
        assignment would leave ``text`` unbound in the except block.
        """
        import inspect

        src = inspect.getsource(DictationPipeline.run)
        # The hoisted assignment must appear BEFORE the try: line.
        text_init_idx = src.find('text = ""')
        try_idx = src.find("\n        try:")
        assert text_init_idx != -1, 'FR-18: run() source must initialize `text = ""` somewhere.'
        assert try_idx != -1, "FR-18: run() source must contain a `try:` block."
        assert text_init_idx < try_idx, (
            'FR-18: `text = ""` must be hoisted to BEFORE the `try:` block '
            "in run() so the except Exception block can reference it for the "
            "partial-text crash-recovery save. Pre-fix, text was initialized "
            "inside the try — an exception before the assignment left text "
            "unbound in the except block."
        )
