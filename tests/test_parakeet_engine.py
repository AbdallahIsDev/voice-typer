"""Tests for the module-level helpers in ``voice_typer.server.parakeet_engine``.

The ONNX migration (PLAN_ONNX_INTEGRATION.md §3) moved most of the
engine's behavior under the ``onnx_asr.load_model`` backend. Engine-class
behavior (load, transcribe, fallback, abort, integrity) is now covered
by the focused ONNX test files:

- ``tests/test_parakeet_onnx_load.py`` — load + is_available + providers
- ``tests/test_parakeet_onnx_transcribe.py`` — parity + transcribe path
- ``tests/test_parakeet_onnx_abort.py`` — inter-chunk abort contract
- ``tests/test_parakeet_onnx_gpu_fallback.py`` — CUDA→CPU session recreation
- ``tests/test_parakeet_onnx_sha.py`` — manifest + integrity allowlist
- ``tests/test_parakeet_cpu_abort.py`` — CPU-fallback abort gate guards
- ``tests/test_parakeet_warmup.py`` — warmup-removed regression guard

This file pins the module-level helper functions re-exported by
``parakeet_engine`` (the canonical home is ``asr_utils.py`` per §5.3/§5.4):

- ``_is_likely_english`` / ``_is_latin_char`` — language-hallucination filter
- ``_merge_chunks`` / ``_compute_overlap_skip`` — overlap-dedup chunk merge
- ``_split_audio`` — chunk-splitting delegate (asr_utils.split_audio)
- ``ParakeetEngine.unload`` — model memory release (not covered elsewhere)
- ``ParakeetEngine.__init__`` — config + state setup (not covered elsewhere)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402

# ─── Helpers ────────────────────────────────────────────────────────────


def _mock_onnx_asr_module() -> MagicMock:
    """Build a MagicMock that quacks like the bits of ``onnx_asr`` we use."""
    mock = MagicMock(name="mock_onnx_asr")
    mock.__version__ = "0.12.0-test"
    mock.load_model.side_effect = lambda *args, **kwargs: MagicMock(name="mock_onnx_asr_model")
    return mock


def _mock_onnxruntime_module() -> MagicMock:
    """Build a MagicMock that quacks like the bits of ``onnxruntime`` we use."""
    mock = MagicMock(name="mock_onnxruntime")
    mock.__version__ = "1.20.0-test"
    mock.get_available_providers.return_value = ["CPUExecutionProvider"]
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests.

    ``_ensure_imports`` caches ``_imports_loaded`` + the imported
    modules on the class. Without this reset, a test that runs
    ``_ensure_imports`` with mocked modules leaks the mocks into the
    next test.
    """
    saved = (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._onnx_asr,
        ParakeetEngine._ort,
    )
    ParakeetEngine._imports_loaded = False
    ParakeetEngine._onnx_asr = None
    ParakeetEngine._ort = None
    yield
    (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._onnx_asr,
        ParakeetEngine._ort,
    ) = saved


def _make_engine(device: str = "cuda", language: str = "en"):
    """Build a ParakeetEngine WITHOUT triggering the lazy onnx_asr import."""
    return ParakeetEngine(device=device, language=language)


def _make_engine_with_mocks(device: str = "cpu", language: str = "en"):
    """Build a ParakeetEngine and force mocked onnx_asr/onnxruntime imports.

    Returns ``(engine, mock_onnx_asr, mock_onnxruntime)``.
    """
    mock_onnx_asr = _mock_onnx_asr_module()
    mock_onnxruntime = _mock_onnxruntime_module()
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device=device, language=language)
        ParakeetEngine._ensure_imports()
    return engine, mock_onnx_asr, mock_onnxruntime


# ─── Init ───────────────────────────────────────────────────────────────


class TestParakeetEngineInit:
    """``ParakeetEngine.__init__`` stores config without importing deps."""

    def test_init_defaults(self):
        engine = _make_engine()
        assert engine.device == "cuda"  # default device is cuda
        assert engine.language == "en"
        assert engine._model is None
        assert engine._processor is None
        assert engine.is_loaded is False

    def test_init_cpu_device(self):
        engine = _make_engine(device="cpu")
        assert engine.device == "cpu"

    def test_init_non_english_language(self):
        engine = _make_engine(language="fr")
        assert engine.language == "fr"

    def test_init_default_batch_size_is_two(self):
        """``_INFERENCE_BATCH_SIZE`` default is 2 (kept for backward
        compat with pre-migration tests even though the ONNX backend
        doesn't batch — ``onnx_asr.recognize`` processes one audio at
        a time)."""
        engine = _make_engine()
        assert engine._INFERENCE_BATCH_SIZE == 2

    def test_init_batch_size_env_var_overrides_default(self, monkeypatch):
        """``PARAKEET_BATCH_SIZE`` env var must override the default of 2."""
        monkeypatch.setenv("PARAKEET_BATCH_SIZE", "4")
        engine = _make_engine()
        assert engine._INFERENCE_BATCH_SIZE == 4

    def test_init_batch_size_clamped_to_minimum_one(self, monkeypatch):
        """A non-positive ``PARAKEET_BATCH_SIZE`` is clamped to 1."""
        monkeypatch.setenv("PARAKEET_BATCH_SIZE", "0")
        engine = _make_engine()
        assert engine._INFERENCE_BATCH_SIZE == 1

    def test_is_loaded_false_when_uninitialized(self):
        engine = _make_engine()
        assert engine.is_loaded is False

    def test_is_loaded_true_when_model_set(self):
        """ONNX engine: ``is_loaded`` checks ``_model`` only (no
        separate processor needed — ``onnx_asr.load_model`` bundles them)."""
        engine = _make_engine()
        engine._model = MagicMock()
        assert engine.is_loaded is True

    def test_device_info_reflects_device(self):
        engine = _make_engine(device="cpu")
        assert engine.device_info == "parakeet/cpu"

    def test_loaded_via_includes_model_id(self):
        engine = _make_engine(device="cuda")
        assert "parakeet/cuda/" in engine.loaded_via
        assert "grikdotnet/parakeet-tdt-0.6b-fp16" in engine.loaded_via


# ─── Module-level helpers ───────────────────────────────────────────────


class TestIsLikelyEnglish:
    """``_is_likely_english`` filters non-Latin-script hallucinations."""

    def test_plain_english_passes(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        assert _is_likely_english("Hello world") is True

    def test_empty_string_passes(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        assert _is_likely_english("") is True
        assert _is_likely_english("   ") is True

    def test_punctuation_only_passes(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        assert _is_likely_english("...") is True
        assert _is_likely_english("!!!  ???") is True

    def test_digits_pass(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        assert _is_likely_english("12345") is True

    def test_pure_cjk_rejected(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        assert _is_likely_english("你好世界") is False

    def test_majority_cjk_rejected(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        # 4 CJK + 1 Latin = 80% non-Latin → rejected.
        assert _is_likely_english("你好a界") is False

    def test_minority_cjk_passes(self):
        from voice_typer.server.parakeet_engine import _is_likely_english

        # 1 CJK + 10 Latin = ~9% non-Latin → passes (< 30% threshold).
        assert _is_likely_english("Hello world 你 test") is True


class TestIsLatinChar:
    """``_is_latin_char`` is the building block of the English filter."""

    def test_latin_letter_is_latin(self):
        from voice_typer.server.parakeet_engine import _is_latin_char

        assert _is_latin_char("a") is True
        assert _is_latin_char("Z") is True

    def test_digit_is_latin(self):
        from voice_typer.server.parakeet_engine import _is_latin_char

        assert _is_latin_char("5") is True

    def test_whitespace_is_latin(self):
        from voice_typer.server.parakeet_engine import _is_latin_char

        # Unicode category "Z*" (Separator) — space is Zs, treated as Latin.
        assert _is_latin_char(" ") is True

    def test_punctuation_is_latin(self):
        from voice_typer.server.parakeet_engine import _is_latin_char

        assert _is_latin_char(".") is True
        assert _is_latin_char(",") is True
        assert _is_latin_char("!") is True

    def test_cjk_is_not_latin(self):
        from voice_typer.server.parakeet_engine import _is_latin_char

        assert _is_latin_char("你") is False
        assert _is_latin_char("世") is False


# ─── unload() ───────────────────────────────────────────────────────────


class TestParakeetEngineUnload:
    """``unload()`` clears the model + processor and runs gc + the
    (no-op for ORT) ``release_gpu_memory()`` helper."""

    def test_unload_clears_model_and_processor(self):
        engine = _make_engine()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        assert engine.is_loaded is True

        # ``release_gpu_memory`` is imported locally inside ``unload()``
        # from ``voice_typer.server.asr_utils`` (the canonical location).
        with patch("voice_typer.server.asr_utils.release_gpu_memory") as mock_release:
            engine.unload()

        assert engine._model is None
        assert engine._processor is None
        assert engine.is_loaded is False
        mock_release.assert_called_once()

    def test_unload_runs_gc(self):
        """``unload()`` must run ``gc.collect()`` AFTER nulling the model
        (OUTSIDE the lock — see the docstring) so the ORT session is
        eligible for immediate destruction."""
        engine = _make_engine()
        engine._model = MagicMock()

        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("gc.collect") as mock_gc,
        ):
            engine.unload()

        mock_gc.assert_called_once()

    def test_unload_when_already_unloaded_is_noop(self):
        """Calling ``unload()`` when the model is already None must not
        raise (idempotent contract — callers like ``transcribe_with_fallback``
        may unload twice in error paths)."""
        engine = _make_engine()
        assert engine._model is None
        with patch("voice_typer.server.asr_utils.release_gpu_memory"):
            # Must not raise.
            engine.unload()
        assert engine._model is None

    def test_unload_waits_for_active_inference(self):
        """``unload()`` must wait for ``_active_inference == 0`` before
        nulling the model so a concurrent transcribe() doesn't
        dereference a freed ORT session (use-after-free guard).

        Verified by setting ``_active_inference = 1`` BEFORE unload and
        asserting the model is NOT nulled while the count is non-zero.
        A background thread then decrements the count + notifies the
        condition variable; unload completes and nulls the model.
        """
        import threading
        import time

        engine = _make_engine()
        engine._model = MagicMock()
        engine._active_inference = 1

        def _release_inference():
            time.sleep(0.05)
            with engine._inference_cond:
                engine._active_inference -= 1
                engine._inference_cond.notify_all()

        with patch("voice_typer.server.asr_utils.release_gpu_memory"):
            t = threading.Thread(target=_release_inference, daemon=True)
            t.start()
            engine.unload()
            t.join(timeout=2.0)

        assert not t.is_alive(), "unload() should have completed after the inference slot was released"
        assert engine._model is None


# ─── Chunk merge / overlap dedup ─────────────────────────────────────────


class TestMergeChunks:
    """``_merge_chunks`` + ``_compute_overlap_skip`` handle chunk boundaries."""

    def test_empty_list_returns_empty(self):
        engine = _make_engine()
        assert engine._merge_chunks([]) == ""

    def test_single_chunk_returned_as_is(self):
        engine = _make_engine()
        result = engine._merge_chunks(["hello world"])
        assert result == "hello world"

    def test_two_chunks_no_overlap_concatenates(self):
        engine = _make_engine()
        result = engine._merge_chunks(["hello there", "world peace"])
        # No overlap duplicate detected → all words kept.
        assert result == "hello there world peace"

    def test_two_chunks_with_overlap_dedups_boundary(self):
        """If the second chunk's leading words duplicate the first chunk's
        trailing words, those duplicates are skipped at the boundary."""
        engine = _make_engine()
        # "the dog" appears at end of chunk 1 AND start of chunk 2 → skipped.
        result = engine._merge_chunks(["I saw the dog", "the dog ran fast"])
        assert result == "I saw the dog ran fast"

    def test_compute_overlap_skip_no_overlap_returns_zero(self):
        assert ParakeetEngine._compute_overlap_skip(["alpha", "beta"], ["gamma", "delta"]) == 0

    def test_compute_overlap_skip_with_overlap_returns_count(self):
        # prev tail contains "the dog"; new head is "the dog" → skip 2.
        assert ParakeetEngine._compute_overlap_skip(["I", "saw", "the", "dog"], ["the", "dog", "ran"]) == 2

    def test_compute_overlap_skip_empty_inputs_return_zero(self):
        assert ParakeetEngine._compute_overlap_skip([], ["a"]) == 0
        assert ParakeetEngine._compute_overlap_skip(["a"], []) == 0
        assert ParakeetEngine._compute_overlap_skip([], []) == 0

    def test_compute_overlap_skip_case_insensitive(self):
        assert ParakeetEngine._compute_overlap_skip(["The", "Dog"], ["the", "dog", "ran"]) == 2

    def test_compute_overlap_skip_ignores_punctuation(self):
        # Punctuation is stripped before comparison.
        assert ParakeetEngine._compute_overlap_skip(["The", "dog."], ["the", "dog", "ran"]) == 2


# ─── _split_audio ────────────────────────────────────────────────────────


class TestSplitAudio:
    """``_split_audio`` produces overlapping chunks for long audio."""

    def test_short_audio_returns_single_chunk(self):
        engine = _make_engine()
        audio = np.ones(16000, dtype=np.float32)  # 1s
        chunks = engine._split_audio(audio, chunk_sec=25, overlap_sec=3)
        assert len(chunks) == 1
        assert len(chunks[0]) == 16000

    def test_long_audio_produces_multiple_chunks(self):
        engine = _make_engine()
        # 60s of audio → ceil(60 / (25-3)) = 3 chunks.
        audio = np.ones(int(60 * 16000), dtype=np.float32)
        chunks = engine._split_audio(audio, chunk_sec=25, overlap_sec=3)
        assert len(chunks) >= 2
        # Each chunk is at most 25s.
        for chunk in chunks:
            assert len(chunk) <= 25 * 16000

    def test_chunks_have_expected_overlap(self):
        """Successive chunks share ``overlap_sec`` of audio."""
        engine = _make_engine()
        # 50s of audio, 25s chunks with 3s overlap.
        audio = np.arange(int(50 * 16000), dtype=np.float32)
        chunks = engine._split_audio(audio, chunk_sec=25, overlap_sec=3)
        if len(chunks) >= 2:
            # The overlap is at the START of chunk[1] and the END of
            # chunk[0]. Verify by checking the first ``overlap`` samples
            # of chunk[1] match the last ``overlap`` samples of chunk[0].
            overlap_samples = int(3 * 16000)
            np.testing.assert_array_equal(
                chunks[1][:overlap_samples],
                chunks[0][-overlap_samples:],
            )
