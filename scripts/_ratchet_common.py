#!/usr/bin/env python3
"""Shared skeleton for the ratchet comparison scripts.

The repo's ratchet scripts (``ruff_ratchet_check.py``,
``mypy_ratchet_check.py``, ``coverage_ratchet_check.py``) grew as
copy-paste clones of one another: the same baseline load/validate
prelude, the same refuse-to-regrow regenerate flow (metadata preserve,
missing/corrupt-baseline guards, ``--force`` bypass), the same
count-comparison algorithm and table renderer, and the same argparse
flag shape — with only wording and paths differing. Duplicated gates
drift (one copy already had); this module is the single authoritative
skeleton so a gate fix lands once.

What lives here vs. in the adapter scripts:

* Here (script-independent): baseline prelude validation, the count
  schema check (``total_count`` + a ``by_*`` detail map), the
  count-comparison algorithm (union of keys, per-key REGRESSION /
  improved / ok classification, exit codes), the regenerate flow
  (metadata preserve + refuse-to-grow + missing/corrupt guards), the
  4-column table renderer, the shared argparse flags, and the
  path/display helpers (env redirection + repo-relative display).
* In each adapter (script-specific): the tool invocation (running
  ruff / mypy / coverage), the output→counts summarizer, the baseline
  path + env-var names, and every user-facing wording choice.

The coverage ratchet is a float ratchet (refuse-to-LOWER, not
refuse-to-grow) so it keeps its own compare/regenerate semantics; it
consumes the baseline prelude, the path/display helpers, and the
argparse flag builder.

Import contract: the scripts run directly (``python scripts/
ruff_ratchet_check.py`` → ``sys.path[0]`` is ``scripts/``) and tests
import them flat with ``scripts/`` on ``sys.path`` (see
``tests/test_coverage_ratchet_strict.py``), so the flat
``import _ratchet_common`` is required — the same pattern as
``scripts/_i18n_common.py``. A package-relative import would fail
under direct script execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────
# Repo root (this module lives in scripts/). Shared by the adapters so
# there is exactly one definition of "where is the project".
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def env_path(name: str, default: Path) -> Path:
    """Resolve a path from an env var, falling back to a default.

    ``RUFF_BASELINE_PATH`` / ``MYPY_BASELINE_PATH`` /
    ``COVERAGE_BASELINE_PATH`` (and ``RUFF_CURRENT_PATH``) let tests
    redirect all reads and writes to a temp file instead of the repo's
    real baseline, so an interrupted test run (timeout, kill, power
    loss) can never leave a fake baseline on disk.
    """
    raw = os.environ.get(name)
    return Path(raw) if raw else default


def display_path(path: Path, project_root: Path) -> str:
    """Render a path relative to the project root when possible."""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


# ── Baseline loading / validation ─────────────────────────────────────


def load_baseline_prelude(
    baseline_path: Path,
    *,
    required_fields: tuple[str, ...],
    create_hint: tuple[str, ...],
) -> dict[str, Any]:
    """Load a baseline JSON file and run the shared schema prelude.

    Enforces, for every ratchet baseline regardless of metric kind:

    * the file exists (exit 2, with the script-specific ``create_hint``
      telling the contributor how to bootstrap it);
    * it parses as JSON (exit 2);
    * its root is a JSON object (exit 2);
    * every required field is present (exit 2).

    Field-specific type checks (counts vs. a float percentage) stay in
    the caller. Exits the process (exit code 2 = "cannot evaluate")
    rather than returning a sentinel — a missing/corrupt floor must
    never be treated as a pass.
    """
    if not baseline_path.is_file():
        print(f"ERROR: baseline file not found: {baseline_path}")
        # Accept a bare string (single hint line) as well as a tuple of lines —
        # a parenthesized string literal is easy to mistake for a 1-tuple, and
        # iterating it unnormalized would print one character per line.
        if isinstance(create_hint, str):
            create_hint = (create_hint,)
        for line in create_hint:
            print(line)
        sys.exit(2)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: baseline file is not valid JSON: {exc}")
        sys.exit(2)
    if not isinstance(baseline, dict):
        print(f"ERROR: baseline root must be a JSON object, got {type(baseline).__name__}")
        sys.exit(2)
    for field in required_fields:
        if field not in baseline:
            print(f"ERROR: baseline missing required field '{field}'")
            sys.exit(2)
    return baseline


def load_counts_baseline(
    baseline_path: Path,
    *,
    detail_field: str,
    create_hint: tuple[str, ...],
) -> dict[str, Any]:
    """Load and validate a count-based baseline (ruff / mypy shape).

    Schema: ``total_count`` (non-negative int) plus a detail map named
    by ``detail_field`` (``"by_rule"`` / ``"by_code"``) mapping str keys
    to non-negative ints. The baseline MAY carry extra
    underscore-prefixed metadata (e.g. ``_comment``,
    ``_schema_version``) which is preserved on regenerate but ignored
    by the comparison logic.
    """
    baseline = load_baseline_prelude(
        baseline_path,
        required_fields=("total_count", detail_field),
        create_hint=create_hint,
    )
    if not isinstance(baseline["total_count"], int) or baseline["total_count"] < 0:
        print(f"ERROR: baseline.total_count must be a non-negative int, got {baseline['total_count']!r}")
        sys.exit(2)
    detail = baseline[detail_field]
    if not isinstance(detail, dict):
        print(f"ERROR: baseline.{detail_field} must be a JSON object, got {type(detail).__name__}")
        sys.exit(2)
    for key, count in detail.items():
        if not isinstance(key, str):
            print(f"ERROR: baseline.{detail_field} has non-string key: {key!r}")
            sys.exit(2)
        if not isinstance(count, int) or count < 0:
            print(f"ERROR: baseline.{detail_field}[{key!r}] must be a non-negative int, got {count!r}")
            sys.exit(2)
    return baseline


# ── Comparison table ──────────────────────────────────────────────────


def format_table(
    rows: list[tuple[str, int, int, str]],
    *,
    key_header: str,
) -> str:
    """Pretty-print comparison rows: (key, baseline, current, status).

    ``key_header`` is the per-script noun ("rule" for ruff, "code" for
    mypy); the empty-rows message is derived from it so both scripts
    keep their exact historical output.
    """
    if not rows:
        return f"  (no {key_header}s to display)"
    key_w = max(len(key_header), max(len(r) for r, _, _, _ in rows))
    base_w = max(len("baseline"), max(len(str(b)) for _, b, _, _ in rows))
    curr_w = max(len("current"), max(len(str(c)) for _, _, c, _ in rows))
    status_w = max(len("status"), max(len(s) for _, _, _, s in rows))
    header = f"  {key_header:<{key_w}}  {'baseline':>{base_w}}  {'current':>{curr_w}}  {'status':<{status_w}}"
    sep = f"  {'-' * key_w}  {'-' * base_w}  {'-' * curr_w}  {'-' * status_w}"
    lines = [header, sep]
    for key, b, c, s in rows:
        lines.append(f"  {key:<{key_w}}  {b:>{base_w}}  {c:>{curr_w}}  {s:<{status_w}}")
    return "\n".join(lines)


# ── Count comparison algorithm (ruff / mypy) ──────────────────────────


def compare_counts(
    baseline: dict[str, Any],
    current_counts: tuple[int, dict[str, int]],
    *,
    detail_field: str,
    key_header: str,
    unit: str,
    report_title: str,
    report_underline: str,
    baseline_display: str,
    scope_line: str | None,
    regenerate_cmd: str,
    full_output_header: str | None = None,
    current_output: str | None = None,
) -> int:
    """Compare current counts to the baseline; return the process exit code.

    The algorithm is the gate and is shared verbatim: the total must not
    grow, every individual detail key must not grow, and either failure
    returns exit code 1. Everything cosmetic (title, wording, the
    regenerate command to suggest, whether the full tool output is
    echoed) is supplied by the adapter so each script keeps its exact
    historical output.
    """
    base_total: int = baseline["total_count"]
    base_by: dict[str, int] = baseline[detail_field]
    curr_total, curr_by = current_counts

    # Build the union of detail keys for table rendering.
    all_keys = sorted(set(base_by) | set(curr_by))
    rows: list[tuple[str, int, int, str]] = []
    regressions: list[tuple[str, int, int]] = []
    for key in all_keys:
        b = base_by.get(key, 0)
        c = curr_by.get(key, 0)
        if c > b:
            status = "REGRESSION"
            regressions.append((key, b, c))
        elif c < b:
            status = "improved"
        else:
            status = "ok"
        rows.append((key, b, c, status))

    total_status = "REGRESSION" if curr_total > base_total else ("improved" if curr_total < base_total else "ok")

    print(report_title)
    print(report_underline)
    print(f"  Baseline file: {baseline_display}")
    if scope_line is not None:
        print(f"  {scope_line}")
    print(f"  Total: baseline={base_total}  current={curr_total}  status={total_status}")
    if curr_total < base_total:
        print("  -> Total IMPROVED. Consider regenerating the baseline to lock in the gain:")
        print(f"       {regenerate_cmd}")
    print()
    print(f"Per-{key_header} breakdown:")
    print(format_table(rows, key_header=key_header))

    if curr_total > base_total:
        print()
        print(f"FAIL: total {unit} count grew from {base_total} to {curr_total}.")
        print("The ratchet only allows counts to shrink. Either:")
        print(f"  1. Fix the new {unit}s introduced in this change, OR")
        print("  2. If the increase is intentional and unavoidable, document why")
        print("     in the PR description and regenerate the baseline:")
        print(f"       {regenerate_cmd}")
        if full_output_header is not None and current_output is not None:
            print()
            print(full_output_header)
            print(current_output)
        return 1

    if regressions:
        print()
        print(f"FAIL: per-{key_header} regression detected (even though total did not grow):")
        for key, b, c in regressions:
            print(f"  {key}: {b} -> {c}")
        print(f"The ratchet requires every individual {key_header} count to be non-increasing.")
        print(f"Fix the new {unit}s OR regenerate the baseline with justification.")
        if full_output_header is not None and current_output is not None:
            print()
            print(full_output_header)
            print(current_output)
        return 1

    print()
    print(f"PASS: ratchet holds (total {curr_total} <= baseline {base_total}).")
    return 0


# ── Regenerate flow (ruff / mypy count baselines) ─────────────────────


def regenerate_counts_baseline(
    baseline_path: Path,
    new_total: int,
    new_by: dict[str, int],
    *,
    detail_field: str,
    unit: str,
    zero_note: str,
    force: bool,
    project_root: Path,
) -> int:
    """Rewrite a count-based baseline with the current counts; return exit code.

    Guards (the gate semantics, shared verbatim):

    * Refuses to write if the new total is HIGHER than the old total —
      that would be a regression, not a ratchet. The guard is
      total-only; a tool upgrade that re-buckets existing errors
      across codes at the same total still regenerates (compare mode
      blocks per-key growth on every check either way).
    * Refuses to regenerate when the existing baseline is missing or
      corrupt: the refuse-to-grow check cannot run without a valid
      prior floor, so a regeneration could silently lock in a
      regression.
    * Both guards are bypassed when ``force`` is True (bootstrap or
      emergency re-baselining after a deliberate scope change); a
      warning is printed.

    Underscore-prefixed metadata fields on the existing baseline are
    preserved; the detail map is written sorted for deterministic
    diffs.
    """
    new_by_sorted = {k: new_by[k] for k in sorted(new_by)}

    metadata: dict[str, Any] = {}
    if baseline_path.is_file():
        try:
            old = json.loads(baseline_path.read_text(encoding="utf-8"))
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
                if k in ("total_count", detail_field):
                    continue
                metadata[k] = v
            old_total = old.get("total_count")
            if isinstance(old_total, int) and new_total > old_total:
                if not force:
                    print(f"REFUSED: new total ({new_total}) > old total ({old_total}).")
                    print("The ratchet only allows counts to shrink. Fix the new")
                    print(f"{unit}s before regenerating the baseline.")
                    print("If the increase is intentional and unavoidable, document")
                    print("why in the PR description and re-run with --force.")
                    return 1
                print(
                    f"WARNING: --force bypassing refuse-to-grow check (new total {new_total} > old total {old_total})."
                )
    elif not force:
        print(f"ERROR: baseline file not found: {baseline_path}")
        print("Refusing to regenerate without --force. A missing baseline means")
        print("the refuse-to-grow check cannot run, so a regeneration could")
        print("silently lock in a regression.")
        print("To override (e.g. for bootstrap), re-run with --force.")
        return 1
    else:
        print("WARNING: --force bypassing missing baseline;")
        print("         refuse-to-grow check skipped.")

    new_baseline = {**metadata, "total_count": new_total, detail_field: new_by_sorted}
    baseline_path.write_text(
        json.dumps(new_baseline, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Regenerated {display_path(baseline_path, project_root)}")
    print(f"  total_count = {new_total}")
    print(f"  {detail_field}     = {json.dumps(new_by_sorted, sort_keys=True)}")
    if new_total == 0:
        print()
        print(zero_note)
    return 0


# ── Shared argparse flags ─────────────────────────────────────────────


def build_ratchet_parser(
    *,
    description: str,
    epilog: str,
    regenerate_help: str,
    force_help: str,
) -> argparse.ArgumentParser:
    """Build the ratchet CLI parser with the shared flag set.

    Every ratchet script exposes the same contract:

    * ``--regenerate`` — rewrite the baseline with the current counts /
      percentage (refuses to regress);
    * ``--force`` — bypass the refuse-to-regress check and the
      missing/corrupt-baseline guard.

    Script-specific flags (``--stdin``, ``--current-path``,
    ``--coverage-xml``, ``--strict``) are added by the adapter so the
    flag order and help text each script already documents are
    preserved.
    """
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help=regenerate_help,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=force_help,
    )
    return parser
