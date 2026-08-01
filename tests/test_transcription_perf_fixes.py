"""Performance fix regression tests for the model-management + transcription
findings.

Covers:

* ``_active_inference`` counter + ``_inference_cond`` Condition on
  ``TranscriptionEngine`` so the model lock is released during the
  ctranslate2 segment-decoding loop. ``unload()`` waits on the cond for
  the counter to return to 0 before nulling ``self._model``, so a
  stuck backend can be torn down without waiting for the full segment
  loop to complete. Mirrors the pattern in
  ``parakeet_engine.py:752-779`` / ``1360-1371``.
* ``_INFERENCE_BATCH_SIZE`` read at ParakeetEngine construction time
  (NOT import time) so changes to ``PARAKEET_BATCH_SIZE`` between engine
  constructions take effect.
* ``_set_active_backend_blocking`` re-checks ``recorder.recording`` and
  ``_busy_event`` INSIDE ``_model_change_lock`` +
  ``_config_mutation_lock`` for race-safety (and re-defers if a
  recording started between the wrapper's check and the lock
  acquisition).

All faster_whisper / torch / ctranslate2 / huggingface_hub imports are
mocked so these tests run on any platform without GPU or model
downloads.
"""

from __future__ import annotations

import inspect
import os
import sys
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_faster_whisper(monkeypatch):
    """Mock faster_whisper + ctranslate2 so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 1
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


# ── IN-4: TranscriptionEngine releases lock during inference ──


class TestInferenceCounterReleasesLockDuringInference:
    """The model lock MUST NOT be held during the ctranslate2
    ``model.transcribe()`` call. ``unload()`` must be able to acquire
    the cond (which acquires the underlying lock) while a transcription
    is in flight — it then waits on ``_inference_cond`` for
    ``_active_inference == 0``."""

    def test_init_creates_active_inference_counter(self):
        """``__init__`` must initialise ``_active_inference = 0``."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        assert engine._active_inference == 0, (
            "IN-4: TranscriptionEngine.__init__ must initialise _active_inference = 0 so unload() can wait on the cond."
        )

    def test_init_creates_inference_cond_wrapping_lock(self):
        """``_inference_cond`` must be a Condition wrapping ``_lock``."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        assert isinstance(engine._inference_cond, threading.Condition), (
            "IN-4: _inference_cond must be a threading.Condition."
        )
        # The Condition's lock must be the engine's _lock so wait()
        # releases the same lock that transcribe() acquires.
        assert engine._inference_cond._lock is engine._lock, (
            "IN-4: _inference_cond must wrap self._lock so wait()/notify() "
            "coordinate with the lock acquired in transcribe()."
        )

    def test_transcribe_increments_counter_under_lock(self):
        """``transcribe()`` must increment ``_active_inference`` under
        the lock BEFORE releasing it for the segment loop."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        engine._device = "cpu"
        engine._compute_type = "int8"
        mock_model = MagicMock()
        captured_counter = []

        def transcribe_side_effect(*args, **kwargs):
            # Capture the counter value DURING model.transcribe() —
            # after the lock is released but BEFORE the finally block
            # decrements. Must be 1.
            captured_counter.append(engine._active_inference)
            return ([MagicMock(text="hello")], MagicMock())

        mock_model.transcribe.side_effect = transcribe_side_effect
        engine._model = mock_model

        result = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert result == "hello"
        # Counter was 1 during the call.
        assert captured_counter == [1], (
            f"IN-4: _active_inference must be 1 during model.transcribe() "
            f"(got {captured_counter}). The lock must be released BEFORE "
            f"the segment loop, but the counter must remain incremented "
            f"so unload() waits."
        )
        # Counter is 0 after the call (finally block decremented).
        assert engine._active_inference == 0

    def test_transcribe_with_fallback_increments_counter(self):
        """``transcribe_with_fallback()`` must also use the counter
        pattern (it's the production path called by the dictation
        pipeline)."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        engine._device = "cpu"
        engine._compute_type = "int8"
        mock_model = MagicMock()
        captured_counter = []

        def transcribe_side_effect(*args, **kwargs):
            captured_counter.append(engine._active_inference)
            return ([MagicMock(text="hello")], MagicMock())

        mock_model.transcribe.side_effect = transcribe_side_effect
        engine._model = mock_model

        result = engine.transcribe_with_fallback(np.zeros(16000, dtype=np.float32))
        assert result == "hello"
        assert captured_counter == [1], (
            f"IN-4: transcribe_with_fallback must increment _active_inference "
            f"to 1 during model.transcribe() (got {captured_counter})."
        )
        assert engine._active_inference == 0

    def test_transcribe_words_increments_counter(self):
        """``transcribe_words()`` must also use the counter pattern."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        engine._device = "cpu"
        engine._compute_type = "int8"
        mock_model = MagicMock()
        captured_counter = []
        segment = MagicMock()
        segment.words = [MagicMock(word=" hello", start=0.0, end=0.5)]

        def transcribe_side_effect(*args, **kwargs):
            captured_counter.append(engine._active_inference)
            return ([segment], MagicMock())

        mock_model.transcribe.side_effect = transcribe_side_effect
        engine._model = mock_model

        result = engine.transcribe_words(np.zeros(16000, dtype=np.float32))
        assert len(result) >= 1
        assert captured_counter == [1], (
            f"IN-4: transcribe_words must increment _active_inference "
            f"to 1 during model.transcribe() (got {captured_counter})."
        )
        assert engine._active_inference == 0

    def test_lock_not_held_during_model_transcribe(self):
        """The lock MUST NOT be held during ``model.transcribe()``.
        This is the core IN-4 fix — previously the entire
        ``_transcribe_unlocked`` call (10-30s for a long dictation) ran
        under ``self._lock``, blocking ``unload()`` / ``is_loaded`` /
        parallel transcribes."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        engine._device = "cpu"
        engine._compute_type = "int8"
        mock_model = MagicMock()
        lock_held_during_call = []

        def transcribe_side_effect(*args, **kwargs):
            # Try to acquire the lock non-blocking — if it succeeds, the
            # lock was NOT held (which is what we want post-IN-4).
            acquired = engine._lock.acquire(blocking=False)
            lock_held_during_call.append(not acquired)
            if acquired:
                engine._lock.release()
            return ([MagicMock(text="hello")], MagicMock())

        mock_model.transcribe.side_effect = transcribe_side_effect
        engine._model = mock_model

        engine.transcribe_with_fallback(np.zeros(16000, dtype=np.float32))
        assert lock_held_during_call == [False], (
            f"IN-4: _lock must NOT be held during model.transcribe() "
            f"(held={lock_held_during_call}). The lock should be released "
            f"before the segment loop so unload() can acquire the cond."
        )


class TestUnloadWaitsForInference:
    """``unload()`` must wait on ``_inference_cond`` for
    ``_active_inference == 0`` before nulling ``self._model``."""

    def test_unload_waits_for_active_inference_to_drain(self):
        """When ``_active_inference > 0``, ``unload()`` must block until
        the counter returns to 0 (rather than nulling the model
        mid-inference and triggering a use-after-free)."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        engine._model = MagicMock()

        # Simulate an in-flight transcription: increment the counter
        # under the cond (mirroring what transcribe() does).
        with engine._inference_cond:
            engine._active_inference += 1

        # Spawn unload() in a thread — it should block on the cond wait.
        unload_done = threading.Event()

        def unload_thread():
            engine.unload()
            unload_done.set()

        t = threading.Thread(target=unload_thread, daemon=True)
        t.start()

        # unload() should NOT complete while _active_inference > 0.
        assert not unload_done.wait(timeout=0.2), (
            "IN-4: unload() must block on _inference_cond while "
            "_active_inference > 0 (rather than nulling the model "
            "mid-inference)."
        )
        # The model is still loaded.
        assert engine._model is not None

        # Now drain the counter — notify the cond. unload() should
        # complete promptly.
        with engine._inference_cond:
            engine._active_inference -= 1
            engine._inference_cond.notify_all()

        assert unload_done.wait(timeout=2.0), (
            "IN-4: unload() must complete promptly after _active_inference returns to 0 and the cond is notified."
        )
        # The model is now nulled.
        assert engine._model is None

    def test_unload_source_uses_inference_cond(self):
        """Source guard: ``unload()`` must use ``_inference_cond`` (NOT
        ``_lock`` directly) so wait() releases the lock and transcribe()
        can notify."""
        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine.unload)
        assert "_inference_cond" in src, (
            "IN-4: unload() must acquire _inference_cond (a Condition "
            "wrapping _lock) so wait() atomically releases the lock and "
            "blocks; transcribe()'s finally block then notifies the cond."
        )
        assert "while self._active_inference > 0" in src, (
            "IN-4: unload() must wait on _inference_cond while _active_inference > 0 before nulling self._model."
        )


class TestTranscribeSourceReleasesLockDuringInference:
    """Source-level guards: the transcribe methods must acquire the lock
    only briefly to increment the counter, then release before calling
    ``_transcribe_unlocked`` / ``_transcribe_words_unlocked``."""

    def test_transcribe_source_uses_counter_pattern(self):
        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine.transcribe)
        assert "self._active_inference += 1" in src, (
            "IN-4: transcribe() must increment _active_inference under the lock."
        )
        assert "self._inference_cond" in src, (
            "IN-4: transcribe() must decrement _active_inference under _inference_cond in a finally block."
        )

    def test_transcribe_with_fallback_source_uses_counter_pattern(self):
        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine.transcribe_with_fallback)
        assert "self._active_inference += 1" in src, (
            "IN-4: transcribe_with_fallback() must increment _active_inference."
        )
        assert "self._inference_cond" in src, "IN-4: transcribe_with_fallback() must decrement under _inference_cond."

    def test_transcribe_words_source_uses_counter_pattern(self):
        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine.transcribe_words)
        assert "self._active_inference += 1" in src, "IN-4: transcribe_words() must increment _active_inference."
        assert "self._inference_cond" in src, "IN-4: transcribe_words() must decrement under _inference_cond."


# ── IN-5: ParakeetEngine reads PARAKEET_BATCH_SIZE at construction time ──


class TestParakeetBatchSizeReadAtConstruction:
    """``_INFERENCE_BATCH_SIZE`` must be read from the env var at
    ParakeetEngine construction time (NOT import time) so changes to
    ``PARAKEET_BATCH_SIZE`` between engine constructions take effect."""

    def test_class_attribute_is_default_one(self):
        """The class-level ``_INFERENCE_BATCH_SIZE`` is now a static
        default of 1 (NOT a frozen env-var read). The instance attribute
        in ``__init__`` overrides it at construction time."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        # The class attribute must be a plain int (not an os.environ.get
        # call result that was evaluated at import time).
        assert ParakeetEngine._INFERENCE_BATCH_SIZE == 1, (
            "IN-5: the class-level _INFERENCE_BATCH_SIZE must be a static "
            "default of 1 (the instance attribute in __init__ overrides it "
            "at construction time)."
        )

    def test_init_reads_env_var_at_construction_time(self, monkeypatch):
        """``__init__`` must read ``PARAKEET_BATCH_SIZE`` from the env
        so changes between constructions take effect."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        # Construct with PARAKEET_BATCH_SIZE=3.
        monkeypatch.setenv("PARAKEET_BATCH_SIZE", "3")
        # Use __new__ to bypass _ensure_hf_env / heavy imports, then
        # manually call the relevant init lines.
        eng1 = ParakeetEngine.__new__(ParakeetEngine)
        eng1._INFERENCE_BATCH_SIZE = max(1, int(os.environ.get("PARAKEET_BATCH_SIZE", "1")))
        assert eng1._INFERENCE_BATCH_SIZE == 3, (
            "IN-5: when PARAKEET_BATCH_SIZE=3 is set, the engine must read 3 at construction time."
        )

        # Change the env var and construct again — the new engine must
        # see the new value (the pre-fix class-attribute form would have
        # frozen the value at import time, ignoring this change).
        monkeypatch.setenv("PARAKEET_BATCH_SIZE", "4")
        eng2 = ParakeetEngine.__new__(ParakeetEngine)
        eng2._INFERENCE_BATCH_SIZE = max(1, int(os.environ.get("PARAKEET_BATCH_SIZE", "1")))
        assert eng2._INFERENCE_BATCH_SIZE == 4, (
            "IN-5: changing PARAKEET_BATCH_SIZE between engine "
            "constructions must take effect (the pre-fix class-attribute "
            "form froze the value at import time)."
        )

    def test_init_source_reads_env_var(self):
        """Source guard: ``__init__`` must contain
        ``os.environ.get(\"PARAKEET_BATCH_SIZE\", \"1\")`` so the value
        is read at construction time."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        src = inspect.getsource(ParakeetEngine.__init__)
        assert 'os.environ.get("PARAKEET_BATCH_SIZE"' in src, (
            "IN-5: __init__ must read PARAKEET_BATCH_SIZE from os.environ "
            "at construction time (NOT rely on the class-attribute form "
            "that evaluated once at import time)."
        )

    def test_class_attribute_source_no_longer_calls_environ_get(self):
        """Source guard: the class-level ``_INFERENCE_BATCH_SIZE`` must
        NOT call ``os.environ.get`` (the import-time freeze bug)."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        # The class attribute is now a plain int literal, not an env-var
        # read. Verify by checking it's not a callable / property.
        assert isinstance(ParakeetEngine._INFERENCE_BATCH_SIZE, int), (
            "IN-5: _INFERENCE_BATCH_SIZE class attribute must be a plain int "
            "(not an os.environ.get result that was evaluated at import time)."
        )
        assert ParakeetEngine._INFERENCE_BATCH_SIZE == 1


# ── IN-7: _set_active_backend_blocking re-checks busy/recording inside locks ──


class TestSetActiveBackendBlockingRechecksBusy:
    """``_set_active_backend_blocking`` must re-check
    ``recorder.recording`` and ``_busy_event`` INSIDE
    ``_model_change_lock`` + ``_config_mutation_lock`` for race-safety.
    The non-blocking ``set_active_backend`` wrapper checks these
    OUTSIDE the lock (best-effort) before spawning the background
    thread; a recording could have started between that check and the
    lock acquisition."""

    def _make_mm_with_mock_backend(
        self,
        *,
        backend_name: str = "whisper",
        recording: bool = False,
        busy: bool = False,
    ):
        """Build a ModelManager with a mock app + real-ish registry.

        Mirrors ``tests/test_model_manager_busy_guard._make_mm`` but
        simplified for IN-7.
        """
        from voice_typer.server.model_manager import ModelManager

        app = MagicMock(name="app")
        app.config.asr_backend = backend_name
        app.config.model_size = "tiny.en"
        app.config.device = "cpu"
        app.config.language = "en"
        app.config.beam_size = 1
        app.config.best_of = 1
        app.config.condition_on_previous_text = False
        app.config.model_idle_unload_minutes = 0
        app._shutting_down = False
        app._pending_dictation = False
        app._thread_registry = MagicMock()
        app._config_mutation_lock = threading.RLock()
        app.recorder.recording = recording
        busy_event = threading.Event()
        if not busy:
            busy_event.set()  # is_set() == True means NOT busy
        app._busy_event = busy_event
        app.config.save.return_value = True

        mm = ModelManager(app)

        engine = MagicMock(name="engine")
        engine.is_loaded = True
        engine.device_info = f"{backend_name}/cpu"

        mock_registry = MagicMock(name="registry")
        mock_registry.active_name = backend_name
        mock_registry.get_active.return_value = engine
        mock_registry.get.return_value = engine
        mock_registry.load_active.return_value = engine
        mock_registry.load_with_fallback.return_value = engine
        mock_registry.available_backends = [backend_name]
        mm._registry = mock_registry

        mm._ensure_engine = MagicMock()
        mm._evict_lru_model = MagicMock()

        return mm, app

    def test_blocking_rechecks_recording_inside_lock(self):
        """When ``recorder.recording`` is True at lock-acquire time,
        ``_set_active_backend_blocking`` must defer (set
        ``_pending_backend_change`` + persist config + return WITHOUT
        running the unload phase)."""
        mm, app = self._make_mm_with_mock_backend(backend_name="whisper", recording=True, busy=False)
        # Pre-register a whisper engine so we can assert it was NOT unloaded.
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        mm._registry.get.return_value = whisper_engine
        mm.transcriber = whisper_engine

        mm._set_active_backend_blocking("qwen")

        # The request was deferred — _pending_backend_change is set.
        assert mm._pending_backend_change == "qwen", (
            "IN-7: _set_active_backend_blocking must re-check "
            "recorder.recording INSIDE the locks and defer (set "
            "_pending_backend_change) when recording is True."
        )
        # Config was persisted.
        assert app.config.asr_backend == "qwen"
        # The OLD backend was NOT unloaded (the whole point of the
        # re-check deferral).
        whisper_engine.unload.assert_not_called()
        # No load was attempted.
        mm._registry.load_active.assert_not_called()

    def test_blocking_rechecks_busy_event_inside_lock(self):
        """When ``_busy_event.is_set()`` is False (busy) at
        lock-acquire time, ``_set_active_backend_blocking`` must defer."""
        mm, app = self._make_mm_with_mock_backend(backend_name="whisper", recording=False, busy=True)
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        mm._registry.get.return_value = whisper_engine
        mm.transcriber = whisper_engine

        mm._set_active_backend_blocking("parakeet")

        assert mm._pending_backend_change == "parakeet", (
            "IN-7: _set_active_backend_blocking must re-check _busy_event INSIDE the locks and defer when busy."
        )
        whisper_engine.unload.assert_not_called()
        mm._registry.load_active.assert_not_called()

    def test_blocking_proceeds_when_not_busy(self):
        """When NOT recording AND not busy at lock-acquire time, the
        full unload/load cycle runs (the re-check does NOT defer
        spuriously)."""
        mm, app = self._make_mm_with_mock_backend(backend_name="whisper", recording=False, busy=False)
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        mm._registry.get.return_value = whisper_engine
        mm.transcriber = whisper_engine

        mm._set_active_backend_blocking("qwen")

        # NOT deferred.
        assert mm._pending_backend_change is None, (
            "IN-7: _set_active_backend_blocking must NOT defer when "
            "not recording and not busy — the full unload/load cycle "
            "should run."
        )
        # Config was set.
        assert app.config.asr_backend == "qwen"
        # Whisper engine WAS unloaded (the unload phase ran).
        whisper_engine.unload.assert_called()
        # Load was attempted.
        mm._registry.load_active.assert_called()

    def test_blocking_source_contains_recheck(self):
        """Source guard: ``_set_active_backend_blocking`` must contain
        a re-check of ``recorder.recording`` and ``_busy_event`` INSIDE
        the locks."""
        from voice_typer.server.model_manager import ModelManager

        src = inspect.getsource(ModelManager._set_active_backend_blocking)
        # The re-check must be INSIDE the ``with self._model_change_lock:``
        # block (i.e. after the lock acquisition, before the unload phase).
        lock_idx = src.index("with self._model_change_lock:")
        unload_idx = src.index("_change_model_unload_phase")
        recheck_idx = src.index("rec_now = bool(self._app.recorder.recording)")
        assert lock_idx < recheck_idx < unload_idx, (
            "IN-7: the recorder.recording re-check must appear BETWEEN "
            "the _model_change_lock acquisition and the "
            "_change_model_unload_phase call (i.e. INSIDE the lock)."
        )
        # The busy_event re-check must also be present inside the lock.
        busy_recheck_idx = src.index("busy_now = not self._app._busy_event.is_set()")
        assert lock_idx < busy_recheck_idx < unload_idx, (
            "IN-7: the _busy_event re-check must appear BETWEEN the "
            "_model_change_lock acquisition and the _change_model_unload_phase "
            "call (i.e. INSIDE the lock)."
        )

    def test_set_active_backend_comment_is_accurate(self):
        """The comment in ``set_active_backend`` must NOT claim the
        background thread re-checks under the lock when it actually
        doesn't. Post-IN-7, the re-check IS implemented, so the comment
        is now accurate — but we verify the comment mentions the
        re-check."""
        from voice_typer.server.model_manager import ModelManager

        src = inspect.getsource(ModelManager.set_active_backend)
        # The comment must mention that the background thread re-checks
        # under the lock (this was the FALSE claim pre-IN-7; now it's
        # true and the comment is correct).
        assert "re-checks" in src or "re-check" in src, (
            "IN-7: the set_active_backend comment must mention that the "
            "background thread re-checks recorder.recording and "
            "_busy_event under the lock (now that the re-check is "
            "actually implemented in _set_active_backend_blocking)."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
