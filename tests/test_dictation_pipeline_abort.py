"""DJ-13 (cloud engines block transcription thread) fixes."""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

# Whisper (transcription.py), abort infrastructure ────────────


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
        """When the underlying ctranslate2 Translator exposes"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        inner_translator = MagicMock()
        engine._model = MagicMock()
        engine._model.model = inner_translator
        engine.request_abort()
        inner_translator.interrupt.assert_called_once()

    def test_request_abort_does_not_raise_when_interrupt_missing(self):
        """Older ctranslate2 / mock models may not expose"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        engine._model = MagicMock()
        # Remove the inner ``model`` attribute so ``getattr(...)`` returns None.
        del engine._model.model
        engine.request_abort()  # must not raise
        assert engine._abort_event.is_set()

    def test_transcribe_breaks_segment_loop_on_abort(self):
        """When ``_abort_event`` is set, the segment loop breaks early"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        # Mock the model so transcribe returns a generator of 5 segments.

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
            def gen():
                for i in range(5):
                    yield make_segment(i)

            info = MagicMock()
            info.language = "en"
            info.language_probability = 1.0
            return gen(), info

        engine._model = MagicMock()
        engine._model.transcribe.side_effect = fake_transcribe
        # Pre-set the abort event, the loop's first iteration check
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
        assert "segment 0" in result
        assert "segment 4" not in result, f"abort should have stopped the loop early, but got full result: {result!r}"


# Parakeet (parakeet_engine.py), abort infrastructure ─────────


class TestParakeetAbortStoppingCriteria:
    """``_AbortStoppingCriteria`` is the legacy transformers-compatible"""

    def test_criteria_returns_false_when_event_not_set(self):
        from voice_typer.server.parakeet_engine import _AbortStoppingCriteria

        event = threading.Event()
        criteria = _AbortStoppingCriteria(event)
        # ``input_ids`` / ``scores`` are arbitrary, the criteria only
        assert criteria(input_ids=None, scores=None) is False

    def test_criteria_returns_true_when_event_set(self):
        from voice_typer.server.parakeet_engine import _AbortStoppingCriteria

        event = threading.Event()
        criteria = _AbortStoppingCriteria(event)
        event.set()
        assert criteria(input_ids=None, scores=None) is True

    def test_criteria_does_not_subclass_transformers_stopping_criteria(self):
        """``transformers.StoppingCriteria``) so the module imports cleanly"""
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

    def test_transcribe_segment_calls_onnx_recognize_api(self):
        """``_transcribe_segment`` MUST call the ONNX Runtime API"""
        from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        engine._model = MagicMock()
        engine._model.recognize.return_value = "hello world"

        audio = np.ones(16000, dtype=np.float32) * 0.05
        text = engine._transcribe_segment(audio)

        # The ONNX API was called with the audio array + sample_rate.
        engine._model.recognize.assert_called_once()
        call_args, call_kwargs = engine._model.recognize.call_args
        # First positional arg is the audio array (the production code
        assert call_args[0] is audio or np.array_equal(call_args[0], audio)
        assert call_kwargs.get("sample_rate") == WHISPER_SAMPLE_RATE
        # The ONNX API has no ``stopping_criteria`` parameter, that was
        assert "stopping_criteria" not in call_kwargs
        # Sanity: the mocked recognize() output flowed through.
        assert text == "hello world"

    def test_chunk_loop_breaks_on_abort(self):
        """``_transcribe_chunks`` breaks early, long audio split into 13"""
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
        chunks = [np.ones(16000, dtype=np.float32) for _ in range(5)]
        results = engine._transcribe_chunks(chunks)
        # Should have stopped after chunk 2 (abort fired after chunk 2
        assert call_count["n"] == 2, (
            f"expected chunk loop to break early after 2 chunks, but ran {call_count['n']} chunks"
        )
        assert results == ["chunk-1", "chunk-2"]


# Cloud (cloud_engines.py), +  ───────────────────────────


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
        """If the abort token is set BEFORE ``transcribe`` is called"""
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="k", consent_given=True)
        engine.request_abort()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("voice_typer.server.cloud_engines._opener", MagicMock())
            audio = np.ones(16000, dtype=np.float32) * 0.05
            result = engine.transcribe(audio)
        assert result == ""

    def test_retry_loop_aborts_before_next_attempt(self):
        """next iteration of the retry loop bails out instead of issuing"""
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
            # Set abort after the 1st attempt fails, the 2nd iteration
            if attempt_count["n"] == 1:
                engine._abort_event.set()
            raise URLError("network-down")

        # Patch _opener.open AND time.sleep so the test doesn't wait
        with pytest.MonkeyPatch.context() as mp:
            mock_opener = MagicMock()
            mock_opener.open.side_effect = failing_open
            mp.setattr("voice_typer.server.cloud_engines._opener", mock_opener)
            mp.setattr("time.sleep", lambda *_: None)
            audio = np.ones(16000, dtype=np.float32) * 0.05
            # Should raise (not retry through all 3 attempts).
            with pytest.raises(CloudEngineError):
                engine.transcribe(audio)
        # Only 1 attempt was made, the 2nd iteration saw the abort
        assert attempt_count["n"] == 1, (
            f"expected retry loop to abort after attempt 1, but got {attempt_count['n']} attempts"
        )

    def test_retry_loop_aborts_before_next_attempt_deepgram(self):
        """Same as above but for the Deepgram path (separate retry"""
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
        """The OpenAI-compatible retry loop opens the request with"""
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


# Pipeline (dictation_pipeline.py), wiring ────────────────────


class TestDictationPipelineRequestAbort:
    """``DictationPipeline.request_abort()`` is the public entry point"""

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
        """A test stub backend that doesn't expose ``request_abort``"""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock(spec=[])  # no attributes
        app.models.active_transcriber.return_value = engine
        pipeline = DictationPipeline(app)
        pipeline.request_abort()  # must not raise

    def test_request_abort_swallows_engine_exceptions(self):
        """If the engine's ``request_abort()`` raises, the pipeline"""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock()
        engine.request_abort.side_effect = RuntimeError("boom")
        app.models.active_transcriber.return_value = engine
        pipeline = DictationPipeline(app)
        pipeline.request_abort()  # must not raise


class TestAbortWatcher:
    """``_AbortWatcher`` polls ``recording._cancelled_cycle_ids`` and"""

    def test_watcher_signals_engine_when_cycle_cancelled(self):
        """When the cycle_id is added to ``_cancelled_cycle_ids``,"""
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
        """When the cycle_id is NEVER added to the cancelled set, the"""
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
        """``stop()`` joins the watcher thread with a 1s timeout, it"""
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
        # No simulated slow duration exists (healthy stop returns well
        # under the 1.0s join timeout), so the budget carries slack for
        # machine load on shared runners.
        assert elapsed < 3.0, f"stop() took {elapsed:.2f}s, expected < 3.0s"

    def test_watcher_handles_missing_recording_attrs(self):
        """lock, the watcher must NOT raise, it just keeps polling"""
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
    """``DictationPipeline._transcribe`` installs an ``_AbortWatcher``"""

    def test_transcribe_clears_abort_and_starts_watcher(self):
        """the active engine (so a stale abort from a previous cycle"""
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
        """If ``transcribe_with_fallback`` raises, the watcher MUST"""
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
        """If the active engine doesn't expose ``clear_abort`` /"""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        engine = MagicMock(spec=[])
        # Manually set the attributes the pipeline reads.
        engine.is_loaded = True
        # MagicMock(spec=[]) blocks attribute setting via __setattr__? No —
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
        assert started == []
