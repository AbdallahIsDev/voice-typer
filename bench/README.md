# Voice Typer — Benchmarks

**Status: Not yet measured.**

The following metrics are planned for measurement but have not been
formally benchmarked yet.  The claims in older README versions
("300-450 ms first-token latency", "sub-second transcription") were
aspirational, not measured.

## Planned benchmark harness

```bash
# Future: run the benchmark
python bench/bench_transcription.py --model small.en --device cpu
```

## Metrics to measure

1. **First-token latency**: time from hotkey release to first character
   appearing in the target window.
2. **Full transcription latency**: time from hotkey release to last
   character pasted.
3. **Streaming partial latency**: time from audio chunk to partial
   transcription appearing (if streaming is enabled).
4. **Model load time**: cold-start time for each model size.
5. **Memory usage**: peak RSS during transcription.

## Methodology

- Run on a dedicated machine (no other CPU-intensive apps).
- Use a fixed 10-second audio sample at 16 kHz mono.
- Repeat 10 times, report median + p90.
- Measure on both CPU and GPU (if available).
- Report Python version, OS, and hardware specs.
