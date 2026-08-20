"""In-memory log ring buffer attached to the ``voice_typer`` root logger.

The VEH callback (:func:`voice_typer.server.crash_handler._veh_callback.
_vectored_handler_impl`) writes only minimal crash metadata (timestamp,
exception code, address, pid, tid, friendly name) plus the pre-computed
static header. The actual log records that led up to the crash are NOT
included — the rotating file log is on disk and would require reading
back during the VEH callback, which is unsafe during heap corruption.

This module wires a :class:`logging.handlers.MemoryHandler` (capacity
200 records) to the ``voice_typer`` root logger at ``INFO`` level. The
handler's target is a dedicated
:class:`logging.handlers.RotatingFileHandler` writing to
``<config_dir>/logs/voice-typer-crash-buffer.log`` (O1). The
MemoryHandler holds the most-recent 200 records in a bounded ring
buffer; it does NOT emit them to the target on every record (the target
is only used when the buffer is flushed explicitly).

When the VEH callback fires (after writing the crash-diagnostics body),
it calls :func:`flush_memory_handler` — a best-effort flush that pushes
the buffered records into ``voice-typer-crash-buffer.log``. The flush
is wrapped in ``try/except`` so a failure inside the crashing process
does not propagate back into the VEH callback.

Limitations (documented):
- For ``STATUS_HEAP_CORRUPTION`` (0xC0000374) the heap is corrupted and
  ANY Python call may fail or deadlock. The flush attempt is still made
  (best-effort) but may silently fail — the buffer is lost in that case.
  This is acceptable because (a) the VEH callback already may fail to
  write its own diagnostics for heap-corruption crashes, and (b) for the
  common case (access violation, stack overrun) the flush works
  reliably.

- The MemoryHandler buffer lives in process memory and is lost on
  ``os._exit`` / SIGKILL. The fast-cleanup path
  (:meth:`ShutdownController._do_fast_cleanup`) calls ``os._exit(0)``
  before the VEH callback fires for Windows logoff/shutdown, so the
  buffer is lost in that path too. This is documented and accepted —
  the fast path runs ONLY on Windows logoff/shutdown where the OS is
  force-killing us within ~5s; the rotating file log already covers
  that case (it was flushed on every record by
  :class:`_SecureTruncatingFileHandler`).

Architecture: this module is intentionally minimal — it owns the
MemoryHandler + target RotatingFileHandler lifecycle. The mutable
state (``_memory_handler``, ``_crash_buffer_handler``) lives on the
``crash_handler`` facade module so test mutations propagate, mirroring
the pattern used by the other crash_handler submodules. Functions here
access state via ``_ch.<name>``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class _CrashBufferMemoryHandler(logging.handlers.MemoryHandler):
    """MemoryHandler with a fail-closed, self-healing PII filter (HU-8).

    The PII redaction filter is attached LAZILY: the
    ``voice_typer.server.security`` import is retried on the first
    record that hits the handler, so a transient import failure (e.g. a
    circular import during early bootstrap, or an interpreter-teardown
    import failure) self-heals as soon as the security module becomes
    importable.

    Fail-closed: if the filter still cannot be attached when a record
    arrives, the record is DROPPED (``handle`` returns False) rather
    than buffered — we lose the crash-buffer tail rather than risk
    persisting unredacted PII to ``voice-typer-crash-buffer.log`` when
    the VEH callback flushes the buffer.

    A WARNING is logged on the FIRST attach failure so operators see
    the degradation; subsequent failures log at DEBUG (the import is
    retried on every record, so a warning per record would be spam).
    """

    _pii_attached: bool = False
    _pii_failed_once: bool = False

    def _ensure_pii_filter(self) -> bool:
        """Attach ``PIIRedactionFilter`` lazily; True once attached.

        Returns True when the filter is (now) attached — the record
        may be buffered. Returns False when the import still fails —
        the caller drops the record (fail-closed).
        """
        if self._pii_attached:
            return True
        # The attach is guarded by the handler's (reentrant) lock so two
        # concurrent ``handle()`` calls that both observe
        # ``_pii_attached=False`` cannot BOTH import + ``addFilter`` and
        # double-attach the filter. ``Handler.lock`` is an RLock, so the
        # nested ``super().handle`` acquire inside ``handle`` is safe.
        self.acquire()
        try:
            if self._pii_attached:
                return True
            try:
                from voice_typer.server.security import PIIRedactionFilter

                self.addFilter(PIIRedactionFilter())
            except Exception as exc:
                if not self._pii_failed_once:
                    self._pii_failed_once = True
                    log.warning(
                        "[CRASH-BUF] PIIRedactionFilter unavailable (%s) — "
                        "crash-buffer records will be DROPPED (fail-closed) "
                        "until the import succeeds",
                        exc,
                    )
                else:
                    log.debug(
                        "[CRASH-BUF] PIIRedactionFilter still unavailable (%s) — "
                        "dropping crash-buffer record (fail-closed)",
                        exc,
                    )
                return False
            self._pii_attached = True
            return True
        finally:
            self.release()

    def handle(self, record: logging.LogRecord) -> bool:
        if not self._ensure_pii_filter():
            return False
        # TRUE in-memory ring buffer: append the record, and when the
        # buffer exceeds capacity DROP the oldest record in memory.
        # Crucially we do NOT call ``super().handle`` — the stock
        # ``MemoryHandler`` evicts the oldest by flushing the whole
        # buffer to the target (writing to disk), which duplicates the
        # main ``voice-typer.log``.  The crash buffer must keep the
        # last ``capacity`` records ONLY in memory and write them to
        # disk solely on the explicit ``flush_memory_handler()`` call
        # from the VEH callback.
        self.acquire()
        try:
            self.buffer.append(record)
            if len(self.buffer) > self.capacity:
                # Discard the oldest (ring-buffer eviction).
                del self.buffer[0]
            return True
        finally:
            self.release()

    def close(self) -> None:
        """Drop the buffer WITHOUT flushing it to the target.

        The stock ``MemoryHandler.close()`` flushes the whole buffer to
        the target file.  Because the logging framework calls
        ``close()`` on every handler at interpreter shutdown, that would
        dump the last ``capacity`` records into
        ``voice-typer-crash-buffer.log`` on EVERY clean exit — mirroring
        the tail of ``voice-typer.log`` for a session that never crashed.

        The crash buffer's ONLY writer is the VEH callback's explicit
        ``flush_memory_handler()`` call (a real crash).  On clean
        shutdown we discard the ring instead of persisting it.
        """
        self.acquire()
        try:
            self.buffer.clear()
            self.target = None
        finally:
            self.release()
        super().close()


# capacity of the in-memory ring buffer. ``_CrashBufferMemoryHandler``
# overrides ``handle`` to append + evict in memory (dropping the oldest
# record past capacity) WITHOUT writing to disk — the target is only
# written by the explicit ``flush_memory_handler()`` call from the VEH
# callback.  The buffer therefore retains the most-recent 200 records
# until a crash triggers the flush.
#
# 200 records is roughly 30-60s of normal voice-typer log traffic at
# the default INFO verbosity (a mix of bubble-level pushes filtered out
# of the file handler, hotkey registrations, lifecycle events). Enough
# to capture the lead-up to a crash without bloating process memory.
_MEMORY_HANDLER_CAPACITY: int = 200


def install_memory_buffer(config_dir: Path) -> None:
    """Attach the MemoryHandler ring buffer to the ``voice_typer`` logger.

    Idempotent: calling it twice with the same config_dir replaces the
    existing target (so a config-dir migration correctly re-points the
    RotatingFileHandler at the new location). Calling it twice with a
    different config_dir re-opens the target file at the new path.

    The MemoryHandler itself is attached to the ``voice_typer`` root
    logger ONCE; subsequent calls only update the target's file path.
    This avoids accumulating duplicate MemoryHandlers on the logger
    across repeated ``set_crash_handler_config_dir`` calls (e.g. in
    tests).

    Parameters
    ----------
    config_dir:
        The voice-typer config directory. The crash-buffer log file
        lives at ``<config_dir>/logs/voice-typer-crash-buffer.log``
        (O1) — co-located with ``voice-typer.log`` so the support
        engineer triaging a crash sees both files in the same
        directory.
    """
    from voice_typer.server import crash_handler as _ch

    try:
        resolved = Path(config_dir).resolve()
    except Exception:
        log.debug("[CRASH-BUF] failed to resolve config_dir", exc_info=True)
        return

    # Reuse _SecureTruncatingFileHandler from the sibling ``log`` package
    # so the crash-buffer file inherits the same 0o600 perms and
    # inter-process rotation lock as the main rotating log. The import
    # is from a sibling package (``voice_typer.server.log``) and is
    # REQUIRED — there is deliberately NO insecure fallback to a stock
    # ``RotatingFileHandler``. If the import fails the exception
    # propagates to the caller (``set_crash_handler_config_dir``
    # suppresses it via ``contextlib.suppress(Exception)``), leaving
    # the crash buffer uninstalled. The crash buffer is a nice-to-have
    # diagnostic aid, not a critical-path component; losing it is
    # preferable to silently writing crash records to a world-readable
    # handler that lacks 0o600 perms and the inter-process rotation
    # lock (a stock ``RotatingFileHandler`` re-opens rotated files with
    # the process umask, which is typically 0o022 — world-readable).
    from voice_typer.server.log import _SecureTruncatingFileHandler

    # Build (or rebuild) the target RotatingFileHandler. The target is
    # replaced on every call so a config-dir migration re-points the
    # file at the new location. The previous target (if any) is closed
    # to release its file handle.
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        # O1: the crash-buffer log lives under ``logs/`` alongside
        # ``voice-typer.log`` (both are PII-redacted support files).
        logs_dir = resolved / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        buffer_path = logs_dir / "voice-typer-crash-buffer.log"
        target_handler = _SecureTruncatingFileHandler(
            buffer_path,
            maxBytes=1 * 1024 * 1024,  # 1 MiB — small buffer file
            backupCount=0,  # single-file policy: truncate in place, never .1
            encoding="utf-8",
            errors="backslashreplace",
        )
        # Tighten perms on POSIX so the crash buffer (which contains
        # the same PII-redacted log records as voice-typer.log) is not
        # world-readable.
        if os.name == "posix":
            with __import__("contextlib").suppress(OSError):
                os.chmod(buffer_path, 0o600)
        # Use the same formatter as the main log file so the records
        # are readable. Fall back to a basic formatter if the log
        # module's _FileFormatter is unavailable.
        try:
            from voice_typer.server.log import _FileFormatter

            target_handler.setFormatter(_FileFormatter())
        except Exception:
            target_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        target_handler.setLevel(logging.DEBUG)
        # Close the previous target (if any) to release its file handle.
        previous_target = getattr(_ch, "_crash_buffer_handler", None)
        if previous_target is not None:
            with __import__("contextlib").suppress(Exception):
                previous_target.close()
        _ch._crash_buffer_handler = target_handler
    except Exception:
        log.debug("[CRASH-BUF] failed to build target RotatingFileHandler", exc_info=True)
        return

    # Attach the MemoryHandler to the voice_typer root logger ONCE.
    # Subsequent calls only update the target on the existing handler
    # (avoids duplicate MemoryHandlers across repeated installs).
    existing = getattr(_ch, "_memory_handler", None)
    if existing is None:
        try:
            memory_handler = _CrashBufferMemoryHandler(
                capacity=_MEMORY_HANDLER_CAPACITY,
                target=None,  # set below
            )
            # MemoryHandler defaults to flushing on every record once
            # the target is set; we explicitly want buffer-only
            # behaviour (flush only on explicit flush() call from the
            # VEH callback). Setting flushLevel to a level never
            # reached (CRITICAL + 1) disables the auto-flush-on-level
            # path. The explicit ``flush()`` call from the VEH callback
            # still works.
            memory_handler.flushLevel = logging.CRITICAL + 1
            memory_handler.setLevel(logging.INFO)
            memory_handler.target = target_handler
            # HU-8: the PII redaction filter is attached LAZILY (and
            # fail-closed) by ``_CrashBufferMemoryHandler`` — the
            # ``voice_typer.server.security`` import is retried on the
            # first record, and records are DROPPED (never buffered
            # unredacted) if the filter still can't be installed.
            # Pre-fix, ``except Exception: pass`` silently swallowed an
            # import failure, leaving the crash buffer unredacted.
            voice_typer_root = logging.getLogger("voice_typer")
            # Avoid duplicate MemoryHandler attachments across repeated
            # install_memory_buffer calls (the dedup check looks for
            # our specific MemoryHandler instance type via the
            # ``_crash_buffer_handler`` attribute marker).
            voice_typer_root.addHandler(memory_handler)
            _ch._memory_handler = memory_handler
        except Exception:
            log.debug("[CRASH-BUF] failed to attach MemoryHandler", exc_info=True)
    else:
        # Update the existing MemoryHandler's target so a config-dir
        # migration re-points the file.
        existing.target = target_handler


def flush_memory_handler() -> None:
    """Best-effort flush of the in-memory log buffer to the crash file.

    Called from the VEH callback after the crash-diagnostics body is
    written. Wraps everything in ``try/except`` so a failure inside the
    crashing process does not propagate back into the VEH callback
    (which must return ``EXCEPTION_CONTINUE_SEARCH`` to the OS).

    For ``STATUS_HEAP_CORRUPTION`` the heap is corrupted and this call
    may silently fail — that's an accepted limitation (see module
    docstring). For access violations / stack overruns (the common
    case), the flush works reliably and the most-recent 200 log records
    are appended to ``voice-typer-crash-buffer.log``.
    """
    try:
        from voice_typer.server import crash_handler as _ch

        memory_handler = getattr(_ch, "_memory_handler", None)
        if memory_handler is None:
            return
        # ``MemoryHandler.flush`` pushes the buffered records to the
        # target (the RotatingFileHandler) and clears the buffer. If
        # the target is None (e.g. set_crash_handler_config_dir was
        # never called), flush is a no-op.
        memory_handler.flush()
    except Exception:
        # Swallow everything — the VEH callback must not raise. The
        # crash-diagnostics body has already been written; losing the
        # log-buffer tail is acceptable.
        pass


def uninstall_memory_buffer() -> None:
    """Remove the MemoryHandler from the voice_typer logger.

    Used by tests that need to reset the logger state between runs.
    Also closes the target RotatingFileHandler so the file handle is
    released (Windows won't let a second test rename / delete the
    file while the handle is open).
    """
    try:
        from voice_typer.server import crash_handler as _ch

        memory_handler = getattr(_ch, "_memory_handler", None)
        if memory_handler is not None:
            with __import__("contextlib").suppress(Exception):
                logging.getLogger("voice_typer").removeHandler(memory_handler)
            target = getattr(memory_handler, "target", None)
            if target is not None:
                with __import__("contextlib").suppress(Exception):
                    target.close()
            _ch._memory_handler = None
        _ch._crash_buffer_handler = None
    except Exception:
        pass


__all__ = [
    "flush_memory_handler",
    "install_memory_buffer",
    "uninstall_memory_buffer",
]
