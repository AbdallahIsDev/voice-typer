#!/usr/bin/env python
"""End-to-end dictation TEXT-path benchmark.

Measures the per-dictation text pipeline every dictation runs through
after ASR returns, in production order:

    1. text cleanup       — ``text_cleanup.clean_transcribed_text``
    2. vocabulary apply   — ``VocabularyManager.apply_to_text``

These two stages run on EVERY dictation regardless of engine; they are
the always-measurable core of the dictation latency budget. The
transcription stage itself is OPTIONAL (``--with-engine``): loading a
model needs downloaded weights, which CI perf runners do not have —
when enabled and available, its latency is reported separately and
added to the total so a local run shows the full dictation cost.

Deterministic: fixed synthetic transcripts, a seeded temp vocabulary
store, an untimed warm-up pass, nearest-rank percentiles. No network
I/O, no clipboard, no audio device.

Usage:
    python bench/bench_dictation_e2e.py
    python bench/bench_dictation_e2e.py --json
    python bench/bench_dictation_e2e.py --iterations 200
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_ITERATIONS = 100

# Representative dictated transcripts (production shapes): a mid-length
# English dictation with correction targets, a longer multi-sentence one,
# and a CJK one (the vocabulary pass tokenizes CJK differently).
_TRANSCRIPTS = (
    "so i was thinking that maybe we could you know move the meeting "
    "to teh afternoon because seperate calendars are hard to sync "
    "recieve the file tommorow and definately confirm before friday",
    "The quick brown fox jumps over the lazy dog. Pack my box with five "
    "dozen liquor jugs. How vexingly quick daft zebras jump when "
    "sphinx of black quartz judges my vow again and again.",
    "今天下午三点开会 讨论新的语音输入功能 请提前准备材料 谢谢",
)

# Seed corrections: enough entries to exercise the combined-alternation
# phrase pass and the word-level dict pass realistically.
_SEED_PHRASES = (
    ("teh", "the"),
    ("recieve", "receive"),
    ("seperate", "separate"),
    ("tommorow", "tomorrow"),
    ("definately", "definitely"),
    ("occured", "occurred"),
    ("untill", "until"),
    ("wich", "which"),
    ("adress", "address"),
    ("becuase", "because"),
    ("move the meeting", "reschedule the meeting"),
    ("hard to sync", "hard to synchronize"),
    ("confirm before friday", "confirm before Friday"),
    ("prepare materials", "prepare the materials"),
    ("advance preparation", "advance prep"),
    ("语音输入", "voice input"),
    ("提前准备材料", "prepare materials in advance"),
    ("下午三点开会", "meeting at three in the afternoon"),
)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile (``pct`` in [0, 100])."""
    if not sorted_vals:
        return 0.0
    rank = math.ceil(len(sorted_vals) * pct / 100.0)
    idx = min(len(sorted_vals) - 1, max(0, rank - 1))
    return sorted_vals[idx]


def _stats(latencies_us: list[float]) -> dict[str, float]:
    if not latencies_us:
        return {"n": 0, "p50_us": 0.0, "p99_us": 0.0, "max_us": 0.0, "mean_us": 0.0}
    ordered = sorted(latencies_us)
    return {
        "n": len(ordered),
        "p50_us": round(_percentile(ordered, 50.0), 2),
        "p99_us": round(_percentile(ordered, 99.0), 2),
        "max_us": round(ordered[-1], 2),
        "mean_us": round(statistics.mean(ordered), 2),
    }


def _build_vocabulary(tmp_dir: Path):
    """A seeded VocabularyManager over an isolated temp store.

    The bundled-corrections file is pointed at a non-existent path so
    the seed set above is the ENTIRE vocabulary — the bench never reads
    or writes the user's real config dir.
    """
    from voice_typer.server.vocabulary import VocabularyManager

    vm = VocabularyManager(
        config_dir=tmp_dir,
        bundled_path=tmp_dir / "nonexistent-bundled.json",
    )
    for wrong, right in _SEED_PHRASES:
        vm.add_phrase("phrase_corrections", wrong, right)
    return vm


def bench_text_pipeline(iterations: int) -> dict:
    """Measure cleanup → vocabulary per synthetic dictation."""
    from voice_typer.server.text_cleanup import clean_transcribed_text

    with tempfile.TemporaryDirectory(prefix="vt-bench-dictation-") as tmp:
        vm = _build_vocabulary(Path(tmp))

        cleanup_us: list[float] = []
        vocab_us: list[float] = []
        total_us: list[float] = []

        def _one() -> None:
            t0 = time.perf_counter_ns()
            for raw in _TRANSCRIPTS:
                # Production order: cleanup first (skip_corrections=True —
                # vocabulary applies the corrections right after, exactly
                # like dictation_pipeline/text_steps.py).
                cleaned = clean_transcribed_text(raw, skip_corrections=True)
                t1 = time.perf_counter_ns()
                vm.apply_to_text(cleaned)
                t2 = time.perf_counter_ns()
                cleanup_us.append((t1 - t0) / 1000.0)
                vocab_us.append((t2 - t1) / 1000.0)
                total_us.append((t2 - t0) / 1000.0)
                t0 = t2

        # Untimed warm-up: the first pass pays lazy imports + the initial
        # regex compile + OS page-cache misses — not steady-state cost.
        _one()
        cleanup_us.clear()
        vocab_us.clear()
        total_us.clear()

        for _ in range(iterations):
            _one()

    return {
        "iterations": iterations,
        "transcripts_per_iteration": len(_TRANSCRIPTS),
        "cleanup": _stats(cleanup_us),
        "vocabulary": _stats(vocab_us),
        "total": _stats(total_us),
    }


def _bench_transcription_stage(model_size: str, device: str) -> dict:
    """Optional: transcription latency on synthetic audio.

    Best-effort — any failure (no model downloaded, no engine backend)
    degrades to a ``skipped`` note instead of failing the bench.
    """
    try:
        from voice_typer.server.transcription import TranscriptionEngine
    except Exception as exc:  # pragma: no cover - environment-dependent
        return {"skipped": f"engine import failed: {exc}"}
    try:
        engine = TranscriptionEngine(model_size=model_size, device=device)
        engine.load()
    except Exception as exc:  # pragma: no cover - environment-dependent
        return {"skipped": f"engine load failed: {exc}"}

    # Reuse the transcription bench's synthetic-audio generator so the
    # input is identical across benches (byte-reproducible, seeded).
    try:
        from bench_transcription import generate_test_audio  # type: ignore[import-not-found]

        audio = generate_test_audio()
    except Exception:
        import numpy as np

        rng = np.random.default_rng(0xA4A4)
        audio = (rng.standard_normal(16000 * 5) * 0.05).astype("float32")

    latencies_us: list[float] = []
    try:
        engine.transcribe_with_fallback(audio)  # untimed warm-up
        for _ in range(5):
            t0 = time.perf_counter_ns()
            engine.transcribe_with_fallback(audio)
            latencies_us.append((time.perf_counter_ns() - t0) / 1000.0)
    except Exception as exc:  # pragma: no cover - environment-dependent
        return {"skipped": f"transcribe failed: {exc}"}

    return {"model": model_size, "device": device, "latency": _stats(latencies_us)}


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end dictation text-path benchmark")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Timed iterations per transcript (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument(
        "--with-engine", action="store_true", help="Also time real transcription (requires a downloaded model)"
    )
    parser.add_argument("--model", default="tiny", help="Model size for --with-engine (default: tiny)")
    parser.add_argument("--device", default="cpu", help="Device for --with-engine (default: cpu)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    args = parser.parse_args()

    text_pipeline = bench_text_pipeline(args.iterations)
    if args.with_engine:
        transcription = _bench_transcription_stage(args.model, args.device)
    else:
        transcription = {"skipped": "not requested (use --with-engine)"}

    results = {"text_pipeline": text_pipeline, "transcription": transcription}

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 72)
    print("Voice Typer — Dictation End-to-End Text-Path Benchmark")
    print("=" * 72)
    tp = results["text_pipeline"]
    print(f"\n## Text Pipeline ({tp['iterations']} iterations × {tp['transcripts_per_iteration']} transcripts)")
    for stage in ("cleanup", "vocabulary", "total"):
        s = tp[stage]
        print(f"  {stage:<12}: p50={s['p50_us']:>9.2f}  p99={s['p99_us']:>9.2f}  max={s['max_us']:>9.2f} µs")
    tr = results["transcription"]
    if "skipped" in tr:
        print(f"\n## Transcription: SKIPPED ({tr['skipped']})")
    else:
        s = tr["latency"]
        print(f"\n## Transcription ({tr['model']}/{tr['device']}): p50={s['p50_us']:.0f} p99={s['p99_us']:.0f} µs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
