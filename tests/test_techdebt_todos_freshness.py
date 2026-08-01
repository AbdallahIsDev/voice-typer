"""Regression tests for XZ-CC-13 — Stale TODO migrate-tests cluster.

The three god-class decomposition packages (``prewarm``, ``recording``,
``server_platform``) each carry a TECH-DEBT TODO block in their
``__init__.py`` documenting the test-patch-compatibility boilerplate
(``_pkg.X`` indirection / ``_RecordingModule`` custom module class)
that exists pending migration of tests to patch submodules directly.

XZ-CC-13 flagged these TODOs as stale (no current date, no tracking
link).  This test file pins the post-fix contract:

1. Each TODO block must reference the migration tracking doc
   ``docs/rw9-god-class-decomposition.md``.
2. Each TODO must carry a date in ``YYYY-MM-DD`` form that is on or
   after ``2026-08-01`` (the date the staleness was addressed).
3. The TODOs must NOT carry a stripped session-prefix artifact
   (the double-space ``"  / TECH-DEBT"`` pattern that appeared when a
   ``CR-XX`` prefix was stripped per C-STYLE-1).
4. The TODOs must NOT contain literal ``CR-`` task-ID prefixes
   (C-STYLE-1: no task IDs / session prefixes in source code).

Only the two files owned by WAVE2-A10 are asserted here:
- ``voice_typer/server/prewarm/__init__.py``
- ``voice_typer/server/recording/__init__.py``

The sibling ``server_platform/__init__.py`` TODO is the responsibility
of a different agent's lane; it is intentionally NOT checked here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREWARM_INIT = REPO_ROOT / "voice_typer" / "server" / "prewarm" / "__init__.py"
RECORDING_INIT = REPO_ROOT / "voice_typer" / "server" / "recording" / "__init__.py"
TRACKING_DOC = "docs/rw9-god-class-decomposition.md"
# The session that addressed the staleness (worklog session start).
MIN_TODO_DATE = "2026-08-01"
# Matches "TODO (YYYY-MM-DD, TECH-DEBT" — the post-fix format.
# Accepts an optional ``/`` separator before TECH-DEBT but rejects
# the bare double-space artifact that signalled a stripped CR-XX prefix.
TODO_DATE_RE = re.compile(r"TODO\s*\(\s*(\d{4}-\d{2}-\d{2})\s*,\s*TECH-DEBT")
# The pre-fix artifact: a double space followed by ``/ TECH-DEBT`` —
# this was left behind when a ``CR-XX`` prefix was stripped.
STRIPPED_PREFIX_ARTIFACT_RE = re.compile(r"TODO\s*\(\s*\d{4}-\d{2}-\d{2}\s*,\s+/\s*TECH-DEBT")
# Reject literal ``CR-`` task-ID prefixes anywhere in the TODO block
# (C-STYLE-1).
SESSION_PREFIX_RE = re.compile(r"\bCR-[A-Z0-9-]+\b")


@pytest.fixture(scope="module")
def prewarm_source() -> str:
    return PREWARM_INIT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def recording_source() -> str:
    return RECORDING_INIT.read_text(encoding="utf-8")


def _todo_blocks(source: str) -> list[str]:
    """Return the contiguous line-runs that mention TECH-DEBT.

    A "TODO block" is the comment/docstring paragraph that opens with
    ``TODO (date, TECH-DEBT ...)`` and runs until the next blank line
    (for docstrings) or the next ``#``-comment gap (for comment blocks).
    """
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
                # line ends the block.  For docstrings this catches the
                # trailing closing paragraph break.
                blocks.append("\n".join(current))
                current = []
                in_block = False
            else:
                current.append(line)
    if in_block and current:
        blocks.append("\n".join(current))
    return blocks


class TestPrewarmInitTODO:
    """XZ-CC-13 — prewarm/__init__.py TODO block freshness."""

    def test_file_exists(self) -> None:
        assert PREWARM_INIT.is_file(), f"missing: {PREWARM_INIT}"

    def test_has_at_least_one_todo_block(self, prewarm_source: str) -> None:
        blocks = _todo_blocks(prewarm_source)
        assert blocks, "prewarm/__init__.py must have at least one TECH-DEBT TODO block"

    def test_todo_references_tracking_doc(self, prewarm_source: str) -> None:
        assert TRACKING_DOC in prewarm_source, f"prewarm/__init__.py TODO must reference {TRACKING_DOC}"

    def test_todo_has_fresh_date(self, prewarm_source: str) -> None:
        matches = TODO_DATE_RE.findall(prewarm_source)
        assert matches, "prewarm/__init__.py TODO must have a 'TODO (YYYY-MM-DD, TECH-DEBT' header"
        for date_str in matches:
            assert date_str >= MIN_TODO_DATE, f"prewarm/__init__.py TODO date {date_str} is older than {MIN_TODO_DATE}"

    def test_no_stripped_prefix_artifact(self, prewarm_source: str) -> None:
        assert not STRIPPED_PREFIX_ARTIFACT_RE.search(prewarm_source), (
            "prewarm/__init__.py TODO has the '  / TECH-DEBT' stripped-prefix artifact "
            "(leftover from a CR-XX session prefix); clean it to 'TECH-DEBT'"
        )

    def test_no_session_prefix_in_source(self, prewarm_source: str) -> None:
        assert not SESSION_PREFIX_RE.search(prewarm_source), (
            "prewarm/__init__.py contains a 'CR-XX' session prefix; violates C-STYLE-1 (no task IDs in source code)"
        )


class TestRecordingInitTODO:
    """XZ-CC-13 — recording/__init__.py TODO block freshness.

    The recording package has TWO copies of the TODO block — one in
    the module docstring (top of file) and one in the inline comment
    block above ``_RecordingModule``.  Both must satisfy the freshness
    contract.
    """

    def test_file_exists(self) -> None:
        assert RECORDING_INIT.is_file(), f"missing: {RECORDING_INIT}"

    def test_has_at_least_two_todo_blocks(self, recording_source: str) -> None:
        blocks = _todo_blocks(recording_source)
        assert len(blocks) >= 2, (
            "recording/__init__.py must have at least two TECH-DEBT TODO blocks "
            "(one in the module docstring, one above _RecordingModule)"
        )

    def test_todo_references_tracking_doc(self, recording_source: str) -> None:
        # Both TODO blocks should mention the tracking doc.
        count = recording_source.count(TRACKING_DOC)
        assert count >= 2, (
            f"recording/__init__.py must reference {TRACKING_DOC} at least twice (once per TODO block); found {count}"
        )

    def test_todo_has_fresh_date(self, recording_source: str) -> None:
        matches = TODO_DATE_RE.findall(recording_source)
        assert len(matches) >= 2, "recording/__init__.py must have two 'TODO (YYYY-MM-DD, TECH-DEBT' headers"
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
    """Sanity check — the referenced tracking doc must actually exist."""

    def test_tracking_doc_exists(self) -> None:
        doc_path = REPO_ROOT / TRACKING_DOC
        assert doc_path.is_file(), f"tracking doc referenced by the TODOs does not exist: {doc_path}"
