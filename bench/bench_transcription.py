#!/usr/bin/env python
"""UX-004: Benchmark harness for Voice Typer transcription latency.

Measures:
1. Full transcription latency: time from audio input to complete text
2. Model load time: cold-start time for each model size

Usage:
    python bench/bench_transcription.py --model small.en --device cpu
    python bench/bench_transcription.py --model tiny.en --device cuda --iterations 10
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def generate_test_audio(duration_seconds: float = 5.0, sample_rate: int = 16000) -> np.ndarray:
    """Generate a synthetic test audio signal (sine wave + noise)."""
    t = np.linspace(0, duration_seconds, int(duration_seconds * sample_rate), False)
    audio = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.random.randn(len(t))
    return audio.astype(np.float32)


def bench_model_load(model_size: str, device: str) -> float:
    """Benchmark model loading time. Returns seconds."""
    from voice_typer.server.transcription import TranscriptionEngine

    t0 = time.perf_counter()
    TranscriptionEngine(model_size=model_size, device=device)
    elapsed = time.perf_counter() - t0
    return elapsed


def bench_transcription(model_size: str, device: str, iterations: int = 5) -> dict:
    """Benchmark transcription latency. Returns stats dict."""
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size=model_size, device=device)
    audio = generate_test_audio(duration_seconds=5.0)

    latencies = []
    for i in range(iterations):
        t0 = time.perf_counter()
        text = engine.transcribe_with_fallback(audio)
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)
        print(f"  iteration {i + 1}/{iterations}: {elapsed:.3f}s -> {text[:50]!r}")

    latencies.sort()
    return {
        "model": model_size,
        "device": device,
        "iterations": iterations,
        "median": latencies[len(latencies) // 2],
        "p90": latencies[int(len(latencies) * 0.9)] if len(latencies) > 1 else latencies[0],
        "min": min(latencies),
        "max": max(latencies),
    }


def main():
    parser = argparse.ArgumentParser(description="Voice Typer transcription benchmark")
    parser.add_argument("--model", default="small.en", help="Model size (tiny.en, small.en, medium.en)")
    parser.add_argument("--device", default="cpu", help="Device (cpu, cuda)")
    parser.add_argument("--iterations", type=int, default=5, help="Number of iterations")
    parser.add_argument("--load-only", action="store_true", help="Only benchmark model load time")
    args = parser.parse_args()

    print("=== Voice Typer Benchmark ===")
    print(f"Model: {args.model}")
    print(f"Device: {args.device}")
    print(f"Iterations: {args.iterations}")
    print()

    if args.load_only:
        print("Benchmarking model load time...")
        load_time = bench_model_load(args.model, args.device)
        print(f"  Model load time: {load_time:.3f}s")
        return

    print("Benchmarking transcription latency...")
    stats = bench_transcription(args.model, args.device, args.iterations)
    print()
    print("=== Results ===")
    print(f"  Model: {stats['model']}")
    print(f"  Device: {stats['device']}")
    print(f"  Iterations: {stats['iterations']}")
    print(f"  Median: {stats['median']:.3f}s")
    print(f"  P90: {stats['p90']:.3f}s")
    print(f"  Min: {stats['min']:.3f}s")
    print(f"  Max: {stats['max']:.3f}s")


if __name__ == "__main__":
    main()
