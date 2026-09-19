"""regression test: pyrefly-baseline.json must not contain stale entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "pyrefly-baseline.json"


def _file_line_count(path: Path) -> int:
    """Return the number of newline-terminated lines in ``path``."""
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def _classify_entry(entry: dict, repo_root: Path) -> str:
    """Return ``\"\"`` if the entry is fresh, else a human-readable staleness reason."""
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
    """Every entry in ``_triage`` must point at real, in-range code."""
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


def test_errors_entries_have_required_fields(baseline: dict) -> None:
    """Each error entry must have ``path`` and ``line`` fields."""
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


def test_comment_documents_current_errors_count(baseline: dict) -> None:
    """The ``_comment`` field must mention the current ``errors`` count."""
    errors = baseline.get("errors", [])
    comment = baseline.get("_comment", "")
    assert isinstance(comment, str) and comment, "baseline['_comment'] must be a non-empty string"
    current_count = len(errors)
    import re

    pattern = re.compile(rf"(?<!\d){current_count}(?!\d)")
    assert pattern.search(comment), (
        f"pyrefly-baseline.json: _comment does not mention the current "
        f"errors count ({current_count}). Update the _comment narrative "
        f"to reflect the post-cleanup floor. (This catches drift like "
        f" where _comment said '266' but the array held 264.)"
    )


# The documented provenance keys every baseline regeneration must carry
DOCUMENTED_METADATA_KEYS = [
    "_justification",
    "_schema_version",
    "_current_state_2026_07_25_rt_fix_11",
    "_current_state_2026_08_01_oi_16",
    "_current_state_2026_08_05_tk_fix_7",
    "_current_state_2026_08_06_regen",
    "_current_state_2026_08_14_fg_regen",
    "_current_state_2026_08_14_qwen_onnx_regen",
    "_current_state_2026_08_15_autostart_fix",
    "_current_state_2026_08_25_pkg_split_regen",
    "_current_state_2026_08_25_shutdown_split_recon",
    "_current_state_2026_09_07_sidecar_split_remap",
    "_current_state_2026_09_09_w3_bp141_recon",
]


@pytest.mark.parametrize("key", DOCUMENTED_METADATA_KEYS)
def test_documented_metadata_keys_survive_regeneration(baseline: dict, key: str) -> None:
    """The stale-entry cleanup paragraphs must be present."""
    assert key in baseline, (
        f"pyrefly-baseline.json: missing {key} metadata key. This key is "
        f"part of the documented provenance audit trail and must be carried "
        f"forward verbatim across regenerations."
    )
    value = baseline[key]
    if isinstance(value, str):
        assert len(value) > 20, (
            f"pyrefly-baseline.json: {key} must be a non-trivial description (got string of len={len(value)})"
        )


def test_errors_array_is_non_empty(baseline: dict) -> None:
    """The baseline must not be silently emptied to bypass the CI audit."""
    errors = baseline.get("errors", [])
    assert len(errors) >= 100, (
        f"pyrefly-baseline.json: errors array has only {len(errors)} "
        f"entries - expected >=100 (the codebase has known platform-"
        f"specific type debt; an empty or near-empty baseline suggests "
        f"the ratchet was bypassed rather than earned)."
    )
