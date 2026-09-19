"""DB file-safety helpers: secure copy, legacy relocation, corruption recovery."""

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
    """symlink-safe, fsync-on-write binary file copy."""
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
    """Best-effort copy of the DB (and ``-wal``/``-shm``"""
    # Read the helper through the facade namespace at call time so a
    from voice_typer.server import history_db as _hd

    secure_copy = _hd._secure_copy_db_file  # noqa: N806

    try:
        bak_main = db.db_path.with_name(f"{db.db_path.name}.pre-migration-v{current_version}.bak")
        # copy the main DB file via the secure helper
        if db.db_path.exists():
            secure_copy(db.db_path, bak_main)
        # Copy the -wal and -shm sidecars if they exist (WAL mode).
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
        log.warning(
            "[HISTORY_DB] Pre-migration backup FAILED (continuing with migration anyway): %s",
            e,
        )


def _maybe_recover_from_corruption(
    db: HistoryDB,
    conn: sqlite3.Connection,
) -> sqlite3.Connection | None:
    """run ``PRAGMA quick_check``; if the result is"""
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as e:
        log.exception(
            "[HISTORY_DB] PRAGMA quick_check raised: %s (treating as corruption and attempting recovery)",
            e,
        )
        # Fall through to the recovery path, we can't verify
        rows = [("quick_check raised", str(e))]

    if len(rows) == 1 and rows[0][0] == "ok":
        return None  # healthy

    log.error(
        "[HISTORY_DB] Integrity check failed: %s. Renaming corrupt DB and creating a fresh one.",
        rows,
    )
    # Close the corrupt connection so we can rename the file.
    with contextlib.suppress(sqlite3.Error):
        conn.close()
    # invalidate all existing read connections BEFORE renaming.
    with db._connections_lock:
        for _ident, rconn in db._all_read_connections:
            with contextlib.suppress(sqlite3.Error):
                rconn.close()
        db._all_read_connections.clear()
        db._read_conn_generation += 1
    # Also clear the current thread's stale read conn (if any)
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
    recovered_inserts = db._try_iterdump_recovery(corrupt_main)
    # Open a fresh connection on a new (empty) DB file.
    try:
        new_conn = db._open_write_conn()
        db._check_wal_mode(new_conn)
    except sqlite3.Error as e:
        db._init_error = e
        # Even if the fresh DB can't be opened, still emit the
        db._notify_corruption_recovered(corrupt_main, 0)
        return None
    # replay the recovered INSERTs on the fresh DB.
    recovered_count = 0
    if recovered_inserts:
        recovered_count = db._apply_recovered_inserts(new_conn, recovered_inserts)
    # emit the history_corrupted event + tray notify.
    db._notify_corruption_recovered(corrupt_main, recovered_count)
    return new_conn


def _try_iterdump_recovery(db: HistoryDB, old_db_path: Path) -> list[str]:
    """attempt to recover INSERT statements from a"""
    # The filter regex lives on the history_db facade; read it through
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
            stripped = stmt.lstrip()
            if insert_transcriptions_re.match(stripped):
                inserts.append(stmt)
    except sqlite3.Error as e:
        # Severe corruption: iterdump raised mid-iteration.
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
    """replay iterdump-recovered INSERT statements on"""
    # Set up the schema on the fresh connection so the INSERTs
    try:
        from voice_typer.server.history_db_internals.schema import (
            init_schema as _init_schema,
        )

        _init_schema(db, conn, _is_recovery=True)
    except Exception as e:  # noqa: BLE001, best-effort recovery
        log.warning(
            "[HISTORY_DB] iterdump recovery: could not initialize schema for replay (skipping %d INSERTs): %s",
            len(inserts),
            e,
        )
        return 0
    # Apply the INSERTs. ``executescript`` issues a COMMIT first
    try:
        script = "\n".join(inserts)
        conn.executescript(script)
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] iterdump recovery: executescript failed (partial recovery may have occurred): %s",
            e,
        )
    # Count actual rows in the fresh DB. This is more accurate
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
    """surface the corruption event to the user."""
    log.warning(
        "[HISTORY_DB] History database was corrupted and has been backed up to %s. Recovered %d rows via iterdump.",
        corrupt_main,
        recovered_count,
    )
    # Best-effort event_bus publication. Wrapped broadly because
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
    except Exception as e:  # noqa: BLE001, best-effort notification
        log.warning(
            "[HISTORY_DB] event_bus.publish(history_corrupted) failed (best-effort, recovery continues): %s",
            e,
        )
    # Best-effort tray notification. ``db._app`` is set by the
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
    except Exception as e:  # noqa: BLE001, best-effort notification
        log.warning(
            "[HISTORY_DB] tray.notify failed (best-effort, recovery continues): %s",
            e,
        )


def _maybe_migrate_legacy_db(config_dir: Path) -> None:
    """O2: move a legacy root-located ``history.db`` into ``db/`` once."""
    # The ``db`` subdir name lives on the history_db facade; read it
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
        # New location already populated, nothing to migrate. Leave the
        return
    # Move sidecars first, then the main file. os.replace is atomic on
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
