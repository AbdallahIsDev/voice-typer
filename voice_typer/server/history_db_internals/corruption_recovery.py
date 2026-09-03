"""DB file-safety helpers: secure copy, legacy relocation, corruption recovery.

Extracted from the once-monolithic ``history_db.py``. The functions in
this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes (the DB
path, the init-error slot, the read-connection registry) via the
passed-in reference, and call back into other ``HistoryDB`` methods
(``_open_write_conn``, ``_check_wal_mode``, ``_try_iterdump_recovery``,
...) through ``db.<method>(...)`` so class-level monkeypatches keep
working. The public ``HistoryDB`` class keeps thin delegating methods
for the instance-scoped functions and re-exports the pure helpers under
their original names.

Names referenced through the ``history_db`` facade namespace AT CALL
TIME (lazy ``_hd.<NAME>`` reads) so tests that monkeypatch them on the
facade keep working: ``_secure_copy_db_file`` (patched by the
tests), ``_INSERT_TRANSCRIPTIONS_RE``, ``DB_SUBDIR``.

Free functions:

- :func:`_secure_copy_db_file` — symlink-safe, fsync-on-write
  binary copy used by every DB-file backup path. Re-exported on the
  facade under the same name.
- :func:`_backup_before_migration` — best-effort pre-migration backup
  of the main DB + WAL/SHM sidecars.
- :func:`_maybe_recover_from_corruption` — ``PRAGMA quick_check``
  gate: rename a corrupt DB to ``history.db.corrupt-<ts>``, salvage
  user rows via iterdump, reopen a fresh DB.
- :func:`_try_iterdump_recovery` — read-only iterdump() extraction of
  ``INSERT INTO transcriptions`` statements from the renamed corrupt
  file.
- :func:`_apply_recovered_inserts` — replay recovered INSERTs on the
  fresh DB and report the surviving row count.
- :func:`_notify_corruption_recovered` — user-facing WARNING log +
  ``history_corrupted`` event_bus publication + best-effort tray notify.
- :func:`_maybe_migrate_legacy_db` / :func:`_maybe_move_legacy_sidecar`
  — one-time O2 relocation of a legacy root-located ``history.db``
  (and sidecars) into the ``db/`` subdirectory.
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
from voice_typer.server.platform_utils import is_windows

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)


def _secure_copy_db_file(src: Path, dst: Path) -> None:
    """symlink-safe, fsync-on-write binary file copy.

    Replaces ``shutil.copy2`` in ``_backup_before_migration`` (and
    other DB-sidecar backup paths). ``shutil.copy2`` follows symlinks on
    BOTH source and destination:

    - If ``src`` is a symlink planted by an attacker, ``copy2`` reads
      the symlink TARGET's content (info disclosure).
    - If ``dst`` is a symlink planted by an attacker, ``copy2`` writes
      THROUGH it to the symlink target (backup hijack / data destruction).

    This helper refuses both: on POSIX it opens ``src`` with
    ``O_RDONLY | O_NOFOLLOW`` and ``dst`` with
    ``O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW`` (mode ``0o600``);
    on Windows it rejects reparse points via ``os.lstat`` before
    falling back to a regular binary copy. After the copy it
    ``fsync``s the destination fd so the backup is durable.
    """
    import shutil

    if not is_windows():
        # POSIX: O_NOFOLLOW on both source and destination.
        src_fd = -1
        dst_fd = -1
        try:
            src_fd = os.open(str(src), os.O_RDONLY | os.O_NOFOLLOW)
            dst_fd = os.open(
                str(dst),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(src_fd, "rb", closefd=False) as f_src, os.fdopen(dst_fd, "wb", closefd=False) as f_dst:
                shutil.copyfileobj(f_src, f_dst)
                f_dst.flush()
                os.fsync(f_dst.fileno())
        finally:
            if src_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(src_fd)
            if dst_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(dst_fd)
    else:
        # Windows: O_NOFOLLOW is not supported. Reject reparse points
        # explicitly via os.lstat (mirrors ``_secure_read_text``'s
        # Windows branch) and fall back to a regular binary copy with
        # fsync.
        for p in (src, dst):
            try:
                attrs = getattr(os.lstat(str(p)), "st_file_attributes", 0) or 0
            except (AttributeError, OSError):
                attrs = 0
            if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise OSError(f"Refusing to follow reparse point during copy: {p}")
        with open(src, "rb") as f_src, open(dst, "wb") as f_dst:
            shutil.copyfileobj(f_src, f_dst)
            f_dst.flush()
            os.fsync(f_dst.fileno())


def _backup_before_migration(db: HistoryDB, current_version: int) -> None:
    """Best-effort copy of the DB (and ``-wal``/``-shm``
    sidecars) to ``history.db.pre-migration-v<from>.bak`` before a
    migration runs.

    Best-effort: if the copy fails (disk full, permissions,
    cross-device), log + continue — DO NOT block the migration on
    backup failure. The user's history is valuable, but blocking
    the schema migration on a backup failure would leave the app
    in a worse state (stuck on the old schema) than simply
    proceeding without a backup.

    Single-slot naming: ``history.db.pre-migration-v<from>.bak``
    (NOT timestamped). A second migration run would skip the
    backup entirely because ``current_version ==
    _CURRENT_SCHEMA_VERSION`` (the backup is only taken when
    ``current_version < _CURRENT_SCHEMA_VERSION``, checked in the
    caller). Even if the same version were migrated twice (e.g.
    a v3 -> v4 migration followed by a v3 -> v4 retry after a
    failure), the second backup would overwrite the first —
    acceptable because the first backup was of the same DB state.

    the copy uses ``_secure_copy_db_file``
    (``O_NOFOLLOW`` on both source and destination, ``0o600`` on
    the destination, ``fsync`` after write). This replaces the
    previous ``shutil.copy2`` call which followed symlinks on
    BOTH source and destination (a symlink-planting attacker
    could redirect the backup to an arbitrary file or read an
    arbitrary file's content into the backup location) and had
    no ``fsync``. The destination is created with mode ``0o600``
    on POSIX so the backup is not world-readable (the main DB
    file is also ``0o600``).
    """
    # Read the helper through the facade namespace at call time so a
    # test monkeypatching ``history_db._secure_copy_db_file`` is
    # observed by this function.
    from voice_typer.server import history_db as _hd

    secure_copy = _hd._secure_copy_db_file  # noqa: N806

    try:
        bak_main = db.db_path.with_name(f"{db.db_path.name}.pre-migration-v{current_version}.bak")
        # copy the main DB file via the secure helper
        # (O_NOFOLLOW on src+dst, 0o600 on dst, fsync).
        if db.db_path.exists():
            secure_copy(db.db_path, bak_main)
        # Copy the -wal and -shm sidecars if they exist (WAL mode).
        # These hold uncheckpointed pages that would otherwise be
        # lost — including them makes the backup a complete
        # restorable snapshot. : routed through the same
        # symlink-safe helper.
        for sidecar in ("-wal", "-shm"):
            src = db.db_path.with_name(db.db_path.name + sidecar)
            if src.exists():
                dst = bak_main.with_name(bak_main.name + sidecar)
                secure_copy(src, dst)
        log.info(
            "[HISTORY_DB] Pre-migration backup created: %s (from schema v%d)",
            bak_main.name,
            current_version,
        )
    except OSError as e:
        # Best-effort: do NOT block the migration on backup
        # failure. The user's history is more valuable than the
        # backup — a stuck migration would leave the app on the
        # old schema, which is worse than proceeding without a
        # backup.
        log.warning(
            "[HISTORY_DB] Pre-migration backup FAILED (continuing with migration anyway): %s",
            e,
        )


def _maybe_recover_from_corruption(
    db: HistoryDB,
    conn: sqlite3.Connection,
) -> sqlite3.Connection | None:
    """run ``PRAGMA quick_check``; if the result is
    anything other than ``("ok",)``, rename the corrupt DB file
    (and its WAL/SHM sidecars) to ``history.db.corrupt-<timestamp>``
    and return a fresh connection on a new (empty) DB file.

    Returns ``None`` if the DB is healthy. Returns a new
    connection if corruption was detected and recovery succeeded.
    Sets ``db._init_error`` and returns ``None`` if recovery
    failed (e.g. the rename or reopen raised).

    The caller is responsible for re-running schema init on the
    returned connection (the fresh DB has no tables yet).

    after renaming the corrupt DB, attempts to recover
    user-data rows via ``iterdump()`` and replays them on the
    fresh DB. Also publishes a ``history_corrupted`` event via
    ``event_bus`` so the renderer can surface a toast to the user.
    If ``iterdump()`` fails (severe corruption), the rename +
    fresh-DB path still runs as a fallback (``recovered_count=0``).
    """
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as e:
        log.exception(
            "[HISTORY_DB] PRAGMA quick_check raised: %s (treating as corruption and attempting recovery)",
            e,
        )
        # Fall through to the recovery path — we can't verify
        # integrity, so assume the worst and rename.
        rows = [("quick_check raised", str(e))]

    if len(rows) == 1 and rows[0][0] == "ok":
        return None  # healthy

    log.error(
        "[HISTORY_DB] Integrity check failed: %s. Renaming corrupt DB and creating a fresh one.",
        rows,
    )
    # Close the corrupt connection so we can rename the file.
    # Suppress errors — the connection may already be in a bad
    # state.
    with contextlib.suppress(sqlite3.Error):
        conn.close()
    # invalidate all existing read connections BEFORE renaming.
    # On POSIX, renaming the corrupt DB file doesn't affect
    # already-open file descriptors — readers would keep reading
    # stale/garbage data from the renamed file, so we close every
    # tracked read conn and bump the generation counter so each
    # reader thread's next ``_get_read_conn`` call detects the
    # mismatch, closes its stale thread-local conn, and reconnects
    # to the fresh DB file. On Windows, closing first is MANDATORY
    # for a different reason: an open SQLite handle locks the file,
    # so ``os.rename`` of the corrupt DB (below) silently fails
    # with WinError 32 — the corrupt-renamed file never appears and
    # ``_try_iterdump_recovery`` finds nothing (recovered_count=0).
    # Closing readers before the rename makes recovery work on both
    # platforms. We can't clear other threads' ``_read_local.conn``
    # directly, but the generation check handles it lazily.
    with db._connections_lock:
        for _ident, rconn in db._all_read_connections:
            with contextlib.suppress(sqlite3.Error):
                rconn.close()
        db._all_read_connections.clear()
        db._read_conn_generation += 1
    # Also clear the current thread's stale read conn (if any)
    # so any subsequent read on this thread reopens immediately.
    if hasattr(db._read_local, "conn") and db._read_local.conn is not None:
        with contextlib.suppress(sqlite3.Error):
            db._read_local.conn.close()
        db._read_local.conn = None
        db._read_local.gen = db._read_conn_generation
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
    # BEFORE opening the fresh DB, attempt to recover
    # user-data INSERTs from the now-renamed corrupt file. The
    # corrupt file is at ``corrupt_main``; we open it read-only
    # so we can't compound the corruption by writing to it.
    recovered_inserts = db._try_iterdump_recovery(corrupt_main)
    # Open a fresh connection on a new (empty) DB file.
    try:
        new_conn = db._open_write_conn()
        db._check_wal_mode(new_conn)
    except sqlite3.Error as e:
        db._init_error = e
        # Even if the fresh DB can't be opened, still emit the
        # corruption event so the user is notified.
        db._notify_corruption_recovered(corrupt_main, 0)
        return None
    # replay the recovered INSERTs on the fresh DB.
    # If no INSERTs were recovered (severe corruption or empty
    # DB), this is a no-op and the fresh DB stays empty.
    recovered_count = 0
    if recovered_inserts:
        recovered_count = db._apply_recovered_inserts(new_conn, recovered_inserts)
    # emit the history_corrupted event + tray notify.
    db._notify_corruption_recovered(corrupt_main, recovered_count)
    return new_conn


def _try_iterdump_recovery(db: HistoryDB, old_db_path: Path) -> list[str]:
    """attempt to recover INSERT statements from a
    corrupt DB via ``connection.iterdump()``.

    Opens the corrupt DB in read-only mode (``?mode=ro`` URI) so
    we can't compound the corruption by writing to the known-bad
    file. Iterates the dump and returns the list of
    ``INSERT INTO transcriptions ...`` statements.

    Schema statements (CREATE TABLE / CREATE INDEX), schema-meta
    rows, FTS5 shadow-table rows, and ``sqlite_sequence`` rows
    are filtered out — the fresh DB's ``init_schema`` recreates
    the schema, and replaying ``schema_meta`` would PRIMARY
    KEY-conflict with the version row ``init_schema`` writes.

    Returns an empty list if the corrupt DB file doesn't exist,
    can't be opened read-only, or ``iterdump()`` raises (severe
    corruption). The caller must handle the empty-list case by
    falling back to the rename + fresh-DB path (which is what
    ``_maybe_recover_from_corruption`` does).
    """
    # The filter regex lives on the history_db facade; read it through
    # that namespace at call time so a facade-level monkeypatch is
    # observed here too.
    from voice_typer.server import history_db as _hd

    insert_transcriptions_re = _hd._INSERT_TRANSCRIPTIONS_RE  # noqa: N806

    inserts: list[str] = []
    if not old_db_path.exists():
        log.warning(
            "[HISTORY_DB] iterdump recovery: corrupt DB file does not exist: %s",
            old_db_path,
        )
        return inserts
    # Build the read-only URI. ``Path.as_uri()`` URL-encodes
    # special chars (spaces, etc.) and produces a proper
    # ``file:///`` URI on both POSIX and Windows.
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
            # iterdump yields strings like:
            #   INSERT INTO "transcriptions" VALUES(1, 'text', ...);
            #   INSERT INTO "schema_meta" VALUES('version','3');
            #   INSERT INTO "transcriptions_fts" VALUES(...);
            # Keep only INSERT INTO transcriptions (user data).
            stripped = stmt.lstrip()
            if insert_transcriptions_re.match(stripped):
                inserts.append(stmt)
    except sqlite3.Error as e:
        # Severe corruption: iterdump raised mid-iteration.
        # Return whatever we have so far (may be partial) rather
        # than discarding everything — partial recovery is
        # strictly better than no recovery.
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


def _apply_recovered_inserts(
    db: HistoryDB,
    conn: sqlite3.Connection,
    inserts: list[str],
) -> int:
    """replay iterdump-recovered INSERT statements on
    the fresh DB.

    The fresh DB's schema is not yet set up at this point
    (``init_schema``'s recursive ``_is_recovery=True`` call runs
    AFTER this method returns), so we run ``init_schema``
    ourselves first. The later recursive call is a no-op because
    all CREATE statements use ``IF NOT EXISTS`` and
    ``schema_meta`` already has ``version=_CURRENT_SCHEMA_VERSION``.

    The INSERTs are applied via ``executescript`` so a single bad
    statement (e.g. a row that violates a constraint) doesn't
    roll back all the others — partial recovery is preferable to
    no recovery for user dictation history.

    Returns the actual number of rows in the ``transcriptions``
    table after the attempt (may be less than ``len(inserts)``
    if some statements failed).
    """
    # Set up the schema on the fresh connection so the INSERTs
    # can target the transcriptions table. Wrapped broadly
    # because init_schema may interact with self._init_error /
    # _backup_before_migration in ways we don't want to crash on
    # during best-effort recovery.
    try:
        from voice_typer.server.history_db_internals.schema import (
            init_schema as _init_schema,
        )

        _init_schema(db, conn, _is_recovery=True)
    except Exception as e:  # noqa: BLE001 — best-effort recovery
        log.warning(
            "[HISTORY_DB] iterdump recovery: could not initialize schema for replay (skipping %d INSERTs): %s",
            len(inserts),
            e,
        )
        return 0
    # Apply the INSERTs. ``executescript`` issues a COMMIT first
    # (clearing any pending transaction from init_schema), then
    # runs each statement. The FTS5 AFTER-INSERT trigger fires
    # for each row and populates ``transcriptions_fts``
    # automatically, so we don't need to replay FTS rows.
    try:
        script = "\n".join(inserts)
        conn.executescript(script)
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] iterdump recovery: executescript failed (partial recovery may have occurred): %s",
            e,
        )
    # Count actual rows in the fresh DB. This is more accurate
    # than ``len(inserts)`` because some INSERTs may have failed
    # (e.g. constraint violations on duplicate ids).
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
            "[HISTORY_DB] iterdump recovery: %d rows recovered into fresh DB",
            count,
        )
    else:
        log.info(
            "[HISTORY_DB] iterdump recovery: no rows recovered (all INSERTs failed or empty source)",
        )
    return count


def _notify_corruption_recovered(
    db: HistoryDB,
    corrupt_main: Path,
    recovered_count: int,
) -> None:
    """surface the corruption event to the user.

    Logs a WARNING-level message naming the backup file's
    location and the number of rows recovered, then publishes a
    ``history_corrupted`` event via ``event_bus`` so the renderer
    can show a toast/notification. If ``db._app.tray.notify``
    is wired (set by the app shell), also calls it for a native
    OS notification.

    All notifications are best-effort: if ``event_bus.publish``
    or ``tray.notify`` raises, the recovery path must still
    succeed (the fresh DB has already been created and populated).
    """
    log.warning(
        "[HISTORY_DB] History database was corrupted and has been backed up to %s. Recovered %d rows via iterdump.",
        corrupt_main,
        recovered_count,
    )
    # Best-effort event_bus publication. Wrapped broadly because
    # the event_bus import or the publish call may fail (e.g.
    # circular import during early init, or a malformed event
    # payload); none of those should crash the recovery path.
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
    # Best-effort tray notification. ``db._app`` is set by the
    # app shell (not by HistoryDB.__init__) — use getattr so the
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
            f"History database was corrupted and backed up. {recovered_count} rows recovered.",
        )
    except Exception as e:  # noqa: BLE001 — best-effort notification
        log.warning(
            "[HISTORY_DB] tray.notify failed (best-effort, recovery continues): %s",
            e,
        )


def _maybe_migrate_legacy_db(config_dir: Path) -> None:
    """O2: move a legacy root-located ``history.db`` into ``db/`` once.

    Before O2 the history DB lived at ``<config_dir>/history.db`` (with
    ``-wal`` / ``-shm`` sidecars). O2 moves it under
    ``<config_dir>/db/`` alongside the other data subdirs (``logs/``,
    ``crashes/``, …). This is a best-effort, idempotent, atomic-ish
    relocation that runs on the first default-constructed ``HistoryDB``
    after the upgrade:

    * Only fires when the legacy root file exists AND the new
      ``db/history.db`` does not — the app never clobbers a newer file.
    * ``os.replace`` is atomic on the same filesystem (both paths are
      under the same config dir), so a crash mid-move cannot leave a
      truncated DB.
    * The ``-wal`` / ``-shm`` sidecars are moved first (the writer
      must not open the main file while its WAL still points at the old
      root location), then the main file. Any failure is logged and
      swallowed — a stuck legacy file (e.g. antivirus lock on Windows)
      falls back to opening the legacy path's replacement at ``db/``
      next launch; the app never crashes on migration failure.
    """
    # The ``db`` subdir name lives on the history_db facade; read it
    # through that namespace at call time.
    from voice_typer.server import history_db as _hd

    db_subdir = _hd.DB_SUBDIR  # noqa: N806

    if not config_dir.is_dir():
        return
    legacy = config_dir / "history.db"
    if not legacy.exists():
        return
    db_dir = config_dir / db_subdir
    db_dir.mkdir(parents=True, exist_ok=True)
    target = db_dir / "history.db"
    if target.exists():
        # New location already populated — nothing to migrate. Leave the
        # stale legacy file alone (a later purge / GDPR walk removes it).
        return
    # Move sidecars first, then the main file. os.replace is atomic on
    # the same filesystem (both live under config_dir).
    for suffix in ("-wal", "-shm"):
        _maybe_move_legacy_sidecar(config_dir, db_dir, suffix)
    try:
        os.replace(legacy, target)
        log.info("[HISTORY_DB] Migrated legacy history.db to db/history.db (O2)")
    except OSError as e:
        log.warning(
            "[HISTORY_DB] Could not migrate legacy history.db to db/ (O2): %s",
            e,
        )


def _maybe_move_legacy_sidecar(config_dir: Path, db_dir: Path, suffix: str) -> None:
    """Best-effort move of a legacy ``history.db<suffix>`` sidecar into ``db/``."""
    src = config_dir / f"history.db{suffix}"
    dst = db_dir / f"history.db{suffix}"
    if not src.exists():
        return
    if dst.exists():
        return
    try:
        os.replace(src, dst)
    except OSError as e:
        log.warning(
            "[HISTORY_DB] Could not migrate legacy history.db%s to db/ (O2): %s",
            suffix,
            e,
        )
