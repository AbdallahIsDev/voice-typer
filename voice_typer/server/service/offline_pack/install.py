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


# ── Atomic swap (§8.3) ──────────────────────────────────────────────────


def atomic_swap_offline_pack(
    new_dir: Path,
    current_dir: Path,
    *,
    stop_worker: Callable[[], None] | None = None,
    start_worker: Callable[[], None] | None = None,
) -> Path | None:
    """Atomically swap ``new_dir`` → ``current_dir`` (§8.3).

    Windows: stop worker → rename ``current_dir`` → ``current_dir.trash``
    → rename ``new_dir`` → ``current_dir`` → start worker → delete
    ``.trash``. The stop/start hooks are caller-provided because the
    worker lifecycle is owned by the worker IPC service. Without
    them, ``os.replace`` raises ``PermissionError`` when the
    destination worker exe is open.

    POSIX: the rename-over is atomic at the *directory* level only if
    the destination doesn't exist. We therefore trash the old directory
    first (``rename current → current.trash``), then rename ``new →
    current``. The worker keeps running on the old inode, the open
    file descriptor inside ``current.trash/`` stays valid until the
    worker exits. No stop/start hook is called.

    Returns the ``.trash`` path (so tests can verify cleanup). On POSIX
    the trash is left in place if the worker is still running on it;
    the caller schedules an async delete (typically on next swap or on
    worker exit).
    """
    trash = Path(str(current_dir) + ".trash")
    # Pre-clean stale trash from a previous failed swap.
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    if platform.system() == "Windows":
        if stop_worker is not None:
            stop_worker()
        # Step 1: move current → trash (Windows: worker must be stopped
        # so the open worker.exe handle is released).
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
        # (AV scan, etc). The next swap will retry.
        shutil.rmtree(trash, ignore_errors=True)
        return trash
    # POSIX, trash-then-rename. The worker keeps running on the old
    # inode (the open file descriptor inside ``current.trash/`` stays
    # valid until the worker exits).
    if current_dir.exists():
        os.replace(current_dir, trash)
    try:
        os.replace(new_dir, current_dir)
    except OSError as exc:
        # Mirror the Windows rollback: without the restore, a failed
        # second rename would leave the INSTALLED PACK MISSING (the
        # launch-time existence probe finds nothing until the next
        # install attempt recovers it).
        log.exception(
            "[PACK] POSIX swap: rename new -> current failed: %s, restoring the previous pack from %s",
            exc,
            trash,
        )
        with contextlib.suppress(OSError):
            os.replace(trash, current_dir)
        raise
    # Best-effort: try to delete the trash. If the worker is still
    # running on a file inside it, the delete fails (we leave it; the
    # caller can retry on worker exit). The ``missing_ok=True`` path
    # handles the case where the trash was already cleaned.
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    return trash


# ── Install stage (§8.3: extract → verify → manifest → swap) ─────────────


def _extract_pack_archive(archive: Path, staging: Path) -> None:
    """Extract *archive* (``pack-<version>.zip``) into *staging*.

    Entries map DIRECTLY into the staging dir (no top-level folder —
    mirrors the release layout the NSIS installer unpacks). Every
    member name is checked against :func:`_safe_pack_member_name`
    (zip-slip defense) and every declared size against the per-file
    cap; a violation aborts the install before anything is swapped.

    Raises :class:`zipfile.BadZipFile` for a non-zip archive or a
    malicious entry, and ``OSError`` for disk failures mid-extract —
    the caller cleans the staging dir and keeps the archive for retry.
    """
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
    """Install a downloaded + SHA-verified pack archive (§8.3).

    Pipeline (all stages fail CLOSED, nothing is half-swapped):

    1. Extract ``pack-<version>.zip`` into ``<pack-root>/<version>.new/``
       (a fresh staging dir, stale leftovers from a failed install are
       discarded first).
    2. Verify every manifest-declared file in the staging dir
       (per-file SHA-256, the same fail-closed rules
       :func:`verify_offline_pack_or_skip` enforces on the installed
       pack).
    3. Write ``pack-manifest.json`` into the staging dir (the manifest
       is the caller's authoritative copy, already schema-validated
       when fetched remotely).
    4. :func:`atomic_swap_offline_pack` the staging dir into
       ``<pack-root>/<version>/``.
    5. Delete the consumed archive (frees ~180 MB, mirrors the NSIS
       full-offline installer) and publish ``offline_pack_verified``
       with ``{version, sha256}`` (the renderer's documented payload).

    On ANY failure the staging dir is removed and the archive is KEPT
    so the next trigger/launch retries (a resume request on the
    complete file short-circuits straight back here). Verification
    failures additionally publish ``offline_pack_corrupt``; extract /
    swap failures publish ``offline_pack_download_failed`` with reason
    ``install_failed``.

    The runtime-pack worker start step is intentionally NOT part of the
    install: the pack is installed, verified, and swapped into place —
    starting the worker process is the host-side wiring's decision and
    a separate concern.
    """
    archive = Path(archive_path)
    pack_dir = offline_pack_dir_for_version(version, root=root)
    staging = Path(str(pack_dir) + ".new")
    if not archive.exists():
        # The download reported success but wrote no archive (e.g. an
        # injected transport in tests), nothing to install.
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
    # existence probe (`offline_pack_exists`) only turns positive once
    # the files AND the manifest are all in place.
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
    # installer's post-extract Delete).
    with contextlib.suppress(OSError):
        archive.unlink()
    _publish_event(
        event_bus,
        "offline_pack_verified",
        {"version": version, "sha256": manifest["sha256"]},
    )
    return True
