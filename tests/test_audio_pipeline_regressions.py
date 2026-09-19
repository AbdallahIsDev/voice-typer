"""regression tests for findings #53, #108, #245."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest


def _install_fake_gtcrn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap ``GtcrnBackend`` for a lightweight fake (happy path)."""

    class _FakeGtcrnBackend:
        def process_hop(self, hop, caches=None):
            return np.asarray(hop, dtype=np.float32) * 0.5, ()

        def reset(self) -> None:
            pass

    from voice_typer.server.audio_filters import gtcrn_backend as _gtcrn_module

    monkeypatch.setattr(_gtcrn_module, "GtcrnBackend", _FakeGtcrnBackend)


def _install_failing_gtcrn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap ``GtcrnBackend`` for one whose constructor always raises."""

    class _FailingGtcrnBackend:
        def __init__(self) -> None:
            raise RuntimeError("gtcrn model unavailable in test")

    from voice_typer.server.audio_filters import gtcrn_backend as _gtcrn_module

    monkeypatch.setattr(_gtcrn_module, "GtcrnBackend", _FailingGtcrnBackend)


def _install_fake_pyrnnoise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a stub ``pyrnnoise`` module so ``_init_rnnoise``'s import succeeds."""
    fake_pyrnnoise = types.ModuleType("pyrnnoise")

    class _FakeRNNoise:
        def __init__(self, sample_rate: int = 48000) -> None:
            self.sample_rate = sample_rate
            self.channels = 1

        def denoise_frame(self, frame_i16):  # noqa: ANN001
            # Return (speech_prob, cleaned_i16), passthrough for testing.
            return (0.95, frame_i16)

    fake_pyrnnoise.RNNoise = _FakeRNNoise
    monkeypatch.setitem(sys.modules, "pyrnnoise", fake_pyrnnoise)


def _remove_module(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Simulate ``name`` not being installed by deleting it from ``sys.modules``."""
    # Save the current state so the auto-undo restores it.
    monkeypatch.setitem(sys.modules, name, None)


class TestGtcrnInitFallback:
    """A failed GTCRN init must fall back to rnnoise at __init__."""

    def test_gtcrn_available_not_degraded(self, monkeypatch: pytest.MonkeyPatch):
        """Backend loads → method stays gtcrn, NOT degraded, backend live."""
        _install_fake_gtcrn(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="gtcrn", sample_rate=16000)

        # Critical assertion: a healthy GTCRN init keeps the selected
        assert ns._method == "gtcrn", f"a successful GTCRN init must keep method='gtcrn'; got {ns._method!r}"
        assert ns._backend is not None, "the GTCRN backend instance must be kept"
        assert ns.is_degraded is False, (
            "is_degraded must be False when the GTCRN backend loads, the UI "
            "must not warn users that they're on a fallback they aren't on"
        )
        assert ns.degraded_reason == ""
        assert ns.latency_ms == pytest.approx(16.0)

    def test_gtcrn_failure_rnnoise_installed_falls_back_at_init(self, monkeypatch: pytest.MonkeyPatch):
        """Backend fails, RNNoise available → method=rnnoise, degraded=True, gtcrn reason."""
        _install_failing_gtcrn(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="gtcrn", sample_rate=16000)

        # Critical assertion: method is narrowed to rnnoise at __init__
        assert ns._method == "rnnoise", (
            f"a failed GTCRN init must fall back to rnnoise at __init__; got _method={ns._method!r}"
        )
        assert ns._backend is not None, "rnnoise backend must be initialized"
        assert ns.is_degraded is True, (
            "is_degraded must be True at __init__ so the UI can warn the user "
            "that the GTCRN model couldn't load (currently falls back to rnnoise)"
        )
        assert "gtcrn" in ns.degraded_reason.lower(), f"degraded_reason must mention gtcrn; got {ns.degraded_reason!r}"
        assert "rnnoise" in ns.degraded_reason.lower(), (
            f"degraded_reason must mention the rnnoise fallback; got {ns.degraded_reason!r}"
        )

    def test_gtcrn_failure_rnnoise_missing_degrades_to_none(self, monkeypatch: pytest.MonkeyPatch):
        """GTCRN fails AND rnnoise missing → method=none, degraded=True,"""
        _install_failing_gtcrn(monkeypatch)
        _remove_module(monkeypatch, "pyrnnoise")

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="gtcrn", sample_rate=16000)

        assert ns._method == "none", (
            "when both gtcrn (failed to load) and rnnoise (missing) are unavailable, method must degrade to 'none'"
        )
        assert ns.is_degraded is True
        reason = ns.degraded_reason.lower()
        assert "gtcrn" in reason, f"degraded_reason must mention gtcrn context; got {ns.degraded_reason!r}"
        assert "rnnoise" in reason, f"degraded_reason must mention rnnoise fallback failure; got {ns.degraded_reason!r}"

    def test_rnnoise_directly_not_degraded_when_installed(self, monkeypatch: pytest.MonkeyPatch):
        """Sanity: rnnoise alone (no gtcrn involvement) is NOT degraded."""
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="rnnoise", sample_rate=16000)

        assert ns._method == "rnnoise"
        assert ns._backend is not None
        assert ns.is_degraded is False, (
            "rnnoise with pyrnnoise installed must NOT be degraded, only a failed gtcrn init triggers the degraded flag"
        )
        assert ns.degraded_reason == ""

    def test_none_method_not_degraded(self, monkeypatch: pytest.MonkeyPatch):
        """Sanity: 'none' method is explicit passthrough, not degraded."""
        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="none", sample_rate=16000)
        assert ns._method == "none"
        assert ns.is_degraded is False
        assert ns.degraded_reason == ""

    def test_gtcrn_failure_process_does_not_silently_passthrough(self, monkeypatch: pytest.MonkeyPatch):
        """Critical regression: process() must NOT return the input unchanged"""
        _install_failing_gtcrn(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE
        from voice_typer.server.audio_filters.noise_suppressor import (
            _RNNOISE_FRAME_SIZE,
            NoiseSuppressor,
        )

        ns = NoiseSuppressor(method="gtcrn", sample_rate=RNNOISE_SAMPLE_RATE)
        assert ns._method == "rnnoise"  # narrowed at __init__

        # Feed a full rnnoise frame at the native 48kHz rate so no
        t = np.linspace(0, 1.0, _RNNOISE_FRAME_SIZE, endpoint=False, dtype=np.float32)
        audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        result = ns.process(audio, RNNOISE_SAMPLE_RATE)

        assert result is not None, "process() must return audio (not None)"
        assert result.shape == audio.shape
        assert ns._method == "rnnoise", (
            "process() must not mutate _method away from rnnoise (the init-time fallback should be sticky)"
        )
        assert ns.is_degraded is True, (
            "is_degraded must remain True after process(), the GTCRN init "
            "failure is a permanent degradation, not a one-time signal"
        )

    def test_noisy_room_preset_selects_gtcrn(self, monkeypatch: pytest.MonkeyPatch):
        """End-to-end: the ``noisy_room`` preset picks the live GTCRN"""
        _install_fake_gtcrn(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor
        from voice_typer.server.audio_presets import PRESET_NOISY_ROOM, get_preset_filters

        preset_filters = get_preset_filters(PRESET_NOISY_ROOM)
        assert preset_filters["noise_suppression_method"] == "gtcrn"

        ns = NoiseSuppressor(
            method=preset_filters["noise_suppression_method"],
            sample_rate=16000,
        )
        # Critical assertion: the suppressor runs the selected backend.
        assert ns.is_degraded is False, (
            "the noisy_room preset selects the bundled GTCRN model; a healthy init must not be marked degraded"
        )
        assert ns._method == "gtcrn", "the noisy_room preset must actually run GTCRN (not a fallback)"


class _FakeConfig:
    """Minimal config object for AudioProcessor tests, mirrors the"""

    def __init__(self, **kwargs):
        self.audio_preset = "custom"
        self.noise_filter_highpass = True
        self.noise_filter_highpass_cutoff_hz = 80.0
        self.noise_filter_gate = True
        self.noise_filter_gate_open_threshold_db = -26.0
        self.noise_filter_gate_close_threshold_db = -32.0
        self.noise_filter_gate_attack_ms = 25.0
        self.noise_filter_gate_hold_ms = 200.0
        self.noise_filter_gate_release_ms = 150.0
        # "none" avoids pulling optional native libs in tests.
        self.noise_suppression_method = "none"
        self.noise_filter_eq = True
        self.noise_filter_eq_low_db = -3.0
        self.noise_filter_eq_mid_db = 3.0
        self.noise_filter_eq_high_db = 2.0
        self.noise_filter_compressor = True
        self.noise_filter_compressor_threshold_db = -18.0
        self.noise_filter_compressor_ratio = 3.0
        self.noise_filter_compressor_attack_ms = 6.0
        self.noise_filter_compressor_release_ms = 60.0
        self.noise_filter_compressor_output_gain_db = 0.0
        self.noise_filter_limiter = True
        self.noise_filter_limiter_ceiling_db = -6.0
        self.noise_filter_limiter_release_ms = 60.0
        self.noise_filter_notch = False
        self.noise_filter_notch_frequency_hz = 0.0
        self.sample_rate = 16000
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestResampleFallbackDegraded:
    """H-22 (High): resample fallback must surface ``is_degraded``."""

    def test_no_resample_when_rates_match(self):
        """Sanity: when input_sr == chain_sr, is_degraded stays False."""
        from voice_typer.server.audio_processor import AudioProcessor

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3
        p.process_chunk(audio, input_sample_rate=16000)
        assert p.is_degraded is False, "no resample needed → no resample-degraded flag"
        assert p.degraded_reasons == []

    def test_resample_success_does_not_degrade(self):
        """Sanity: when scipy is available and resample succeeds, no degraded."""
        pytest.importorskip("scipy.signal")
        from voice_typer.server.audio_processor import AudioProcessor

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        audio = (np.random.randn(4800).astype(np.float32)) * 0.3
        result = p.process_chunk(audio, input_sample_rate=48000)
        assert result is not None
        assert p.is_degraded is False, "successful resample must NOT set the degraded flag"

    def test_resample_failure_sets_degraded(self, monkeypatch: pytest.MonkeyPatch):
        """H-22: when resample_poly raises, is_degraded becomes True and"""
        from voice_typer.server import audio_processor as ap_module
        from voice_typer.server.audio_processor import AudioProcessor

        def _raise_runtime_error():
            raise RuntimeError("simulated scipy missing")

        monkeypatch.setattr(ap_module, "_get_resample_poly_fn", lambda: _raise_runtime_error)

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        assert p.is_degraded is False, "freshly constructed processor must not be degraded"

        # Feed a chunk at a different rate to trigger the resample path.
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3
        result = p.process_chunk(audio, input_sample_rate=48000)

        assert result is not None, "process_chunk must return audio even on resample failure"
        assert result.shape == audio.shape

        # Critical assertion: the degraded flag is now set.
        assert p.is_degraded is True, (
            "H-22: resample failure must set is_degraded so the UI can warn "
            "the user that filters are mistuned (wrong rate)"
        )
        # The degraded reason must mention both sample rates so the user
        reasons = p.degraded_reasons
        assert len(reasons) >= 1, "degraded_reasons must include the resample reason"
        resample_reason = next(
            (r for r in reasons if "resample" in r.lower()),
            None,
        )
        assert resample_reason is not None, f"degraded_reasons must include a resample-related reason; got {reasons!r}"
        assert "48000" in resample_reason, f"reason must mention input_sr=48000; got {resample_reason!r}"
        assert "16000" in resample_reason, f"reason must mention chain_sr=16000; got {resample_reason!r}"

    def test_resample_degraded_flag_is_latched(self, monkeypatch: pytest.MonkeyPatch):
        """H-22: once set, the flag stays set across subsequent chunks"""
        from voice_typer.server import audio_processor as ap_module
        from voice_typer.server.audio_processor import AudioProcessor

        def _raise_runtime_error():
            raise RuntimeError("simulated scipy missing")

        monkeypatch.setattr(ap_module, "_get_resample_poly_fn", lambda: _raise_runtime_error)

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3

        # First chunk fails resample → degraded=True.
        p.process_chunk(audio, input_sample_rate=48000)
        assert p.is_degraded is True

        # Second chunk at the SAME mismatched rate → still degraded.
        p.process_chunk(audio, input_sample_rate=48000)
        assert p.is_degraded is True

        # Third chunk at the correct rate → STILL degraded (latched).
        p.process_chunk(audio, input_sample_rate=16000)
        assert p.is_degraded is True, (
            "resample-degraded flag is latched, stays set until reset() or set_sample_rate() (the corrective action)"
        )

    def test_reset_clears_resample_degraded_flag(self, monkeypatch: pytest.MonkeyPatch):
        """H-22: reset() clears the flag (new recording session = clean slate)."""
        from voice_typer.server import audio_processor as ap_module
        from voice_typer.server.audio_processor import AudioProcessor

        def _raise_runtime_error():
            raise RuntimeError("simulated scipy missing")

        monkeypatch.setattr(ap_module, "_get_resample_poly_fn", lambda: _raise_runtime_error)

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3
        p.process_chunk(audio, input_sample_rate=48000)
        assert p.is_degraded is True

        p.reset()
        assert p.is_degraded is False, (
            "reset() must clear the resample-degraded flag, a new recording session starts with a clean slate"
        )
        assert p.degraded_reasons == []

    def test_set_sample_rate_clears_resample_degraded_flag(self, monkeypatch: pytest.MonkeyPatch):
        """H-22: set_sample_rate() clears the flag (the corrective action)."""
        from voice_typer.server import audio_processor as ap_module
        from voice_typer.server.audio_processor import AudioProcessor

        def _raise_runtime_error():
            raise RuntimeError("simulated scipy missing")

        monkeypatch.setattr(ap_module, "_get_resample_poly_fn", lambda: _raise_runtime_error)

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3
        p.process_chunk(audio, input_sample_rate=48000)
        assert p.is_degraded is True

        # The corrective action: retune the chain to the input rate.
        p.set_sample_rate(48000)
        assert p.is_degraded is False, (
            "set_sample_rate() must clear the resample-degraded flag, the "
            "chain is now tuned to the input rate, so the resample path "
            "is no longer taken"
        )
        assert p.degraded_reasons == []


class TestVectorizedDynamicsFilters:
    """the 4 dynamics filters (equalizer, compressor,"""

    @pytest.fixture
    def small_audio(self) -> np.ndarray:
        """Small mocked audio array (256 samples of noise) for fast tests."""
        return (np.random.randn(256).astype(np.float32)) * 0.3

    def test_equalizer_uses_lfilter_not_per_sample_loop(self, small_audio, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("scipy.signal")
        from voice_typer.server.audio_filters.equalizer import Equalizer

        eq = Equalizer(low_db=-3.0, mid_db=3.0, high_db=2.0, sample_rate=16000)

        # Count lfilter invocations during process().
        from scipy.signal import lfilter

        call_count = {"n": 0}
        original_lfilter = lfilter

        def counting_lfilter(*args, **kwargs):
            call_count["n"] += 1
            return original_lfilter(*args, **kwargs)

        import scipy.signal as sig

        monkeypatch.setattr(sig, "lfilter", counting_lfilter)

        from voice_typer.server.audio_filters import base as _af_base

        monkeypatch.setattr(_af_base, "_lfilter", None)

        result = eq.process(small_audio, 16000)

        assert result is not None
        assert result.shape == small_audio.shape
        # The vectorized EQ uses 2 lfilter calls (one for low band,
        assert call_count["n"] == 2, (
            f"vectorized EQ must use exactly 2 lfilter calls; got {call_count['n']} "
            "(if this drops to 0, the vectorization was reverted to a per-sample loop)"
        )

    def test_compressor_uses_lfilter_not_per_sample_loop(self, small_audio, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("scipy.signal")
        from voice_typer.server.audio_filters.compressor import Compressor

        comp = Compressor(
            threshold_db=-18.0,
            ratio=3.0,
            attack_ms=6.0,
            release_ms=60.0,
            sample_rate=16000,
        )

        from scipy.signal import lfilter

        call_count = {"n": 0}
        original_lfilter = lfilter

        def counting_lfilter(*args, **kwargs):
            call_count["n"] += 1
            return original_lfilter(*args, **kwargs)

        import scipy.signal as sig

        monkeypatch.setattr(sig, "lfilter", counting_lfilter)

        from voice_typer.server.audio_filters import base as _af_base

        monkeypatch.setattr(_af_base, "_lfilter", None)

        result = comp.process(small_audio, 16000)

        assert result is not None
        assert result.shape == small_audio.shape
        # The vectorized compressor uses 2 lfilter calls (attack env +
        assert call_count["n"] == 2, f"vectorized Compressor must use exactly 2 lfilter calls; got {call_count['n']}"

    def test_limiter_uses_lfilter_not_per_sample_loop(self, small_audio, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("scipy.signal")
        from voice_typer.server.audio_filters.limiter import Limiter

        lim = Limiter(ceiling_db=-6.0, release_ms=60.0, sample_rate=16000)

        from scipy.signal import lfilter

        call_count = {"n": 0}
        original_lfilter = lfilter

        def counting_lfilter(*args, **kwargs):
            call_count["n"] += 1
            return original_lfilter(*args, **kwargs)

        import scipy.signal as sig

        monkeypatch.setattr(sig, "lfilter", counting_lfilter)

        from voice_typer.server.audio_filters import base as _af_base

        monkeypatch.setattr(_af_base, "_lfilter", None)

        result = lim.process(small_audio, 16000)

        assert result is not None
        assert result.shape == small_audio.shape
        # The vectorized limiter uses 2 lfilter calls (attack env +
        assert call_count["n"] == 2, f"vectorized Limiter must use exactly 2 lfilter calls; got {call_count['n']}"

    def test_noise_gate_uses_maximum_accumulate_for_peak_hold(self, small_audio):
        """the noise gate's peak-hold level estimator must use"""
        import inspect

        from voice_typer.server.audio_filters import noise_gate as ng_module
        from voice_typer.server.audio_filters.noise_gate import NoiseGate

        gate = NoiseGate(
            open_threshold_db=-26.0,
            close_threshold_db=-32.0,
            attack_ms=25.0,
            hold_ms=200.0,
            release_ms=150.0,
            sample_rate=16000,
        )

        # Smoke test: process must succeed and preserve shape.
        result = gate.process(small_audio, 16000)
        assert result is not None
        assert result.shape == small_audio.shape

        # Static check: the source of NoiseGate.process must contain
        source = inspect.getsource(ng_module.NoiseGate.process)
        assert "np.maximum.accumulate" in source, (
            "NoiseGate.process must use np.maximum.accumulate for the "
            "peak-hold level estimator (vectorized). If this assertion "
            "fails, the vectorization was reverted to a per-sample max() "
            "loop, for the CPU-cost rationale."
        )
        # the loop body must NOT call np.abs(), abs must be pre-computed
        assert "abs_x = np.abs(samples)" in source, (
            "NoiseGate.process must pre-compute abs_x outside the state-machine "
            "loop (vectorized), the per-sample abs() call was the original "
            "hot-path cost."
        )

    def test_compressor_log10_receives_array_not_scalar(self, small_audio, monkeypatch: pytest.MonkeyPatch):
        """``np.log10`` must be called with an ARRAY argument"""
        pytest.importorskip("scipy.signal")
        from voice_typer.server.audio_filters.compressor import Compressor

        comp = Compressor(threshold_db=-18.0, ratio=3.0, sample_rate=16000)

        call_args: list = []
        original_log10 = np.log10

        def tracking_log10(x, *args, **kwargs):
            call_args.append(x)
            return original_log10(x, *args, **kwargs)

        monkeypatch.setattr(np, "log10", tracking_log10)

        comp.process(small_audio, 16000)

        # The vectorized path calls np.log10 once with a 256-element
        assert len(call_args) >= 1, "np.log10 must be called at least once"
        # Find the array call (the envelope array, not the scalar
        array_calls = [a for a in call_args if hasattr(a, "ndim") and a.ndim >= 1]
        assert len(array_calls) >= 1, (
            "np.log10 must be called with an array argument (vectorized); "
            "all calls were scalar, indicates a per-sample loop regression"
        )

    def test_all_four_filters_process_without_raising(self, small_audio):
        """Sanity smoke test: all four vectorized filters must process"""
        pytest.importorskip("scipy.signal")
        from voice_typer.server.audio_filters import (
            Compressor,
            Equalizer,
            Limiter,
            NoiseGate,
        )

        for f in (
            Equalizer(sample_rate=16000),
            Compressor(sample_rate=16000),
            Limiter(sample_rate=16000),
            NoiseGate(sample_rate=16000),
        ):
            result = f.process(small_audio.copy(), 16000)
            assert result is not None, f"{f.name} returned None"
            assert result.shape == small_audio.shape, (
                f"{f.name} changed shape: in={small_audio.shape}, out={result.shape}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
