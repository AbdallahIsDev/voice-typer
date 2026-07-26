#!/usr/bin/env python
"""Benchmark the streaming text assembler hot path.

:class:`voice_typer.server.streaming.StreamingTextAssembler.add_words`
is called for every streaming transcription chunk (typically 5-20 words
per call, every ~500ms). The assembler deduplicates words by timestamp,
maintains a sorted commit horizon, and rebuilds the committed-text
cache lazily on read.

This benchmark measures the steady-state throughput of add_words +
committed_text read across a long dictation session (1K-10K words). It
catches regressions in:

  * The deque-based word storage (AUDIO-019 eviction).
  * The seen-timestamps set (RACE-031 contention approximation).
  * The cached committed_text property (PERF-018 invalidation).
  * The word-key index for overlap dedup (PERF-NEW-004).

Usage:
    python bench/bench_streaming.py
    python bench/bench_streaming.py --words 10000 --chunk-size 20
    python bench/bench_streaming.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_TOTAL_WORDS = 5000
DEFAULT_CHUNK_SIZE = 15  # typical streaming chunk: ~15 words / 500ms


def _make_words(n: int, start_offset_s: float = 0.0):
    """Generate ``n`` synthetic WordTiming objects with non-overlapping
    timestamps (so the dedup path doesn't short-circuit them all)."""
    from voice_typer.server.streaming import WordTiming

    # 200ms per word — matches a moderate speaking rate.
    per_word_s = 0.2
    return [
        WordTiming(
            word=f"word{i}",
            start_seconds=start_offset_s + i * per_word_s,
            end_seconds=start_offset_s + (i + 1) * per_word_s - 0.01,
        )
        for i in range(n)
    ]


def bench_streaming_assembler(total_words: int, chunk_size: int) -> dict:
    """Benchmark StreamingTextAssembler.add_words + committed_text read."""
    from voice_typer.server.streaming import StreamingTextAssembler

    asm = StreamingTextAssembler()
    # Commit horizon advances with each chunk so most words are
    # committed (matches production: the right_guard is small relative
    # to the audio already processed).
    all_words = _make_words(total_words)
    chunks = [all_words[i : i + chunk_size] for i in range(0, total_words, chunk_size)]

    per_chunk_us: list[float] = []
    commit_read_us: list[float] = []
    horizon_s = 0.0
    for chunk in chunks:
        if chunk:
            horizon_s = chunk[-1].end_seconds - 0.05  # 50ms right guard
        t0 = time.perf_counter_ns()
        asm.add_words(chunk, commit_horizon_seconds=horizon_s)
        per_chunk_us.append((time.perf_counter_ns() - t0) / 1000.0)
        # Read committed_text every chunk — matches the streaming
        # callback that surfaces partial transcripts to the UI.
        t0 = time.perf_counter_ns()
        _ = asm.committed_text
        commit_read_us.append((time.perf_counter_ns() - t0) / 1000.0)

    per_chunk_us.sort()
    commit_read_us.sort()
    return {
        "total_words": total_words,
        "chunk_size": chunk_size,
        "n_chunks": len(chunks),
        "add_words_p50_us": round(per_chunk_us[len(per_chunk_us) // 2], 1),
        "add_words_p99_us": round(per_chunk_us[int(len(per_chunk_us) * 0.99)], 1),
        "add_words_max_us": round(per_chunk_us[-1], 1),
        "committed_text_p50_us": round(commit_read_us[len(commit_read_us) // 2], 1),
        "committed_text_p99_us": round(commit_read_us[int(len(commit_read_us) * 0.99)], 1),
        "committed_text_max_us": round(commit_read_us[-1], 1),
        # Streaming budget: chunk arrives every ~500ms; add_words +
        # committed_text together must be well under that. 1% of budget
        # is a healthy ceiling.
        "budget_500ms_margin_pct": round(
            (500_000 - per_chunk_us[int(len(per_chunk_us) * 0.99)] - commit_read_us[int(len(commit_read_us) * 0.99)])
            / 500_000
            * 100.0,
            1,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Streaming assembler benchmark")
    parser.add_argument("--words", type=int, default=DEFAULT_TOTAL_WORDS)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = bench_streaming_assembler(args.words, args.chunk_size)

    if args.json:
        json.dump([result], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 72)
    print("Voice Typer — Streaming Text Assembler Benchmark")
    print("=" * 72)
    print()
    print(f"  Total words       : {result['total_words']}")
    print(f"  Chunk size        : {result['chunk_size']} words (~500ms each)")
    print(f"  Chunks measured   : {result['n_chunks']}")
    print()
    print("  add_words (per chunk):")
    print(
        f"    p50 / p99 / max : {result['add_words_p50_us']} / "
        f"{result['add_words_p99_us']} / {result['add_words_max_us']} µs"
    )
    print("  committed_text (per read):")
    print(
        f"    p50 / p99 / max : {result['committed_text_p50_us']} / "
        f"{result['committed_text_p99_us']} / {result['committed_text_max_us']} µs"
    )
    print()
    print(
        f"  500ms chunk margin: {result['budget_500ms_margin_pct']}% "
        f"({'PASS' if result['budget_500ms_margin_pct'] > 0 else 'FAIL'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
