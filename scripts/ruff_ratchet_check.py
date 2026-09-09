#!/usr/bin/env python3
"""ruff ratchet comparison script.

Compares the current ruff violation counts (captured in
``ruff-current.json``) against the baseline (``ruff-baseline.json``).

The comparison algorithm, baseline validation, refuse-to-regrow
regenerate flow, table rendering, and argparse flag shape live in
``scripts/_ratchet_common.py`` (shared with the mypy ratchet so gate
fixes land once); this module is the ruff-specific adapter — the
current-input loading (file / stdin with fail-closed guards), the
violation->counts summarizer, the F-rule baseline filter, and the
user-facing wording.

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

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _ratchet_common import (
    PROJECT_ROOT,
    build_ratchet_parser,
    compare_counts,
    display_path,
    env_path,
    load_counts_baseline,
    regenerate_counts_baseline,
)

BASELINE_PATH = env_path("RUFF_BASELINE_PATH", PROJECT_ROOT / "ruff-baseline.json")
CURRENT_PATH = env_path("RUFF_CURRENT_PATH", PROJECT_ROOT / "ruff-current.json")

# Wordings shared by the compare/regenerate entry points below.
CREATE_HINT = (
    "Create it with: ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | "
    "python scripts/ruff_ratchet_check.py --regenerate"
)
REGENERATE_CMD = (
    "ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | "
    "python scripts/ruff_ratchet_check.py --regenerate"
)
ZERO_NOTE = "Ratchet is now at zero — any new ruff violation will fail CI."


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


def compare(current_violations: list[dict[str, Any]]) -> int:
    """Compare current violations to baseline. Return process exit code."""
    baseline = load_counts_baseline(BASELINE_PATH, detail_field="by_rule", create_hint=CREATE_HINT)
    return compare_counts(
        baseline,
        _summarize(current_violations),
        detail_field="by_rule",
        key_header="rule",
        unit="violation",
        report_title="Ruff ratchet comparison",
        report_underline="=======================",
        baseline_display=display_path(BASELINE_PATH, PROJECT_ROOT),
        scope_line=None,
        regenerate_cmd=REGENERATE_CMD,
    )


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

    return regenerate_counts_baseline(
        BASELINE_PATH,
        new_total,
        new_by_rule,
        detail_field="by_rule",
        unit="violation",
        zero_note=ZERO_NOTE,
        force=force,
        project_root=PROJECT_ROOT,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_ratchet_parser(
        description="ruff ratchet comparison script.",
        epilog=__doc__,
        regenerate_help=(
            "Rewrite ruff-baseline.json with the current violation counts. "
            "Only use this after FIXING violations — refuses to grow the "
            "baseline and refuses to run when the baseline is missing/corrupt."
        ),
        force_help=(
            "Bypass the refuse-to-grow check AND the missing/corrupt-baseline "
            "guard on --regenerate. Intended for bootstrap or emergency "
            "re-baselining after a deliberate scope change. Prints a warning."
        ),
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
