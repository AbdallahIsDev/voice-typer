"""Fixture-hygiene drift guard for the tests/ tree.

Two classes of duplication crept into the suite historically:

1. Local config-factory functions copy-pasted across test files
   (``def makeConfig(...)``) instead of importing a shared helper from
   ``tests/fixtures/``. Copies drift independently — each divergence is
   a silent behavioral difference between suites that all claim to test
   the same config shape.

2. Local ``FakeConfig`` class definitions in the audio-filter-chain
   tests. The canonical minimal config stand-in now lives in
   :class:`tests.fixtures.config_helpers.FakeConfig`; the two audio
   files that previously carried private copies (which had already
   drifted by three fields) must keep importing the shared one.

This module is a pure source scan (no imports of the scanned files), so
it stays fast and cannot itself become part of the drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"

# Files whose config-class definitions were consolidated onto
# tests.fixtures.config_helpers.FakeConfig; they must never re-define a
# local copy.
CONSOLIDATED_CONFIG_CLASS_FILES = (
    TESTS_DIR / "test_audio_processor.py",
    TESTS_DIR / "test_audio_processor_set_sample_rate.py",
)

_LOCAL_CONFIG_CLASS_RE = re.compile(r"^\s*class\s+_(?:Fake)?Config\b", re.MULTILINE)
_LOCAL_MAKE_CONFIG_FACTORY_RE = re.compile(r"\bdef\s+makeConfig\s*\(")


def _iter_test_sources():
    """Yield ``(path, text)`` for every non-fixture test source file.

    This module itself is skipped — its docstring quotes the forbidden
    pattern and would otherwise self-match.
    """
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path == Path(__file__):
            continue
        if FIXTURES_DIR in path.parents:
            continue
        yield path, path.read_text(encoding="utf-8", errors="replace")


def test_no_local_make_config_factories() -> None:
    """No test file may define a local ``def makeConfig(`` factory.

    Shared factories belong in ``tests/fixtures/`` so every consumer
    sees identical defaults; local copies are exactly how the fixture
    drift this guard prevents starts.
    """
    offenders: list[str] = []
    for path, text in _iter_test_sources():
        match = _LOCAL_MAKE_CONFIG_FACTORY_RE.search(text)
        if match:
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(TESTS_DIR.parent)}:{line}")
    assert not offenders, (
        "Local config factories found outside tests/fixtures/ — "
        "import the shared helper instead of redefining it: "
        f"{offenders}"
    )


@pytest.mark.parametrize("path", CONSOLIDATED_CONFIG_CLASS_FILES, ids=lambda p: p.name)
def test_consolidated_files_import_shared_fake_config(path: Path) -> None:
    """The audio-filter test files must use the shared FakeConfig.

    They previously carried private copies that had already diverged;
    this pins the consolidation so the duplication cannot silently
    return.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    assert not _LOCAL_CONFIG_CLASS_RE.search(text), (
        f"{path.name} re-defines a local config class — import FakeConfig from tests.fixtures.config_helpers instead"
    )
    assert "from tests.fixtures.config_helpers import FakeConfig" in text, (
        f"{path.name} must import FakeConfig from tests.fixtures.config_helpers"
    )
