#!/usr/bin/env python
"""Benchmark the real-time audio filter chain hot path.

ADR-0009 §11 specifies a perf budget for the audio filter chain:
    * process 60s of 48kHz audio
    * < 5% CPU on a single core
    * < 15ms total added latency
    * no dropouts (every chunk processed in real-time)

This benchmark measures the per-chunk processing time of
:class:`voice_typer.server.audio_filters.FilterChain.process` on
synthetic audio at both 16 kHz (the ASR-native rate) and 48 kHz (the
typical device-native rate), with and without a noise-suppressor in
the chain (the most expensive filter when RNNoise is installed).

The benchmark is designed to detect regressions in the audio hot path
between releases. It is NOT a real-time-ness proof — that requires
running against a live PortAudio stream — but a sustained regression
in ``process_chunk`` throughput directly causes dropouts at smaller
buffer sizes.

Usage:
    python bench/bench_audio_filter_chain.py
    python bench/bench_audio_filter_chain.py --duration 10 --rate 48000
    python bench/bench_audio_filter_chain.py --json

Requires: numpy. Does NOT require scipy, RNNoise, or any optional dep
(the chain degrades gracefully when those are missing — the benchmark
measures the filters that ARE available, and reports the degraded
state so a regression in the degraded path is also visible).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Chunk size used by the production audio path: 512 samples at the
# device-native rate (matches XV-20's "actual is 512 samples at native
# rate" finding). 16 kHz path uses 512 samples too (one chunk = 32ms).
DEFAULT_CHUNK_SAMPLES = 512
DEFAULT_RATE = 16000
DEFAULT_DURATION_S = 5.0


def _make_config(noise_suppression: str = "none") -> object:
    """Build a config object with all chain filters enabled.

    ``noise_suppression="none"`` skips the (optional, expensive)
    RNNoise/DeepFilterNet path so the benchmark can run in environments
    without those libraries installed. Set to ``"rnnoise"`` to exercise
    that path when available.

    scipy availability: when scipy is NOT installed (e.g. in a minimal
    sandbox venv), the IIR-based filters (HighPass, Notch, Equalizer,
    Compressor, Limiter) raise ModuleNotFoundError on first ``process``
    call. We detect scipy at config-build time and disable those filters
    so the benchmark can still measure the always-on, scipy-free filters
    (NoiseGate + the noise-suppressor's degraded path). The degraded
    state is reported in the result so a regression in either path
    (full or degraded) is visible.
    """
    from voice_typer.server.audio_chain_builder import _DEFAULTS

    try:
        import scipy  # noqa: F401
        scipy_available = True
    except ImportError:
        scipy_available = False

    overrides: dict = {"noise_suppression_method": noise_suppression}
    if not scipy_available:
        # Disable every IIR / scipy-dependent filter so the chain
        # still produces non-None output. NoiseGate is sample-by-sample
        # Python (no scipy) so it stays on.
        overrides.update({
            "noise_filter_highpass": False,
            "noise_filter_notch": False,
            "noise_filter_eq": False,
            "noise_filter_compressor": False,
            "noise_filter_limiter": False,
        })

    class _DictConfig:
        def __init__(self, overrides: dict) -> None:
            self._overrides = overrides

        def __getattr__(self, name: str):
            if name in self._overrides:
                return self._overrides[name]
            return _DEFAULTS.get(name)

    return _DictConfig(overrides)


def generate_test_audio(num_samples: int) -> np.ndarray:
    """Synthesize a mono float32 test signal: 440Hz sine + pink-ish noise.

    The signal has a strong periodic component (so dynamics filters have
    something to track) plus broadband noise (so the noise gate / high-pass
    have non-trivial work). Amplitude is normalized to -6 dBFS peak so the
    limiter ceiling (-6 dB) is just barely engaged.
    """
    rng = np.random.default_rng(seed=0xA4A4)  # deterministic for repeatable runs
    t = np.arange(num_samples, dtype=np.float32) / 48000.0
    sine = 0.4 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    noise = (0.05 * rng.standard_normal(num_samples)).astype(np.float32)
    return sine + noise


def bench_filter_chain(
    sample_rate: int,
    duration_seconds: float,
    chunk_samples: int,
    noise_suppression: str,
) -> dict:
    """Benchmark FilterChain.process over many chunks.

    Returns a dict with timing stats + chain metadata. The key metric
    is ``p99_per_chunk_us`` — the 99th percentile per-chunk processing
    time. If this exceeds ``chunk_duration_us = chunk_samples /
    sample_rate * 1e6`` by any margin, dropouts are inevitable at this
    chunk size.
    """
    from voice_typer.server.audio_chain_builder import build_chain

    config = _make_config(noise_suppression=noise_suppression)
    chain = build_chain(config, sample_rate=sample_rate)

    total_samples = int(duration_seconds * sample_rate)
    audio = generate_test_audio(total_samples)

    # Pre-allocate the chunk buffer once — the production path also
    # reuses a single buffer (allocating per chunk on a RT thread is a
    # known cause of xruns).
    chunk = np.zeros(chunk_samples, dtype=np.float32)

    # Warm-up: some filters (NoiseSuppressor) need a few frames to
    # populate their internal state. Discard the first 10 chunks so
    # the steady-state measurement isn't skewed by one-time init.
    for i in range(10):
        chunk[:] = audio[i * chunk_samples : (i + 1) * chunk_samples]
        chain.process(chunk, sample_rate)

    n_chunks = max(1, total_samples // chunk_samples - 10)
    per_chunk_us: list[float] = []
    for i in range(n_chunks):
        chunk[:] = audio[(10 + i) * chunk_samples : (10 + i + 1) * chunk_samples]
        t0 = time.perf_counter_ns()
        chain.process(chunk, sample_rate)
        per_chunk_us.append((time.perf_counter_ns() - t0) / 1000.0)

    per_chunk_us.sort()
    chunk_duration_us = chunk_samples / sample_rate * 1e6
    p50 = per_chunk_us[len(per_chunk_us) // 2]
    p99 = per_chunk_us[min(len(per_chunk_us) - 1, int(len(per_chunk_us) * 0.99))]
    p99_9 = per_chunk_us[min(len(per_chunk_us) - 1, int(len(per_chunk_us) * 0.999))]
    return {
        "sample_rate": sample_rate,
        "chunk_samples": chunk_samples,
        "chunk_duration_us": round(chunk_duration_us, 1),
        "n_chunks": n_chunks,
        "filter_names": list(chain.filter_names),
        "total_latency_ms": round(chain.total_latency_ms, 3),
        "is_degraded": chain.is_degraded,
        "degraded_reasons": list(chain.degraded_reasons),
        "p50_per_chunk_us": round(p50, 1),
        "p99_per_chunk_us": round(p99, 1),
        "p99_9_per_chunk_us": round(p99_9, 1),
        "realtime_margin_pct": round((chunk_duration_us - p99) / chunk_duration_us * 100.0, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audio filter chain benchmark")
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help="Duration of audio to process per scenario (seconds)",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=None,
        help="Sample rate to benchmark (default: both 16000 and 48000)",
    )
    parser.add_argument(
        "--chunk-samples",
        type=int,
        default=DEFAULT_CHUNK_SAMPLES,
        help="Chunk size in samples (default: 512 — matches PortAudio path)",
    )
    parser.add_argument(
        "--noise-suppression",
        default="none",
        choices=("none", "rnnoise", "deepfilternet", "speex"),
        help="Noise suppressor method (default: none — exercises only always-on filters)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    args = parser.parse_args()

    rates = [args.rate] if args.rate is not None else [16000, 48000]
    results = [
        bench_filter_chain(rate, args.duration, args.chunk_samples, args.noise_suppression)
        for rate in rates
    ]

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 72)
    print("Voice Typer — Audio Filter Chain Benchmark")
    print("=" * 72)
    for r in results:
        print()
        print(f"  Sample rate      : {r['sample_rate']} Hz")
        print(f"  Chunk size       : {r['chunk_samples']} samples ({r['chunk_duration_us']:.0f} µs)")
        print(f"  Chunks measured  : {r['n_chunks']}")
        print(f"  Filters          : {', '.join(r['filter_names']) or '(empty)'}")
        print(f"  Chain latency    : {r['total_latency_ms']} ms (sum of per-filter latency)")
        print(f"  Degraded         : {r['is_degraded']}")
        if r["degraded_reasons"]:
            for reason in r["degraded_reasons"]:
                print(f"    - {reason}")
        print(f"  Per-chunk p50    : {r['p50_per_chunk_us']} µs")
        print(f"  Per-chunk p99    : {r['p99_per_chunk_us']} µs")
        print(f"  Per-chunk p99.9  : {r['p99_9_per_chunk_us']} µs")
        print(f"  Realtime margin  : {r['realtime_margin_pct']}% (positive = headroom)")
        # ADR-0009 §11 budget: < 15ms latency. The chain reports its
        # OWN latency (sum of per-filter); the benchmark does not
        # include resample latency (handled by AudioProcessor).
        budget_ok = r["total_latency_ms"] < 15.0 and r["realtime_margin_pct"] > 0
        print(f"  ADR-0009 §11     : {'PASS' if budget_ok else 'FAIL'} "
              f"(latency<15ms & realtime margin>0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
