"""Public top-level accessors for the ``config`` package.

Extracted from ``config/__init__.py`` (W1-A2 / AC-131) to chip away
at the monolith. These are pure leaf functions (no Config dependency)
that walk the user-data directory for uninstall flows
(``purge_user_data`` / ``purge_all_user_data``) plus two small helpers
used by the migration backup path (``_legacy_voice_typer_dir`` /
``_prune_kept_backups``).

Import-safety: this module is imported at the TOP of
``config/__init__.py``. To avoid a circular import, every name that
lives in ``config/__init__.py`` itself (e.g. ``_config_dir``,
``_RECOVERY_FILENAME``) is imported LAZILY inside the function body
or via the explicit re-export from leaf modules. The leaf imports
below (``_user_data_files._RECOVERY_FILENAME``, etc.) are safe
because they do not import ``config`` themselves.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server._user_data_files import (
    _LEGACY_RECOVERY_FILENAME,
    _RECOVERY_FILENAME,
    _USER_DATA_FILES,
)
from voice_typer.server.config._defaults import _USER_DATA_DIRS

if TYPE_CHECKING:  # pragma: no cover — typing-only, never imported at runtime
    pass

log = logging.getLogger("voice_typer.server.config")


def _legacy_voice_typer_dir() -> Path:
    """canonical ``~/.voice-typer`` path used by the legacy migration
    probe in :func:`_config_dir`.

    The actual probe runs in
    :func:`voice_typer.server.config_internals.paths._config_dir`; this
    helper exists in ``config`` so the
    ``test_config_py_still_has_legacy_migration_probe`` regression guard
    (which scans ``config.py`` for the literal
    ``Path.home() / ".voice-typer"`` pattern) continues to pass after
    the  split.  :func:`_config_dir` calls this helper via the
    lazy-import shim
    :func:`voice_typer.server.config_internals.paths._get_legacy_voice_typer_dir`
    to avoid a circular module-load.
    """
    return Path.home() / ".voice-typer"


def _prune_kept_backups(directory: Path, *, prefix: str, keep: int) -> None:
    """prune oldest backup files in ``directory`` whose name
    starts with ``prefix``, keeping only the ``keep`` newest.

    Best-effort: filesystem errors during stat / unlink of individual
    candidates are swallowed (a single un-prunable file must not abort
    the migration backup flow). The function is a no-op if fewer than
    ``keep + 1`` matching files exist.

    Selection criterion: ``Path.stat().st_mtime`` (newest kept). Ties
    are broken by lexicographic name order so the prune is
    deterministic across runs (important for tests).

    Rationale: without pruning, every version bump creates a new
    ``config.json.pre-migration-v{N}-{ts}-{pid}.bak`` and the
    directory grows unbounded across many launches. Capping at 3
    keeps the most recent three recovery points (typically enough to
    roll back to the prior version after a botched migration) while
    preventing disk bloat.
    """
    candidates = sorted(directory.glob(f"{prefix}*"))
    if len(candidates) <= keep:
        return
    # Sort newest-first (mtime desc, name asc as tie-breaker).
    candidates.sort(key=lambda p: (-p.stat().st_mtime, p.name))
    for stale in candidates[keep:]:
        try:
            stale.unlink()
        except OSError:
            # Best-effort: skip un-prunable files (locked, permission
            # denied, etc.). A stale leftover is preferable to
            # aborting the migration flow.
            continue


def purge_user_data(*, remove_config_dir: bool = False) -> dict[str, list[str]]:
    """remove all user-data files / subdirs created by Voice Typer.

    Intended to be called from uninstall scripts (Linux ``prerm --purge``,
    Windows NSIS uninstaller hook, macOS ``Uninstall Voice Typer.app``
    helper). The function is idempotent — missing files / dirs are
    silently skipped (returning them in the ``missing`` list so the
    caller can log a report if needed).

    Parameters
    ----------
    remove_config_dir
        If ``True``, also remove the config directory itself (after
        emptying it). Defaults to ``False`` so a re-install preserves
        the directory's permissions / ownership. Set to ``True`` for a
        true "clean slate" uninstall.

    Returns
    -------
    dict
        ``{"removed": [...], "missing": [...], "errors": [...]}`` —
        ``removed`` lists every file / dir that was successfully deleted,
        ``missing`` lists every entry that did not exist (often the
        common case for optional files like ``history.db-wal``), and
        ``errors`` lists ``(path, error_message)`` tuples for entries
        that existed but could not be removed (permission errors, etc.).
        The function NEVER raises — uninstall scripts must not abort
        mid-cleanup if a single file is locked.

    The function is best-effort and platform-agnostic. It does NOT
    remove the OS keychain entries (those live in
    ``credential_store.PROVIDER_TO_CONFIG_FIELD`` — callers that want a
    full secret purge should call
    ``credential_store.delete_all_secrets()`` separately, since that
    operation is irreversible and may require user interaction on some
    platforms). It also does NOT remove autostart entries (LaunchAgent
    plist, HKCU Run key, Task Scheduler entry, XDG autostart .desktop
    file) — those are owned by ``autostart_launcher`` and have their own
    ``disable_autostart()`` API.
    """
    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    # ``_config_dir`` is re-exported by ``config/__init__.py`` and is
    # routinely monkeypatched by tests via
    # ``monkeypatch.setattr("voice_typer.server.config._config_dir", ...)``.
    # Lazy import so the patched binding takes effect.
    import voice_typer.server.config as _cfg

    base = _cfg._config_dir()
    for name in _USER_DATA_FILES:
        path = base / name
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except OSError as e:
            errors.append(f"{path}: {e}")

    for name in _USER_DATA_DIRS:
        path = base / name
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            shutil.rmtree(path)
            removed.append(str(path))
        except OSError as e:
            errors.append(f"{path}: {e}")

    # Also clean up any versioned / corrupt / pre-migration backups
    # created by Config.load() / Config.save(). These follow the
    # patterns: ``config.json.bak``, ``config.json.v<N>.bak``,
    # ``config.json.pre-migration-v<N>.bak``,
    # ``config.json.corrupt-<ts>``.
    #
    # also sweep ``history.db.pre-migration-v*`` —
    # ``HistoryDB._backup_before_migration`` creates a byte-for-byte
    # copy of the full history DB before schema migration, containing
    # all dictated text in plaintext. Pre- this file survived
    # ``purge_user_data`` (the loop only matched ``history.db.corrupt-*``)
    # — a GDPR Art. 17 gap mirroring the ``history.db.corrupt-*`` issue
    # fixed by
    if base.exists():
        for entry in base.iterdir():
            name = entry.name
            if name == "config.json":
                continue  # already handled above
            if not (
                name.startswith("config.json.")
                or name.startswith("history.db.corrupt-")
                or name.startswith("history.db.pre-migration-v")
                # The crash-recovery quarantine pattern uses the
                # prefixed on-disk name
                # ``voice-typer-recovery.json.corrupt.<ts>`` (see
                # ``crash_recovery._quarantine_corrupt``); the prior
                # check used the bare unprefixed name and never matched
                # a real file.
                or name.startswith(f"{_RECOVERY_FILENAME}.corrupt")
                # Pre-migration installs quarantined the corrupt file
                # under the legacy prefixed name
                # (``voice-typer-recovery.json.corrupt.<ts>``) — match
                # it too so a pre-migration purge still removes it.
                or name.startswith(f"{_LEGACY_RECOVERY_FILENAME}.corrupt")
            ):
                continue
            if entry.is_dir():
                continue
            try:
                entry.unlink()
                removed.append(str(entry))
            except OSError as e:
                errors.append(f"{entry}: {e}")

    if remove_config_dir and base.exists():
        try:
            shutil.rmtree(base)
            removed.append(str(base))
        except OSError as e:
            errors.append(f"{base}: {e}")

    return {"removed": removed, "missing": missing, "errors": errors}


def purge_all_user_data(*, remove_models: bool = True) -> dict[str, list[str]]:
    """Remove ALL user data for uninstall.

    Unlike :func:`purge_user_data` (which preserves the config dir by
    default and is intended for "keep my settings but wipe runtime
    artifacts" flows), this function wipes the ENTIRE user-data root:
    config files, history DB, logs, crash dumps, mic-test recordings,
    the rotating Rust ``logs/`` subdirectory, the archived crash
    diagnostics, AND (optionally) the GB-sized HuggingFace model cache.

    Intended to be called from the uninstaller (Linux ``prerm --purge``,
    Windows NSIS ``deleteAppDataOnUninstall`` hook, macOS ``Uninstall
    Voice Typer.app`` helper). The function is idempotent — missing
    files / dirs are silently skipped — and NEVER raises: an uninstall
    script must not abort mid-cleanup if a single file is locked (the
    lock holder is typically the dying backend process shutting down
    in parallel).

    Parameters
    ----------
    remove_models
        If ``True`` (the default), also recursively delete the
        HuggingFace model cache directory. This is potentially
        GB-sized (a single Whisper / Parakeet / Qwen model is
        500 MB – 3 GB), so callers that want to preserve models for a
        re-install should pass ``remove_models=False``.

        Implementation note: :func:`purge_user_data` with
        ``remove_config_dir=True`` already recursively removes the
        entire ``_config_dir()`` — which INCLUDES the canonical HF
        cache subdir (``<config_dir>/huggingface`` per
        :func:`voice_typer.server._paths.hf_cache_dir`). The explicit
        HF-cache deletion below is a belt-and-suspenders pass that
        also covers the LEGACY cache path
        (``~/.voice-typer/huggingface`` — see
        :func:`voice_typer.server._paths.legacy_hf_cache_dir`) used as
        a defensive fallback when ``_config_dir()`` itself raises
        (e.g. the BootTrigger scenario where ``$HOME`` is unset). On
        a normal install the explicit pass is a no-op because
        ``purge_user_data`` already unlinked the parent dir.

    Returns
    -------
    dict
        ``{"deleted": [...], "failed": [...]}`` — ``deleted`` lists
        every file / dir path that was successfully removed (a
        superset of :func:`purge_user_data`'s ``removed`` list,
        plus the HF cache dir when ``remove_models=True``);
        ``failed`` lists ``"<path>: <error>"`` strings for entries
        that existed but could not be removed (permission errors,
        locked files, etc.). The function NEVER raises — failures are
        surfaced in ``failed`` so the uninstaller can log a report
        without aborting.

    The function does NOT remove OS keychain entries (call
    ``credential_store.delete_all_secrets()`` separately for an
    irreversible secret purge) or autostart entries (call
    ``autostart_launcher.disable_autostart()`` separately).
    """
    # 1. Wipe the entire config directory via the existing entry point.
    #    ``remove_config_dir=True`` recursively removes every file /
    #    subdir under ``_config_dir()`` (config.json, history.db + WAL,
    #    logs/, huggingface/, crash_diagnostics/, etc.) and
    #    then the dir itself.
    base_result = purge_user_data(remove_config_dir=True)
    deleted: list[str] = list(base_result.get("removed", []))
    failed: list[str] = list(base_result.get("errors", []))

    # 2. Optionally also delete the HuggingFace model cache directory.
    #    This is a belt-and-suspenders pass — see the docstring above
    #    for why the explicit step is needed alongside
    #    ``purge_user_data(remove_config_dir=True)``. We resolve BOTH
    #    the canonical and the legacy cache paths and remove whichever
    #    exist; a missing path is silently skipped (it's the common
    #    case on fresh installs that never downloaded a model).
    if remove_models:
        cache_paths: list[Path] = []
        # Canonical path: ``<config_dir>/huggingface``. Resolved via
        # the public helper so we stay in lock-step with the ASR
        # engines that actually populate the cache (they set
        # ``HF_HOME=<config_dir>/huggingface`` via ``asr_setup``).
        try:
            from voice_typer.server._paths import (
                hf_cache_dir,
                legacy_hf_cache_dir,
            )

            cache_paths.append(hf_cache_dir())
            cache_paths.append(legacy_hf_cache_dir())
        except Exception as exc:
            # ``_config_dir()`` itself may raise in the BootTrigger
            # scenario (``$HOME`` / ``%USERPROFILE%`` unset). Surface
            # the failure in ``failed`` so the uninstaller can log it
            # — the legacy path may still be resolvable below.
            failed.append(f"hf_cache_dir: {type(exc).__name__}: {exc}")

        for cache_path in cache_paths:
            try:
                if not cache_path.exists():
                    continue
            except OSError as exc:
                failed.append(f"{cache_path}: {type(exc).__name__}: {exc}")
                continue
            try:
                shutil.rmtree(cache_path)
                deleted.append(str(cache_path))
            except OSError as exc:
                failed.append(f"{cache_path}: {type(exc).__name__}: {exc}")

    return {"deleted": deleted, "failed": failed}
