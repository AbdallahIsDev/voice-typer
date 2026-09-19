"""``voice_typer/server/dictation_pipeline.py``."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.dictation_pipeline import (
    BackendNotLoadedError,
    DictationPipeline,
)


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
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
        # ``recorder`` is read by the finally block in run(), make
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

    # Auto-mock unknown attributes (like MagicMock) but DO NOT
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
    """Build a fresh DictationPipeline tied to ``app``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "fix-j-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    # ``_check_resources_throttled`` reads these, they're normally
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


def _wire_run_finally_block(app: _TestApp) -> None:
    """Set up the MagicMock attributes that ``run()``'s finally block"""
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = MagicMock(name="old-thread")


class TestBusyContextEntered:
    """FR-14: ``_transcribe`` must enter the registry's ``busy_context``"""

    def test_busy_flag_is_true_mid_call(self):
        """``registry.is_busy(active_name)`` must return ``True`` while"""
        app = _TestApp()
        # Use a real AsrBackendRegistry so the busy flag is real
        config = MagicMock()
        config.asr_backend = "whisper"
        config.disabled_backends = []
        registry = AsrBackendRegistry(config)
        app.models.registry = registry

        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.device_info = "mock"

        # Coordinate: the transcribe thread signals when it's inside
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
            except BaseException as e:  # noqa: BLE001, capture for assertion
                exception_holder.append(e)

        transcribe_thread = threading.Thread(target=_run_transcribe, name="fix-j-transcribe")
        transcribe_thread.start()
        try:
            # Wait for the transcribe thread to enter active.transcribe_with_fallback.
            assert inside_call.wait(timeout=2.0), (
                "FR-14: transcribe thread did not enter "
                "active.transcribe_with_fallback within 2s, the busy_context "
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
            "active.transcribe_with_fallback is executing, the busy flag "
            "is the signal ModelManager.ensure_active_engine_loaded reads "
            "to reject new dictation requests when the backend is stuck. "
            f"Got: {busy_snapshot}"
        )
        # And the flag must be cleared after the call returns.
        assert registry.is_busy("whisper") is False, (
            "FR-14: registry.is_busy must return False after the transcribe "
            "call returns, busy_context's finally block must clear the flag "
            "so the next dictation isn't rejected."
        )
        assert result_holder == ["hello"], (
            "FR-14: _transcribe must still return the transcript text, "
            "wrapping with busy_context must not change the return value. "
            f"Got: {result_holder}"
        )

    def test_busy_flag_cleared_on_exception(self):
        """FR-14: the busy flag must be cleared even if"""
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
            "call raised, busy_context's finally block must clear the flag "
            "on the exception path too (else a stuck busy flag would block "
            "all future dictation requests)."
        )

    def test_transcribe_still_calls_active_directly(self):
        """FR-14 (compatibility guard): the busy_context wrapper must"""
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
        assert active.transcribe_with_fallback.call_count == 1, (
            "FR-14: the busy_context wrapper must still call "
            "active.transcribe_with_fallback exactly once, wrapping "
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
    """FR-15: when ``active_transcriber()`` returns ``None`` AND there"""

    def test_raises_backend_not_loaded_when_active_none_and_no_session(self):
        """Batch path with ``active=None`` AND no streaming session must"""
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
        assert "wait for the model" in str(exc_info.value).lower(), (
            "FR-15: BackendNotLoadedError message must tell the user to "
            "wait for the model to finish loading. Got: " + str(exc_info.value)
        )

    def test_does_not_raise_attribute_error_when_active_none(self):
        """
        ``None.transcribe_with_fallback``). Post-fix, the explicit
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
        """FR-15 doesn't break the streaming path: when a streaming"""
        app = _TestApp()
        app.models.active_transcriber.return_value = None
        fake_session = MagicMock()
        fake_session.finalize.return_value = "streaming text"
        app.recording.pop_streaming_session.return_value = fake_session

        pipeline = _new_pipeline(app)
        result = pipeline._transcribe()
        assert result == "streaming text", (
            "FR-15: streaming path must still complete when active is None, "
            "the None-check is on the batch path only (session.finalize() "
            "doesn't need active)."
        )
        assert pipeline._device_info == "Parakeet ASR", (
            "FR-15: when active is None on the streaming path, _device_info "
            "must fall back to 'Parakeet ASR' (UE-10-F6)."
        )


class TestPartialSavedToCrashRecovery:
    """FR-18: when ``run()``'s generic ``except Exception`` block fires,"""

    def test_partial_text_saved_when_post_transcribe_stage_raises(self):
        """When a stage AFTER ``TranscribeStage`` raises, the partial"""
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

        # Run, the boom stage must trigger the generic except Exception
        pipeline.run(
            audio=None,
            duration=0.0,
            recorded_rms=0.0,
            cycle_id="fix-j-cycle",
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
            "thread is async, flush forces a synchronous write)."
        )

    def test_no_save_when_crash_recovery_disabled(self):
        """When ``crash_recovery_enabled`` is ``False``, the except"""
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
        )

        (
            app._crash_recovery.add.assert_not_called(),
            (
                "FR-18: when crash_recovery_enabled is False, run()'s except "
                "Exception block must NOT call crash_recovery.add, the save "
                "is gated on the user's opt-in."
            ),
        )

    def test_no_save_when_text_empty(self):
        """When the partial text is empty (e.g. transcription returned"""
        app = _TestApp()
        app.config.crash_recovery_enabled = True
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = ""  # empty!
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        # Insert the boom stage BEFORE EmptyCheckStage so the empty-
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
        )

        (
            app._crash_recovery.add.assert_not_called(),
            (
                "FR-18: when the partial text is empty, run()'s except Exception "
                "block must NOT call crash_recovery.add, saving an empty string "
                "pollutes the recovery buffer with no useful content."
            ),
        )

    def test_text_hoisted_outside_try_block(self):
        """FR-18 (source-text guard): ``text = \"\"`` must be initialized"""
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
            "inside the try, an exception before the assignment left text "
            "unbound in the except block."
        )
