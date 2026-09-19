"""Regression tests for through fixes in the dictation pipeline."""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.dictation_pipeline import DictationPipeline
from voice_typer.server.dictation_pipeline.enhancement_steps import (
    _get_shared_polish_executor,
    _reset_shared_polish_executor,
)
from voice_typer.server.dictation_pipeline.orchestrator import _OrchestratorMixin


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
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

    def __getattr__(self, name: str) -> MagicMock:
        # Auto-mock unknown attributes (like MagicMock) but DO NOT
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
            "_llm_consent_warned",
            "_llm_polish_fail_notified",
            "_last_vocab_sig",
        }:
            raise AttributeError(name)
        mock = MagicMock()
        object.__setattr__(self, name, mock)
        return mock


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app`` via ``__new__``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "jb-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


def _wire_run_finally_block(app: _TestApp) -> None:
    """Configure ``app.recording`` so the finally block short-circuits cleanly."""
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = MagicMock(name="old-thread")
    app.recording.pop_streaming_session = MagicMock(return_value=None)


@pytest.fixture(autouse=True)
def _reset_shared_polish_executor_fixture():
    """test-isolation: reset the shared executor before each test."""
    _reset_shared_polish_executor()
    yield
    _reset_shared_polish_executor()


class TestAudioReleaseAfterTranscribe:
    """: zero and release both audio references after TranscribeStage."""

    def test_audio_released_after_transcribe_stage(self):
        """After TranscribeStage returns, ``self._audio`` and ``ctx.audio``"""
        app = _TestApp()
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = "hello world"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active
        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        from voice_typer.server.dictation_stages import build_default_stages

        original_stages = build_default_stages()
        spy_state: dict = {}

        class _SpyStage:
            name = "spy"
            timed = False

            def run(self, text, ctx):
                spy_state["ctx_audio"] = ctx.audio
                spy_state["pipeline_audio"] = ctx.pipeline._audio
                # Abort the pipeline, we only care about the post-
                from voice_typer.server.dictation_stages import _PipelineAbortEmpty

                raise _PipelineAbortEmpty()

        spy_stages = [_SpyStage() if s.name == "empty_check" else s for s in original_stages]
        pipeline._stages = spy_stages

        # Allocate a real numpy audio buffer so we can verify it's
        audio = np.ones(1024, dtype=np.float32)
        pipeline.run(
            audio=audio,
            duration=1.0,
            recorded_rms=0.5,
            cycle_id="jb66-cycle",
        )

        # The spy stage ran AFTER TranscribeStage. At that point,
        assert spy_state["ctx_audio"] is None, (
            ": ctx.audio must be None after TranscribeStage returns, "
            "stages 3-11 don't need it and holding it pins the audio buffer "
            "through LLM polish / storage / paste. Got: "
            f"{spy_state['ctx_audio']!r}"
        )
        assert spy_state["pipeline_audio"] is None, (
            ": pipeline._audio must be None after TranscribeStage returns "
            "— holding it pins the audio buffer through the rest of the pipeline. "
            f"Got: {spy_state['pipeline_audio']!r}"
        )

    def test_audio_zeroed_in_place_before_release(self):
        """reference is dropped, so forensic recovery from process memory"""
        app = _TestApp()
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active
        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        from voice_typer.server.dictation_stages import build_default_stages

        original_stages = build_default_stages()
        audio_after_transcribe: list = []

        class _CaptureAudioStage:
            name = "capture"
            timed = False

            def run(self, text, ctx):
                audio_after_transcribe.append(ctx.pipeline._audio)
                from voice_typer.server.dictation_stages import _PipelineAbortEmpty

                raise _PipelineAbortEmpty()

        capture_stages = [_CaptureAudioStage() if s.name == "empty_check" else s for s in original_stages]
        pipeline._stages = capture_stages

        audio = np.ones(512, dtype=np.float32)
        pipeline.run(
            audio=audio,
            duration=1.0,
            recorded_rms=0.5,
            cycle_id="jb66-zero",
        )

        # The pipeline's audio reference is None (verified by the spy).
        assert audio_after_transcribe == [None], (
            f": pipeline._audio must be None immediately after TranscribeStage. Got: {audio_after_transcribe}"
        )
        assert not audio.any(), (
            ": the audio buffer must be zeroed in-place (SEC-audit-008) "
            "BEFORE the reference is dropped, so forensic recovery can't "
            f"recover voice data. Got non-zero buffer: max={audio.max()}"
        )

    def test_finally_block_zero_is_noop_on_normal_path(self):
        """
        On the normal path (TranscribeStage succeeded), the finally
        ``self._audio`` is None (defense-in-depth contract).
        """
        app = _TestApp()
        app.recorder._last_audio_stats = None
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.is_loaded = True
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active
        _wire_run_finally_block(app)

        pipeline = _new_pipeline(app)

        # Normal path: audio is provided, transcribed, then released
        pipeline.run(
            audio=np.ones(256, dtype=np.float32),
            duration=1.0,
            recorded_rms=0.5,
            cycle_id="jb66-finally",
        )

        # After run() returns, self._audio must be None (released by
        assert pipeline._audio is None, (
            ": pipeline._audio must be None after run returns on the "
            "normal path (released by after TranscribeStage; finally "
            "block is a no-op). "
            f"Got: {pipeline._audio!r}"
        )


class TestLLMPolishSkipForLongTranscripts:
    """: skip polish for transcripts above ``_LLM_POLISH_WORD_LIMIT``."""

    def _make_app_with_polish(self) -> _TestApp:
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        app.config.llm_api_url = ""
        app.config.llm_model = ""
        app.config.llm_preset = "professional"
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished"
        return app

    def test_short_transcript_calls_polish(self):
        """A short transcript (< 1500 words) must call polish normally."""
        app = self._make_app_with_polish()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False

        result = pipeline._apply_llm_polish("hello world")

        assert result == "polished", f": short transcripts must still be polished. Got: {result!r}"
        app._llm_polisher.polish.assert_called_once_with("hello world")

    def test_long_transcript_skips_polish(self, caplog):
        """A transcript above ``_LLM_POLISH_WORD_LIMIT`` must skip polish"""
        app = self._make_app_with_polish()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False

        # Build a transcript above the 1500-word limit.
        long_text = " ".join(["word"] * (pipeline._LLM_POLISH_WORD_LIMIT + 10))

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._apply_llm_polish(long_text)

        # Original text returned unchanged.
        assert result == long_text, (
            f": long transcripts must return the original text (no polish). Got: {result[:50]!r}..."
        )
        # Polish was NEVER called, no API request, no leaked thread.
        app._llm_polisher.polish.assert_not_called()
        # A log line explains the skip.
        skip_logs = [r for r in caplog.records if "Skipping polish for long transcript" in r.getMessage()]
        assert skip_logs, (
            ": skipping polish for a long transcript must log an INFO "
            "line explaining the skip (so operators can audit why polish "
            "wasn't applied)."
        )

    def test_word_limit_constant_exists_and_is_reasonable(self):
        """``_LLM_POLISH_WORD_LIMIT`` must exist and be in a reasonable"""
        assert hasattr(DictationPipeline, "_LLM_POLISH_WORD_LIMIT"), (
            ": DictationPipeline must define ``_LLM_POLISH_WORD_LIMIT`` as a class attribute."
        )
        limit = DictationPipeline._LLM_POLISH_WORD_LIMIT
        assert 1000 <= limit <= 5000, (
            ": _LLM_POLISH_WORD_LIMIT must be in [1000, 5000], low "
            "enough to skip genuinely long dictations, high enough to not "
            f"skip normal multi-sentence utterances. Got: {limit}"
        )


class TestSharedPolishExecutor:
    """: a single shared ``ThreadPoolExecutor`` is reused across"""

    def test_executor_is_singleton(self):
        """Two calls to ``_get_shared_polish_executor`` must return the"""
        # Reset to ensure a clean state.
        _reset_shared_polish_executor()
        executor1 = _get_shared_polish_executor()
        executor2 = _get_shared_polish_executor()
        assert executor1 is executor2, (
            ": _get_shared_polish_executor must return the SAME executor "
            "instance across calls (module-level singleton). Got two different "
            f"instances: {executor1!r} vs {executor2!r}"
        )

    def test_executor_reused_across_polish_calls(self):
        """Two consecutive ``_call_polish_with_timeout`` calls must"""
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished"

        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False

        # Intercept the executor's submit to count distinct executors.
        _reset_shared_polish_executor()
        seen_executors: list = []
        original_get = _get_shared_polish_executor

        def _tracking_get():
            ex = original_get()
            seen_executors.append(ex)
            return ex

        # Patch the global lookup used inside _call_polish_with_timeout.
        import voice_typer.server.dictation_pipeline.enhancement_steps as es

        original_fn = es._get_shared_polish_executor
        es._get_shared_polish_executor = _tracking_get
        try:
            pipeline._call_polish_with_timeout(app._llm_polisher, "first")
            pipeline._call_polish_with_timeout(app._llm_polisher, "second")
        finally:
            es._get_shared_polish_executor = original_fn

        assert len(seen_executors) == 2, (
            f": two polish calls must each look up the shared executor once. Got {len(seen_executors)} lookups."
        )
        assert seen_executors[0] is seen_executors[1], (
            ": two consecutive polish calls must reuse the SAME executor "
            "(module-level singleton). Got two different instances, the "
            "executor is being allocated per-call instead of shared."
        )

    def test_executor_has_max_workers_one(self):
        """The shared executor must have ``max_workers=1`` so concurrent"""
        _reset_shared_polish_executor()
        executor = _get_shared_polish_executor()
        # ThreadPoolExecutor exposes ``_max_workers`` (CPython
        max_workers = getattr(executor, "_max_workers", None)
        assert max_workers == 1, (
            ": the shared executor must have max_workers=1 so concurrent "
            "polish calls queue (bounding the stalled-thread count to 1 "
            "regardless of cycle frequency). Got: "
            f"{max_workers}"
        )

    def test_no_shutdown_per_cycle(self):
        """``_call_polish_with_timeout`` must NOT call"""
        _reset_shared_polish_executor()
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished"

        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False

        pipeline._call_polish_with_timeout(app._llm_polisher, "hello")

        executor = _get_shared_polish_executor()
        # The executor must NOT have been shut down, it's still
        assert not executor._shutdown, (
            ": the shared executor must NOT be shut down per cycle, "
            "it's shared across cycles and must live for the process lifetime. "
            "Got: executor._shutdown=True after a polish call."
        )


class TestVocabSuggestionDeltaPublish:
    """pending list actually changed. Pre-, every cycle with a non-"""

    def _make_suggestion(self, original: str, corrected: str, confidence: float = 0.9):
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        return CorrectionSuggestion(
            original=original,
            corrected=corrected,
            confidence=confidence,
            context="...",
            timestamp=1.0,
        )

    def _make_automation(self, pending):
        """Build a mock VocabularyAutomation with a fixed pending list."""
        automation = MagicMock()
        # ``analyze_transcription`` returns the suggestions that were
        automation.analyze_transcription.return_value = pending
        automation.get_pending_suggestions.return_value = list(pending)
        automation.auto_apply_high_confidence_suggestions.return_value = 0
        return automation

    def test_first_publish_always_fires(self):
        """The first time a non-empty pending list is seen, the event"""
        app = _TestApp()
        app.config.vocabulary_automation_enabled = True
        automation = self._make_automation([self._make_suggestion("foo", "bar")])
        app._vocabulary_automation = automation

        pipeline = _new_pipeline(app)

        published: list = []
        import voice_typer.server.event_bus as event_bus

        original_publish = event_bus.publish

        def _capture(event):
            if isinstance(event, dict) and event.get("type") == "vocabulary_suggestion":
                published.append(event)
            return original_publish(event)

        event_bus.publish = _capture
        try:
            pipeline._analyze_vocabulary("some text")
        finally:
            event_bus.publish = original_publish

        assert len(published) == 1, (
            ": the first non-empty pending list must always publish "
            f"(no prior signature). Got {len(published)} publishes."
        )

    def test_unchanged_pending_skips_redundant_publish(self):
        """When the pending list is unchanged since the last publish,"""
        app = _TestApp()
        app.config.vocabulary_automation_enabled = True
        suggestions = [self._make_suggestion("foo", "bar")]
        automation = self._make_automation(suggestions)
        app._vocabulary_automation = automation

        pipeline = _new_pipeline(app)

        published: list = []
        import voice_typer.server.event_bus as event_bus

        original_publish = event_bus.publish

        def _capture(event):
            if isinstance(event, dict) and event.get("type") == "vocabulary_suggestion":
                published.append(event)
            return original_publish(event)

        event_bus.publish = _capture
        try:
            # Cycle 1: publishes (first non-empty).
            pipeline._analyze_vocabulary("text one")
            # Cycle 2: same pending list, must NOT re-publish.
            pipeline._analyze_vocabulary("text two")
            # Cycle 3: still same, must NOT re-publish.
            pipeline._analyze_vocabulary("text three")
        finally:
            event_bus.publish = original_publish

        assert len(published) == 1, (
            ": when the pending list is unchanged across cycles, the "
            "vocabulary_suggestion event must NOT be re-published (delta-publish). "
            f"Got {len(published)} publishes across 3 cycles with the same list."
        )

    def test_changed_pending_re_publishes(self):
        """When the pending list changes (count or contents), the event"""
        app = _TestApp()
        app.config.vocabulary_automation_enabled = True
        automation = self._make_automation([self._make_suggestion("foo", "bar")])
        app._vocabulary_automation = automation

        pipeline = _new_pipeline(app)

        published: list = []
        import voice_typer.server.event_bus as event_bus

        original_publish = event_bus.publish

        def _capture(event):
            if isinstance(event, dict) and event.get("type") == "vocabulary_suggestion":
                published.append(event)
            return original_publish(event)

        event_bus.publish = _capture
        try:
            # Cycle 1: 1 suggestion.
            pipeline._analyze_vocabulary("text one")
            # Cycle 2: now 2 suggestions, count changed → re-publish.
            automation.get_pending_suggestions.return_value = [
                self._make_suggestion("foo", "bar"),
                self._make_suggestion("baz", "qux"),
            ]
            pipeline._analyze_vocabulary("text two")
        finally:
            event_bus.publish = original_publish

        assert len(published) == 2, (
            ": when the pending list count changes, the event must be "
            f"re-published. Got {len(published)} publishes across 2 cycles "
            "with different list sizes."
        )

    def test_signature_lives_on_app_not_pipeline(self):
        """The ``_last_vocab_sig`` attribute must live on ``self._app``"""
        app = _TestApp()
        app.config.vocabulary_automation_enabled = True
        automation = self._make_automation([self._make_suggestion("foo", "bar")])
        app._vocabulary_automation = automation

        # Cycle 1: pipeline1 publishes.
        pipeline1 = _new_pipeline(app)
        pipeline1._analyze_vocabulary("text one")
        # The signature must be set on the app.
        assert hasattr(app, "_last_vocab_sig"), (
            ": the signature must be set on ``self._app`` (session-scoped) "
            "so a fresh pipeline per cycle can read the prior signature."
        )

        pipeline2 = _new_pipeline(app)
        published: list = []
        import voice_typer.server.event_bus as event_bus

        original_publish = event_bus.publish

        def _capture(event):
            if isinstance(event, dict) and event.get("type") == "vocabulary_suggestion":
                published.append(event)
            return original_publish(event)

        event_bus.publish = _capture
        try:
            pipeline2._analyze_vocabulary("text two")
        finally:
            event_bus.publish = original_publish

        assert len(published) == 0, (
            ": a fresh pipeline per cycle must read the prior signature "
            "from ``self._app`` and skip the redundant publish. The signature "
            "must NOT live on the pipeline (cycle-scoped), that would reset "
            "every cycle and re-publish every time."
        )


class TestSharedStageList:
    """: the 11-stage template is cached as a class attribute"""

    def test_shared_stages_class_attribute_exists(self):
        """class attribute (initially ``None``, lazy-init on first"""
        assert hasattr(_OrchestratorMixin, "_SHARED_STAGES"), (
            ": _OrchestratorMixin must define ``_SHARED_STAGES`` as a class attribute."
        )

    def test_init_populates_shared_stages(self):
        """The first ``__init__`` call must populate ``_SHARED_STAGES``"""
        # Reset to ensure a clean state (other tests may have populated it).
        original_shared = _OrchestratorMixin._SHARED_STAGES
        try:
            _OrchestratorMixin._SHARED_STAGES = None
            assert _OrchestratorMixin._SHARED_STAGES is None, ": _SHARED_STAGES must be resettable to None for testing."

            app = _TestApp()
            pipeline1 = DictationPipeline(app)
            assert _OrchestratorMixin._SHARED_STAGES is not None, (
                ": the first __init__ call must populate _SHARED_STAGES (lazy-init)."
            )
            assert pipeline1._stages is not _OrchestratorMixin._SHARED_STAGES, (
                ": pipeline._stages must be an instance-owned copy, not a "
                "reference to the shared template (a shared mutable list "
                "lets one pipeline's insert/remove corrupt the others)."
            )
            assert [s.name for s in pipeline1._stages] == [s.name for s in _OrchestratorMixin._SHARED_STAGES], (
                ": the instance copy must match the template content."
            )

            # Second __init__ must reuse the SAME template (no rebuild)
            shared_after_first = _OrchestratorMixin._SHARED_STAGES
            pipeline2 = DictationPipeline(app)
            assert pipeline2._stages is not shared_after_first, (
                ": the second __init__ call must also hand out an instance-owned copy, not the template itself."
            )
            assert pipeline2._stages is not pipeline1._stages, ": two pipelines must never share one mutable list."
            assert _OrchestratorMixin._SHARED_STAGES is shared_after_first, (
                ": _SHARED_STAGES must not be re-allocated on the second __init__ call (the cache must persist)."
            )
        finally:
            _OrchestratorMixin._SHARED_STAGES = original_shared

    def test_shared_stages_have_correct_count(self):
        """The shared stage list must contain exactly 11 stages (the"""
        original_shared = _OrchestratorMixin._SHARED_STAGES
        try:
            _OrchestratorMixin._SHARED_STAGES = None
            app = _TestApp()
            DictationPipeline(app)
            assert len(_OrchestratorMixin._SHARED_STAGES) == 11, (
                ": the shared stage list must contain exactly 11 stages "
                "(the standard dictation pipeline). Got: "
                f"{len(_OrchestratorMixin._SHARED_STAGES)}"
            )
        finally:
            _OrchestratorMixin._SHARED_STAGES = original_shared

    def test_stages_are_stateless(self):
        """Each stage's ``run`` must read from ``ctx``, not ``self`` -"""
        import inspect

        from voice_typer.server import dictation_stages

        stateful_stages: list = []
        for stage_cls in [
            dictation_stages.TranscribeStage,
            dictation_stages.EmptyCheckStage,
            dictation_stages.CleanupStage,
            dictation_stages.VocabularyStage,
            dictation_stages.TemplatesStage,
            dictation_stages.PunctuationStage,
            dictation_stages.LLMPolishStage,
            dictation_stages.AIEnhancementStage,
            dictation_stages.VocabularyAutomationStage,
            dictation_stages.StoreResultStage,
            dictation_stages.PasteStage,
        ]:
            src = inspect.getsource(stage_cls.run)
            # Look for ``self._<attr> =`` (attribute writes), these
            import re

            writes = re.findall(r"self\._\w+\s*=", src)
            if writes:
                stateful_stages.append((stage_cls.__name__, writes))

        assert not stateful_stages, (
            ": stages must be stateless (no ``self._<attr> =`` writes in "
            "``run``) so the shared list is safe to reuse across cycles. "
            f"Found stateful stages: {stateful_stages}"
        )
