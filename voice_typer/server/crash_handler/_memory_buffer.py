"""In-memory log ring buffer attached to the ``voice_typer`` root logger."""

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
    """MemoryHandler with a fail-closed, self-healing PII filter.

    The PII redaction filter is attached LAZILY: the
    ``voice_typer.server.security`` import is retried on the first
    record that hits the handler, so a transient import failure (e.g. a
    circular import during early bootstrap, or an interpreter-teardown
    import failure) self-heals as soon as the security module becomes
    importable.

    The filter is a REAL redaction pass for this handler: ``handle``
    applies the handler's filter chain (stdlib ``Handler.handle``
    semantics, ``self.filter(record)`` before the record is emitted)
    before appending the record to the ring buffer, so the buffered
    ``LogRecord`` is mutated in place by ``PIIRedactionFilter`` no
    matter how the record reached the buffer. The crash buffer's
    redaction therefore does NOT depend on the rotating file handler
    earlier in the ``voice_typer`` logger's handler chain having
    already redacted the shared record (that file-handler pass still
    runs first in production and its in-place mutation is idempotent —
    the filter's own ``redacted_msg`` guard skips a redundant re-scan
    of the same record, but a handler reorder or level change can no
    longer ship unredacted PII into ``lausu-crash-buffer.log``).
    A filter that returns False vetoes the record (dropped, not
    buffered), matching the drop-on-veto contract every stdlib handler
    honours.

    Fail-closed: if the filter still cannot be attached when a record
    arrives, the record is DROPPED (``handle`` returns False) rather
    than buffered, we lose the crash-buffer tail rather than risk
    persisting unredacted PII to ``lausu-crash-buffer.log`` when
    the VEH callback flushes the buffer.

    A WARNING is logged on the FIRST attach failure so operators see
    the degradation; subsequent failures log at DEBUG (the import is
    retried on every record, so a warning per record would be spam).
    """

    _pii_attached: bool = False
    _pii_failed_once: bool = False

    def _ensure_pii_filter(self) -> bool:
        """Attach ``PIIRedactionFilter`` lazily; True once attached.

        Returns True when the filter is (now) attached, the record
        """
        if self._pii_attached:
            return True
        # The attach is guarded by the handler's (reentrant) lock so two
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
                        "[CRASH-BUF] PIIRedactionFilter unavailable (%s), "
                        "crash-buffer records will be DROPPED (fail-closed) "
                        "until the import succeeds",
                        exc,
                    )
                else:
                    log.debug(
                        "[CRASH-BUF] PIIRedactionFilter still unavailable (%s), "
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
        # Apply the handler's filter chain FIRST, stdlib
        rv = self.filter(record)
        if not rv:
            return False
        if isinstance(rv, logging.LogRecord):
            record = rv
        # TRUE in-memory ring buffer: append the record, and when the
        self.acquire()
        try:
            self.buffer.append(record)
            if len(self.buffer) > self.capacity:
                # Discard the oldest (ring-buffer eviction).
                del self.buffer[0]
        finally:
            self.release()
        # ``rv`` is truthy here (the veto branch above already
        return True

    def close(self) -> None:
        """Drop the buffer WITHOUT flushing it to the target."""
        self.acquire()
        try:
            self.buffer.clear()
            self.target = None
        finally:
            self.release()
        super().close()


# capacity of the in-memory ring buffer. ``_CrashBufferMemoryHandler``
_MEMORY_HANDLER_CAPACITY: int = 200


def install_memory_buffer(config_dir: Path) -> None:
    """Attach the MemoryHandler ring buffer to the ``voice_typer`` logger."""
    from voice_typer.server import crash_handler as _ch

    try:
        resolved = Path(config_dir).resolve()
    except Exception:
        log.debug("[CRASH-BUF] failed to resolve config_dir", exc_info=True)
        return

    # Reuse _SecureTruncatingFileHandler from the sibling ``log`` package
    from voice_typer.server.log import _SecureTruncatingFileHandler, get_logs_dir

    # Build (or rebuild) the target RotatingFileHandler. The target is
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        # O1: the crash-buffer log lives under ``logs/`` alongside
        logs_dir = get_logs_dir(resolved)
        logs_dir.mkdir(parents=True, exist_ok=True)
        buffer_path = logs_dir / "lausu-crash-buffer.log"
        target_handler = _SecureTruncatingFileHandler(
            buffer_path,
            maxBytes=1 * 1024 * 1024,  # 1 MiB, small buffer file
            backupCount=0,  # single-file policy: truncate in place, never .1
            encoding="utf-8",
            errors="backslashreplace",
            delay=True,  # only create the file on first actual write (crash flush)
        )
        # Tighten perms on POSIX so the crash buffer (which contains
        if os.name == "posix":
            with __import__("contextlib").suppress(OSError):
                os.chmod(buffer_path, 0o600)
        # Use the same formatter as the main log file so the records
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
    existing = getattr(_ch, "_memory_handler", None)
    if existing is None:
        try:
            memory_handler = _CrashBufferMemoryHandler(
                capacity=_MEMORY_HANDLER_CAPACITY,
                target=None,  # set below
            )
            # MemoryHandler defaults to flushing on every record once
            memory_handler.flushLevel = logging.CRITICAL + 1
            # CRITICAL: ``flushOnClose`` must be False. Python's
            memory_handler.flushOnClose = False
            memory_handler.setLevel(logging.INFO)
            memory_handler.target = target_handler
            # The PII redaction filter is attached LAZILY (and
            voice_typer_root = logging.getLogger("voice_typer")
            # Avoid duplicate MemoryHandler attachments across repeated
            voice_typer_root.addHandler(memory_handler)
            _ch._memory_handler = memory_handler
        except Exception:
            log.debug("[CRASH-BUF] failed to attach MemoryHandler", exc_info=True)
    else:
        # Update the existing MemoryHandler's target so a config-dir
        existing.target = target_handler


def flush_memory_handler() -> None:
    """Best-effort flush of the in-memory log buffer to the crash file."""
    try:
        from voice_typer.server import crash_handler as _ch

        memory_handler = getattr(_ch, "_memory_handler", None)
        if memory_handler is None:
            return
        # ``MemoryHandler.flush`` pushes the buffered records to the
        memory_handler.flush()
    except Exception:
        # Swallow everything, the VEH callback must not raise. The
        pass


def uninstall_memory_buffer() -> None:
    """Remove the MemoryHandler from the voice_typer logger."""
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
