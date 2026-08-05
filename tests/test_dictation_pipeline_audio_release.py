"""Regression tests for through fixes in the dictation pipeline.

Covers:

* (High)** — ``orchestrator.run`` held the full audio buffer
  through all 11 stages even though only ``TranscribeStage`` (stage 1)
  reads ``ctx.audio`` / ``self._audio``. The fix zeros and releases
  both references immediately after ``TranscribeStage`` returns (the
  finally-block zero-and-clear is kept as defense-in-depth — it
  becomes a no-op on the normal path). For a 30-min @ 16 kHz mono
  float32 recording (~115 MB), this frees the audio ~5-10 s earlier
  (before LLM polish, storage, and paste).

* (High)** — ``_apply_llm_polish`` sent the entire transcript
  to the LLM in one un-chunked call with a fixed 4 s timeout. For
  long transcripts (1000+ words), the round-trip typically exceeds 4
  s, so polish silently degraded to a no-op AND leaked a daemon
  thread. The fix skips polish entirely when the transcript exceeds
  ``_LLM_POLISH_WORD_LIMIT`` (1500 words by default), preserving the
  4 s budget for short utterances where polish is most valuable.

* (Medium)** — ``_call_polish_with_timeout`` allocated a fresh
  ``ThreadPoolExecutor(max_workers=1)`` per cycle and called
  ``executor.shutdown(wait=False)`` on the timeout path. On a stalled
  endpoint, rapid start/stop cycles accumulated up to 10 stalled
  daemon threads + orphaned sockets in 40 s. The fix uses a
  module-level singleton executor (``_get_shared_polish_executor``)
  with ``max_workers=1`` — concurrent polish calls queue, bounding
  the stalled-thread count to 1 regardless of cycle frequency. The
  executor is NEVER shut down per cycle.

* (Medium)** — ``_analyze_vocabulary`` re-serialized and
  re-published ALL pending suggestions (up to MAX_PENDING=200, ~50 KB
  per event) on EVERY cycle where the list was non-empty, even when
  the list was unchanged. The fix tracks a ``(count, sha256)`` signature
  on ``self._app._last_vocab_sig`` (via ``getattr``/``setattr`` so
  ``app.py`` is untouched) and only publishes when the list changed.

* (Low)** — ``DictationPipeline.__init__`` allocated 11 new
  stage objects per cycle (11k allocations for 1000 cycles). The fix
  caches the stage list as a class attribute (``_SHARED_STAGES``),
  lazy-init on first ``__init__``. Stage objects are stateless (each
  ``run`` reads from ``ctx``, not ``self``), so sharing is safe.
"""

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

# ─── Shared test helpers (mirrors test_dictation_pipeline_transcribe_fixes) ──


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Mirrors the pattern in ``test_dictation_pipeline_transcribe_fixes.py``:
    a custom class (instead of ``MagicMock``) so the notify-once flag
    attributes correctly default to ``False`` via ``getattr(..., False)``.
    """

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
        # auto-create the notify-once flag names OR the
        # vocab signature — they must default to None / False via
        # getattr-with-default.
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
    """Build a fresh DictationPipeline tied to ``app`` via ``__new__``.

    Mirrors how ``RecordingController._stop_impl`` constructs a new
    pipeline per transcription cycle. Bypasses ``__init__`` (which
    expects a real VoiceTyperApp) and manually sets the attributes
    ``run()`` reads.
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "jb-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
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


# ─── : audio release after TranscribeStage ───────────────────────────


class TestAudioReleaseAfterTranscribe:
    """: zero and release both audio references after TranscribeStage.

    No stage after TranscribeStage (stages 3-11) reads ``ctx.audio`` or
    ``self._audio`` — they operate on ``text`` only. Holding the audio
    through LLM polish / storage / paste pinned a 30-min @ 16 kHz mono
    float32 buffer (~115 MB) for ~5-10 s longer than necessary.
    """

    def test_audio_released_after_transcribe_stage(self):
        """After TranscribeStage returns, ``self._audio`` and ``ctx.audio``
        must both be ``None`` so subsequent stages don't pin the buffer."""
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

        # Build a custom stage list: transcribe + a spy stage that
        # captures the state of ctx.audio and pipeline._audio when it
        # runs (immediately after TranscribeStage).
        from voice_typer.server.dictation_stages import build_default_stages

        original_stages = build_default_stages()
        spy_state: dict = {}

        class _SpyStage:
            name = "spy"
            timed = False

            def run(self, text, ctx):
                spy_state["ctx_audio"] = ctx.audio
                spy_state["pipeline_audio"] = ctx.pipeline._audio
                # Abort the pipeline — we only care about the post-
                # transcribe state.
                from voice_typer.server.dictation_stages import _PipelineAbortEmpty

                raise _PipelineAbortEmpty()

        spy_stages = [_SpyStage() if s.name == "empty_check" else s for s in original_stages]
        pipeline._stages = spy_stages

        # Allocate a real numpy audio buffer so we can verify it's
        # zeroed + released.
        audio = np.ones(1024, dtype=np.float32)
        pipeline.run(
            audio=audio,
            duration=1.0,
            recorded_rms=0.5,
            cycle_id="jb66-cycle",
            watchdog=None,
        )

        # The spy stage ran AFTER TranscribeStage. At that point,
        # both audio references must already be None.
        assert spy_state["ctx_audio"] is None, (
            ": ctx.audio must be None after TranscribeStage returns — "
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
        """The audio buffer must be zeroed (SEC-audit-008) BEFORE the
        reference is dropped — so forensic recovery from process memory
        can't recover voice data after the release."""
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
                # The audio buffer was zeroed (in-place) and the
                # reference was dropped. We can't observe the buffer
                # contents here (the reference is gone), but we CAN
                # verify the reference is None.
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
            watchdog=None,
        )

        # The pipeline's audio reference is None (verified by the spy).
        assert audio_after_transcribe == [None], (
            f": pipeline._audio must be None immediately after TranscribeStage. Got: {audio_after_transcribe}"
        )
        # The ORIGINAL buffer (still referenced by the test) must have
        # been zeroed in-place — proving SEC-audit-008's zeroing ran.
        assert not audio.any(), (
            ": the audio buffer must be zeroed in-place (SEC-audit-008) "
            "BEFORE the reference is dropped, so forensic recovery can't "
            f"recover voice data. Got non-zero buffer: max={audio.max()}"
        )

    def test_finally_block_zero_is_noop_on_normal_path(self):
        """On the normal path (TranscribeStage succeeded), the finally
        block's audio-zero step is a no-op — ``self._audio`` is already
        None. This test ensures the finally block doesn't raise when
        ``self._audio`` is None (defense-in-depth contract)."""
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
        # by the fix. The finally block's audio-zero step finds
        # ``self._audio is None`` and is a no-op.
        pipeline.run(
            audio=np.ones(256, dtype=np.float32),
            duration=1.0,
            recorded_rms=0.5,
            cycle_id="jb66-finally",
            watchdog=None,
        )

        # After run() returns, self._audio must be None (released by
        # , then the finally block is a no-op).
        assert pipeline._audio is None, (
            ": pipeline._audio must be None after run returns on the "
            "normal path (released by after TranscribeStage; finally "
            "block is a no-op). "
            f"Got: {pipeline._audio!r}"
        )


# ─── : skip LLM polish for long transcripts ──────────────────────────


class TestLLMPolishSkipForLongTranscripts:
    """: skip polish for transcripts above ``_LLM_POLISH_WORD_LIMIT``.

    Long transcripts (1500+ words) typically exceed the 4 s pipeline
    timeout — polish silently degrades to a no-op AND leaks a daemon
    thread. Skipping polish preserves the 4 s budget for short
    utterances where polish is most valuable.
    """

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
        """A transcript above ``_LLM_POLISH_WORD_LIMIT`` must skip polish
        and return the original text — preserving the 4 s budget for
        short utterances and avoiding the leaked-thread overhead."""
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
        # Polish was NEVER called — no API request, no leaked thread.
        app._llm_polisher.polish.assert_not_called()
        # A log line explains the skip.
        skip_logs = [r for r in caplog.records if "Skipping polish for long transcript" in r.getMessage()]
        assert skip_logs, (
            ": skipping polish for a long transcript must log an INFO "
            "line explaining the skip (so operators can audit why polish "
            "wasn't applied)."
        )

    def test_word_limit_constant_exists_and_is_reasonable(self):
        """``_LLM_POLISH_WORD_LIMIT`` must exist and be in a reasonable
        range (1000-5000 words — low enough to actually skip long
        dictations, high enough to not skip normal multi-sentence
        utterances)."""
        assert hasattr(DictationPipeline, "_LLM_POLISH_WORD_LIMIT"), (
            ": DictationPipeline must define ``_LLM_POLISH_WORD_LIMIT`` as a class attribute."
        )
        limit = DictationPipeline._LLM_POLISH_WORD_LIMIT
        assert 1000 <= limit <= 5000, (
            ": _LLM_POLISH_WORD_LIMIT must be in [1000, 5000] — low "
            "enough to skip genuinely long dictations, high enough to not "
            f"skip normal multi-sentence utterances. Got: {limit}"
        )


# ─── : shared ThreadPoolExecutor re-use ──────────────────────────────


class TestSharedPolishExecutor:
    """: a single shared ``ThreadPoolExecutor`` is reused across
    cycles. Pre-, each ``_call_polish_with_timeout`` allocated a
       fresh executor and called ``shutdown(wait=False)`` on timeout —
       leaking up to 10 stalled threads + sockets in 40 s on a stalled
       endpoint.
    """

    def test_executor_is_singleton(self):
        """Two calls to ``_get_shared_polish_executor`` must return the
        SAME executor instance (singleton)."""
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
        """Two consecutive ``_call_polish_with_timeout`` calls must
        reuse the same executor (verified by intercepting submit)."""
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
            "(module-level singleton). Got two different instances — the "
            "executor is being allocated per-call instead of shared."
        )

    def test_executor_has_max_workers_one(self):
        """The shared executor must have ``max_workers=1`` so concurrent
        polish calls queue (bounding the stalled-thread count to 1)."""
        _reset_shared_polish_executor()
        executor = _get_shared_polish_executor()
        # ThreadPoolExecutor exposes ``_max_workers`` (CPython
        # implementation detail). Verify it's 1.
        max_workers = getattr(executor, "_max_workers", None)
        assert max_workers == 1, (
            ": the shared executor must have max_workers=1 so concurrent "
            "polish calls queue (bounding the stalled-thread count to 1 "
            "regardless of cycle frequency). Got: "
            f"{max_workers}"
        )

    def test_no_shutdown_per_cycle(self):
        """``_call_polish_with_timeout`` must NOT call
        ``executor.shutdown()`` — the executor is shared across cycles
        and must live for the process lifetime. Verified by checking
        the executor is still alive after a polish call."""
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
        # The executor must NOT have been shut down — it's still
        # usable for the next cycle.
        assert not executor._shutdown, (
            ": the shared executor must NOT be shut down per cycle — "
            "it's shared across cycles and must live for the process lifetime. "
            "Got: executor._shutdown=True after a polish call."
        )


# ─── : vocab suggestion delta-publish ────────────────────────────────


class TestVocabSuggestionDeltaPublish:
    """: only re-publish the vocabulary_suggestion event when the
    pending list actually changed. Pre-, every cycle with a non-
       empty pending list re-published ALL 200 suggestions (~50 KB per
       event).
    """

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
        # "found" this cycle — the publish path doesn't care about
        # these, only about ``get_pending_suggestions``.
        automation.analyze_transcription.return_value = pending
        automation.get_pending_suggestions.return_value = list(pending)
        automation.auto_apply_high_confidence_suggestions.return_value = 0
        return automation

    def test_first_publish_always_fires(self):
        """The first time a non-empty pending list is seen, the event
        must be published (no prior signature to compare against)."""
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
        """When the pending list is unchanged since the last publish,
        the event must NOT be re-published (delta-publish)."""
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
            # Cycle 2: same pending list — must NOT re-publish.
            pipeline._analyze_vocabulary("text two")
            # Cycle 3: still same — must NOT re-publish.
            pipeline._analyze_vocabulary("text three")
        finally:
            event_bus.publish = original_publish

        assert len(published) == 1, (
            ": when the pending list is unchanged across cycles, the "
            "vocabulary_suggestion event must NOT be re-published (delta-publish). "
            f"Got {len(published)} publishes across 3 cycles with the same list."
        )

    def test_changed_pending_re_publishes(self):
        """When the pending list changes (count or contents), the event
        must be re-published."""
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
            # Cycle 2: now 2 suggestions — count changed → re-publish.
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
        """The ``_last_vocab_sig`` attribute must live on ``self._app``
        (session-scoped), NOT on the pipeline (cycle-scoped). A fresh
        pipeline per cycle must see the prior signature from the app."""
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

        # Cycle 2: a FRESH pipeline (mirrors per-cycle construction in
        # recording_controller) must see the prior signature from the
        # app — and NOT re-publish (delta-publish).
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
            "must NOT live on the pipeline (cycle-scoped) — that would reset "
            "every cycle and re-publish every time."
        )


# ─── : shared stage list cache ───────────────────────────────────────


class TestSharedStageList:
    """: the 11-stage list is cached as a class attribute
       (``_SHARED_STAGES``) and reused across all pipeline instances.
    Pre-, each ``__init__`` allocated 11 new stage objects — 11k
       allocations for 1000 cycles.
    """

    def test_shared_stages_class_attribute_exists(self):
        """``_OrchestratorMixin`` must define ``_SHARED_STAGES`` as a
        class attribute (initially ``None``, lazy-init on first
        ``__init__``)."""
        assert hasattr(_OrchestratorMixin, "_SHARED_STAGES"), (
            ": _OrchestratorMixin must define ``_SHARED_STAGES`` as a class attribute."
        )

    def test_init_populates_shared_stages(self):
        """The first ``__init__`` call must populate ``_SHARED_STAGES``
        (lazy-init). Subsequent ``__init__`` calls must reuse it."""
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
            assert pipeline1._stages is _OrchestratorMixin._SHARED_STAGES, (
                ": pipeline._stages must reference the shared class attribute, not a fresh list."
            )

            # Second __init__ must reuse the SAME list (no new allocation).
            shared_after_first = _OrchestratorMixin._SHARED_STAGES
            pipeline2 = DictationPipeline(app)
            assert pipeline2._stages is shared_after_first, (
                ": the second __init__ call must reuse the shared stage "
                "list — a fresh list would mean the cache is broken."
            )
            assert _OrchestratorMixin._SHARED_STAGES is shared_after_first, (
                ": _SHARED_STAGES must not be re-allocated on the second __init__ call (the cache must persist)."
            )
        finally:
            _OrchestratorMixin._SHARED_STAGES = original_shared

    def test_shared_stages_have_correct_count(self):
        """The shared stage list must contain exactly 11 stages (the
        standard dictation pipeline)."""
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
        """Each stage's ``run`` must read from ``ctx``, not ``self`` —
        this is the invariant that makes the shared-list safe. Verified
        by inspecting the stage classes' ``run`` method source (no
        ``self._`` attribute writes)."""
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
            # Look for ``self._<attr> =`` (attribute writes) — these
            # would mean the stage carries per-cycle state.
            import re

            writes = re.findall(r"self\._\w+\s*=", src)
            if writes:
                stateful_stages.append((stage_cls.__name__, writes))

        assert not stateful_stages, (
            ": stages must be stateless (no ``self._<attr> =`` writes in "
            "``run``) so the shared list is safe to reuse across cycles. "
            f"Found stateful stages: {stateful_stages}"
        )
