"""Tests for the mypy ratchet mechanism."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = PROJECT_ROOT / "mypy-baseline.json"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "mypy_ratchet_check.py"
MYPY_TARGET = "voice_typer/server/"

# Required schema fields on the baseline file.
REQUIRED_FIELDS = ("total_count", "by_code")


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
    """Path the script will actually read/write (honors MYPY_BASELINE_PATH)."""
    override = os.environ.get("MYPY_BASELINE_PATH")
    return Path(override) if override else BASELINE_PATH


def _mypy_lines(codes: list[str]) -> str:
    """Render synthetic mypy error lines, one per entry in ``codes``."""
    lines = [f"voice_typer/server/probe.py:{i + 1}: error: synthetic error [{code}]" for i, code in enumerate(codes)]
    return "\n".join(lines) + ("\n" if lines else "")


def _has_mypy() -> bool:
    """True if `mypy` is importable as a module (mirrors CI's `python -m mypy`)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class TestBaselineSchema:
    """Verify mypy-baseline.json exists, is valid JSON, and matches the schema."""

    def test_baseline_file_exists(self) -> None:
        assert BASELINE_PATH.is_file(), (
            "mypy-baseline.json must exist at the repo root. Create it with: "
            "python -m mypy voice_typer/server/ | "
            "python scripts/mypy_ratchet_check.py --regenerate --stdin --force"
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
        assert isinstance(tc, int) and not isinstance(tc, bool), "total_count must be int, not bool"

    def test_by_code_is_object(self) -> None:
        baseline = _load_baseline()
        bc = baseline["by_code"]
        assert isinstance(bc, dict), f"by_code must be a JSON object, got {type(bc).__name__}"

    def test_by_code_keys_are_strings(self) -> None:
        baseline = _load_baseline()
        for key in baseline["by_code"]:
            assert isinstance(key, str), f"by_code key must be str, got {type(key).__name__}: {key!r}"

    def test_by_code_values_are_non_negative_ints(self) -> None:
        baseline = _load_baseline()
        for code, count in baseline["by_code"].items():
            assert isinstance(count, int), f"by_code[{code!r}] must be int, got {type(count).__name__}"
            assert not isinstance(count, bool), f"by_code[{code!r}] must be int, not bool"
            assert count >= 0, f"by_code[{code!r}] must be >= 0, got {count}"

    def test_total_count_equals_sum_of_by_code(self) -> None:
        baseline = _load_baseline()
        tc = baseline["total_count"]
        bc = baseline["by_code"]
        expected = sum(bc.values())
        assert tc == expected, (
            f"total_count ({tc}) must equal sum(by_code.values()) ({expected}). "
            "If you fixed errors, regenerate the baseline with: "
            "python scripts/mypy_ratchet_check.py --regenerate"
        )

    def test_metadata_fields_are_optional_and_ignored(self) -> None:
        """Underscore-prefixed metadata fields are allowed but not required."""
        baseline = _load_baseline()
        # No assertion on presence, just verify no non-underscore non-required fields exist.
        allowed = set(REQUIRED_FIELDS)
        for key in baseline:
            if key in allowed:
                continue
            assert key.startswith("_"), (
                f"Unexpected non-underscore field in baseline: {key!r}. "
                "Only 'total_count', 'by_code', and underscore-prefixed metadata are allowed."
            )


def _pick_representative_code(baseline: dict) -> tuple[str, int]:
    """Pick a mypy error code with count > 1 from the baseline for use in compare tests."""
    by_code = baseline.get("by_code", {})
    for code, count in sorted(by_code.items()):
        if count > 1:
            return code, count
    for code, count in sorted(by_code.items()):
        if count > 0:
            return code, count
    return "attr-defined", 3


class TestCompareLogic:
    """Verify scripts/mypy_ratchet_check.py compare behavior with synthetic inputs."""

    @pytest.fixture(autouse=True)
    def _synthetic_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Seed a known baseline into tmp_path; never touch the real repo file."""
        baseline_file = tmp_path / "mypy-baseline.json"
        baseline_file.write_text(
            json.dumps(
                {
                    "_comment": "synthetic baseline for TestCompareLogic, tmp_path",
                    "_target": "voice_typer/server/",
                    "_schema_version": 1,
                    "total_count": 4,
                    "by_code": {"attr-defined": 3, "name-defined": 1},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("MYPY_BASELINE_PATH", str(baseline_file))
        yield

    def test_equal_counts_passes(self) -> None:
        # Feed the exact union of the synthetic baseline's by_code counts
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        by_code = _baseline["by_code"]
        stdin = _mypy_lines([code for code, count in by_code.items() for _ in range(count)])
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 0, (
            f"Expected exit 0 (counts equal baseline), got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "PASS" in result.stdout
        # Total is exactly the baseline total (4), so no "improved" hint.
        assert "improved" not in result.stdout.lower()

    def test_total_grew_fails(self) -> None:
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _code, _count = _pick_representative_code(_baseline)
        _total = _baseline["total_count"]
        stdin = _mypy_lines([_code] * (_total + 1))
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (total grew), got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "FAIL" in result.stdout
        assert "total error count grew" in result.stdout

    def test_new_code_fails(self) -> None:
        stdin = _mypy_lines(["attr-defined"] * 3 + ["name-defined", "assignment"])
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (new code grew total), got {result.returncode}.\nstdout:\n{result.stdout}"
        )
        assert "FAIL" in result.stdout

    def test_per_code_regression_with_same_total_fails(self) -> None:
        # Construct a per-code regression with SAME total: attr-defined
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _attr = _baseline["by_code"].get("attr-defined", 3)
        _name = _baseline["by_code"].get("name-defined", 1)
        stdin = _mypy_lines(["attr-defined"] * (_attr + 1) + ["name-defined"] * max(0, _name - 1))
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (per-code regression), got {result.returncode}.\nstdout:\n{result.stdout}"
        )
        assert "per-code regression" in result.stdout
        assert "attr-defined" in result.stdout

    def test_total_shrunk_passes_with_improved_hint(self) -> None:
        # Use the representative code - 1 to show shrinkage.
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _code, _count = _pick_representative_code(_baseline)
        stdin = _mypy_lines([_code] * max(0, _count - 1))
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 0
        assert "improved" in result.stdout.lower()
        # Should suggest regenerating to lock in the gain.
        assert "regenerate" in result.stdout.lower()

    def test_zero_violations_passes(self) -> None:
        stdin = _mypy_lines([])
        result = _run_script(["--stdin"], stdin=stdin)
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_empty_stdin_treated_as_zero_violations(self) -> None:
        result = _run_script(["--stdin"], stdin="")
        assert result.returncode == 0

    def test_invalid_input_does_not_crash(self) -> None:
        # Garbage that matches no error-line pattern → 0 errors parsed →
        result = _run_script(["--stdin"], stdin="not mypy output at all\n---\n")
        assert result.returncode in (0, 1, 2)
        # No unhandled exception (no traceback).
        assert "Traceback" not in result.stderr


class TestRegenerateLogic:
    """Verify the --regenerate subcommand behaves correctly."""

    @pytest.fixture(autouse=True)
    def _synthetic_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        baseline_file = tmp_path / "mypy-baseline.json"
        baseline_file.write_text(
            json.dumps(
                {
                    "_comment": "synthetic baseline for TestRegenerateLogic, tmp_path",
                    "_target": "voice_typer/server/",
                    "_schema_version": 1,
                    "total_count": 3,
                    "by_code": {"name-defined": 3},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("MYPY_BASELINE_PATH", str(baseline_file))
        yield

    def test_regenerate_refuses_to_grow(self) -> None:
        # More errors than current baseline total, should refuse.
        _baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        _old_total = _baseline["total_count"]
        stdin = _mypy_lines(["name-defined"] * (_old_total + 5))
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 1, (
            f"Expected exit 1 (refuse to grow), got {result.returncode}.\nstdout:\n{result.stdout}"
        )
        assert "REFUSED" in result.stdout
        # Baseline file should be unchanged.
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == _old_total

    def test_regenerate_same_count_succeeds(self) -> None:
        # Same count as input, should succeed (idempotent).
        stdin = _mypy_lines(["name-defined"] * 3)
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == 3
        assert baseline["by_code"] == {"name-defined": 3}

    def test_regenerate_smaller_count_succeeds(self) -> None:
        # 2 errors (down from 3), should succeed.
        stdin = _mypy_lines(["name-defined"] * 2)
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == 2
        assert baseline["by_code"] == {"name-defined": 2}

    def test_regenerate_to_zero_succeeds(self) -> None:
        stdin = _mypy_lines([])
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert baseline["total_count"] == 0
        assert baseline["by_code"] == {}

    def test_regenerate_preserves_metadata_fields(self) -> None:
        # Verify _target, _schema_version are preserved.
        stdin = _mypy_lines(["name-defined"] * 2)
        result = _run_script(["--regenerate", "--stdin"], stdin=stdin)
        assert result.returncode == 0
        baseline = json.loads(_baseline_path().read_text(encoding="utf-8"))
        assert "_schema_version" in baseline
        assert "_target" in baseline


@pytest.mark.skipif(not _has_mypy(), reason="mypy not installed in this environment")
@pytest.mark.timeout(600)  # overrides CI's --timeout=120: mypy on the full server scope is slow
class TestRatchetHolds:
    """Verify the ratchet is not currently regressed by running the actual comparison."""

    def test_current_mypy_count_is_at_or_below_baseline(self) -> None:
        """Run `python -m mypy voice_typer/server/` and compare to the baseline."""
        mypy_result = subprocess.run(
            [sys.executable, "-m", "mypy", MYPY_TARGET],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=540,
        )
        assert mypy_result.returncode in (0, 1), (
            f"mypy exited with unexpected code {mypy_result.returncode}.\n"
            f"stdout:\n{mypy_result.stdout}\nstderr:\n{mypy_result.stderr}"
        )

        # Now run the ratchet script with the combined output as stdin.
        combined = (mypy_result.stdout or "") + (mypy_result.stderr or "")
        result = _run_script(["--stdin"], stdin=combined)
        assert result.returncode == 0, (
            f"Ratchet regression: current mypy errors exceed the baseline.\n"
            f"ratchet stdout:\n{result.stdout}\n"
            f"Either fix the new errors OR (if intentional) update the baseline:\n"
            f"  python -m mypy voice_typer/server/ | "
            f"python scripts/mypy_ratchet_check.py --regenerate --stdin --force"
        )


class TestScriptSelfConsistency:
    """Verify the script handles edge cases without crashing."""

    def test_help_exits_zero(self) -> None:
        result = _run_script(["--help"])
        assert result.returncode == 0
        assert "mypy ratchet" in result.stdout.lower()
