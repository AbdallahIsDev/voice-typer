"""Logging filters and handler classes for the Voice Typer log package.

Extracted from the original monolithic ``log/__init__.py``. Contains:

- :func:`_stderr_line` / :func:`_quiet_handler_error` — quiet emit-failure
  diagnostics (no stock ``--- Logging error ---`` spam)
- :class:`_SessionFilter` — injects ``session_id`` / ``component``
- :class:`_BubbleLevelExclusionFilter` — keeps high-frequency
  ``bubble_level`` records out of the rotating file
- :class:`_FlushingStreamHandler` — console handler that flushes per emit
- :class:`_SecureTruncatingFileHandler` — single-file in-place truncation
  with inter-process emit/rotation locks + post-truncate chmod

``setup_logging`` resolves :class:`_SecureTruncatingFileHandler` and
other collaborators via the package object at call time so tests that
``monkeypatch.setattr(voice_typer.server.log, ...)`` keep working after
the split (C-ARCH-2 sibling-module late lookup).
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
import threading

# Keep the historical logger name so ``caplog.at_level(...,
# logger="voice_typer.server.log")`` still captures these diagnostics
# after the module split.
log = logging.getLogger("voice_typer.server.log")

_emit_reentrancy = threading.local()
"""Re-entrancy guard for :meth:`_SecureTruncatingFileHandler.emit`.

Set to ``True`` while one ``emit`` holds (or is attempting) the
inter-process emit lock. A nested ``emit`` on the same thread (logging
from inside the emit path, e.g. a lock helper emitting a diagnostic)
writes fail-open WITHOUT acquiring the lock: re-acquiring the same
byte-range lock from the same process deadlocks on Windows
(``msvcrt.locking`` byte locks conflict even intra-process) and is a
no-op at best on POSIX. The flag is thread-local so concurrent
threads still serialize through the lock file.
"""


def _stderr_line(text: str) -> None:
    """Write one plain line to ``sys.stderr`` (best-effort, never raises).

    Used by the handler-error path so a failed record still surfaces a
    concise diagnostic WITHOUT recursing through the logging framework
    (which would re-enter the very handler that just failed).
    """
    try:
        if sys.stderr is None:
            return
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _quiet_handler_error(handler: logging.Handler, record: logging.LogRecord) -> None:  # noqa: D401
    """Replace the stock ``handleError`` with a single concise line.

    Python's default ``Handler.handleError`` prints a multi-line
    ``--- Logging error ---`` header + full traceback to stderr for
    EVERY failed record. Under a console (PowerShell) that interleaves
    stdout/stderr this renders as a bare ``--- Logging error ---`` line
    with no usable information, and the header is emitted for every
    subsequent failure, exactly the garbage the user reported. We emit
    ONE line with the exception class + truncated message instead; the
    traceback stays in the record (JSON mode) and the failure is still
    visible.
    """
    try:
        exc = sys.exc_info()[1]
        detail = f"{type(exc).__name__}" if exc is not None else "unknown"
        if exc is not None:
            msg = str(exc)
            if msg:
                detail = f"{detail}: {msg[:200]}"
        _stderr_line(f"[LOG] {type(handler).__name__} emit failed ({detail})")
    except Exception:
        pass


# ── Session filter ────────────────────────────────────────────────────


class _SessionFilter(logging.Filter):
    """Inject ``session_id`` and ``component`` attributes into every record.

    The ``session_id`` is set once per process by :func:`setup_logging`
    on the package attribute ``voice_typer.server.log._session_id``
    (the canonical store test fixtures snapshot/restore). The
    ``component`` defaults to the logger name so formatters can show
    which module produced the record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            # Late package lookup so writes to
            # ``voice_typer.server.log._session_id`` (setup_logging /
            # test fixture restore) are observed here.
            import voice_typer.server.log as _log_pkg

            record.session_id = getattr(_log_pkg, "_session_id", "")
        if not hasattr(record, "component"):
            record.component = record.name
        return True


class _BubbleLevelExclusionFilter(logging.Filter):
    """ADR-0020 §11: keep ``bubble_level`` records out of the file log.

    The ``bubble_level`` event is a high-frequency (~60 Hz, coalesced
    to ≤30 Hz on the Tauri host per §9) RMS/peak push used only for
    the live waveform bubble. It carries no diagnostic value in the
    rotating file and would dominate the on-disk log. The console/stderr
    handler is unaffected, only the file handler attaches this filter.

    The marker string is the exact event-type token used by the
    ``bubble_level`` push and by ``IPCServer._send``'s high-frequency
    drop log, so any record mentioning it is excluded from the file.

    WARNING+ records are kept unconditionally (cheap path,
    no ``getMessage()`` call) so legitimate error logs mentioning
    ``bubble_level`` are never silently dropped from the file.
    """

    _MARKER = "bubble_level"

    def filter(self, record: logging.LogRecord) -> bool:
        # cheap path, WARNING+ records are always kept (no
        # ``getMessage()`` call) so a legitimate
        # ``"bubble_level handler crashed"``-style error is never
        # dropped from the file. The expensive substring match only
        # runs for DEBUG / INFO records, which is the level the
        # high-frequency bubble push is emitted at, so the
        # noise-suppression behaviour is preserved while protecting
        # diagnostic error lines.
        if record.levelno >= logging.WARNING:
            return True
        # hybrid check. ``record.msg`` is the raw template
        # string (no %-format substitution), checking it first avoids
        # the ``getMessage()`` call on every DEBUG/INFO record. Fall
        # back to ``getMessage()`` only when the record carries
        # positional args (i.e. the marker could appear in the
        # substituted output but not the template). The hot path is a
        # literal ``"bubble_level"`` log call with no args, so the
        # cheap ``in record.msg`` check covers it; the fallback
        # preserves correctness for callers that interpolate the
        # marker via ``%s``.
        if not record.args:
            return self._MARKER not in record.msg
        return self._MARKER not in record.getMessage()


class _FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every emit.

    the standard ``logging.StreamHandler`` only flushes on
    ``close()`` (or when its internal buffer fills up).  For an
    interactive terminal session this means startup logs can sit in
    the buffer for several seconds, making VoiceTyper look like it's
    hanging silently.  Subclassing and calling ``self.flush()`` after
    every ``emit()`` guarantees each log line is written to the
    terminal immediately.

    This subclass is also used as the dedup key in
    :func:`setup_logging` so calling ``setup_logging`` multiple times
    doesn't add duplicate console handlers.

    On Windows, ``FlushFileBuffers`` on a console handle returns
    ``ERROR_INVALID_FUNCTION`` (WinError 1).  That is EXPECTED console
    behaviour, the write already reached the OS (the stream is
    configured ``write_through=True``), so a failing flush is not
    degradation.  ``emit`` swallows flush errors silently and only
    surfaces a diagnostic when the WRITE itself fails (a genuinely
    broken stream).
    """

    # One-shot diagnostic guard: a genuinely broken stream (write
    # failure) is reported once, never per-line.
    _flushed_once: bool = False

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            # Write the record directly instead of delegating to the
            # stock ``StreamHandler.emit``: the stock path calls
            # ``self.flush()`` inside its own try/except and routes a
            # flush exception (WinError 1 on Windows consoles) to
            # ``handleError``, which would emit a spurious "console
            # degraded" diagnostic on every launch.
            self.stream.write(self.format(record) + self.terminator)
        except OSError as exc:
            # On Windows, ``write()`` on a console handle with
            # ``write_through=True`` or ``line_buffering=True`` can raise
            # ``ERROR_INVALID_FUNCTION`` (WinError 1, winerror=1) even
            # when the data WAS written, the raise comes from the
            # underlying flush, not a lost write.  Treat WinError 1
            # on Windows as benign (silent); any other OSError is a
            # genuinely broken stream.
            if os.name == "nt" and getattr(exc, "winerror", None) == 1:
                return
            self._handle_broken_stream()
            return
        except Exception:
            # Only reach here when the WRITE itself failed, a genuinely
            # broken stream (closed fd, EPIPE, etc.).  Emit one
            # diagnostic and keep the handler alive so later writes
            # still reach the buffer.
            self._handle_broken_stream()
            return
        # Best-effort: with ``line_buffering=True`` each newline already
        # flushes; this is belt-and-suspenders and never raised.
        with contextlib.suppress(Exception):
            self.flush()

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802, override of logging.Handler.handleError
        # Safety net: log once, never detach, never spam.
        self._handle_broken_stream()

    def _handle_broken_stream(self) -> None:
        """Log ONE diagnostic about a genuinely broken write stream, then
        keep the handler alive so subsequent writes still reach the
        buffer (they will be flushed at process exit if not before)."""
        if self._flushed_once:
            return
        self._flushed_once = True
        exc = sys.exc_info()[1]
        detail = f"{type(exc).__name__}" if exc is not None else "unknown"
        if exc is not None:
            msg = str(exc)
            if msg:
                detail = f"{detail}: {msg[:200]}"
        _stderr_line(f"[LOG] console stream may be degraded, terminal output may be delayed ({detail})")


class _SecureTruncatingFileHandler(logging.handlers.RotatingFileHandler):
    """``RotatingFileHandler`` subclass that truncates IN PLACE (single-file
    policy) and is inter-process safe AND re-locks perms.

    Combines three concerns:
    1. Single-file policy: ``doRollover`` TRUNCATES the active file in
       place (empties it) when it exceeds ``maxBytes``, a numbered
       backup (``.1``, ``.2``, ...) is NEVER created. The file on disk
       is always exactly one file.
    2. Inter-process truncation safety (``fcntl.flock`` /
       ``msvcrt.locking``) so the main app and prewarm process don't
       race on truncation.
    3. Post-truncation ``os.chmod(self.baseFilename, 0o600)`` on POSIX so
       the active log file is never world-readable.
    4. Inter-process EMIT safety: ``emit`` holds the same lock file
       across the rollover-check + write so two processes sharing one
       log file (the autostart launcher and the backend it spawns both
       write ``voice-typer.log`` during the launch overlap) cannot
       interleave bytes mid-line. On Windows the C runtime emulates
       append mode with seek-then-write, so concurrent writers can
       overwrite each other's bytes and leave fragments (a bare tail
       such as ``dinator`` with no timestamp template). The emit lock
       is acquired NON-BLOCKING and never logs: when contended (or when
       re-entered from inside the emit path) the record is still
       written, fail-open, matching the pre-lock behavior.

    The lock is held only for one record's check + write, NOT across
    records. Rotation re-checks under the lock (another process may
    have truncated while we waited).
    """

    def __init__(self, filename, *args, **kwargs):
        super().__init__(filename, *args, **kwargs)
        self._rotation_lock_path = f"{filename}.lock"

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802, override of logging.Handler.handleError
        _quiet_handler_error(self, record)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        """Write the record under the inter-process emit lock, never losing it.

        The emit lock (the same ``<log>.lock`` file the rotation path
        uses) is held across the rollover-check + write so concurrent
        processes sharing this file cannot interleave bytes mid-line.
        The lock is acquired NON-BLOCKING and SILENTLY (no logging on
        failure: emitting a diagnostic here would recurse into this
        very method): when the lock is contended, or when re-entered
        from inside the emit path (see :data:`_emit_reentrancy`), the
        record is written fail-open without the lock, exactly the
        pre-lock behavior, and a rotation failure still appends the
        record with ONE concise stderr line instead of the stock
        ``--- Logging error ---`` block.
        """
        if getattr(_emit_reentrancy, "active", False):
            self._emit_fail_open(record)
            return
        lock_fd = self._try_acquire_emit_lock()
        _emit_reentrancy.active = True
        try:
            try:
                if self.shouldRollover(record):
                    if lock_fd is not None:
                        # Lock already held: truncate inline without
                        # re-acquiring (a second acquire on a new fd
                        # would self-conflict on Windows). Re-check
                        # first, another process may have truncated
                        # while we acquired.
                        if self._rotation_needed():
                            self._truncate_locked()
                    else:
                        try:
                            self.doRollover()
                        except Exception as exc:  # noqa: BLE001, rotation is best-effort
                            _stderr_line(
                                f"[LOG-SETUP] log rotation failed ({type(exc).__name__}), appending without rotating"
                            )
            except Exception:
                # ``shouldRollover`` itself failed (e.g. broken stream) —
                # still try to write the record.
                pass
            logging.FileHandler.emit(self, record)
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)
        finally:
            self._release_rotation_lock(lock_fd)
            _emit_reentrancy.active = False

    def _emit_fail_open(self, record: logging.LogRecord) -> None:
        """Write *record* without the emit lock (re-entrant path).

        Reached only when ``emit`` recurses on the same thread (see
        :data:`_emit_reentrancy`). Rotation failures still append the
        record with the single concise stderr line.
        """
        try:
            try:
                if self.shouldRollover(record):
                    try:
                        self.doRollover()
                    except Exception as exc:  # noqa: BLE001, rotation is best-effort
                        _stderr_line(
                            f"[LOG-SETUP] log rotation failed ({type(exc).__name__}), appending without rotating"
                        )
            except Exception:
                pass
            logging.FileHandler.emit(self, record)
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

    def _try_acquire_emit_lock(self):
        """Non-blocking silent acquire of the inter-process emit lock.

        Same lock file as the rotation path (``<log>.lock``), but unlike
        :meth:`_acquire_rotation_lock` this NEVER blocks (Windows
        ``LK_LOCK`` would stall every log line ~10s under contention)
        and NEVER logs (a diagnostic here would recurse into
        :meth:`emit`). Returns the lock fd on success, ``None`` when
        contended or on any error: the caller writes fail-open.
        """
        if getattr(_emit_reentrancy, "active", False):
            return None
        fd = None
        try:
            if os.name == "posix":
                import fcntl

                fd = os.open(self._rotation_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                    return None
                return fd
            if os.name == "nt":
                import msvcrt

                fd = os.open(self._rotation_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                with contextlib.suppress(OSError):
                    os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                    return None
                return fd
        except Exception:
            if isinstance(fd, int):
                with contextlib.suppress(OSError):
                    os.close(fd)
        return None

    def _acquire_rotation_lock(self):
        """Open the lock file and acquire an inter-process lock on it."""
        fd = None
        try:
            if os.name == "posix":
                import fcntl

                fd = os.open(self._rotation_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                return fd
            if os.name == "nt":
                import msvcrt

                fd = os.open(self._rotation_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                # ``msvcrt.locking(LK_LOCK)`` blocks ~10s then raises
                # ``PermissionError`` (an ``OSError`` subclass) if it cannot
                # acquire the byte-range lock. The previous ``contextlib.suppress``
                # silently swallowed that, returning ``fd`` as if the lock was
                # held, two processes racing rotation could both pass. We now
                # retry once with ``LK_NBLCK`` (non-blocking), if the holder
                # released the byte during the ~10s block, we grab it
                # instantly; if not, we fail CLOSED (close fd, return None)
                # so the caller's ``_rotation_needed()`` short-circuit kicks
                # in and no rotation is attempted without the inter-process
                # lock. Fail-closed prevents two concurrent rotations from
                # clobbering each other's rename / re-open, which previously
                # could truncate voice-typer.log.
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                except OSError:
                    # LK_LOCK timed out, try a single non-blocking acquire.
                    # If the holder released in the meantime, we succeed
                    # silently (lock is now held, no warning needed).
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    except OSError:
                        # Both LK_LOCK (10s blocking) and LK_NBLCK (instant)
                        # failed, the byte range is contended. Fail CLOSED:
                        # close the fd so it is not leaked, return None so
                        # ``doRollover`` skips rotation (no rotation without
                        # the inter-process lock). Log at WARNING (not DEBUG)
                        # so the operator can see the persistent contention.
                        log.warning(
                            "[LOG-SETUP] Windows rotation lock acquire failed "
                            "(LK_LOCK timed out, LK_NBLCK retry also failed), "
                            "fail-closed: rotation skipped to avoid concurrent "
                            "rotation race"
                        )
                        with contextlib.suppress(OSError):
                            os.close(fd)
                        return None
                return fd
        except Exception as exc:
            # log only the exception class name. ``str(exc)``
            # can include the lock file path (which contains the user's
            # home directory), leaking it to stderr/debug logs is a
            # minor PII/privacy leak. The class name (e.g.
            # ``PermissionError``, ``OSError``) is enough for an
            # operator to diagnose the failure mode without exposing
            # the on-disk path layout.
            log.debug(
                "[LOG-SETUP] inter-process rotation lock acquire failed (%s); falling back to racy rotation",
                type(exc).__name__,
            )
            if isinstance(fd, int):
                with contextlib.suppress(OSError):
                    os.close(fd)
        return None

    def _release_rotation_lock(self, fd) -> None:
        if fd is None:
            return
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except Exception as exc:  # noqa: BLE001, release-path: surface at DEBUG, never propagate during finally
            log.debug(
                "[LOG-SETUP] inter-process rotation lock release failed (%s)",
                type(exc).__name__,
                exc_info=True,
            )
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _rotation_needed(self) -> bool:
        """Re-check whether rotation is still needed after lock acquisition."""
        try:
            return os.path.getsize(self.baseFilename) >= self.maxBytes
        except OSError:
            return True

    def _truncate_locked(self) -> None:
        """Truncate the active log file in place; caller must hold the lock.

        The inter-process lock file is already acquired by the caller
        (either :meth:`doRollover` or :meth:`emit`), so this performs
        only the truncate + post-truncate chmod. Extracted so
        :meth:`emit` can rotate inline without a second (self-deadlocking
        on Windows) lock acquisition.
        """
        # Truncate in place. ``seek(0)`` first so the file position
        # is at the start; ``truncate(0)`` empties it. The next
        # ``emit`` appends from position 0. The file keeps its
        # identity (same path/inode), so the inter-process lock file
        # (``<name>.lock``) and any open handles stay valid.
        #
        # ``stream`` is ``TextIOWrapper | None`` per typeshed (None
        # when the file failed to open, e.g. disk full / perms). A
        # None stream cannot be truncated, skip the truncate (the
        # base ``FileHandler.emit`` raises ``RuntimeError`` on a
        # None stream, surfaced as ONE concise stderr line by
        # ``handleError``) rather than crashing inside the
        # inter-process rotation lock.
        if self.stream is not None:
            self.stream.seek(0)
            self.stream.truncate(0)
        # Belt-and-suspenders: chmod INSIDE the lock so even if a
        # caller bypassed the umask (or a future refactor swapped
        # the open mode), the file is still re-locked to 0o600
        # before any other process can observe it.
        #
        # Logged (not silently suppressed) so an operator can see
        # when the chmod fails: e.g. on NFS with root-squash, on a
        # read-only filesystem, or under a SELinux policy that
        # denies chmod. Log only the exception class name (not
        # ``str(exc)``, which can include the log file path →
        # home-directory leak).
        if os.name == "posix":
            try:
                os.chmod(self.baseFilename, 0o600)
            except OSError as exc:
                log.warning(
                    "[LOG-SETUP] post-truncate chmod to 0o600 failed "
                    "(%s), log file may be world-readable; investigate "
                    "filesystem perms (NFS root-squash, read-only mount, "
                    "SELinux policy)",
                    type(exc).__name__,
                )

    def doRollover(self) -> None:  # noqa: D401, N802
        # Single-file policy: when the active log exceeds ``maxBytes``,
        # TRUNCATE it in place (empty the file) and keep writing to the
        # SAME path.  Numbered backups (``voice-typer.log.1`` ...) are
        # NEVER created, the file on disk is always exactly one file.
        #
        # Short-circuit on the file-size pre-check: if the active log
        # file is under the size cap, no truncation is needed.  We do
        # this BEFORE acquiring the inter-process lock so the no-op path
        # doesn't acquire + release it.  The check is duplicated AFTER
        # lock acquisition in case another process truncated while we
        # were acquiring the lock.
        if not self._rotation_needed():
            return
        lock_fd = self._acquire_rotation_lock()
        try:
            if not self._rotation_needed():
                return
            self._truncate_locked()
        finally:
            self._release_rotation_lock(lock_fd)
