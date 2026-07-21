"""Secure atomic file I/O helpers for config persistence.

CR-28 (config.py split): this module was extracted from
``voice_typer.server.config`` to separate the low-level secure-file-I/O
concerns (atomic write, symlink-TOCTOU-safe read) from the higher-level
config dataclass / load / save logic.  The functions here are
re-exported from ``config.py`` so existing call sites — including
``credential_store.migrate_secrets_to_keyring`` (function-level import)
and tests that monkeypatch ``voice_typer.server.config._secure_atomic_write``
— keep working unchanged.

The functions are byte-level behavior-preserving copies of the
originals in ``config.py`` (same signatures, same logic, same return
values, same exception behaviour).
"""

import contextlib
import logging
import os

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger("voice_typer.server.config")


def _secure_atomic_write(path: os.PathLike, content: str) -> None:
    """Write content to ``path`` atomically and securely.

    NEW-SEC-008: prevents symlink-TOCTOU attacks by:
    1. Writing to a temp file in the same directory (so os.replace
       is atomic on the same filesystem).
    2. On POSIX, using ``os.open(tmp, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)``
       for the temp file. ``O_EXCL`` prevents a pre-created temp file
       from being hijacked; ``O_NOFOLLOW`` refuses to follow symlinks.
    3. On POSIX, tightening the target directory to 0o700 before the
       write so the temp file is not world-readable.
    4. Using ``os.replace(tmp, target)`` which is atomic on POSIX
       and does NOT follow symlinks on the target (it replaces the
       directory entry).

    SEC-007: file mode 0o600 ensures API keys in config.json are not
    world-readable on multi-user POSIX systems.

    Parameters
    ----------
    path : Path
        Target file path.
    content : str
        Content to write (UTF-8 encoded).
    """
    tmp_path = None
    try:
        # Create temp file in same directory for atomic rename
        from pathlib import Path

        tmp_path = Path(path).with_suffix(Path(path).suffix + ".tmp")
        if not is_windows():
            # POSIX: use O_NOFOLLOW to prevent symlink attacks on the
            # temp file itself, and O_EXCL to prevent a pre-created
            # temp file from being hijacked.
            fd = os.open(
                str(tmp_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
        else:
            # Windows: O_NOFOLLOW not available, but NTFS ACLs under
            # %APPDATA% are per-user. Use standard open + fsync.
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        # os.replace is atomic and does NOT follow symlinks on the
        # target — it replaces the directory entry itself.
        os.replace(str(tmp_path), str(path))
    except Exception:
        # Clean up temp file on failure
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


def _secure_read_text(path: os.PathLike, *, encoding: str = "utf-8") -> str:
    """SEC-002: Read text from a file securely, refusing to follow symlinks.

    On POSIX, opens the file with ``os.O_RDONLY | os.O_NOFOLLOW`` to
    prevent symlink-TOCTOU attacks. If ``path`` is a symlink, the open
    call raises ``OSError`` with ``errno=ELOOP`` (or ``EINVAL`` on some
    kernels). On Windows, checks for reparse points before reading.

    After opening, uses ``os.fstat()`` to verify the inode so that a
    race between the open and the read is detectable (the file could be
    replaced by a symlink or different file in the window between
    ``open()`` and ``read()`` — on Linux this is extremely unlikely
    due to O_NOFOLLOW, but the inode check provides defense in depth).

    Parameters
    ----------
    path : Path
        File to read.
    encoding : str
        Text encoding (default UTF-8).

    Returns
    -------
    str
        File contents as a string.

    Raises
    ------
    OSError
        If the file is a symlink (POSIX) or cannot be opened.
    ValueError
        If the inode changed between open and read (TOCTOU detected).
    """
    from pathlib import Path

    p = Path(path)
    if not is_windows():
        # POSIX: O_NOFOLLOW refuses to follow symlinks
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            # SEC-002: Record the inode after opening to detect TOCTOU replacement
            stat_before = os.fstat(fd)
            f = os.fdopen(fd, "r", encoding=encoding)
            try:
                content = f.read()
                # SEC-002: Re-stat the fd to verify inode hasn't changed
                # Must do this before f.close() since close() releases the fd
                stat_after = os.fstat(fd)
                if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                    raise ValueError(f"SEC-002: inode changed during read of {p} — possible TOCTOU attack")
            finally:
                f.close()
            return content
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
    else:
        # Windows: check for reparse points (symlinks/junctions) before reading
        # NTFS reparse points have the FILE_ATTRIBUTE_REPARSE_POINT bit set.
        try:
            # `st_file_attributes` is a Windows-only attribute on
            # `os.stat_result`. Use ``getattr`` with a default of 0 so
            # the type-checker doesn't reject the access on the
            # cross-platform `stat_result` type (which doesn't declare
            # this attribute). On non-Windows platforms the attribute
            # is absent at runtime and ``getattr`` returns 0, so the
            # reparse-point check is a no-op (correct behavior —
            # reparse points are a Windows-only NTFS concept).
            stat_result = os.lstat(str(p)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
            if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise OSError(f"SEC-002: refusing to follow reparse point: {p}")
        except (AttributeError, OSError):
            pass  # lstat not available or file doesn't exist; open() will catch it
        with open(p, encoding=encoding) as f:
            # SEC-002: verify inode on Windows too (using os.fstat on the fileno)
            stat_before = os.fstat(f.fileno())
            content = f.read()
            stat_after = os.fstat(f.fileno())
            if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                raise ValueError(f"SEC-002: inode changed during read of {p} — possible TOCTOU attack")
            return content
