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
    re-export is what the tests patch, same pattern as the lazy
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
    power loss, the file DATA is durable (fsynced earlier) but the
    rename itself is not.

    Best-effort: any failure (ctypes missing, CreateFileW fails,
    FlushFileBuffers fails) is logged at DEBUG and swallowed so the
    caller's write still succeeds, the pre-fix behavior (rename not
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
        GENERIC_WRITE = 0x40000000  # noqa: N806
        FILE_SHARE_READ = 0x00000001  # noqa: N806
        FILE_SHARE_WRITE = 0x00000002  # noqa: N806
        OPEN_EXISTING = 3  # noqa: N806
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # noqa: N806
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value  # noqa: N806

        # CreateFileW signature:
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
    except Exception as e:  # noqa: BLE001, best-effort; never raise
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
       , both of which persist security-critical data (API keys, user
        settings) where the fsync cost is justified.  Pass
        ``durability=False`` for non-critical writes (cache files,
        telemetry dumps, PID files, onboarding sentinels) where the
        atomicity guarantee still matters but a power-loss window of a
        few seconds is acceptable.  Trade-off: skipping fsync can lose
        the most-recent write on power loss (the os.replace rename may
        not be durable on disk), but saves ~2ms per write on SSDs and
        ~10-50ms on spinning rust, significant for high-frequency
        non-critical writes.

    the inner ``with os.fdopen(fd, ...)`` was previously
        wrapped in a try/except that called ``os.close(fd)`` on any
        exception.  But the with-block's ``__exit__`` ALREADY closes
        the fd, so the except's ``os.close(fd)`` was a DOUBLE-CLOSE.
        On a quiet fd-table this only emits EBADF (suppressed by
        ``contextlib.suppress(OSError)``); but under concurrent load
        the closed fd number can be REUSED by another thread's
        ``os.open``/``socket``/etc., and the second ``os.close(fd)``
        would close that unrelated fd, silent corruption of an
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
    owned_fd = -1
    try:
        # use a UNIQUE tmp name per call.  mkstemp returns
        fd, tmp_name = tempfile.mkstemp(
            dir=str(parent),
            prefix=target.name + ".",
            suffix=".tmp",
        )
        owned_fd = fd
        tmp_path = Path(tmp_name)

        # manual try/finally (not a with-block) so we can flip
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
        _chmod_owner_only(target)

        # fsync the parent directory so the rename is durable.
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
_DEFAULT_MAX_READ_BYTES = 16 * 1024 * 1024

# Monotonic counter mixed into the ``.corrupt-<ts>-<pid>-<ns>`` quarantine
_QUARANTINE_SUFFIX_SEQ: "itertools.count" = itertools.count()

# Windows-only: os.replace onto a destination that another thread/process
_OS_REPLACE_MAX_ATTEMPTS = 20
_OS_REPLACE_RETRY_DELAY_S = 0.1


def _read_with_byte_limit(f, max_bytes: int | None) -> str:
    """bounded read helper used by :func:`_secure_read_text`.

    Reads text from ``f`` in 64 KiB chunks.  After each chunk, encodes
    the chunk to UTF-8 to count its byte length (text-mode ``len()``
    counts CHARACTERS, not bytes, for non-ASCII content those differ by
    up to 4x).  If the running byte total exceeds ``max_bytes``, raises
    ``ValueError`` immediately (does NOT continue reading the rest of
    the file).  If ``max_bytes is None``, reads the whole file
    (unbounded, preserved for backward compat with callers that
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
        chunk_bytes = len(chunk.encode("utf-8", errors="replace"))
        total_bytes += chunk_bytes
        if total_bytes > max_bytes:
            raise ValueError(
                f"file exceeds max_bytes={max_bytes} "
                f"(read {total_bytes} bytes so far), refusing to "
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
        owned_fd = -1
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
        owned_fd = fd
        try:
            stat_before = os.fstat(fd)
            f = os.fdopen(fd, "r", encoding=encoding)
            owned_fd = -1  # fd is now owned by f; sentinel prevents double-close
            try:
                # bounded read, aborts with ValueError if the
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
        stat_result = None
        try:
            stat_result = os.lstat(str(p)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
        except (AttributeError, OSError):
            attrs = 0
        if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise OSError(f"SEC-002: refusing to follow reparse point: {p}")
        # pre-check file size on Windows (no fstat-on-fd pattern
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
      chmod'd to 0o600 on POSIX by :func:`_secure_atomic_write`
      itself (it chmods its target on every success branch —
      ``save`` adds no second permission layer; mirrors
      ``config.py:1172-1174``).

    The helper is intentionally minimal, it does NOT know about
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
       , ``T`` is left unconstrained and resolves to ``Unknown``).
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
                ``T = Unknown`` (effectively ``Any``: preserves the
                pre-generic behaviour).
        """
        if not self._path.exists():
            # main file missing, try .bak before returning default.
            recovered = self._try_load_bak()
            if recovered is not None:
                return recovered  # type: ignore[return-value, no-any-return]
            return self._default  # type: ignore[return-value, no-any-return]
        try:
            raw = _sfio_shim()._secure_read_text(self._path, encoding="utf-8")
            result = json.loads(raw)
            # populate the diff cache so the next save() can skip
            self._last_written_bytes = raw.encode("utf-8")
            return result  # type: ignore[return-value, no-any-return]
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.warning(
                "[PERSISTED_JSON] Failed to load %s: %s, quarantining corrupt file and returning default",
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
                "[PERSISTED_JSON] Main file corrupt/missing, restored from .bak: %s",
                self._bak_path.name,
            )
            # cache the recovered .bak bytes so the next save()
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
                concurrent corruptions, even within the same second from
                DIFFERENT processes, or back-to-back from the same process
               , produce distinct filenames without needing an
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
                forensics, strictly better than raising).
        """
        try:
            if not self._path.exists():
                return
            # Embed epoch seconds + PID + sub-second nanoseconds so two
            _sfio = _sfio_shim()
            ts = int(_sfio.time.time())
            pid = os.getpid()
            ts_ns = (_sfio.time.time_ns() % 1_000_000 + next(_sfio._QUARANTINE_SUFFIX_SEQ)) % 1_000_000
            corrupt_path = self._path.with_name(f"{self._path.name}.corrupt-{ts}-{pid}-{ts_ns}")
            # os.replace is atomic AND overwrites the destination
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
                    fsync calls are skipped, suitable for non-critical cache
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
                  by :func:`_secure_atomic_write` itself, once per write, on
                  every success branch (mirrors ``config.py:1172-1174``).
                  On Windows this is a no-op (POSIX permission bits are
                  ignored; ACLs apply). ``save`` deliberately does NOT
                  re-chmod either path, the write helper already did.
                * The parent directory is created (``parents=True,
                  exist_ok=True``) so the caller doesn't have to.
                * ``_secure_atomic_write`` is imported LAZILY from
                  :mod:`voice_typer.server.config` (not from this module) so
                  existing test patches on
                  ``voice_typer.server.config._secure_atomic_write`` keep
                  working, the symbol is defined here but re-exported from
                  ``config``; the re-export is what existing tests monkeypatch
        (e.g. ``test_vocabulary_history_db_fixes.py``'s  retry
                  tests).  Lazy import avoids the circular import that a
                  module-level ``from voice_typer.server.config import ...``
                  would create (``config`` itself imports from
                  ``secure_file_io``).

        the previous implementation used ``Path.read_bytes()``
                and ``Path.write_bytes()`` for the ``.bak`` comparison + write.
                Both follow symlinks, so an attacker who planted symlinks at
                BOTH ``self._path`` and ``self._bak_path`` got a
                read-from-arbitrary-file + write-to-arbitrary-file primitive
                (the previous config: which contains API keys for
                ``credential_store``: was read through the ``self._path``
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
        from voice_typer.server.config import _secure_atomic_write

        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2, ensure_ascii=False)
        content_bytes = content.encode("utf-8")

        # (High): diff-cache optimization. The cache
        if self._last_written_bytes is not None and content_bytes == self._last_written_bytes:
            return

        # Best-effort single-slot .bak before overwrite.
        if self._path.exists() and not self._path.is_symlink() and not self._bak_path.is_symlink():
            try:
                # Read via _secure_read_text (O_NOFOLLOW on POSIX,
                existing_text = _sfio_shim()._secure_read_text(self._path, encoding="utf-8")
                existing_bytes = existing_text.encode("utf-8")
                if existing_bytes != content_bytes:
                    # The 0o600 perms on the ``.bak`` are set inside
                    _secure_atomic_write(self._bak_path, existing_text)
            except OSError as e:
                log.debug(
                    "[PERSISTED_JSON] Failed to back up %s to %s: %s",
                    self._path,
                    self._bak_path,
                    e,
                )
        elif self._path.is_symlink() or self._bak_path.is_symlink():
            # explicit log so a symlink-planting attack is
            log.warning(
                "[PERSISTED_JSON] Refusing to back up %s to %s, one of "
                "the paths is a symlink (symlink-following defense). "
                "The main save will still proceed (os.replace replaces "
                "the symlink with a fresh regular file).",
                self._path,
                self._bak_path,
            )

        _secure_atomic_write(self._path, content, durability=durability)
        # update the diff cache so the next save() can skip if
        self._last_written_bytes = content_bytes
