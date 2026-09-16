"""Coverage-floor drift guards (MO-89).

The coverage floor is declared in three places that MUST agree:

1. ``[tool.coverage.report].fail_under`` in ``pyproject.toml`` (what a
   local ``coverage report`` enforces),
2. the explicit ``--cov-fail-under`` in ``.github/workflows/build.yml``
   (the CI gate, applied on every matrix leg),
3. the ``make test-cov`` recipe.

Before MO-89 those sites drifted from reality: the floor sat at 65 % while
a real full-suite run measured 84.84 %, so the gate had ~19 points of
slack. This module pins the three declarations to ONE value and pins the
two properties that keep the number honest:

* No production module may be excluded from measurement —
  ``[tool.coverage.run].omit`` is for ``tests/*`` only. Excluding a
  module raises the percentage without measuring more code.
* The ratchet floor (``coverage-baseline.json``, enforced by
  ``scripts/coverage_ratchet_check.py`` on the ubuntu × 3.12 leg) must sit
  at or ABOVE ``fail_under``, so the two gates can never contradict each
  other (a run the ratchet rejects must not be waved through by the
  ``fail_under`` floor).

The floor may only RISE: each raise must come from a measured total via
the ratchet (``python scripts/coverage_ratchet_check.py --regenerate``),
never from a blind bump and never downward to accommodate a drop.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover - Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]
    except ImportError:  # pragma: no cover - tomli not in the lock
        pytest.skip(
            "tomli backport not installed on Python 3.10, skipping coverage-floor drift check",
            allow_module_level=True,
        )

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "build.yml"
MAKEFILE_PATH = PROJECT_ROOT / "Makefile"
BASELINE_PATH = PROJECT_ROOT / "coverage-baseline.json"

# The lowest floor this repo will accept. Every raise is recorded here so a
# silent downgrade fails loudly; a genuine, measured increase should bump
# this constant AND the three declarations in the same change.
MIN_FLOOR = 78

_COV_FAIL_UNDER_RE = re.compile(r"--cov-fail-under=(\d+)")


def _declared_flags(text: str) -> set[int]:
    """Every ``--cov-fail-under=<n>`` value declared in *text*."""
    return {int(match) for match in _COV_FAIL_UNDER_RE.findall(text)}


def _coverage_config() -> dict:
    with PYPROJECT_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["coverage"]


def _fail_under() -> int:
    return int(_coverage_config()["report"]["fail_under"])


class TestCoverageFloorIsSingleSourced:
    """``fail_under``, the CI flag and the Makefile flag must agree."""

    def test_ci_workflow_declares_the_same_floor(self) -> None:
        fail_under = _fail_under()
        flags = _declared_flags(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
        assert flags == {fail_under}, (
            f".github/workflows/build.yml declares --cov-fail-under={sorted(flags)}, "
            f"but pyproject.toml sets fail_under = {fail_under}. "
            "The CI gate and the local gate must be the same number."
        )

    def test_makefile_declares_the_same_floor(self) -> None:
        fail_under = _fail_under()
        flags = _declared_flags(MAKEFILE_PATH.read_text(encoding="utf-8"))
        assert flags == {fail_under}, (
            f"Makefile declares --cov-fail-under={sorted(flags)}, but pyproject.toml "
            f"sets fail_under = {fail_under}. `make test-cov` must mirror CI."
        )

    def test_floor_was_not_lowered(self) -> None:
        fail_under = _fail_under()
        assert fail_under >= MIN_FLOOR, (
            f"coverage floor dropped to {fail_under} (minimum accepted: {MIN_FLOOR}). "
            "The floor only rises, and only from a measured total via "
            "`python scripts/coverage_ratchet_check.py --regenerate`."
        )


class TestCoverageScopeIsNotNarrowed:
    """Production code must not be excluded from measurement."""

    def test_source_covers_the_whole_package(self) -> None:
        source = _coverage_config()["run"]["source"]
        assert source == ["voice_typer"], (
            f"[tool.coverage.run].source must be exactly ['voice_typer'] so every "
            f"production module is measured; got {source!r}."
        )

    def test_omit_list_excludes_no_production_code(self) -> None:
        omit = _coverage_config()["run"]["omit"]
        offenders = [entry for entry in omit if not entry.startswith("tests/")]
        assert not offenders, (
            f"[tool.coverage.run].omit contains non-test entries {offenders}. Excluding "
            "production modules raises the percentage without measuring more code; add "
            "tests (or a `# pragma: no cover` on the genuinely unreachable branch) "
            "instead of omitting the module."
        )


class TestRatchetFloorAndFailUnderAgree:
    """The ratchet floor must never sit below the ``fail_under`` floor."""

    def test_ratchet_floor_is_at_or_above_fail_under(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        total = float(baseline["total_coverage"])
        fail_under = _fail_under()
        assert total >= fail_under, (
            f"coverage-baseline.json floor ({total}) is BELOW the fail_under floor "
            f"({fail_under}); the ratchet would reject runs that `fail_under` accepts, "
            "which makes the two gates contradict each other."
        )

    def test_ratchet_floor_is_not_trivially_low(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        total = float(baseline["total_coverage"])
        assert total >= MIN_FLOOR, (
            f"coverage-baseline.json floor ({total}) is below the accepted minimum "
            f"({MIN_FLOOR}); the ratchet must only rise, from a measured total."
        )
