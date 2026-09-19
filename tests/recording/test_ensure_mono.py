"""Focused tests for the ``_ensure_mono`` downmix contract."""

from __future__ import annotations

import numpy as np
from voice_typer.server.recording.format import ensure_mono

from tests.fixtures.recorder_test_helpers import make_recorder


class TestEnsureMonoNoCopyContract:
    """The downmix returns either a view of the caller's input or one"""

    def test_stereo_downmix_returns_caller_owned_array(self):
        """The stereo result must own its storage (``base is None``),"""
        r = make_recorder()
        audio = np.array([[1.0, 3.0], [2.0, 4.0], [5.0, 7.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        assert result is not audio
        assert result.base is None, "stereo downmix must return a fresh caller-owned array"
        assert result.flags.owndata

    def test_stereo_downmix_bytes_identical_to_elementwise_formula(self):
        """The output must be bit-identical to the element-wise"""
        r = make_recorder()
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal((512, 2)) * 0.5).astype(np.float32)
        result = ensure_mono(r, audio)
        expected = np.add(audio[:, 0], audio[:, 1], out=np.empty(512, dtype=np.float32))
        expected *= 0.5
        np.testing.assert_array_equal(result, expected)
        assert result.dtype == np.float32

    def test_stereo_downmix_matches_np_mean(self):
        """Correctness cross-check against ``np.mean`` (float32)."""
        r = make_recorder()
        audio = np.array([[1.0, 3.0], [2.0, 4.0], [5.0, 7.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)

    def test_1d_input_passthrough_is_same_object(self):
        """Already-mono 1-D input is returned as-is (genuinely no copy)."""
        r = make_recorder()
        audio = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = ensure_mono(r, audio)
        assert result is audio

    def test_single_column_2d_input_is_zero_copy_view(self):
        """A 2-D single-column input is reshaped to 1-D as a VIEW of"""
        r = make_recorder()
        audio = np.array([[1.0], [2.0], [3.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0], dtype=np.float32))
        assert result.base is audio, "(n,1) reshape must be a zero-copy view of the input"

    def test_multi_channel_fallback_correct_and_independent(self):
        """>2-channel input falls back to ``np.mean`` and returns an"""
        r = make_recorder()
        audio = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)
        assert result is not audio

    def test_successive_stereo_calls_do_not_share_storage(self):
        """Each stereo call must get its own output storage, no"""
        r = make_recorder()
        results = []
        for i in range(10):
            audio = np.full((64, 2), float(i), dtype=np.float32)
            results.append(ensure_mono(r, audio))
        for i, res in enumerate(results):
            assert np.all(res == float(i)), f"result {i} corrupted by a later call: {res}"
        # Distinct storages (no two results share a base buffer).
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                assert results[i] is not results[j]
                a = results[i]
                b = results[j]
                if a.base is not None and b.base is not None:
                    assert a.base is not b.base, f"results {i} and {j} share backing storage"
