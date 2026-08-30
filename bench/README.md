# Voice Typer — Benchmarks

**Status: Benchmark harness available — run on your hardware (cold import is tens of milliseconds and depends on hardware/OS — ≈ 84 ms observed on Windows; the CI-tracked worker-startup metric runs ≈ 0.9 s first-run / ≈ 0.3 s median on CI runners, target ≤ 600 ms first-run (cold); model sizes verified: tiny.en 74 MB, small.en 463 MB, parakeet 2393 MB).**

The benchmark harness (`bench_transcription.py`) is ready for use. Run it
to generate measurements for your hardware configuration. The startup
claims in the README are now backed by `bench/bench_startup.py` output
(cold-import latency and on-disk model sizes).

## Running the benchmark

```bash
# Transcription latency (default: small.en on CPU, 5 iterations)
python bench/bench_transcription.py --model small.en --device cpu

# Model load time only
python bench/bench_transcription.py --model tiny.en --device cpu --load-only

# GPU transcription
python bench/bench_transcription.py --model small.en --device cuda --iterations 10
```

## Metrics measured

1. **Full transcription latency**: time from audio input to complete text
2. **Model load time**: cold-start time for each model size

## Planned metrics

3. **First-token latency**: time from hotkey release to first character
   appearing in the target window.
4. **Streaming partial latency**: time from audio chunk to partial
   transcription appearing (if streaming is enabled).
5. **Memory usage**: peak RSS during transcription.

## Methodology

- Run on a dedicated machine (no other CPU-intensive apps).
- Use a fixed 10-second audio sample at 16 kHz mono.
- Repeat 10 times, report median + p90.
- Measure on both CPU and GPU (if available).
- Report Python version, OS, and hardware specs.

## pytest-benchmark integration

To add automated regression benchmarks:

```bash
pip install pytest-benchmark
```

Then create `tests/bench/` with fixtures that use the `benchmark` fixture
to track performance across commits.
