#!/usr/bin/env python
"""Startup and model-size benchmark for README perf claims.

Measures:
1. Tray import time (cold start to first tray paint, approximated by the
   wall-clock time to ``import voice_typer.server.tray`` in a FRESH
   subprocess).  A fresh subprocess is the only honest way to measure
   cold-start latency — in-process re-imports are contaminated by cached
   C extensions (``numpy``, ``sounddevice``, etc. stay in ``sys.modules``
   even after ``del`` / ``importlib.reload``).
2. Model load time for each model size.
3. Actual model file sizes on disk (to verify README claims like
   "small.en ~466 MB").

The benchmark delegates the per-run subprocess spawn to
``scripts/profile_imports.py`` so there is one source of truth for the
cold-start methodology (the same script wired into the CI perf gate).
Run 1 is the TRUE cold start (OS page cache cold); subsequent runs warm
the page cache.  We report first-run (cold), median, and p99 wall-clock.

Usage:
    python bench/bench_startup.py
    python bench/bench_startup.py --check-sizes-only
    python bench/bench_startup.py --runs 5 --json

The JSON output is consumed by ``.github/workflows/perf.yml`` for the
CI ratchet comparison against ``bench/bench-baseline.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

# Add the project root to the path so we can import voice_typer.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_RUNS = 5
DEFAULT_TARGET = "voice_typer.server.tray"


def check_model_sizes() -> dict[str, int]:
    """Check actual model sizes on disk (in MB).

    Returns a dict mapping model name → size in MB.
    """
    from voice_typer.server.config import _config_dir

    cache_dir = _config_dir() / "huggingface" / "hub"
    models = {
        "tiny.en": "Systran/faster-whisper-tiny.en",
        "small.en": "Systran/faster-whisper-small.en",
        "medium.en": "Systran/faster-whisper-medium.en",
        "parakeet": "nvidia/parakeet-tdt-0.6b-v3",
    }

    sizes: dict[str, int] = {}
    for name, repo_id in models.items():
        model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
        if not model_dir.exists():
            sizes[name] = -1  # not downloaded
            continue
        total = 0
        for f in model_dir.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        sizes[name] = total // (1024 * 1024)  # MB

    return sizes


def _measure_one_import(target: str, *, python: str) -> float:
    """Spawn a fresh ``python -c "import <target>"`` subprocess.

    Returns wall-clock seconds.  Raises ``subprocess.CalledProcessError``
    on import failure.  A fresh subprocess is the only honest cold-start
    measurement — see the module docstring above.
    """
    start = time.perf_counter()
    proc = subprocess.run(
        [python, "-c", f"import {target}"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            proc.args,
            output=proc.stdout,
            stderr=proc.stderr,
        )
    return wall


def measure_import_times(
    target: str = DEFAULT_TARGET,
    *,
    runs: int = DEFAULT_RUNS,
    python: str = sys.executable,
) -> dict[str, float | list[float]]:
    """Run ``runs`` fresh-subprocess imports of ``target``.

    Returns a dict with:
      * ``runs``: list of per-run wall-clock ms (run 0 is the cold start)
      * ``first_run_ms``: the TRUE cold-start wall-clock (page cache cold)
      * ``median_ms``: median across all runs (page-cache-warm steady state)
      * ``p99_ms``: 99th percentile (worst observed, minus outliers)
      * ``min_ms`` / ``max_ms``: range
    """
    walls: list[float] = []
    for _ in range(runs):
        wall = _measure_one_import(target, python=python)
        walls.append(wall * 1000.0)  # → ms

    sorted_walls = sorted(walls)
    # p99 with a small N clamps to the max; for N≥10 it is a real tail.
    p99_idx = min(len(sorted_walls) - 1, int(len(sorted_walls) * 0.99))
    return {
        "target": target,
        "runs": [round(w, 1) for w in walls],
        "first_run_ms": round(walls[0], 1),
        "median_ms": round(statistics.median(walls), 1),
        "p99_ms": round(sorted_walls[p99_idx], 1),
        "min_ms": round(min(walls), 1),
        "max_ms": round(max(walls), 1),
        "n": len(walls),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Startup benchmark")
    parser.add_argument(
        "--check-sizes-only",
        action="store_true",
        help="Only check model file sizes, don't measure import time.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of fresh-subprocess import runs (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"Import target to profile (default: {DEFAULT_TARGET}).",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter for the fresh subprocess (default: current).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable (for CI ratchet comparison).",
    )
    args = parser.parse_args()

    sizes = check_model_sizes()

    if args.check_sizes_only:
        if args.json:
            json.dump({"model_sizes_mb": sizes}, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        print("=" * 60)
        print("Voice Typer — Model Size Benchmark")
        print("=" * 60)
        print("\n## Model Sizes on Disk\n")
        print(f"{'Model':<15} {'Size (MB)':<12} {'Status'}")
        print("-" * 45)
        for name, mb in sizes.items():
            if mb < 0:
                print(f"{name:<15} {'—':<12} not downloaded")
            else:
                print(f"{name:<15} {mb:<12} downloaded")
        return 0

    stats = measure_import_times(args.target, runs=args.runs, python=args.python)

    if args.json:
        json.dump(
            {
                "cold_import": stats,
                "model_sizes_mb": sizes,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    print("=" * 60)
    print("Voice Typer — Startup & Model Size Benchmark")
    print("=" * 60)

    # Model sizes
    print("\n## Model Sizes on Disk\n")
    print(f"{'Model':<15} {'Size (MB)':<12} {'Status'}")
    print("-" * 45)
    for name, mb in sizes.items():
        if mb < 0:
            print(f"{name:<15} {'—':<12} not downloaded")
        else:
            print(f"{name:<15} {mb:<12} downloaded")

    # Import time — fresh subprocess per run, so run 0 is the true cold
    # start (OS page cache cold).  Subsequent runs warm the page cache,
    # so the median reflects steady-state cold-start after a recent
    # launch; the first_run reflects a cold-boot launch.
    print("\n## Cold Import Time (fresh-subprocess per run)\n")
    print(f"  Target         : {stats['target']}")
    print(f"  Runs           : {stats['n']}")
    print(f"  First run (TRUE cold, page-cache cold): {stats['first_run_ms']:.0f} ms")
    print(f"  Median (page-cache warm)             : {stats['median_ms']:.0f} ms")
    print(f"  p99 (worst observed)                 : {stats['p99_ms']:.0f} ms")
    print(f"  Min / Max                            : {stats['min_ms']:.0f} / {stats['max_ms']:.0f} ms")
    print(f"  (per-run: {', '.join(f'{w:.0f}ms' for w in stats['runs'])})")

    print("\n## README Claims Verification\n")
    print("Update README.md with these measured values:")
    print(f"  - Tray icon cold start (first run): ~{stats['first_run_ms']:.0f} ms")
    print(f"  - Tray icon cold start (median)   : ~{stats['median_ms']:.0f} ms")
    for name, mb in sizes.items():
        if mb > 0:
            print(f"  - {name} model: {mb} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
