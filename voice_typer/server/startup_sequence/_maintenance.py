"""Startup maintenance sweeps extracted from ``startup_sequence``.

Owns the stale-file housekeeping that runs once per process startup
(phase 2 of :class:`startup_sequence.StartupSequence`): the
corrupt-quarantine / pre-migration backup sweep and the atomic-write
``.tmp`` leftover sweep. Bodies are moved verbatim from the former
``startup_sequence.py`` monolith, behavior and log lines unchanged
(C-LOG-1).

Patch-target contract (C-ARCH-2): these functions live here, so tests
that patch them target
``voice_typer.server.startup_sequence._maintenance.X``. Production
callers (``startup_sequence._phases_late``: the phase-8 post-ready
dispatch) resolve the names through this module object at call time,
so such patches stay effective.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only (no runtime import, no new coupling): the registry is
    from voice_typer.server.thread_registry import ThreadRegistry

# pre-split ``voice_typer.server.startup_sequence`` logger (same
log = logging.getLogger("voice_typer.server.startup_sequence")

# Stale backup file retention: files older than this are swept at startup.
_BACKUP_RETENTION_MAX_AGE_SECONDS: float = 15 * 24 * 60 * 60.0

# Stale ``.tmp`` file retention: atomic-write temp files
_TMP_RETENTION_MAX_AGE_SECONDS: float = 5 * 60.0

# Glob patterns for corrupt-quarantine and pre-migration backup files that
_BACKUP_FILE_GLOBS: tuple[str, ...] = (
    "history.db.pre-migration-v*.bak",
    "history.db.corrupt-*",
    # O2: since history.db moved under db/, its corrupt-quarantine and
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
_TMP_SWEEP_SUBDIRS: tuple[str, ...] = ("crash_diagnostics",)

# Join budget ``shutdown_all()`` may spend on the post-ready sweep worker.
_SWEEP_JOIN_TIMEOUT_SECONDS: float = 1.0


def _sweep_stale_files_after_ready(
    config_dir: Path,
    thread_registry: ThreadRegistry | None = None,
) -> None:
    """Dispatch the stale backup/``.tmp`` sweeps on a fire-and-forget"""

    def _worker() -> None:
        with contextlib.suppress(Exception):
            _sweep_stale_backup_files(config_dir)

    try:
        registry = (
            thread_registry if thread_registry is not None and hasattr(thread_registry, "spawn_and_register") else None
        )
        if registry is not None:
            registry.spawn_and_register(
                "startup-post-ready-sweep",
                _worker,
                stop_event=None,
                daemon=True,
                join_timeout=_SWEEP_JOIN_TIMEOUT_SECONDS,
            )
        else:
            thread = threading.Thread(
                target=_worker,
                name="startup-post-ready-sweep",
                daemon=True,
            )
            thread.start()
    except Exception:  # pragma: no cover, thread-spawn failure tolerance
        log.debug("[STARTUP] could not spawn post-ready sweep thread", exc_info=True)


def _sweep_stale_backup_files(config_dir: Path) -> None:
    """Delete stale corrupt-quarantine and pre-migration backup files."""
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

    # Walks the top-level config_dir AND each configured subdir. A recent
    _sweep_stale_tmp_files(config_path, now)
    for subdir_name in _TMP_SWEEP_SUBDIRS:
        subdir = config_path / subdir_name
        try:
            if subdir.is_dir():
                _sweep_stale_tmp_files(subdir, now)
        except OSError as exc:  # pragma: no cover, per-subdir error tolerance
            log.debug(
                "[STARTUP] could not sweep .tmp files in subdir %s: %s",
                subdir_name,
                exc,
            )


def _sweep_stale_tmp_files(directory: Path, now: float) -> None:
    """Sweep ``*.tmp`` files older than ``_TMP_RETENTION_MAX_AGE_SECONDS``."""
    try:
        tmp_paths = list(directory.glob("*.tmp"))
    except OSError as exc:  # pragma: no cover, glob failure tolerance
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
