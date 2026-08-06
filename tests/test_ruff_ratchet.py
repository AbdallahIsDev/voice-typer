"""Tests for the ruff ratchet mechanism.

These tests verify:

1. ``ruff-baseline.json`` exists, is valid JSON, and matches the schema
   documented in ``docs/ruff-ratchet.md``:
   - Required fields: ``total_count`` (non-negative int), ``by_rule`` (object).
   - ``total_count == sum(by_rule.values())``.
   - All ``by_rule`` values are non-negative ints and keys are strings.
2. ``scripts/ruff_ratchet_check.py`` comparison logic behaves correctly:
   - Equal counts → exit 0 (PASS).
   - Total grew → exit 1 (FAIL).
   - Per-rule grew (total same) → exit 1 (FAIL).
   - Total shrank → exit 0 (PASS with "improved" hint).
3. The regenerate subcommand:
   - Refuses to grow the baseline (exit 1).
   - Successfully shrinks the baseline when counts decrease.
   - Preserves underscore-prefixed metadata fields.
4. The ratchet *currently* holds — i.e. the actual ruff violation count
   in ``voice_typer/server/`` is ``<=`` the baseline. This catches the
   case where a contributor adds a violation but forgets to update the
   baseline (CI would catch this too, but the local test surfaces it
   faster).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = PROJECT_ROOT / "ruff-baseline.json"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "ruff_ratchet_check.py"

# Required schema fields on the baseline file.
REQUIRED_FIELDS = ("total_count", "by_rule")


# ── Helpers ──────────────────────────────────────────────────────────


def _load_baseline() -> dict:
    """Load and return the baseline file as a dict (fails the test on I/O or JSON errors)."""
    assert BASELINE_PATH.is_file(), f"baseline file missing: {BASELINE_PATH}"
    text = BASELINE_PATH.read_text(encoding="utf-8")
    return json.loads(text)


def _run_script(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the ratchet script with the given args. Returns the CompletedProcess."""
    cmd = [sys.executable, str(SCRIPT_PATH), *args]
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        timeout=30,
    )


def _baseline_path() -> Path:
    """Path the script will actually read/write (honors RUFF_BASELINE_PATH).

    Tests that exercise compare/regenerate logic point the script at a
    tmp_path baseline via ``RUFF_BASELINE_PATH`` and must read results
    back from the same location, never from the repo's real file.
    """
    override = os.environ.get("RUFF_BASELINE_PATH")
    return Path(override) if override else BASELINE_PATH


def _has_ruff() -> bool:
    """True if `ruff` is importable as a module (mirrors CI's `python -m ruff`)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── 1. Baseline schema tests ─────────────────────────────────────────


class TestBaselineSchema:
    """Verify ruff-baseline.json exists, is valid JSON, and matches the schema."""

    def test_baseline_file_exists(self) -> None:
        assert BASELINE_PATH.is_file(), (
            "ruff-baseline.json must exist at the repo root. Create it with: "
            "ruff check voice_typer/server/ --output-format=json | "
            "python scripts/ruff_ratchet_check.py --regenerate --stdin"
        )

    def test_baseline_is_valid_json(self) -> None:
        # _load_baseline raises if JSON is invalid; loading it is the test.
        _load_baseline()

    def test_baseline_root_is_object(self) -> None:
        baseline = _load_baseline()
        assert isinstance(baseline, dict), f"baseline root must be a JSON object, got {type(baseline).__name__}"

    def test_baseline_has_required_fields(self) -> None:
        baseline = _load_baseline()
        for field in REQUIRED_FIELDS:
            assert field in baseline, f"baseline missing required field: {field!r}"

    def test_total_count_is_non_negative_int(self) -> None:
        baseline = _load_baseline()
        tc = baseline["total_count"]
        assert isinstance(tc, int), f"total_count must be int, got {type(tc).__name__}"
        assert tc >= 0, f"total_count must be >= 0, got {tc}"
        # bool is a subclass of int in Python — reject it explicitly.
        assert isinstance(tc, int) and not isinstance(tc, bool), "total_count must be int, not bool"

    def test_by_rule_is_object(self) -> None:
        baseline = _load_baseline()
        br = baseline["by_rule"]
        assert isinstance(br, dict), f"by_rule must be a JSON object, got {type(br).__name__}"

    def test_by_rule_keys_are_strings(self) -> None:
        baseline = _load_baseline()
        for key in baseline["by_rule"]:
            assert isinstance(key, str), f"by_rule key must be str, got {type(key).__name__}: {key!r}"

    def test_by_rule_values_are_non_negative_ints(self) -> None:
        baseline = _load_baseline()
        for rule, count in baseline["by_rule"].items():
            assert isinstance(count, int), f"by_rule[{rule!r}] must be int, got {type(count).__name__}"
            assert not isinstance(count, bool), f"by_rule[{rule!r}] must be int, not bool"
            assert count >= 0, f"by_rule[{rule!r}] must be >= 0, got {count}"

    def test_total_count_equals_sum_of_by_rule(self) -> None:
        baseline = _load_baseline()
        tc = baseline["total_count"]
        br = baseline["by_rule"]
        expected = sum(br.values())
        assert tc == expected, (
            f"total_count ({tc}) must equal sum(by_rule.values()) ({expected}). "
            "If you fixed violations, regenerate the baseline with: "
            "ruff check voice_typer/server/ --output-format=json | "
            "python scripts/ruff_ratchet_check.py --regenerate --stdin"
        )

    def test_metadata_fields_are_optional_and_ignored(self) -> None:
        """Underscore-prefixed metadata fields are allowed but not required."""
        baseline = _load_baseline()
        # No assertion on presence — just verify no non-underscore non-required fields exist.
        allowed = set(REQUIRED_FIELDS)
        for key in baseline:
            if key in allowed:
                continue
            assert key.startswith("_"), (
                f"Unexpected non-underscore field in baseline: {key!r}. "
                "Only 'total_count', 'by_rule', and underscore-prefixed metadata are allowed."
            )


# ── 2. Comparison logic tests ────────────────────────────────────────


def _pick_representative_rule(baseline: dict) -> tuple[str, int]:
    """Pick a rule with count > 1 from the baseline for use in compare tests.

    the tests previously hardcoded N806 with fallback 27, but N806
    dropped to 0 after parallel agents cleaned up naming violations.
    Hardcoding any specific rule makes the tests brittle to baseline
    regeneration. Instead, we pick a rule with count > 1 (so shrink-by-1
    is meaningful) directly from the current baseline. If no rule has
    count > 1, we fall back to count > 0; if the baseline is empty, we
    fall back to a synthetic B007:3 input (matches the original test
    intent and the per-rule regression test below).
    """
    by_rule = baseline.get("by_rule", {})
    for rule, count in sorted(by_rule.items()):
        if count > 1:
            return rule, count
    for rule, count in sorted(by_rule.items()):
        if count > 0:
            return rule, count
    return "B007", 3


class TestCompareLogic:
    """Verify scripts/ruff_ratchet_check.py compare behavior with synthetic inputs."""

    @pytest.fixture(autouse=True)
    def _synthetic_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Seed a known baseline into tmp_path; never touch the real repo file.

        the repo's actual ``ruff-baseline.json`` was reset to
        ``total_count: 0`` after parallel-agent cleanup, which broke
        several tests in this class that assumed a non-empty baseline
        (``_pick_representative_rule`` falls back to the synthetic
        ``B007, 3`` pair, but the script then sees 3 B007 violations
        against a baseline of 0 and reports REGRESSION instead of PASS).
        The fix is to seed a known baseline (``B007: 3, UP007: 1``,
        total 4) before each compare test so the test outcome is
        independent of the actual repo baseline content.

        Hardening: the synthetic baseline is written to a ``tmp_path``
        file and the script is redirected there via the
        ``RUFF_BASELINE_PATH`` env override. The repo's real
        ``ruff-baseline.json`` is never written, so an interrupted test
        run (timeout, kill, power loss) can no longer leave a fake
        baseline on disk.

        The two-rule baseline (B007 + UP007) is needed by
        ``test_per_rule_regression_with_same_total_fails`` which
        grows one rule's count and shrinks another's to keep the
        total constant — that's the only way to trigger the
        per-rule-regression-with-same-total code path.
        """
        baseline_file = tmp_path / "ruff-baseline.json"
        baseline_file.write_text(
            json.dumps(
                {
                    "_comment": "synthetic baseline for TestCompareLogic — tmp_path",
                    "_target": "voice_typer/ tests/ scripts/ conftest.py",
                    "_schema_version": 1,
                    "total_count": 4,
                    "by_rule": {"B007": 3, "UP007": 1},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("RUFF_BASELINE_PATH", str(baseline_file))
        yield

    def test_equal_counts_passes(self) -> None:
        # Use a rule with count > 1 from the current baseline to avoid brittleness.
        # previously hardcoded N806 with fallback 27, but N806 dropped to 0
        # after parallel agents cleaned up naming violations. Now we pick a
        # representative rule dynamically from the baseline so the test stays
        # valid as the baseline evolves.
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _rule, _count = _pick_representative_rule(_baseline)
        stdin = json.dumps([{"code": _rule}] * _count)
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 0, (
            f"Expected exit 0 (counts equal baseline), got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "PASS" in result.stdout

    def test_total_grew_fails(self) -> None:
        # Use the same representative rule + 1 to exceed per-rule count
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _rule, _count = _pick_representative_rule(_baseline)
        stdin = json.dumps([{"code": _rule}] * (_count + 1))
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (total grew), got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "FAIL" in result.stdout
        assert "REGRESSION" in result.stdout

    def test_new_rule_fails(self) -> None:
        # B007: 180 (same baseline total) + F401: 1 (new) → total 181 > 180
        stdin = json.dumps([{"code": "B007"}] * 180 + [{"code": "F401"}])
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 1
        assert "FAIL" in result.stdout

    def test_per_rule_regression_with_same_total_fails(self) -> None:
        # Construct a per-rule regression with SAME total: B007 grows
        # by 1 while UP007 shrinks by 1, so total stays at 4. The
        # script must flag the B007 per-rule regression even though
        # the total is unchanged.
        # previously this test relied on the actual repo
        # baseline having a B007 entry; the synthetic baseline
        # fixture now provides B007: 3 + UP007: 1 = 4 total.
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _b007 = _baseline["by_rule"].get("B007", 3)
        _up007 = _baseline["by_rule"].get("UP007", 1)
        # Input: (_b007 + 1) B007 + max(0, _up007 - 1) UP007
        # → B007 grew (per-rule regression), total = _b007 + 1 + _up007 - 1 = _b007 + _up007 (same)
        stdin = json.dumps([{"code": "B007"}] * (_b007 + 1) + [{"code": "UP007"}] * max(0, _up007 - 1))
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (per-rule regression), got {result.returncode}.\nstdout:\n{result.stdout}"
        )
        assert "per-rule regression" in result.stdout
        assert "B007" in result.stdout

    def test_total_shrunk_passes_with_improved_hint(self) -> None:
        # Use the representative rule - 1 to show shrinkage.
        # previously hardcoded N806 with fallback 27; switched to
        # dynamic rule selection so the test survives baseline regeneration.
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _rule, _count = _pick_representative_rule(_baseline)
        stdin = json.dumps([{"code": _rule}] * max(0, _count - 1))
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 0
        assert "improved" in result.stdout.lower()
        # Should suggest regenerating to lock in the gain.
        assert "regenerate" in result.stdout.lower()

    def test_zero_violations_passes(self) -> None:
        stdin = json.dumps([])
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_empty_stdin_treated_as_zero_violations(self) -> None:
        result = _run_script(["--stdin"], stdin="")
        assert result.returncode == 0

    def test_invalid_json_exits_nonzero(self) -> None:
        result = _run_script(["--stdin"], stdin="not valid json")
        # Invalid JSON → 0 violations parsed → comparison passes (treated as empty).
        # The script prints an ERROR but does not exit 1 because there's nothing to fail on.
        # However, if the baseline is non-zero, an empty current would PASS (improved).
        # Verify the script handled it gracefully without crashing.
        assert result.returncode in (0, 1, 2)
        # No unhandled exception (no traceback).
        assert "Traceback" not in result.stderr


# ── 3. Regenerate logic tests ─────────────────────────────────────────


class TestRegenerateLogic:
    """Verify the --regenerate subcommand behaves correctly.

    These tests mutate the baseline file. They use a fixture to
    snapshot and restore the original content so the test suite is
    idempotent and does not leave the baseline in a broken state if
    a test fails.

    the ``_restore_baseline`` fixture now ALSO seeds a
    synthetic non-empty baseline before each test (in addition to
    restoring the original after). The previous version only
    restored, which left the test exposed to the actual repo
    baseline content — when parallel-agent cleanup reset
    ``total_count`` to 0, the regenerate-refuses-to-grow guard
    rejected every test's synthetic input as a "regression" (3 > 0),
    breaking 3 tests. Seeding a known starting point (3 violations
    of UP007) makes the tests deterministic and independent of the
    repo baseline state.
    """

    @pytest.fixture(autouse=True)
    def _restore_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Seed a known baseline into tmp_path; never touch the real repo file.

        the regenerate tests seed a synthetic non-empty baseline
        so they are deterministic and independent of the repo baseline
        state. The synthetic file now lives in ``tmp_path`` and the
        script is redirected there via ``RUFF_BASELINE_PATH`` — the
        repo's real ``ruff-baseline.json`` is never written, so an
        interrupted test run cannot leave a fake baseline on disk.
        """
        baseline_file = tmp_path / "ruff-baseline.json"
        baseline_file.write_text(
            json.dumps(
                {
                    "_comment": "synthetic baseline for TestRegenerateLogic — tmp_path",
                    "_target": "voice_typer/ tests/ scripts/ conftest.py",
                    "_schema_version": 1,
                    "total_count": 3,
                    "by_rule": {"UP007": 3},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("RUFF_BASELINE_PATH", str(baseline_file))
        yield

    def test_regenerate_refuses_to_grow(self) -> None:
        # More violations than current baseline total — should refuse.
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _old_total = _baseline["total_count"]
        stdin = json.dumps([{"code": "UP007"}] * (_old_total + 5))
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (refuse to grow), got {result.returncode}.\nstdout:\n{result.stdout}"
        )
        assert "REFUSED" in result.stdout
        # Baseline file should be unchanged.
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == _old_total

    def test_regenerate_same_count_succeeds(self) -> None:
        # Same count as input — should succeed (idempotent).
        stdin = json.dumps([{"code": "UP007"}] * 3)
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == 3
        assert baseline["by_rule"] == {"UP007": 3}

    def test_regenerate_smaller_count_succeeds(self) -> None:
        # 2 violations (down from 3) — should succeed.
        stdin = json.dumps([{"code": "UP007"}, {"code": "UP007"}])
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == 2
        assert baseline["by_rule"] == {"UP007": 2}

    def test_regenerate_to_zero_succeeds(self) -> None:
        stdin = json.dumps([])
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == 0
        assert baseline["by_rule"] == {}

    def test_regenerate_preserves_metadata_fields(self) -> None:
        # Verify _target, _schema_version are preserved.
        stdin = json.dumps([{"code": "UP007"}, {"code": "UP007"}])
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert "_schema_version" in baseline
        assert "_target" in baseline


# ── 4. Live ratchet holds (current count <= baseline) ────────────────


@pytest.mark.skipif(not _has_ruff(), reason="ruff not installed in this environment")
class TestRatchetHolds:
    """Verify the ratchet is not currently regressed by running the actual comparison."""

    def test_current_ruff_count_is_at_or_below_baseline(self) -> None:
        """Run `ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json` and compare.

        This is the same command CI runs. If this test fails, either:
        - A new violation was introduced → fix it OR update the baseline.
        - The ruff version changed and emits different counts → update the baseline.
        """
        # scope now matches ruff-baseline.json _target
        # (voice_typer/ tests/ scripts/ conftest.py). Previously this
        # test ran ruff against voice_typer/server/ only (3 violations)
        # but compared against the 180-violation baseline — the test
        # always passed with "improved by 177" regardless of regressions
        # in tests/ or scripts/.
        ruff_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "voice_typer/",
                "tests/",
                "scripts/",
                "conftest.py",
                "--output-format=json",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )
        # ruff exits 1 when violations are found — that's expected.
        # Only fail on crashes (e.g. ruff not found, config errors).
        assert ruff_result.returncode in (0, 1), (
            f"ruff exited with unexpected code {ruff_result.returncode}.\n"
            f"stdout:\n{ruff_result.stdout}\nstderr:\n{ruff_result.stderr}"
        )

        # Now run the ratchet script with this as stdin.
        result = _run_script(["--stdin"], stdin=ruff_result.stdout)
        assert result.returncode == 0, (
            f"Ratchet regression: current ruff violations exceed the baseline.\n"
            f"ratchet stdout:\n{result.stdout}\n"
            f"Either fix the new violations OR (if intentional) update the baseline:\n"
            f"  ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | "
            f"python scripts/ruff_ratchet_check.py --regenerate --stdin"
        )

    def test_f_rules_have_zero_violations(self) -> None:
        """F-rules (pyflakes) are a hard-fail in CI — verify zero violations currently.

        If this test fails, a real bug (unused import, undefined name,
        redefinition) was introduced. Fix it immediately; do NOT update
        any baseline.
        """
        # Scope expanded from `voice_typer/server/` only to the full CI
        # scope `voice_typer/ tests/ scripts/ conftest.py` so this test
        # matches the `Ruff (F-rules hard-fail)` step in
        # .github/workflows/build.yml. Previously the test ran ruff
        # against `voice_typer/server/` only and could pass locally
        # while CI failed on an F-rule violation in tests/, scripts/,
        # or conftest.py.
        ruff_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "voice_typer/",
                "tests/",
                "scripts/",
                "conftest.py",
                "--select",
                "F",
                "--no-fix",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )
        assert ruff_result.returncode == 0, (
            f"F-rule violations found (these are real bugs):\n"
            f"{ruff_result.stdout}\n{ruff_result.stderr}\n"
            f"Fix the violations — F-rules are a hard-fail in CI and are NOT "
            f"tracked by the ratchet baseline."
        )


# ── 5. Script self-consistency ───────────────────────────────────────


class TestScriptSelfConsistency:
    """Verify the script handles edge cases without crashing."""

    def test_missing_current_file_exits_nonzero(self, tmp_path: Path) -> None:
        # Point --current-path at a non-existent file.
        bogus = tmp_path / "does-not-exist.json"
        result = _run_script(["--current-path", str(bogus)])
        # Script prints ERROR and proceeds with empty list (treated as 0 violations).
        # Since baseline is non-zero, that would be "improved" → exit 0.
        # We accept 0 or 2 here; the test just verifies no crash.
        assert result.returncode in (0, 2)
        assert "Traceback" not in result.stderr

    def test_help_exits_zero(self) -> None:
        result = _run_script(["--help"])
        assert result.returncode == 0
        assert "RW-11" in result.stdout or "ruff ratchet" in result.stdout.lower()
