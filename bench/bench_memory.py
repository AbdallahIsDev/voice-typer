#!/usr/bin/env python
"""Memory footprint benchmark.

Measures peak RSS for the three memory-sensitive phases of the Voice
Typer lifecycle:

1. **Cold import** — peak RSS after ``import voice_typer.server.tray``
   in a fresh subprocess.  This is the "how much RAM does the tray
   process consume at idle" number.  A regression here means a new
   heavy dependency was added to the import graph.

2. **Model load** — peak RSS after ``TranscriptionEngine.load()``.  In
   environments where the model is unavailable (sandbox without
   HuggingFace consent), the bench records the import-only RSS and
   marks the model-load phase as skipped.

3. **Sustained transcription** — peak RSS after 60s of
   ``transcribe_with_fallback`` calls on a deterministic audio signal.
   This catches leaks in the transcription hot path (e.g. a buffer that
   grows per call).  In environments without a loaded model, the bench
   falls back to a sustained ``FilterChain.process`` loop on synthetic
   audio so the bench still produces a meaningful "sustained load" RSS.

All measurements use ``psutil.Process().memory_info().peak_rss`` where
available (Windows / macOS); on Linux the high-water mark RSS is read
from ``/proc/<pid>/status`` (field ``VmHWM:``) because psutil does not
expose it directly.  The cold-import measurement runs in a SUBPROCESS
so the parent's RSS (which includes the bench harness itself) does not
pollute the measurement.

Usage:
    python bench/bench_memory.py
    python bench/bench_memory.py --json
    python bench/bench_memory.py --duration 30
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DURATION_S = 60.0
DEFAULT_IMPORT_TARGET = "voice_typer.server.tray"


def _peak_rss_mb() -> int | None:
    """Return current peak RSS in MB, or ``None`` if unavailable.

    On Linux, ``psutil`` does not expose ``peak_rss`` directly — we
    parse ``VmHWM`` from ``/proc/<pid>/status``.  On Windows / macOS,
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
    try:
        status_path = Path(f"/proc/{proc.pid}/status")  # type: ignore[possibly-undefined]
        if status_path.is_file():
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001 — best-effort
        pass
    try:
        return int(proc.memory_info().rss / (1024 * 1024))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — best-effort
        return None


def _measure_cold_import_in_subprocess(target: str, *, python: str) -> dict:
    """Spawn a fresh subprocess that imports ``target`` and reports RSS.

    The subprocess prints ``PEAK_RSS_MB=<int>`` to stdout just before
    exiting.  We parse that line so the parent process's RSS (which
    includes the bench harness + numpy + psutil) does not pollute the
    measurement.
    """
    script = (
        "import sys, os, time;\n"
        f"import {target};\n"
        # Give the OS a beat to settle VmHWM after the import.
        "time.sleep(0.05);\n"
        "try:\n"
        "    import psutil;\n"
        "    mi = psutil.Process().memory_info();\n"
        "    peak = getattr(mi, 'peak_rss', None);\n"
        "    if peak is None:\n"
        "        # Linux fallback: /proc/<pid>/status VmHWM\n"
        "        peak = None;\n"
        "        status_path = f'/proc/{os.getpid()}/status';\n"
        "        if os.path.isfile(status_path):\n"
        "            for line in open(status_path):\n"
        "                if line.startswith('VmHWM:'):\n"
        "                    peak = int(line.split()[1]) * 1024;\n"
        "                    break;\n"
        "    if peak is None:\n"
        "        peak = mi.rss;\n"
        "    print(f'PEAK_RSS_MB={int(peak / (1024 * 1024))}');\n"
        "except Exception as e:\n"
        "    print(f'PEAK_RSS_MB_ERROR={e}', file=sys.stderr);\n"
        "    sys.exit(1);\n"
    )
    proc = subprocess.run(
        [python, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if proc.returncode != 0:
        return {
            "target": target,
            "skipped": True,
            "error": proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "subprocess failed",
        }
    # Parse "PEAK_RSS_MB=<int>" from stdout.
    for line in proc.stdout.splitlines():
        if line.startswith("PEAK_RSS_MB="):
            try:
                mb = int(line.split("=", 1)[1])
                return {"target": target, "peak_rss_mb": mb}
            except ValueError:
                continue
    return {
        "target": target,
        "skipped": True,
        "error": f"could not parse PEAK_RSS_MB from stdout: {proc.stdout!r}",
    }


def bench_cold_import(target: str = DEFAULT_IMPORT_TARGET, *, python: str = sys.executable) -> dict:
    """Measure peak RSS for a cold ``import <target>`` in a subprocess."""
    return {
        "name": "cold_import",
        **_measure_cold_import_in_subprocess(target, python=python),
    }


def bench_model_load(model_size: str = "small.en", device: str = "cpu") -> dict:
    """Measure peak RSS for ``TranscriptionEngine.load()``.

    If the model is unavailable (ConsentRequiredError, missing
    faster-whisper, etc.), the bench records the post-import RSS and
    marks the load phase as skipped.
    """
    rss_before = _peak_rss_mb()
    try:
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size=model_size, device=device)
        engine.load()
        rss_after = _peak_rss_mb()
        return {
            "name": "model_load",
            "model": model_size,
            "device": device,
            "peak_rss_mb_before": rss_before,
            "peak_rss_mb_after": rss_after,
            "delta_rss_mb": (rss_after - rss_before) if (rss_before is not None and rss_after is not None) else None,
            "skipped": False,
        }
    except Exception as exc:  # noqa: BLE001 — soft skip on missing model
        rss_after = _peak_rss_mb()
        return {
            "name": "model_load",
            "model": model_size,
            "device": device,
            "peak_rss_mb_before": rss_before,
            "peak_rss_mb_after": rss_after,
            "skipped": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


def bench_sustained_transcription(duration_seconds: float) -> dict:
    """Measure peak RSS during ``duration_seconds`` of sustained load.

    If a model is loadable, runs ``transcribe_with_fallback`` in a
    loop.  Otherwise falls back to a ``FilterChain.process`` loop on
    synthetic audio — the chain is always available (it degrades
    gracefully when scipy / RNNoise are missing) and exercises a
    comparable sustained-load memory profile (audio buffers + filter
    state).

    The ``duration_seconds`` window starts AFTER setup completes (model
    load or chain construction) so the measurement reflects steady-state
    load, not setup cost.
    """
    rss_before = _peak_rss_mb()
    mode: str = "transcription"
    n_iters = 0

    # Try the transcription path first.
    engine = None
    audio = None
    try:
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        engine.load()
        import numpy as np

        rng = np.random.default_rng(seed=0xA4A4)
        audio = (0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 5.0, 5 * 16000, False))
                 + 0.1 * rng.standard_normal(5 * 16000)).astype(np.float32)
    except Exception:  # noqa: BLE001 — fall back to FilterChain
        mode = "filter_chain_fallback"
        engine = None
        audio = None

    # Try the FilterChain fallback path if transcription setup failed.
    chain = None
    chunk = None
    if engine is None:
        try:
            from voice_typer.server.audio_chain_builder import build_chain
            from voice_typer.server.config import Config

            cfg = Config()
            try:
                import scipy  # noqa: F401

                scipy_available = True
            except ImportError:
                scipy_available = False
                for flag in (
                    "noise_filter_highpass",
                    "noise_filter_notch",
                    "noise_filter_eq",
                    "noise_filter_compressor",
                    "noise_filter_limiter",
                ):
                    setattr(cfg, flag, False)
            chain = build_chain(cfg, sample_rate=16000)
            import numpy as np

            rng = np.random.default_rng(seed=0xA4A4)
            chunk = np.zeros(512, dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 — both paths failed
            rss_after = _peak_rss_mb()
            return {
                "name": "sustained_load",
                "duration_s": duration_seconds,
                "mode": "failed",
                "iterations": n_iters,
                "peak_rss_mb_before": rss_before,
                "peak_rss_mb_after": rss_after,
                "skipped": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

    # Steady-state load loop.  The deadline is computed HERE (after all
    # setup) so the measurement reflects steady-state, not setup cost.
    import numpy as np  # type: ignore[no-redef]

    rng = np.random.default_rng(seed=0xA4A4)
    deadline = time.perf_counter() + duration_seconds
    if engine is not None and audio is not None:
        while time.perf_counter() < deadline:
            engine.transcribe_with_fallback(audio)
            n_iters += 1
    elif chain is not None and chunk is not None:
        while time.perf_counter() < deadline:
            chunk[:] = (0.4 * np.sin(2 * np.pi * 440 * np.arange(512, dtype=np.float32) / 16000.0)
                        + 0.05 * rng.standard_normal(512)).astype(np.float32)
            chain.process(chunk, 16000)
            n_iters += 1

    rss_after = _peak_rss_mb()
    return {
        "name": "sustained_load",
        "duration_s": duration_seconds,
        "mode": mode,
        "iterations": n_iters,
        "peak_rss_mb_before": rss_before,
        "peak_rss_mb_after": rss_after,
        "delta_rss_mb": (rss_after - rss_before) if (rss_before is not None and rss_after is not None) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Memory footprint benchmark")
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Sustained-load duration in seconds (default: {DEFAULT_DURATION_S}).",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_IMPORT_TARGET,
        help=f"Import target for the cold-import bench (default: {DEFAULT_IMPORT_TARGET}).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    args = parser.parse_args()

    results = {
        "cold_import": bench_cold_import(args.target),
        "model_load": bench_model_load(),
        "sustained_load": bench_sustained_transcription(args.duration),
    }

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 72)
    print("Voice Typer — Memory Footprint Benchmark")
    print("=" * 72)

    ci = results["cold_import"]
    print(f"\n## Cold Import ({ci['target']})")
    if ci.get("skipped"):
        print(f"  SKIPPED: {ci.get('error', 'unknown')}")
    else:
        print(f"  Peak RSS: {ci['peak_rss_mb']} MB")

    ml = results["model_load"]
    print(f"\n## Model Load ({ml['model']} / {ml['device']})")
    if ml.get("skipped"):
        print(f"  SKIPPED: {ml.get('error', 'unknown')}")
        print(f"  RSS before/after: {ml['peak_rss_mb_before']} / {ml['peak_rss_mb_after']} MB")
    else:
        print(f"  Peak RSS before/after: {ml['peak_rss_mb_before']} / {ml['peak_rss_mb_after']} MB")
        print(f"  Delta: {ml['delta_rss_mb']} MB")

    sl = results["sustained_load"]
    print(f"\n## Sustained Load ({sl['duration_s']}s, mode={sl['mode']})")
    if sl.get("skipped"):
        print(f"  SKIPPED: {sl.get('error', 'unknown')}")
    else:
        print(f"  Iterations: {sl['iterations']}")
        print(f"  Peak RSS before/after: {sl['peak_rss_mb_before']} / {sl['peak_rss_mb_after']} MB")
        print(f"  Delta: {sl['delta_rss_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
