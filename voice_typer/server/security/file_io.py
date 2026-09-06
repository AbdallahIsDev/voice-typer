"""Secure atomic file I/O helpers for config persistence.

(config.py split): this module was extracted from
``voice_typer.server.config`` to separate the low-level secure-file-I/O
concerns (atomic write, symlink-TOCTOU-safe read) from the higher-level
config dataclass / load / save logic.  The functions here are
re-exported from ``config.py`` so existing call sites -- including
``credential_store.migrate_secrets_to_keyring`` (function-level import)
and tests that monkeypatch ``voice_typer.server.config._secure_atomic_write``
-- keep working unchanged.

This module also hosts :class:`PersistedJSON`, a higher-level helper
that bundles atomic-write + single-slot ``.bak`` before overwrite +
corrupt-quarantine on load failure + 0o600 file perms.  The shared
helper eliminates the DRY violation where ``config.py``,
``crash_recovery.py``, ``duck_crash_recovery.py``, ``vocabulary.py``,
and ``templates.py`` each reimplemented a slightly different subset of
the same three-pronged pattern.  ``vocabulary.py`` and ``templates.py``
(the two modules that had NO backup/quarantine) are now routed through
this helper; the other three modules retain their own variants for now
(migrating them is a separate refactor that risks regressions).
"""

import contextlib
import itertools
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Generic, TypeVar

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger("voice_typer.server.config")


def _sfio_shim():
    """Return the ``voice_typer.server.secure_file_io`` back-compat shim.

    ``PersistedJSON`` resolves ``_secure_read_text`` / ``time`` /
    ``_QUARANTINE_SUFFIX_SEQ`` through the SHIM at call time so existing
    tests that monkeypatch ``voice_typer.server.secure_file_io.<name>``
    keep working (the shim re-exports the canonical symbols; the
    re-export is what the tests patch — same pattern as the lazy
    ``voice_typer.server.config._secure_atomic_write`` lookup below).
    """
    from voice_typer.server import secure_file_io as _shim

    return _shim


def _windows_fsync_directory(path: str) -> None:
    """fsync a directory on Windows via ``CreateFileW`` +
    ``FlushFileBuffers`` with ``FILE_FLAG_BACKUP_SEMANTICS``.

    This is the standard Windows durability recipe (used by SQLite,
    PostgreSQL, etc.). Without it, ``os.replace``'s directory-entry
    update sits in the NTFS log buffer for seconds and may not survive
    power loss — the file DATA is durable (fsynced earlier) but the
    rename itself is not.

    Best-effort: any failure (ctypes missing, CreateFileW fails,
    FlushFileBuffers fails) is logged at DEBUG and swallowed so the
    caller's write still succeeds — the pre-fix behavior (rename not
    durable across power loss) is the fallback.

    Only invoked on Windows (guarded by ``is_windows()`` at the call
    site). The ``ctypes.windll`` attribute does not exist on POSIX, so
    this function MUST NOT be called from a non-Windows host (the
    call-site guard handles that).
    """
    try:
        import ctypes
        from ctypes import wintypes

        # kernel32 is always available on Windows.
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # Constants (avoid relying on pywin32 / Windows SDK headers):
        #   GENERIC_WRITE             = 0x40000000
        #   FILE_SHARE_READ           = 0x00000001
        #   FILE_SHARE_WRITE          = 0x00000002
        #   OPEN_EXISTING             = 3
        #   FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        # FILE_FLAG_BACKUP_SEMANTICS is required to open a directory
        # handle on Windows (without it, CreateFileW fails with
        # ERROR_ACCESS_DENIED on directories).
        GENERIC_WRITE = 0x40000000  # noqa: N806
        FILE_SHARE_READ = 0x00000001  # noqa: N806
        FILE_SHARE_WRITE = 0x00000002  # noqa: N806
        OPEN_EXISTING = 3  # noqa: N806
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # noqa: N806
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value  # noqa: N806

        # CreateFileW signature:
        #   HANDLE CreateFileW(
        #     LPCWSTR lpFileName, DWORD dwDesiredAccess, DWORD dwShareMode,
        #     LPSECURITY_ATTRIBUTES lpSecurityAttributes,
        #     DWORD dwCreationDisposition, DWORD dwFlagsAndAttributes,
        #     HANDLE hTemplateFile
        #   )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        handle = kernel32.CreateFileW(
            path,
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == INVALID_HANDLE_VALUE or handle is None:
            raise ctypes.WinError()  # type: ignore[attr-defined]
        try:
            # FlushFileBuffers signature: BOOL FlushFileBuffers(HANDLE hFile)
            kernel32.FlushFileBuffers.restype = wintypes.BOOL
            kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
            if not kernel32.FlushFileBuffers(handle):
                raise ctypes.WinError()  # type: ignore[attr-defined]
        finally:
            # CloseHandle signature: BOOL CloseHandle(HANDLE hObject)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            if not kernel32.CloseHandle(handle):
                log.debug(
                    "[CONFIG] CloseHandle failed for directory %s (best-effort)",
                    path,
                )
    except OSError as e:
        log.debug(
            "[CONFIG] Windows directory-fsync of %s failed (best-effort): %s",
            path,
            e,
        )
    except Exception as e:  # noqa: BLE001 — best-effort; never raise
        log.debug(
            "[CONFIG] Windows directory-fsync of %s failed (best-effort, non-OSError): %s",
            path,
            e,
        )


def _secure_atomic_write(
    path: os.PathLike,
    content: str,
    *,
    durability: bool = True,
) -> None:
    """Write content to ``path`` atomically and securely.

        The temp filename is generated via
        ``tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
        suffix=".tmp")`` instead of the previous fixed name
        (``path.with_suffix(path.suffix + ".tmp")``).  The fixed name
        caused concurrent callers to collide on ``O_EXCL`` (EEXIST); the
        second caller's broad ``except`` then ``unlink()``-ed the FIRST
        caller's already-written temp file (silent data loss).

        The Windows branch now uses the mkstemp-provided fd
        (which has O_EXCL semantics on Windows too) wrapped with
        ``os.fdopen`` instead of plain ``open()``.

        On POSIX, after ``os.replace`` the parent directory is
        fsynced so the rename is durable across power loss.  Best-effort.

    ``durability`` controls whether the two ``fsync`` calls
        (file data + parent directory) run.  The default ``True``
        preserves the existing POSIX-durability behavior used by
        ``Config.save()`` and ``credential_store._write_plaintext_fallback``
        — both of which persist security-critical data (API keys, user
        settings) where the fsync cost is justified.  Pass
        ``durability=False`` for non-critical writes (cache files,
        telemetry dumps, PID files, onboarding sentinels) where the
        atomicity guarantee still matters but a power-loss window of a
        few seconds is acceptable.  Trade-off: skipping fsync can lose
        the most-recent write on power loss (the os.replace rename may
        not be durable on disk), but saves ~2ms per write on SSDs and
        ~10-50ms on spinning rust — significant for high-frequency
        non-critical writes.

    the inner ``with os.fdopen(fd, ...)`` was previously
        wrapped in a try/except that called ``os.close(fd)`` on any
        exception.  But the with-block's ``__exit__`` ALREADY closes
        the fd, so the except's ``os.close(fd)`` was a DOUBLE-CLOSE.
        On a quiet fd-table this only emits EBADF (suppressed by
        ``contextlib.suppress(OSError)``); but under concurrent load
        the closed fd number can be REUSED by another thread's
        ``os.open``/``socket``/etc., and the second ``os.close(fd)``
        would close that unrelated fd — silent corruption of an
        unrelated resource.  The fix uses an ``owned_fd`` sentinel
        (set to ``-1`` immediately after ``os.fdopen`` succeeds) so the
        except path only closes the fd if ``os.fdopen`` itself failed
        (i.e. the fd is still owned by this function, not by ``f``).
    """
    from pathlib import Path

    target = Path(path)
    parent = target.parent
    tmp_path = None
    # ``owned_fd`` tracks ownership of the raw fd.  ``-1`` is the
    # sentinel meaning "fd is now owned by the file object ``f`` (or
    # already closed); do NOT call ``os.close`` on it again".  Without
    # this sentinel, a write/flush/fsync failure inside the with-block
    # would trigger a double-close: with-block ``__exit__`` closes the
    # fd, then the except path's ``os.close(fd)`` closes it AGAIN.  On a
    # quiet fd-table that's a benign EBADF (suppressed); under concurrent
    # load the fd number may have been reused by another ``os.open`` and
    # the second close silently corrupts an unrelated resource.
    owned_fd = -1
    try:
        # use a UNIQUE tmp name per call.  mkstemp returns
        # an open fd (with O_EXCL semantics) so we never collide with
        # a concurrent caller's tmp file.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(parent),
            prefix=target.name + ".",
            suffix=".tmp",
        )
        owned_fd = fd
        tmp_path = Path(tmp_name)

        # manual try/finally (not a with-block) so we can flip
        # ``owned_fd`` to ``-1`` AFTER ``os.fdopen`` succeeds — proving
        # to the outer except that the fd is now owned by ``f`` and
        # must not be closed again.  Using a with-block here would
        # reintroduce the double-close: the with-block's ``__exit__``
        # closes the fd on any exception, but we can't set ``owned_fd
        # = -1`` between ``os.fdopen(fd)`` and the body of the
        # with-block.
        # Write in BINARY mode: text-mode ``os.fdopen(fd, "w")`` applies
        # the platform newline translation (LF -> CRLF on Windows), which
        # corrupts byte-exact content contracts (e.g. ``config.json.bak``
        # must be byte-for-byte identical to the config.json it backs up —
        # a forensic-recovery contract asserted by
        # ``tests/test_config_service_secure_backup.py``). Binary mode
        # preserves the exact bytes on every platform. ``str`` content is
        # UTF-8-encoded explicitly (same bytes text mode produced on
        # POSIX).
        f = os.fdopen(fd, "wb")
        owned_fd = -1  # fd is now owned by f; sentinel prevents double-close
        try:
            if isinstance(content, str):
                f.write(content.encode("utf-8"))
            else:
                f.write(content)
            f.flush()
            # skip fsync of the file data when durability=False.
            if durability:
                os.fsync(f.fileno())
        finally:
            f.close()

        # os.replace is atomic and does NOT follow symlinks on the target.
        #
        # On Windows, os.replace raises PermissionError (WinError 5
        # "Access is denied") when another thread/process has the
        # destination open at the moment of the rename — e.g. two
        # concurrent Config.save() calls racing to persist config.json
        # regression surface). The lock is held only for the
        # other writer's brief write window, so the failure is
        # transient: retry with a short backoff before propagating.
        # POSIX renames cannot fail this way (rename(2) never blocks on
        # an open destination), so the retry is Windows-only.
        if is_windows():
            _last_replace_exc: OSError | None = None
            for _attempt in range(_OS_REPLACE_MAX_ATTEMPTS):
                try:
                    os.replace(str(tmp_path), str(target))
                    break
                except PermissionError as exc:
                    _last_replace_exc = exc
                    time.sleep(_OS_REPLACE_RETRY_DELAY_S)
            else:
                if _last_replace_exc is not None:
                    raise _last_replace_exc
        else:
            os.replace(str(tmp_path), str(target))

        # explicit chmod to 0o600 (POSIX, best-effort) —
        # defense-in-depth even though ``tempfile.mkstemp`` creates the
        # tmp file with 0o600 already. ``os.replace`` brings the
        # source inode (with its permissions) to the destination on
        # POSIX, so the 0o600 from mkstemp IS preserved across the
        # rename — but we re-apply it explicitly so a future refactor
        # that changes the tmp-creation path (e.g. a caller that
        # passes a pre-opened fd, or a future Python release that
        # changes mkstemp's default mode) can't silently leak
        # world-readable config files. ``_chmod_owner_only`` is a
        # no-op on Windows (POSIX permission bits are ignored; ACLs
        # apply) and suppresses OSError at debug level so a read-only
        # filesystem doesn't fail the write.
        _chmod_owner_only(target)

        # fsync the parent directory so the rename is durable.
        # POSIX-only -- Windows has no equivalent.  Best-effort.
        # skip when durability=False (the rename still happens,
        # but its durability across power loss is not guaranteed).
        # on Windows, the file DATA is durable (the fsync at
        # line 132 above has no Windows guard and runs unconditionally
        # when durability=True), but the directory-entry update (the
        # rename) sits in the NTFS log buffer for seconds and may not
        # survive power loss. The standard Windows durability recipe
        # (used by SQLite, PostgreSQL, etc.) is to open the parent
        # directory with ``CreateFileW(FILE_FLAG_BACKUP_SEMANTICS)``
        # and call ``FlushFileBuffers(handle)`` on it. Without this,
        # ``os.replace`` is atomic but not durable across power loss
        # on Windows.
        if durability:
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
            else:
                _windows_fsync_directory(str(parent))
    except Exception:
        if owned_fd != -1:
            with contextlib.suppress(OSError):
                os.close(owned_fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


# default upper bound on a single ``_secure_read_text`` call.
# 16 MiB is well above any legitimate config / vocabulary / templates /
# credential-store / crash-recovery file size (those are all < 1 MB in
# practice) but prevents a maliciously planted multi-GB file from
# exhausting RAM.  Callers reading genuinely large files should pass an
# explicit ``max_bytes`` (e.g. ``max_bytes=64 * 1024 * 1024`` for a 64
# MiB cap).
_DEFAULT_MAX_READ_BYTES = 16 * 1024 * 1024

# Monotonic counter mixed into the ``.corrupt-<ts>-<pid>-<ns>`` quarantine
# suffix so rapid back-to-back / concurrent quarantine events never collide
# (see ``PersistedJSON._quarantine_corrupt``). GIL-atomic ``next()`` — no
# lock needed.
_QUARANTINE_SUFFIX_SEQ: "itertools.count" = itertools.count()

# Windows-only: os.replace onto a destination that another thread/process
# holds open raises PermissionError (WinError 5). ``_secure_atomic_write``
# retries up to ``_OS_REPLACE_MAX_ATTEMPTS`` times with a short sleep so
# concurrent Config.save() calls don't spuriously fail. The window
# is tiny for a single racing writer, but SUSTAINED contention (4+ threads
# hammering the same target without the mutation lock — the
# ``test_concurrent_saves_no_false_return`` stress test — or Defender's
# real-time scan briefly pinning config.json) can hold the destination for
# well over 500ms; 10 x 50ms was empirically exhausted on CI
# (windows-2022/3.11: 2 of 80 saves returned False). 20 x 100ms = 2s covers
# the contended case while staying invisible to callers (the loop only
# runs when a replace actually collides).
_OS_REPLACE_MAX_ATTEMPTS = 20
_OS_REPLACE_RETRY_DELAY_S = 0.1


def _read_with_byte_limit(f, max_bytes: int | None) -> str:
    """bounded read helper used by :func:`_secure_read_text`.

    Reads text from ``f`` in 64 KiB chunks.  After each chunk, encodes
    the chunk to UTF-8 to count its byte length (text-mode ``len()``
    counts CHARACTERS, not bytes — for non-ASCII content those differ by
    up to 4x).  If the running byte total exceeds ``max_bytes``, raises
    ``ValueError`` immediately (does NOT continue reading the rest of
    the file).  If ``max_bytes is None``, reads the whole file
    (unbounded — preserved for backward compat with callers that
    explicitly opt out of the cap).

    Mirrors the chunked-read pattern from
    :func:`voice_typer.server.cloud_engines._read_capped` (SEC-030) so
    the two bounded-read helpers behave consistently.
    """
    if max_bytes is None:
        return f.read()
    chunks: list[str] = []
    total_bytes = 0
    while True:
        chunk = f.read(64 * 1024)
        if not chunk:
            break
        # Encode to UTF-8 to count BYTES, not characters.  For ASCII
        # content 1 char == 1 byte, but for non-ASCII (CJK, emoji, etc.)
        # a single character can be 2-4 bytes.  Counting characters
        # would under-report the memory footprint by up to 4x.
        chunk_bytes = len(chunk.encode("utf-8", errors="replace"))
        total_bytes += chunk_bytes
        if total_bytes > max_bytes:
            raise ValueError(
                f"file exceeds max_bytes={max_bytes} "
                f"(read {total_bytes} bytes so far) — refusing to "
                f"continue reading to prevent unbounded memory consumption"
            )
        chunks.append(chunk)
    return "".join(chunks)


def _secure_read_text(
    path: os.PathLike,
    *,
    encoding: str = "utf-8",
    max_bytes: int | None = _DEFAULT_MAX_READ_BYTES,
) -> str:
    """SEC-002: Read text from a file securely, refusing to follow symlinks.

        On POSIX, opens the file with ``os.O_RDONLY | os.O_NOFOLLOW`` to
        prevent symlink-TOCTOU attacks. On Windows, checks for reparse
        points before reading.

    the inner ``os.fdopen`` was previously wrapped in a
        try/except that called ``os.close(fd)`` on any exception.  But
        ``f.close()`` in the ``finally`` block ALREADY closes the fd, so
        the except's ``os.close(fd)`` was a DOUBLE-CLOSE.  On a quiet
        fd-table this only emits EBADF (suppressed); but under concurrent
        load the closed fd number can be REUSED by another thread's
        ``os.open``/``socket``/etc., and the second ``os.close(fd)``
        would close that unrelated fd.  The fix uses an ``owned_fd``
        sentinel (set to ``-1`` immediately after ``os.fdopen`` succeeds)
        so the except path only closes the fd if ``os.fdopen`` itself
        failed.

    ``max_bytes`` (default 16 MiB) caps the total bytes read.
        A maliciously planted multi-GB file at the config path would
        otherwise exhaust RAM before the JSON parser saw a single byte.
        The cap is enforced in 64 KiB chunks via
        :func:`_read_with_byte_limit` so the read aborts as soon as the
        cap is exceeded (not after reading the whole file).  Pass
        ``max_bytes=None`` for the legacy unbounded behaviour (used by
        tests that intentionally read large fixtures).
    """
    from pathlib import Path

    p = Path(path)
    if not is_windows():
        # ``owned_fd`` tracks ownership of the raw fd.  ``-1`` is
        # the sentinel meaning "fd is now owned by the file object ``f``
        # (or already closed); do NOT call ``os.close`` on it again".
        owned_fd = -1
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
        owned_fd = fd
        try:
            stat_before = os.fstat(fd)
            f = os.fdopen(fd, "r", encoding=encoding)
            owned_fd = -1  # fd is now owned by f; sentinel prevents double-close
            try:
                # bounded read — aborts with ValueError if the
                # file exceeds max_bytes before the read completes.
                content = _read_with_byte_limit(f, max_bytes)
                stat_after = os.fstat(f.fileno())
                if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                    raise ValueError(f"SEC-002: inode changed during read of {p} -- possible TOCTOU attack")
            finally:
                f.close()
            return content
        except Exception:
            if owned_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(owned_fd)
            raise
    else:
        # split the try so the deliberate reparse-point raise
        # is NOT caught by the tolerant except (which previously swallowed
        # it, making the Windows reparse-point protection dead code).
        # initialize stat_result to None BEFORE the try-block so an
        # OSError from os.lstat does not leave it unbound (the subsequent
        # `stat_result is not None` reference would raise UnboundLocalError,
        # which is NOT caught by the caller's `except (json.JSONDecodeError,
        # OSError, ValueError)` and would crash app startup).
        stat_result = None
        try:
            stat_result = os.lstat(str(p)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
        except (AttributeError, OSError):
            attrs = 0
        if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise OSError(f"SEC-002: refusing to follow reparse point: {p}")
        # pre-check file size on Windows (no fstat-on-fd pattern
        # here because we use the high-level ``open()`` rather than
        # ``os.open``).  This is a fast-path rejection of obviously
        # oversized files; the chunked read below is the slow-path
        # safety net for the TOCTOU case where the file grows between
        # the lstat and the read.
        if max_bytes is not None and stat_result is not None and stat_result.st_size > max_bytes:
            raise ValueError(f"file size {stat_result.st_size} exceeds max_bytes={max_bytes}")
        with open(p, encoding=encoding) as f:
            stat_before = os.fstat(f.fileno())
            content = _read_with_byte_limit(f, max_bytes)
            stat_after = os.fstat(f.fileno())
            if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                raise ValueError(f"SEC-002: inode changed during read of {p} -- possible TOCTOU attack")
            return content


def _chmod_owner_only(path: Path) -> None:
    """Best-effort chmod ``path`` to 0o600 on POSIX.

    Mirrors ``config.py:1172-1174``.  POSIX-only (Windows ignores POSIX
    permission bits and uses ACLs instead).  Errors are logged at debug
    level so the caller's write still succeeds on a read-only
    filesystem.
    """
    if is_windows():
        return
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        log.debug(
            "[CONFIG] Failed to chmod %s to 0o600 (best-effort): %s",
            path,
            e,
        )


# Generic type parameter for :class:`PersistedJSON`.
#
# The default value passed to ``__init__`` is intentionally typed as
# ``Any`` (not ``T``) so legacy callers that pass ``default=None`` and
# later ``.save(some_dict)`` keep type-checking clean (they get the
# pre-generic ``Any`` behaviour). New callers can opt INTO type safety
# by explicitly parameterising the class — e.g.
# ``PersistedJSON[dict[str, Any]](path, default={})`` — after which
# both :meth:`load` and :meth:`save` are statically checked against
# ``dict[str, Any]``. The two existing call sites
# (:class:`VocabularyManager`, :class:`TemplateManager`) currently do
# not parameterise; parameterising them is a mechanical follow-up that
# is out of scope for this change because those modules are owned by
# another agent's area.
T = TypeVar("T")


class PersistedJSON(Generic[T]):
    """Atomic-write + single-slot ``.bak`` + corrupt-quarantine + 0o600 perms.

    A higher-level helper that bundles the three-pronged safe-persistence
    pattern that was previously copy-pasted (with drift) across
    ``config.py``, ``crash_recovery.py``, ``duck_crash_recovery.py``,
    ``vocabulary.py``, and ``templates.py``.

    Behaviour summary:

    * :meth:`load` reads the JSON file via :func:`_secure_read_text`
      (POSIX ``O_NOFOLLOW`` + inode re-verification).  On parse failure
      (``json.JSONDecodeError`` / ``OSError`` / ``ValueError``), the
      corrupt file is *quarantined* by atomically renaming it to
      ``<path>.corrupt-<timestamp>`` (best-effort) and the configured
      ``default`` is returned.  This preserves the corrupt file for
      forensic recovery AND prevents the next :meth:`save` from
      overwriting it.

    * :meth:`save` writes the JSON content via
      :func:`_secure_atomic_write` (POSIX ``O_NOFOLLOW`` on the tmp
      file, ``fsync`` of data + parent dir, atomic ``os.replace``).
      Before the overwrite, if the existing file's bytes differ from
      the new content, a single-slot ``<path>.bak`` is written
      byte-for-byte (so a re-save of identical content does not
      churn the backup).  The ``.bak`` and the final file are
      chmod'd to 0o600 on POSIX (mirrors ``config.py:1172-1174``).

    The helper is intentionally minimal — it does NOT know about
    schema validation, defaults-merging, or in-memory cacheing.  Those
    concerns remain in the caller (``VocabularyManager``,
    ``TemplateManager``, etc.).  The caller is responsible for calling
    :meth:`load` and :meth:`save` at the right points and for
    interpreting the returned default.

    Generic type parameter ``T``:
        The class is parameterised by ``T`` so callers can opt into
        static type-checking on the JSON round-trip. The default value
        is intentionally typed as ``Any`` so legacy callers that pass
        ``default=None`` and later ``.save(some_dict)`` keep
        type-checking clean (they get the pre-generic ``Any`` behaviour
        — ``T`` is left unconstrained and resolves to ``Unknown``).
        Callers that want type safety parameterise explicitly:

        >>> from voice_typer.server.secure_file_io import PersistedJSON
        >>> store: PersistedJSON[dict[str, object]] = PersistedJSON(
        ...     path, default={},
        ... )
        >>> data: dict[str, object] = store.load()
        >>> store.save({"key": "value"})

        Without parameterisation, ``load()`` returns ``T = Unknown``
        (effectively ``Any``) and ``save(data)`` accepts anything —
        identical to the pre-generic behaviour. The two existing call
        sites (``VocabularyManager``, ``TemplateManager``) do not
        parameterise yet; parameterising them is a mechanical
        follow-up out of scope for this change.
    """

    def __init__(self, path: Path, *, default: Any = None) -> None:
        self._path = Path(path)
        self._default = default
        self._bak_path = self._path.with_name(self._path.name + ".bak")
        # (High): _last_written_bytes cache for  diff optimization.
        # Populated on load() and updated on save(). Stores the actual
        # UTF-8 bytes of the last-written (or last-loaded) content — NOT
        # just the byte length — so that a subsequent save with identical
        # content can skip BOTH the file read (for .bak diff) AND the
        # write (no fsync, no rename, no .bak churn). This eliminates
        # the 2-fsync-per-save overhead for vocabulary/templates that are
        # saved frequently but rarely change.
        self._last_written_bytes: bytes | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def default(self) -> Any:
        return self._default

    def load(self) -> T:
        """Load JSON.  On parse failure, quarantine the corrupt file and
                return the configured default.

                If the file does not exist, returns the default without logging
                (the typical first-launch case).

                If the file exists but cannot be read (``OSError``) or parsed
                (``json.JSONDecodeError`` / ``ValueError``), the corrupt file is
                renamed to ``<path>.corrupt-<timestamp>`` (best-effort; if the
                rename fails the corrupt file is left in place) and the default
                is returned.  This mirrors the pattern in
                ``config.py:1744-1763`` and ``crash_recovery.py:186-219``.

        (Medium): if the main file is corrupt/missing, attempt
                to load from the ``.bak`` before returning the default. The
                ``.bak`` is a single-slot snapshot written on every save (when
                content differs). If the ``.bak`` loads successfully, log a
                warning and return the recovered data.

                Returns ``T`` so callers that parameterise the class get a
                statically-typed value back; unparameterised callers get
                ``T = Unknown`` (effectively ``Any`` — preserves the
                pre-generic behaviour).
        """
        if not self._path.exists():
            # main file missing — try .bak before returning default.
            recovered = self._try_load_bak()
            if recovered is not None:
                return recovered  # type: ignore[return-value, no-any-return]
            return self._default  # type: ignore[return-value, no-any-return]
        try:
            raw = _sfio_shim()._secure_read_text(self._path, encoding="utf-8")
            result = json.loads(raw)
            # populate the diff cache so the next save() can skip
            # both the file read AND the write if the content hasn't
            # changed. Cache the actual UTF-8 bytes (not just the length)
            # so a content-equality check is sufficient on the next save.
            self._last_written_bytes = raw.encode("utf-8")
            return result  # type: ignore[return-value, no-any-return]
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.warning(
                "[PERSISTED_JSON] Failed to load %s: %s — quarantining corrupt file and returning default",
                self._path,
                exc,
            )
            self._quarantine_corrupt()
            # try .bak recovery after quarantining the corrupt main.
            recovered = self._try_load_bak()
            if recovered is not None:
                return recovered  # type: ignore[return-value, no-any-return]
            return self._default  # type: ignore[return-value]

    def _try_load_bak(self) -> Any | None:
        """attempt to load from the .bak file. Returns None if .bak
        is missing or also corrupt (caller falls back to default)."""
        try:
            if not self._bak_path.exists() or self._bak_path.is_symlink():
                return None
            raw = _sfio_shim()._secure_read_text(self._bak_path, encoding="utf-8")
            result = json.loads(raw)
            log.warning(
                "[PERSISTED_JSON] Main file corrupt/missing — restored from .bak: %s",
                self._bak_path.name,
            )
            # cache the recovered .bak bytes so the next save()
            # can skip both the file read AND the write if the content
            # hasn't changed (mirrors the main-file load() path).
            self._last_written_bytes = raw.encode("utf-8")
            return result
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.debug(
                "[PERSISTED_JSON] .bak recovery failed for %s: %s",
                self._bak_path,
                exc,
            )
            return None

    def _quarantine_corrupt(self) -> None:
        """Best-effort rename the corrupt file to ``<path>.corrupt-<ts>-<pid>-<ns>``.

                Mirrors ``crash_recovery.py:_quarantine_corrupt``: the corrupt
                file is renamed aside for forensic recovery.  Best-effort —
                never raises.  If the file disappeared between the
                ``exists()`` check and now, or the rename fails (cross-device,
                permissions), the failure is logged at debug level and
                swallowed so the caller's load still returns the default
                cleanly.

        the filename embeds epoch seconds + PID + sub-second
                nanoseconds (``time.time_ns() % 1_000_000``) so two
                concurrent corruptions — even within the same second from
                DIFFERENT processes, or back-to-back from the same process
                — produce distinct filenames without needing an
                ``exists()`` probe loop.  This mirrors the
                migration-backup path in ``config.py:1900-1903`` and the
                corrupt-config rename in ``config.py:1779-1782``.  The
                previous implementation used ``int(time.time())`` + a
                counter loop with an ``exists()`` TOCTOU window: two
                processes corrupting their files in the same second both
                picked ``ts`` + ``counter=0`` and one overwrote the
                other's quarantine via the subsequent ``os.replace`` —
                losing forensic history.

        uses :func:`os.replace` instead of :meth:`Path.rename`.
                ``os.rename`` is atomic on POSIX but FAILS on Windows if the
                destination already exists (``OSError`` winerror 183).  The
                PID + nanosecond suffix makes a destination collision
                essentially impossible, but ``os.replace`` is retained as
                the safety net: it is atomic AND overwrites an existing
                destination on BOTH POSIX and Windows, so even if a future
                change weakens the suffix uniqueness, the worst case is the
                previous-behavior overwrite (no corruption, just lost
                forensics — strictly better than raising).
        """
        try:
            if not self._path.exists():
                return
            # Embed epoch seconds + PID + sub-second nanoseconds so two
            # concurrent quarantine events never pick the same filename
            # (closes the same-second TOCTOU race that the previous
            # ``while corrupt_path.exists(): counter += 1`` loop had).
            # Mirrors the suffix scheme already used by
            # ``config.py:_backup_before_migration`` and
            # ``config.py:_backup_before_downgrade``.
            #
            # A module-level monotonic counter is mixed into the
            # nanosecond component: on Windows ``time.time_ns()`` can
            # return the SAME value for rapid back-to-back / concurrent
            # calls inside the same millisecond (coarse system-timer
            # granularity), which would make two quarantine events pick
            # the identical suffix and ``os.replace`` would silently
            # overwrite one quarantine file. ``itertools.count`` is
            # GIL-atomic so no lock is needed; the counter only
            # disambiguates calls within the same ms window (wrapping
            # would require 1M calls inside one ms — impossible).
            _sfio = _sfio_shim()
            ts = int(_sfio.time.time())
            pid = os.getpid()
            ts_ns = (_sfio.time.time_ns() % 1_000_000 + next(_sfio._QUARANTINE_SUFFIX_SEQ)) % 1_000_000
            corrupt_path = self._path.with_name(f"{self._path.name}.corrupt-{ts}-{pid}-{ts_ns}")
            # os.replace is atomic AND overwrites the destination
            # on both POSIX and Windows (Path.rename / os.rename would
            # fail on Windows if the destination exists).  With the
            # pid + nanosecond suffix a collision is essentially
            # impossible, but os.replace is the safety net so we never
            # raise on the rename path.
            os.replace(str(self._path), str(corrupt_path))
            log.warning(
                "[PERSISTED_JSON] Quarantined corrupt file: %s -> %s",
                self._path.name,
                corrupt_path.name,
            )
        except OSError as move_exc:
            log.debug(
                "[PERSISTED_JSON] Could not move corrupt file %s aside: %s",
                self._path,
                move_exc,
            )

    def save(self, data: T, *, durability: bool = True) -> None:
        """Atomic save.  Creates ``.bak`` before overwrite.  Sets 0o600 perms.

                Parameters
                ----------
                data : T
                    JSON-serialisable payload.  ``json.dumps(data, indent=2,
                    ensure_ascii=False)`` is used so non-ASCII characters
                    survive the round-trip (mirrors ``vocabulary.py`` and
                    ``templates.py`` which both pass ``ensure_ascii=False``).
                    Typed as ``T`` so callers that parameterise the class get
                    static type-checking on the saved shape; unparameterised
                    callers pass ``T = Unknown`` (accepts anything —
                    pre-generic behaviour).
                durability : bool
        (High): when ``True`` (default), the write
                    uses the full ``_secure_atomic_write`` path with ``fsync``
                    of both file data and parent directory. When ``False``, the
                    fsync calls are skipped — suitable for non-critical cache
                    files where the OS page cache is sufficient and the
                    fsync overhead (2 syscalls per save) is undesirable.

                Notes
                -----
                * The ``.bak`` is single-slot: each save overwrites the previous
                  ``.bak`` (so re-running saves does not accumulate backup
                  files).  Only files whose bytes DIFFER from the new content
                  are backed up (a re-save of identical content is a no-op for
                  the backup slot).
                * On POSIX the ``.bak`` and the final file are chmod'd to 0o600
                  (mirrors ``config.py:1172-1174``).  On Windows this is a
                  no-op (POSIX permission bits are ignored; ACLs apply).
                * The parent directory is created (``parents=True,
                  exist_ok=True``) so the caller doesn't have to.
                * ``_secure_atomic_write`` is imported LAZILY from
                  :mod:`voice_typer.server.config` (not from this module) so
                  existing test patches on
                  ``voice_typer.server.config._secure_atomic_write`` keep
                  working — the symbol is defined here but re-exported from
                  ``config``; the re-export is what existing tests monkeypatch
        (e.g. ``test_vocabulary_history_db_fixes.py``'s  retry
                  tests).  Lazy import avoids the circular import that a
                  module-level ``from voice_typer.server.config import ...``
                  would create (``config`` itself imports from
                  ``secure_file_io``).

        the previous implementation used ``Path.read_bytes()``
                and ``Path.write_bytes()`` for the ``.bak`` comparison + write.
                Both follow symlinks — so an attacker who planted symlinks at
                BOTH ``self._path`` and ``self._bak_path`` got a
                read-from-arbitrary-file + write-to-arbitrary-file primitive
                (the previous config — which contains API keys for
                ``credential_store`` — was read through the ``self._path``
                symlink and written through the ``self._bak_path`` symlink).
                The fix refuses to follow symlinks on EITHER path: if either
                is a symlink, the backup is skipped (the main save still
                proceeds because ``_secure_atomic_write`` already handles
                symlinks safely via ``os.replace``).  The existing-file read
                is routed through :func:`_secure_read_text` (POSIX
                ``O_NOFOLLOW`` + inode re-verification); the ``.bak`` write
                is routed through :func:`_secure_atomic_write` (atomic
                ``os.replace`` does not follow the destination symlink).
        """
        # Lazy import so monkeypatches on
        # ``voice_typer.server.config._secure_atomic_write`` are
        # observed at call time (mirrors the pre-existing lazy import
        # in ``vocabulary._save_user`` and ``templates._save``).
        from voice_typer.server.config import _secure_atomic_write

        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2, ensure_ascii=False)
        content_bytes = content.encode("utf-8")

        # (High): diff-cache optimization. The cache
        # stores the actual UTF-8 bytes of the last-written (or
        # last-loaded) content. If the new content's bytes match the
        # cached bytes EXACTLY, skip both the file read (no need to
        # re-check the on-disk content) AND the write (no fsync, no
        # rename, no .bak churn). This eliminates the redundant
        # read-then-write cycle for vocabulary/templates that are saved
        # frequently but rarely change.
        #
        # Correctness: the cache is populated ONLY after a successful
        # load() or save() (both of which guarantee the on-disk bytes
        # match the cached bytes), and is invalidated on a failed load
        # (see ``test_cache_invalidated_on_failed_load``). So a cache
        # hit here is proof that the on-disk content matches the new
        # content — no need to re-read.
        if self._last_written_bytes is not None and content_bytes == self._last_written_bytes:
            return

        # Best-effort single-slot .bak before overwrite.
        #
        # SECURITY: refuse to follow symlinks on EITHER path.  If
        # ``self._path`` is a symlink, ``Path.read_bytes()`` (used
        # pre-fix) would read the SYMLINK TARGET's bytes — exfiltrating
        # an arbitrary file's content into the ``.bak``.  If
        # ``self._bak_path`` is a symlink, ``Path.write_bytes()``
        # (used pre-fix) would write THROUGH the symlink to its target
        # — overwriting an attacker-chosen file with the exfiltrated
        # bytes.  Together: read-from-arbitrary-file + write-to-
        # arbitrary-file primitive ( finding that the
        # split moved into this shared helper WITHOUT fixing).
        #
        # The fix:
        #   - Explicitly check ``is_symlink()`` on both paths and
        #     SKIP the backup entirely if either is a symlink (the
        #     main save via ``_secure_atomic_write`` is unaffected —
        #     it uses ``os.replace`` which does NOT follow the
        #     destination symlink, it replaces it).
        #   - Use ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` +
        #     inode re-verification) for the existing-file read.
        #     Defense-in-depth: even without the explicit
        #     ``is_symlink()`` check, ``O_NOFOLLOW`` would raise
        #     ``OSError`` on a symlink.  The explicit check is for
        #     Windows (where ``O_NOFOLLOW`` is not supported) and
        #     for clarity.
        #   - Use ``_secure_atomic_write`` for the ``.bak`` write.
        #     Its ``os.replace`` semantics ensure we never write
        #     THROUGH a symlink at ``self._bak_path``; we replace
        #     the symlink itself with a fresh regular file.
        if self._path.exists() and not self._path.is_symlink() and not self._bak_path.is_symlink():
            try:
                # Read via _secure_read_text (O_NOFOLLOW on POSIX,
                # reparse-point check on Windows).  If the existing
                # file is somehow not valid UTF-8 (e.g. corrupt or
                # hand-edited with a different encoding), this raises
                # OSError/UnicodeDecodeError — caught by the
                # ``except OSError`` below, and the backup is skipped
                # (acceptable: the .bak is best-effort, and a non-UTF-8
                # file is by definition already corrupt — backing it
                # up via the JSON-aware save path would not help).
                existing_text = _sfio_shim()._secure_read_text(self._path, encoding="utf-8")
                existing_bytes = existing_text.encode("utf-8")
                if existing_bytes != content_bytes:
                    _secure_atomic_write(self._bak_path, existing_text)
                    _chmod_owner_only(self._bak_path)
            except OSError as e:
                log.debug(
                    "[PERSISTED_JSON] Failed to back up %s to %s: %s",
                    self._path,
                    self._bak_path,
                    e,
                )
        elif self._path.is_symlink() or self._bak_path.is_symlink():
            # explicit log so a symlink-planting attack is
            # visible in the logs (defense-in-depth visibility — the
            # backup is silently skipped, but the operator can grep
            # for this message to detect the attack).
            log.warning(
                "[PERSISTED_JSON] Refusing to back up %s to %s — one of "
                "the paths is a symlink (symlink-following defense). "
                "The main save will still proceed (os.replace replaces "
                "the symlink with a fresh regular file).",
                self._path,
                self._bak_path,
            )

        _secure_atomic_write(self._path, content, durability=durability)
        # update the diff cache so the next save() can skip if
        # the content hasn't changed. Store the actual bytes (not just
        # the length) so a content-equality check is sufficient.
        self._last_written_bytes = content_bytes
        _chmod_owner_only(self._path)
