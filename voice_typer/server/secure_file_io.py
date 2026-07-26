"""Secure atomic file I/O helpers for config persistence.

CR-28 (config.py split): this module was extracted from
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
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger("voice_typer.server.config")


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

    ER-80: ``durability`` controls whether the two ``fsync`` calls
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
    """
    from pathlib import Path

    target = Path(path)
    parent = target.parent
    tmp_path = None
    tmp_fd = None
    try:
        # use a UNIQUE tmp name per call.  mkstemp returns
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
                # ER-80: skip fsync of the file data when durability=False.
                if durability:
                    os.fsync(f.fileno())
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            tmp_fd = None
            raise
        tmp_fd = None  # the with-block closed the fd

        # os.replace is atomic and does NOT follow symlinks on the target.
        os.replace(str(tmp_path), str(target))

        # fsync the parent directory so the rename is durable.
        # POSIX-only -- Windows has no equivalent.  Best-effort.
        # ER-80: skip when durability=False (the rename still happens,
        # but its durability across power loss is not guaranteed).
        if durability and not is_windows():
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
        # XZ-R10-01: split the try so the deliberate reparse-point raise
        # is NOT caught by the tolerant except (which previously swallowed
        # it, making the Windows reparse-point protection dead code).
        try:
            stat_result = os.lstat(str(p)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
        except (AttributeError, OSError):
            attrs = 0
        if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise OSError(f"SEC-002: refusing to follow reparse point: {p}")
        with open(p, encoding=encoding) as f:
            stat_before = os.fstat(f.fileno())
            content = f.read()
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


class PersistedJSON:
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
    """

    def __init__(self, path: Path, *, default: Any = None) -> None:
        self._path = Path(path)
        self._default = default
        self._bak_path = self._path.with_name(self._path.name + ".bak")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def default(self) -> Any:
        return self._default

    def load(self) -> Any:
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
        """
        if not self._path.exists():
            return self._default
        try:
            raw = _secure_read_text(self._path, encoding="utf-8")
            return json.loads(raw)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.warning(
                "[PERSISTED_JSON] Failed to load %s: %s — quarantining corrupt file and returning default",
                self._path,
                exc,
            )
            self._quarantine_corrupt()
            return self._default

    def _quarantine_corrupt(self) -> None:
        """Best-effort rename the corrupt file to ``<path>.corrupt-<ts>``.

        Mirrors ``crash_recovery.py:_quarantine_corrupt``: if a corrupt
        file with the same timestamp already exists (extremely unlikely
        — would need two corruptions within the same second), disambiguate
        with a counter.  Best-effort — never raises.  If the file
        disappeared between the ``exists()`` check and now, or the
        rename fails (cross-device, permissions), the failure is logged
        at debug level and swallowed so the caller's load still returns
        the default cleanly.
        """
        try:
            if not self._path.exists():
                return
            ts = int(time.time())
            corrupt_path = self._path.with_name(f"{self._path.name}.corrupt-{ts}")
            counter = 0
            while corrupt_path.exists():
                counter += 1
                corrupt_path = self._path.with_name(f"{self._path.name}.corrupt-{ts}.{counter}")
            self._path.rename(corrupt_path)
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

    def save(self, data: Any) -> None:
        """Atomic save.  Creates ``.bak`` before overwrite.  Sets 0o600 perms.

        Parameters
        ----------
        data : Any
            JSON-serialisable payload.  ``json.dumps(data, indent=2,
            ensure_ascii=False)`` is used so non-ASCII characters
            survive the round-trip (mirrors ``vocabulary.py`` and
            ``templates.py`` which both pass ``ensure_ascii=False``).

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
          (e.g. ``test_vocabulary_history_db_fixes.py``'s XV-88 retry
          tests).  Lazy import avoids the circular import that a
          module-level ``from voice_typer.server.config import ...``
          would create (``config`` itself imports from
          ``secure_file_io``).
        """
        # Lazy import so monkeypatches on
        # ``voice_typer.server.config._secure_atomic_write`` are
        # observed at call time (mirrors the pre-existing lazy import
        # in ``vocabulary._save_user`` and ``templates._save``).
        from voice_typer.server.config import _secure_atomic_write

        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2, ensure_ascii=False)
        content_bytes = content.encode("utf-8")

        # Best-effort single-slot .bak before overwrite.
        if self._path.exists():
            try:
                existing_bytes = self._path.read_bytes()
                if existing_bytes != content_bytes:
                    self._bak_path.write_bytes(existing_bytes)
                    _chmod_owner_only(self._bak_path)
            except OSError as e:
                log.debug(
                    "[PERSISTED_JSON] Failed to back up %s to %s: %s",
                    self._path,
                    self._bak_path,
                    e,
                )

        _secure_atomic_write(self._path, content)
        _chmod_owner_only(self._path)
