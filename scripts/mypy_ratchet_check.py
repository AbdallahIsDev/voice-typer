#!/usr/bin/env python3
"""mypy ratchet comparison script.

Compares the current mypy error counts on the server scope
(``voice_typer/server/``) against a committed baseline
(``mypy-baseline.json``). This exists because mypy is NOT a clean gate
for this codebase today: numpy 2.x's PEP 695 stubs cannot be parsed at
the project's mypy language level (``python_version = "3.10"``, CQ-058),
so numpy is shadowed by a local Any-stub (``voice_typer/mypy_stubs/``),
which lets mypy type-check every server module — exposing ~700 latent
typing-debt errors that were previously hidden by the numpy parse
failure. Those errors are tracked here instead of being fixed all at
once.

CI policy
---------
* ``total_count`` MUST NOT grow.
* Per-code counts in ``by_code`` MUST NOT grow.
* Counts MAY shrink — that's the whole point of the ratchet.
* When a count shrinks, contributors SHOULD regenerate the baseline so
  the new (lower) number becomes the floor (see ``--regenerate``).

Usage
-----
Run from the project root.

1. Check mode (default) — runs mypy and compares against the baseline::

       python scripts/mypy_ratchet_check.py

   Exit code 0 if current <= baseline for every code (and total).
   Exit code 1 if any code (or total) grew above the baseline. In that
   case the full mypy output is printed so the new errors can be found.

2. Regenerate baseline (only run locally after FIXING errors)::

       python scripts/mypy_ratchet_check.py --regenerate

   Rewrites ``mypy-baseline.json`` with the current error counts.
   The script REFUSES to regenerate if the new total is HIGHER than the
   old total — that would be a regression, not a ratchet. Pass ``--force``
   to bootstrap a missing baseline (prints a warning).

3. Stdin mode (for tests / one-off checks without running mypy)::

       python -m mypy voice_typer/server/ | python scripts/mypy_ratchet_check.py --stdin
       python -m mypy voice_typer/server/ | python scripts/mypy_ratchet_check.py --regenerate --stdin --force
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ── Paths (relative to project root) ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The mypy scope the ratchet guards. Must stay in sync with the
# Makefile `typecheck` target and `.pre-commit-config.yaml`.
MYPY_TARGET = "voice_typer/server/"


def _env_path(name: str, default: Path) -> Path:
    """Resolve a path from an env var, falling back to a default.

    MYPY_BASELINE_PATH lets tests redirect all reads/writes to a temp
    file instead of the repo's real ``mypy-baseline.json``, so an
    interrupted test run can never leave a fake baseline on disk.
    """
    raw = os.environ.get(name)
    return Path(raw) if raw else default


def _display_path(path: Path) -> str:
    """Render a path relative to the project root when possible."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


BASELINE_PATH = _env_path("MYPY_BASELINE_PATH", PROJECT_ROOT / "mypy-baseline.json")

# Required schema fields on the baseline file. The baseline MAY carry
# extra underscore-prefixed metadata (e.g. ``_comment``, ``_schema_version``)
# which is preserved on regenerate but ignored by the comparison logic.
REQUIRED_FIELDS = ("total_count", "by_code")

# Matches a mypy error line:
#   path:line: error: message [code]
# The code suffix is optional in older mypy output; such lines are
# bucketed under "(untyped)". The file part is anchored with \S so an
# indented continuation / context line (which mypy prefixes with
# whitespace) can never be miscounted as a new error.
_ERROR_RE = re.compile(
    r"^(?P<file>\S.*?):(?P<line>\d+): error: (?P<message>.*?)(?: \[(?P<code>[a-z][a-z-]*)\])?$"
)


def run_mypy() -> str:
    """Run mypy on the server scope and return its combined output.

    mypy exits 0 (clean), 1 (type errors found — expected), or 2+
    (internal failure). A return code above 1 means the gate cannot
    evaluate and is treated as a hard error rather than a silent pass.
    """
    cmd = [sys.executable, "-m", "mypy", MYPY_TARGET]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=600)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode not in (0, 1):
        print("ERROR: mypy failed to run (exit {proc.returncode}):")
        print(out)
        sys.exit(2)
    return out


def _summarize(output: str) -> tuple[int, dict[str, int]]:
    """Reduce mypy output to (total_count, by_code dict)."""
    by_code: Counter[str] = Counter()
    total = 0
    for raw_line in output.splitlines():
        # Strip CR so the anchored regex is robust to CRLF-piped output
        # (e.g. when the live test captures mypy output on Windows).
        line = raw_line.rstrip("\r\n")
        match = _ERROR_RE.match(line)
        if not match:
            continue
        total += 1
        code = match.group("code") or "(untyped)"
        by_code[code] += 1
    return total, dict(by_code)


def _load_baseline() -> dict[str, Any]:
    """Load and validate the baseline file."""
    if not BASELINE_PATH.is_file():
        print(f"ERROR: baseline file not found: {BASELINE_PATH}")
        print(
            "Create it with: python scripts/mypy_ratchet_check.py --regenerate --force"
            "  (or pipe mypy output: python -m mypy voice_typer/server/ | "
            "python scripts/mypy_ratchet_check.py --regenerate --stdin --force)"
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
    if not isinstance(baseline["by_code"], dict):
        print(f"ERROR: baseline.by_code must be a JSON object, got {type(baseline['by_code']).__name__}")
        sys.exit(2)
    for code, count in baseline["by_code"].items():
        if not isinstance(code, str):
            print(f"ERROR: baseline.by_code has non-string key: {code!r}")
            sys.exit(2)
        if not isinstance(count, int) or count < 0:
            print(f"ERROR: baseline.by_code[{code!r}] must be a non-negative int, got {count!r}")
            sys.exit(2)
    return baseline


def _format_table(rows: list[tuple[str, int, int, str]]) -> str:
    """Pretty-print code comparison rows: (code, baseline, current, status)."""
    if not rows:
        return "  (no codes to display)"
    code_w = max(len("code"), max(len(r) for r, _, _, _ in rows))
    base_w = max(len("baseline"), max(len(str(b)) for _, b, _, _ in rows))
    curr_w = max(len("current"), max(len(str(c)) for _, _, c, _ in rows))
    status_w = max(len("status"), max(len(s) for _, _, _, s in rows))
    header = f"  {'code':<{code_w}}  {'baseline':>{base_w}}  {'current':>{curr_w}}  {'status':<{status_w}}"
    sep = f"  {'-' * code_w}  {'-' * base_w}  {'-' * curr_w}  {'-' * status_w}"
    lines = [header, sep]
    for code, b, c, s in rows:
        lines.append(f"  {code:<{code_w}}  {b:>{base_w}}  {c:>{curr_w}}  {s:<{status_w}}")
    return "\n".join(lines)


def compare(output: str) -> int:
    """Compare current mypy output to baseline. Return process exit code."""
    baseline = _load_baseline()
    base_total: int = baseline["total_count"]
    base_by_code: dict[str, int] = baseline["by_code"]
    curr_total, curr_by_code = _summarize(output)

    all_codes = sorted(set(base_by_code) | set(curr_by_code))
    rows: list[tuple[str, int, int, str]] = []
    regressions: list[tuple[str, int, int]] = []
    for code in all_codes:
        b = base_by_code.get(code, 0)
        c = curr_by_code.get(code, 0)
        if c > b:
            status = "REGRESSION"
            regressions.append((code, b, c))
        elif c < b:
            status = "improved"
        else:
            status = "ok"
        rows.append((code, b, c, status))

    total_status = "REGRESSION" if curr_total > base_total else ("improved" if curr_total < base_total else "ok")

    print("mypy ratchet comparison")
    print("=======================")
    print(f"  Baseline file: {_display_path(BASELINE_PATH)}")
    print(f"  Scope:         {MYPY_TARGET}")
    print(f"  Total: baseline={base_total}  current={curr_total}  status={total_status}")
    if curr_total < base_total:
        print("  -> Total IMPROVED. Consider regenerating the baseline to lock in the gain:")
        print("       python scripts/mypy_ratchet_check.py --regenerate")
    print()
    print("Per-code breakdown:")
    print(_format_table(rows))

    if curr_total > base_total:
        print()
        print(f"FAIL: total error count grew from {base_total} to {curr_total}.")
        print("The ratchet only allows counts to shrink. Either:")
        print("  1. Fix the new errors introduced in this change, OR")
        print("  2. If the increase is intentional and unavoidable, document why")
        print("     in the PR description and regenerate the baseline:")
        print("       python scripts/mypy_ratchet_check.py --regenerate")
        print()
        print("Full mypy output (to locate the new errors):")
        print(output)
        return 1

    if regressions:
        print()
        print("FAIL: per-code regression detected (even though total did not grow):")
        for code, b, c in regressions:
            print(f"  {code}: {b} -> {c}")
        print("The ratchet requires every individual code count to be non-increasing.")
        print("Fix the new errors OR regenerate the baseline with justification.")
        print()
        print("Full mypy output (to locate the new errors):")
        print(output)
        return 1

    print()
    print(f"PASS: ratchet holds (total {curr_total} <= baseline {base_total}).")
    return 0


def regenerate(output: str, *, force: bool = False) -> int:
    """Rewrite the baseline file with the current error counts.

    Refuses to write if the new total is HIGHER than the old total —
    that would be a regression, not a ratchet. (The guard is
    total-only: a mypy upgrade that re-buckets existing errors across
    codes at the same total will regenerate the distribution. That is
    acceptable because compare mode still blocks per-code growth on
    every check, so the gate is protected either way.) Also refuses
    when the existing baseline is missing or corrupt, because the
    refuse-to-grow check cannot run without a valid prior baseline.
    Both guards are bypassed when ``force`` is True (intended for
    bootstrap or emergency re-baselining after a deliberate scope
    change); a warning is printed.
    """
    new_total, raw_by_code = _summarize(output)
    new_by_code = {code: count for code, count in raw_by_code.items()}
    new_by_code_sorted = {k: new_by_code[k] for k in sorted(new_by_code)}

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
                    print("errors before regenerating the baseline.")
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

    new_baseline = {**metadata, "total_count": new_total, "by_code": new_by_code_sorted}
    BASELINE_PATH.write_text(json.dumps(new_baseline, indent=2) + "\n", encoding="utf-8")
    print(f"Regenerated {_display_path(BASELINE_PATH)}")
    print(f"  total_count = {new_total}")
    print(f"  by_code     = {json.dumps(new_by_code_sorted, sort_keys=True)}")
    if new_total == 0:
        print()
        print("Ratchet is now at zero — any new mypy error will fail the gate.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="mypy ratchet comparison script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rewrite mypy-baseline.json with the current error counts. "
        "Only use this after FIXING errors — refuses to grow the baseline "
        "and refuses to run when the baseline is missing/corrupt.",
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
        help="Read mypy output from stdin instead of running mypy. Useful for "
        "tests and one-off checks.",
    )
    args = parser.parse_args(argv)

    output = sys.stdin.read() if args.stdin else run_mypy()

    if args.regenerate:
        return regenerate(output, force=args.force)
    return compare(output)


if __name__ == "__main__":
    sys.exit(main())
