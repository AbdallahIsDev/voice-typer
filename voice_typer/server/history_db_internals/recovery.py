"""Corruption recovery, pre-migration backup, and iterdump-replay helpers.

Extracted from the once-monolithic ``history_db.py`` (wave 2 split). The
functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes
(``db_path``, ``_init_error``, ``_connections_lock``,
``_all_read_connections``, ``_read_conn_generation``, ``_read_local``)
via the passed-in reference.

Free functions:

- :func:`backup_before_migration` — best-effort copy of the DB (and
  ``-wal``/``-shm`` sidecars) before a migration runs.
- :func:`maybe_recover_from_corruption` — runs ``PRAGMA quick_check``;
  if not ``("ok",)``, renames the corrupt DB and returns a fresh
  connection on a new (empty) DB file. Returns ``None`` if healthy.
- :func:`try_iterdump_recovery` — opens the corrupt DB read-only and
  extracts the ``INSERT INTO transcriptions ...`` statements.
- :func:`apply_recovered_inserts` — replays recovered INSERTs on the
  fresh DB.
- :func:`notify_corruption_recovered` — emits ``history_corrupted``
  event + tray notification.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)


def backup_before_migration(db: HistoryDB, current_version: int) -> None:
    """Best-effort copy of the DB (and ``-wal``/``-shm`` sidecars) to
    ``history.db.pre-migration-v<from>.bak`` before a migration runs.

    Best-effort: if the copy fails (disk full, permissions,
    cross-device), log + continue — DO NOT block the migration on
    backup failure. The user's history is valuable, but blocking the
    schema migration on a backup failure would leave the app in a
    worse state (stuck on the old schema) than simply proceeding
    without a backup.

    Single-slot naming: ``history.db.pre-migration-v<from>.bak`` (NOT
    timestamped). A second migration run would skip the backup entirely
    because ``current_version == _CURRENT_SCHEMA_VERSION`` (the backup
    is only taken when ``current_version < _CURRENT_SCHEMA_VERSION``,
    checked in the caller). Even if the same version were migrated
    twice (e.g. a v3 -> v4 migration followed by a v3 -> v4 retry after
    a failure), the second backup would overwrite the first — acceptable
    because the first backup was of the same DB state.

    The copy uses ``_secure_copy_db_file`` (``O_NOFOLLOW`` on both
    source and destination, ``0o600`` on the destination, ``fsync``
    after write). This replaces the previous ``shutil.copy2`` call which
    followed symlinks on BOTH source and destination. The destination is
    created with mode ``0o600`` on POSIX so the backup is not
    world-readable (the main DB file is also ``0o600``).
    """
    # ``_secure_copy_db_file`` is defined on the history_db module
    # (FR-29 source-inspection test requires its ``def`` to appear
    # exactly once in that module's source). Import lazily so the
    # function reference always tracks monkeypatches (e.g. the spy
    # installed by ``test_backup_uses_secure_copy_not_shutil_copy2``).
    from voice_typer.server import history_db as _hd

    try:
        bak_main = db.db_path.with_name(f"{db.db_path.name}.pre-migration-v{current_version}.bak")
        # copy the main DB file via the secure helper
        # (O_NOFOLLOW on src+dst, 0o600 on dst, fsync).
        if db.db_path.exists():
            _hd._secure_copy_db_file(db.db_path, bak_main)
        # Copy the -wal and -shm sidecars if they exist (WAL mode).
        # These hold uncheckpointed pages that would otherwise be lost
        # — including them makes the backup a complete restorable
        # snapshot. Routed through the same symlink-safe helper.
        for sidecar in ("-wal", "-shm"):
            src = db.db_path.with_name(db.db_path.name + sidecar)
            if src.exists():
                dst = bak_main.with_name(bak_main.name + sidecar)
                _hd._secure_copy_db_file(src, dst)
        log.info(
            "[HISTORY_DB] Pre-migration backup created: %s (from schema v%d)",
            bak_main.name,
            current_version,
        )
    except OSError as e:
        # Best-effort: do NOT block the migration on backup failure.
        log.warning(
            "[HISTORY_DB] Pre-migration backup FAILED (continuing with migration anyway): %s",
            e,
        )


def secure_copy_db_file_impl(src: Path, dst: Path) -> None:
    """FR-8 / SEC-002: symlink-safe, fsync-on-write binary file copy.

    Body of :func:`voice_typer.server.history_db._secure_copy_db_file`,
    extracted into this module during the wave-2 ``history_db`` split.
    The wrapper in ``history_db.py`` remains a one-line delegator so
    the FR-29 source-inspection test
    (``test_secure_copy_db_file_defined_exactly_once``) and the
    monkeypatch spy in
    ``test_backup_uses_secure_copy_not_shutil_copy2`` keep working
    unchanged.

    Replaces the previous ``shutil.copy2`` call which followed symlinks
    on BOTH source and destination — a symlink-planting attacker could
    redirect the backup to an arbitrary file or read an arbitrary
    file's content into the backup location.

    On POSIX:
        * Open src with ``O_RDONLY | O_NOFOLLOW`` — raises ``ELOOP``
          if src is a symlink (refuses to follow).
        * Open dst with ``O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW`` —
          raises ``ELOOP`` if dst is a symlink (refuses to write through
          to the symlink target).
        * Create dst with mode ``0o600`` so the backup is not
          world-readable (the main DB file is also ``0o600``).
        * ``fsync`` the dst fd after writing so the backup survives
          power loss (the source DB file is fsynced by SQLite itself).

    On Windows (``O_NOFOLLOW`` is not supported by the Win32 filesystem):
        * Reject src / dst reparse points via ``os.lstat`` +
          ``st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`` check
          (mirrors the pattern in
          :func:`voice_typer.server.secure_file_io._secure_read_text`).
        * Fall through to plain binary copy + ``fsync``.

    The destination's parent directory is NOT fsynced here — this is
    a backup helper, not a durability-critical atomic write. The
    caller (``backup_before_migration``) is best-effort and explicitly
    tolerates backup loss.
    """
    import shutil

    from voice_typer.server.platform_utils import is_windows

    if not is_windows():
        # ── POSIX: O_NOFOLLOW on both src and dst ────────────────────
        # Source: open read-only, refuse symlinks (ELOOP).
        src_fd = os.open(str(src), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            # Destination: open write-only, create-or-truncate, refuse
            # symlinks (ELOOP). Mode 0o600 — backup is not world-readable.
            # ``open`` mode arg is masked by umask, so we explicitly
            # fchmod() after open to guarantee 0o600 regardless of umask.
            dst_fd = os.open(
                str(dst),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            try:
                os.fchmod(dst_fd, 0o600)
                with (
                    os.fdopen(src_fd, "rb", closefd=False) as f_src,
                    os.fdopen(dst_fd, "wb", closefd=False) as f_dst,
                ):
                    shutil.copyfileobj(f_src, f_dst, length=64 * 1024)
                # fsync the dst fd so the bytes hit disk before we
                # close it (durability — survives power loss).
                os.fsync(dst_fd)
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
        return

    # ── Windows: reparse-point rejection + binary copy + fsync ────────
    # ``O_NOFOLLOW`` is not supported on Win32; we use ``os.lstat`` +
    # ``st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`` to reject
    # reparse points (the Windows analogue of symlinks). Mirrors the
    # pattern in ``secure_file_io._secure_read_text``.
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400  # noqa: N806

    for label, p in (("src", src), ("dst", dst)):
        try:
            stat_result = os.lstat(str(p)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
        except (AttributeError, OSError):
            attrs = 0
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"SEC-002: refusing to follow reparse point ({label}): {p}")

    with open(src, "rb") as f_src, open(dst, "wb") as f_dst:
        shutil.copyfileobj(f_src, f_dst, length=64 * 1024)
        f_dst.flush()
        os.fsync(f_dst.fileno())


def maybe_recover_from_corruption(
    db: HistoryDB,
    conn: sqlite3.Connection,
) -> sqlite3.Connection | None:
    """Run ``PRAGMA quick_check``; if not ``("ok",)``, rename the
    corrupt DB file (and its WAL/SHM sidecars) to
    ``history.db.corrupt-<timestamp>`` and return a fresh connection on
    a new (empty) DB file.

    Returns ``None`` if the DB is healthy. Returns a new connection if
    corruption was detected and recovery succeeded. Sets
    ``db._init_error`` and returns ``None`` if recovery failed (e.g. the
    rename or reopen raised).

    The caller is responsible for re-running schema init on the returned
    connection (the fresh DB has no tables yet).

    After renaming the corrupt DB, attempts to recover user-data rows
    via ``iterdump()`` and replays them on the fresh DB. Also publishes
    a ``history_corrupted`` event via ``event_bus`` so the renderer can
    surface a toast to the user. If ``iterdump()`` fails (severe
    corruption), the rename + fresh-DB path still runs as a fallback
    (``recovered_count=0``).
    """
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as e:
        log.error(
            "[HISTORY_DB] PRAGMA quick_check raised: %s (treating as corruption and attempting recovery)",
            e,
        )
        # Fall through to the recovery path — we can't verify integrity,
        # so assume the worst and rename.
        rows = [("quick_check raised", str(e))]

    if len(rows) == 1 and rows[0][0] == "ok":
        return None  # healthy

    log.error(
        "[HISTORY_DB] Integrity check failed: %s. Renaming corrupt DB and creating a fresh one.",
        rows,
    )
    # Close the corrupt connection so we can rename the file. Suppress
    # errors — the connection may already be in a bad state.
    with contextlib.suppress(sqlite3.Error):
        conn.close()
    # Rename the corrupt DB and its WAL/SHM sidecar files.
    timestamp = int(time.time())
    corrupt_suffix = f".corrupt-{timestamp}"
    corrupt_main = db.db_path.with_name(db.db_path.name + corrupt_suffix)
    for sidecar in ("", "-wal", "-shm"):
        src = db.db_path.with_name(db.db_path.name + sidecar)
        if src.exists():
            dst = corrupt_main.with_name(corrupt_main.name + sidecar)
            with contextlib.suppress(OSError):
                src.rename(dst)
    log.warning(
        "[HISTORY_DB] Renamed corrupt DB to %s",
        corrupt_main,
    )
    # invalidate all existing read connections. On POSIX, renaming the
    # corrupt DB file doesn't affect already-open file descriptors —
    # readers would keep reading stale/garbage data from the renamed
    # file. Close every tracked read conn and bump the generation
    # counter so each reader thread's next ``_get_read_conn`` call
    # detects the mismatch, closes its stale thread-local conn, and
    # reconnects to the fresh DB file.
    with db._connections_lock:
        for _ident, rconn in db._all_read_connections:
            with contextlib.suppress(sqlite3.Error):
                rconn.close()
        db._all_read_connections.clear()
        db._read_conn_generation += 1
    # Also clear the current thread's stale read conn (if any) so any
    # subsequent read on this thread reopens immediately.
    if hasattr(db._read_local, "conn") and db._read_local.conn is not None:
        with contextlib.suppress(sqlite3.Error):
            db._read_local.conn.close()
        db._read_local.conn = None
        db._read_local.gen = db._read_conn_generation
    # BEFORE opening the fresh DB, attempt to recover user-data INSERTs
    # from the now-renamed corrupt file. Call back into the method on
    # ``db`` so test monkeypatches (``monkeypatch.setattr(db,
    # "_try_iterdump_recovery", fake)``) are observed.
    recovered_inserts = db._try_iterdump_recovery(corrupt_main)
    # Open a fresh connection on a new (empty) DB file.
    try:
        new_conn = db._open_write_conn()
        db._check_wal_mode(new_conn)
    except sqlite3.Error as e:
        db._init_error = e
        # Even if the fresh DB can't be opened, still emit the corruption
        # event so the user is notified.
        db._notify_corruption_recovered(corrupt_main, 0)
        return None
    # replay the recovered INSERTs on the fresh DB. If no INSERTs were
    # recovered (severe corruption or empty DB), this is a no-op.
    recovered_count = 0
    if recovered_inserts:
        recovered_count = db._apply_recovered_inserts(new_conn, recovered_inserts)
    # emit the history_corrupted event + tray notify.
    db._notify_corruption_recovered(corrupt_main, recovered_count)
    return new_conn


def try_iterdump_recovery(db: HistoryDB, old_db_path: Path) -> list[str]:
    """Attempt to recover INSERT statements from a corrupt DB via
    ``connection.iterdump()``.

    Opens the corrupt DB in read-only mode (``?mode=ro`` URI) so we
    can't compound the corruption by writing to the known-bad file.
    Iterates the dump and returns the list of
    ``INSERT INTO transcriptions ...`` statements.

    Schema statements, schema-meta rows, FTS5 shadow-table rows, and
    ``sqlite_sequence`` rows are filtered out — the fresh DB's
    ``init_schema`` recreates the schema, and replaying ``schema_meta``
    would PRIMARY KEY-conflict with the version row ``init_schema``
    writes.

    Returns an empty list if the corrupt DB file doesn't exist, can't be
    opened read-only, or ``iterdump()`` raises (severe corruption).
    """
    # ``_INSERT_TRANSCRIPTIONS_RE`` lives on the history_db module so
    # tests can ``from voice_typer.server.history_db import
    # _INSERT_TRANSCRIPTIONS_RE`` and verify its behavior. Lazy import
    # so the regex always tracks monkeypatches.
    from voice_typer.server import history_db as _hd

    _INSERT_TRANSCRIPTIONS_RE = _hd._INSERT_TRANSCRIPTIONS_RE  # noqa: N806

    inserts: list[str] = []
    if not old_db_path.exists():
        log.warning(
            "[HISTORY_DB] iterdump recovery: corrupt DB file does not exist: %s",
            old_db_path,
        )
        return inserts
    # Build the read-only URI. ``Path.as_uri()`` URL-encodes special
    # chars and produces a proper ``file:///`` URI on both POSIX and
    # Windows.
    try:
        uri = old_db_path.resolve().as_uri() + "?mode=ro"
    except (OSError, ValueError) as e:
        log.warning(
            "[HISTORY_DB] iterdump recovery: could not resolve path %s: %s",
            old_db_path,
            e,
        )
        return inserts
    try:
        ro_conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] iterdump recovery: could not open corrupt DB read-only (%s): %s",
            old_db_path,
            e,
        )
        return inserts
    try:
        for stmt in ro_conn.iterdump():
            stripped = stmt.lstrip()
            if _INSERT_TRANSCRIPTIONS_RE.match(stripped):
                inserts.append(stmt)
    except sqlite3.Error as e:
        # Severe corruption: iterdump raised mid-iteration. Return
        # whatever we have so far (may be partial) — partial recovery
        # is strictly better than no recovery.
        log.warning(
            "[HISTORY_DB] iterdump recovery: iterdump() raised mid-iteration "
            "(severe corruption, returning %d partial statements): %s",
            len(inserts),
            e,
        )
        return inserts
    finally:
        with contextlib.suppress(sqlite3.Error):
            ro_conn.close()
    log.info(
        "[HISTORY_DB] iterdump recovery: recovered %d INSERT statements from %s",
        len(inserts),
        old_db_path,
    )
    return inserts


def apply_recovered_inserts(
    db: HistoryDB,
    conn: sqlite3.Connection,
    inserts: list[str],
) -> int:
    """Replay iterdump-recovered INSERT statements on the fresh DB.

    The fresh DB's schema is not yet set up at this point
    (``init_schema``'s recursive ``_is_recovery=True`` call runs AFTER
    this method returns), so we run ``init_schema`` ourselves first.
    The later recursive call is a no-op because all CREATE statements
    use ``IF NOT EXISTS`` and ``schema_meta`` already has
    ``version=_CURRENT_SCHEMA_VERSION``.

    The INSERTs are applied via ``executescript`` so a single bad
    statement (e.g. a row that violates a constraint) doesn't roll back
    all the others — partial recovery is preferable to no recovery.

    Returns the actual number of rows in the ``transcriptions`` table
    after the attempt (may be less than ``len(inserts)``).
    """
    from voice_typer.server.history_db_internals.schema import (
        init_schema as _init_schema,
    )

    try:
        _init_schema(db, conn, _is_recovery=True)
    except Exception as e:  # noqa: BLE001 — best-effort recovery
        log.warning(
            "[HISTORY_DB] iterdump recovery: could not initialize schema for replay (skipping %d INSERTs): %s",
            len(inserts),
            e,
        )
        return 0
    # Apply the INSERTs. ``executescript`` issues a COMMIT first
    # (clearing any pending transaction from init_schema), then runs
    # each statement. The FTS5 AFTER-INSERT trigger fires for each row
    # and populates ``transcriptions_fts`` automatically.
    try:
        script = "\n".join(inserts)
        conn.executescript(script)
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] iterdump recovery: executescript failed (partial recovery may have occurred): %s",
            e,
        )
    # Count actual rows in the fresh DB. This is more accurate than
    # ``len(inserts)`` because some INSERTs may have failed.
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM transcriptions")
        row = cursor.fetchone()
        count = int(row[0]) if row else 0
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] iterdump recovery: could not count recovered rows: %s",
            e,
        )
        return 0
    if count > 0:
        log.info(
            "[HISTORY_DB] iterdump recovery: %d row(s) recovered into fresh DB",
            count,
        )
    else:
        log.info(
            "[HISTORY_DB] iterdump recovery: no rows recovered (all INSERTs failed or empty source)",
        )
    return count


def notify_corruption_recovered(
    db: HistoryDB,
    corrupt_main: Path,
    recovered_count: int,
) -> None:
    """Surface the corruption event to the user.

    Logs a WARNING-level message naming the backup file's location and
    the number of rows recovered, then publishes a ``history_corrupted``
    event via ``event_bus`` so the renderer can show a toast. If
    ``db._app.tray.notify`` is wired, also calls it for a native OS
    notification.

    All notifications are best-effort: if ``event_bus.publish`` or
    ``tray.notify`` raises, the recovery path must still succeed.
    """
    log.warning(
        "[HISTORY_DB] History database was corrupted and has been backed up to %s. Recovered %d row(s) via iterdump.",
        corrupt_main,
        recovered_count,
    )
    # Best-effort event_bus publication.
    try:
        from voice_typer.server import event_bus

        event_bus.publish(
            {
                "type": "history_corrupted",
                "data": {
                    "path": str(corrupt_main),
                    "db_path": str(db.db_path),
                    "recovered_count": recovered_count,
                },
            }
        )
    except Exception as e:  # noqa: BLE001 — best-effort notification
        log.warning(
            "[HISTORY_DB] event_bus.publish(history_corrupted) failed (best-effort, recovery continues): %s",
            e,
        )
    # Best-effort tray notification. ``db._app`` is set by the app
    # shell (not by HistoryDB.__init__) — use getattr so the
    # attribute-missing case during early init is handled.
    app = getattr(db, "_app", None)
    if app is None:
        return
    tray = getattr(app, "tray", None)
    if tray is None:
        return
    notify = getattr(tray, "notify", None)
    if notify is None:
        return
    try:
        notify(
            APP_NAME,
            f"History database was corrupted and backed up. {recovered_count} row(s) recovered.",
        )
    except Exception as e:  # noqa: BLE001 — best-effort notification
        log.warning(
            "[HISTORY_DB] tray.notify failed (best-effort, recovery continues): %s",
            e,
        )
