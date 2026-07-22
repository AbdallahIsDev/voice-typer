"""Secure atomic file I/O helpers for config persistence.

CR-28 (config.py split): this module was extracted from
``voice_typer.server.config`` to separate the low-level secure-file-I/O
concerns (atomic write, symlink-TOCTOU-safe read) from the higher-level
config dataclass / load / save logic.  The functions here are
re-exported from ``config.py`` so existing call sites -- including
``credential_store.migrate_secrets_to_keyring`` (function-level import)
and tests that monkeypatch ``voice_typer.server.config._secure_atomic_write``
-- keep working unchanged.
"""

import contextlib
import logging
import os
import tempfile

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger("voice_typer.server.config")


def _secure_atomic_write(path: os.PathLike, content: str) -> None:
    """Write content to ``path`` atomically and securely.

    G4-CR-01: the temp filename is now generated via
    ``tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
    suffix=".tmp")`` instead of the previous fixed name
    (``path.with_suffix(path.suffix + ".tmp")``).  The fixed name
    caused concurrent callers to collide on ``O_EXCL`` (EEXIST); the
    second caller's broad ``except`` then ``unlink()``-ed the FIRST
    caller's already-written temp file (silent data loss).

    G4-M-02: the Windows branch now uses the mkstemp-provided fd
    (which has O_EXCL semantics on Windows too) wrapped with
    ``os.fdopen`` instead of plain ``open()``.

    G4-M-01: on POSIX, after ``os.replace`` the parent directory is
    fsynced so the rename is durable across power loss.  Best-effort.
    """
    from pathlib import Path

    target = Path(path)
    parent = target.parent
    tmp_path = None
    tmp_fd = None
    try:
        # G4-CR-01: use a UNIQUE tmp name per call.  mkstemp returns
        # an open fd (with O_EXCL semantics) so we never collide with
        # a concurrent caller's tmp file.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(parent),
            prefix=target.name + ".",
            suffix=".tmp",
        )
        tmp_fd = fd
        tmp_path = Path(tmp_name)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            tmp_fd = None
            raise
        tmp_fd = None  # the with-block closed the fd

        # os.replace is atomic and does NOT follow symlinks on the target.
        os.replace(str(tmp_path), str(target))

        # G4-M-01: fsync the parent directory so the rename is durable.
        # POSIX-only -- Windows has no equivalent.  Best-effort.
        if not is_windows():
            try:
                dir_fd = os.open(str(parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    with contextlib.suppress(OSError):
                        os.close(dir_fd)
            except OSError as e:
                log.debug(
                    "[CONFIG] fsync of parent directory %s failed (best-effort): %s",
                    parent,
                    e,
                )
    except Exception:
        if tmp_fd is not None:
            with contextlib.suppress(OSError):
                os.close(tmp_fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


def _secure_read_text(path: os.PathLike, *, encoding: str = "utf-8") -> str:
    """SEC-002: Read text from a file securely, refusing to follow symlinks.

    On POSIX, opens the file with ``os.O_RDONLY | os.O_NOFOLLOW`` to
    prevent symlink-TOCTOU attacks. On Windows, checks for reparse
    points before reading.
    """
    from pathlib import Path

    p = Path(path)
    if not is_windows():
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            stat_before = os.fstat(fd)
            f = os.fdopen(fd, "r", encoding=encoding)
            try:
                content = f.read()
                stat_after = os.fstat(fd)
                if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                    raise ValueError(f"SEC-002: inode changed during read of {p} -- possible TOCTOU attack")
            finally:
                f.close()
            return content
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
    else:
        try:
            stat_result = os.lstat(str(p)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
            if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise OSError(f"SEC-002: refusing to follow reparse point: {p}")
        except (AttributeError, OSError):
            pass
        with open(p, encoding=encoding) as f:
            stat_before = os.fstat(f.fileno())
            content = f.read()
            stat_after = os.fstat(f.fileno())
            if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                raise ValueError(f"SEC-002: inode changed during read of {p} -- possible TOCTOU attack")
            return content
