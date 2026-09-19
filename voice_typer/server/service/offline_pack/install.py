"""Local pack install pipeline: extract → verify → atomic swap (§8.3)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from .core import (
    OFFLINE_PACK_MAX_PER_FILE_BYTES,
    OfflinePackManifest,
    _safe_pack_member_name,
    _verify_manifest_files,
    offline_pack_dir_for_version,
)
from .events import _publish_event

log = logging.getLogger(__name__)


def atomic_swap_offline_pack(
    new_dir: Path,
    current_dir: Path,
    *,
    stop_worker: Callable[[], None] | None = None,
    start_worker: Callable[[], None] | None = None,
) -> Path | None:
    """Atomically swap ``new_dir`` → ``current_dir`` (§8.3).

    Returns the ``.trash`` path (so tests can verify cleanup). On POSIX
    """
    trash = Path(str(current_dir) + ".trash")
    # Pre-clean stale trash from a previous failed swap.
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    if platform.system() == "Windows":
        if stop_worker is not None:
            stop_worker()
        # Step 1: move current → trash (Windows: worker must be stopped
        if current_dir.exists():
            try:
                os.replace(current_dir, trash)
            except OSError as exc:
                log.exception("[PACK] Windows swap: rename current -> trash failed: %s", exc)
                if start_worker is not None:
                    start_worker()
                raise
        # Step 2: move new → current.
        try:
            os.replace(new_dir, current_dir)
        except OSError as exc:
            log.exception("[PACK] Windows swap: rename new -> current failed: %s", exc)
            # Roll back: restore the trash as the current pack.
            with contextlib.suppress(OSError):
                os.replace(trash, current_dir)
            if start_worker is not None:
                start_worker()
            raise
        if start_worker is not None:
            start_worker()
        # Best-effort trash cleanup, don't fail if it can't be deleted
        shutil.rmtree(trash, ignore_errors=True)
        return trash
    # POSIX, trash-then-rename. The worker keeps running on the old
    if current_dir.exists():
        os.replace(current_dir, trash)
    try:
        os.replace(new_dir, current_dir)
    except OSError as exc:
        # Mirror the Windows rollback: without the restore, a failed
        log.exception(
            "[PACK] POSIX swap: rename new -> current failed: %s, restoring the previous pack from %s",
            exc,
            trash,
        )
        with contextlib.suppress(OSError):
            os.replace(trash, current_dir)
        raise
    # Best-effort: try to delete the trash. If the worker is still
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    return trash


def _extract_pack_archive(archive: Path, staging: Path) -> None:
    """Extract *archive* (``pack-<version>.zip``) into *staging*."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not _safe_pack_member_name(info.filename):
                raise zipfile.BadZipFile(f"unsafe archive member {info.filename!r}")
            if info.file_size > OFFLINE_PACK_MAX_PER_FILE_BYTES:
                raise zipfile.BadZipFile(f"archive member {info.filename!r} exceeds the per-file cap")
            target = staging / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def install_offline_pack(
    archive_path: Path,
    version: str,
    manifest: OfflinePackManifest,
    *,
    root: Path | None = None,
    event_bus: ModuleType | None = None,
) -> bool:
    """:func:`verify_offline_pack_or_skip` enforces on the installed"""
    archive = Path(archive_path)
    pack_dir = offline_pack_dir_for_version(version, root=root)
    staging = Path(str(pack_dir) + ".new")
    if not archive.exists():
        # The download reported success but wrote no archive (e.g. an
        log.warning("[PACK] install skipped: archive %s not found", archive)
        return False
    # Fresh staging dir, never merge with a previous attempt's files.
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        _extract_pack_archive(archive, staging)
    except (zipfile.BadZipFile, OSError):
        log.exception("[PACK] install failed while extracting %s (pack %s)", archive, version)
        shutil.rmtree(staging, ignore_errors=True)
        _publish_event(
            event_bus,
            "offline_pack_download_failed",
            {"version": version, "reason": "install_failed", "attempts": 1},
        )
        return False
    if not _verify_manifest_files(staging, manifest, version=version):
        log.error("[PACK] install verification failed for pack %s. NOT swapped in", version)
        shutil.rmtree(staging, ignore_errors=True)
        _publish_event(
            event_bus,
            "offline_pack_corrupt",
            {
                "version": version,
                "path": str(pack_dir),
                "reason": "install_verification_failed",
            },
        )
        return False
    # Manifest written AFTER file verification: the installed pack's
    (staging / "pack-manifest.json").write_text(json.dumps(dict(manifest)), encoding="utf-8")
    try:
        atomic_swap_offline_pack(staging, pack_dir)
    except OSError:
        log.exception("[PACK] install failed at the atomic swap for pack %s", version)
        shutil.rmtree(staging, ignore_errors=True)
        _publish_event(
            event_bus,
            "offline_pack_download_failed",
            {"version": version, "reason": "install_failed", "attempts": 1},
        )
        return False
    log.info("[PACK] installed pack %s at %s", version, pack_dir)
    # The archive is consumed, free the ~180 MB (mirrors the NSIS
    with contextlib.suppress(OSError):
        archive.unlink()
    _publish_event(
        event_bus,
        "offline_pack_verified",
        {"version": version, "sha256": manifest["sha256"]},
    )
    return True
