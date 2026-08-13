"""regression test: pyrefly-baseline.json must not contain stale entries.

A baseline entry is "stale" iff EITHER:
  - its `path` field points to a file that no longer exists on disk
    (e.g. log.py was refactored into the log/ package), OR
  - its `line` field is past the EOF of the file it points to
    (e.g. ipc_server.py:1103 but the file is 713 lines).

Stale entries make the CI audit step in `.github/workflows/build.yml`
unreliable: the audit compares the LIVE pyrefly output count against
``len(baseline['errors'])``, so a stale entry inflates the floor and
silently hides new regressions. This test fails on any stale entry so a
future refactor that leaves dangling references in the baseline is
caught before merge.

AGENTS.md forbids artificially shrinking the baseline to hide real
errors. This test does NOT validate that the baseline is "complete" -
it only validates that every entry it DOES contain points at real,
in-range code. Adding new entries for real errors is always allowed;
leaving stale entries behind is not.

The test runs on LINUX but the baseline is platform-agnostic (paths are
relative to the repo root, line numbers are file-anchored). It does
NOT invoke pyrefly (the CI step does that with pyrefly==1.11.1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "pyrefly-baseline.json"


def _file_line_count(path: Path) -> int:
    """Return the number of newline-terminated lines in ``path``.

    Matches pyrefly's own line-counting convention (1-indexed; a file
    with N newlines has N lines, and a final unterminated line still
    counts as a line). Returns 0 if the file does not exist.
    """
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def _classify_entry(entry: dict, repo_root: Path) -> str:
    """Return ``""`` if the entry is fresh, else a human-readable staleness reason."""
    raw_path = entry.get("path")
    line = entry.get("line")
    if not raw_path or not isinstance(raw_path, str):
        return f"missing-or-invalid path field (got {raw_path!r})"
    abs_path = repo_root / raw_path
    if not abs_path.exists():
        return f"file does not exist: {raw_path}"
    if not isinstance(line, int) or line < 1:
        return f"invalid line field (got {line!r}) for {raw_path}"
    n = _file_line_count(abs_path)
    if line > n:
        return f"line {line} past EOF ({n} lines) of {raw_path}"
    return ""


@pytest.fixture(scope="module")
def baseline() -> dict:
    """Load pyrefly-baseline.json once for the module."""
    assert BASELINE_PATH.exists(), f"pyrefly-baseline.json not found at {BASELINE_PATH}"
    with BASELINE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Stale-entry regression: every entry in errors[] and _triage[] must
# point at a file that exists and a line within that file's range.
# ---------------------------------------------------------------------


def _collect_stale(entries: list[dict], repo_root: Path, array_name: str) -> list[tuple[int, str, dict]]:
    """Return ``[(index, reason, entry), ...]`` for every stale entry."""
    stale: list[tuple[int, str, dict]] = []
    for i, entry in enumerate(entries):
        reason = _classify_entry(entry, repo_root)
        if reason:
            stale.append((i, reason, entry))
    return stale


def test_errors_array_has_no_stale_entries(baseline: dict) -> None:
    """Every entry in ``errors`` must point at real, in-range code."""
    errors = baseline.get("errors", [])
    assert isinstance(errors, list), "baseline['errors'] must be a list"
    stale = _collect_stale(errors, REPO_ROOT, "errors")
    if stale:
        lines = [f"  [{i}] {e.get('path')}:{e.get('line')} -- {reason}" for i, reason, e in stale[:20]]
        tail = f"\n  ... and {len(stale) - 20} more" if len(stale) > 20 else ""
        pytest.fail(
            f"pyrefly-baseline.json: {len(stale)} stale entries in "
            f"`errors` array (of {len(errors)} total). Each stale entry "
            f"must be either remapped to its live location or dropped.\n" + "\n".join(lines) + tail
        )


def test_triage_array_has_no_stale_entries(baseline: dict) -> None:
    """Every entry in ``_triage`` must point at real, in-range code.

    The ``_triage`` array is a filtered view of ``errors`` (non-platform-
    specific subset) used for one-by-one bug assignment. Stale entries
    here defeat the purpose of the triage.
    """
    triage = baseline.get("_triage", [])
    if not isinstance(triage, list):
        pytest.fail("baseline['_triage'] must be a list")
    stale = _collect_stale(triage, REPO_ROOT, "_triage")
    if stale:
        lines = [f"  [{i}] {e.get('path')}:{e.get('line')} -- {reason}" for i, reason, e in stale[:20]]
        tail = f"\n  ... and {len(stale) - 20} more" if len(stale) > 20 else ""
        pytest.fail(
            f"pyrefly-baseline.json: {len(stale)} stale entries in "
            f"`_triage` array (of {len(triage)} total). Each stale entry "
            f"must be either remapped to its live location or dropped.\n" + "\n".join(lines) + tail
        )


# ---------------------------------------------------------------------
# Schema sanity: required fields are present on every entry.
# ---------------------------------------------------------------------


def test_errors_entries_have_required_fields(baseline: dict) -> None:
    """Each error entry must have ``path`` and ``line`` fields.

    These are the fields the staleness check depends on; a missing
    field would silently pass the staleness check above (the check
    treats missing-path as stale, but a malformed entry that the CI
    audit step also can't interpret should fail loudly here).
    """
    required = {"path", "line"}
    errors = baseline.get("errors", [])
    missing: list[tuple[int, set]] = []
    for i, e in enumerate(errors):
        if not isinstance(e, dict):
            missing.append((i, {"<not-a-dict>"}))
            continue
        absent = required - set(e.keys())
        if absent:
            missing.append((i, absent))
    if missing:
        pytest.fail(
            f"pyrefly-baseline.json: {len(missing)} `errors` entries are "
            f"missing required fields {required}: {missing[:10]}"
        )


# ---------------------------------------------------------------------
# Metadata drift regression : the baseline must document its
# current count. The CI audit step reads the live count from
# pyrefly-current.json and compares it to len(errors). The _comment
# string is human-readable documentation; we only assert that the
# post-cleanup count is mentioned so future drift is caught.
# ---------------------------------------------------------------------


def test_comment_documents_current_errors_count(baseline: dict) -> None:
    """The ``_comment`` field must mention the current ``errors`` count.

    Catches metadata drift like  (where _comment said "266" but
    the array actually held 264 entries). We look for the literal
    decimal representation of ``len(errors)`` somewhere in ``_comment``
    so a maintainer who shrinks the array is reminded to update the
    narrative.
    """
    errors = baseline.get("errors", [])
    comment = baseline.get("_comment", "")
    assert isinstance(comment, str) and comment, "baseline['_comment'] must be a non-empty string"
    current_count = len(errors)
    # The count must appear as a standalone decimal token (not as a
    # substring of a larger number like "2160" matching "216").
    import re

    pattern = re.compile(rf"(?<!\d){current_count}(?!\d)")
    assert pattern.search(comment), (
        f"pyrefly-baseline.json: _comment does not mention the current "
        f"errors count ({current_count}). Update the _comment narrative "
        f"to reflect the post-cleanup floor. (This catches drift like "
        f" where _comment said '266' but the array held 264.)"
    )


def test_current_state_tk_fix_7_key_exists(baseline: dict) -> None:
    """The  cleanup paragraph must be present.

    Documents the cleanup so future maintainers can trace why entries
    were dropped. The key name is intentionally timestamped
    (2026_08_05) to match the existing ``_current_state_*`` convention.
    """
    key = "_current_state_2026_08_05_tk_fix_7"
    assert key in baseline, (
        f"pyrefly-baseline.json: missing {key} metadata key. This key documents the  stale-entry cleanup."
    )
    value = baseline[key]
    assert isinstance(value, str) and len(value) > 100, (
        f"pyrefly-baseline.json: {key} must be a non-trivial description "
        f"of the  cleanup (got {type(value).__name__} of "
        f"len={len(value) if isinstance(value, str) else 'n/a'})"
    )


# ---------------------------------------------------------------------
# Floor sanity: the errors array must be non-empty (the codebase has
# known platform-specific type debt; an empty array would mean someone
# wiped the baseline to bypass the audit).
# ---------------------------------------------------------------------


def test_errors_array_is_non_empty(baseline: dict) -> None:
    """The baseline must not be silently emptied to bypass the CI audit.

    Per AGENTS.md: "Never delete, regenerate, or modify baseline
    files to artificially reduce error counts." An empty `errors`
    array would make the CI audit step compare live_count > 0, which
    would fail on the first real error - but it would also be a
    strong signal that someone tried to bypass the ratchet. The
    codebase has known platform-specific type debt (see _justification),
    so the array should be substantively populated.
    """
    errors = baseline.get("errors", [])
    assert len(errors) >= 100, (
        f"pyrefly-baseline.json: errors array has only {len(errors)} "
        f"entries - expected >=100 (the codebase has known platform-"
        f"specific type debt; an empty or near-empty baseline suggests "
        f"the ratchet was bypassed rather than earned)."
    )
