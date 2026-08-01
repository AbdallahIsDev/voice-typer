#!/usr/bin/env python3
"""Reusable import-time profiler (cold-start measurement).

(session-1, Medium, Documentation): ``bench/COLDSTART_REPORT.md``
referenced this script in its "Artifacts" section (§7) but the file was
missing from the repository, so readers could not reproduce the
"first-run cold start" numbers cited in the report. This implementation
restores the artifact so the cold-start methodology is reproducible.

Design (mirrors the contract documented in
``bench/COLDSTART_REPORT.md`` §2.1):

* Spawns ``python -X importtime -c "import <target>"`` in a **fresh
  subprocess** for each of N runs. ``-X importtime`` prints per-module
  ``self`` and ``cumulative`` microseconds to stderr; we parse the
  indented tree into flat rows.
* A fresh subprocess is the only honest way to measure import latency
  — in-process re-imports are contaminated by cached C extensions
  (``numpy``, ``sounddevice``, etc. stay in ``sys.modules`` even after
  ``del`` / ``importlib.reload``).
* Aggregates across N runs: reports **median** self/cumulative per
  module, plus the **min** wall-clock total (the honest cold number;
  the median is contaminated by warm OS page cache on runs 2+).

Usage::

    # Profile a single target, 3 runs, print top-20 slowest modules:
    python scripts/profile_imports.py voice_typer.server.tray

    # Profile two targets and write a report file:
    python scripts/profile_imports.py \\
        --runs 5 \\
        --top 50 \\
        --output scripts/coldstart_AFTER.txt \\
        voice_typer.server.tray voice_typer.server.app

    # Save the BEFORE / AFTER artifacts referenced by COLDSTART_REPORT.md:
    python scripts/profile_imports.py --output scripts/coldstart_BEFORE.txt \\
        voice_typer.server.tray
    # ... apply optimisations ...
    python scripts/profile_imports.py --output scripts/coldstart_AFTER.txt \\
        voice_typer.server.tray

The output format is the same indented-``import time`` tree that
``python -X importtime`` produces, plus a header summarising the
aggregate per-target wall-clock and the top-N slowest modules by
``self`` microseconds.

Exit code is 0 on success, non-zero if any subprocess import fails
(so CI can gate on regressions: ``python scripts/profile_imports.py
--runs 3 --max-self-us 50000 voice_typer.server.tray``).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO


@dataclass(frozen=True)
class ImportRow:
    """One row of ``python -X importtime`` output for a single module.

    ``self_us`` is the time spent in this module's body (excluding
    nested imports). ``cum_us`` is the cumulative time including all
    nested imports. ``indent`` is the depth in the import tree (0 for
    the root ``<target>`` import, 1 for its direct children, etc.).
    """

    self_us: int
    cum_us: int
    module: str
    indent: int


def _parse_importtime(stderr_text: str) -> list[ImportRow]:
    """Parse ``python -X importtime`` stderr output into flat rows.

    The format (Python 3.7+) is one line per imported module::

        import time: self [us] | cumulative | <indent>module

    where ``<indent>`` is a number of spaces proportional to the
    nesting depth. The header line ``import time: self [us] | cumulative | ...``
    is skipped. Lines starting with ``import time:`` are parsed; any
    other lines (e.g. ``Could not find platform independent libraries``
    warnings) are ignored.
    """
    rows: list[ImportRow] = []
    for line in stderr_text.splitlines():
        if not line.startswith("import time:"):
            continue
        # Strip the leading ``import time: `` prefix.
        body = line[len("import time:") :].strip()
        # The first two tokens are ``<self_us>`` and ``<cum_us>``;
        # the rest (after the ``|`` separators) is ``<indent>module``.
        # Example: ``self [us] | cumulative | imported package``
        # After stripping the prefix, body looks like:
        #   ``456 | 1234 |   voice_typer.server.tray``
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 3:
            continue
        try:
            self_us = int(parts[0])
            cum_us = int(parts[1])
        except ValueError:
            # Header line ("self [us] | cumulative | imported package") — skip.
            continue
        module_field = parts[2]
        # Count leading spaces to determine indent depth.
        stripped = module_field.lstrip(" ")
        indent = (len(module_field) - len(stripped)) // 2  # 2 spaces per level
        rows.append(ImportRow(self_us=self_us, cum_us=cum_us, module=stripped, indent=indent))
    return rows


def _run_one(target: str, *, python: str) -> tuple[list[ImportRow], float]:
    """Run one ``python -X importtime`` subprocess for ``target``.

    Returns ``(rows, wall_clock_seconds)``. Raises
    :class:`subprocess.CalledProcessError` if the import fails (non-zero
    exit) — the caller decides whether to retry or propagate.
    """
    start = time.perf_counter()
    proc = subprocess.run(
        [python, "-X", "importtime", "-c", f"import {target}"],
        capture_output=True,
        text=True,
        check=False,
    )
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            proc.args,
            output=proc.stdout,
            stderr=proc.stderr,
        )
    rows = _parse_importtime(proc.stderr)
    return rows, wall


def _aggregate(rows_per_run: Sequence[list[ImportRow]]) -> dict[str, dict[str, float]]:
    """Aggregate ``self_us`` / ``cum_us`` per module across runs.

    Returns ``{module: {"self_median": float, "self_max": int,
    "cum_median": float, "runs": int}}``. Median is used because the
    OS page cache warms up after run 1, so the mean is biased low;
    the median is more representative of the steady-state cold start
    (the *first* run is the true cold start — callers should also
    look at ``--runs 1`` output for the worst case).
    """
    by_module: dict[str, list[ImportRow]] = {}
    for rows in rows_per_run:
        for row in rows:
            by_module.setdefault(row.module, []).append(row)
    aggregated: dict[str, dict[str, float]] = {}
    for module, runs in by_module.items():
        self_values = [r.self_us for r in runs]
        cum_values = [r.cum_us for r in runs]
        aggregated[module] = {
            "self_median": statistics.median(self_values),
            "self_max": max(self_values),
            "cum_median": statistics.median(cum_values),
            "runs": len(runs),
        }
    return aggregated


def _format_report(
    target: str,
    rows_per_run: Sequence[list[ImportRow]],
    walls: Sequence[float],
    *,
    top: int,
    stream: IO[str],
) -> None:
    """Write a human-readable report for one ``target`` to ``stream``.

    The report has three sections:

    1. **Summary** — N runs, wall-clock min/median/max (seconds).
    2. **Top-N by self time** — the slowest modules by median
       ``self_us`` across runs.
    3. **Raw importtime tree** — the indented tree from run 1 (the
       true cold run) verbatim, so callers can diff BEFORE/AFTER.
    """
    stream.write(f"# Cold-start import profile: {target}\n\n")
    stream.write(f"## Summary ({len(walls)} run(s))\n\n")
    stream.write(
        f"- Wall-clock (s): min={min(walls):.3f}  median={statistics.median(walls):.3f}  max={max(walls):.3f}\n"
    )
    stream.write(f"- Modules imported (run 1, cold): {len(rows_per_run[0]) if rows_per_run else 0}\n\n")

    aggregated = _aggregate(rows_per_run)
    # Sort by self_median descending; ties broken by self_max for stability.
    ranked = sorted(
        aggregated.items(),
        key=lambda kv: (-kv[1]["self_median"], -kv[1]["self_max"]),
    )
    stream.write(f"## Top {top} modules by median self time (microseconds)\n\n")
    stream.write("| rank | self_median (us) | self_max (us) | cum_median (us) | module |\n")
    stream.write("|------|------------------|---------------|-----------------|--------|\n")
    for rank, (module, stats) in enumerate(ranked[:top], start=1):
        stream.write(
            f"| {rank} | {stats['self_median']:.0f} | {stats['self_max']} | {stats['cum_median']:.0f} | `{module}` |\n"
        )
    stream.write("\n")

    stream.write("## Raw importtime tree (run 1, cold)\n\n")
    stream.write("```\n")
    if rows_per_run:
        # Re-emit the indented tree from run 1. We reconstruct the
        # ``import time:`` line format so the output is diff-able
        # against a raw ``python -X importtime`` invocation.
        for row in rows_per_run[0]:
            indent = "  " * row.indent
            stream.write(f"import time: {row.self_us:>9} | {row.cum_us:>9} | {indent}{row.module}\n")
    stream.write("```\n\n")


def _write_csv(
    target: str,
    rows_per_run: Sequence[list[ImportRow]],
    walls: Sequence[float],
    *,
    stream: IO[str],
) -> None:
    """Write a machine-readable CSV report (one row per module per run).

    Columns: ``target``, ``run``, ``wall_s``, ``module``, ``indent``,
    ``self_us``, ``cum_us``. Suitable for diffing BEFORE/AFTER in a
    spreadsheet or with ``csvdiff``.
    """
    writer = csv.writer(stream)
    writer.writerow(["target", "run", "wall_s", "module", "indent", "self_us", "cum_us"])
    for run_idx, (rows, wall) in enumerate(zip(rows_per_run, walls, strict=False)):
        for row in rows:
            writer.writerow([target, run_idx, f"{wall:.6f}", row.module, row.indent, row.self_us, row.cum_us])


def profile_target(
    target: str,
    *,
    runs: int,
    top: int,
    python: str,
    output: Path | None,
    csv_output: Path | None,
    max_self_us: int | None,
) -> int:
    """Profile one import target across ``runs`` runs.

    Returns the process exit code (0 on success, 1 on import failure
    or threshold violation).
    """
    rows_per_run: list[list[ImportRow]] = []
    walls: list[float] = []
    # Track import errors per-run; the early return below surfaces the
    # first failure so no `last_error` accumulator is needed.
    for _ in range(runs):
        try:
            rows, wall = _run_one(target, python=python)
        except subprocess.CalledProcessError as exc:
            sys.stderr.write(f"[profile_imports] {target}: import failed (exit {exc.returncode})\n")
            if exc.stderr:
                sys.stderr.write(f"  stderr: {exc.stderr[:500]}\n")
            return 1
        rows_per_run.append(rows)
        walls.append(wall)

    # Always write the human-readable report.
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            _format_report(target, rows_per_run, walls, top=top, stream=f)
        sys.stderr.write(f"[profile_imports] {target}: report → {output}\n")
    else:
        _format_report(target, rows_per_run, walls, top=top, stream=sys.stdout)

    if csv_output is not None:
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        with csv_output.open("w", encoding="utf-8", newline="") as f:
            _write_csv(target, rows_per_run, walls, stream=f)
        sys.stderr.write(f"[profile_imports] {target}: csv → {csv_output}\n")

    # Threshold gate (optional): fail if any single module's max self_us
    # exceeds ``--max-self-us``. Useful for CI regression gates.
    if max_self_us is not None:
        aggregated = _aggregate(rows_per_run)
        worst = max(
            (stats["self_max"] for stats in aggregated.values()),
            default=0,
        )
        if worst > max_self_us:
            sys.stderr.write(
                f"[profile_imports] {target}: THRESHOLD VIOLATION — max self_us={worst} > limit={max_self_us}\n"
            )
            return 2

    return 0


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cold-start import profiler. Runs `python -X importtime` in a "
            "fresh subprocess N times per target and reports per-module "
            "self/cumulative microseconds."
        ),
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Dotted module paths to profile (e.g. voice_typer.server.tray).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of fresh-subprocess runs per target (default: 3).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of slowest modules to list in the report (default: 20).",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use for the subprocess (default: current).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the human-readable report to this file (default: stdout).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Write a machine-readable CSV (one row per module per run) to this file.",
    )
    parser.add_argument(
        "--max-self-us",
        type=int,
        default=None,
        help=(
            "CI gate: exit code 2 if any single module's max self_us exceeds "
            "this limit. Useful for catching cold-start regressions."
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    overall_exit = 0
    for target in args.targets:
        exit_code = profile_target(
            target,
            runs=args.runs,
            top=args.top,
            python=args.python,
            output=args.output,
            csv_output=args.csv,
            max_self_us=args.max_self_us,
        )
        if exit_code != 0:
            overall_exit = exit_code
    return overall_exit


if __name__ == "__main__":
    sys.exit(main())
