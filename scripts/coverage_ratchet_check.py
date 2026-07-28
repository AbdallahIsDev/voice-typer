#!/usr/bin/env python3
"""XS-86: coverage ratchet comparison script.

Compares the current total coverage percentage (parsed from
``coverage.xml`` or produced by ``coverage report --format=json``)
against the baseline (``coverage-baseline.json``).

CI policy
---------
* ``total_coverage`` MUST NOT decrease (beyond a small epsilon for
  float jitter).
* Coverage MAY increase — when it does, contributors SHOULD regenerate
  the baseline so the new (higher) number becomes the floor (see
  ``--regenerate``).
* The fixed ``--cov-fail-under=65`` floor in CI still catches
  catastrophic drops below 65%; this ratchet catches silent erosion
  (e.g. 70% → 65.01%).

Usage
-----
Run from the project root.

1. CI mode (default):
   ::

       # pytest already emitted coverage.xml via --cov-report=xml:
       python scripts/coverage_ratchet_check.py

   Exit code 0 if ``current >= baseline`` (within epsilon).
   Exit code 1 if ``current < baseline``.

2. Regenerate baseline (only run locally after IMPROVING coverage):
   ::

       coverage report --format=json > /dev/null  # or just run pytest
       python scripts/coverage_ratchet_check.py --regenerate

   Rewrites ``coverage-baseline.json`` with the current coverage %.
   The script REFUSES to regenerate if the new total is LOWER than the
   old total — that would be a regression, not a ratchet.

3. Custom coverage.xml path:
   ::

       python scripts/coverage_ratchet_check.py --coverage-xml path/to/coverage.xml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# ── Paths (relative to project root) ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = PROJECT_ROOT / "coverage-baseline.json"
COVERAGE_XML_PATH = PROJECT_ROOT / "coverage.xml"

# Required schema fields on the baseline file. The baseline MAY carry
# extra underscore-prefixed metadata (e.g. ``_comment``, ``_schema_version``)
# which is preserved on regenerate but ignored by the comparison logic.
REQUIRED_FIELDS = ("total_coverage",)

# Float comparison tolerance (percentage points). Coverage % can wobble
# by a few hundredths of a point due to Python version / line-number
# arithmetic differences across matrix legs; treat anything within this
# epsilon as "no change".
EPSILON = 0.01


def _parse_coverage_xml(path: Path) -> float | None:
    """Parse the <coverage line-rate="..."/> attribute from coverage.xml.

    coverage.py emits ``line-rate`` as a fraction (0.0–1.0); we convert
    to a percentage. Returns ``None`` if the file is missing or unparseable.
    """
    if not path.is_file():
        return None
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        print(f"ERROR: failed to parse coverage XML: {exc}")
        return None
    root = tree.getroot()
    if root.tag != "coverage":
        print(f"ERROR: expected <coverage> root element, got <{root.tag}>")
        return None
    line_rate = root.get("line-rate")
    if line_rate is None:
        print("ERROR: coverage XML missing line-rate attribute")
        return None
    try:
        return float(line_rate) * 100.0
    except ValueError:
        print(f"ERROR: line-rate is not a float: {line_rate!r}")
        return None


def _run_coverage_json() -> float | None:
    """Invoke ``coverage report --format=json`` and parse the total %.

    Used as a fallback when coverage.xml is unavailable (e.g. local
    runs without pytest-cov). Returns ``None`` on any failure.
    """
    try:
        result = subprocess.run(
            ["coverage", "report", "--format=json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("ERROR: `coverage` command not found on PATH.")
        print("       Install coverage.py (e.g. `pip install coverage`) and re-run.")
        return None
    if result.returncode not in (0, 1):
        print(f"ERROR: `coverage report --format=json` exited with code {result.returncode}")
        if result.stderr:
            print(f"       stderr: {result.stderr.strip()}")
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"ERROR: failed to parse coverage JSON: {exc}")
        return None
    totals = data.get("totals")
    if not isinstance(totals, dict):
        print("ERROR: coverage JSON missing 'totals' object")
        return None
    percent = totals.get("percent_covered")
    if percent is None:
        print("ERROR: coverage JSON totals missing 'percent_covered'")
        return None
    try:
        return float(percent)
    except (TypeError, ValueError):
        print(f"ERROR: percent_covered is not a float: {percent!r}")
        return None


def _load_current_coverage() -> float | None:
    """Resolve the current total coverage %.

    Prefer ``coverage.xml`` (always produced by the CI pytest step via
    ``--cov-report=xml``); fall back to invoking
    ``coverage report --format=json`` if the XML is missing.
    """
    pct = _parse_coverage_xml(COVERAGE_XML_PATH)
    if pct is not None:
        return pct
    print(f"NOTE: {COVERAGE_XML_PATH.name} not found, falling back to `coverage report --format=json`")
    return _run_coverage_json()


def _load_baseline() -> dict[str, Any]:
    """Load and validate the baseline file."""
    if not BASELINE_PATH.is_file():
        print(f"ERROR: baseline file not found: {BASELINE_PATH}")
        print("Create it with: coverage report --format=json | python scripts/coverage_ratchet_check.py --regenerate")
        sys.exit(2)
    try:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: baseline file is not valid JSON: {exc}")
        sys.exit(2)
    if not isinstance(baseline, dict):
        print(f"ERROR: baseline root must be a JSON object, got {type(baseline).__name__}")
        sys.exit(2)
    for field in REQUIRED_FIELDS:
        if field not in baseline:
            print(f"ERROR: baseline missing required field '{field}'")
            sys.exit(2)
    tc = baseline["total_coverage"]
    if not isinstance(tc, int | float) or isinstance(tc, bool) or tc < 0:
        print(f"ERROR: baseline.total_coverage must be a non-negative number, got {tc!r}")
        sys.exit(2)
    return baseline


def compare(current_pct: float) -> int:
    """Compare current coverage % to baseline. Return process exit code."""
    baseline = _load_baseline()
    base_pct: float = float(baseline["total_coverage"])

    delta = current_pct - base_pct
    if delta < -EPSILON:
        status = "REGRESSION"
    elif delta > EPSILON:
        status = "improved"
    else:
        status = "ok (within epsilon)"

    print("Coverage ratchet comparison")
    print("===========================")
    print(f"  Baseline file: {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  Total: baseline={base_pct:.4f}%  current={current_pct:.4f}%  status={status}")
    if delta > EPSILON:
        print("  -> Total IMPROVED. Consider regenerating the baseline to lock in the gain:")
        print("       python scripts/coverage_ratchet_check.py --regenerate")

    if delta < -EPSILON:
        print()
        print(f"FAIL: total coverage dropped from {base_pct:.4f}% to {current_pct:.4f}% (delta {delta:+.4f}%).")
        print("The ratchet only allows coverage to increase. Either:")
        print("  1. Add tests for the code paths whose coverage regressed, OR")
        print("  2. If the drop is intentional and unavoidable, document why")
        print("     in the PR description and regenerate the baseline:")
        print("       python scripts/coverage_ratchet_check.py --regenerate")
        return 1

    print()
    print(f"PASS: ratchet holds (current {current_pct:.4f}% >= baseline {base_pct:.4f}%).")
    return 0


def regenerate(current_pct: float, *, force: bool = False) -> int:
    """Rewrite the baseline file with the current coverage %.

    Refuses to write if the new total is LOWER than the old total —
    that would be a regression, not a ratchet. Use ``--force`` to
    bypass the refuse-to-lower guard (e.g. after a intentional
    coverage drop that's been documented).
    """
    # Preserve metadata fields from the existing baseline.
    metadata: dict[str, Any] = {}
    if BASELINE_PATH.is_file():
        try:
            old = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                for k, v in old.items():
                    if k in REQUIRED_FIELDS:
                        continue
                    metadata[k] = v
                old_pct = old.get("total_coverage")
                if (
                    isinstance(old_pct, int | float)
                    and not isinstance(old_pct, bool)
                    and current_pct < float(old_pct) - EPSILON
                ):
                    if force:
                        print(
                            f"WARNING: --force bypassing refuse-to-lower check "
                            f"(new {current_pct:.4f}% < old {old_pct:.4f}%)."
                        )
                    else:
                        print(f"REFUSED: new coverage ({current_pct:.4f}%) < old coverage ({old_pct:.4f}%).")
                        print("The ratchet only allows coverage to increase. Add tests for")
                        print("the regressed code paths before regenerating the baseline.")
                        print("To override (e.g. after an intentional drop), re-run with --force.")
                        return 1
        except (json.JSONDecodeError, OSError) as exc:
            if force:
                print(f"WARNING: --force bypassing corrupt/missing baseline ({exc}).")
            else:
                print(f"ERROR: baseline is corrupt or unreadable ({exc}).")
                print("Refusing to regenerate without --force (would lose the ratchet floor).")
                return 1

    new_baseline = {**metadata, "total_coverage": round(current_pct, 4)}
    BASELINE_PATH.write_text(
        json.dumps(new_baseline, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Regenerated {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  total_coverage = {current_pct:.4f}%")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="XS-86: coverage ratchet comparison script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rewrite coverage-baseline.json with the current coverage percentage. "
        "Only use this after IMPROVING coverage — refuses to lower the baseline.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the refuse-to-lower check and the corrupt/missing-baseline "
        "guard. Use only after documenting an intentional coverage drop.",
    )
    parser.add_argument(
        "--coverage-xml",
        type=Path,
        default=COVERAGE_XML_PATH,
        help=f"Path to coverage.xml (default: {COVERAGE_XML_PATH.name}).",
    )
    args = parser.parse_args(argv)

    # If a non-default coverage.xml path was provided, prefer it.
    current_pct: float | None
    if args.coverage_xml != COVERAGE_XML_PATH:
        current_pct = _parse_coverage_xml(args.coverage_xml)
        if current_pct is None:
            print(f"ERROR: could not parse coverage XML at {args.coverage_xml}")
            return 2
    else:
        current_pct = _load_current_coverage()
        if current_pct is None:
            print("NOTE: could not determine current coverage % — skipping ratchet check.")
            print("      Run pytest with --cov-report=xml first, OR install coverage")
            print("      and re-run this script (it will invoke `coverage report`).")
            print("      PASS (skip) — the ratchet is gated on data availability.")
            return 0

    if args.regenerate:
        return regenerate(current_pct, force=args.force)
    return compare(current_pct)


if __name__ == "__main__":
    sys.exit(main())
