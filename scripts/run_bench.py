#!/usr/bin/env python3
"""Run every ``bench/bench_*.py`` script with ``--json`` and concatenate
the outputs into ``bench-current.json``.

Backing driver for ``make bench``: the CI perf-ratchet input consumed by
``.github/workflows/perf.yml`` (compared against
``bench/bench-baseline.json``). Discovery is glob-based so a new
``bench/bench_*.py`` file is picked up automatically; there is no
hand-maintained script list to keep in sync with the directory.

Each bench script runs in a fresh interpreter
(``<sys.executable> <script> --json``). A script that is missing or exits
non-zero contributes a skip marker instead of failing the whole run —
CI runners legitimately skip benches whose preconditions are absent
(e.g. a transcription bench with no downloaded model).

Output contract (unchanged from the previous inline ``make bench``
heredoc): ``bench-current.json`` is a JSON object::

    {"version": 1,
     "generated_at": "<UTC ISO-8601, seconds precision, Z suffix>",
     "results": {"<script stem>": <bench JSON output or skip marker>}}

``results`` is keyed by script stem; the perf-ratchet consumer resolves
metrics by dotted key, so it is order-independent (the map is emitted in
sorted-by-name order for stable diffs).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "bench"
OUTPUT_PATH = REPO_ROOT / "bench-current.json"
BASELINE_PATH = "bench/bench-baseline.json"

_Runner = Callable[..., subprocess.CompletedProcess]


def _run_one(script: Path, runner: _Runner = subprocess.run) -> dict[str, Any]:
    """Run a single bench script with ``--json``; return its results entry.

    Success → the script's parsed JSON. Missing file → ``missing``. Non-zero
    exit → the last non-empty stderr line (or ``failed`` when stderr is
    empty).
    """
    if not script.is_file():
        return {"skipped": True, "error": "missing"}
    result = runner(
        [sys.executable, str(script), "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    stderr = result.stderr.strip()
    if stderr:
        return {"skipped": True, "error": stderr.splitlines()[-1]}
    return {"skipped": True, "error": "failed"}


def build_bench_output(bench_dir: Path, runner: _Runner = subprocess.run) -> dict[str, Any]:
    """Run every ``bench_*.py`` in *bench_dir*; return the output document."""
    results: dict[str, Any] = {}
    for script in sorted(bench_dir.glob("bench_*.py")):
        results[script.stem] = _run_one(script, runner=runner)
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "results": results,
    }


def main() -> int:
    out = build_bench_output(BENCH_DIR)
    OUTPUT_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.name} -> compare against {BASELINE_PATH} (see .github/workflows/perf.yml)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
