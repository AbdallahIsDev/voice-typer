"""GTCRN noise-suppression backend + suppressor integration tests.

Covers the three layers of the ``"gtcrn"`` noise-suppression method
(the bundled ONNX streaming model that replaced the retired
DeepFilterNet option):

1. **Backend unit tests (fake ONNX session)** — a fake
   ``onnxruntime.InferenceSession`` is injected so the tests do NOT
   need the real model file or real inference. The fake implements an
   identity enhancement (``enh = mix``), which — because the backend's
   sqrt-Hann analysis/synthesis pair is perfectly reconstructing at
   50 % overlap — makes the emitted stream exactly the input delayed
   by ONE hop. That gives a precise, model-independent contract to
   pin: hop assembly, first-hop zero padding, overlap-add, cache
   threading, and ``reset()``.

2. **Suppressor integration tests (fake backend class)** — the
   ``GtcrnBackend`` symbol is swapped for a lightweight fake (mirroring
   how the RNNoise tests inject a fake ``pyrnnoise`` module via
   ``sys.modules``) to exercise ``NoiseSuppressor``'s hop buffering /
   carry persistence / ``None``-on-underfill / reset plumbing, the
   16 kHz-native no-resampling path, and the 48 kHz round-trip
   resampling path without any ONNX dependency.

3. **Degradation matrix** — a failing backend construction must fall
   back to RNNoise at ``__init__`` (``is_degraded=True``, reason
   mentioning GTCRN) and further to ``"none"`` when RNNoise is also
   unavailable.

4. **Loader legacy-value remap** — an on-disk
   ``noise_suppression_method="deepfilternet"`` loads as ``"gtcrn"``
   (and the never-implemented ``"speex"`` as ``"rnnoise"``) instead of
   being reset to the default.

5. **Real-model tests (skipped when the bundled model is absent)** —
   loads the actual ``gtcrn_simple.onnx``, feeds 1 s of noisy sine
   through the full suppressor path, asserts finite / same-length /
   noise-energy-reduced output, and gates the per-hop latency at
   20 ms (the audio-thread budget; measured ~2 ms on the reference
   CPU).
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

scipy = pytest.importorskip("scipy.signal")  # window design in the backend
ort = pytest.importorskip("onnxruntime")  # the backend's runtime dependency

from voice_typer.server._audio_constants import (  # noqa: E402
    RNNOISE_SAMPLE_RATE,
    WHISPER_SAMPLE_RATE,
)
from voice_typer.server.audio_filters import gtcrn_backend as gtcrn_module  # noqa: E402
from voice_typer.server.audio_filters.gtcrn_backend import (  # noqa: E402
    CACHE_SHAPES,
    HOP,
    MODEL_PATH,
    GtcrnBackend,
)
from voice_typer.server.audio_filters.noise_suppressor import (  # noqa: E402
    _GTCRN_HOP_SIZE,
    NoiseSuppressor,
)

# ═══════════════════════════════════════════════════════════════════════════
# Fake ONNX session (backend-level tests)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeGraphInput:
    """Minimal stand-in for ``onnxruntime.NodeArg`` (just the name)."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeIdentitySession:
    """Fake ``InferenceSession`` implementing identity enhancement.

    ``run`` returns ``enh = mix`` unchanged (so the backend's ISTFT
    must reconstruct the input exactly, delayed by one hop) and each
    cache ``cache + 1`` — a visible, monotonic marker proving the
    backend threads the PREVIOUS call's cache outputs into the next
    call's inputs.
    """

    def __init__(self, path: str, providers=None, sess_options=None) -> None:
        self.path = path
        self.run_calls = 0
        self.input_names = ["mix", "conv_cache", "tra_cache", "inter_cache"]

    def get_inputs(self):
        return [_FakeGraphInput(n) for n in self.input_names]

    def run(self, output_names, feed):
        self.run_calls += 1
        mix = feed[self.input_names[0]]
        caches = [feed[n] for n in self.input_names[1:]]
        return [mix.copy()] + [np.asarray(c, dtype=np.float32) + 1.0 for c in caches]


@pytest.fixture
def fake_identity_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> GtcrnBackend:
    """Construct a ``GtcrnBackend`` over the fake identity session.

    ``onnxruntime.InferenceSession`` is monkeypatched on the REAL
    onnxruntime module (the backend imports it inside ``__init__``, so
    the patch is picked up at call time), and ``MODEL_PATH`` is pointed
    at a scratch file so the existence check passes without the real
    bundled model.
    """
    scratch_model = tmp_path / "fake_gtcrn.onnx"
    scratch_model.write_bytes(b"not-a-real-model")
    monkeypatch.setattr(ort, "InferenceSession", _FakeIdentitySession)
    monkeypatch.setattr(gtcrn_module, "MODEL_PATH", scratch_model)
    return GtcrnBackend()


class TestGtcrnBackendFakeSession:
    """Backend streaming semantics with a fake identity ONNX session."""

    def test_identity_stream_is_input_delayed_by_one_hop(self, fake_identity_backend):
        """Identity enhancement → emitted stream == input delayed by 1 hop.

        The first emitted block is the zero-padded pre-roll (all zeros,
        because the very first analysis frame's left half is zeros),
        then each block equals the PREVIOUS hop exactly (sqrt-Hann
        analysis × synthesis is perfectly reconstructing at 50 %
        overlap). This pins hop assembly, the first-hop zero padding,
        and the overlap-add bookkeeping in one assertion set.
        """
        backend = fake_identity_backend
        rng = np.random.default_rng(11)
        hops = [rng.standard_normal(HOP).astype(np.float32) * 0.2 for _ in range(4)]

        outputs = [backend.process_hop(hop)[0] for hop in hops]

        assert len(outputs) == 4
        for out in outputs:
            assert out.shape == (HOP,)
            assert out.dtype == np.float32
        # Block 0 covers the pre-roll region (input zero-padded left).
        assert np.allclose(outputs[0], 0.0, atol=1e-6), (
            f"first block must be the zero-padded pre-roll; got {outputs[0][:8]!r}"
        )
        # Block t (t >= 1) is hop t-1, to float32 FFT round-trip precision.
        for t in range(1, 4):
            assert np.allclose(outputs[t], hops[t - 1], atol=1e-5), (
                f"output block {t} must reconstruct hop {t - 1} (one-hop algorithmic delay)"
            )

    def test_caches_thread_across_hops_and_reset_clears(self, fake_identity_backend):
        """The fake cache marker (input + 1) proves cache threading.

        After construction the warmup hop runs then ``reset()`` zeroes
        everything, so caches start at 0. Each processed hop adds 1 —
        if the backend failed to feed the previous outputs back in,
        the marker would stay at 1 forever.
        """
        backend = fake_identity_backend
        assert all(np.all(c == 0.0) for c in backend.caches), (
            "caches must be zero right after construction (warmup + reset)"
        )

        hop = np.ones(HOP, dtype=np.float32) * 0.1
        backend.process_hop(hop)
        assert all(np.all(c == 1.0) for c in backend.caches), "after ONE hop the fake's +1 cache marker must be visible"
        backend.process_hop(hop)
        assert all(np.all(c == 2.0) for c in backend.caches), (
            "after TWO hops the marker must be 2 — the previous call's "
            "cache outputs must be threaded into the next call's inputs"
        )

        backend.reset()
        assert all(np.all(c == 0.0) for c in backend.caches), "reset() must zero the recurrent caches"
        backend.process_hop(hop)
        assert all(np.all(c == 1.0) for c in backend.caches), "after reset the cache sequence restarts from zero"

    def test_hop_length_normalized_defensively(self, fake_identity_backend):
        """Short hops are zero-padded, long hops truncated — never raised."""
        backend = fake_identity_backend
        short = np.ones(100, dtype=np.float32) * 0.3
        out_short, _ = backend.process_hop(short)
        assert out_short.shape == (HOP,)

        long_ = np.ones(HOP * 2, dtype=np.float32) * 0.3
        out_long, _ = backend.process_hop(long_)
        assert out_long.shape == (HOP,)

    def test_explicit_caches_parameter_is_used(self, fake_identity_backend):
        """Passing ``caches=`` explicitly overrides the internal state.

        Feeding all-zero caches on every call keeps the marker at 1 —
        proving the explicit argument (not the internal state) was
        consumed. The returned new_caches still update the internal
        state (production threading).
        """
        backend = fake_identity_backend
        zeros = tuple(np.zeros(shape, dtype=np.float32) for shape in CACHE_SHAPES)
        hop = np.ones(HOP, dtype=np.float32) * 0.1
        for _ in range(3):
            _, new_caches = backend.process_hop(hop, caches=zeros)
            assert all(np.all(c == 1.0) for c in new_caches), (
                "explicit zero caches must be the ones consumed (marker stays 1)"
            )

    def test_bad_graph_input_arity_raises_at_init(self, monkeypatch, tmp_path):
        """A graph with the wrong input arity must fail AT CONSTRUCTION
        (init-time warmup) — never on the first real audio chunk."""

        class _TwoInputSession(_FakeIdentitySession):
            def get_inputs(self):
                return [_FakeGraphInput(n) for n in ("mix", "conv_cache")]

        scratch_model = tmp_path / "bad_graph.onnx"
        scratch_model.write_bytes(b"not-a-real-model")
        monkeypatch.setattr(ort, "InferenceSession", _TwoInputSession)
        monkeypatch.setattr(gtcrn_module, "MODEL_PATH", scratch_model)
        with pytest.raises(RuntimeError, match="input arity"):
            GtcrnBackend()

    def test_missing_model_file_raises(self, monkeypatch, tmp_path):
        """A missing bundled model is an init-time error (the suppressor
        catches it and degrades to RNNoise — see the matrix below)."""
        monkeypatch.setattr(ort, "InferenceSession", _FakeIdentitySession)
        monkeypatch.setattr(gtcrn_module, "MODEL_PATH", tmp_path / "absent.onnx")
        with pytest.raises(RuntimeError, match="not found"):
            GtcrnBackend()


# ═══════════════════════════════════════════════════════════════════════════
# Fake backend class (suppressor-level tests)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeGtcrnBackend:
    """Minimal stand-in for ``GtcrnBackend`` at the suppressor layer.

    Records every hop it receives and returns ``hop * 0.5`` (a
    deterministic, detectable transform). Mirrors the fake-pyrnnoise
    pattern used by the RNNoise tests: the class symbol is swapped on
    the ``gtcrn_backend`` module namespace so ``_init_gtcrn``'s
    call-time ``from ... import GtcrnBackend`` resolves to the fake.
    """

    last_instance: _FakeGtcrnBackend | None = None

    def __init__(self) -> None:
        self.hops: list[np.ndarray] = []
        self.reset_calls = 0
        _FakeGtcrnBackend.last_instance = self

    def process_hop(self, hop, caches=None):
        hop = np.asarray(hop, dtype=np.float32)
        self.hops.append(hop.copy())
        return hop * 0.5, ()

    def reset(self) -> None:
        self.reset_calls += 1
        self.hops.clear()


@pytest.fixture
def fake_gtcrn_class(monkeypatch: pytest.MonkeyPatch) -> type[_FakeGtcrnBackend]:
    _FakeGtcrnBackend.last_instance = None
    monkeypatch.setattr(gtcrn_module, "GtcrnBackend", _FakeGtcrnBackend)
    return _FakeGtcrnBackend


def _install_fake_pyrnnoise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a stub ``pyrnnoise`` module (same shape as the RNNoise tests)."""
    fake_pyrnnoise = types.ModuleType("pyrnnoise")

    class _FakeRNNoise:
        def __init__(self, sample_rate: int = RNNOISE_SAMPLE_RATE) -> None:
            self.sample_rate = sample_rate
            self.channels = 1

        def denoise_frame(self, frame_i16):
            return (0.95, frame_i16)

    fake_pyrnnoise.RNNoise = _FakeRNNoise
    monkeypatch.setitem(sys.modules, "pyrnnoise", fake_pyrnnoise)


def _remove_module(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setitem(sys.modules, name, None)


class TestSuppressorGtcrnIntegration:
    """``NoiseSuppressor`` hop buffering / carry / reset with a fake backend."""

    def test_init_success_not_degraded(self, fake_gtcrn_class):
        ns = NoiseSuppressor(method="gtcrn", sample_rate=WHISPER_SAMPLE_RATE)
        assert ns._method == "gtcrn"
        assert ns._backend is not None
        assert ns.is_degraded is False
        assert ns.degraded_reason == ""
        assert ns.latency_ms == pytest.approx(16.0), "GTCRN latency is one 256-sample hop at 16 kHz (16 ms)"

    def test_hop_buffering_output_length_and_carry(self, fake_gtcrn_class):
        """700 samples @16 kHz → 2 full hops processed, 188-sample carry.

        Output length ALWAYS matches the input length (zero-padded
        past the processed hops), mirroring the RNNoise contract.
        """
        ns = NoiseSuppressor(method="gtcrn", sample_rate=WHISPER_SAMPLE_RATE)
        rng = np.random.default_rng(5)
        audio = (rng.standard_normal(700) * 0.1).astype(np.float32)

        result = ns.process(audio, WHISPER_SAMPLE_RATE)

        assert result is not None
        assert result.shape == audio.shape, "output length must equal input length"
        backend = fake_gtcrn_class.last_instance
        assert len(backend.hops) == 2, (
            f"700 samples at 16 kHz must yield exactly 2 full 256-sample hops; got {len(backend.hops)}"
        )
        for hop in backend.hops:
            assert hop.shape == (_GTCRN_HOP_SIZE,)
        # The two processed hops are the FIRST 512 samples of the input.
        assert np.array_equal(backend.hops[0], audio[:256])
        assert np.array_equal(backend.hops[1], audio[256:512])
        # The processed region is halved by the fake (hop * 0.5); the
        # tail beyond the last full hop is zero-padded.
        assert np.allclose(result[:512], audio[:512] * 0.5, atol=1e-7)
        assert np.all(result[512:] == 0.0)
        # 188 samples remain in the carry for the next call.
        assert ns._carry.size == 700 - 2 * _GTCRN_HOP_SIZE

    def test_carry_persists_across_process_calls(self, fake_gtcrn_class):
        """A second call consumes the previous carry first — hop 3 spans
        the carry tail plus the new chunk's head."""
        ns = NoiseSuppressor(method="gtcrn", sample_rate=WHISPER_SAMPLE_RATE)
        rng = np.random.default_rng(6)
        first = (rng.standard_normal(700) * 0.1).astype(np.float32)
        second = (rng.standard_normal(68) * 0.1).astype(np.float32)

        ns.process(first, WHISPER_SAMPLE_RATE)
        carry_before = ns._carry.copy()
        result = ns.process(second, WHISPER_SAMPLE_RATE)

        backend = fake_gtcrn_class.last_instance
        assert len(backend.hops) == 3, (
            "700 + 68 samples = 768 = exactly 3 hops; the third hop must "
            "consume the 188-sample carry plus the first 68 new samples"
        )
        assert np.array_equal(backend.hops[2], np.concatenate([carry_before, second]))
        assert result is not None
        assert result.shape == second.shape

    def test_underfilled_chunk_returns_none(self, fake_gtcrn_class):
        """Fewer than 256 buffered samples → None (same contract as RNNoise)."""
        ns = NoiseSuppressor(method="gtcrn", sample_rate=WHISPER_SAMPLE_RATE)
        audio = np.zeros(100, dtype=np.float32)
        assert ns.process(audio, WHISPER_SAMPLE_RATE) is None
        assert fake_gtcrn_class.last_instance.hops == [], "no hop may run while underfilled"
        assert ns._carry.size == 100

    def test_reset_clears_carry_and_backend_state(self, fake_gtcrn_class):
        ns = NoiseSuppressor(method="gtcrn", sample_rate=WHISPER_SAMPLE_RATE)
        audio = (np.random.default_rng(7).standard_normal(700) * 0.1).astype(np.float32)
        ns.process(audio, WHISPER_SAMPLE_RATE)
        assert ns._carry.size == 188

        ns.reset()

        backend = fake_gtcrn_class.last_instance
        assert backend.reset_calls == 1, "reset() must forward to the backend (caches + tail)"
        assert ns._carry.size == 0
        # After reset the carry is gone: 100 samples alone underfill again.
        assert ns.process(np.zeros(100, dtype=np.float32), WHISPER_SAMPLE_RATE) is None

    def test_non_native_rate_resamples_to_16k(self, fake_gtcrn_class):
        """48 kHz input round-trips through the 16 kHz-native model.

        1440 samples @48 kHz → 480 @16 kHz → 1 full hop + 224 carry.
        The GTCRN resampler pair is separate from the RNNoise pair
        (native-rate check keys on 16 kHz, not 48 kHz).
        """
        ns = NoiseSuppressor(method="gtcrn", sample_rate=RNNOISE_SAMPLE_RATE)
        rng = np.random.default_rng(8)
        audio = (rng.standard_normal(1440) * 0.1).astype(np.float32)

        result = ns.process(audio, RNNOISE_SAMPLE_RATE)

        assert ns.is_degraded is False
        assert result is not None
        assert result.shape == audio.shape
        assert np.all(np.isfinite(result))
        backend = fake_gtcrn_class.last_instance
        assert len(backend.hops) == 1, "1440 @48k → 480 @16k → exactly one 256-sample hop"
        assert backend.hops[0].shape == (_GTCRN_HOP_SIZE,)
        # Resamplers for the GTCRN path were built (native rate is 16k, not 48k).
        assert ns._gtcrn_upsampler is not None
        assert ns._gtcrn_downsampler is not None
        assert ns._gtcrn_resampler_rate == RNNOISE_SAMPLE_RATE

    def test_legacy_deepfilternet_alias_routes_to_gtcrn(self, fake_gtcrn_class):
        """Direct construction with the retired value still gets the live
        backend (config-file loads are remapped by ``Config.load``;
        this covers embedders/tests passing the old name)."""
        ns = NoiseSuppressor(method="deepfilternet", sample_rate=WHISPER_SAMPLE_RATE)
        assert ns._method == "gtcrn"
        assert ns.is_degraded is False
        assert ns._backend is not None


# ═══════════════════════════════════════════════════════════════════════════
# Degradation matrix (init failure → rnnoise → none)
# ═══════════════════════════════════════════════════════════════════════════


class TestGtcrnDegradationMatrix:
    """GTCRN init failure must degrade to rnnoise at ``__init__`` time."""

    @staticmethod
    def _install_failing_backend(monkeypatch: pytest.MonkeyPatch) -> None:
        class _FailingBackend:
            def __init__(self) -> None:
                raise RuntimeError("model exploded")

        monkeypatch.setattr(gtcrn_module, "GtcrnBackend", _FailingBackend)

    def test_failure_rnnoise_available_falls_back_at_init(self, monkeypatch):
        self._install_failing_backend(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        ns = NoiseSuppressor(method="gtcrn", sample_rate=16000)

        assert ns._method == "rnnoise", f"a failed GTCRN init must narrow to rnnoise at __init__ (got {ns._method!r})"
        assert ns._backend is not None
        assert ns.is_degraded is True, "the UI must see the degradation immediately"
        reason = ns.degraded_reason.lower()
        assert "gtcrn" in reason, f"reason must mention gtcrn; got {ns.degraded_reason!r}"
        assert "rnnoise" in reason, f"reason must mention the rnnoise fallback; got {ns.degraded_reason!r}"

    def test_failure_rnnoise_missing_degrades_to_none(self, monkeypatch):
        self._install_failing_backend(monkeypatch)
        _remove_module(monkeypatch, "pyrnnoise")

        ns = NoiseSuppressor(method="gtcrn", sample_rate=16000)

        assert ns._method == "none", "both backends unavailable → passthrough"
        assert ns.is_degraded is True
        reason = ns.degraded_reason.lower()
        assert "gtcrn" in reason, f"reason must preserve the GTCRN context; got {ns.degraded_reason!r}"
        assert "rnnoise" in reason, f"reason must also surface the rnnoise fallback failure; got {ns.degraded_reason!r}"

    def test_failure_reason_surfaces_original_exception(self, monkeypatch):
        self._install_failing_backend(monkeypatch)
        _install_fake_pyrnnoise(monkeypatch)

        ns = NoiseSuppressor(method="gtcrn", sample_rate=16000)
        assert "model exploded" in ns.degraded_reason, (
            "the original exception message must reach degraded_reason so the user can act on the specific failure"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Parity surfaces (allowlist ↔ schema Literal ↔ method routing)
# ═══════════════════════════════════════════════════════════════════════════


class TestNoiseSuppressionMethodParity:
    """The enum must agree across the IPC allowlist and the schema Literal."""

    def test_allowlist_matches_schema_literal(self):
        import typing

        from voice_typer.server.config import Config
        from voice_typer.server.config_validators import NOISE_SUPPRESSION_METHODS

        literal_args = set(typing.get_args(typing.get_type_hints(Config)["noise_suppression_method"]))
        assert literal_args == {"rnnoise", "gtcrn", "none"}
        assert set(NOISE_SUPPRESSION_METHODS) == literal_args, (
            "NOISE_SUPPRESSION_METHODS (IPC validator allowlist) must match the "
            "Config schema Literal — a drift silently rejects valid IPC values"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Loader legacy-value remap
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def isolated_config_dir(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    return tmp_config_dir


class TestLoaderLegacyValueRemap:
    """On-disk legacy enum values load as their live successors.

    A VALUE remap (deepfilternet → gtcrn) is better UX than the
    reset-to-default the invalid-enum reset would apply: the user who
    explicitly chose the premium denoiser keeps a premium denoiser.
    """

    def test_deepfilternet_loads_as_gtcrn(self, isolated_config_dir: Path):
        from voice_typer.server.config import Config

        config_file = isolated_config_dir / "config.json"
        # ``audio_preset="custom"`` so apply_preset does not overwrite
        # the method (a user who picked a method manually is on custom).
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "audio_preset": "custom",
                    "noise_suppression_method": "deepfilternet",
                }
            ),
            encoding="utf-8",
        )

        instance = Config.load()

        assert instance.noise_suppression_method == "gtcrn", (
            f"the legacy 'deepfilternet' value must load as 'gtcrn' (got {instance.noise_suppression_method!r})"
        )
        assert any("deepfilternet" in w and "gtcrn" in w for w in instance.last_load_warnings), (
            "the remap must append a load warning naming both the legacy and "
            f"the live value; got {instance.last_load_warnings!r}"
        )

    def test_speex_loads_as_rnnoise(self, isolated_config_dir: Path):
        from voice_typer.server.config import Config

        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "audio_preset": "custom",
                    "noise_suppression_method": "speex",
                }
            ),
            encoding="utf-8",
        )

        instance = Config.load()

        assert instance.noise_suppression_method == "rnnoise", (
            "the never-implemented 'speex' value must load as the default "
            f"'rnnoise' (got {instance.noise_suppression_method!r})"
        )

    def test_live_values_pass_through_untouched(self, isolated_config_dir: Path):
        from voice_typer.server.config import Config

        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "audio_preset": "custom",
                    "noise_suppression_method": "gtcrn",
                }
            ),
            encoding="utf-8",
        )

        instance = Config.load()

        assert instance.noise_suppression_method == "gtcrn"
        assert not any("noise_suppression_method" in w for w in instance.last_load_warnings), (
            f"live values must not produce migration warnings; got {instance.last_load_warnings!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Real-model tests (skipped when the bundled ONNX model is absent)
# ═══════════════════════════════════════════════════════════════════════════

_requires_real_model = pytest.mark.skipif(
    not MODEL_PATH.exists(),
    reason=f"bundled GTCRN model not found at {MODEL_PATH}",
)


class TestRealModel:
    """End-to-end tests against the actual bundled ONNX model."""

    @_requires_real_model
    def test_noisy_sine_suppressed_same_length_finite(self):
        """1 s of 440 Hz sine + white noise through the full suppressor.

        Assertions: finite output, EXACT input length (the
        length-match pad/truncate contract), and the noise energy on
        the noisy segment is reduced (output RMS strictly below input
        RMS — GTCRN suppresses both the stationary tone and the white
        noise; measured ~0.03× input RMS on the reference CPU).
        """
        ns = NoiseSuppressor(method="gtcrn", sample_rate=WHISPER_SAMPLE_RATE)
        assert ns.is_degraded is False, f"the bundled model must load cleanly; degraded_reason={ns.degraded_reason!r}"

        rng = np.random.default_rng(42)
        t = np.arange(WHISPER_SAMPLE_RATE, dtype=np.float32) / WHISPER_SAMPLE_RATE
        audio = (
            0.3 * np.sin(2 * np.pi * 440.0 * t) + 0.15 * rng.standard_normal(WHISPER_SAMPLE_RATE).astype(np.float32)
        ).astype(np.float32)

        result = ns.process(audio, WHISPER_SAMPLE_RATE)

        assert result is not None
        assert result.shape == audio.shape, "output length must equal input length"
        assert np.all(np.isfinite(result)), "output must be finite (no NaN/Inf)"

        seg = slice(256, WHISPER_SAMPLE_RATE)  # skip the zero-padded first hop
        in_rms = float(np.sqrt(np.mean(audio[seg].astype(np.float64) ** 2)))
        out_rms = float(np.sqrt(np.mean(result[seg].astype(np.float64) ** 2)))
        assert out_rms < in_rms, (
            f"the denoiser must reduce the noisy-segment energy: in={in_rms:.4f}, out={out_rms:.4f}"
        )

    @_requires_real_model
    def test_hop_latency_budget(self):
        """PERF gate: mean per-hop inference time must stay ≤ 20 ms.

        The hop is 16 ms of audio at 16 kHz; the audio worker thread
        needs RTF < ~1 for the filter chain to keep up. Measured ~2 ms
        mean on the reference CPU (upstream reports RTF 0.07).
        """
        backend = GtcrnBackend()
        rng = np.random.default_rng(1)
        hop = (rng.standard_normal(HOP) * 0.1).astype(np.float32)

        backend.process_hop(hop)  # warm the graph beyond the init warmup

        n_hops = 50
        t0 = time.perf_counter()
        for _ in range(n_hops):
            backend.process_hop(hop)
        mean_ms = (time.perf_counter() - t0) / n_hops * 1000.0

        assert mean_ms <= 20.0, (
            f"mean per-hop inference {mean_ms:.2f} ms exceeds the 20 ms audio-thread budget (16 ms hop → RTF > 1.25)"
        )

    @_requires_real_model
    def test_non_native_rate_end_to_end(self):
        """The 48 kHz round-trip path with the real model stays finite,
        length-matched, and noise-reducing."""
        ns = NoiseSuppressor(method="gtcrn", sample_rate=RNNOISE_SAMPLE_RATE)
        assert ns.is_degraded is False

        rng = np.random.default_rng(9)
        n = RNNOISE_SAMPLE_RATE // 2  # 0.5 s
        t = np.arange(n, dtype=np.float32) / RNNOISE_SAMPLE_RATE
        audio = (0.3 * np.sin(2 * np.pi * 440.0 * t) + 0.15 * rng.standard_normal(n).astype(np.float32)).astype(
            np.float32
        )

        result = ns.process(audio, RNNOISE_SAMPLE_RATE)

        assert result is not None
        assert result.shape == audio.shape
        assert np.all(np.isfinite(result))
        seg = slice(2000, n - 2000)
        in_rms = float(np.sqrt(np.mean(audio[seg].astype(np.float64) ** 2)))
        out_rms = float(np.sqrt(np.mean(result[seg].astype(np.float64) ** 2)))
        assert out_rms < in_rms
