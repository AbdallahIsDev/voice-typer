"""Public top-level accessors for the ``config`` package."""

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

if TYPE_CHECKING:  # pragma: no cover, typing-only, never imported at runtime
    pass

log = logging.getLogger("voice_typer.server.config")


def _legacy_voice_typer_dir() -> Path:
    """canonical ``~/.lausu`` path used by the legacy migration"""
    return Path.home() / ".lausu"


def _prune_kept_backups(directory: Path, *, prefix: str, keep: int) -> None:
    """prune oldest backup files in ``directory`` whose name"""
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
            continue


def purge_user_data(*, remove_config_dir: bool = False) -> dict[str, list[str]]:
    """remove all user-data files / subdirs created by Lausu."""
    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    # ``_config_dir`` is re-exported by ``config/__init__.py`` and is
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
                or name.startswith(f"{_RECOVERY_FILENAME}.corrupt")
                # Pre-migration installs quarantined the corrupt file
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
    Lausu.app`` helper). The function is idempotent, missing
    files / dirs are silently skipped, and NEVER raises: an uninstall
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
        entire ``_config_dir()``: which INCLUDES the canonical HF
        cache subdir (``<config_dir>/huggingface`` per
        :func:`voice_typer.server._paths.hf_cache_dir`). The explicit
        HF-cache deletion below is a belt-and-suspenders pass that
        also covers the LEGACY cache path
        (``~/.lausu/huggingface``: see
        :func:`voice_typer.server._paths.legacy_hf_cache_dir`) used as
        a defensive fallback when ``_config_dir()`` itself raises
        (e.g. the BootTrigger scenario where ``$HOME`` is unset). On
        a normal install the explicit pass is a no-op because
        ``purge_user_data`` already unlinked the parent dir.

    Returns
    -------
    dict
        ``{"deleted": [...], "failed": [...]}``: ``deleted`` lists
        every file / dir path that was successfully removed (a
        superset of :func:`purge_user_data`'s ``removed`` list,
        plus the HF cache dir when ``remove_models=True``);
        ``failed`` lists ``"<path>: <error>"`` strings for entries
        that existed but could not be removed (permission errors,
        locked files, etc.). The function NEVER raises, failures are
        surfaced in ``failed`` so the uninstaller can log a report
        without aborting.

    The function does NOT remove OS keychain entries (call
    ``credential_store.delete_all_secrets()`` separately for an
    irreversible secret purge) or autostart entries (call
    ``autostart_launcher.disable_autostart()`` separately).
    """
    # 1. Wipe the entire config directory via the existing entry point.
    base_result = purge_user_data(remove_config_dir=True)
    deleted: list[str] = list(base_result.get("removed", []))
    failed: list[str] = list(base_result.get("errors", []))

    # 2. Optionally also delete the HuggingFace model cache directory.
    if remove_models:
        cache_paths: list[Path] = []
        # Canonical path: ``<config_dir>/huggingface``. Resolved via
        try:
            from voice_typer.server._paths import (
                hf_cache_dir,
                legacy_hf_cache_dir,
            )

            cache_paths.append(hf_cache_dir())
            cache_paths.append(legacy_hf_cache_dir())
        except Exception as exc:
            # ``_config_dir()`` itself may raise in the BootTrigger
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
