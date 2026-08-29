"""Startup maintenance sweeps extracted from ``startup_sequence``.

Owns the stale-file housekeeping that runs once per process startup
(phase 2 of :class:`startup_sequence.StartupSequence`): the
corrupt-quarantine / pre-migration backup sweep and the atomic-write
``.tmp`` leftover sweep. Bodies are moved verbatim from the former
``startup_sequence.py`` monolith — behavior and log lines unchanged
(C-LOG-1).

Patch-target contract (C-ARCH-2): these functions live here, so tests
that patch them target
``voice_typer.server.startup_sequence._maintenance.X``. Production
callers (``startup_sequence._phases_early``) resolve the names through
this module object at call time, so such patches stay effective.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

# Explicit logger name (not ``__name__``): tests capture logs at the
# pre-split ``voice_typer.server.startup_sequence`` logger (same
# convention as ``app_lifecycle.py``), and this module's records must
# keep landing there after the split.
log = logging.getLogger("voice_typer.server.startup_sequence")

# Stale backup file retention: files older than this are swept at startup.
# 15 days (user preference 2026-08-20): short enough that corrupt-quarantine
# / pre-migration backups cannot accumulate across a month of crashes, long
# enough to preserve forensic value for a real corruption investigation.
# (The log-rotation and crash-diagnostics sweeps keep their own 30-day
# cadence — this constant only bounds the backup-file globs below.)
_BACKUP_RETENTION_MAX_AGE_SECONDS: float = 15 * 24 * 60 * 60.0

# Stale ``.tmp`` file retention: atomic-write temp files
# (``_secure_atomic_write`` in ``secure_file_io.py`` uses
# ``tempfile.mkstemp(..., suffix=".tmp")`` + ``os.replace``) are unlinked on
# success and on exception, but persist if the process is killed (SIGKILL /
# power loss / OOM-killer) between ``mkstemp`` and either branch. The
# 30-day backup retention is far too long for these — they have ZERO
# forensic value (they're mid-write intermediates) and accumulate
# indefinitely over many crashes. 5 minutes is long enough that a
# concurrent process mid-write (e.g. another app instance, or a
# long-running ``gdpr-export`` zip build) is NOT swept out from under it
# (the typical mkstemp→os.replace window is <1 s), and short enough that
# stale crash-leftover ``.tmp`` files don't accumulate across sessions.
_TMP_RETENTION_MAX_AGE_SECONDS: float = 5 * 60.0

# Glob patterns for corrupt-quarantine and pre-migration backup files that
# accumulate indefinitely without an automatic sweep. The GDPR purge function
# (service/privacy.py) only cleans these on explicit user action; this sweep
# runs at every startup to bound disk usage. Files newer than the retention
# period are preserved for forensic value.
_BACKUP_FILE_GLOBS: tuple[str, ...] = (
    "history.db.pre-migration-v*.bak",
    "history.db.corrupt-*",
    # O2: since history.db moved under db/, its corrupt-quarantine and
    # pre-migration backups are created there too. Both locations are
    # swept (the root patterns above cover pre-O2 installs that have
    # not yet run the one-time migration).
    "db/history.db.pre-migration-v*.bak",
    "db/history.db.corrupt-*",
    "config.json.corrupt-*",
    "config.json.pre-migration-v*.bak",
    "config.json.v*.bak",
    "config.json.bak.failed-migration-*",
    "voice-typer-recovery.json.corrupt.*",
    "recovery.json.corrupt.*",
)

# Subdirectories of ``config_dir`` that also accumulate ``.tmp`` files
# (atomic-write intermediates for crash-diagnostics archiving, GDPR export
# zips, etc.) and should be swept with the same 5-minute age gate as the
# top-level ``config_dir``. Each entry is a relative directory name; missing
# subdirs are silently skipped.
_TMP_SWEEP_SUBDIRS: tuple[str, ...] = ("crash_diagnostics",)


def _sweep_stale_backup_files(config_dir: Path) -> None:
    """Delete stale corrupt-quarantine and pre-migration backup files.

    Mirrors the pattern of ``_sweep_stale_log_rotations`` (log/__init__.py)
    and ``_sweep_stale_diagnostics`` (crash_handler/_diagnostics_archive.py).
    Files newer than ``_BACKUP_RETENTION_MAX_AGE_SECONDS`` are preserved for
    forensic value. Per-file errors are swallowed so one bad file never
    aborts the sweep.

    Also sweeps stale ``.tmp`` files left over by ``_secure_atomic_write``
    when the process was killed between ``mkstemp`` and ``os.replace`` /
    the ``except`` unlink. ``.tmp`` files have no forensic value (they're
    mid-write intermediates), so a much shorter ``_TMP_RETENTION_MAX_AGE_SECONDS``
    (5 min) age gate is used — long enough that a concurrent process
    mid-write (e.g. a long-running ``gdpr-export`` zip build, another
    app instance) is NOT swept out from under it, short enough
    that crash-leftover ``.tmp`` files don't accumulate across sessions.
    The ``.tmp`` sweep also walks ``_TMP_SWEEP_SUBDIRS`` (e.g.
    ``crash_diagnostics/``) which receive atomic writes from the
    crash-handler archive path and the GDPR-export zip builder.
    """
    if config_dir is None:
        return
    config_path = Path(config_dir)
    if not config_path.is_dir():
        return
    now = time.time()
    for pattern in _BACKUP_FILE_GLOBS:
        try:
            for file_path in config_path.glob(pattern):
                try:
                    if not file_path.is_file():
                        continue
                    age = now - file_path.stat().st_mtime
                    if age > _BACKUP_RETENTION_MAX_AGE_SECONDS:
                        file_path.unlink()
                        log.info(
                            "[STARTUP] swept stale backup file (age=%.0f days): %s",
                            age / 86400.0,
                            file_path.name,
                        )
                except OSError as exc:
                    log.debug(
                        "[STARTUP] could not sweep backup file %s: %s",
                        file_path.name,
                        exc,
                    )
        except OSError as exc:
            log.debug("[STARTUP] glob error for pattern %s: %s", pattern, exc)

    # ── Stale ``.tmp`` sweep (5-min age gate) ────────────────────────────
    # Walks the top-level config_dir AND each configured subdir. A recent
    # ``.tmp`` file might belong to a concurrent process mid-write
    # (another app instance, a long-running gdpr-export zip
    # build), so we only unlink files older than the 5-minute cutoff.
    _sweep_stale_tmp_files(config_path, now)
    for subdir_name in _TMP_SWEEP_SUBDIRS:
        subdir = config_path / subdir_name
        try:
            if subdir.is_dir():
                _sweep_stale_tmp_files(subdir, now)
        except OSError as exc:  # pragma: no cover — per-subdir error tolerance
            log.debug(
                "[STARTUP] could not sweep .tmp files in subdir %s: %s",
                subdir_name,
                exc,
            )


def _sweep_stale_tmp_files(directory: Path, now: float) -> None:
    """Sweep ``*.tmp`` files older than ``_TMP_RETENTION_MAX_AGE_SECONDS``.

    The 5-minute age gate is the key safety property: a ``.tmp`` file
    created seconds ago is left alone (a concurrent process might be
    mid-write), while one left over from a crash last session is purged.

    Per-file errors are swallowed (same pattern as the backup sweep) so
    one unreadable file never aborts the sweep.
    """
    try:
        tmp_paths = list(directory.glob("*.tmp"))
    except OSError as exc:  # pragma: no cover — glob failure tolerance
        log.debug("[STARTUP] glob error for *.tmp in %s: %s", directory, exc)
        return
    for file_path in tmp_paths:
        try:
            if not file_path.is_file():
                continue
            age = now - file_path.stat().st_mtime
            if age > _TMP_RETENTION_MAX_AGE_SECONDS:
                file_path.unlink()
                log.info(
                    "[STARTUP] swept stale .tmp file (age=%.0f s): %s",
                    age,
                    file_path.name,
                )
        except OSError as exc:
            log.debug(
                "[STARTUP] could not sweep .tmp file %s: %s",
                file_path.name,
                exc,
            )
