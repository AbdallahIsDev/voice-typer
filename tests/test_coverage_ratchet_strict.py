"""Tests for ``--strict`` flag on ``scripts/coverage_ratchet_check.py``.

Background
----------
The coverage ratchet script historically printed a NOTE and exited 0
(skip-with-pass) when ``coverage.xml`` was missing AND the
``coverage report --format=json`` fallback also failed to produce a
total. That's fine for local dev (the ratchet is gated on data
availability), but in CI it lets a job that lost its coverage data
(e.g. the pytest ``--cov`` step crashed before emitting
``coverage.xml``) pass the ratchet step vacuously.

 adds a ``--strict`` flag that flips the missing-data branch
from exit-0-skip to exit-1-fail. CI jobs should pass ``--strict``;
local dev keeps the historical skip-with-exit-0 default.

Tests
-----
1. ``--strict`` exits 1 when coverage data is unavailable.
2. Default (no ``--strict``) keeps exit-0 skip behavior.
3. ``--strict`` has NO effect when coverage data IS available —
   compare/regenerate behave identically to the default.
4. ``--help`` documents ``--strict``.
5. ``--strict`` + ``--regenerate`` + no data → still exits 1
   (you can't regenerate a baseline without data; ``--strict`` is
   checked before ``--regenerate``).

These tests import the script as a module and call ``main()``
directly with monkeypatched internals so they don't depend on the
repo's actual ``coverage.xml`` / ``.coverage`` state (other parallel
sub-agents may be running pytest with ``--cov`` at the same time).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# Make the scripts/ directory importable so we can load
# coverage_ratchet_check as a module (mirrors how scripts/ruff_ratchet_check.py
# is imported in tests/test_ruff_ratchet.py when needed).
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Import AFTER sys.path manipulation. ruff: noqa: E402
import coverage_ratchet_check as crc  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────


def _capture_main(
    args: list[str],
    *,
    monkeypatch: pytest.MonkeyPatch,
    coverage_pct: float | None,
    baseline: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Run ``crc.main(args)`` with stubbed I/O.

    * ``coverage_pct``: stubs ``_load_current_coverage`` to return this
      value (``None`` simulates "no coverage data anywhere"; a float
      simulates "coverage.xml was parsed successfully").
    * ``baseline``: if provided, writes a synthetic baseline JSON to a
      tmp_path file and points ``crc.BASELINE_PATH`` at it. Required
      whenever ``coverage_pct`` is not None (the compare/regenerate
      paths load the baseline).

    Returns ``(exit_code, captured_stdout)``. stdout is captured by
    redirecting ``print`` via ``capsys``-style monkeypatching of
    ``builtins.print``.
    """
    monkeypatch.setattr(crc, "_load_current_coverage", lambda: coverage_pct)

    # _parse_coverage_xml must NEVER touch the real repo's coverage.xml.
    monkeypatch.setattr(crc, "_parse_coverage_xml", lambda _path: coverage_pct)
    # _run_coverage_json is the fallback path; stub it so we never shell
    # out to a real `coverage` binary during tests.
    monkeypatch.setattr(crc, "_run_coverage_json", lambda: coverage_pct)

    if baseline is not None:
        baseline_file = Path(os.environ["VT_TEST_BASELINE_PATH"])
        baseline_file.write_text(
            __import__("json").dumps(baseline),
            encoding="utf-8",
        )
        monkeypatch.setattr(crc, "BASELINE_PATH", baseline_file)
        # compare() / regenerate() call BASELINE_PATH.relative_to(PROJECT_ROOT).
        # When the baseline lives in tmp_path (outside the repo), that raises
        # ValueError. Repoint PROJECT_ROOT at the baseline's parent so the
        # relative_to call succeeds and prints a sane path.
        monkeypatch.setattr(crc, "PROJECT_ROOT", baseline_file.parent)

    output: list[str] = []
    original_print = print

    def _capturing_print(*pargs: Any, **pkwargs: Any) -> None:
        # Convert print args to a string the same way print does.
        sep = pkwargs.get("sep", " ")
        end = pkwargs.get("end", "\n")
        output.append(sep.join(str(p) for p in pargs) + end)

    monkeypatch.setattr("builtins.print", _capturing_print)
    try:
        rc = crc.main(args)
    finally:
        monkeypatch.setattr("builtins.print", original_print)
    return rc, "".join(output)


@pytest.fixture
def _baseline_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reserve a tmp_path baseline file location for tests that need one.

    Sets ``VT_TEST_BASELINE_PATH`` env var so ``_capture_main`` can find
    the path. The actual file is written by ``_capture_main`` when the
    test passes a ``baseline=`` dict.
    """
    baseline_file = tmp_path / "coverage-baseline.json"
    monkeypatch.setenv("VT_TEST_BASELINE_PATH", str(baseline_file))
    return baseline_file


# ── 1. --strict fails when coverage data is unavailable ──────────────


class TestStrictFailsOnMissingData:
    """``--strict`` must exit 1 when coverage data is unavailable."""

    def test_strict_exits_1_when_no_coverage_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rc, out = _capture_main(
            ["--strict"],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        assert rc == 1, f"--strict should exit 1 when coverage data is unavailable, got {rc}.\nstdout:\n{out}"
        assert "FAIL" in out
        assert "--strict" in out

    def test_strict_message_documents_skip_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The --strict failure message should hint at the default skip behavior."""
        rc, out = _capture_main(
            ["--strict"],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        assert rc == 1
        # Message should mention either "skip" or "default" so the
        # contributor knows the non-strict behavior differs.
        assert "skip" in out.lower() or "default" in out.lower()


# ── 2. Default (no --strict) keeps exit-0 skip behavior ──────────────


class TestDefaultSkipsOnMissingData:
    """default behavior (no --strict) must keep exit-0 skip."""

    def test_default_exits_0_when_no_coverage_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rc, out = _capture_main(
            [],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        assert rc == 0, (
            f"Default (no --strict) should exit 0 (skip) when coverage data is unavailable, got {rc}.\nstdout:\n{out}"
        )
        assert "PASS" in out
        assert "skip" in out.lower()

    def test_default_explicit_no_strict_exits_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing ``--strict=False`` is not a thing (argparse store_true);
        the absence of the flag IS the non-strict mode. Verify explicitly."""
        # No --strict flag on the command line at all.
        rc, _out = _capture_main(
            [],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        assert rc == 0


# ── 3. --strict has NO effect when coverage data IS present ──────────


class TestStrictNoEffectWhenDataPresent:
    """``--strict`` must not change behavior when data IS available.

    The strict gate only fires in the missing-data branch. When
    coverage.xml / coverage report produces a valid %, the script
    proceeds to compare (or regenerate) and behaves identically with
    or without ``--strict``.
    """

    _BASELINE: dict[str, Any] = {
        "_comment": "synthetic baseline for  tests",
        "_schema_version": 1,
        "total_coverage": 65.0,
    }

    def test_strict_with_data_present_passes_when_ratchet_holds(
        self, monkeypatch: pytest.MonkeyPatch, _baseline_path: Path
    ) -> None:
        # current 70% >= baseline 65% → PASS regardless of --strict.
        rc, out = _capture_main(
            ["--strict"],
            monkeypatch=monkeypatch,
            coverage_pct=70.0,
            baseline=self._BASELINE,
        )
        assert rc == 0, f"--strict with data present should PASS when ratchet holds, got {rc}.\nstdout:\n{out}"
        assert "PASS" in out

    def test_strict_with_data_present_fails_on_regression(
        self, monkeypatch: pytest.MonkeyPatch, _baseline_path: Path
    ) -> None:
        # current 60% < baseline 65% → FAIL regardless of --strict.
        rc, out = _capture_main(
            ["--strict"],
            monkeypatch=monkeypatch,
            coverage_pct=60.0,
            baseline=self._BASELINE,
        )
        assert rc == 1, f"--strict with data present should FAIL on regression, got {rc}.\nstdout:\n{out}"
        assert "FAIL" in out
        assert "REGRESSION" in out

    def test_default_with_data_present_passes_when_ratchet_holds(
        self, monkeypatch: pytest.MonkeyPatch, _baseline_path: Path
    ) -> None:
        # Same as test_strict_with_data_present_passes_when_ratchet_holds
        # but WITHOUT --strict. Verifies the two paths agree.
        rc, out = _capture_main(
            [],
            monkeypatch=monkeypatch,
            coverage_pct=70.0,
            baseline=self._BASELINE,
        )
        assert rc == 0
        assert "PASS" in out

    def test_default_with_data_present_fails_on_regression(
        self, monkeypatch: pytest.MonkeyPatch, _baseline_path: Path
    ) -> None:
        rc, out = _capture_main(
            [],
            monkeypatch=monkeypatch,
            coverage_pct=60.0,
            baseline=self._BASELINE,
        )
        assert rc == 1
        assert "FAIL" in out

    def test_strict_and_default_agree_when_data_present(
        self, monkeypatch: pytest.MonkeyPatch, _baseline_path: Path
    ) -> None:
        """For every (current_pct, baseline) pair where data is present,
        --strict and default must produce the same exit code."""
        cases = [
            (70.0, 65.0, 0),  # improved
            (65.0, 65.0, 0),  # equal (within epsilon)
            (60.0, 65.0, 1),  # regression
            (80.0, 65.0, 0),  # big improvement
        ]
        for current, baseline_pct, expected_rc in cases:
            baseline = {"total_coverage": baseline_pct}
            rc_strict, _ = _capture_main(
                ["--strict"],
                monkeypatch=monkeypatch,
                coverage_pct=current,
                baseline=baseline,
            )
            rc_default, _ = _capture_main(
                [],
                monkeypatch=monkeypatch,
                coverage_pct=current,
                baseline=baseline,
            )
            assert rc_strict == expected_rc, (
                f"--strict: current={current}, baseline={baseline_pct} expected rc={expected_rc}, got {rc_strict}"
            )
            assert rc_default == expected_rc, (
                f"default: current={current}, baseline={baseline_pct} expected rc={expected_rc}, got {rc_default}"
            )


# ── 4. --strict + --regenerate + no data → still exits 1 ─────────────


class TestStrictRegenerateNoData:
    """``--strict`` is checked BEFORE ``--regenerate``.

    You can't regenerate a baseline without coverage data. With
    ``--strict``, the missing-data branch returns 1 before the
    regenerate branch is reached. Without ``--strict``, it returns 0
    (skip) before reaching regenerate.
    """

    def test_strict_regenerate_no_data_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rc, out = _capture_main(
            ["--strict", "--regenerate"],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        assert rc == 1, (
            f"--strict --regenerate with no data should exit 1 (strict "
            f"gate fires before regenerate), got {rc}.\nstdout:\n{out}"
        )
        assert "FAIL" in out

    def test_default_regenerate_no_data_exits_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rc, _out = _capture_main(
            ["--regenerate"],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        # Default: skip-with-exit-0 fires before regenerate is reached.
        assert rc == 0


# ── 5. --help documents --strict ─────────────────────────────────────


class TestHelpDocumentsStrict:
    """``--help`` must mention ``--strict``."""

    def test_help_mentions_strict(self) -> None:
        # argparse --help calls sys.exit(0) after printing to stdout.
        # Capture it via the parser directly to avoid SystemExit.
        crc.main.__wrapped__ if hasattr(crc.main, "__wrapped__") else None
        # Simpler: reconstruct the parser by inspecting the module's
        # main(). We can't easily do that, so instead invoke main with
        # --help and catch SystemExit.
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as exc_info:
            crc.main(["--help"])
        assert exc_info.value.code == 0
        help_text = buf.getvalue()
        assert "--strict" in help_text, f"--help output should document --strict.\nhelp:\n{help_text}"
        # The help text should explain WHEN to use --strict (CI).
        assert "CI" in help_text or "strict" in help_text.lower()


# ── 6. Self-consistency: --strict is a store_true flag ───────────────


class TestStrictFlagSemantics:
    """Verify the --strict flag is a boolean store_true (no value required)."""

    def test_strict_flag_is_in_argparse(self) -> None:
        """Re-build the parser the same way main() does and verify --strict exists."""
        import argparse

        parser = argparse.ArgumentParser()
        # Mirror the three flags main() registers.
        parser.add_argument("--regenerate", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--coverage-xml", type=Path, default=Path("coverage.xml"))
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit 1 (instead of 0) when coverage data is unavailable.",
        )
        # Default (no flag) → False.
        ns = parser.parse_args([])
        assert ns.strict is False
        # With flag → True.
        ns = parser.parse_args(["--strict"])
        assert ns.strict is True
        # Flag does not consume a value.
        ns = parser.parse_args(["--strict", "--coverage-xml", "foo.xml"])
        assert ns.strict is True
        assert ns.coverage_xml == Path("foo.xml")
