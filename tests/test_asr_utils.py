"""Tests for :func:`voice_typer.server.asr_utils.split_audio`."""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.asr_utils import split_audio

SR = WHISPER_SAMPLE_RATE


class TestSplitAudio:
    """``split_audio`` chunking contract."""

    def test_empty_audio_returns_empty_list(self):
        audio = np.zeros(0, dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        assert chunks == []

    def test_audio_shorter_than_chunk_returns_single_chunk(self):
        # 1s of audio, 25s chunks → single chunk == whole array.
        audio = np.arange(SR, dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        assert len(chunks) == 1
        np.testing.assert_array_equal(chunks[0], audio)

    def test_audio_exactly_chunk_sized_returns_single_chunk(self):
        # 25s of audio at the default sample rate, 25s chunks → 1 chunk.
        chunk_samples = int(25.0 * SR)
        audio = np.arange(chunk_samples, dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        assert len(chunks) == 1
        np.testing.assert_array_equal(chunks[0], audio)

    def test_long_audio_produces_multiple_chunks(self):
        # 50s of audio, 25s chunks, 3s overlap → step = 22s → 3 chunks.
        audio = np.arange(int(50 * SR), dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        assert len(chunks) >= 2
        chunk_len = int(25.0 * SR)
        for chunk in chunks:
            assert len(chunk) <= chunk_len

    def test_successive_chunks_share_overlap(self):
        """Last ``overlap_duration`` samples of chunk[0] == first of chunk[1]."""
        audio = np.arange(int(50 * SR), dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        assert len(chunks) >= 2
        overlap_samples = int(3.0 * SR)
        np.testing.assert_array_equal(
            chunks[1][:overlap_samples],
            chunks[0][-overlap_samples:],
        )

    def test_last_chunk_reaches_end_of_audio(self):
        """
        The final sample of ``audio`` must appear in the last chunk.
        Regression guard for the word-drop bug pinned by
        """
        audio = np.arange(int(90 * SR), dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=30.0, overlap_duration=3.0)
        assert len(chunks) >= 2
        assert chunks[-1][-1] == audio[-1]

    def test_default_sample_rate_matches_whisper(self):
        """Omitting ``sample_rate`` defaults to :data:`WHISPER_SAMPLE_RATE`."""
        audio = np.arange(SR, dtype=np.float32)
        chunks_default = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        chunks_explicit = split_audio(
            audio,
            chunk_duration=25.0,
            overlap_duration=3.0,
            sample_rate=SR,
        )
        assert len(chunks_default) == len(chunks_explicit)
        for a, b in zip(chunks_default, chunks_explicit, strict=True):
            np.testing.assert_array_equal(a, b)

    def test_custom_sample_rate_scales_chunk_lengths(self):
        """``sample_rate`` controls samples-per-chunk independently of duration."""
        audio = np.arange(250, dtype=np.float32)  # 2.5s at 100 Hz
        chunks = split_audio(
            audio,
            chunk_duration=1.0,
            overlap_duration=0.1,
            sample_rate=100,
        )
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 100  # chunk_len = int(1.0 * 100)

    def test_chunks_are_views_not_copies(self):
        """Slices of ``audio`` are numpy views; mutating ``audio`` reflects."""
        audio = np.arange(int(50 * SR), dtype=np.float32)
        chunks = split_audio(audio, chunk_duration=25.0, overlap_duration=3.0)
        assert len(chunks) >= 1
        # Mutating the source array must reflect in the first chunk.
        original_first_sample = int(chunks[0][0])
        audio[0] = -999.0
        assert chunks[0][0] == -999.0
        # Restore for hygiene.
        audio[0] = original_first_sample


class TestSplitAudioDelegationFromEngines:
    """
    Engine ``_split_audio`` methods delegate to the shared helper.
    These tests pin the delegation contract: the engine methods must
    """

    def test_parakeet_delegates_to_split_audio(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        audio = np.arange(int(50 * SR), dtype=np.float32)
        chunks_method = ParakeetEngine._split_audio(None, audio, 25.0, 3.0)
        chunks_helper = split_audio(
            audio,
            chunk_duration=25.0,
            overlap_duration=3.0,
            sample_rate=SR,
        )
        assert len(chunks_method) == len(chunks_helper)
        for a, b in zip(chunks_method, chunks_helper, strict=True):
            np.testing.assert_array_equal(a, b)

    def test_qwen_delegates_to_split_audio(self):
        from voice_typer.server.qwen_engine import QwenEngine

        audio = np.arange(int(90 * SR), dtype=np.float32)
        # QwenEngine._split_audio is a @staticmethod.
        chunks_method = QwenEngine._split_audio(audio, 30.0, 3.0)
        chunks_helper = split_audio(
            audio,
            chunk_duration=30.0,
            overlap_duration=3.0,
            sample_rate=SR,
        )
        assert len(chunks_method) == len(chunks_helper)
        for a, b in zip(chunks_method, chunks_helper, strict=True):
            np.testing.assert_array_equal(a, b)


class TestRequireHuggingFaceConsent:
    """``_require_huggingface_consent`` raises the TYPED subclass."""

    def test_missing_consent_raises_typed_subclass_with_fields(self):
        from voice_typer.server.asr_errors import (
            ConsentRequiredError,
            HuggingFaceConsentRequiredError,
        )
        from voice_typer.server.asr_utils import _require_huggingface_consent

        cfg = type("Cfg", (), {"huggingface_consent": False})()

        with pytest.raises(ConsentRequiredError) as excinfo:
            _require_huggingface_consent(cfg, "tiny")
        # The typed subclass carries the wire fields the consent
        assert isinstance(excinfo.value, HuggingFaceConsentRequiredError)
        assert excinfo.value.provider == "huggingface"
        assert excinfo.value.scope == "download"

    def test_none_config_is_treated_as_not_consented(self):
        from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError
        from voice_typer.server.asr_utils import _require_huggingface_consent

        with pytest.raises(HuggingFaceConsentRequiredError):
            _require_huggingface_consent(None, "tiny")

    def test_consent_given_returns_silently(self):
        from voice_typer.server.asr_utils import _require_huggingface_consent

        cfg = type("Cfg", (), {"huggingface_consent": True})()

        # No raise, the caller proceeds with the download.
        assert _require_huggingface_consent(cfg, "tiny") is None
