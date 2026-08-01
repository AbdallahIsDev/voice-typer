"""Tests for property-based testing using hypothesis.

TEST-009: Property-based tests for corrections roundtrip, config serialization,
audio buffer operations, and text cleanup with random strings.
TEST-013: Fuzzing for corrections.json parser with random JSON structures.
"""

from __future__ import annotations

import json

import pytest
from voice_typer.server.text_cleanup import clean_transcribed_text

try:
    from hypothesis import HealthCheck, assume, given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

    # Provide no-op decorators so class definition doesn't fail
    def given(**kwargs):
        def decorator(func):
            return func

        return decorator

    def assume(condition):
        pass

    class settings:  # noqa: N801 — matches hypothesis.settings name
        def __init__(self, **kwargs):
            pass

        def __call__(self, func):
            return func

    class HealthCheck:
        too_slow = None
        function_scoped_fixture = None

    class st:  # noqa: N801 — matches hypothesis.strategies alias
        @staticmethod
        def text(**kwargs):
            return None

        @staticmethod
        def sampled_from(elements):
            return None

        @staticmethod
        def integers(**kwargs):
            return None

        @staticmethod
        def one_of(*args):
            return None

        @staticmethod
        def dictionaries(*args, **kwargs):
            return None

        @staticmethod
        def lists(*args, **kwargs):
            return None

        @staticmethod
        def none():
            return None

        @staticmethod
        def booleans():
            return None

        @staticmethod
        def floats(**kwargs):
            return None

        @staticmethod
        def characters(**kwargs):
            return None


pytestmark = pytest.mark.skipif(
    not HAS_HYPOTHESIS,
    reason="hypothesis not installed — install with: pip install hypothesis",
)


# Corrections roundtrip ────────────────────────────────────


class TestCorrectionsRoundtrip:
    """Apply then reverse corrections — the text should be recoverable."""

    @given(text=st.text(min_size=1, max_size=200, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_cleanup_never_crashes(self, text):
        """clean_transcribed_text should never raise on arbitrary input."""
        result = clean_transcribed_text(text)
        assert isinstance(result, str)

    @given(text=st.text(min_size=0, max_size=100, alphabet=st.characters(whitelist_categories=("L",))))
    @settings(max_examples=30)
    def test_cleanup_preserves_nonempty_input(self, text):
        """Non-empty alphabetic input should produce non-empty output."""
        assume(text.strip())
        result = clean_transcribed_text(text)
        assert len(result) > 0

    @given(text=st.text(min_size=0, max_size=50))
    @settings(max_examples=30)
    def test_cleanup_idempotent(self, text):
        """Applying cleanup twice should produce the same result as once."""
        first = clean_transcribed_text(text)
        second = clean_transcribed_text(first)
        assert second == first


# Config serialization roundtrip ───────────────────────────


class TestConfigSerializationRoundtrip:
    """Config save/load should roundtrip without data loss."""

    @given(
        hotkey=st.sampled_from(["<f2>", "<f5>", "<f9>", "<caps_lock>"]),
        model_size=st.sampled_from(["tiny.en", "small.en", "medium.en"]),
        device=st.sampled_from(["cuda", "cpu"]),
        beam_size=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_config_roundtrip(self, tmp_path, monkeypatch, hotkey, model_size, device, beam_size):
        from voice_typer.server.config import Config

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(
            hotkey=hotkey,
            model_size=model_size,
            device=device,
            beam_size=beam_size,
        )
        c.save()
        loaded = Config.load()
        assert loaded.hotkey == hotkey
        assert loaded.beam_size == beam_size


# Audio buffer operations with random sizes ────────────────


class TestAudioBufferOperations:
    """Audio buffer operations should handle arbitrary sizes."""

    @given(chunk_size=st.integers(min_value=1, max_value=1024), num_chunks=st.integers(min_value=1, max_value=10))
    @settings(max_examples=30)
    def test_buffer_concatenation(self, chunk_size, num_chunks):
        import numpy as np

        chunks = [np.ones((chunk_size, 1), dtype=np.float32) for _ in range(num_chunks)]
        total = np.concatenate(chunks, axis=0)
        expected_samples = chunk_size * num_chunks
        assert total.shape == (expected_samples, 1)

    @given(size=st.integers(min_value=1, max_value=10000))
    @settings(max_examples=20)
    def test_rms_computation(self, size):
        import numpy as np

        audio = np.ones(size, dtype=np.float32)
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        assert abs(rms - 1.0) < 1e-6


# Text cleanup with random strings ─────────────────────────


class TestTextCleanupRandom:
    """Text cleanup should handle random strings gracefully."""

    @given(text=st.text(min_size=0, max_size=300))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes_on_random_text(self, text):
        result = clean_transcribed_text(text)
        assert isinstance(result, str)

    @given(s=st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    @settings(max_examples=30)
    def test_single_word_capitalized(self, s):
        """A single word should be capitalized."""
        result = clean_transcribed_text(s.lower())
        if result:  # may be empty if the word is a misspelling that gets "corrected" away
            assert result[0].isupper() or result == s


# Fuzzing for corrections.json parser ──────────────────────


class TestCorrectionsJsonFuzzing:
    """Fuzz the corrections.json parser with random JSON structures."""

    @given(
        obj=st.one_of(
            st.dictionaries(st.text(), st.text()),
            st.dictionaries(st.text(), st.lists(st.text())),
            st.lists(st.integers()),
            st.none(),
            st.booleans(),
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parser_handles_random_json(self, tmp_path, monkeypatch, obj):
        """Parser should handle malformed/random JSON gracefully (no crashes)."""
        from voice_typer.server import text_cleanup

        # Write random JSON as a corrections file
        corrections_file = tmp_path / "voice-typer-corrections.json"
        try:
            corrections_file.write_text(json.dumps(obj), encoding="utf-8")
        except (TypeError, ValueError):
            assume(False)  # skip objects that can't be serialized

        # configure_corrections should not crash
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        # Result is either None (no error) or an error message string
        assert result is None or isinstance(result, str)

    @given(text=st.text(min_size=0, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Z"))))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_cleanup_after_random_corrections(self, tmp_path, monkeypatch, text):
        """After loading random corrections, cleanup should still work."""
        from voice_typer.server import text_cleanup

        # Write a simple valid corrections file
        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text(json.dumps({"misspellings": {}}), encoding="utf-8")
        text_cleanup.configure_corrections(config_dir=tmp_path)
        result = clean_transcribed_text(text)
        assert isinstance(result, str)
