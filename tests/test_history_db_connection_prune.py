"""DJ-19 regression tests: dead-thread read connections are pruned.

``HistoryDB._get_read_conn`` already calls
``_prune_dead_read_connections_locked`` opportunistically on each
new-connection creation. DJ-19 adds a periodic prune worker (single
long-lived daemon thread, NOT a ``threading.Timer`` cascade — that's
the DJ-37 anti-pattern) so dead-thread connections are pruned within
``_READ_CONN_PRUNE_INTERVAL_S`` seconds even when no new reader
threads show up.

These tests verify:
1. The prune thread exists and is a daemon.
2. The prune thread is started by ``__init__`` and stopped by ``close``.
3. The prune worker actually closes connections whose owning thread
   has exited, within the configured interval.
4. The prune worker uses ``Event.wait(timeout=...)`` (NOT a
   ``threading.Timer`` cascade — the DJ-37 anti-pattern).
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history_prune.db")
    yield db_instance
    db_instance.close()


def test_prune_thread_started_on_init(db):
    """DJ-19: ``__init__`` starts the prune daemon thread."""
    assert db._read_conn_prune_thread is not None
    assert db._read_conn_prune_thread.is_alive()
    assert db._read_conn_prune_thread.daemon, "prune thread must be a daemon so it dies at process exit"
    assert db._read_conn_prune_thread.name == "HistoryDBReadConnPrune"


def test_prune_thread_uses_event_wait_not_timer(tmp_path, monkeypatch):
    """DJ-19 / DJ-37: the prune worker must NOT use ``threading.Timer``.

    The cascade pattern (``Timer(60, ...).start()`` re-scheduled from
    inside the callback) is the DJ-37 anti-pattern: each iteration
    allocates a new Timer and depends on the previous callback firing
    to schedule the next. A single long-lived daemon thread that wakes
    via ``Event.wait(timeout=...)`` is the correct pattern.
    """
    from voice_typer.server import history_db

    timer_instances: list[threading.Timer] = []
    real_timer = threading.Timer

    def tracking_timer(*args, **kwargs):
        t = real_timer(*args, **kwargs)
        timer_instances.append(t)
        return t

    # Patch threading.Timer at the history_db module's namespace so
    # the prune worker would pick it up if it used Timer.
    monkeypatch.setattr(history_db.threading, "Timer", tracking_timer)

    # use the pytest ``tmp_path`` fixture (auto-cleaned by
    # pytest's tmp_path_factory) instead of tempfile.mkdtemp() (which
    # leaks the dir on test failure / SIGTERM).
    db_path = tmp_path / "test_prune_not_timer.db"
    db = history_db.HistoryDB(db_path=db_path)
    try:
        # Give the prune thread a moment to potentially schedule a Timer
        # (it shouldn't — it uses Event.wait instead).
        time.sleep(0.05)
        assert len(timer_instances) == 0, (
            f"prune worker used threading.Timer (created {len(timer_instances)} Timer instances) — DJ-37 anti-pattern"
        )
    finally:
        db.close()


def test_close_stops_prune_thread(db):
    """DJ-19: ``close()`` signals the prune thread to stop and joins it."""
    assert db._read_conn_prune_thread is not None
    assert db._read_conn_prune_thread.is_alive()
    db.close()
    # After close, the prune thread attributes are cleared and the
    # thread has exited.
    assert db._read_conn_prune_thread is None
    assert db._read_conn_prune_stop_event is None


def test_prune_worker_closes_dead_thread_connections(db, monkeypatch):
    """DJ-19: the prune worker closes connections whose owning thread
    has exited, within ``_READ_CONN_PRUNE_INTERVAL_S`` seconds.

    We can't wait 60s in a test, so we monkey-patch the interval
    constant to 0.1s and add a connection from a thread that
    immediately exits. The prune worker should close it within ~1s.
    """
    from voice_typer.server import history_db

    # Shrink the prune interval to 0.1s so the test runs in <1s.
    monkeypatch.setattr(history_db, "_READ_CONN_PRUNE_INTERVAL_S", 0.1)

    # Restart the prune thread so it picks up the new interval.
    db._stop_read_conn_prune_thread()
    db._start_read_conn_prune_thread()

    def spawn_dead_connection():
        """Run on a worker thread; creates a read conn then exits."""
        # This populates _all_read_connections with an entry whose
        # thread_ident is this worker thread's ident.
        db._get_read_conn()

    t = threading.Thread(target=spawn_dead_connection, name="DeadReaderThread")
    t.start()
    t.join()
    assert not t.is_alive(), "worker thread should have exited"

    # The dead-thread connection is now in _all_read_connections.
    with db._connections_lock:
        dead_conns = [(ident, conn) for ident, conn in db._all_read_connections if ident == t.ident]
    assert len(dead_conns) == 1, f"expected 1 dead-thread connection, got {len(dead_conns)}"
    dead_conn = dead_conns[0][1]

    # Wait for the prune worker to close it (timeout 3s = 30 intervals).
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with db._connections_lock:
            still_alive = any(ident == t.ident for ident, _ in db._all_read_connections)
        if not still_alive:
            break
        time.sleep(0.05)

    with db._connections_lock:
        still_alive = any(ident == t.ident for ident, _ in db._all_read_connections)
    assert not still_alive, "prune worker did not close the dead-thread connection within 3s"

    # Verify the connection itself was actually closed.
    # A closed sqlite3.Connection raises sqlite3.ProgrammingError on use.
    with pytest.raises(sqlite3.ProgrammingError):
        dead_conn.execute("SELECT 1")


def test_prune_worker_survives_transient_error(db, monkeypatch):
    """DJ-19: the prune worker keeps running even if a prune tick raises.

    The worker's ``except Exception`` clause must catch the error and
    continue (next interval), NOT crash and permanently disable the
    worker.
    """
    from voice_typer.server import history_db

    # Shrink the interval so we can exercise multiple ticks quickly.
    monkeypatch.setattr(history_db, "_READ_CONN_PRUNE_INTERVAL_S", 0.1)
    db._stop_read_conn_prune_thread()

    # Make _prune_dead_read_connections_locked raise on the first call
    # then succeed. We use a mutable flag.
    call_count = {"n": 0}
    real_prune = db._prune_dead_read_connections_locked

    def flaky_prune():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise sqlite3.OperationalError("simulated transient error")
        return real_prune()

    monkeypatch.setattr(db, "_prune_dead_read_connections_locked", flaky_prune)
    db._start_read_conn_prune_thread()

    # Wait for at least 2 ticks (each ~0.1s).
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and call_count["n"] < 2:
        time.sleep(0.05)

    assert call_count["n"] >= 2, f"prune worker died after first tick error; only {call_count['n']} tick(s) ran"
    assert db._read_conn_prune_thread is not None
    assert db._read_conn_prune_thread.is_alive(), "prune worker thread should still be alive after a transient error"


# the ``tempfile_mkdtemp()`` helper was removed — the
# ``test_prune_thread_uses_event_wait_not_timer`` test now uses the
# pytest ``tmp_path`` fixture (auto-cleaned) instead of leaking a
# temp dir on test failure / SIGTERM. No other test in this module
# used the helper.
