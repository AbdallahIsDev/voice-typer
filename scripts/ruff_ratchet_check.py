#!/usr/bin/env python3
"""ruff ratchet comparison script.

Compares the current ruff violation counts (captured in
``ruff-current.json``) against the baseline (``ruff-baseline.json``).

CI policy
---------
* ``total_count`` MUST NOT grow.
* Per-rule counts in ``by_rule`` MUST NOT grow.
* Counts MAY shrink — that's the whole point of the ratchet.
* When a count shrinks, contributors SHOULD regenerate the baseline so
  the new (lower) number becomes the floor (see ``--regenerate``).

Usage
-----
Run from the project root.

1. CI mode (default):
   ::
       ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json > ruff-current.json
       python scripts/ruff_ratchet_check.py

   Exit code 0 if current <= baseline for every rule (and total).
   Exit code 1 if any rule (or total) grew above the baseline.

2. Regenerate baseline (only run locally after FIXING violations):
   ::
       ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | \
           python scripts/ruff_ratchet_check.py --regenerate

   Rewrites ``ruff-baseline.json`` with the current violation counts.
   The script REFUSES to regenerate if the new total is HIGHER than the
   old total — that would be a regression, not a ratchet.

   The script also REFUSES to regenerate if the baseline file is missing
   or corrupt, because the refuse-to-grow check cannot run without a
   valid prior baseline — a missing or unreadable floor cannot be
   compared against, so a regeneration would accept any new total,
   however large. To override (e.g. for bootstrap or
   emergency re-baselining after a deliberate scope change), pass
   ``--force``: this skips both the missing-baseline guard and the
   refuse-to-grow check, and prints a warning.

3. Stdin mode (no temp file):
   ::
       ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | \
           python scripts/ruff_ratchet_check.py --stdin

   Reads the current ruff JSON from stdin instead of ``ruff-current.json``.
   Useful for local one-off checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ── Paths (relative to project root) ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    """Resolve a path from an env var, falling back to a default.

    RUFF_BASELINE_PATH / RUFF_CURRENT_PATH let tests redirect all reads
    and writes to a temp file instead of the repo's real
    ``ruff-baseline.json``, so an interrupted test run (timeout, kill,
    power loss) can never leave a fake baseline on disk.
    """
    raw = os.environ.get(name)
    return Path(raw) if raw else default


def _display_path(path: Path) -> str:
    """Render a path relative to the project root when possible."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


BASELINE_PATH = _env_path("RUFF_BASELINE_PATH", PROJECT_ROOT / "ruff-baseline.json")
CURRENT_PATH = _env_path("RUFF_CURRENT_PATH", PROJECT_ROOT / "ruff-current.json")

# Required schema fields on the baseline file. The baseline MAY carry
# extra underscore-prefixed metadata (e.g. ``_comment``, ``_schema_version``)
# which is preserved on regenerate but ignored by the comparison logic.
REQUIRED_FIELDS = ("total_count", "by_rule")


def _load_ruff_json(path: Path | None, stdin_data: str | None) -> list[dict[str, Any]]:
    """Load ruff --output-format=json output and return the violation list.

    ruff emits a JSON array (one object per violation). An empty array
    means "no violations". ``path`` takes precedence over ``stdin_data``.

    When the current file is missing, empty, or unparseable,
    this function now exits with code 2 instead of silently returning
    ``[]``. The previous behavior was a silent-pass hole: ``compare([])``
    saw ``curr_total=0`` and reported "PASS (improved)" even though ruff
    had never actually run. Empty stdin still returns ``[]`` to preserve
    the documented "empty stdin = 0 violations" contract exercised by
    ``tests/test_ruff_ratchet.py::TestCompareLogic::test_empty_stdin_treated_as_zero_violations``.
    """
    if path is not None:
        if not path.is_file():
            print(f"ERROR: current ruff JSON file not found: {path}")
            print("Did you forget to run `ruff check ... --output-format=json > ruff-current.json`?")
            print("Refusing to silently treat a missing file as 0 violations.")
            sys.exit(2)
        raw = path.read_text(encoding="utf-8")
    elif stdin_data is not None:
        raw = stdin_data
    else:
        # No source provided — read from the default current path.
        return _load_ruff_json(CURRENT_PATH, None)

    raw = raw.strip()
    if not raw:
        if path is not None:
            # Empty file means ruff did not actually write output (e.g.
            # the command crashed before serializing). Treat as a hard
            # failure rather than silently reporting 0 violations.
            print(f"ERROR: current ruff JSON file is empty: {path}")
            print("Did the ruff command fail before writing output?")
            print("Refusing to silently treat an empty file as 0 violations.")
            sys.exit(2)
        # Empty stdin = 0 violations. ruff emits `[]` (2 bytes) for the
        # no-violations case; an empty stdin typically arises from a
        # unit-test harness that explicitly passes empty input. We
        # preserve the existing contract here so
        # test_empty_stdin_treated_as_zero_violations continues to pass.
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: failed to parse ruff JSON: {exc}")
        print("Refusing to silently treat unparseable input as 0 violations.")
        sys.exit(2)
    if not isinstance(parsed, list):
        print(f"ERROR: expected a JSON array of violations, got {type(parsed).__name__}")
        print("Refusing to silently treat a non-array root as 0 violations.")
        sys.exit(2)
    return parsed


def _summarize(violations: list[dict[str, Any]]) -> tuple[int, dict[str, int]]:
    """Reduce a violation list to (total_count, by_rule dict)."""
    by_rule: Counter[str] = Counter()
    for v in violations:
        code = v.get("code")
        if isinstance(code, str):
            by_rule[code] += 1
    return len(violations), dict(by_rule)


def _load_baseline() -> dict[str, Any]:
    """Load and validate the baseline file."""
    if not BASELINE_PATH.is_file():
        print(f"ERROR: baseline file not found: {BASELINE_PATH}")
        print(
            "Create it with: ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | "
            "python scripts/ruff_ratchet_check.py --regenerate"
        )
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
    if not isinstance(baseline["total_count"], int) or baseline["total_count"] < 0:
        print(f"ERROR: baseline.total_count must be a non-negative int, got {baseline['total_count']!r}")
        sys.exit(2)
    if not isinstance(baseline["by_rule"], dict):
        print(f"ERROR: baseline.by_rule must be a JSON object, got {type(baseline['by_rule']).__name__}")
        sys.exit(2)
    for rule, count in baseline["by_rule"].items():
        if not isinstance(rule, str):
            print(f"ERROR: baseline.by_rule has non-string key: {rule!r}")
            sys.exit(2)
        if not isinstance(count, int) or count < 0:
            print(f"ERROR: baseline.by_rule[{rule!r}] must be a non-negative int, got {count!r}")
            sys.exit(2)
    return baseline


def _format_table(rows: list[tuple[str, int, int, str]]) -> str:
    """Pretty-print rule comparison rows: (rule, baseline, current, status)."""
    if not rows:
        return "  (no rules to display)"
    rule_w = max(len("rule"), max(len(r) for r, _, _, _ in rows))
    base_w = max(len("baseline"), max(len(str(b)) for _, b, _, _ in rows))
    curr_w = max(len("current"), max(len(str(c)) for _, _, c, _ in rows))
    status_w = max(len("status"), max(len(s) for _, _, _, s in rows))
    header = f"  {'rule':<{rule_w}}  {'baseline':>{base_w}}  {'current':>{curr_w}}  {'status':<{status_w}}"
    sep = f"  {'-' * rule_w}  {'-' * base_w}  {'-' * curr_w}  {'-' * status_w}"
    lines = [header, sep]
    for rule, b, c, s in rows:
        lines.append(f"  {rule:<{rule_w}}  {b:>{base_w}}  {c:>{curr_w}}  {s:<{status_w}}")
    return "\n".join(lines)


def compare(current_violations: list[dict[str, Any]]) -> int:
    """Compare current violations to baseline. Return process exit code."""
    baseline = _load_baseline()
    base_total: int = baseline["total_count"]
    base_by_rule: dict[str, int] = baseline["by_rule"]
    curr_total, curr_by_rule = _summarize(current_violations)

    # Build the union of rule codes for table rendering.
    all_rules = sorted(set(base_by_rule) | set(curr_by_rule))
    rows: list[tuple[str, int, int, str]] = []
    regressions: list[tuple[str, int, int]] = []
    for rule in all_rules:
        b = base_by_rule.get(rule, 0)
        c = curr_by_rule.get(rule, 0)
        if c > b:
            status = "REGRESSION"
            regressions.append((rule, b, c))
        elif c < b:
            status = "improved"
        else:
            status = "ok"
        rows.append((rule, b, c, status))

    total_status = "REGRESSION" if curr_total > base_total else ("improved" if curr_total < base_total else "ok")

    print("Ruff ratchet comparison")
    print("=======================")
    print(f"  Baseline file: {_display_path(BASELINE_PATH)}")
    print(f"  Total: baseline={base_total}  current={curr_total}  status={total_status}")
    if curr_total < base_total:
        print("  -> Total IMPROVED. Consider regenerating the baseline to lock in the gain:")
        print(
            "       ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | "
            "python scripts/ruff_ratchet_check.py --regenerate"
        )
    print()
    print("Per-rule breakdown:")
    print(_format_table(rows))

    if curr_total > base_total:
        print()
        print(f"FAIL: total violation count grew from {base_total} to {curr_total}.")
        print("The ratchet only allows counts to shrink. Either:")
        print("  1. Fix the new violations introduced in this change, OR")
        print("  2. If the increase is intentional and unavoidable, document why")
        print("     in the PR description and regenerate the baseline:")
        print(
            "       ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | "
            "python scripts/ruff_ratchet_check.py --regenerate"
        )
        return 1

    if regressions:
        print()
        print("FAIL: per-rule regression detected (even though total did not grow):")
        for rule, b, c in regressions:
            print(f"  {rule}: {b} -> {c}")
        print("The ratchet requires every individual rule count to be non-increasing.")
        print("Fix the new violations OR regenerate the baseline with justification.")
        return 1

    print()
    print(f"PASS: ratchet holds (total {curr_total} <= baseline {base_total}).")
    return 0


def regenerate(current_violations: list[dict[str, Any]], *, force: bool = False) -> int:
    """Rewrite the baseline file with the current violation counts.

    Refuses to write if the new total is HIGHER than the old total —
    that would be a regression, not a ratchet.

    Also refuses to regenerate when the existing baseline is missing or
    corrupt, because the refuse-to-grow check cannot run without a valid
    prior baseline — a missing/corrupt baseline must not become a silent
    escape hatch that locks in an arbitrary regression. Both
    guards are bypassed when ``force`` is True (intended for bootstrap
    or emergency re-baselining after a deliberate scope change); a
    warning is printed in that case.

    F-rule (pyflakes) codes — F401, F811, F821, F841, etc.
    — are stripped from the regenerated ``by_rule`` and ``total_count``
    so the baseline never carries a non-zero F-rule floor. Per
    ``docs/ruff-ratchet.md`` §"Step 1", F-rules must hard-fail at zero
    tolerance: if any F-rule count > 0 appears in the current ruff
    output, ``compare()`` sees ``b=0`` (baseline omits F-rules) vs
    ``c>0`` and reports a per-rule REGRESSION. Locking a non-zero
    F-rule count into the baseline would silently absorb future
    regressions in unused-import / undefined-name / unused-variable
    checks — exactly the hole this filter closes.
    """
    _raw_total, raw_by_rule = _summarize(current_violations)

    # Drop F-rule codes so the baseline never carries a
    # non-zero F-rule floor (see docstring).
    new_by_rule = {code: count for code, count in raw_by_rule.items() if not code.startswith("F")}
    new_total = sum(new_by_rule.values())
    f_rule_total = _raw_total - new_total
    if f_rule_total > 0:
        print(
            f"NOTE: {f_rule_total} F-rule (pyflakes) violations omitted from the "
            'baseline per docs/ruff-ratchet.md §"Step 1" (F-rules hard-fail at 0; '
            "the ratchet does not track a non-zero F-rule floor). Fix them before "
            "committing — F-rules are real bugs (unused imports, undefined names, "
            "unused variables, redefinitions)."
        )

    # Preserve metadata fields from the existing baseline.
    metadata: dict[str, Any] = {}
    if BASELINE_PATH.is_file():
        try:
            old = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            if not force:
                print(f"ERROR: existing baseline is corrupt or unreadable: {exc}")
                print("Refusing to regenerate without --force. A corrupt baseline")
                print("means the refuse-to-grow check cannot run, so a regeneration")
                print("could silently lock in a regression.")
                print("To override (e.g. for bootstrap), re-run with --force.")
                return 1
            print(f"WARNING: --force bypassing corrupt baseline ({exc});")
            print("         refuse-to-grow check skipped.")
            old = None
        if isinstance(old, dict):
            for k, v in old.items():
                if k in REQUIRED_FIELDS:
                    continue
                metadata[k] = v
            old_total = old.get("total_count")
            if isinstance(old_total, int) and new_total > old_total:
                if not force:
                    print(f"REFUSED: new total ({new_total}) > old total ({old_total}).")
                    print("The ratchet only allows counts to shrink. Fix the new")
                    print("violations before regenerating the baseline.")
                    print("If the increase is intentional and unavoidable, document")
                    print("why in the PR description and re-run with --force.")
                    return 1
                print(
                    f"WARNING: --force bypassing refuse-to-grow check (new total {new_total} > old total {old_total})."
                )
    elif not force:
        print(f"ERROR: baseline file not found: {BASELINE_PATH}")
        print("Refusing to regenerate without --force. A missing baseline means")
        print("the refuse-to-grow check cannot run, so a regeneration could")
        print("silently lock in a regression.")
        print("To override (e.g. for bootstrap), re-run with --force.")
        return 1
    else:
        print("WARNING: --force bypassing missing baseline;")
        print("         refuse-to-grow check skipped.")

    # Sort by_rule by rule code for deterministic diffs.
    new_by_rule_sorted = {k: new_by_rule[k] for k in sorted(new_by_rule)}
    new_baseline = {**metadata, "total_count": new_total, "by_rule": new_by_rule_sorted}
    BASELINE_PATH.write_text(
        json.dumps(new_baseline, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Regenerated {_display_path(BASELINE_PATH)}")
    print(f"  total_count = {new_total}")
    print(f"  by_rule     = {json.dumps(new_by_rule_sorted, sort_keys=True)}")
    if new_total == 0:
        print()
        print("Ratchet is now at zero — any new ruff violation will fail CI.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ruff ratchet comparison script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rewrite ruff-baseline.json with the current violation counts. "
        "Only use this after FIXING violations — refuses to grow the "
        "baseline and refuses to run when the baseline is missing/corrupt.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the refuse-to-grow check AND the missing/corrupt-baseline "
        "guard on --regenerate. Intended for bootstrap or emergency "
        "re-baselining after a deliberate scope change. Prints a warning.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read current ruff JSON from stdin instead of ruff-current.json.",
    )
    parser.add_argument(
        "--current-path",
        type=Path,
        default=CURRENT_PATH,
        help=f"Path to the current ruff JSON file (default: {CURRENT_PATH.name}). Ignored when --stdin is set.",
    )
    args = parser.parse_args(argv)

    stdin_data: str | None = None
    if args.stdin:
        stdin_data = sys.stdin.read()

    if args.regenerate:
        if stdin_data is None and not args.current_path.is_file():
            # Allow regenerating from stdin OR from a current file.
            # If neither is present, fall back to running ruff directly
            # by re-emitting a helpful error.
            print("ERROR: --regenerate requires either --stdin or an existing current file.")
            print("       Run: ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | \\")
            print("             python scripts/ruff_ratchet_check.py --regenerate --stdin")
            return 2
        violations = _load_ruff_json(None if args.stdin else args.current_path, stdin_data)
        return regenerate(violations, force=args.force)

    violations = _load_ruff_json(None if args.stdin else args.current_path, stdin_data)
    return compare(violations)


if __name__ == "__main__":
    sys.exit(main())
