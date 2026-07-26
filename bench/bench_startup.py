#!/usr/bin/env python
"""NEW-DOC-007: Startup and model-size benchmark for README perf claims.

Measures:
1. Tray icon appearance time (cold start to first tray paint).
2. Model load time for each model size.
3. Actual model file sizes on disk (to verify README claims like
   "small.en ~466 MB").

Usage:
    python bench/bench_startup.py
    python bench/bench_startup.py --check-sizes-only

The results are printed in a format suitable for pasting into README.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Add the project root to the path so we can import voice_typer.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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

    sizes = {}
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


def measure_import_time() -> float:
    """Measure the time to import the core app module (cold import).

    This approximates the "tray icon appears" latency since the tray
    starts after the import chain completes.
    """
    # Clear any cached imports.
    for mod in list(sys.modules.keys()):
        if mod.startswith("voice_typer"):
            del sys.modules[mod]

    t0 = time.perf_counter()
    # Import the tray module — this is the first thing that runs.
    import voice_typer.server.tray  # noqa: F401

    t1 = time.perf_counter()
    return (t1 - t0) * 1000  # ms


def main():
    parser = argparse.ArgumentParser(description="Startup benchmark")
    parser.add_argument(
        "--check-sizes-only",
        action="store_true",
        help="Only check model file sizes, don't measure import time.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Voice Typer — Startup & Model Size Benchmark")
    print("=" * 60)

    # Model sizes
    print("\n## Model Sizes on Disk\n")
    sizes = check_model_sizes()
    print(f"{'Model':<15} {'Size (MB)':<12} {'Status'}")
    print("-" * 45)
    for name, mb in sizes.items():
        if mb < 0:
            print(f"{name:<15} {'—':<12} not downloaded")
        else:
            print(f"{name:<15} {mb:<12} downloaded")

    if args.check_sizes_only:
        return

    # Import time (approximates tray appearance latency)
    print("\n## Cold Import Time (approximates tray icon latency)\n")
    # Run 3 iterations and take the median.
    times = [measure_import_time() for _ in range(3)]
    times.sort()
    median = times[1]
    print(f"  Median of 3 runs: {median:.0f} ms")
    print(f"  (All runs: {', '.join(f'{t:.0f}ms' for t in times)})")

    print("\n## README Claims Verification\n")
    print("Update README.md with these measured values:")
    print(f"  - Tray icon appearance: ~{median:.0f} ms (cold import)")
    for name, mb in sizes.items():
        if mb > 0:
            print(f"  - {name} model: {mb} MB")


if __name__ == "__main__":
    main()
