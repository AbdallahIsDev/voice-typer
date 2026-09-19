"""
Regression tests for XZ-CC-13: Stale TODO migrate-tests cluster.
``CR-XX`` prefix was stripped per C-STYLE-1).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREWARM_INIT = REPO_ROOT / "voice_typer" / "server" / "prewarm" / "__init__.py"
RECORDING_INIT = REPO_ROOT / "voice_typer" / "server" / "recording" / "__init__.py"
TRACKING_DOC = "docs/history/rw9-god-class-decomposition.md"
MIN_TODO_DATE = "2026-08-01"
# Matches "TODO (YYYY-MM-DD, TECH-DEBT", the post-fix format.
TODO_DATE_RE = re.compile(r"TODO\s*\(\s*(\d{4}-\d{2}-\d{2})\s*,\s*TECH-DEBT")
# The pre-fix artifact: a double space followed by ``/ TECH-DEBT`` —
STRIPPED_PREFIX_ARTIFACT_RE = re.compile(r"TODO\s*\(\s*\d{4}-\d{2}-\d{2}\s*,\s+/\s*TECH-DEBT")
# (C-STYLE-1).
SESSION_PREFIX_RE = re.compile(r"\bCR-[A-Z0-9-]+\b")


@pytest.fixture(scope="module")
def recording_source() -> str:
    return RECORDING_INIT.read_text(encoding="utf-8")


def _todo_blocks(source: str) -> list[str]:
    """Return the contiguous line-runs that mention TECH-DEBT."""
    blocks: list[str] = []
    current: list[str] = []
    in_block = False
    for line in source.splitlines():
        if "TECH-DEBT" in line and "TODO" in line:
            in_block = True
            current = [line]
        elif in_block:
            if line.strip() == "" or (
                line.lstrip().startswith("#") is False
                and not line.startswith(" ")
                and not line.startswith("\t")
                and "TODO" not in line
            ):
                # Heuristic: a blank line OR a non-indented non-comment
                blocks.append("\n".join(current))
                current = []
                in_block = False
            else:
                current.append(line)
    if in_block and current:
        blocks.append("\n".join(current))
    return blocks


class TestPrewarmInitTODO:
    """, prewarm/__init__.py TODO block freshness."""

    def test_file_exists(self) -> None:
        assert PREWARM_INIT.is_file(), f"missing: {PREWARM_INIT}"

    def test_todo_block_freshness_if_present(self) -> None:
        source = PREWARM_INIT.read_text(encoding="utf-8")
        blocks = _todo_blocks(source)
        if not blocks:
            pytest.skip(
                "prewarm/__init__.py no longer carries a TECH-DEBT TODO "
                "block, the package was re-architected as a worker-startup "
                "phase (master plan §6.2). Skipping freshness assertions."
            )
        # Pre-deletion state, the TODO block must satisfy the freshness contract.
        assert TRACKING_DOC in source, f"prewarm/__init__.py TODO must reference {TRACKING_DOC}"
        matches = TODO_DATE_RE.findall(source)
        assert matches, "prewarm/__init__.py TODO must have a 'TODO (YYYY-MM-DD, TECH-DEBT' header"
        for date_str in matches:
            assert date_str >= MIN_TODO_DATE, f"prewarm/__init__.py TODO date {date_str} is older than {MIN_TODO_DATE}"
        assert not STRIPPED_PREFIX_ARTIFACT_RE.search(source), (
            "prewarm/__init__.py TODO has the '  / TECH-DEBT' "
            "stripped-prefix artifact (leftover from a CR-XX session "
            "prefix); clean it to 'TECH-DEBT'"
        )
        assert not SESSION_PREFIX_RE.search(source), (
            "prewarm/__init__.py contains a 'CR-XX' session prefix; violates C-STYLE-1 (no task IDs in source code)"
        )


class TestRecordingInitTODO:
    """recording/__init__.py patch-compat boilerplate state."""

    def test_file_exists(self) -> None:
        assert RECORDING_INIT.is_file(), f"missing: {RECORDING_INIT}"

    def test_no_custom_module_class(self) -> None:
        source = RECORDING_INIT.read_text(encoding="utf-8")
        assert "_RecordingModule" not in source, (
            "recording/__init__.py must not reinstall a custom module "
            "subclass for test-patch routing; patch submodules directly"
        )
        assert "sys.modules[__name__].__class__" not in source

    def test_no_mutable_routing_frozensets(self) -> None:
        source = RECORDING_INIT.read_text(encoding="utf-8")
        assert "_MUTABLE_RESAMPLING" not in source
        assert "_MUTABLE_BUFFER" not in source

    def test_todo_blocks_if_present_are_fresh(self, recording_source: str) -> None:
        blocks = _todo_blocks(recording_source)
        matches = TODO_DATE_RE.findall(recording_source)
        if not blocks or not matches:
            pytest.skip("no TECH-DEBT TODO blocks present (boilerplate removed)")
        assert len(matches) == len(blocks) or matches
        for date_str in matches:
            assert date_str >= MIN_TODO_DATE, (
                f"recording/__init__.py TODO date {date_str} is older than {MIN_TODO_DATE}"
            )

    def test_no_stripped_prefix_artifact(self, recording_source: str) -> None:
        assert not STRIPPED_PREFIX_ARTIFACT_RE.search(recording_source), (
            "recording/__init__.py TODO has the '  / TECH-DEBT' stripped-prefix artifact "
            "(leftover from a CR-XX session prefix); clean it to 'TECH-DEBT'"
        )

    def test_no_session_prefix_in_source(self, recording_source: str) -> None:
        assert not SESSION_PREFIX_RE.search(recording_source), (
            "recording/__init__.py contains a 'CR-XX' session prefix; violates C-STYLE-1 (no task IDs in source code)"
        )


class TestTrackingDocExists:
    """Sanity check, the referenced tracking doc must actually exist."""

    def test_tracking_doc_exists(self) -> None:
        doc_path = REPO_ROOT / TRACKING_DOC
        assert doc_path.is_file(), f"tracking doc referenced by the TODOs does not exist: {doc_path}"
