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
# device-native rate (matches 's "actual is 512 samples at native
# rate" finding). 16 kHz path uses 512 samples too (one chunk = 32ms).
DEFAULT_CHUNK_SAMPLES = 512
DEFAULT_RATE = 16000
DEFAULT_DURATION_S = 5.0


def _rnnoise_available() -> bool:
    """True when the rnnoise package is importable (skip-if-unavailable)."""
    try:
        import rnnoise  # noqa: F401

        return True
    except ImportError:
        return False


def _make_config(noise_suppression: str = "none") -> object:
    """Build a config object with all chain filters enabled.

    ``noise_suppression="none"`` skips the (optional, expensive)
    RNNoise/GTCRN path so the benchmark can run in environments
    without those libraries installed. Set to ``"rnnoise"`` or
    ``"gtcrn"`` to exercise that path when available.

    scipy availability: when scipy is NOT installed (e.g. in a minimal
    sandbox venv), the IIR-based filters (HighPass, Notch, Equalizer,
    Compressor, Limiter) raise ModuleNotFoundError on first ``process``
    call. We detect scipy at config-build time and disable those filters
    so the benchmark can still measure the always-on, scipy-free filters
    (NoiseGate + the noise-suppressor's degraded path). The degraded
    state is reported in the result so a regression in either path
    (full or degraded) is visible.

    Rationale: previously this function imported a parallel ``_DEFAULTS``
    dict from ``audio_chain_builder`` and used a ``_DictConfig`` shim
    that fell back to ``_DEFAULTS`` for any unspecified field — a DRY
    violation (Rule P2) that duplicated every ``Config`` default. The
    dict was deleted; this function now constructs a real
    ``Config()`` instance and applies overrides via ``setattr`` so
    there is exactly one source of truth for each default.
    """
    from voice_typer.server.config import Config

    try:
        import scipy  # noqa: F401

        scipy_available = True
    except ImportError:
        scipy_available = False

    # Construct a real Config() so every default comes from the single
    # source of truth (Config dataclass). Overrides are applied via
    # setattr — same pattern build_chain_from_dict uses.
    cfg = Config()
    cfg.noise_suppression_method = noise_suppression
    if not scipy_available:
        # Disable every IIR / scipy-dependent filter so the chain
        # still produces non-None output. NoiseGate is sample-by-sample
        # Python (no scipy) so it stays on.
        for flag in (
            "noise_filter_highpass",
            "noise_filter_notch",
            "noise_filter_eq",
            "noise_filter_compressor",
            "noise_filter_limiter",
        ):
            setattr(cfg, flag, False)

    return cfg


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


def _peak_rss_mb() -> int | None:
    """Return current peak RSS in MB, or ``None`` if unavailable.

    Peak RSS is captured at the end of the bench loop so the CI
    ratchet can detect memory regressions in the audio filter chain.
    On Linux, ``psutil`` does not expose ``peak_rss`` directly — we
    parse ``VmHWM`` from ``/proc/<pid>/status``. On Windows / macOS,
    ``memory_info().peak_rss`` is used directly.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        proc = psutil.Process()
        mi = proc.memory_info()
        if hasattr(mi, "peak_rss"):
            return int(mi.peak_rss / (1024 * 1024))
    except Exception:  # noqa: BLE001 — best-effort
        pass
    # Linux fallback: parse VmHWM from /proc/<pid>/status.
    try:
        status_path = Path(f"/proc/{proc.pid}/status")
        if status_path.is_file():
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001 — best-effort
        pass
    # Last-resort: instantaneous RSS.
    try:
        return int(proc.memory_info().rss / (1024 * 1024))
    except Exception:  # noqa: BLE001 — best-effort
        return None


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
    last_output: np.ndarray | None = None
    for i in range(n_chunks):
        chunk[:] = audio[(10 + i) * chunk_samples : (10 + i + 1) * chunk_samples]
        t0 = time.perf_counter_ns()
        result = chain.process(chunk, sample_rate)
        per_chunk_us.append((time.perf_counter_ns() - t0) / 1000.0)
        if isinstance(result, np.ndarray):
            last_output = result
    # Non-finite-output gate: a filter that emits NaN/Inf (a failure
    # mode reproduced 3/3 runs by the float32 b/a high-pass before the
    # SOS rewrite) is CORRUPTING audio while its timing may look fine.
    # The bench must fail loudly instead of printing PASS over garbage
    # output.
    if last_output is not None and not np.isfinite(last_output).all():
        raise SystemExit(
            "FAIL: filter chain produced non-finite (NaN/Inf) output — audio corruption in the chain under benchmark."
        )

    per_chunk_us.sort()
    chunk_duration_us = chunk_samples / sample_rate * 1e6
    p50 = per_chunk_us[len(per_chunk_us) // 2]
    p99 = per_chunk_us[min(len(per_chunk_us) - 1, int(len(per_chunk_us) * 0.99))]
    p99_9 = per_chunk_us[min(len(per_chunk_us) - 1, int(len(per_chunk_us) * 0.999))]
    # Capture peak RSS so the bench can detect memory regressions
    # in the audio filter chain (e.g. a filter that allocates an
    # unbounded buffer per chunk).  See ``bench/bench_memory.py`` for
    # the dedicated memory-peak bench that exercises cold import + model
    # load + sustained transcription.
    peak_rss_mb = _peak_rss_mb()
    return {
        "sample_rate": sample_rate,
        "chunk_samples": chunk_samples,
        "chunk_duration_us": round(chunk_duration_us, 1),
        "n_chunks": n_chunks,
        "filter_names": list(chain.filter_names),
        "noise_suppression": noise_suppression,
        "total_latency_ms": round(chain.total_latency_ms, 3),
        "is_degraded": chain.is_degraded,
        "degraded_reasons": list(chain.degraded_reasons),
        "p50_per_chunk_us": round(p50, 1),
        "p99_per_chunk_us": round(p99, 1),
        "p99_9_per_chunk_us": round(p99_9, 1),
        "realtime_margin_pct": round((chunk_duration_us - p99) / chunk_duration_us * 100.0, 1),
        "peak_rss_mb": peak_rss_mb,
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
        choices=("none", "rnnoise", "gtcrn"),
        help="Noise suppressor method (default: none — exercises only always-on filters)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    args = parser.parse_args()

    rates = [args.rate] if args.rate is not None else [16000, 48000]
    # Tracked noise-suppression variant: the default run exercises the
    # ``none`` chain (always available) AND the ``rnnoise`` chain when
    # the rnnoise package is importable (skip-if-unavailable — RNNoise
    # is a declared dependency so CI runners have it; minimal sandboxes
    # may not). Rows are APPENDED after the base scenarios so the perf
    # ratchet's positional indices (``…0`` / ``…1``) stay stable, and
    # each row carries ``noise_suppression`` so consumers can tell the
    # scenarios apart.
    scenarios = [args.noise_suppression]
    if args.noise_suppression == "none" and _rnnoise_available():
        scenarios.append("rnnoise")
    results = [bench_filter_chain(rate, args.duration, args.chunk_samples, ns) for ns in scenarios for rate in rates]

    exit_code = 0
    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        # JSON consumers (the CI ratchet) get the same hard gate via
        # the exit code.
        for r in results:
            if r["realtime_margin_pct"] <= 0:
                return 1
        return 0

    print("=" * 72)
    print("Voice Typer — Audio Filter Chain Benchmark")
    print("=" * 72)
    for r in results:
        print()
        print(f"  Sample rate      : {r['sample_rate']} Hz")
        print(f"  Noise suppression: {r['noise_suppression']}")
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
        print(f"  ADR-0009 §11     : {'PASS' if budget_ok else 'FAIL'} (latency<15ms & realtime margin>0)")
        if not budget_ok:
            exit_code = 1
    # Hard gate: a negative realtime margin means per-chunk processing
    # exceeds the chunk duration — dropouts are INEVITABLE at this
    # chunk size regardless of what the baseline ratchet tolerates.
    # This assertion is independent of bench-baseline.json so a ratchet
    # raised past the budget can never mask an un-runnable chain.
    for r in results:
        if r["realtime_margin_pct"] <= 0:
            print(
                f"\nGATE FAIL: {r['sample_rate']} Hz realtime margin "
                f"{r['realtime_margin_pct']}% <= 0 — per-chunk processing exceeds "
                "the chunk duration (dropouts inevitable). Exiting non-zero."
            )
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
