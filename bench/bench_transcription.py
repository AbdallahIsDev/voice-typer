#!/usr/bin/env python
"""Benchmark harness for Voice Typer transcription latency.

Measures:
1. Full transcription latency: time from audio input to complete text
2. Model load time: cold-start time for each model size
3. Words-per-second (WPS) throughput — the user-facing "how fast does my
   speech become text" metric.

Methodology fixes:
* ``np.random.default_rng(seed=0xA4A4)`` (matches
  ``bench_audio_filter_chain.py:117``) so the synthetic-audio path is
  deterministic.  Two runs of ``bench_transcription.py`` on the same
  machine now produce byte-identical latency distributions.
* Default ``--iterations 10`` so p90 is no longer ``max`` (the previous
  ``n=5`` made ``int(5 * 0.9) = 4`` index into ``sorted[4]`` — the max).
* ``--fixture`` option loads a real 16 kHz speech WAV from
  ``tests/fixtures/`` when available (falls back to the synthetic
  signal if the fixture is missing or a different file is requested).
* ``--json`` output for CI ratchet comparison.
* ``psutil.Process().memory_info().peak_rss`` captured before + after
  the benchmark loop and reported in MB (also captures peak RSS
  for this bench).

Usage:
    python bench/bench_transcription.py --model small.en --device cpu
    python bench/bench_transcription.py --iterations 10 --json
    python bench/bench_transcription.py --fixture tests/fixtures/test_440hz_1s_16k.wav
    python bench/bench_transcription.py --model tiny.en --load-only
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Deterministic RNG seed — matches bench_audio_filter_chain.py:117 so
# the synthetic-audio path is byte-reproducible across runs.
RNG_SEED = 0xA4A4

DEFAULT_ITERATIONS = 20  # nearest-rank p90 needs N >= 10; 20 gives a real tail + absorbs scheduler noise
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_DURATION_S = 5.0
DEFAULT_FIXTURE = "tests/fixtures/test_440hz_1s_16k.wav"


def _try_psutil() -> object | None:
    """Return a ``psutil.Process`` for the current PID, or ``None``.

    ``psutil`` is an optional dependency for the bench harness; if it is
    missing the bench still runs but skips the peak_rss capture.
    """
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process()


def _peak_rss_mb(proc: object | None) -> int | None:
    """Return current peak RSS in MB, or ``None`` if unavailable.

    ``psutil.Process().memory_info().peak_rss`` is platform-specific:

    * Windows / macOS: exposed directly via ``memory_info().peak_rss``.
    * Linux: NOT exposed by psutil — the high-water mark RSS lives in
      ``/proc/<pid>/status`` (field ``VmHWM:`` in kB). We parse it
      directly so the bench captures the true peak rather than the
      instantaneous RSS (which is what ``memory_info().rss`` returns).

    Falls back to ``memory_info().rss`` (current RSS) on platforms
    where neither path is available — still useful, just a different
    metric.
    """
    if proc is None:
        return None
    try:
        mi = proc.memory_info()  # type: ignore[attr-defined]
        if hasattr(mi, "peak_rss"):
            return int(mi.peak_rss / (1024 * 1024))
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        pass
    # Linux fallback: parse VmHWM from /proc/<pid>/status.
    try:
        pid = proc.pid  # type: ignore[attr-defined]
        status_path = Path(f"/proc/{pid}/status")
        if status_path.is_file():
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmHWM:"):
                    # "VmHWM:    12345 kB"
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        pass
    # Last-resort fallback: instantaneous RSS (not peak, but still useful).
    try:
        return int(proc.memory_info().rss / (1024 * 1024))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        return None


def generate_test_audio(
    duration_seconds: float = DEFAULT_DURATION_S,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    *,
    seed: int = RNG_SEED,
) -> np.ndarray:
    """Generate a deterministic synthetic test audio signal.

    440 Hz sine + Gaussian noise — identical bytes on every run because
    the RNG is seeded (the previous implementation used
    ``np.random.randn`` which drew from the global RNG state, making the
    benchmark non-deterministic).
    """
    rng = np.random.default_rng(seed=seed)
    t = np.linspace(0, duration_seconds, int(duration_seconds * sample_rate), False)
    audio = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * rng.standard_normal(len(t))
    return audio.astype(np.float32)


def load_fixture_audio(path: Path, *, target_sample_rate: int = DEFAULT_SAMPLE_RATE) -> np.ndarray:
    """Load a 16 kHz mono WAV file as a float32 numpy array.

    The fixture is a real recording (or, in the case of
    ``test_440hz_1s_16k.wav``, a deterministic synthetic tone) — either
    way it is a *stable* signal that does not depend on the global RNG
    state, so the benchmark is reproducible across runs.
    """
    with wave.open(str(path), "rb") as w:
        n_channels = w.getnchannels()
        sample_rate = w.getframerate()
        sampwidth = w.getsampwidth()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)

    if sample_rate != target_sample_rate:
        # The fixture is 16 kHz; the engine expects 16 kHz. We do NOT
        # resample here — if a non-16 kHz fixture is requested, the
        # engine will receive it as-is (matching the production path,
        # which trusts the input sample rate).
        pass

    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map.get(sampwidth)
    if dtype is None:
        raise ValueError(f"Unsupported sample width {sampwidth} bytes in {path}")
    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)
    # Normalize to [-1, 1] based on the source dtype's full scale.
    audio /= float(np.iinfo(dtype).max)
    return audio


def _resolve_fixture(fixture_arg: str | None) -> tuple[np.ndarray, str]:
    """Resolve the ``--fixture`` argument to an audio array + source label.

    Returns ``(audio, source_label)`` where ``source_label`` is one of
    ``"fixture:<path>"``, ``"synthetic"`` (when ``--fixture`` is unset),
    or ``"synthetic(fallback: <reason>)"`` (when the fixture was
    requested but unavailable).
    """
    if fixture_arg is None:
        return generate_test_audio(), "synthetic"

    fixture_path = (PROJECT_ROOT / fixture_arg).resolve() if not Path(fixture_arg).is_absolute() else Path(fixture_arg)
    if not fixture_path.is_file():
        return (
            generate_test_audio(),
            f"synthetic(fallback: fixture {fixture_arg} not found)",
        )
    try:
        audio = load_fixture_audio(fixture_path)
    except Exception as exc:  # noqa: BLE001 — fallback path
        return (
            generate_test_audio(),
            f"synthetic(fallback: failed to load {fixture_arg}: {exc})",
        )
    return audio, f"fixture:{fixture_arg}"


def bench_model_load(model_size: str, device: str) -> dict:
    """Benchmark model loading time. Returns dict with seconds + peak RSS."""
    from voice_typer.server.transcription import TranscriptionEngine

    proc = _try_psutil()
    rss_before = _peak_rss_mb(proc)
    t0 = time.perf_counter()
    _engine = TranscriptionEngine(model_size=model_size, device=device)
    # The load-deferral refactor made ``__init__`` hollow (model weights
    # load lazily on first transcribe) — time the explicit ``load()``
    # too, otherwise this bench measures a no-op constructor and reports
    # a load cost that never happens in production. A load failure (no
    # model downloaded) soft-skips with a ``load_error`` note instead of
    # crashing the bench — mirrors the transcription path's behavior.
    load_error: str | None = None
    try:
        _engine.load()
    except Exception as exc:  # noqa: BLE001 — model-load failure is a soft skip
        load_error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0
    rss_after = _peak_rss_mb(proc)
    result = {
        "model": model_size,
        "device": device,
        "load_seconds": round(elapsed, 4),
        "peak_rss_mb_before": rss_before,
        "peak_rss_mb_after": rss_after,
        "delta_rss_mb": (rss_after - rss_before) if (rss_before is not None and rss_after is not None) else None,
    }
    if load_error is not None:
        result["load_error"] = load_error
    return result


def _percentile(sorted_latencies: list[float], pct: float) -> float:
    """Nearest-rank percentile. ``pct`` in [0, 100].

    Uses the ceil(nearest-rank) definition: p90 of 10 samples is the
    9th value, not the 10th (the old ``int(N*0.9)`` index made p90
    equal the MAXIMUM at the default 10 iterations, overstating the
    tail). For N >= 20 (the new DEFAULT_ITERATIONS) the two definitions
    converge; the nearest-rank form stays correct at small N.
    """
    import math

    if not sorted_latencies:
        return 0.0
    rank = math.ceil(len(sorted_latencies) * pct / 100.0)
    idx = min(len(sorted_latencies) - 1, max(0, rank - 1))
    return sorted_latencies[idx]


def bench_transcription(
    model_size: str,
    device: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    audio: np.ndarray | None = None,
    audio_source: str = "synthetic",
) -> dict:
    """Benchmark transcription latency. Returns stats dict.

    If the model cannot be loaded (not downloaded / faster-whisper
    missing / sandbox without GPU), the returned dict carries
    ``"skipped": True`` and an ``"error"`` string instead of timing
    stats.  The CI ratchet treats a skipped result as a no-op (no
    regression, no improvement) so a missing model on a CI runner does
    not break the build.
    """
    from voice_typer.server.transcription import TranscriptionEngine

    proc = _try_psutil()
    rss_before = _peak_rss_mb(proc)
    try:
        engine = TranscriptionEngine(model_size=model_size, device=device)
        # Production callers go through ``transcribe_with_fallback`` which
        # raises RuntimeError("Model not loaded. Call load() first.") if
        # the model has not been loaded.  The original bench
        # skipped this and crashed on the first iteration — the bench
        # only ran successfully in environments where ``__init__``
        # happened to autoload (it does not).
        engine.load()
    except Exception as exc:  # noqa: BLE001 — model-load failure is a soft skip
        return {
            "model": model_size,
            "device": device,
            "iterations": iterations,
            "audio_source": audio_source,
            "skipped": True,
            "error": f"{type(exc).__name__}: {exc}",
            "peak_rss_mb_before": rss_before,
            "peak_rss_mb_after": _peak_rss_mb(proc),
        }

    if audio is None:
        audio = generate_test_audio(duration_seconds=DEFAULT_DURATION_S)
    duration_s = float(len(audio)) / DEFAULT_SAMPLE_RATE

    latencies: list[float] = []
    word_counts: list[int] = []
    rss_after_each: list[int | None] = []
    # One UNTIMED warm-up iteration: the first call on the CPU path pays
    # one-time engine warm-up (ctranslate2 allocator growth, thread-pool
    # spin-up) that is not representative of steady-state latency.
    # Without it the reported min/median include cold-start cost.
    engine.transcribe_with_fallback(audio)
    for i in range(iterations):
        t0 = time.perf_counter()
        text = engine.transcribe_with_fallback(audio)
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)
        word_counts.append(len(text.split()) if isinstance(text, str) else 0)
        rss_after_each.append(_peak_rss_mb(proc))
        if i < 3 or i == iterations - 1:
            preview = (text or "")[:50]
            print(f"  iteration {i + 1}/{iterations}: {elapsed:.3f}s -> {preview!r}")

    latencies.sort()
    median = latencies[len(latencies) // 2]
    p90 = _percentile(latencies, 90.0)
    p99 = _percentile(latencies, 99.0)
    rss_after = _peak_rss_mb(proc)
    median_words = statistics.median(word_counts) if word_counts else 0
    # Words-per-second = median word count / median latency.  This is the
    # user-facing "how fast does my speech become text" metric.
    wps = (median_words / median) if median > 0 else 0.0
    return {
        "model": model_size,
        "device": device,
        "iterations": iterations,
        "audio_source": audio_source,
        "audio_duration_s": round(duration_s, 3),
        "median": round(median, 4),
        "p90": round(p90, 4),
        "p99": round(p99, 4),
        "min": round(min(latencies), 4),
        "max": round(max(latencies), 4),
        "median_word_count": int(median_words),
        "wps": round(wps, 2),
        "peak_rss_mb_before": rss_before,
        "peak_rss_mb_after": rss_after,
        "delta_rss_mb": (rss_after - rss_before) if (rss_before is not None and rss_after is not None) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Typer transcription benchmark")
    parser.add_argument("--model", default="small.en", help="Model size (tiny.en, small.en, medium.en)")
    parser.add_argument("--device", default="cpu", help="Device (cpu, cuda)")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Number of iterations (default: {DEFAULT_ITERATIONS} — p90 requires >= 10)",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="Only benchmark model load time",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help=(
            f"Path to a 16 kHz mono WAV fixture (default: {DEFAULT_FIXTURE} when set; "
            "unset = synthetic deterministic signal). Falls back to synthetic on "
            "missing/unloadable file."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable (for CI ratchet comparison).",
    )
    args = parser.parse_args()

    if args.load_only:
        load_stats = bench_model_load(args.model, args.device)
        if args.json:
            json.dump({"model_load": load_stats}, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        print("=== Voice Typer Model Load Benchmark ===")
        print(f"Model: {args.model}  Device: {args.device}")
        print(f"  Load time: {load_stats['load_seconds']:.3f}s")
        if load_stats["delta_rss_mb"] is not None:
            print(f"  Peak RSS delta: {load_stats['delta_rss_mb']} MB")
        return 0

    audio, audio_source = _resolve_fixture(args.fixture)
    stats = bench_transcription(
        args.model,
        args.device,
        iterations=args.iterations,
        audio=audio,
        audio_source=audio_source,
    )

    if args.json:
        json.dump({"transcription": stats}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=== Voice Typer Benchmark ===")
    print(f"Model: {stats['model']}")
    print(f"Device: {stats['device']}")
    print(f"Iterations: {stats['iterations']}")
    print(f"Audio source: {stats['audio_source']} ({stats['audio_duration_s']}s)")
    print()
    print("=== Results ===")
    print(f"  Median: {stats['median']:.3f}s")
    print(f"  P90:    {stats['p90']:.3f}s")
    print(f"  P99:    {stats['p99']:.3f}s")
    print(f"  Min:    {stats['min']:.3f}s")
    print(f"  Max:    {stats['max']:.3f}s")
    print(f"  Words-per-second (median): {stats['wps']:.2f}")
    if stats["delta_rss_mb"] is not None:
        print(
            f"  Peak RSS delta: {stats['delta_rss_mb']} MB "
            f"(before={stats['peak_rss_mb_before']} "
            f"after={stats['peak_rss_mb_after']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
