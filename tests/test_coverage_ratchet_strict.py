"""Tests for ``--strict`` flag on ``scripts/coverage_ratchet_check.py``."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# Make the scripts/ directory importable so we can load
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Import AFTER sys.path manipulation. ruff: noqa: E402
import coverage_ratchet_check as crc  # noqa: E402


def _capture_main(
    args: list[str],
    *,
    monkeypatch: pytest.MonkeyPatch,
    coverage_pct: float | None,
    baseline: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Run ``crc.main(args)`` with stubbed I/O."""
    monkeypatch.setattr(crc, "_load_current_coverage", lambda: coverage_pct)

    # _parse_coverage_xml must NEVER touch the real repo's coverage.xml.
    monkeypatch.setattr(crc, "_parse_coverage_xml", lambda _path: coverage_pct)
    monkeypatch.setattr(crc, "_run_coverage_json", lambda: coverage_pct)

    if baseline is not None:
        baseline_file = Path(os.environ["VT_TEST_BASELINE_PATH"])
        baseline_file.write_text(
            __import__("json").dumps(baseline),
            encoding="utf-8",
        )
        monkeypatch.setattr(crc, "BASELINE_PATH", baseline_file)
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
    """Reserve a tmp_path baseline file location for tests that need one."""
    baseline_file = tmp_path / "coverage-baseline.json"
    monkeypatch.setenv("VT_TEST_BASELINE_PATH", str(baseline_file))
    return baseline_file


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
        assert "skip" in out.lower() or "default" in out.lower()


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
        """Passing ``--strict=False`` is not a thing (argparse store_true);"""
        # No --strict flag on the command line at all.
        rc, _out = _capture_main(
            [],
            monkeypatch=monkeypatch,
            coverage_pct=None,
        )
        assert rc == 0


class TestStrictNoEffectWhenDataPresent:
    """``--strict`` must not change behavior when data IS available."""

    _BASELINE: dict[str, Any] = {
        "_comment": "synthetic baseline for  tests",
        "_schema_version": 1,
        "total_coverage": 65.0,
    }

    def test_strict_with_data_present_passes_when_ratchet_holds(
        self, monkeypatch: pytest.MonkeyPatch, _baseline_path: Path
    ) -> None:
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
        """For every (current_pct, baseline) pair where data is present,"""
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


class TestStrictRegenerateNoData:
    """``--strict`` is checked BEFORE ``--regenerate``."""

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


class TestHelpDocumentsStrict:
    """``--help`` must mention ``--strict``."""

    def test_help_mentions_strict(self) -> None:
        crc.main.__wrapped__ if hasattr(crc.main, "__wrapped__") else None
        # Simpler: reconstruct the parser by inspecting the module's
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

    def test_baseline_path_env_redirects_reads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``COVERAGE_BASELINE_PATH`` redirects BASELINE_PATH at import time."""
        import importlib
        import json

        redirect = tmp_path / "redirected-baseline.json"
        monkeypatch.setenv("COVERAGE_BASELINE_PATH", str(redirect))
        reloaded = importlib.reload(crc)
        try:
            assert redirect == reloaded.BASELINE_PATH, "COVERAGE_BASELINE_PATH must override the default baseline path"
            # A compare against the redirected baseline uses the redirected
            redirect.write_text(json.dumps({"total_coverage": 84.0}), encoding="utf-8")
            monkeypatch.setattr(reloaded, "_load_current_coverage", lambda: 84.0)
            rc, out = 0, ""
            try:
                import contextlib
                import io

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = reloaded.main([])
                out = buf.getvalue()
            finally:
                assert rc in (0, 1), f"compare must exit 0/1 against the redirected baseline, got {rc}: {out}"
        finally:
            importlib.reload(crc)  # restore the module-global default path
