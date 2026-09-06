"""Tests for the ER-88 resampling fallback anti-aliasing + warning fix.

These tests cover the linear-interp fallback path in
``voice_typer.server.recording.resampling.resample_audio`` (used when
``scipy.signal.resample_poly`` is unavailable). Pre-fix, the fallback
used pure ``np.interp`` with no anti-aliasing filter — when
DOWNSAMPLING (e.g. 48k→16k, 44.1k→16k), energy above the target
Nyquist (8 kHz) aliased into the speech band, silently degrading ASR
accuracy on the streaming partial-transcription path.

Post-fix, the fallback applies a short windowed-sinc FIR low-pass
filter at ``target_sr / 2`` BEFORE the linear-interp decimation, and
emits a one-time WARNING so the streaming path surfaces the quality
degradation (it normally suppresses per-call logging via
``log_resample=False``).

The tests are placed under the ``test_recording_controller_*`` namespace
because ``recording_controller`` is the primary consumer of the
resampling pipeline (via ``DictationPipeline``) and this sub-agent owns
that test-file pattern. The module under test lives in
``voice_typer.server.recording.resampling``.
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Mock sounddevice so importing recording.py is cheap and headless."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


@pytest.fixture(autouse=True)
def _reset_linear_interp_warning_flag():
    """Reset the one-time warning flag before each test so we can assert
    the warning fires on the first fallback invocation per test."""
    import voice_typer.server.recording.resampling as res_mod

    res_mod._linear_interp_warned = False
    yield
    res_mod._linear_interp_warned = False


def _force_linear_interp_fallback(monkeypatch):
    """Patch ``_get_resample_poly`` to raise ``ResampleUnavailableError``
    so the linear-interp fallback path is exercised deterministically."""
    import voice_typer.server.recording.resampling as res_mod
    from voice_typer.server.recording.exceptions import ResampleUnavailableError

    def raising_get_resample():
        raise ResampleUnavailableError("scipy not available for test")

    # Patch via the package namespace so the production code's
    # ``_recording_pkg._get_resample_poly()`` lookup picks up the patch
    # (same pattern as tests/test_recording.py::TestResampleFallback).
    monkeypatch.setattr(
        "voice_typer.server.recording.resampling._get_resample_poly",
        raising_get_resample,
    )
    return res_mod


# ────────────────────────────────────────────────────────────────────────────
# Test 1: downsampling via linear-interp fallback applies an anti-aliasing
# FIR filter — high-frequency energy above target_sr/2 is attenuated.
# ────────────────────────────────────────────────────────────────────────────
class TestLinearInterpAntialiasing:
    """ER-88: the no-scipy linear-interp fallback applies an anti-aliasing
    FIR low-pass filter at ``target_sr / 2`` before decimating, so energy
    above the target Nyquist does not alias into the speech band."""

    def test_downsampling_attenuates_above_target_nyquist(self, monkeypatch):
        """A 12 kHz sine (above 8 kHz target Nyquist) at 48 kHz must be
        strongly attenuated after 48k→16k linear-interp resampling.

        Pre-fix, np.interp aliases 12 kHz energy into the 0-8 kHz band,
        producing a strong aliased component. Post-fix, the anti-aliasing
        FIR low-passes at 8 kHz first, so the 12 kHz energy is suppressed.
        """
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        sr_in = 48000
        duration = 0.5  # 500 ms — long enough for the FIR transient to settle
        t = np.linspace(0, duration, int(sr_in * duration), endpoint=False)
        # 12 kHz sine — well above the 8 kHz target Nyquist.
        high_freq_audio = (np.sin(2 * np.pi * 12000 * t) * 0.5).astype(np.float32)

        result = resample_audio(high_freq_audio, sr_in, 16000, log_resample=False)

        # The result must be at ~16 kHz length.
        expected_len = int(len(high_freq_audio) * 16000 / sr_in)
        assert abs(len(result) - expected_len) <= 1, (
            f"Expected ~{expected_len} samples after 48k→16k resample, got {len(result)}"
        )
        # Skip the FIR transient (first/last 31 samples ≈ filter length).
        # The middle of the result should be near-zero (12 kHz attenuated
        # by the low-pass at 8 kHz). Allow some residual — the 31-tap FIR
        # has finite stop-band attenuation (~40 dB), so a small aliased
        # component is expected, but it must be MUCH smaller than the
        # 0.5-amplitude input.
        middle = result[100:-100]
        peak = float(np.max(np.abs(middle)))
        # Pre-fix (no anti-aliasing), peak would be ~0.5 (full aliasing).
        # Post-fix (FIR applied), peak should be < 0.1 (~ -14 dB attenuation
        # — well below the input amplitude).
        assert peak < 0.1, (
            f"ER-88: 12 kHz signal should be attenuated by the anti-aliasing "
            f"FIR (peak < 0.1); got peak={peak:.4f}. The linear-interp "
            f"fallback is likely missing the FIR pre-filter."
        )

    def test_upsampling_skips_antialiasing_filter(self, monkeypatch):
        """Upsampling (target_sr > effective_sr) must NOT apply the FIR —
        linear interp's natural (sin x / x) response already attenuates
        the upper half of the source band, so an additional FIR would
        needlessly attenuate legitimate signal.

        A 4 kHz sine at 16 kHz upsampled to 48 kHz should retain most of
        its amplitude (4 kHz is well below the 8 kHz source Nyquist).
        """
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        sr_in = 16000
        duration = 0.5
        t = np.linspace(0, duration, int(sr_in * duration), endpoint=False)
        # 4 kHz sine — well below the 8 kHz source Nyquist.
        low_freq_audio = (np.sin(2 * np.pi * 4000 * t) * 0.5).astype(np.float32)

        result = resample_audio(low_freq_audio, sr_in, 48000, log_resample=False)

        # Upsampling should preserve the signal (linear interp of a
        # 4 kHz sine at 16 kHz → 48 kHz retains ~0.5 amplitude after
        # the (sin x / x) response — close to 0.5 but slightly less).
        middle = result[100:-100]
        peak = float(np.max(np.abs(middle)))
        # The (sin x / x) response at 4 kHz/16 kHz = 0.25 normalized →
        # attenuation is small. Allow some slack.
        assert peak > 0.3, (
            f"ER-88: 4 kHz signal should be preserved on upsampling "
            f"(peak > 0.3 — no anti-aliasing filter applied); got "
            f"peak={peak:.4f}. The fallback may be mis-applying the FIR "
            f"on the upsampling path."
        )

    def test_antialias_fir_cache_returns_same_filter_for_same_ratio(self, monkeypatch):
        """The anti-aliasing FIR cache must return the same filter object
        for the same (effective_sr, target_sr) pair (memoization)."""
        res_mod = _force_linear_interp_fallback(monkeypatch)
        # Clear the cache to start fresh.
        res_mod._antialias_fir_cache.clear()

        fir1 = res_mod._get_antialias_fir(48000, 16000)
        fir2 = res_mod._get_antialias_fir(48000, 16000)
        assert fir1 is not None, "Downsampling FIR should be returned (not None)"
        assert fir2 is fir1, (
            "ER-88: cache must return the SAME filter object on the second "
            "call for the same (effective_sr, target_sr) pair (memoization)."
        )

    def test_antialias_fir_returns_none_for_upsampling(self, monkeypatch):
        """Upsampling / same-rate resampling returns ``None`` (no filter
        needed — linear interp's natural response suffices)."""
        res_mod = _force_linear_interp_fallback(monkeypatch)
        assert res_mod._get_antialias_fir(16000, 48000) is None, (
            "ER-88: upsampling must return None (no anti-aliasing needed)."
        )
        assert res_mod._get_antialias_fir(16000, 16000) is None, (
            "ER-88: same-rate resampling must return None (no anti-aliasing needed)."
        )

    def test_fir_normalized_dc_gain_is_one(self, monkeypatch):
        """The FIR's DC gain must be 1.0 so a constant (DC) signal passes
        through unchanged — prevents amplitude drift on silent/DC chunks."""
        res_mod = _force_linear_interp_fallback(monkeypatch)
        fir = res_mod._get_antialias_fir(48000, 16000)
        assert fir is not None
        dc_gain = float(fir.sum())
        assert abs(dc_gain - 1.0) < 1e-5, (
            f"ER-88: FIR DC gain must be 1.0 (got {dc_gain:.6f}). A non-unit "
            f"DC gain would attenuate or amplify DC/silent chunks."
        )


# ────────────────────────────────────────────────────────────────────────────
# Test 2: one-time WARNING is emitted on the first linear-interp fallback
# use, even when the caller passes log_resample=False (the streaming path).
# ────────────────────────────────────────────────────────────────────────────
class TestLinearInterpOneTimeWarning:
    """ER-88: a one-time WARNING is emitted on the first linear-interp
    fallback invocation, even when ``log_resample=False`` (the streaming
    partial-transcription path's default). Subsequent invocations are
    silent (avoids log spam at 16 Hz)."""

    def test_first_fallback_call_emits_warning_even_when_log_resample_false(self, monkeypatch, caplog):
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        audio = np.ones(4800, dtype=np.float32)
        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            # log_resample=False — simulates the streaming partial path
            # which suppresses per-call INFO logs.
            resample_audio(audio, 48000, 16000, log_resample=False)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("linear-interp resampling fallback" in m for m in warning_messages), (
            "ER-88: first linear-interp fallback call must emit a one-time "
            f"WARNING even with log_resample=False; got: {warning_messages}"
        )

    def test_second_fallback_call_does_not_repeat_warning(self, monkeypatch, caplog):
        """The one-time warning must NOT repeat on subsequent calls (the
        streaming path runs at ~16 Hz; spam would drown the log)."""
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        audio = np.ones(4800, dtype=np.float32)
        # First call: emits the warning (drain the caplog so we only see
        # the second call's records below).
        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            resample_audio(audio, 48000, 16000, log_resample=False)
        caplog.clear()

        # Second call: must NOT repeat the warning.
        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            resample_audio(audio, 48000, 16000, log_resample=False)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("linear-interp resampling fallback" in m for m in warning_messages), (
            f"ER-88: one-time warning must NOT repeat on the second call; got: {warning_messages}"
        )

    def test_warning_mentions_anti_aliasing_status(self, monkeypatch, caplog):
        """The warning message must indicate whether the anti-aliasing
        FIR was applied (downsampling) or not (upsampling/same-rate)."""
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        # Downsampling: FIR applied → warning should mention anti-aliasing.
        audio = np.ones(4800, dtype=np.float32)
        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            resample_audio(audio, 48000, 16000, log_resample=False)
        downsamp_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Anti-aliasing FIR applied" in m for m in downsamp_msgs), (
            f"ER-88: warning for downsampling must mention 'Anti-aliasing FIR applied'; got: {downsamp_msgs}"
        )


# ────────────────────────────────────────────────────────────────────────────
# Test 3: the existing length contract is preserved (anti-aliasing filter
# uses mode="same" so output length is unchanged).
# ────────────────────────────────────────────────────────────────────────────
class TestLengthContractPreserved:
    """ER-88: the anti-aliasing FIR uses ``np.convolve(., mode='same')``
    so the linear-interp path's output length is unchanged from pre-fix
    behavior. Existing callers (and tests) that depend on the
    ``int(len(input) * target_sr / effective_sr)`` length formula
    continue to hold."""

    def test_downsampled_length_matches_legacy_formula(self, monkeypatch):
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        for sr_in, sr_out in [(48000, 16000), (44100, 16000), (32000, 16000)]:
            audio = np.ones(int(sr_in * 0.1), dtype=np.float32)  # 100 ms
            result = resample_audio(audio, sr_in, sr_out, log_resample=False)
            expected_len = int(len(audio) * sr_out / sr_in)
            assert abs(len(result) - expected_len) <= 1, (
                f"ER-88: length contract broken for {sr_in}→{sr_out}: expected ~{expected_len}, got {len(result)}"
            )

    def test_empty_input_still_returns_empty(self, monkeypatch):
        """An empty input must return an empty output (no FIR shape error)."""
        _force_linear_interp_fallback(monkeypatch)
        from voice_typer.server.recording.resampling import resample_audio

        empty = np.array([], dtype=np.float32)
        result = resample_audio(empty, 48000, 16000, log_resample=False)
        assert len(result) == 0, (
            "ER-88: empty input must return empty output (the FIR guard "
            "`src_audio.size >= fir.size` skips convolution for tiny inputs)."
        )


# ────────────────────────────────────────────────────────────────────────────
# Test 4: the cached-FIR-taps fast path is numerically equivalent to
# ``resample_poly`` — exercised UNMOCKED (real scipy ``upfirdn``, real
# taps). The earlier tests mocked ``upfirdn``, which is exactly why the
# tuple-unpacking bug survived: every call raised ``ValueError`` inside
# the fast path and silently fell back to ``resample_poly``.
# ────────────────────────────────────────────────────────────────────────────
class TestCachedTapsFastPathNumericEquivalence:
    """The cached-taps path must run (no exception churn, no fallback)
    and produce output matching ``scipy.signal.resample_poly``.

    * Uncapped ratios (48k→16k: up=1, down=3): BIT-identical output —
      the cached design is scipy's design, and the shared
      ``_resample_via_cached_taps`` helper applies scipy's exact
      ``raw[n_pre_remove : n_pre_remove + n_out]`` trim.
    * Capped "ugly" ratios (44.1k→16k): same length + finite + strongly
      correlated — the intentionally shorter FIR trades transition-band
      width for ~30× fewer MACs, so exact equality does not hold by
      design.
    """

    def test_fast_path_bit_matches_resample_poly_at_48k(self):
        """48k→16k float32: fast path output is bit-identical to resample_poly."""
        import math

        from scipy.signal import resample_poly
        from voice_typer.server.recording.resampling import resample_audio

        sr_in, sr_out = 48000, 16000
        g = math.gcd(sr_in, sr_out)
        up, down = sr_out // g, sr_in // g
        n = 512  # the production chunk size
        t = np.arange(n, dtype=np.float32) / sr_in
        audio = (0.4 * np.sin(2 * np.pi * 220.0 * t) + 0.05 * np.sin(2 * np.pi * 3.0 * t)).astype(np.float32)

        reference = resample_poly(audio, up, down)
        result = resample_audio(audio.copy(), sr_in, sr_out, log_resample=False)

        assert len(result) == len(reference) == -(-n * up // down), (
            f"fast path output length {len(result)} != resample_poly length {len(reference)}"
        )
        assert result.dtype == np.float32
        assert np.all(np.isfinite(result))
        np.testing.assert_array_equal(
            result,
            reference.astype(np.float32),
            err_msg="cached-taps fast path must be bit-identical to resample_poly at uncapped ratios",
        )

    def test_fast_path_runs_without_fallback(self, monkeypatch):
        """The fast path must not raise-and-fall-back on every call.

        ``_get_resample_poly`` is patched to a function whose RESULT
        raises if invoked: if the ``upfirdn`` fast path worked, the
        fallback is never touched and the resample succeeds; if the
        old tuple bug were present (``ValueError`` on every call), the
        fallback would run and blow up the test.
        """
        from voice_typer.server.recording import resampling

        def _boom(*args, **kwargs):
            raise AssertionError("resample_poly fallback was invoked — fast path failed")

        monkeypatch.setattr(resampling, "_get_resample_poly", lambda: _boom)
        audio = np.sin(2 * np.pi * 220.0 * np.arange(512) / 48000).astype(np.float32)
        result = resampling.resample_audio(audio, 48000, 16000, log_resample=False)
        assert len(result) == 171  # ceil(512 * 1 / 3)
        assert np.all(np.isfinite(result))

    def test_upfirdn_receives_unpacked_taps_array(self, monkeypatch):
        """Spy on the real ``upfirdn``: its first positional argument
        must be the taps ARRAY, not the 3-tuple (the original defect)."""
        from voice_typer.server.recording import resampling

        calls: list[object] = []
        # Wrap the real scipy upfirdn via the production import path.
        import scipy.signal

        original_upfirdn = scipy.signal.upfirdn

        def spying_upfirdn(h, x, *args, **kwargs):
            calls.append(h)
            return original_upfirdn(h, x, *args, **kwargs)

        monkeypatch.setattr(scipy.signal, "upfirdn", spying_upfirdn)
        audio = np.ones(512, dtype=np.float32)
        resampling.resample_audio(audio, 48000, 16000, log_resample=False)

        assert len(calls) == 1, f"expected exactly one upfirdn call, got {len(calls)}"
        taps_arg = calls[0]
        assert isinstance(taps_arg, np.ndarray), (
            f"upfirdn must receive the unpacked taps ndarray, got {type(taps_arg)!r}"
        )

    def test_capped_ratio_44k1_same_length_and_correlated(self):
        """44.1k→16k (capped half_len): same length as resample_poly,
        finite, and strongly correlated (intentional design deviation,
        not a bug)."""
        import math

        from scipy.signal import resample_poly
        from voice_typer.server.recording.resampling import resample_audio

        sr_in, sr_out = 44100, 16000
        g = math.gcd(sr_in, sr_out)
        up, down = sr_out // g, sr_in // g
        n = 512
        t = np.arange(n, dtype=np.float64) / sr_in
        audio = (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)

        reference = resample_poly(audio, up, down)
        result = resample_audio(audio.copy(), sr_in, sr_out, log_resample=False)

        assert len(result) == len(reference) == -(-n * up // down)
        assert np.all(np.isfinite(result))
        corr = float(np.corrcoef(reference.astype(np.float64), result.astype(np.float64))[0, 1])
        assert corr > 0.95, f"capped-ratio output diverged from resample_poly (corr={corr:.4f})"
