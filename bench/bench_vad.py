#!/usr/bin/env python
"""Benchmark the VAD (Voice Activity Detection) hot path.

The VAD processor is called on every audio chunk to decide SPEECH vs
SILENCE. Two paths exist:

1. :class:`voice_typer.server.vad_processor.VadProcessor.update_frame`
   — the state machine: takes a per-frame RMS (dB) + optional Silero
   probability, returns the new state. Cheap (pure-Python state machine).
2. :func:`voice_typer.server.vad.compute_vad_prob` — the Silero model
   inference path. Expensive (Torch forward pass). Only invoked when
   ``use_silero_vad`` is enabled.

This benchmark measures both paths and reports per-call latency. The
Silero path is auto-skipped when torch is unavailable (the production
code degrades to RMS-only VAD in that case).

Usage:
    python bench/bench_vad.py
    python bench/bench_vad.py --iterations 5000 --include-silero
    python bench/bench_vad.py --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 30ms frame at 16kHz = 480 samples (matches the production VAD frame).
DEFAULT_FRAME_SAMPLES = 480
DEFAULT_RATE = 16000


class _StubConfig:
    """Minimal config stub for VadProcessor — exposes the few attributes
    VadProcessor reads during construction and on first vad_enabled
    access."""

    use_silero_vad = False
    vad_speech_threshold = 0.5
    vad_silence_threshold = 0.3
    vad_enabled = True


def _make_vad_processor():
    from voice_typer.server.vad_processor import VadProcessor

    # Pass a None ``vad_check_available_fn`` so VadProcessor uses its
    # own lazy import path — this mirrors production behavior. We don't
    # want to pre-import torch for the state-machine-only benchmark.
    return VadProcessor(_StubConfig(), vad_check_available_fn=None)


def _make_silero_audio(num_samples: int) -> np.ndarray:
    """Generate a 16kHz mono signal that looks like speech: 200ms bursts
    of 200Hz tone at -10dBFS, separated by 100ms of low noise."""
    rng = np.random.default_rng(seed=0xBEEF)
    t = np.arange(num_samples, dtype=np.float32) / DEFAULT_RATE
    # 200Hz carrier (close to male speech fundamental)
    carrier = np.sin(2 * np.pi * 200.0 * t).astype(np.float32)
    # Amplitude envelope: 200ms on, 100ms off, repeat
    period = 0.3
    envelope = ((t % period) < 0.2).astype(np.float32)
    signal = (0.3 * carrier * envelope).astype(np.float32)
    noise = (0.005 * rng.standard_normal(num_samples)).astype(np.float32)
    return (signal + noise).astype(np.float32)


def bench_update_frame_state_machine(iterations: int) -> dict:
    """Benchmark VadProcessor.update_frame (pure-Python state machine)."""
    vp = _make_vad_processor()
    # Simulate a steady stream of -25dB frames (mid-range, above the
    # default speech threshold of -40dB). The state machine will
    # transition UNKNOWN → SPEECH → SILENCE cyclically.
    chunk_rms_db = -25.0
    per_call_us: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        vp.update_frame(chunk_rms_db, vad_prob=None)
        per_call_us.append((time.perf_counter_ns() - t0) / 1000.0)
    per_call_us.sort()
    return {
        "path": "VadProcessor.update_frame (state machine, no Silero)",
        "iterations": iterations,
        "p50_us": round(per_call_us[len(per_call_us) // 2], 2),
        "p99_us": round(per_call_us[int(len(per_call_us) * 0.99)], 2),
        "max_us": round(per_call_us[-1], 2),
        "mean_us": round(statistics.mean(per_call_us), 2),
    }


def bench_compute_vad_prob(iterations: int) -> dict | None:
    """Benchmark vad.compute_vad_prob (Silero model inference).

    Returns None when torch / Silero is unavailable — the production
    path degrades to RMS-only VAD in that case, so the benchmark
    mirrors that.
    """
    try:
        from voice_typer.server.vad import compute_vad_prob, is_available
    except ImportError:
        return None
    if not is_available():
        return None
    # Silero needs 16kHz float32 input. Use 512 samples (32ms) — a
    # common chunk size for the production path.
    frame = _make_silero_audio(DEFAULT_FRAME_SAMPLES)
    # Warm-up: first call loads the model (slow). Discard from the
    # steady-state measurement.
    compute_vad_prob(frame, DEFAULT_RATE)
    per_call_us: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        compute_vad_prob(frame, DEFAULT_RATE)
        per_call_us.append((time.perf_counter_ns() - t0) / 1000.0)
    per_call_us.sort()
    return {
        "path": "vad.compute_vad_prob (Silero inference)",
        "iterations": iterations,
        "p50_us": round(per_call_us[len(per_call_us) // 2], 2),
        "p99_us": round(per_call_us[int(len(per_call_us) * 0.99)], 2),
        "max_us": round(per_call_us[-1], 2),
        "mean_us": round(statistics.mean(per_call_us), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="VAD benchmark")
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--include-silero", action="store_true", help="Also benchmark Silero path (slow)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [bench_update_frame_state_machine(args.iterations)]
    if args.include_silero:
        silero = bench_compute_vad_prob(args.iterations)
        if silero is not None:
            results.append(silero)
        else:
            print("NOTE: Silero VAD unavailable — skipping (production degrades to RMS-only).", file=sys.stderr)

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 72)
    print("Voice Typer — VAD Benchmark")
    print("=" * 72)
    for r in results:
        print()
        print(f"  Path        : {r['path']}")
        print(f"  Iterations  : {r['iterations']}")
        print(f"  Mean / call : {r['mean_us']} µs")
        print(f"  p50 / call  : {r['p50_us']} µs")
        print(f"  p99 / call  : {r['p99_us']} µs")
        print(f"  Max / call  : {r['max_us']} µs")
        # 30ms frame = 30000µs budget. Silero inference should be
        # well under that on a modern CPU; the state machine is
        # negligible (<100µs typically).
        budget_us = 30000
        margin = round((budget_us - r["p99_us"]) / budget_us * 100.0, 1)
        print(f"  30ms budget : {'PASS' if margin > 0 else 'FAIL'} (p99={r['p99_us']}µs, margin={margin}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
