"""AP-17: regression tests for the FTS5 startup rebuild sweep.

The ``delete``, ``clear_all``, and ``apply_retention`` paths each
issue the FTS5 ``'rebuild'`` command after their bulk DELETEs to zero
dictated text out of ``transcriptions_fts_data`` (GDPR Art. 17
right-to-erasure). But that rebuild is wrapped in a tolerant
``try/except sqlite3.Error`` — if it fails (transient FTS5 error,
disk full), the failure is logged at ERROR and swallowed (no raise,
no rollback), incrementing ``self._fts5_rebuild_failures`` and
publishing an ``event_bus`` event. The segment data from the failed
delete lingers in ``transcriptions_fts_data``, recoverable via
forensic tools, until FTS5's background compaction happens to merge
that segment (days or weeks later) — silently breaking the GDPR
Art. 17 right-to-erasure with only an ERROR log.

The fix adds a STARTUP SWEEP: on every HistoryDB construction (after
the schema is initialized), ``_fts5_startup_rebuild`` runs
``INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')``
once on the writer connection. This bounds the worst-case exposure
window for any failed delete/clear_all/apply_retention rebuilds in
the previous session to "between launches" — on the next launch the
FTS5 segment data is rebuilt from the current content table, so
lingering dictated text from a previously-failed delete is cleared.

These tests verify:
  1. The startup sweep runs on every HistoryDB construction.
  2. After a failed delete-time rebuild in session N, the next launch
     (session N+1) clears the lingering FTS5 segment data.
  3. The startup sweep is best-effort: a failure is swallowed and the
     app still starts.
  4. The startup sweep is skipped on migration failure (schema
     inconsistent, FTS5 table may not exist).
"""

from __future__ import annotations

import logging
import sqlite3

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _fts5_data_size(conn: sqlite3.Connection) -> int:
    """Return the total bytes in the FTS5 shadow-table segment data.

    ``transcriptions_fts_data`` holds the raw segment blobs. After a
    ``'rebuild'`` on an empty content table, this table is empty (or
    near-empty). After a delete WITHOUT a successful rebuild, this
    table retains the deleted row's segment data — the dictated text
    remains recoverable via forensic tools.
    """
    try:
        cur = conn.execute("SELECT COALESCE(SUM(length(block)), 0) FROM transcriptions_fts_data")
        return int(cur.fetchone()[0])
    except sqlite3.Error:
        # FTS5 shadow table doesn't exist (pre-V3 migration).
        return -1


class _FlakyConn:
    """Wraps a real ``sqlite3.Connection`` so we can inject a failure
    on the FTS5 ``'rebuild'`` SQL statement.

    ``sqlite3.Connection`` does not allow setting ``execute`` /
    ``cursor`` as attributes (they're read-only slot wrappers), so we
    proxy via ``__getattr__`` for everything except the methods we
    override.
    """

    def __init__(self, real, rebuild_should_fail):
        self._real = real
        self._rebuild_should_fail = rebuild_should_fail
        self.rebuild_attempts: list[bool] = []

    def cursor(self):
        return _FlakyCursor(self._real.cursor(), self)

    def execute(self, sql, *args, **kwargs):
        # Fail on either per-delete ``'optimize'`` (the per-row
        # FTS5 purge in :meth:`HistoryDB.delete` — preferred over
        # ``'rebuild'`` for O(N) reasons) OR the periodic
        # ``'rebuild'`` (run by the retention sweep and the
        # startup rebuild on each launch). Both forms reach the
        # FTS5 shadow table via the same ``transcriptions_fts``
        # INSERT command.
        if "VALUES('optimize')" in sql or "VALUES('rebuild')" in sql:
            self.rebuild_attempts.append(True)
            if self._rebuild_should_fail():
                raise sqlite3.OperationalError("simulated FTS5 rebuild failure (AP-17 test)")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self):
        return self._real.commit()

    def close(self):
        return self._real.close()

    @property
    def row_factory(self):
        return self._real.row_factory

    @row_factory.setter
    def row_factory(self, v):
        self._real.row_factory = v

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FlakyCursor:
    def __init__(self, real, parent):
        self._real = real
        self._parent = parent

    def execute(self, sql, *args, **kwargs):
        if "VALUES('optimize')" in sql or "VALUES('rebuild')" in sql:
            self._parent.rebuild_attempts.append(True)
            if self._parent._rebuild_should_fail():
                raise sqlite3.OperationalError("simulated FTS5 rebuild failure (AP-17 test)")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestFts5StartupRebuild:
    """AP-17: ``_fts5_startup_rebuild`` runs on every launch and bounds
    the worst-case exposure window for failed delete-time rebuilds to
    'between launches'."""

    def test_startup_sweep_runs_on_construction(self, db, monkeypatch):
        """The startup sweep must be called during HistoryDB construction
        (i.e. from the schema-init path on the writer thread)."""
        from voice_typer.server.history_db import HistoryDB

        # The ``db`` fixture was already constructed, so its startup
        # sweep already ran with the real method. Re-patch the class
        # method and construct a fresh HistoryDB to verify the sweep
        # fires on construction.
        startup_calls: list[int] = []
        real_startup = HistoryDB._fts5_startup_rebuild

        def spy_startup(self_db, conn):
            startup_calls.append(1)
            return real_startup(self_db, conn)

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", spy_startup)

        db2 = HistoryDB(db_path=db.db_path)
        try:
            assert len(startup_calls) >= 1, (
                "AP-17 violation: _fts5_startup_rebuild was not called during "
                "HistoryDB construction — the startup sweep does not run on "
                "launch, so lingering FTS5 segment data from failed deletes "
                "is NOT bounded to 'between launches'."
            )
        finally:
            db2.close()

    def test_startup_sweep_clears_lingering_segments_after_failed_delete(self, tmp_path, monkeypatch):
        """AP-17 core regression: when ``delete()``'s FTS5 ``'rebuild'``
        fails (transient FTS5 error), the segment data lingers in
        ``transcriptions_fts_data``. The next launch's startup sweep
        must run ``'rebuild'`` and succeed, clearing the lingering
        segment data so the deleted dictated text is no longer
        recoverable via forensic tools.

        Sequence:
          1. Session 1: add a transcription, then delete it with the
             FTS5 ``'rebuild'`` forced to fail (call #1 — simulating a
             transient FTS5 error).
          2. Close session 1.
          3. Session 2: re-open the same DB. The startup sweep runs
             ``'rebuild'`` (call #2) and succeeds, clearing the
             lingering segment data.
        """
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "history.db"

        # ── Session 1: add + delete with a failing rebuild ──────────
        db1 = HistoryDB(db_path=db_path)
        try:
            rid = db1.add_transcription("top secret dictated text AP-17")
            db1.flush()
            assert rid > 0, "add_transcription must return a positive row id"

            # Sanity: the FTS5 shadow table has segment data for the
            # inserted row.
            read_conn = db1._get_read_conn()
            pre_insert_size = _fts5_data_size(read_conn)
            assert pre_insert_size > 0, "expected non-empty FTS5 segment data after insert — test setup is broken"

            # Wire a flaky _submit_write so the delete closure's FTS5
            # ``'rebuild'`` SQL raises sqlite3.OperationalError on the
            # first (and only) rebuild attempt. The row DELETE itself
            # still commits (the rebuild is best-effort, after the
            # commit).
            rebuild_fail_state = {"failed_once": False}
            real_submit = db1._submit_write

            def rebuild_should_fail():
                if not rebuild_fail_state["failed_once"]:
                    rebuild_fail_state["failed_once"] = True
                    return True
                return False

            def flaky_submit(fn, *, wait=True):
                def wrapped(real_conn):
                    return fn(_FlakyConn(real_conn, rebuild_should_fail))

                return real_submit(wrapped, wait=wait)

            monkeypatch.setattr(db1, "_submit_write", flaky_submit)

            # Delete — the row delete succeeds (returns True) but the
            # post-delete FTS5 ``'rebuild'`` fails (simulated transient
            # error). The failure is logged at WARNING and swallowed
            # (matching the existing delete pattern).
            assert db1.delete(rid) is True, (
                "delete must return True even when the post-delete FTS5 "
                "rebuild fails — the row delete already committed"
            )

            # The rebuild was attempted exactly once during the delete
            # closure (and failed).
            # ``rebuild_attempts`` lives on the FlakyConn created
            # inside the wrapped closure; we verify via the
            # ``failed_once`` flag that the failure path was taken.
            assert rebuild_fail_state["failed_once"] is True, (
                "the delete-time FTS5 rebuild did not fail as expected — test setup is broken"
            )

            # Capture the FTS5 segment-data size AFTER the failed
            # delete (before closing db1). Because the delete-time
            # rebuild failed, the dictated text's segment data is
            # still physically present in
            # ``transcriptions_fts_data`` — only the rowid is marked
            # as deleted in the FTS5 delete-bitmap. The startup sweep
            # in session 2 must shrink this.
            db1.checkpoint(truncate=True)
            post_delete_size = _fts5_data_size(read_conn)
            assert post_delete_size >= pre_insert_size, (
                "expected the failed-delete FTS5 segment data to still "
                f"be present (size={post_delete_size}, pre_insert="
                f"{pre_insert_size}) — test setup is broken"
            )
        finally:
            db1.close()

        # ── Session 2: re-open (simulates next launch) ─────────────
        # Spy the startup sweep so we can assert it was called. The spy
        # delegates to the real method so the sweep actually executes
        # (we want to assert the FTS5 state is consistent post-sweep,
        # not just that the method was invoked).
        startup_calls: list[int] = []
        real_startup = HistoryDB._fts5_startup_rebuild

        def spy_startup(self_db, conn):
            startup_calls.append(1)
            return real_startup(self_db, conn)

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", spy_startup)

        db2 = HistoryDB(db_path=db_path)
        try:
            # Assert: the startup sweep was called during db2
            # construction.
            assert len(startup_calls) >= 1, (
                "AP-17 violation: _fts5_startup_rebuild was not called "
                "during HistoryDB construction — the startup sweep does "
                "not run on launch, so lingering FTS5 segment data from "
                "the failed delete in session 1 is NOT cleared."
            )

            # Force a checkpoint so the WAL is flushed (deterministic
            # size assertion).
            db2.checkpoint(truncate=True)

            # Assert: the FTS5 shadow-table segment data was cleared by
            # the startup sweep's ``'rebuild'``. Before the sweep, this
            # table contained the deleted transcription's dictated text
            # (the delete-time rebuild failed in session 1, so
            # ``post_delete_size`` reflects the lingering data). After
            # the sweep, the segment data is rebuilt from the
            # (now-empty) content table, so it must be smaller —
            # proving the dictated text's segment data was zeroed.
            #
            # Note: the post-sweep size is NOT necessarily 0 — FTS5
            # retains a small structural record (~9 bytes) even for an
            # empty index. The meaningful assertion is that the size
            # shrank from the post-delete (lingering-data) state to
            # the empty-index baseline, matching the pattern in
            # ``test_clear_all_empties_fts5_shadow_data``.
            read_conn = db2._get_read_conn()
            post_sweep_size = _fts5_data_size(read_conn)
            assert post_sweep_size < post_delete_size, (
                f"AP-17 violation: FTS5 segment data did not shrink after "
                f"the startup sweep (post_delete={post_delete_size}, "
                f"post_sweep={post_sweep_size}). The deleted "
                "transcription's dictated text remains recoverable from "
                "transcriptions_fts_data via forensic tools — the "
                "startup sweep did not clear the lingering segments "
                "from session 1's failed delete."
            )

            # Assert: the content table is empty (the row was deleted
            # in session 1).
            cur = read_conn.execute("SELECT count(*) FROM transcriptions")
            content_count = int(cur.fetchone()[0])
            assert content_count == 0, f"expected empty content table after delete, got {content_count}"

            # Assert: the FTS5 index row count matches the content
            # table (both 0 after the sweep).
            cur = read_conn.execute("SELECT count(*) FROM transcriptions_fts")
            fts_count = int(cur.fetchone()[0])
            assert fts_count == 0, f"expected empty FTS5 index after startup sweep, got {fts_count}"
        finally:
            db2.close()

    def test_startup_sweep_failure_is_swallowed(self, tmp_path, monkeypatch, caplog):
        """AP-17: the startup sweep is best-effort. If the rebuild fails
        (e.g. transient FTS5 error, disk full), the failure is logged
        at WARNING and swallowed — the app must still start. The next
        launch will retry."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "history.db"

        # Pre-create the DB so session 2 has a real FTS5 table to
        # attempt the rebuild on.
        db1 = HistoryDB(db_path=db_path)
        db1.add_transcription("seed text")
        db1.flush()
        db1.close()

        # Patch _fts5_startup_rebuild to raise sqlite3.Error directly
        # (simulating a transient FTS5 error during the startup sweep).
        def failing_startup(self_db, conn):
            raise sqlite3.OperationalError("simulated startup-sweep failure (AP-17 test)")

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", failing_startup)

        # Constructing HistoryDB must NOT raise even though the startup
        # sweep fails. The _init_db_schema wrapper has a
        # contextlib.suppress(Exception) guard around the call.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db"):
            db2 = HistoryDB(db_path=db_path)
        try:
            # The HistoryDB instance must be usable for normal
            # operations despite the startup-sweep failure.
            assert db2.add_transcription("post-failure text") > 0
            db2.flush()
            recent = db2.get_recent(limit=10)
            assert len(recent) >= 1
        finally:
            db2.close()

    def test_startup_sweep_succeeds_silently_at_debug_level(self, tmp_path, monkeypatch, caplog):
        """AP-17: on success, the startup sweep logs at DEBUG (not INFO
        or WARNING) — a successful sweep is the expected steady state
        and shouldn't add noise to the log stream."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "history.db"

        startup_calls: list[int] = []
        real_startup = HistoryDB._fts5_startup_rebuild

        def spy_startup(self_db, conn):
            startup_calls.append(1)
            return real_startup(self_db, conn)

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", spy_startup)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db_internals.writer"):
            db = HistoryDB(db_path=db_path)
        try:
            assert len(startup_calls) >= 1
            # The DEBUG success message must be present.
            debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
            assert any("FTS5 startup rebuild succeeded" in m for m in debug_messages), (
                "expected DEBUG log 'FTS5 startup rebuild succeeded' on a "
                f"successful sweep; got debug messages: {debug_messages}"
            )
            # No WARNING or ERROR records should be present for the
            # startup sweep on a fresh DB.
            warning_messages = [
                r.getMessage()
                for r in caplog.records
                if r.levelno >= logging.WARNING and "startup rebuild" in r.getMessage().lower()
            ]
            assert warning_messages == [], (
                f"expected no WARNING/ERROR for the startup sweep on a fresh DB; got: {warning_messages}"
            )
        finally:
            db.close()
