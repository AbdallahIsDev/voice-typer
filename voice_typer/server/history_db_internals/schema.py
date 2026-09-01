"""Schema initialization, migration, and write-connection helpers.

Extracted from the once-monolithic ``history_db.py`` ( split). The
functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (or specific
parameters) instead of ``self`` — they do not depend on instance state
beyond what is passed in.

Public re-exports (used by tests via ``history_db._MIGRATIONS`` /
``history_db._CURRENT_SCHEMA_VERSION``):

- ``_CURRENT_SCHEMA_VERSION``
- ``_MIGRATION_V2``, ``_MIGRATION_V3``, ``_MIGRATION_V4``
- ``_MIGRATIONS``

Free functions:

- :func:`open_write_conn` — opens + configures the writer's connection.
- :func:`check_wal_mode` — verifies WAL mode is actually enabled.
- :func:`init_schema` — runs CREATE TABLE, migrations, indexes, integrity
  check. Returns the connection to use (may be a fresh one if corruption
  was detected and the DB was recreated).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server.platform_utils import is_windows

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

#: Guards the one-time ``[HISTORY] History database initialized`` INFO
#: line per DB path. ``init_schema`` runs on every ``HistoryDB``
#: construction (writer thread) and again during corruption recovery;
#: without the guard the same line appeared twice within milliseconds,
#: cluttering the log with a duplicate.
_announced_db_paths: set[str] = set()

_CURRENT_SCHEMA_VERSION = 4

_MIGRATION_V2 = """
    ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;
    ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT '';
"""

# M-61: FTS5 full-text search index.
#
# Previously `search()` did a `WHERE text LIKE ?` table scan — O(n) on
# the full transcriptions table. For a user with thousands of history
# rows this is several hundred milliseconds per keystroke in the search
# box. The FTS5 virtual table brings this down to O(log n + match count)
# and gives proper tokenization (case-insensitive, Unicode-aware,
# prefix queries via `query*`).
#
# The migration is intentionally additive:
#   - CREATE VIRTUAL TABLE IF NOT EXISTS — safe to re-run on every
#     schema init (existing FTS table is left untouched).
#   - Triggers keep the FTS table in sync with INSERT/UPDATE/DELETE on
#     `transcriptions`. They are created with `IF NOT EXISTS` so the
#     migration is idempotent.
#   - The `INSERT INTO transcriptions_fts(rowid, text) SELECT id, text
#     FROM transcriptions` backfill is safe to re-run because the FTS
#     table is empty on the first migration (and a re-run after a
#     successful migration is a no-op: `transcriptions_fts` already
#     contains every rowid, so the reinsert just overwrites the same
#     row). The backfill is wrapped in its own transaction so a partial
#     failure (e.g. disk full) doesn't leave the FTS table half-populated
#     AND the schema_meta version bumped.
#
# the entire migration runs inside an explicit BEGIN / COMMIT.
# Previously each migration statement ran in its own implicit
# transaction (Python sqlite3 autocommit-off semantics), so a crash
# mid-migration could leave the schema half-migrated with the version
# number already bumped. The explicit transaction ensures the schema
# version is only persisted if every statement in the migration
# succeeded.
_MIGRATION_V3 = """
    BEGIN;
    CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5(
        text,
        content='transcriptions',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS transcriptions_ai_fts AFTER INSERT ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_ad_fts AFTER DELETE ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_au_fts AFTER UPDATE ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    INSERT INTO transcriptions_fts(rowid, text) SELECT id, text FROM transcriptions;
    COMMIT;
"""

# At-rest encryption of the dictated ``text`` column (design gate:
# docs/adr/XZ-R11-04-at-rest-encryption.md; cipher module:
# ``voice_typer/server/_text_crypto.py``).
#
# This migration is deliberately a PLAIN migration (no embedded
# ``BEGIN;``) so the migration runner wraps it in one transaction and —
# critically — runs its partial-prior-state reconciliation over it: the
# ``text_is_encrypted`` column is already part of the canonical CREATE
# TABLE above, so on a FRESH database the ALTER must be filtered out
# (otherwise "duplicate column name" aborts the migration), while on a
# database created before this feature it is the statement that adds the
# column. Trigger-bearing statements cannot carry their own
# ``BEGIN;…COMMIT;`` wrapper AND benefit from that filtering, which is
# why the runner's split/reassemble path (it joins the split fragments
# back into semantically identical SQL) is the right shape here.
#
# FTS5 guard design (the load-bearing part):
#
#   - INSERT path: rows are ALWAYS inserted with plaintext + flag 0, so
#     ``transcriptions_ai_fts`` indexes plaintext tokens; the writer then
#     UPDATEs the row to ciphertext + flag 1. The au_fts WHEN guard makes
#     that flag-flip UPDATE a no-op for FTS (the plaintext tokens stay in
#     the index — full-text search keeps working for encrypted rows, ADR
#     §6 decision: FTS shadow tables remain plaintext-tokenized).
#   - UPDATE guard is ``NEW.text_is_encrypted = 0 AND OLD.text_is_encrypted
#     = 0`` (not merely "flag unchanged"): a favorite-toggle UPDATE on an
#     encrypted row also has an unchanged flag, but OLD.text is ciphertext —
#     issuing the FTS5 'delete' command with tokens that don't match the
#     originally indexed tokens raises "database disk image is malformed"
#     (verified in-sandbox against SQLite 3.53). Real text edits on
#     plaintext rows still re-index normally.
#   - DELETE guard (``old.text_is_encrypted = 0``): the 'delete' command
#     for an encrypted row would present ciphertext tokens that were never
#     indexed — same corruption — so token removal is skipped for
#     encrypted rows. Stale rowids left in the index are harmless: every
#     FTS search SQL JOINs back against ``transcriptions``, which filters
#     dangling rowids out of the result set.
#
# All three triggers are DROPped + recreated (IF EXISTS on the drop) because
# a pre-v4 database carries the unguarded v3 definitions.
_MIGRATION_V4 = """
    ALTER TABLE transcriptions ADD COLUMN text_is_encrypted INTEGER DEFAULT 0;
    DROP TRIGGER IF EXISTS transcriptions_ai_fts;
    DROP TRIGGER IF EXISTS transcriptions_ad_fts;
    DROP TRIGGER IF EXISTS transcriptions_au_fts;
    CREATE TRIGGER transcriptions_ai_fts AFTER INSERT ON transcriptions
    WHEN new.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER transcriptions_ad_fts AFTER DELETE ON transcriptions
    WHEN old.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER transcriptions_au_fts AFTER UPDATE ON transcriptions
    WHEN NEW.text_is_encrypted = 0 AND OLD.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
"""

_MIGRATIONS = {
    2: _MIGRATION_V2,
    3: _MIGRATION_V3,
    4: _MIGRATION_V4,
}

#: Matches ``ALTER TABLE <name> ADD COLUMN <col>`` so the migration runner
#: can detect which columns a plain migration intends to add and skip the
#: ALTER when the column is already present (partial-prior-state
#: reconciliation). Compiled once at import time.
_ADD_COLUMN_RE = re.compile(
    r"\bALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN\s+(\w+)",
    re.IGNORECASE,
)


def _add_column_name(stmt: str) -> str | None:
    """Return the column added by an ``ALTER TABLE ... ADD COLUMN`` statement.

    Returns ``None`` for statements that are not ``ALTER TABLE ADD COLUMN``
    so non-ALTER statements in a plain migration (e.g. ``INSERT`` or
    ``CREATE``) are never filtered out by the migration runner's
    partial-prior-state reconciliation.
    """
    match = _ADD_COLUMN_RE.search(stmt)
    return match.group(1) if match else None


def open_write_conn(db_path: Path) -> sqlite3.Connection:
    """Open and configure the writer thread's connection.

        The writer owns the *only* write-capable connection in the
        process. Configuration:
          - ``journal_mode=WAL`` — concurrent readers don't block writes.
          - ``synchronous=NORMAL`` — safe in WAL mode, faster than FULL.
          - ``busy_timeout=5000`` — safety net for *external* writers
            (antivirus, external CLI). In-process contention is
            impossible because there's only one writer thread.
          - ``cache_size=-20000`` — 20 MB page cache.
    ``secure_delete=ON`` — : overwrite deleted rows
            with zeros so dictated text is not recoverable from free
            pages.

        SEC-007: on POSIX, tightens the DB file and its parent
        directory to 0o600 / 0o700 so transcription history is not
        world-readable. SQLite creates ``-wal`` and ``-shm`` sidecar
        files in WAL mode; we chmod those too (best-effort, since
        they may be created lazily on first write).
    """
    # Ensure the parent directory exists on EVERY platform before the
    # connection opens the file. SQLite cannot create intermediate
    # directories; on a fresh install ``<config>/db/`` does not exist yet
    # (the legacy-DB migration in history_db.py only creates it when a
    # pre-O2 root ``history.db`` exists), so skipping this mkdir on
    # Windows made every fresh-install open fail with "unable to open
    # database file" and the writer refuse all writes for the session.
    # ``mkdir(parents=True, exist_ok=True)`` is idempotent.
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("[HISTORY_DB] Could not create DB directory %s: %s", db_path.parent, e)
    # SEC-007: tighten dir permissions before the connection creates
    # files in it (POSIX only — Windows has no POSIX mode bits).
    if not is_windows():
        try:
            os.chmod(db_path.parent, 0o700)
        except OSError as e:
            log.warning("[HISTORY_DB] Could not tighten dir perms: %s", e)
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        timeout=5.0,
    )
    # Safety net for external contention only (in-process contention
    # is impossible — there's only one writer thread).
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-20000")  # 20 MB
    # secure_delete=ON overwrites deleted rows with zeros
    # before freeing the page, so dictated text is not recoverable
    # from free pages by an attacker with filesystem access. This
    # complements the GDPR delete path (which unlinks the DB file
    # entirely) by ensuring that in-place deletes (clear_all,
    # apply_retention, delete by id) don't leave plaintext in free
    # pages that could be carved out with a hex editor. Tradeoff:
    # deletes are slightly slower (extra I/O to zero the page).
    # Acceptable for transcription history where privacy outweighs
    # throughput. Note: this PRAGMA is database-persistent — once
    # set, it applies to all connections on this DB file.
    conn.execute("PRAGMA secure_delete=ON")
    conn.row_factory = sqlite3.Row
    # SEC-007: chmod the DB file (and sidecar files if present).
    if not is_windows():
        for suffix in ("", "-wal", "-shm"):
            p = db_path.with_suffix(db_path.suffix + suffix) if suffix else db_path
            try:
                if p.exists():
                    os.chmod(p, 0o600)
            except OSError:
                pass
    # opt into FK enforcement (XZ-R11-11). Per-connection PRAGMA —
    # must be set on every new connection (NOT database-persistent).
    # Wrapped in try/except so a read-only FS / locked DB doesn't
    # abort connection setup (the FK setting is a hardening extra,
    # not a correctness requirement for the current schema). The
    # current schema has no FK constraints so this is a no-op today,
    # but it is a latent footgun if FKs are added later — SQLite
    # defaults to ``foreign_keys=OFF`` for backward compat with
    # pre-2004 schemas, silently allowing orphaned child rows.
    # Readers don't need this (FK enforcement is write-path only).
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error as e:
        log.debug(
            "[HISTORY_DB] PRAGMA foreign_keys=ON failed (best-effort): %s",
            e,
        )
    return conn


def check_wal_mode(conn: sqlite3.Connection, db_path: Path) -> None:
    """Verify WAL mode is actually enabled.

        ``PRAGMA journal_mode=WAL`` returns the *resulting* journal
        mode. On network filesystems, certain antivirus locks, or
        read-only filesystems, SQLite may silently fall back to
        ``delete`` (rollback journal) mode. In rollback mode, readers
        DO block the writer and the user-reported 9s regression
        returns.

        This method fetches the PRAGMA result and logs a warning if
        WAL is not active. It does NOT crash — the app should still
        work (just slower) — but the warning must be visible so users
        can diagnose the misconfiguration.

    (privacy): after the PRAGMA runs (which may lazily create
        the ``-wal`` and ``-shm`` sidecar files), we re-run the chmod
        loop on the DB file and its sidecars so they get ``0o600`` on
        POSIX. Previously the chmod loop in ``open_write_conn`` ran
        BEFORE ``PRAGMA journal_mode=WAL`` actually created the sidecar
        files, so they inherited the process umask (typically ``0o644``
        = world-readable on multi-user Linux). The re-chmod here closes
        the race for the writer's connection.
    """
    try:
        cur = conn.execute("PRAGMA journal_mode=WAL")
        mode_row = cur.fetchone()
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] Could not set/check WAL mode (%s) at %s — "
            "app will work but writes may be slower and more contended.",
            e,
            db_path,
        )
        return
    mode = mode_row[0] if mode_row else ""
    if str(mode).lower() != "wal":
        log.warning(
            "[HISTORY_DB] WAL mode NOT enabled (got %r) at %s — "
            "app will work but writes may be slower and more contended.",
            mode,
            db_path,
        )
    # WAL mode was just set (or attempted). If it succeeded,
    # SQLite has now created the ``-wal`` and ``-shm`` sidecar files
    # on disk (they were NOT present when ``open_write_conn`` ran its
    # chmod loop because that runs BEFORE the PRAGMA). Re-run the
    # chmod loop here so the sidecars get 0o600 too — without this,
    # they inherit the process umask (typically 0o644 on Linux =
    # world-readable, exposing dictated text in the WAL to any local
    # user). Best-effort — chmod failures are logged at debug level.
    if not is_windows():
        for suffix in ("", "-wal", "-shm"):
            p = db_path.with_suffix(db_path.suffix + suffix) if suffix else db_path
            try:
                if p.exists():
                    os.chmod(p, 0o600)
            except OSError as chmod_exc:
                log.debug(
                    "[HISTORY_DB] chmod 0o600 on %s failed after WAL switch (best-effort): %s",
                    p,
                    chmod_exc,
                )


def init_schema(
    db: HistoryDB,
    conn: sqlite3.Connection,
    _is_recovery: bool = False,
) -> sqlite3.Connection:
    """Initialize the database schema and run migrations.

        IMPL-A: previously this method called ``self._get_conn()``;
        now it takes the writer's connection as a parameter so it can
        run on the writer thread.

    after each successful migration iteration, the
        schema version is persisted via ``INSERT OR REPLACE INTO
        schema_meta``. Previously the version was read but never
        written, so migrations re-ran on every launch (the V3 FTS5
        backfill re-scanned every row each startup).

    each migration is wrapped in an explicit
        ``BEGIN; … COMMIT;`` transaction (via ``executescript``). On
        ``sqlite3.Error``, the transaction is rolled back and
        ``db._init_error`` is set so the writer thread surfaces the
        failure to ``__init__`` and skips the main write loop. The
        per-statement try/except that previously swallowed errors
        (allowing a partial migration to leave the schema
        half-migrated) is removed — a partial migration now fails
        loudly and rolls back ALL changes (including DDL ALTERs,
        which SQLite would otherwise auto-commit between statements).

    at the end of a successful init, ``PRAGMA
        quick_check`` is run. If the result is anything other than
        ``("ok",)``, the corrupt DB is renamed to
        ``history.db.corrupt-<timestamp>`` and a fresh DB is created.
        The ``_is_recovery`` flag prevents infinite recursion if the
        fresh DB also fails the integrity check.

        FIX (preserved from prior version): schema/metadata BEFORE
        indexes that depend on migrated columns. The original code ran
        CREATE INDEX idx_favorite ON transcriptions(favorite) BEFORE
        the migration code. On an existing database created without
        the 'favorite' column, CREATE INDEX would fail with "no such
        column: favorite". Fix: create the table first, then run
        schema versioning + migrations, then create indexes.

        Returns the connection to use (may be a fresh one if
        corruption was detected and the DB was recreated). Callers
        must use the returned connection, not the one they passed in.
    """
    # (Medium): clear any stale ``_init_error`` from a prior
    # failed init_schema call so the writer thread doesn't permanently
    # bail out. Pre-fix, ``_init_error`` was set in 3 places (migration
    # failure at line 411, writer_loop init, corruption recovery) but
    # NEVER cleared to None. During corruption recovery,
    # ``_apply_recovered_inserts`` calls ``init_schema(_is_recovery=True)``.
    # If that fails (transient disk-full), ``_init_error=e`` is set and
    # the function returns ``conn`` (doesn't raise). A recursive
    # ``init_schema`` may SUCCEED on retry (disk-full was transient) but
    # ``_init_error`` remains set from the first call — the writer
    # thread checks ``if self._init_error is not None:`` and exits
    # without entering the write loop, leaving the user with "history
    # DB unavailable" for the rest of the session even though the
    # schema is fully set up. Clearing at the TOP of init_schema means
    # BOTH the initial call AND the recursive recovery call start with
    # a clean slate — a failure during this invocation re-sets it, a
    # success leaves it cleared.
    with contextlib.suppress(Exception):
        db._init_error = None

    cursor = conn.cursor()

    # ``init_schema`` has three exit
    # paths (migration failure, corruption-recovery recursion, normal
    # return) — each closes ``cursor`` before returning so no cursor
    # is leaked even when a fresh connection is substituted mid-init.

    # New DBs opt into
    # ``PRAGMA auto_vacuum=INCREMENTAL`` so subsequent
    # ``PRAGMA incremental_vacuum(N)`` calls (in ``apply_retention``
    # and ``clear_all``) can reclaim free pages incrementally —
    # without the exclusive lock and full file rewrite that
    # ``VACUUM`` requires. ``auto_vacuum`` can ONLY be set when the
    # schema is empty (no tables), so this is a no-op for existing
    # DBs (which keep the full-``VACUUM``-at-20% fallback path).
    # Detection: query ``sqlite_master`` for any user table — if
    # none exist, this is a fresh DB and the PRAGMA takes effect.
    try:
        has_tables = cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        has_tables = True  # be conservative — don't touch auto_vacuum
    if not has_tables:
        try:
            cursor.execute("PRAGMA auto_vacuum=INCREMENTAL")
            log.info(
                "[HISTORY_DB] New DB: set PRAGMA auto_vacuum=INCREMENTAL "
                "(enables fast incremental_vacuum reclamation in "
                "apply_retention / clear_all)"
            )
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] Could not set auto_vacuum=INCREMENTAL on "
                "new DB (%s) — falling back to full-VACUUM reclamation",
                e,
            )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            duration REAL DEFAULT 0,
            model TEXT DEFAULT '',
            device TEXT DEFAULT '',
            word_count INTEGER DEFAULT 0,
            char_count INTEGER DEFAULT 0,
            favorite INTEGER DEFAULT 0,
            language TEXT DEFAULT '',
            text_is_encrypted BOOLEAN DEFAULT 0
        )
    """)

    # Schema version tracking (must run BEFORE CREATE INDEX that
    # references 'favorite').
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Get current schema version
    cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
    row = cursor.fetchone()
    current_version = int(row[0]) if row else 1

    # run each migration in an explicit
    # ``BEGIN; … COMMIT;`` transaction via ``executescript``.
    # ``executescript`` is used for BOTH migration shapes:
    #
    # 1. Trigger-bearing migrations (e.g. _MIGRATION_V3 with
    #    ``CREATE TRIGGER ... BEGIN ... END;``) carry their own
    #    ``BEGIN;…COMMIT;`` and CANNOT be naively split on ``;``
    #    (the inner statement terminators inside BEGIN/END would
    #    be misinterpreted as end-of-statement).
    #
    # 2. Plain ALTER/CREATE migrations (e.g. _MIGRATION_V2) are
    #    wrapped in ``BEGIN;…COMMIT;`` so the whole migration is
    #    atomic. Without the wrapper, SQLite's DDL auto-commit
    #    behavior would persist each ALTER individually — a
    #    mid-migration failure would leave the schema
    #    half-migrated with no way to roll back the already-
    #    committed ALTERs.
    #
    # On ``sqlite3.Error``: rollback the transaction, set
    # ``_init_error``, and return early. The version is NOT
    # bumped — the next launch retries from the pre-migration
    # version. The per-statement try/except that previously
    # swallowed errors () is removed because it allowed
    # partial migrations to silently corrupt the schema.
    # Best-effort pre-migration backup. If a future migration
    # (v4+) has a logic bug that silently corrupts rows rather than
    # failing loudly, the corrupt-file rename () would NOT
    # trigger (PRAGMA quick_check passes on a structurally-valid but
    # semantically-wrong DB). The pre-migration backup gives the user
    # a recovery path: ``history.db.pre-migration-v<from>.bak`` is a
    # byte-for-byte copy of the DB at the OLD schema version, taken
    # BEFORE any migration statement runs. Single-slot naming means
    # re-running migrations on an already-migrated DB (where
    # ``current_version == _CURRENT_SCHEMA_VERSION``) is a no-op —
    # the backup step is skipped (no migration to back up).
    if current_version < _CURRENT_SCHEMA_VERSION:
        db._backup_before_migration(current_version)

    for version in range(current_version + 1, _CURRENT_SCHEMA_VERSION + 1):
        migration_sql = _MIGRATIONS.get(version)
        if not migration_sql:
            continue

        try:
            # Migrations split into two shapes:
            #
            # 1. Plain migrations (no embedded ``BEGIN;``) such as
            #    _MIGRATION_V2 — a sequence of ``ALTER TABLE ADD
            #    COLUMN`` statements. These need partial-prior-state
            #    reconciliation: a previous run may have added SOME of
            #    the columns but failed before the version was bumped
            #    (disk full, process killed mid-migration). Re-running
            #    the whole migration verbatim would hit "duplicate
            #    column name" on the already-added columns and — under
            #    the previous handler — bump the version unconditionally,
            #    leaving the NOT-yet-added columns missing forever.
            #
            #    Fix: pre-compute the existing columns, filter out
            #    ``ALTER TABLE ADD COLUMN`` statements whose column
            #    already exists (the intent is satisfied), and run the
            #    remaining statements in a single ``BEGIN;…COMMIT;``
            #    transaction via ``executescript``. The version is only
            #    bumped when ALL remaining statements succeed; a
            #    non-duplicate error rolls back and leaves the version
            #    un-bumped so the next launch retries the missing ALTERs.
            #
            # 2. Migrations carrying their own ``BEGIN;…COMMIT;`` (e.g.
            #    _MIGRATION_V3 with triggers) — passed through unchanged.
            #    V3 uses ``IF NOT EXISTS`` for all CREATE statements and
            #    an idempotent backfill, so re-running on a
            #    partial-prior state is already safe.
            needs_wrapper = "BEGIN;" not in migration_sql.upper()
            if needs_wrapper:
                cursor.execute("PRAGMA table_info(transcriptions)")
                pre_existing_cols = {row[1] for row in cursor.fetchall()}
                statements = [s.strip() for s in migration_sql.split(";") if s.strip()]
                kept: list[str] = []
                for stmt in statements:
                    col = _add_column_name(stmt)
                    if col is not None and col in pre_existing_cols:
                        log.info(
                            "[HISTORY_DB] Migration v%d: column %r "
                            "already exists — skipping ALTER "
                            "(partial-prior-state reconciliation)",
                            version,
                            col,
                        )
                        continue
                    kept.append(stmt)
                if kept:
                    wrapped_sql = "BEGIN;\n" + ";\n".join(kept) + ";\nCOMMIT;\n"
                    cursor.executescript(wrapped_sql)
                else:
                    log.info(
                        "[HISTORY_DB] Migration v%d: all statements "
                        "already applied — persisting version without "
                        "re-running any statement",
                        version,
                    )
            else:
                cursor.executescript(migration_sql)
            # persist the version after each successful
            # migration iteration so the next launch doesn't
            # re-run it. ``INSERT OR REPLACE`` handles both the
            # initial insert and subsequent updates.
            cursor.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
                (str(version),),
            )
            conn.commit()
            log.info(
                "[HISTORY_DB] Migrated schema to version %d (transactional, version persisted)",
                version,
            )
        except sqlite3.Error as e:
            # rollback any partial migration. The version is NOT
            # bumped — the next launch retries. Surface the error to
            # ``__init__`` via ``_init_error`` so the writer thread
            # skips the main write loop.
            #
            # The previous "duplicate column name" special case is no
            # longer needed: plain migrations now pre-filter ALTER TABLE
            # ADD COLUMN statements whose column already exists, so a
            # partial-prior state is reconciled rather than aborting the
            # whole migration. A "duplicate column name" error reaching
            # here means a concurrent writer added the column between
            # our PRAGMA and our ALTER (a race) — rolling back and
            # retrying on the next launch is the correct response.
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            log.error(
                "[HISTORY_DB] Migration v%d failed: %s (version NOT bumped; transaction rolled back; _init_error set)",
                version,
                e,
            )
            db._init_error = e
            with contextlib.suppress(Exception):
                cursor.close()
            return conn

    # Create indexes AFTER migration so 'favorite' column exists.
    # refresh existing_columns post-migration and guard
    # idx_favorite creation so a rolled-back migration (which
    # returns early above) doesn't crash the whole init. The
    # index on timestamp is safe to create unconditionally —
    # 'timestamp' is in the original CREATE TABLE.
    cursor.execute("PRAGMA table_info(transcriptions)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp
        ON transcriptions(timestamp DESC)
    """)
    # Composite covering index for the (timestamp DESC, id DESC) ordering
    # used by get_recent / search / get_favorites. The single-column
    # ``idx_timestamp`` cannot satisfy the secondary ``id DESC`` tie-break
    # without a sort pass; on a 500K-row DB this pushed the OFFSET
    # pagination path to ~594ms because SQLite still had to sort the
    # tie-group per timestamp value. The composite index makes both the
    # ORDER BY and the keyset WHERE clause ``timestamp < ? OR
    # (timestamp = ? AND id < ?)`` index-served (O(log N) per page).
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp_id
        ON transcriptions(timestamp DESC, id DESC)
    """)
    if "favorite" in existing_columns:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorite
            ON transcriptions(favorite)
        """)
        # composite index on (favorite, timestamp ASC) —
        # serves the retention DELETE subquery at apply_retention.
        # ``CREATE INDEX IF NOT EXISTS`` is idempotent, so this
        # serves as BOTH new-DB creation AND migration for existing
        # databases (existing DBs re-run _init_db_schema on every
        # launch). Guarded by the same ``existing_columns`` check
        # as ``idx_favorite`` because the index references the
        # ``favorite`` column.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorite_timestamp
            ON transcriptions(favorite, timestamp ASC)
        """)
    else:
        log.warning(
            "[HISTORY_DB] Skipping idx_favorite creation: 'favorite' "
            "column missing (migration was rolled back or not yet "
            "applied). Next launch will retry.",
        )

    # integrity check at the end of schema init. Skip
    # on recovery to prevent infinite recursion if the fresh DB
    # also fails the check (in which case _init_error is set on
    # the second failure and the writer exits).
    if not _is_recovery:
        new_conn = db._maybe_recover_from_corruption(conn)
        if new_conn is not None:
            # Corruption detected and a fresh DB was created.
            # Re-run schema init on the fresh connection.
            with contextlib.suppress(Exception):
                cursor.close()
            return init_schema(db, new_conn, _is_recovery=True)

    key = str(db.db_path)
    if key not in _announced_db_paths:
        # Emit the one-time INFO so the log stays clean when both the
        # initial construction and a corruption-recovery re-init run.
        _announced_db_paths.add(key)
        log.info(
            "[HISTORY] History database initialized: %s (schema v%d)",
            db.db_path,
            _CURRENT_SCHEMA_VERSION,
        )
    else:
        log.debug(
            "[HISTORY] History database initialized: %s (schema v%d, repeat)",
            db.db_path,
            _CURRENT_SCHEMA_VERSION,
        )
    with contextlib.suppress(Exception):
        cursor.close()
    return conn
