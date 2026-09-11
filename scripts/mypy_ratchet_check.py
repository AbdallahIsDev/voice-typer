#!/usr/bin/env python3
"""mypy ratchet comparison script.

Compares the current mypy error counts on the server scope
(``voice_typer/server/``) against a committed baseline
(``mypy-baseline.json``). This exists because mypy is NOT a clean gate
for this codebase today: numpy 2.x's PEP 695 stubs cannot be parsed at
the project's mypy language level (``python_version = "3.10"``, CQ-058),
so numpy is shadowed by a local Any-stub (``voice_typer/mypy_stubs/``),
which lets mypy type-check every server module, exposing ~700 latent
typing-debt errors that were previously hidden by the numpy parse
failure. Those errors are tracked here instead of being fixed all at
once.

The comparison algorithm, baseline validation, refuse-to-regrow
regenerate flow, table rendering, and argparse flag shape live in
``scripts/_ratchet_common.py`` (shared with the ruff ratchet so gate
fixes land once); this module is the mypy-specific adapter, the mypy
            invocation, the output->counts summarizer, and the user-facing wording.

CI policy
---------
* ``total_count`` MUST NOT grow.
* Per-code counts in ``by_code`` MUST NOT grow.
* Counts MAY shrink, that's the whole point of the ratchet.
* When a count shrinks, contributors SHOULD regenerate the baseline so
  the new (lower) number becomes the floor (see ``--regenerate``).

Usage
-----
Run from the project root.

1. Check mode (default), runs mypy and compares against the baseline::

       python scripts/mypy_ratchet_check.py

   Exit code 0 if current <= baseline for every code (and total).
   Exit code 1 if any code (or total) grew above the baseline. In that
   case the full mypy output is printed so the new errors can be found.

2. Regenerate baseline (only run locally after FIXING errors)::

       python scripts/mypy_ratchet_check.py --regenerate

   Rewrites ``mypy-baseline.json`` with the current error counts.
   The script REFUSES to regenerate if the new total is HIGHER than
   the old total, that would be a regression, not a ratchet. Pass
   ``--force`` to bootstrap a missing baseline (prints a warning).

3. Stdin mode (for tests / one-off checks without running mypy)::

       python -m mypy voice_typer/server/ | python scripts/mypy_ratchet_check.py --stdin
       python -m mypy voice_typer/server/ | python scripts/mypy_ratchet_check.py --regenerate --stdin --force
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter

from _ratchet_common import (
    PROJECT_ROOT,
    build_ratchet_parser,
    compare_counts,
    display_path,
    env_path,
    load_counts_baseline,
    regenerate_counts_baseline,
)

# The mypy scope the ratchet guards. Must stay in sync with the
# Makefile `typecheck` target and `.pre-commit-config.yaml`.
MYPY_TARGET = "voice_typer/server/"

BASELINE_PATH = env_path("MYPY_BASELINE_PATH", PROJECT_ROOT / "mypy-baseline.json")

# Wordings shared by the compare/regenerate entry points below.
CREATE_HINT = (
    "Create it with: python scripts/mypy_ratchet_check.py --regenerate --force"
    "  (or pipe mypy output: python -m mypy voice_typer/server/ | "
    "python scripts/mypy_ratchet_check.py --regenerate --stdin --force)"
)
REGENERATE_CMD = "python scripts/mypy_ratchet_check.py --regenerate"
FULL_OUTPUT_HEADER = "Full mypy output (to locate the new errors):"

# Matches a mypy error line:
#   path:line: error: message [code]
# The code suffix is optional in older mypy output; such lines are
# bucketed under "(untyped)". The file part is anchored with \S so an
# indented continuation / context line (which mypy prefixes with
# whitespace) can never be miscounted as a new error.
_ERROR_RE = re.compile(r"^(?P<file>\S.*?):(?P<line>\d+): error: (?P<message>.*?)(?: \[(?P<code>[a-z][a-z-]*)\])?$")


def run_mypy() -> str:
    """Run mypy on the server scope and return its combined output.

    mypy exits 0 (clean), 1 (type errors found, expected), or 2+
    (internal failure). A return code above 1 means the gate cannot
    evaluate and is treated as a hard error rather than a silent pass.
    """
    cmd = [sys.executable, "-m", "mypy", MYPY_TARGET]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=600)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode not in (0, 1):
        print(f"ERROR: mypy failed to run (exit {proc.returncode}):")
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


def compare(output: str) -> int:
    """Compare current mypy output to baseline. Return process exit code."""
    baseline = load_counts_baseline(BASELINE_PATH, detail_field="by_code", create_hint=CREATE_HINT)
    return compare_counts(
        baseline,
        _summarize(output),
        detail_field="by_code",
        key_header="code",
        unit="error",
        report_title="mypy ratchet comparison",
        report_underline="=======================",
        baseline_display=display_path(BASELINE_PATH, PROJECT_ROOT),
        scope_line=f"Scope:         {MYPY_TARGET}",
        regenerate_cmd=REGENERATE_CMD,
        full_output_header=FULL_OUTPUT_HEADER,
        current_output=output,
    )


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
    new_total, new_by_code = _summarize(output)
    return regenerate_counts_baseline(
        BASELINE_PATH,
        new_total,
        new_by_code,
        detail_field="by_code",
        unit="error",
        zero_note="Ratchet is now at zero, any new mypy error will fail the gate.",
        force=force,
        project_root=PROJECT_ROOT,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_ratchet_parser(
        description="mypy ratchet comparison script.",
        epilog=__doc__,
        regenerate_help=(
            "Rewrite mypy-baseline.json with the current error counts. "
            "Only use this after FIXING errors, refuses to grow the baseline "
            "and refuses to run when the baseline is missing/corrupt."
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
        help="Read mypy output from stdin instead of running mypy. Useful for tests and one-off checks.",
    )
    args = parser.parse_args(argv)

    output = sys.stdin.read() if args.stdin else run_mypy()

    if args.regenerate:
        return regenerate(output, force=args.force)
    return compare(output)


if __name__ == "__main__":
    sys.exit(main())
