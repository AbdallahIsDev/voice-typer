"""Version-migration helpers for the ``Config`` dataclass.

Extracted from ``config/__init__.py`` to chip away
at the monolith.

The forward-migration orchestrator (``_run_migrations``) and the
forward-backup helper (``_backup_before_migration``) already live in
``voice_typer.server.config_internals.migrations`` — the
``Config._run_migrations`` / ``Config._backup_before_migration``
classmethods are one-line delegators to those impl functions.

This module hosts the remaining inline-body migration method:
``_backup_before_downgrade_impl`` (the versioned-downgrade backup
path), called from ``Config.load`` when an older build loads a
newer-schema config.

Import-safety: this module is imported at the TOP of
``config/__init__.py``. Every name that lives in
``config/__init__.py`` itself (``_secure_read_text``,
``_secure_atomic_write``, ``_prune_kept_backups``, ``_CURRENT_SCHEMA_VERSION``,
``log``) is looked up lazily via ``import voice_typer.server.config
as _cfg`` inside the function body so monkeypatching on the
``config`` module namespace keeps taking effect.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only, never imported at runtime
    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")


def _backup_before_downgrade_impl(
    cls: type[Config],
    data: dict[str, Any],
    loaded_version: Any,
    config_file,
) -> None:
    """best-effort versioned backup when an older build loads a
    newer-version config.

    Called from :meth:`load` ONLY when ``loaded_version >
    _CURRENT_SCHEMA_VERSION`` (i.e. the user ran a newer build of
    Voice Typer and then downgraded). The in-memory ``data`` dict
    already has the higher-version fields filtered out by
    :meth:`_filter_unknown_keys`; without a backup, the next
    :meth:`save` would atomically overwrite the on-disk file with a
    config that has the higher version number but is missing the
    higher-version fields — silently destroying the user's data.

    This method copies the on-disk ``config.json`` (NOT the in-memory
    ``data`` — the on-disk bytes still have all the higher-version
    fields) to a timestamped ``config.json.v{loaded_version}-{ts}-{pid}-{ns}.bak``
    so two backup events never collide.

    previously the filename was single-slot
    ``config.json.v{loaded_version}.bak`` (no timestamp/PID) and
    ``_backup_before_downgrade`` was called UNCONDITIONALLY on every
    load meeting the version condition. After the first downgrade
    load (backup captures original high-version config), any
    ``Config.save()`` writes the degraded config (schema_version=N
    but MISSING all v{N} fields) to ``config.json``. On next
    restart, ``load()`` sees ``loaded_version=N > current``, calls
    ``_backup_before_downgrade`` AGAIN, reads the DEGRADED on-disk
    file, and overwrites ``config.json.v{N}.bak`` with degraded
    content — destroying the original v{N} fields. The fix mirrors
    ``_backup_before_migration``: embed timestamp + PID +
    sub-second nanoseconds in the filename and prune to keep=3 so
    the original high-version backup survives subsequent degraded
    loads.

    Also appends a non-blocking warning to ``data["_load_warnings"]``
    so the renderer can surface it via ``last_load_warnings`` — the
    user gets an honest signal that they ran an older build against
    a newer config and that a backup was created at a specific path.

    Best-effort: if the copy fails (read-only filesystem, out of
    disk, etc.) the warning is logged at WARNING level so the
    operator can investigate. The load itself is NOT aborted — the
    user can still use the app with the older build's known fields.
    """
    import voice_typer.server.config as _cfg

    if not isinstance(loaded_version, int):
        return
    # embed schema version + epoch seconds + PID +
    # sub-second nanoseconds in the filename so two backup events
    # never collide (even within the same second from different
    # processes — e.g. two app instances launched in parallel
    # against the same user account during a downgrade). Mirrors
    # ``_backup_before_migration`` at line 1879.
    ts_sec = int(time.time())
    pid = os.getpid()
    ts_ns = time.time_ns() % 1_000_000
    versioned_bak = config_file.parent / f"config.json.v{loaded_version}-{ts_sec}-{pid}-{ts_ns}.bak"
    # use the secure read/write helpers (O_NOFOLLOW + atomic
    # os.replace + fsync + 0o600) instead of ``shutil.copy2``.
    # ``shutil.copy2`` is (a) non-atomic (file-by-file copy — an
    # interrupted copy leaves a partial .bak that gives a false
    # sense of recoverability), (b) follows symlinks on both SOURCE
    # and DEST (a local attacker who replaces config.json with a
    # symlink to ~/.bashrc between the user's downgrade-launch and
    # the copy2 call gets ~/.bashrc content copied into the .bak —
    # info disclosure via the .bak file), (c) no fsync (the .bak
    # may not be durable across power loss). Mirrors the
    # fix prescribed for ``_backup_before_migration``.
    try:
        raw_text = _cfg._secure_read_text(config_file)
        _cfg._secure_atomic_write(versioned_bak, raw_text)
        log.warning(
            "[CONFIG] downgraded build loaded newer config schema_version=%d "
            "(supported=%d); backed up original to %s before any save can overwrite",
            loaded_version,
            _cfg._CURRENT_SCHEMA_VERSION,
            versioned_bak,
        )
        data.setdefault("_load_warnings", []).append(
            f"Config file schema_version={loaded_version} is newer than this build "
            f"supports ({_cfg._CURRENT_SCHEMA_VERSION}). Unknown fields were dropped from "
            f"the in-memory config. The original file was backed up to "
            f"{versioned_bak.name} before any save can overwrite it — restore this "
            f"file manually after upgrading to a newer build."
        )
    except (OSError, ValueError) as e:
        # OSError covers filesystem errors (read-only fs, out of
        # disk, permission denied); ValueError covers the
        # SEC-002 inode-changed-during-read guard (symlink TOCTOU
        # detection). Both are best-effort failures — the load
        # itself is NOT aborted.
        log.warning(
            "[CONFIG] failed to back up newer-version config to %s before downgrade save: %s",
            versioned_bak,
            e,
        )
        data.setdefault("_load_warnings", []).append(
            f"Config file schema_version={loaded_version} is newer than this build "
            f"supports ({_cfg._CURRENT_SCHEMA_VERSION}). Unknown fields were dropped. "
            f"WARNING: backup of the original file failed ({e}) — downgrading and "
            f"saving will irrecoverably lose the higher-version fields."
        )
        return
    # cap retained versioned-downgrade backups to 3
    # (oldest pruned) so the directory doesn't grow unbounded
    # across many version bumps + restart cycles. Mirrors the
    # ``_backup_before_migration`` prune call. The prefix
    # ``config.json.v`` matches BOTH the old single-slot
    # ``config.json.v<N>.bak`` (kept for backward-compat with
    # existing on-disk backups from pre- builds) AND the new
    # timestamped ``config.json.v<N>-<ts>-<pid>-<ns>.bak``.
    try:
        _cfg._prune_kept_backups(
            config_file.parent,
            prefix="config.json.v",
            keep=3,
        )
    except OSError as prune_exc:
        log.debug(
            "[CONFIG] failed to prune old versioned-downgrade backups: %s",
            prune_exc,
        )
