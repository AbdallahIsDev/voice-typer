"""Tests for the DJ-12 (uninterruptible transcription inference) and
DJ-13 (cloud engines block transcription thread) fixes.

Coverage:

* **DJ-12** — the abort hotkey (ESC) and the watchdog force-recover
  path used to set ``_cancelled_cycle_ids`` and let the inference run
  to completion — the late result was dropped by the paste guard, but
  the ctranslate2 / transformers / cloud-HTTP call kept the
  transcription thread busy for up to 30s. The fix wires an
  ``_abort_event`` through all three engines (Whisper, Parakeet,
  Cloud) and bridges the cancel set to the engine's ``request_abort()``
  via a polling ``_AbortWatcher`` thread started in
  ``DictationPipeline._transcribe``.

* **DJ-13** — the cloud engines used a 30s per-request timeout and
  retried 3x with no abort check, so a stuck cloud could block the
  transcription thread for up to 35s. The fix reduces the timeout to
  10s and checks the abort token before each retry.

Tests use mocked models (no GPU / no real network) so they run on any
platform in <1s each.
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

# Whisper (transcription.py) —  abort infrastructure ────────────


@pytest.fixture(autouse=True)
def mock_faster_whisper(monkeypatch):
    """Mock faster_whisper so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 0
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


class TestTranscriptionEngineAbort:
    """Whisper engine (``transcription.py``) abort API."""

    def test_engine_has_abort_event_in_init(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        assert hasattr(engine, "_abort_event")
        assert isinstance(engine._abort_event, threading.Event)
        assert not engine._abort_event.is_set()

    def test_request_abort_sets_event(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        assert not engine._abort_event.is_set()
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_clear_abort_clears_event(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        engine.request_abort()
        assert engine._abort_event.is_set()
        engine.clear_abort()
        assert not engine._abort_event.is_set()

    def test_request_abort_calls_ctranslate2_interrupt_when_available(self):
        """When the underlying ctranslate2 Translator exposes
        ``interrupt()`` (ctranslate2 >= 4.x), ``request_abort()`` calls
        it so a mid-segment ``model.transcribe()`` C-level call returns
        promptly instead of running to completion.
        """
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        # Simulate a WhisperModel wrapping a ctranslate2 Translator that
        # exposes interrupt(). The inner ``.model`` attribute is the
        # translator; ``interrupt`` is the ctranslate2 >= 4.x API.
        inner_translator = MagicMock()
        engine._model = MagicMock()
        engine._model.model = inner_translator
        engine.request_abort()
        inner_translator.interrupt.assert_called_once()

    def test_request_abort_does_not_raise_when_interrupt_missing(self):
        """Older ctranslate2 / mock models may not expose
        ``interrupt()``. ``request_abort()`` must NOT raise — only the
        between-segments check fires in that case."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        engine._model = MagicMock()
        # Remove the inner ``model`` attribute so ``getattr(...)`` returns None.
        del engine._model.model
        engine.request_abort()  # must not raise
        assert engine._abort_event.is_set()

    def test_transcribe_breaks_segment_loop_on_abort(self):
        """When ``_abort_event`` is set, the segment loop breaks early
        and returns the partial text collected so far (NOT the full
        audio's transcription). This is the core DJ-12 fix for the
        Whisper path: bounded latency instead of waiting for the full
        inference to complete."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        # Mock the model so transcribe returns a generator of 5 segments.
        # The loop should break BEFORE consuming all 5 once abort fires.

        def make_segment(i):
            seg = MagicMock()
            seg.text = f"segment {i}"
            seg.start = float(i)
            seg.end = float(i + 1)
            seg.avg_logprob = -0.5
            seg.no_speech_prob = 0.1
            return seg

        def fake_transcribe(*args, **kwargs):
            # Generator that yields 5 segments; the abort check between
            # iterations should fire after the 2nd segment.
            def gen():
                for i in range(5):
                    yield make_segment(i)

            info = MagicMock()
            info.language = "en"
            info.language_probability = 1.0
            return gen(), info

        engine._model = MagicMock()
        engine._model.transcribe.side_effect = fake_transcribe
        # Pre-set the abort event — the loop's first iteration check
        # fires before any segment is produced (the check is at the top
        # of the for-loop body, before ``segment_count += 1``).
        # To exercise the "mid-loop break" path we instead set the
        # event after the first segment is yielded. Use a wrapper that
        # sets the event on the second ``next()`` call.
        original_transcribe = engine._model.transcribe.side_effect

        def triggering_transcribe(*args, **kwargs):
            segments, info = original_transcribe(*args, **kwargs)

            def triggering_gen():
                for i, seg in enumerate(segments):
                    if i == 1:
                        engine._abort_event.set()
                    yield seg

            return triggering_gen(), info

        engine._model.transcribe.side_effect = triggering_transcribe

        audio = np.ones(16000, dtype=np.float32) * 0.05  # 1s of audio
        result = engine.transcribe(audio)
        # Partial text: only "segment 0" + "segment 1" (the abort fires
        # after segment 1 is yielded, before segment 2 is consumed).
        # The exact count depends on where the check fires; assert we
        # got FEWER than all 5 segments.
        assert "segment 0" in result
        assert "segment 4" not in result, f"abort should have stopped the loop early, but got full result: {result!r}"


# Parakeet (parakeet_engine.py) —  StoppingCriteria ─────────────


class TestParakeetAbortStoppingCriteria:
    """``_AbortStoppingCriteria`` is the transformers-compatible
    stopping criterion that wires the abort event into ``model.generate()``.
    """

    def test_criteria_returns_false_when_event_not_set(self):
        from voice_typer.server.parakeet_engine import _AbortStoppingCriteria

        event = threading.Event()
        criteria = _AbortStoppingCriteria(event)
        # ``input_ids`` / ``scores`` are arbitrary — the criteria only
        # checks the event.
        assert criteria(input_ids=None, scores=None) is False

    def test_criteria_returns_true_when_event_set(self):
        from voice_typer.server.parakeet_engine import _AbortStoppingCriteria

        event = threading.Event()
        criteria = _AbortStoppingCriteria(event)
        event.set()
        assert criteria(input_ids=None, scores=None) is True

    def test_criteria_does_not_subclass_transformers_stopping_criteria(self):
        """The criteria is duck-typed (NOT a subclass of
        ``transformers.StoppingCriteria``) so the module imports cleanly
        even when ``transformers`` is not installed."""
        from voice_typer.server.parakeet_engine import _AbortStoppingCriteria

        try:
            from transformers import StoppingCriteria
        except ImportError:
            pytest.skip("transformers not installed; isinstance check N/A")
        if not isinstance(StoppingCriteria, type):
            pytest.skip(
                "transformers.StoppingCriteria is not a concrete type in this env (lazy import); isinstance check N/A"
            )
        criteria = _AbortStoppingCriteria(threading.Event())
        # Duck-typed: NOT a subclass instance. (If this assertion ever
        # fails because someone made it a subclass, that's fine — the
        # docstring just says it doesn't NEED to be.)
        assert not isinstance(criteria, StoppingCriteria)


class TestParakeetEngineAbort:
    """Parakeet engine (``parakeet_engine.py``) abort API."""

    def test_engine_has_abort_event_in_init(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        assert hasattr(engine, "_abort_event")
        assert isinstance(engine._abort_event, threading.Event)
        assert not engine._abort_event.is_set()

    def test_request_abort_sets_event(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_clear_abort_clears_event(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        engine.request_abort()
        engine.clear_abort()
        assert not engine._abort_event.is_set()

    def test_transcribe_segment_passes_stopping_criteria(self):
        """``_transcribe_segment`` MUST pass ``stopping_criteria`` to
        ``model.generate()`` so the abort event is checked between
        generated tokens. Verified by inspecting the call kwargs on a
        mocked model."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        # Return a fake output with a sequences attribute.
        fake_output = MagicMock()
        fake_output.sequences = MagicMock()
        engine._model.generate.return_value = fake_output
        engine._model.device = "cpu"
        engine._model.dtype = "float32"
        engine._processor.decode.return_value = "hello"

        audio = np.ones(16000, dtype=np.float32) * 0.05
        engine._transcribe_segment(audio)
        # Verify generate was called with stopping_criteria.
        call_kwargs = engine._model.generate.call_args.kwargs
        assert "stopping_criteria" in call_kwargs
        criteria_list = call_kwargs["stopping_criteria"]
        assert len(criteria_list) == 1
        # The criteria wraps the engine's abort event.
        assert criteria_list[0]._abort_event is engine._abort_event

    def test_transcribe_batch_passes_stopping_criteria(self):
        """Same check for the batched path."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        fake_output = MagicMock()
        fake_output.sequences = MagicMock()
        engine._model.generate.return_value = fake_output
        engine._model.device = "cpu"
        engine._model.dtype = "float32"
        engine._processor.decode.return_value = ["hello"]

        batch = [np.ones(16000, dtype=np.float32) * 0.05]
        engine._transcribe_batch(batch)
        call_kwargs = engine._model.generate.call_args.kwargs
        assert "stopping_criteria" in call_kwargs
        assert len(call_kwargs["stopping_criteria"]) == 1

    def test_chunk_loop_breaks_on_abort(self):
        """When the abort event is set, the chunk-iteration loop in
        ``_transcribe_chunks_batched`` breaks early — long audio split
        into 13 chunks stops after the current chunk rather than
        decoding all remaining ones."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        # Mock _transcribe_segment so we can count calls.
        call_count = {"n": 0}

        def fake_segment(chunk):
            call_count["n"] += 1
            # Set abort after the 2nd chunk completes.
            if call_count["n"] == 2:
                engine._abort_event.set()
            return f"chunk-{call_count['n']}"

        engine._transcribe_segment = fake_segment
        # Force the sequential branch (batch size 1).
        engine._INFERENCE_BATCH_SIZE = 1
        chunks = [np.ones(16000, dtype=np.float32) for _ in range(5)]
        results = engine._transcribe_chunks_batched(chunks)
        # Should have stopped after chunk 2 (abort fired after chunk 2
        # returned, the loop's next-iteration check sees the event and
        # breaks).
        assert call_count["n"] <= 3, f"expected chunk loop to break early, but ran {call_count['n']} chunks"
        assert results == ["chunk-1", "chunk-2"]


# Cloud (cloud_engines.py) —  +  ───────────────────────────


class TestCloudEngineAbort:
    """CloudEngine abort API + retry-loop abort check (DJ-12 + DJ-13a)."""

    def test_engine_has_abort_event_in_init(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="k", consent_given=True)
        assert hasattr(engine, "_abort_event")
        assert isinstance(engine._abort_event, threading.Event)
        assert not engine._abort_event.is_set()

    def test_request_abort_sets_event(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="k", consent_given=True)
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_clear_abort_clears_event(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="k", consent_given=True)
        engine.request_abort()
        engine.clear_abort()
        assert not engine._abort_event.is_set()

    def test_transcribe_skips_network_call_when_abort_already_set(self):
        """If the abort token is set BEFORE ``transcribe`` is called
        (e.g.ESC hit during audio finalization), the network call is
        skipped entirely — return empty so the pipeline's empty-check
        path runs instead of waiting 10s for a request the user
        already cancelled."""
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="k", consent_given=True)
        engine.request_abort()
        with pytest.MonkeyPatch.context() as mp:
            # If the network call IS made, this patch would fire and
            # the test would fail with AttributeError.
            mp.setattr("voice_typer.server.cloud_engines._opener", MagicMock())
            audio = np.ones(16000, dtype=np.float32) * 0.05
            result = engine.transcribe(audio)
        assert result == ""

    def test_retry_loop_aborts_before_next_attempt(self):
        """When the abort token is set during a retry backoff, the
        next iteration of the retry loop bails out instead of issuing
        another 10s HTTP call."""
        from urllib.error import URLError

        from voice_typer.server.asr_errors import CloudEngineError
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="valid-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        attempt_count = {"n": 0}

        def failing_open(*args, **kwargs):
            attempt_count["n"] += 1
            # Set abort after the 1st attempt fails — the 2nd iteration
            # should bail out before opening another connection.
            if attempt_count["n"] == 1:
                engine._abort_event.set()
            raise URLError("network-down")

        # Patch _opener.open AND time.sleep so the test doesn't wait
        # for the exponential backoff.
        with pytest.MonkeyPatch.context() as mp:
            mock_opener = MagicMock()
            mock_opener.open.side_effect = failing_open
            mp.setattr("voice_typer.server.cloud_engines._opener", mock_opener)
            mp.setattr("time.sleep", lambda *_: None)
            audio = np.ones(16000, dtype=np.float32) * 0.05
            # Should raise (not retry through all 3 attempts).
            with pytest.raises(CloudEngineError):
                engine.transcribe(audio)
        # Only 1 attempt was made — the 2nd iteration saw the abort
        # token and bailed out before opening another connection.
        assert attempt_count["n"] == 1, (
            f"expected retry loop to abort after attempt 1, but got {attempt_count['n']} attempts"
        )

    def test_retry_loop_aborts_before_next_attempt_deepgram(self):
        """Same as above but for the Deepgram path (separate retry
        loop in ``_send_deepgram``)."""
        from urllib.error import URLError

        from voice_typer.server.asr_errors import CloudEngineError
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="valid-key",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            consent_given=True,
        )
        attempt_count = {"n": 0}

        def failing_open(*args, **kwargs):
            attempt_count["n"] += 1
            if attempt_count["n"] == 1:
                engine._abort_event.set()
            raise URLError("network-down")

        with pytest.MonkeyPatch.context() as mp:
            mock_opener = MagicMock()
            mock_opener.open.side_effect = failing_open
            mp.setattr("voice_typer.server.cloud_engines._opener", mock_opener)
            mp.setattr("time.sleep", lambda *_: None)
            audio = np.ones(16000, dtype=np.float32) * 0.05
            with pytest.raises(CloudEngineError):
                engine.transcribe(audio)
        assert attempt_count["n"] == 1


class TestCloudEngineTimeout:
    """DJ-13b: per-request timeout reduced from 30s to 10s."""

    def test_timeout_constant_is_10_seconds(self):
        from voice_typer.server.cloud_engines import CloudEngine

        assert CloudEngine._REQUEST_TIMEOUT_SECONDS == 10.0

    def test_openai_path_uses_10s_timeout(self):
        """The OpenAI-compatible retry loop opens the request with
        ``timeout=self._REQUEST_TIMEOUT_SECONDS`` (= 10s), NOT 30s."""
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="valid-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        captured_kwargs: dict = {}

        def fake_open(req, **kwargs):
            captured_kwargs.update(kwargs)
            # Simulate a successful response. ``_read_capped`` calls
            # ``resp.read(64 * 1024)`` in a loop until an empty chunk is
            # returned, so the mock must yield the body ONCE then empty
            # bytes on subsequent reads.
            mock_resp = MagicMock()
            mock_resp.read = MagicMock(side_effect=[b'{"text": "hello"}', b""])
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with pytest.MonkeyPatch.context() as mp:
            mock_opener = MagicMock()
            mock_opener.open.side_effect = fake_open
            mp.setattr("voice_typer.server.cloud_engines._opener", mock_opener)
            audio = np.ones(16000, dtype=np.float32) * 0.05
            engine.transcribe(audio)
        assert captured_kwargs.get("timeout") == 10.0, f"expected timeout=10.0, got {captured_kwargs.get('timeout')!r}"

    def test_deepgram_path_uses_10s_timeout(self):
        """Same check for the Deepgram path."""
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="valid-key",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            consent_given=True,
        )
        captured_kwargs: dict = {}

        def fake_open(req, **kwargs):
            captured_kwargs.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.read = MagicMock(
                side_effect=[
                    b'{"results": {"channels": [{"alternatives": [{"transcript": "hi"}]}]}}',
                    b"",
                ]
            )
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with pytest.MonkeyPatch.context() as mp:
            mock_opener = MagicMock()
            mock_opener.open.side_effect = fake_open
            mp.setattr("voice_typer.server.cloud_engines._opener", mock_opener)
            audio = np.ones(16000, dtype=np.float32) * 0.05
            engine.transcribe(audio)
        assert captured_kwargs.get("timeout") == 10.0


# Pipeline (dictation_pipeline.py) —  wiring ────────────────────


class TestDictationPipelineRequestAbort:
    """``DictationPipeline.request_abort()`` is the public entry point
    that delegates to the active engine's ``request_abort()``."""

    def test_request_abort_calls_engine_request_abort(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock()
        app.models.active_transcriber.return_value = engine
        pipeline = DictationPipeline(app)
        pipeline.request_abort()
        engine.request_abort.assert_called_once()

    def test_request_abort_noop_when_active_is_none(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        app.models.active_transcriber.return_value = None
        pipeline = DictationPipeline(app)
        # Must NOT raise.
        pipeline.request_abort()

    def test_request_abort_noop_when_engine_lacks_abort_api(self):
        """A test stub backend that doesn't expose ``request_abort``
        should not cause the pipeline to raise."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock(spec=[])  # no attributes
        app.models.active_transcriber.return_value = engine
        pipeline = DictationPipeline(app)
        pipeline.request_abort()  # must not raise

    def test_request_abort_swallows_engine_exceptions(self):
        """If the engine's ``request_abort()`` raises, the pipeline
        must NOT propagate the failure — the abort token is
        best-effort."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock()
        engine.request_abort.side_effect = RuntimeError("boom")
        app.models.active_transcriber.return_value = engine
        pipeline = DictationPipeline(app)
        pipeline.request_abort()  # must not raise


class TestAbortWatcher:
    """``_AbortWatcher`` polls ``recording._cancelled_cycle_ids`` and
    signals the engine when the cycle appears in the set."""

    def test_watcher_signals_engine_when_cycle_cancelled(self):
        """When the cycle_id is added to ``_cancelled_cycle_ids``,
        the watcher calls ``engine.request_abort()`` within ~100ms."""
        from voice_typer.server.dictation_pipeline import _AbortWatcher

        app = MagicMock()
        cancelled_set: set[str] = set()
        cancelled_lock = threading.Lock()
        app.recording._cancelled_cycle_ids = cancelled_set
        app.recording._cancelled_cycle_ids_lock = cancelled_lock
        engine = MagicMock()
        watcher = _AbortWatcher(app, "cycle-1", engine)
        watcher.start()
        try:
            # Add the cycle to the cancelled set after a short delay.
            time.sleep(0.05)
            with cancelled_lock:
                cancelled_set.add("cycle-1")
            # Wait up to 1s for the watcher to notice and signal.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if engine.request_abort.called:
                    break
                time.sleep(0.02)
            assert engine.request_abort.called, "watcher did not call engine.request_abort() within 1s of cancel"
            assert watcher._abort_signalled
        finally:
            watcher.stop()

    def test_watcher_does_not_signal_when_cycle_not_cancelled(self):
        """When the cycle_id is NEVER added to the cancelled set, the
        watcher does NOT call ``engine.request_abort()``."""
        from voice_typer.server.dictation_pipeline import _AbortWatcher

        app = MagicMock()
        cancelled_set: set[str] = set()
        cancelled_lock = threading.Lock()
        app.recording._cancelled_cycle_ids = cancelled_set
        app.recording._cancelled_cycle_ids_lock = cancelled_lock
        engine = MagicMock()
        watcher = _AbortWatcher(app, "cycle-1", engine)
        watcher.start()
        try:
            time.sleep(0.3)  # 3 poll cycles with no cancel
            assert not engine.request_abort.called
        finally:
            watcher.stop()

    def test_watcher_stop_is_bounded(self):
        """``stop()`` joins the watcher thread with a 1s timeout — it
        must NOT block indefinitely even if the watcher is mid-poll."""
        from voice_typer.server.dictation_pipeline import _AbortWatcher

        app = MagicMock()
        cancelled_set: set[str] = set()
        cancelled_lock = threading.Lock()
        app.recording._cancelled_cycle_ids = cancelled_set
        app.recording._cancelled_cycle_ids_lock = cancelled_lock
        engine = MagicMock()
        watcher = _AbortWatcher(app, "cycle-1", engine)
        watcher.start()
        t0 = time.monotonic()
        watcher.stop()
        elapsed = time.monotonic() - t0
        assert elapsed < 1.5, f"stop() took {elapsed:.2f}s, expected < 1.5s"

    def test_watcher_handles_missing_recording_attrs(self):
        """If the app's recording lacks ``_cancelled_cycle_ids`` or the
        lock, the watcher must NOT raise — it just keeps polling
        (no-op) until stopped."""
        from voice_typer.server.dictation_pipeline import _AbortWatcher

        app = MagicMock()
        app.recording._cancelled_cycle_ids = None
        app.recording._cancelled_cycle_ids_lock = None
        engine = MagicMock()
        watcher = _AbortWatcher(app, "cycle-1", engine)
        watcher.start()
        try:
            time.sleep(0.2)
            assert not engine.request_abort.called
        finally:
            watcher.stop()


class TestPipelineTranscribeInstallsAbortWatcher:
    """``DictationPipeline._transcribe`` installs an ``_AbortWatcher``
    before calling the engine and stops it in a ``finally`` block."""

    def test_transcribe_clears_abort_and_starts_watcher(self):
        """When ``_transcribe`` runs, it MUST call ``clear_abort()`` on
        the active engine (so a stale abort from a previous cycle
        doesn't suppress the new one) and start a watcher."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock()
        engine.is_loaded = True
        engine.transcribe_with_fallback.return_value = "hello world"
        app.models.active_transcriber.return_value = engine
        app.recording.pop_streaming_session.return_value = None
        pipeline = DictationPipeline(app)
        pipeline._cycle_id = "cycle-1"
        pipeline._audio = np.ones(16000, dtype=np.float32) * 0.05
        pipeline._audio_stats = (0.05, 0.5, 50.0)
        # Patch _AbortWatcher so we can observe start/stop calls without
        # running a real daemon thread.
        started: list[bool] = []
        stopped: list[bool] = []

        class FakeWatcher:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                started.append(True)

            def stop(self):
                stopped.append(True)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("voice_typer.server.dictation_pipeline._AbortWatcher", FakeWatcher)
            text = pipeline._transcribe()
        assert text == "hello world"
        engine.clear_abort.assert_called_once()
        assert started == [True]
        assert stopped == [True]

    def test_transcribe_stops_watcher_even_on_exception(self):
        """If ``transcribe_with_fallback`` raises, the watcher MUST
        still be stopped (the ``finally`` block runs)."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock()
        engine.is_loaded = True
        engine.transcribe_with_fallback.side_effect = RuntimeError("gpu-on-fire")
        app.models.active_transcriber.return_value = engine
        app.recording.pop_streaming_session.return_value = None
        pipeline = DictationPipeline(app)
        pipeline._cycle_id = "cycle-1"
        pipeline._audio = np.ones(16000, dtype=np.float32) * 0.05
        pipeline._audio_stats = (0.05, 0.5, 50.0)
        stopped: list[bool] = []

        class FakeWatcher:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                stopped.append(True)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("voice_typer.server.dictation_pipeline._AbortWatcher", FakeWatcher)
            with pytest.raises(RuntimeError, match="gpu-on-fire"):
                pipeline._transcribe()
        assert stopped == [True], "watcher.stop() must run in finally even on exception"

    def test_transcribe_does_not_install_watcher_for_engine_without_abort_api(self):
        """If the active engine doesn't expose ``clear_abort`` /
        ``request_abort`` (e.g. a legacy backend), the pipeline must
        NOT crash — it just skips the abort watcher."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        # spec=[] means MagicMock with NO attributes — hasattr returns False.
        engine = MagicMock(spec=[])
        # Manually set the attributes the pipeline reads.
        engine.is_loaded = True
        # MagicMock(spec=[]) blocks attribute setting via __setattr__? No —
        # spec only affects __getattr__ for nonexistent attrs. We can
        # still set is_loaded and transcribe_with_fallback directly.
        engine.transcribe_with_fallback = MagicMock(return_value="hello")
        app.models.active_transcriber.return_value = engine
        app.recording.pop_streaming_session.return_value = None
        pipeline = DictationPipeline(app)
        pipeline._cycle_id = "cycle-1"
        pipeline._audio = np.ones(16000, dtype=np.float32) * 0.05
        pipeline._audio_stats = (0.05, 0.5, 50.0)
        started: list[bool] = []

        class FakeWatcher:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                started.append(True)

            def stop(self):
                pass

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("voice_typer.server.dictation_pipeline._AbortWatcher", FakeWatcher)
            text = pipeline._transcribe()
        assert text == "hello"
        # Watcher should NOT have been started because the engine lacks
        # the abort API (hasattr returns False on a spec=[] mock).
        assert started == []
