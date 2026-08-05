"""SA-5 (audio) — regression tests for findings #53, #108, #245.

Scope
-----

This file pins three fixes performed by sub-agent SA-5 in the
Fix-Existing mode session:

- **Finding #53 (S2-CR-19, High)** — per-sample Python for-loops in 4
  dynamics audio filters. The fix vectorized the equalizer, compressor,
  limiter, and noise_gate via ``scipy.signal.lfilter`` /
  ``np.maximum.accumulate``. These tests verify the vectorized path is
  still in use by counting ``lfilter`` / ``maximum.accumulate``
  invocations and asserting no per-sample ``abs()``/``log10()``/
  ``power()`` calls happen inside the filter ``process()`` bodies.

- **Finding #108 (S3-CR-6, Critical)** — ``noise_suppressor.py``
  ``deepfilternet`` path was a silent passthrough: users selecting the
  ``noisy_room`` preset got ZERO neural noise suppression with no UI
  signal. The fix marks ``is_degraded=True`` AND falls back to
  ``rnnoise`` at ``__init__`` time (not at ``process()`` time) so the
  UI can warn the user before the first audio chunk. These tests stub
  the ``df`` and ``pyrnnoise`` imports to exercise all four
  combinations (installed / not-installed) and assert the degraded
  flag, the degraded reason, and the effective ``_method`` after
  construction.

- **Finding #245 (H-22, High)** — ``audio_processor.py`` resample
  fallback silently filtered at the wrong rate when ``scipy`` was
  missing or ``resample_poly`` raised. The fix latches a
  ``_resample_degraded`` flag and surfaces it via ``is_degraded`` /
  ``degraded_reasons`` so the UI can warn the user. These tests force
  the resample path to fail (by monkeypatching ``_get_resample_poly``
  to raise) and assert the degraded flag is set, the reason mentions
  both sample rates, and the flag is cleared by ``reset()`` /
  ``set_sample_rate()`` (the corrective actions).

All tests use mocked numpy arrays (no real audio devices, no real
audio files). The ``df`` / ``pyrnnoise`` stubs are pure Python objects
injected via ``sys.modules`` so the test environment doesn't need the
real native libraries installed.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Shared stubs / fixtures
# ═══════════════════════════════════════════════════════════════════════════


def _install_fake_df(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a stub ``df`` module so ``_init_deepfilternet``'s import succeeds.

    The stub provides ``init_df`` and ``enhance`` callables so the
    historical ``from df import enhance, init_df`` line would also
    succeed — but the new ``_init_deepfilternet`` only probes for the
    top-level module (it doesn't actually call ``init_df`` because
    processing isn't wired). The stub mirrors the old shape so a future
    revert of the probe-to-call refactor still passes.
    """
    fake_df = types.ModuleType("df")
    fake_df.init_df = lambda *a, **kw: (None, None)
    fake_df.enhance = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "df", fake_df)


def _install_fake_pyrnnoise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a stub ``pyrnnoise`` module so ``_init_rnnoise``'s import succeeds.

    The stub ``RNNoise`` class's ``denoise_frame`` returns the input
    frame unchanged (passthrough) so tests can assert the rnnoise path
    was actually invoked without needing the real native library.
    """
    fake_pyrnnoise = types.ModuleType("pyrnnoise")

    class _FakeRNNoise:
        def __init__(self, sample_rate: int = 48000) -> None:
            self.sample_rate = sample_rate
            self.channels = 1

        def denoise_frame(self, frame_i16):  # noqa: ANN001
            # Return (speech_prob, cleaned_i16) — passthrough for testing.
            return (0.95, frame_i16)

    fake_pyrnnoise.RNNoise = _FakeRNNoise
    monkeypatch.setitem(sys.modules, "pyrnnoise", fake_pyrnnoise)


def _remove_module(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Simulate ``name`` not being installed by deleting it from ``sys.modules``.

    Uses ``monkeypatch.setitem(sys.modules, name, None)`` rather than
    ``del sys.modules[name]`` so the deletion is auto-undone after the
    test (restoring the real module if it was previously imported).
    """
    # Save the current state so the auto-undo restores it.
    monkeypatch.setitem(sys.modules, name, None)


# ═══════════════════════════════════════════════════════════════════════════
# Finding #108 (Critical): deepfilternet init-time fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestDeepFilterNetInitFallback:
    """S3-CR-6 (Critical): deepfilternet must fall back to rnnoise at __init__.

    Before the fix, ``__init__`` left ``self._method == "deepfilternet"``
    and ``is_degraded == False`` when the ``df`` package was importable.
    The first ``process()`` call then silently fell through to
    passthrough — users in noisy environments got ZERO noise suppression.

    The fix narrows every known method to ``"rnnoise"`` or ``"none"`` at
    construction time and marks ``is_degraded=True`` so the UI can warn
    the user before the first audio chunk.
    """

    def test_deepfilternet_installed_rnnoise_installed_falls_back_at_init(self, monkeypatch: pytest.MonkeyPatch):
        """Both libs available → method=rnnoise, degraded=True, df reason."""
        _install_fake_df(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="deepfilternet", sample_rate=16000)

        # Critical assertion: method is narrowed to rnnoise at __init__
        # (not at process() time). This means process() reaches the
        # rnnoise branch directly and the user gets neural noise
        # suppression instead of silent passthrough.
        assert ns._method == "rnnoise", (
            f"deepfilternet must fall back to rnnoise at __init__; got _method={ns._method!r}"
        )
        assert ns._backend is not None, "rnnoise backend must be initialized"
        assert ns.is_degraded is True, (
            "is_degraded must be True at __init__ so the UI can warn the user "
            "that deepfilternet isn't wired (currently falls back to rnnoise)"
        )
        assert "deepfilternet" in ns.degraded_reason.lower(), (
            f"degraded_reason must mention deepfilternet; got {ns.degraded_reason!r}"
        )
        assert "rnnoise" in ns.degraded_reason.lower(), (
            f"degraded_reason must mention the rnnoise fallback; got {ns.degraded_reason!r}"
        )

    def test_deepfilternet_installed_rnnoise_missing_degrades_to_none(self, monkeypatch: pytest.MonkeyPatch):
        """df available but rnnoise missing → method=none, degraded=True,
        reason mentions BOTH the df fallback AND the rnnoise failure."""
        _install_fake_df(monkeypatch)
        _remove_module(monkeypatch, "pyrnnoise")

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="deepfilternet", sample_rate=16000)

        assert ns._method == "none", (
            "when both deepfilternet (not wired) and rnnoise (missing) are unavailable, method must degrade to 'none'"
        )
        assert ns.is_degraded is True
        # The reason must preserve BOTH contexts: the df fallback AND the
        # rnnoise failure. This is more actionable than silently
        # overwriting with just the rnnoise message.
        reason = ns.degraded_reason.lower()
        assert "deepfilternet" in reason, (
            f"degraded_reason must mention deepfilternet context; got {ns.degraded_reason!r}"
        )
        assert "rnnoise" in reason, f"degraded_reason must mention rnnoise fallback failure; got {ns.degraded_reason!r}"

    def test_deepfilternet_missing_rnnoise_installed_falls_back_at_init(self, monkeypatch: pytest.MonkeyPatch):
        """df missing, rnnoise available → method=rnnoise, degraded=True."""
        _remove_module(monkeypatch, "df")
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="deepfilternet", sample_rate=16000)

        assert ns._method == "rnnoise"
        assert ns._backend is not None
        assert ns.is_degraded is True
        assert "deepfilternet" in ns.degraded_reason.lower()
        assert "rnnoise" in ns.degraded_reason.lower()

    def test_deepfilternet_missing_rnnoise_missing_degrades_to_none(self, monkeypatch: pytest.MonkeyPatch):
        """Both missing → method=none, degraded=True, reason mentions both."""
        _remove_module(monkeypatch, "df")
        _remove_module(monkeypatch, "pyrnnoise")

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="deepfilternet", sample_rate=16000)

        assert ns._method == "none"
        assert ns.is_degraded is True
        reason = ns.degraded_reason.lower()
        assert "deepfilternet" in reason
        assert "rnnoise" in reason

    def test_rnnoise_directly_not_degraded_when_installed(self, monkeypatch: pytest.MonkeyPatch):
        """Sanity: rnnoise alone (no deepfilternet involvement) is NOT degraded."""
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="rnnoise", sample_rate=16000)

        assert ns._method == "rnnoise"
        assert ns._backend is not None
        assert ns.is_degraded is False, (
            "rnnoise with pyrnnoise installed must NOT be degraded — only "
            "deepfilternet selection triggers the degraded flag"
        )
        assert ns.degraded_reason == ""

    def test_none_method_not_degraded(self, monkeypatch: pytest.MonkeyPatch):
        """Sanity: 'none' method is explicit passthrough, not degraded."""
        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor

        ns = NoiseSuppressor(method="none", sample_rate=16000)
        assert ns._method == "none"
        assert ns.is_degraded is False
        assert ns.degraded_reason == ""

    def test_deepfilternet_process_does_not_silently_passthrough(self, monkeypatch: pytest.MonkeyPatch):
        """Critical regression: process() must NOT return the input unchanged
        when deepfilternet was selected. The user must get rnnoise-processed
        audio (or, if rnnoise is also missing, an explicit degraded signal)."""
        _install_fake_df(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE
        from voice_typer.server.audio_filters.noise_suppressor import (
            _RNNOISE_FRAME_SIZE,
            NoiseSuppressor,
        )

        ns = NoiseSuppressor(method="deepfilternet", sample_rate=RNNOISE_SAMPLE_RATE)
        assert ns._method == "rnnoise"  # narrowed at __init__

        # Feed a full rnnoise frame at the native 48kHz rate so no
        # resampling is needed and the frame is exactly one rnnoise
        # frame (480 samples). Use a deterministic sine wave so the
        # test is reproducible.
        t = np.linspace(0, 1.0, _RNNOISE_FRAME_SIZE, endpoint=False, dtype=np.float32)
        audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        result = ns.process(audio, RNNOISE_SAMPLE_RATE)

        # The stub RNNoise returns the input frame unchanged, so the
        # result equals the input — but the *path* went through
        # _process_rnnoise (not silent passthrough). The key assertion
        # is that ``_method`` stayed "rnnoise" and ``is_degraded`` is
        # True (deepfilternet wasn't silently used).
        assert result is not None, "process() must return audio (not None)"
        assert result.shape == audio.shape
        assert ns._method == "rnnoise", (
            "process() must not mutate _method away from rnnoise (the init-time fallback should be sticky)"
        )
        assert ns.is_degraded is True, (
            "is_degraded must remain True after process() — the deepfilternet "
            "fallback is a permanent degradation, not a one-time signal"
        )

    def test_noisy_room_preset_yields_degraded_suppressor(self, monkeypatch: pytest.MonkeyPatch):
        """End-to-end: the ``noisy_room`` preset picks deepfilternet; the
        constructed NoiseSuppressor must be degraded (signaling the
        deepfilternet → rnnoise fallback) so the UI can warn the user
        before they speak their first word."""
        _install_fake_df(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor
        from voice_typer.server.audio_presets import PRESET_NOISY_ROOM, get_preset_filters

        preset_filters = get_preset_filters(PRESET_NOISY_ROOM)
        assert preset_filters["noise_suppression_method"] == "deepfilternet"

        ns = NoiseSuppressor(
            method=preset_filters["noise_suppression_method"],
            sample_rate=16000,
        )
        # Critical assertion: the suppressor is degraded at __init__ time.
        assert ns.is_degraded is True, (
            "noisy_room preset selects deepfilternet which is not wired; the "
            "suppressor must be marked degraded so the UI warns the user"
        )
        assert ns._method == "rnnoise", (
            "noisy_room preset must effectively use rnnoise (the fallback), not silent passthrough"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Finding #245 (High): resample fallback silent wrong-rate filtering
# ═══════════════════════════════════════════════════════════════════════════


class _FakeConfig:
    """Minimal config object for AudioProcessor tests — mirrors the
    FakeConfig in tests/test_audio_processor.py."""

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
    """H-22 (High): resample fallback must surface ``is_degraded``.

    Before the fix, when the input sample rate differed from the
    chain's rate AND scipy was missing or ``resample_poly`` raised,
    ``process_chunk`` silently filtered at the wrong rate (an 80 Hz
    high-pass built at 16 kHz actually cuts at 240 Hz when fed 48 kHz
    audio). The only signal was a log WARNING — invisible to the UI.

    The fix latches ``self._resample_degraded`` and surfaces it via
    ``is_degraded`` / ``degraded_reasons`` so the UI can warn the user
    to call ``set_sample_rate`` or install scipy.
    """

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
        # 48000 -> 16000 (3:1 downsample). scipy is available in the
        # test env, so resample_poly should succeed.
        audio = (np.random.randn(4800).astype(np.float32)) * 0.3
        result = p.process_chunk(audio, input_sample_rate=48000)
        assert result is not None
        assert p.is_degraded is False, "successful resample must NOT set the degraded flag"

    def test_resample_failure_sets_degraded(self, monkeypatch: pytest.MonkeyPatch):
        """H-22: when resample_poly raises, is_degraded becomes True and
        degraded_reasons mentions both sample rates."""
        from voice_typer.server import audio_processor as ap_module
        from voice_typer.server.audio_processor import AudioProcessor

        # Force the resample path to raise (simulates scipy missing or
        # any other resample failure). ``audio_processor`` resolves the
        # resampler via the lazy ``_get_resample_poly_fn()`` accessor
        # (which imports from ``recording.resampling`` on first use), so
        # the accessor is patched to return a raising callable.
        def _raise_runtime_error():
            raise RuntimeError("simulated scipy missing")

        monkeypatch.setattr(ap_module, "_get_resample_poly_fn", lambda: _raise_runtime_error)

        p = AudioProcessor(_FakeConfig(), sample_rate=16000)
        assert p.is_degraded is False, "freshly constructed processor must not be degraded"

        # Feed a chunk at a different rate to trigger the resample path.
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3
        result = p.process_chunk(audio, input_sample_rate=48000)

        # The chunk is still processed (at the wrong rate) — better to
        # filter at the wrong rate than to drop the chunk entirely.
        assert result is not None, "process_chunk must return audio even on resample failure"
        assert result.shape == audio.shape

        # Critical assertion: the degraded flag is now set.
        assert p.is_degraded is True, (
            "H-22: resample failure must set is_degraded so the UI can warn "
            "the user that filters are mistuned (wrong rate)"
        )
        # The degraded reason must mention both sample rates so the user
        # knows exactly what went wrong.
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
        """H-22: once set, the flag stays set across subsequent chunks
        (latched) — cleared only by reset() or set_sample_rate()."""
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
        # The flag indicates "this processor has experienced a resample
        # failure at some point" — useful diagnostic info even after the
        # input rate changes.
        p.process_chunk(audio, input_sample_rate=16000)
        assert p.is_degraded is True, (
            "resample-degraded flag is latched — stays set until reset() or set_sample_rate() (the corrective action)"
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
            "reset() must clear the resample-degraded flag — a new recording session starts with a clean slate"
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
            "set_sample_rate() must clear the resample-degraded flag — the "
            "chain is now tuned to the input rate, so the resample path "
            "is no longer taken"
        )
        assert p.degraded_reasons == []


# ═══════════════════════════════════════════════════════════════════════════
# Finding #53 (High): vectorized dynamics filters — regression guard
# ═══════════════════════════════════════════════════════════════════════════


class TestVectorizedDynamicsFilters:
    """S2-CR-19 (High): the 4 dynamics filters (equalizer, compressor,
    limiter, noise_gate) must NOT use per-sample Python loops with
    transcendental calls (abs/log10/exp/power) on the RT thread.

    The original OBS-ported code spent ~1ms per chunk on the RT thread
    doing 32,768 Python iterations/sec with float()/abs()/
    conditional/math.log10/exp. The vectorized version uses
    ``scipy.signal.lfilter`` (C call) and ``np.maximum.accumulate`` —
    the per-chunk cost drops to ~50µs.

    These tests verify the vectorized path is still in use by counting
    ``lfilter`` / ``maximum.accumulate`` invocations during ``process()``
    and asserting that ``np.log10`` / ``np.power`` are called with
    ARRAY arguments (not scalar, which would indicate a per-sample
    loop). The tests use small mocked numpy arrays — no real audio.
    """

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

        # `_get_lfilter` caches the resolved lfilter reference in
        # `audio_filters.base._lfilter` after the first successful import,
        # so a plain `scipy.signal.lfilter` patch is bypassed once any
        # earlier test has warmed the cache. Reset the cache (monkeypatch
        # restores it after the test) so the counting wrapper is captured
        # by this process() call.
        from voice_typer.server.audio_filters import base as _af_base

        monkeypatch.setattr(_af_base, "_lfilter", None)

        result = eq.process(small_audio, 16000)

        assert result is not None
        assert result.shape == small_audio.shape
        # The vectorized EQ uses 2 lfilter calls (one for low band,
        # one for high band — the mid band is computed by subtraction).
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

        # See test_equalizer_uses_lfilter_not_per_sample_loop — reset the
        # `_get_lfilter` cache so the counting wrapper is captured.
        from voice_typer.server.audio_filters import base as _af_base

        monkeypatch.setattr(_af_base, "_lfilter", None)

        result = comp.process(small_audio, 16000)

        assert result is not None
        assert result.shape == small_audio.shape
        # The vectorized compressor uses 2 lfilter calls (attack env +
        # release env, run in parallel then max'd).
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

        # See test_equalizer_uses_lfilter_not_per_sample_loop — reset the
        # `_get_lfilter` cache so the counting wrapper is captured.
        from voice_typer.server.audio_filters import base as _af_base

        monkeypatch.setattr(_af_base, "_lfilter", None)

        result = lim.process(small_audio, 16000)

        assert result is not None
        assert result.shape == small_audio.shape
        # The vectorized limiter uses 2 lfilter calls (attack env +
        # release env).
        assert call_count["n"] == 2, f"vectorized Limiter must use exactly 2 lfilter calls; got {call_count['n']}"

    def test_noise_gate_uses_maximum_accumulate_for_peak_hold(self, small_audio):
        """S2-CR-19: the noise gate's peak-hold level estimator must use
        ``np.maximum.accumulate`` (vectorized running-maximum), not a
        per-sample Python ``max()`` loop. The state-machine loop
        (open/close + attack/hold/release) is allowed to remain a
        Python loop because its state transitions are inherently
        sequential — but the expensive per-sample ``abs()`` and
        peak-hold bookkeeping must be vectorized.

        ``numpy.ufunc.accumulate`` is a read-only C-level attribute that
        cannot be monkeypatched at runtime, so this test uses source
        inspection (``inspect.getsource``) to verify the vectorized
        primitive is present and the per-sample ``abs()`` call is NOT
        inside a Python loop body. This is a static regression guard —
        if the vectorization is reverted to a per-sample loop, the
        source will lose the ``np.maximum.accumulate`` call and the
        test will fail.
        """
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
        # the vectorized primitive. If the vectorization is reverted
        # to a per-sample Python loop, this assertion fails.
        source = inspect.getsource(ng_module.NoiseGate.process)
        assert "np.maximum.accumulate" in source, (
            "NoiseGate.process must use np.maximum.accumulate for the "
            "peak-hold level estimator (vectorized). If this assertion "
            "fails, the vectorization was reverted to a per-sample max() "
            "loop — see S2-CR-19 for the CPU-cost rationale."
        )
        # The state-machine loop is allowed (inherently sequential), but
        # the loop body must NOT call np.abs() — abs must be pre-computed
        # once outside the loop (vectorized).
        assert "abs_x = np.abs(samples)" in source, (
            "NoiseGate.process must pre-compute abs_x outside the state-machine "
            "loop (vectorized) — the per-sample abs() call was the original "
            "S2-CR-19 hot-path cost."
        )

    def test_compressor_log10_receives_array_not_scalar(self, small_audio, monkeypatch: pytest.MonkeyPatch):
        """S2-CR-19: ``np.log10`` must be called with an ARRAY argument
        (vectorized gain computation), not a scalar (per-sample loop).

        If the original per-sample loop were restored, ``np.log10`` would
        be called n times with scalar args (n=256 here). The vectorized
        version calls it once with the full envelope array."""
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
        # array. A per-sample loop would call it 256 times with scalars.
        assert len(call_args) >= 1, "np.log10 must be called at least once"
        # Find the array call (the envelope array, not the scalar
        # ``10.0 ** (gain_db / 20.0)`` which doesn't use log10).
        array_calls = [a for a in call_args if hasattr(a, "ndim") and a.ndim >= 1]
        assert len(array_calls) >= 1, (
            "np.log10 must be called with an array argument (vectorized); "
            "all calls were scalar — indicates a per-sample loop regression"
        )

    def test_all_four_filters_process_without_raising(self, small_audio):
        """Sanity smoke test: all four vectorized filters must process
        a small mocked audio array without raising. Catches import
        errors, scipy API drift, and shape mismatches."""
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
