"""Tests for ``voice_typer.server.parakeet_engine`` (CR-77).

Parakeet TDT v3 is an optional ASR backend alongside Whisper / Qwen. It
uses NVIDIA's ``parakeet-tdt-0.6b-v3`` via HuggingFace Transformers and
auto-downloads weights on first load. These tests cover the engine's API
surface WITHOUT importing torch / transformers / huggingface_hub — those
heavy deps are mocked at the ``sys.modules`` level, mirroring how
``tests/test_qwen_engine.py`` mocks ``qwen_asr``.

Mocking pattern
---------------
The engine's ``_ensure_imports()`` classmethod lazily imports
``torch`` + ``transformers`` and stashes the modules on class attributes
(``_torch``, ``_AutoModelForTDT``, ``_AutoProcessor``). We mock both
modules via ``patch.dict("sys.modules", ...)`` so the lazy import sees
our mocks and populates the class attrs. Each test that triggers
``_ensure_imports()`` must therefore either:
  - run inside a ``with patch.dict("sys.modules", ...)`` block, OR
  - use the ``_make_engine_with_mocks`` helper which does that for it.

We also reset the class-level ``_imports_loaded`` flag between tests so
a successful import in one test doesn't leak into the next.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ─── Helpers ────────────────────────────────────────────────────────────


def _mock_torch():
    """Build a MagicMock that quacks like the bits of torch we use."""
    mock = MagicMock()
    mock.cuda.is_available.return_value = False
    mock.float16 = "fp16"
    mock.float32 = "fp32"
    return mock


def _mock_transformers():
    """Build a MagicMock that exposes ``AutoModelForTDT`` + ``AutoProcessor``."""
    mock = MagicMock()
    mock.AutoModelForTDT = MagicMock()
    mock.AutoProcessor = MagicMock()
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests.

    ``_ensure_imports`` caches ``_imports_loaded`` and the imported
    modules on the class. Without this reset, a test that runs
    ``_ensure_imports`` with mocked modules leaks the mocks into the
    next test (which may not have its own mocks set up).
    """
    from voice_typer.server.parakeet_engine import ParakeetEngine

    saved = (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._torch,
        ParakeetEngine._AutoModelForTDT,
        ParakeetEngine._AutoProcessor,
        ParakeetEngine._hf_home_set,
    )
    ParakeetEngine._imports_loaded = False
    ParakeetEngine._torch = None
    ParakeetEngine._AutoModelForTDT = None
    ParakeetEngine._AutoProcessor = None
    ParakeetEngine._hf_home_set = False
    yield
    (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._torch,
        ParakeetEngine._AutoModelForTDT,
        ParakeetEngine._AutoProcessor,
        ParakeetEngine._hf_home_set,
    ) = saved


def _make_engine(device: str = "cuda", language: str = "en"):
    """Build a ParakeetEngine WITHOUT touching torch / transformers.

    ``__init__`` calls ``_ensure_hf_env()`` which swallows all errors,
    so this is safe without mocks.
    """
    from voice_typer.server.parakeet_engine import ParakeetEngine

    return ParakeetEngine(device=device, language=language)


def _make_engine_with_mocks(device: str = "cuda", language: str = "en"):
    """Build a ParakeetEngine and force mocked torch/transformers imports.

    Returns ``(engine, mock_torch, mock_transformers)`` so the test can
    configure ``from_pretrained`` / ``cuda.is_available`` etc.
    """
    mock_torch = _mock_torch()
    mock_transformers = _mock_transformers()
    with patch.dict(
        sys.modules,
        {"torch": mock_torch, "transformers": mock_transformers},
    ):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine(device=device, language=language)
        # Force the lazy import to fire now (inside the patch context).
        ParakeetEngine._ensure_imports()
    return engine, mock_torch, mock_transformers


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

    def test_is_loaded_false_when_uninitialized(self):
        engine = _make_engine()
        assert engine.is_loaded is False

    def test_is_loaded_true_when_model_and_processor_set(self):
        engine = _make_engine()
        engine._model = MagicMock()
        assert engine.is_loaded is False  # processor still None
        engine._processor = MagicMock()
        assert engine.is_loaded is True

    def test_device_info_reflects_device(self):
        engine = _make_engine(device="cpu")
        assert engine.device_info == "parakeet/cpu"

    def test_loaded_via_includes_model_id(self):
        engine = _make_engine(device="cuda")
        assert "parakeet/cuda/" in engine.loaded_via
        assert "parakeet-tdt-0.6b-v3" in engine.loaded_via


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


# ─── load() ─────────────────────────────────────────────────────────────


class TestParakeetEngineLoad:
    """``load()`` downloads + loads the model with mocked torch/transformers."""

    def test_load_failure_raises_when_imports_missing(self):
        """If torch/transformers aren't installed, load() returns False."""
        engine = _make_engine()
        # No patch.dict for torch/transformers — _ensure_imports fails.
        # But there's a wrinkle: the autouse conftest fixture does NOT
        # mock torch/transformers, so the import will only fail if the
        # real packages aren't installed. On a real test machine they
        # likely aren't, but to make this test deterministic we force
        # the lazy-import guard to fail by clearing sys.modules entries
        # and refusing to install mocks.
        with patch.dict(sys.modules, {"torch": None, "transformers": None}):
            result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_success_with_cached_model(self):
        """load() succeeds when imports + cache + from_pretrained all work."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cpu")

        # _is_cached() reads from a config dir; patch to skip the disk
        # probe and report "already cached" so we don't try to download.
        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()
            result = engine.load()

        assert result is True
        assert engine.is_loaded is True
        assert engine._model is not None
        assert engine._processor is not None

    def test_load_cuda_falls_back_to_cpu_when_unavailable(self):
        """CUDA requested but not available → load on CPU, still succeeds."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = False

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()
            result = engine.load()

        assert result is True
        # from_pretrained must have been called with device_map="cpu".
        call_kwargs = mock_transformers.AutoModelForTDT.from_pretrained.call_args
        assert call_kwargs.kwargs.get("device_map") == "cpu"

    def test_load_returns_true_when_already_loaded(self):
        """Idempotent load: if model is already set, return True immediately."""
        engine, _, _ = _make_engine_with_mocks()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        # from_pretrained must NOT be called.
        result = engine.load()
        assert result is True

    def test_load_failure_when_from_pretrained_raises(self):
        """An exception from from_pretrained returns False (not re-raise)."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cpu")
        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.side_effect = RuntimeError("disk read failed")
            result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_invokes_progress_callback(self):
        """When supplied, the progress callback is called with status messages."""
        engine, _, mock_transformers = _make_engine_with_mocks(device="cpu")
        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()
            messages: list[str] = []
            engine.load(progress_callback=messages.append)
        assert any("Parakeet" in m for m in messages)
        assert any("ready" in m.lower() for m in messages)


# ─── transcribe() ───────────────────────────────────────────────────────


class TestParakeetEngineTranscribe:
    """``transcribe()`` returns cleaned text or raises if not loaded."""

    def test_transcribe_when_not_loaded_raises(self):
        engine = _make_engine()
        audio = np.ones(16000, dtype=np.float32)
        with pytest.raises(RuntimeError, match="not loaded"):
            engine.transcribe(audio)

    def test_transcribe_empty_audio_returns_empty_string(self):
        """``transcribe([])`` short-circuits before touching the model."""
        engine = _make_engine()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        result = engine.transcribe(np.array([], dtype=np.float32))
        assert result == ""
        # Model.generate must NOT have been called.
        engine._model.generate.assert_not_called()

    def test_transcribe_short_audio_calls_segment_once(self):
        """Audio ≤ CHUNK_SECONDS (25s) → single _transcribe_segment call."""
        engine = _make_engine()
        # Wire up mocks for processor + model.
        mock_processor = MagicMock()
        mock_inputs = MagicMock()
        mock_processor.return_value = mock_inputs
        mock_output = MagicMock()
        mock_output.sequences = [42]
        mock_processor.decode.return_value = "hello world"
        mock_model = MagicMock()
        mock_model.generate.return_value = mock_output
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)  # 1s of audio
        result = engine.transcribe(audio)
        assert result == "hello world"
        mock_model.generate.assert_called_once()

    def test_transcribe_strips_whitespace(self):
        engine = _make_engine()
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "  hello world  "
        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock(sequences=[42])
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)
        assert engine.transcribe(audio) == "hello world"

    def test_transcribe_returns_empty_when_decoded_is_empty_list(self):
        """``processor.decode`` returning a list with no items → ""."""
        engine = _make_engine()
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = []
        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock(sequences=[])
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)
        assert engine.transcribe(audio) == ""

    def test_transcribe_returns_first_element_when_decode_returns_list(self):
        engine = _make_engine()
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = ["first", "second"]
        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock(sequences=[42])
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)
        assert engine.transcribe(audio) == "first"

    def test_transcribe_non_english_text_filtered_for_en_language(self):
        """language="en" + non-Latin output → empty string."""
        engine = _make_engine(language="en")
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "你好世界"
        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock(sequences=[42])
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)
        assert engine.transcribe(audio) == ""

    def test_transcribe_non_english_text_kept_for_non_en_language(self):
        """language != "en" → no Latin filter applied."""
        engine = _make_engine(language="fr")
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "你好世界"
        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock(sequences=[42])
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)
        assert engine.transcribe(audio) == "你好世界"

    def test_transcribe_long_audio_splits_into_chunks(self):
        """Audio > CHUNK_SECONDS (25s) → multiple chunks, merged output."""
        engine = _make_engine()
        # 30s of audio → 2 chunks.
        audio = np.ones(int(30 * 16000), dtype=np.float32)

        # Each call to _transcribe_segment returns a sentence. The merge
        # step looks for overlap duplicates — make them distinct so no
        # words are dropped.
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.side_effect = ["hello there", "world peace"]
        mock_model = MagicMock()
        mock_model.generate.side_effect = [
            MagicMock(sequences=[1]),
            MagicMock(sequences=[2]),
        ]
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        result = engine.transcribe(audio)
        # Two distinct sentences, no overlap → concatenated with space.
        assert result == "hello there world peace"
        assert mock_model.generate.call_count == 2


# ─── transcribe_with_fallback() ─────────────────────────────────────────


class TestParakeetEngineTranscribeWithFallback:
    """``transcribe_with_fallback`` retries on CPU after a CUDA error."""

    def test_fallback_raises_when_not_loaded(self):
        from voice_typer.server.parakeet_engine import TranscriptionBackendError

        engine = _make_engine()
        with pytest.raises(TranscriptionBackendError, match="not loaded"):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

    def test_fallback_returns_empty_for_empty_audio(self):
        engine = _make_engine()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        result = engine.transcribe_with_fallback(np.array([], dtype=np.float32))
        assert result == ""

    def test_fallback_retries_on_cpu_after_cuda_error(self):
        """A CUDA error on the GPU path → retry on CPU, return text."""
        engine, mock_torch, _ = _make_engine_with_mocks(device="cuda")
        # Wire up mocks for the CPU retry path. The GPU path raises a
        # CUDA error; transcribe_with_fallback catches it, moves the
        # model to CPU, and calls _transcribe_impl.
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "cpu result"
        mock_model = MagicMock()
        mock_model.device = "cuda"
        mock_model.dtype = "float16"
        engine._processor = mock_processor
        engine._model = mock_model

        # Stub transcribe to raise once, then _transcribe_impl to return.
        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("CUDA out of memory")),
            patch.object(engine, "_transcribe_impl", return_value="cpu result") as mock_impl,
        ):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert result == "cpu result"
        mock_model.to.assert_called_once()
        # CPU retry must pin dtype=float32 (HIGH-18 / PERF-REL-1).
        call_kwargs = mock_model.to.call_args.kwargs
        assert call_kwargs.get("device") == "cpu"
        assert call_kwargs.get("dtype") == mock_torch.float32
        mock_impl.assert_called_once()

    def test_fallback_reraises_non_cuda_errors_as_backend_error(self):
        """A non-CUDA error is wrapped in TranscriptionBackendError."""
        from voice_typer.server.parakeet_engine import TranscriptionBackendError

        engine = _make_engine(device="cpu")
        engine._model = MagicMock()
        engine._processor = MagicMock()

        with (
            patch.object(engine, "transcribe", side_effect=ValueError("bad audio shape")),
            pytest.raises(TranscriptionBackendError, match="bad audio shape"),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

    def test_fallback_cuda_failure_on_cpu_too_raises_backend_error(self):
        """Both GPU path AND CPU retry failing → TranscriptionBackendError."""
        from voice_typer.server.parakeet_engine import TranscriptionBackendError

        engine, mock_torch, _ = _make_engine_with_mocks(device="cuda")
        engine._model = MagicMock()
        engine._processor = MagicMock()

        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("CUDA cublas error")),
            patch.object(
                engine,
                "_transcribe_impl",
                side_effect=RuntimeError("CPU also failed"),
            ),
            pytest.raises(TranscriptionBackendError, match="CPU fallback also failed"),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))


# ─── unload() ───────────────────────────────────────────────────────────


class TestParakeetEngineUnload:
    """``unload()`` clears the model + processor and releases GPU memory."""

    def test_unload_clears_model_and_processor(self):
        engine = _make_engine()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        assert engine.is_loaded is True

        # Patch release_gpu_memory so we don't actually import torch.
        with patch("voice_typer.server.transcription.release_gpu_memory") as mock_release:
            engine.unload()

        assert engine._model is None
        assert engine._processor is None
        assert engine.is_loaded is False
        mock_release.assert_called_once()


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
        from voice_typer.server.parakeet_engine import ParakeetEngine

        assert ParakeetEngine._compute_overlap_skip(["alpha", "beta"], ["gamma", "delta"]) == 0

    def test_compute_overlap_skip_with_overlap_returns_count(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        # prev tail contains "the dog"; new head is "the dog" → skip 2.
        assert ParakeetEngine._compute_overlap_skip(["I", "saw", "the", "dog"], ["the", "dog", "ran"]) == 2

    def test_compute_overlap_skip_empty_inputs_return_zero(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        assert ParakeetEngine._compute_overlap_skip([], ["a"]) == 0
        assert ParakeetEngine._compute_overlap_skip(["a"], []) == 0
        assert ParakeetEngine._compute_overlap_skip([], []) == 0

    def test_compute_overlap_skip_case_insensitive(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        assert ParakeetEngine._compute_overlap_skip(["The", "Dog"], ["the", "dog", "ran"]) == 2

    def test_compute_overlap_skip_ignores_punctuation(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

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


# ─── G4-H-04 / G4-CR-06 / G4-M-44 (Session 7 — Group 4) regression tests ──


class TestG4H04ConsentGate:
    """G4-H-04: ParakeetEngine.load() must gate HuggingFace downloads on
    explicit ``config.huggingface_consent``.  Cache hits (model already
    on disk) do NOT need consent; only the network download does.

    Pre-fix: ``ParakeetEngine.load()`` called ``snapshot_download()`` with
    NO consent check — user could trigger a ~2.5 GB download from
    huggingface.co without consent by selecting the Parakeet backend and
    starting a dictation.
    """

    def test_consent_false_raises_on_cache_miss(self):
        """When ``config.huggingface_consent`` is False and the model is
        NOT cached, ``load()`` raises ``ConsentRequiredError``."""
        from voice_typer.server.cloud_engines import ConsentRequiredError

        engine, _, _ = _make_engine_with_mocks(device="cpu")
        # Config with consent=False (the default).
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        with (
            patch.object(type(engine), "_is_cached", return_value=False),
        ):
            with pytest.raises(ConsentRequiredError, match="consent not given"):
                engine.load()

    def test_consent_none_raises_on_cache_miss(self):
        """When ``config`` is None (degenerate path) and the model is
        NOT cached, ``load()`` raises ``ConsentRequiredError`` — safe
        default per GDPR Art. 6/13."""
        from voice_typer.server.cloud_engines import ConsentRequiredError

        engine, _, _ = _make_engine_with_mocks(device="cpu")
        engine.config = None  # degenerate / test path

        with patch.object(type(engine), "_is_cached", return_value=False):
            with pytest.raises(ConsentRequiredError, match="consent not given"):
                engine.load()

    def test_consent_true_proceeds_to_download(self):
        """When ``config.huggingface_consent`` is True and the model is
        NOT cached, ``load()`` proceeds with the download (existing
        happy path preserved)."""
        engine, _, mock_transformers = _make_engine_with_mocks(device="cpu")
        engine.config = type("FakeConfig", (), {"huggingface_consent": True})()

        # Make _is_cached return False on the first call (cache-miss
        # branch) and True on subsequent calls (post-download).  We
        # also need _verify_model_integrity to return True so the
        # unconditional integrity check passes.
        cached_calls = [False, True, True]

        def _fake_cached():
            return cached_calls.pop(0) if cached_calls else True

        with (
            patch.object(type(engine), "_is_cached", side_effect=_fake_cached),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=True,
            ),
            patch("huggingface_hub.snapshot_download", return_value="/fake/path"),
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()
            # Disable the cache_dir block so we don't touch the real FS.
            with patch("voice_typer.server.config._config_dir") as mock_dir:
                # Use a non-existent path so model_dir.is_dir() is False.
                mock_dir.return_value = Path("/nonexistent/parakeet/test")
                result = engine.load()

        assert result is True
        assert engine.is_loaded is True

    def test_cache_hit_skips_consent_gate(self):
        """When the model IS cached, ``load()`` does NOT raise even if
        ``config.huggingface_consent`` is False — local file access
        doesn't need network consent."""
        engine, _, mock_transformers = _make_engine_with_mocks(device="cpu")
        # Config with consent=False, BUT model is cached.
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=True,
            ),
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()
            # Bypass the cache_dir block.
            with patch("voice_typer.server.config._config_dir") as mock_dir:
                mock_dir.return_value = Path("/nonexistent/parakeet/test")
                result = engine.load()

        assert result is True


class TestG4CR06UnconditionalIntegrityVerify:
    """G4-CR-06: ``ParakeetEngine.load()`` must call
    ``verify_model_integrity`` UNCONDITIONALLY on every load, not just
    after a fresh download.  Cache hits (model already on disk) must
    also be verified — an attacker with write access to the HF cache
    could tamper with ``model.safetensors`` and the next load would
    feed tampered weights to the ASR engine with no SHA-256 check.

    Pre-fix: integrity verification was gated behind the cache-miss
    branch; cache hits skipped verification entirely.
    """

    def test_cache_hit_triggers_integrity_check(self):
        """When ``_is_cached`` returns True, ``load()`` STILL calls
        ``_verify_model_integrity`` against the manifest."""
        engine, _, mock_transformers = _make_engine_with_mocks(device="cpu")
        # No config — but cache hit so consent gate is skipped.
        engine.config = None

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=True,
            ) as mock_verify,
        ):
            mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
            mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()
            with patch("voice_typer.server.config._config_dir") as mock_dir:
                mock_dir.return_value = Path("/nonexistent/parakeet/test")
                result = engine.load()

        assert result is True
        # _verify_model_integrity must have been called at least once.
        mock_verify.assert_called()

    def test_cache_hit_with_tampered_model_returns_false(self, tmp_path):
        """When the cached model fails integrity check, ``load()``
        returns False and cleans up the tampered cache dir."""
        # Build a fake HF cache dir with a tampered snapshot.
        cache_root = tmp_path / "huggingface" / "hub"
        model_dir = cache_root / "models--nvidia--parakeet-tdt-0.6b-v3"
        snapshot_dir = model_dir / "snapshots" / "abc123"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        (snapshot_dir / "config.json").write_text('{"tampered": true}')

        engine, _, _ = _make_engine_with_mocks(device="cpu")
        engine.config = None

        # _is_cached returns True (cache hit), but _verify_model_integrity
        # returns False (tampered).
        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=False,
            ),
            patch("voice_typer.server.config._config_dir", return_value=tmp_path),
        ):
            result = engine.load()

        assert result is False
        assert engine.is_loaded is False
        # G4-CR-06 / cache cleanup: the tampered cache dir MUST have
        # been removed so the next load() doesn't re-discover it.
        assert not model_dir.exists(), (
            "G4-CR-06: tampered HF cache directory must be removed after "
            "integrity check failure so the next load() doesn't re-load "
            "the same tampered files."
        )


class TestG4M44CpuFallbackNotification:
    """G4-M-44: Parakeet CUDA→CPU fallback must emit a ONE-TIME tray
    notification via ``event_bus.publish`` and must NOT permanently
    set ``self.device`` to ``"cpu"`` — the next ``load()`` re-attempts
    CUDA (per-transcription fallback, not permanent).

    Pre-fix: the fallback permanently moved the model to CPU for the
    session with no user notification.  Dictation got mysteriously
    slower and the user had no idea why.
    """

    def test_cpu_fallback_emits_notification(self):
        """A successful CUDA→CPU fallback publishes a notification +
        status event via event_bus."""
        engine, mock_torch, _ = _make_engine_with_mocks(device="cuda")
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "cpu result"
        mock_model = MagicMock()
        mock_model.device = "cuda"
        mock_model.dtype = "float16"
        engine._processor = mock_processor
        engine._model = mock_model

        published_events: list[dict] = []
        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("CUDA out of memory")),
            patch.object(engine, "_transcribe_impl", return_value="cpu result"),
            patch(
                "voice_typer.server.event_bus.publish",
                side_effect=lambda e: published_events.append(e),
            ),
        ):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert result == "cpu result"
        # G4-M-44: notification event must have been published.
        notif_events = [e for e in published_events if e.get("type") == "notification"]
        assert notif_events, (
            "G4-M-44: CUDA→CPU fallback must emit a 'notification' event "
            "via event_bus.publish so the user knows why dictation got slower."
        )
        assert "GPU transcription failed" in notif_events[0]["data"]["message"]
        # G4-M-44: status event for tray "(CPU fallback)" suffix.
        status_events = [e for e in published_events if e.get("type") == "parakeet_cpu_fallback"]
        assert status_events, (
            "G4-M-44: CUDA→CPU fallback must emit a 'parakeet_cpu_fallback' "
            "status event so tray.py can show '(CPU fallback)' suffix."
        )

    def test_cpu_fallback_notification_is_one_time(self):
        """The notification fires ONCE per session — subsequent
        fallbacks in the same load() cycle do NOT re-notify."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "cpu result"
        mock_model = MagicMock()
        mock_model.device = "cuda"
        mock_model.dtype = "float16"
        engine._processor = mock_processor
        engine._model = mock_model

        published_events: list[dict] = []
        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("CUDA cublas error")),
            patch.object(engine, "_transcribe_impl", return_value="cpu result"),
            patch(
                "voice_typer.server.event_bus.publish",
                side_effect=lambda e: published_events.append(e),
            ),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))
            # Second call: fallback fires again but should NOT re-notify.
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        notif_events = [e for e in published_events if e.get("type") == "notification"]
        assert len(notif_events) == 1, (
            "G4-M-44: notification must be ONE-TIME per session — "
            f"got {len(notif_events)} notifications for 2 fallbacks."
        )

    def test_cpu_fallback_does_not_mutate_device(self):
        """``self.device`` stays ``"cuda"`` after a CPU fallback so the
        next ``load()`` re-attempts CUDA (per-transcription fallback)."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "cpu result"
        mock_model = MagicMock()
        mock_model.device = "cuda"
        mock_model.dtype = "float16"
        engine._processor = mock_processor
        engine._model = mock_model

        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("CUDA cudnn error")),
            patch.object(engine, "_transcribe_impl", return_value="cpu result"),
            patch("voice_typer.server.event_bus.publish", return_value=True),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert engine.device == "cuda", (
            "G4-M-44: self.device must stay 'cuda' after CPU fallback so "
            "the next load() re-attempts CUDA (per-transcription fallback, "
            "not permanent)."
        )

    def test_load_resets_notification_flag(self):
        """``load()`` resets ``_cpu_fallback_notified`` so a fallback
        after the next reload re-notifies the user."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        # Simulate a prior fallback that already notified.
        engine._cpu_fallback_notified = True

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=True,
            ),
            patch("voice_typer.server.config._config_dir") as mock_dir,
        ):
            mock_dir.return_value = Path("/nonexistent/parakeet/test")
            # Bypass from_pretrained — we just want the flag reset.
            engine._model = MagicMock()
            engine._processor = MagicMock()
            engine.load()

        assert engine._cpu_fallback_notified is False, (
            "G4-M-44: load() must reset _cpu_fallback_notified so the next fallback re-notifies the user."
        )
