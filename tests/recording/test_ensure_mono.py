"""Focused tests for the ``_ensure_mono`` downmix contract.

Pins the allocation/no-copy behavior of
:func:`voice_typer.server.recording.format.ensure_mono` (exercised
through the ``Recorder._ensure_mono`` delegator):

- the stereo (2-channel) fast path returns a FRESH caller-owned array
  (exactly one clear output allocation, aliased to nothing — neither
  the per-thread scratch nor the input), with output bytes identical
  to the ``(L+R) / 2`` element-wise computation the scratch+copy
  revision produced;
- the already-mono paths are genuinely no-copy (1-D passthrough
  returns the input object; a 2-D single-column input returns a
  zero-copy ``reshape(-1)`` view of it);
- the rare >2-channel fallback stays correct and independent.

The presence/type of the ``_mono_scratch_local`` holder in
``Recorder.__init__`` is pinned by
``tests/test_recorder_mono_and_disconnect_fixes.py`` (downmix
correctness + independence + thread isolation are covered there too);
this file owns the no-copy / ownership contract.
"""

from __future__ import annotations

import numpy as np

from tests.fixtures.recorder_test_helpers import make_recorder


class TestEnsureMonoNoCopyContract:
    """The downmix returns either a view of the caller's input or one
    fresh output allocation — never a shared scratch alias."""

    def test_stereo_downmix_returns_caller_owned_array(self):
        """The stereo result must own its storage (``base is None``),
        so storing it in ``_buffer`` / ``_preroll_buffer`` cannot alias
        any reusable scratch or the input chunk."""
        r = make_recorder()
        audio = np.array([[1.0, 3.0], [2.0, 4.0], [5.0, 7.0]], dtype=np.float32)
        result = r._ensure_mono(audio)
        assert result is not audio
        assert result.base is None, "stereo downmix must return a fresh caller-owned array"
        assert result.flags.owndata

    def test_stereo_downmix_bytes_identical_to_elementwise_formula(self):
        """The output must be bit-identical to the element-wise
        ``(L+R) * 0.5`` float32 computation (the same operations the
        scratch+``view.copy()`` revision performed, in the same
        order)."""
        r = make_recorder()
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal((512, 2)) * 0.5).astype(np.float32)
        result = r._ensure_mono(audio)
        expected = np.add(audio[:, 0], audio[:, 1], out=np.empty(512, dtype=np.float32))
        expected *= 0.5
        # assert_array_equal (not allclose): the operations are
        # identical, so the bytes must match exactly.
        np.testing.assert_array_equal(result, expected)
        assert result.dtype == np.float32

    def test_stereo_downmix_matches_np_mean(self):
        """Correctness cross-check against ``np.mean`` (float32)."""
        r = make_recorder()
        audio = np.array([[1.0, 3.0], [2.0, 4.0], [5.0, 7.0]], dtype=np.float32)
        result = r._ensure_mono(audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)

    def test_1d_input_passthrough_is_same_object(self):
        """Already-mono 1-D input is returned as-is (genuinely no copy)."""
        r = make_recorder()
        audio = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = r._ensure_mono(audio)
        assert result is audio

    def test_single_column_2d_input_is_zero_copy_view(self):
        """A 2-D single-column input is reshaped to 1-D as a VIEW of
        the input (no copy) — the other already-mono path."""
        r = make_recorder()
        audio = np.array([[1.0], [2.0], [3.0]], dtype=np.float32)
        result = r._ensure_mono(audio)
        np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0], dtype=np.float32))
        assert result.base is audio, "(n,1) reshape must be a zero-copy view of the input"

    def test_multi_channel_fallback_correct_and_independent(self):
        """>2-channel input falls back to ``np.mean`` and returns an
        array that does not alias the input."""
        r = make_recorder()
        audio = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        result = r._ensure_mono(audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)
        assert result is not audio

    def test_successive_stereo_calls_do_not_share_storage(self):
        """Each stereo call must get its own output storage — no
        result may alias a previous call's result."""
        r = make_recorder()
        results = []
        for i in range(10):
            audio = np.full((64, 2), float(i), dtype=np.float32)
            results.append(r._ensure_mono(audio))
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
