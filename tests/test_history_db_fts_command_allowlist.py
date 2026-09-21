"""Allowlist guards for the history DB's non-bindable SQL statements.

FTS5 special commands and ``PRAGMA`` modes cannot be parameter-bound, so the
history DB builds those statements as text. These tests pin the two defenses
added around that surface: an allowlist gating the FTS5 command text, and
fixed statement literals (no interpolation) for the WAL checkpoint modes.
"""

from __future__ import annotations

import sqlite3
from typing import cast

import pytest
from voice_typer.server.history_db import HistoryDB
from voice_typer.server.history_db_internals import crud_writes, retention


def test_fts5_allowlist_matches_the_command_annotation() -> None:
    """The runtime allowlist is derived from the ``Fts5Command`` annotation."""
    assert {"rebuild", "optimize"} == retention._FTS5_COMMANDS


@pytest.mark.parametrize("command", ["rebuild", "optimize"])
def test_rebuild_fts_accepts_allowlisted_commands(command: str) -> None:
    """Both allowlisted commands pass the guard (failure is the FTS5 layer)."""
    conn = sqlite3.connect(":memory:")
    try:
        # No FTS5 tables exist, so the statement itself errors, but only
        # AFTER the allowlist check accepted the command.
        assert retention._rebuild_fts(conn, command=cast("retention.Fts5Command", command)) is False
    finally:
        conn.close()


@pytest.mark.parametrize(
    "command",
    ["drop", "rebuild); DROP TABLE transcriptions; --", "", "REBUILD"],
)
def test_rebuild_fts_rejects_commands_outside_the_allowlist(command: str) -> None:
    """Anything but the two allowlisted words is refused before any SQL runs."""
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="unsupported FTS5 command"):
            retention._rebuild_fts(conn, command=cast("retention.Fts5Command", command))
    finally:
        conn.close()


class _FakeCursor:
    def fetchone(self):
        return (0, 0, 0)


class _RecordingConnection:
    """Captures every statement handed to ``execute``."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> _FakeCursor:
        self.statements.append(statement)
        return _FakeCursor()


@pytest.mark.parametrize(
    ("truncate", "expected"),
    [(True, "PRAGMA wal_checkpoint(TRUNCATE)"), (False, "PRAGMA wal_checkpoint(RESTART)")],
)
def test_checkpoint_wal_executes_fixed_literal_statements(truncate: bool, expected: str) -> None:
    """The checkpoint statement is a fixed literal, never an interpolated one."""
    conn = _RecordingConnection()
    assert crud_writes.checkpoint_wal(cast("HistoryDB", object()), cast("sqlite3.Connection", conn), truncate) is True
    assert conn.statements == [expected]
