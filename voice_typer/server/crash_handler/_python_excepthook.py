"""Python-level excepthook + VEH install/remove."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime

log = logging.getLogger(__name__)

# wall-clock budget for the per-handler ``flush()`` loop in
_FLUSH_LOOP_BUDGET_S = 0.5


def _format_redacted_traceback(exc_tb) -> str:
    """Format a traceback with PII-safe fields only."""
    if exc_tb is None:
        return ""
    try:
        frames = traceback.extract_tb(exc_tb)
    except Exception:
        return ""
    if not frames:
        return ""
    lines = ["Traceback (most recent call last):"]
    for frame in frames:
        try:
            basename = os.path.basename(frame.filename) if frame.filename else "<unknown>"
            lineno = frame.lineno if frame.lineno is not None else 0
            func = frame.name or "<unknown>"
        except Exception:
            continue
        lines.append(f'  File "{basename}", line {lineno}, in {func}')
    return "\n".join(lines)


def _get_active_asr_backend() -> str:
    """Best-effort lookup of the active ASR backend (DISK READ).

    Returns the backend name (e.g. ``"whisper"``, ``"parakeet"``,
    """
    try:
        from voice_typer.server.config import Config

        cfg = Config.load()
        return str(getattr(cfg, "asr_backend", "<unknown>"))
    except Exception:
        return "<unknown>"


def _refresh_cached_asr_backend() -> str:
    """Refresh the cached active ASR backend (DISK READ)."""
    from voice_typer.server import crash_handler as _ch

    try:
        backend = _get_active_asr_backend()
    except Exception:
        backend = "<unknown>"
    _ch._cached_active_backend = backend
    return backend


def _get_cached_asr_backend() -> str:
    """Return the cached active ASR backend (NO DISK I/O)."""
    from voice_typer.server import crash_handler as _ch

    cached = getattr(_ch, "_cached_active_backend", None)
    if cached:
        return cached
    with contextlib.suppress(Exception):
        return _refresh_cached_asr_backend()
    return "<unknown>"


def _safe_redact_fallback(value: str) -> str:
    """Guaranteed-safe redaction fallback ()."""
    try:
        import hashlib

        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"<redacted:sha256:{digest}>"
    except Exception:
        return "<redacted:imports-failed>"


def _redact_exc_value(value: str) -> str:
    """Redact ``exc_value`` text through ``redact_pii`` + ``redact_secret``."""
    try:
        from voice_typer.server._secrets import redact_secret
        from voice_typer.server.security import redact_pii

        return redact_secret(redact_pii(value), aggressive=True)
    except Exception:
        return _safe_redact_fallback(value)


def _get_secure_atomic_write():
    """Resolve the secure atomic-write callable, or None on import failure."""
    try:
        from voice_typer.server.config import _secure_atomic_write

        return _secure_atomic_write
    except Exception:
        return None


def _write_crash_marker(exc_type, exc_value, exc_tb, thread_name: str | None) -> None:
    """Write a ``python_crash.<PID>[.<thread>].txt`` marker file."""
    from voice_typer.server import crash_handler as _ch

    if _ch._python_crash_dir is None:
        return

    with contextlib.suppress(Exception):
        # Resolve the marker path + thread-name-for-content based on
        if thread_name is None:
            thread_name_for_content = threading.current_thread().name
            marker_path = _ch._python_crash_dir / f"python_crash.{os.getpid()}.txt"
        else:
            thread_name_for_content = thread_name
            safe_thread_name = _sanitize_thread_name_for_filename(thread_name)
            marker_path = _ch._python_crash_dir / f"python_crash.{os.getpid()}.{safe_thread_name}.txt"

        timestamp = datetime.now().isoformat()
        # Truncate + redact exc_value so user speech and secrets
        _raw_value = str(exc_value)[:200] if exc_value is not None else "None"
        _safe_value = _redact_exc_value(_raw_value)
        # Redacted traceback (file basenames + line numbers +
        _traceback_text = _format_redacted_traceback(exc_tb)
        # Static context for triage, app/python/OS version + active
        try:
            import voice_typer

            _app_version = getattr(voice_typer, "__version__", "<unknown>")
        except Exception:
            _app_version = "<unknown>"
        try:
            _python_version = sys.version
        except Exception:
            _python_version = "<unknown>"
        try:
            _os_version = platform.platform()
        except Exception:
            _os_version = "<unknown>"
        _asr_backend = _get_cached_asr_backend()
        content_lines = [
            f"exc_type={exc_type.__name__ if exc_type is not None else 'Unknown'}",
            f"exc_value={_safe_value}",
            f"thread={thread_name_for_content}",
            f"timestamp={timestamp}",
            # Static triage context.
            f"app_version={_app_version}",
            f"python_version={_python_version}",
            f"os_version={_os_version}",
            f"asr_backend={_asr_backend}",
        ]
        # Append the redacted traceback unconditionally so support
        if _traceback_text:
            content_lines.append("")
            content_lines.append(_traceback_text)
        content = "\n".join(content_lines) + "\n"
        # atomic-write import is decoupled from the redaction
        _atomic_write = _get_secure_atomic_write()
        if _atomic_write is not None:
            # durability=False, the crash marker is a
            _atomic_write(marker_path, content, durability=False)
        else:
            # defensive ``os.chmod`` after the write retroactively
            try:
                fd = os.open(
                    str(marker_path),
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,
                )
                try:
                    os.write(fd, content.encode("utf-8"))
                finally:
                    os.close(fd)
                # Belt-and-braces: even if the umask was loose on
                with contextlib.suppress(OSError):
                    os.chmod(marker_path, 0o600)
            except OSError:
                # Last-resort: Path.write_text preserves the
                marker_path.write_text(content, encoding="utf-8")
                with contextlib.suppress(OSError):
                    os.chmod(marker_path, 0o600)


def _crash_excepthook(exc_type, exc_value, exc_tb) -> None:
    """Custom sys.excepthook for unhandled Python exceptions."""
    from voice_typer.server import crash_handler as _ch

    with contextlib.suppress(Exception):
        # Log ONLY ``exc_type.__name__`` at CRITICAL, never
        log.critical(
            "[CRASH] Unhandled Python exception: %s",
            exc_type.__name__ if exc_type is not None else "Unknown",
        )
        # Emit the PII-safe redacted traceback UNCONDITIONALLY so
        if exc_tb is not None:
            try:
                redacted_tb = _format_redacted_traceback(exc_tb)
                if redacted_tb:
                    log.critical("[CRASH] Redacted traceback (PII-safe):\n%s", redacted_tb)
            except Exception:
                pass  # Never let traceback formatting crash the excepthook
        # Full UNREDACTED traceback only when VOICE_TYPER_DEBUG=1
        if os.environ.get("VOICE_TYPER_DEBUG", "") == "1":
            log.critical("[CRASH] Full traceback (VOICE_TYPER_DEBUG=1)", exc_info=(exc_type, exc_value, exc_tb))
    # Write a python_crash.<PID>.txt marker so the next session's
    _write_crash_marker(exc_type, exc_value, exc_tb, thread_name=None)
    if _ch._original_excepthook is not None and _ch._original_excepthook is not _crash_excepthook:
        with contextlib.suppress(Exception):
            _ch._original_excepthook(exc_type, exc_value, exc_tb)
    # bound the per-handler ``flush()`` loop by a wall-clock
    _flush_start = time.perf_counter()
    for handler in logging.getLogger("voice_typer").handlers:
        if time.perf_counter() - _flush_start > _FLUSH_LOOP_BUDGET_S:
            log.warning(
                "[CRASH] flush loop exceeded %.2fs budget; skipping remaining handlers (crash marker already written)",
                _FLUSH_LOOP_BUDGET_S,
            )
            break
        with contextlib.suppress(Exception):
            handler.flush()


def install_python_excepthook() -> None:
    """Install the custom sys.excepthook. Idempotent."""
    from voice_typer.server import crash_handler as _ch

    # refresh the cache on every call (cheap disk read, runs
    with contextlib.suppress(Exception):
        _refresh_cached_asr_backend()
    if sys.excepthook is _crash_excepthook:
        return
    _ch._original_excepthook = sys.excepthook
    sys.excepthook = _crash_excepthook


def _sanitize_thread_name_for_filename(name: str) -> str:
    """Map a thread name to a filename-safe token."""
    if not name:
        return "thread"
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    safe = safe.strip("_") or "thread"
    return safe[:40]


def _thread_crash_excepthook(args) -> None:
    """Custom ``threading.excepthook`` for unhandled thread exceptions."""
    from voice_typer.server import crash_handler as _ch

    # Defensive: ``threading.ExceptHookArgs`` is a namedtuple, but a
    try:
        exc_type = args.exc_type
        exc_value = args.exc_value
        exc_tb = args.exc_traceback
        thread = getattr(args, "thread", None)
    except AttributeError:
        return  # nothing we can safely do; bail out silently

    # Resolve the thread name defensively, ``thread`` may be None or
    thread_name = "thread"
    with contextlib.suppress(Exception):
        if thread is not None:
            thread_name = thread.name or "thread"

    with contextlib.suppress(Exception):
        # Log ONLY ``exc_type.__name__`` at CRITICAL, never
        type_name = exc_type.__name__ if exc_type is not None else "Unknown"
        log.critical(
            "[CRASH] Unhandled exception in thread %r: %s",
            thread_name,
            type_name,
        )
        # PII-safe redacted traceback, same pipeline as the main hook.
        if exc_tb is not None:
            try:
                redacted_tb = _format_redacted_traceback(exc_tb)
                if redacted_tb:
                    log.critical("[CRASH] Redacted traceback (PII-safe):\n%s", redacted_tb)
            except Exception:
                pass  # Never let traceback formatting crash the hook
        if os.environ.get("VOICE_TYPER_DEBUG", "") == "1":
            log.critical(
                "[CRASH] Full traceback (VOICE_TYPER_DEBUG=1)",
                exc_info=(exc_type, exc_value, exc_tb),
            )

    # Write a python_crash.<PID>.<thread_name>.txt marker so the next
    _write_crash_marker(exc_type, exc_value, exc_tb, thread_name=thread_name)

    # Chain to the previously-installed threading.excepthook (typically
    original = getattr(_ch, "_original_threading_excepthook", None)
    if original is not None and original is not _thread_crash_excepthook:
        with contextlib.suppress(Exception):
            original(args)
    # Flush all handlers so the CRITICAL record lands on disk before
    _flush_start = time.perf_counter()
    for handler in logging.getLogger("voice_typer").handlers:
        if time.perf_counter() - _flush_start > _FLUSH_LOOP_BUDGET_S:
            log.warning(
                "[CRASH] flush loop exceeded %.2fs budget; skipping remaining handlers (crash marker already written)",
                _FLUSH_LOOP_BUDGET_S,
            )
            break
        with contextlib.suppress(Exception):
            handler.flush()


def install_threading_excepthook() -> None:
    """Install the custom ``threading.excepthook``. Idempotent."""
    import threading

    from voice_typer.server import crash_handler as _ch

    # refresh the cache on every call (cheap disk read, runs
    with contextlib.suppress(Exception):
        _refresh_cached_asr_backend()
    if threading.excepthook is _thread_crash_excepthook:
        # Already installed. If _original is still None (e.g. installed
        if _ch._original_threading_excepthook is None:
            with contextlib.suppress(Exception):
                _ch._original_threading_excepthook = threading.__excepthook__
        return
    _ch._original_threading_excepthook = threading.excepthook
    threading.excepthook = _thread_crash_excepthook


def remove_threading_excepthook() -> None:
    """Restore the original ``threading.excepthook``. Idempotent."""
    import threading

    from voice_typer.server import crash_handler as _ch

    original = getattr(_ch, "_original_threading_excepthook", None)
    if original is not None:
        threading.excepthook = original
        _ch._original_threading_excepthook = None  # type: ignore[assignment]
    elif threading.excepthook is _thread_crash_excepthook:
        # install was called via a test path that didn't track
        threading.excepthook = threading.__excepthook__


def remove_python_excepthook() -> None:
    """Restore the original ``sys.excepthook``. Idempotent."""
    from voice_typer.server import crash_handler as _ch

    original = getattr(_ch, "_original_excepthook", None)
    if original is not None:
        sys.excepthook = original
        _ch._original_excepthook = None  # type: ignore[assignment]
    elif sys.excepthook is _crash_excepthook:
        # install was called via a test path that didn't track
        sys.excepthook = sys.__excepthook__


def install_crash_handler() -> bool:
    """Install the Windows Vectored Exception Handler."""
    from voice_typer.server import crash_handler as _ch

    if _ch._handler_handle is not None:
        return True
    if sys.platform != "win32":
        return False

    try:
        _ch._ensure_kernel32()

        from ctypes import wintypes

        add_veh = _ch._kernel32.AddVectoredExceptionHandler
        add_veh.argtypes = [wintypes.ULONG, ctypes.c_void_p]
        add_veh.restype = ctypes.c_void_p

        handler_ptr = add_veh(1, _ch._vectored_handler)
        if handler_ptr:
            _ch._handler_handle = handler_ptr
            log.info(
                "[CRASH] Windows VEH installed, will capture silent crashes "
                "(heap corruption, access violation, stack overrun)"
            )
            return True
        else:
            log.warning("[CRASH] AddVectoredExceptionHandler failed")
            return False
    except Exception as exc:
        log.warning("[CRASH] Failed to install VEH: %s", exc)
        return False


def remove_crash_handler() -> None:
    """Remove the VEH handler. Idempotent."""
    from voice_typer.server import crash_handler as _ch

    if _ch._handler_handle is None or sys.platform != "win32":
        _ch._handler_handle = None
        return
    with contextlib.suppress(Exception):
        from ctypes import wintypes

        remove_veh = _ch._kernel32.RemoveVectoredExceptionHandler
        remove_veh.argtypes = [ctypes.c_void_p]
        remove_veh.restype = wintypes.ULONG
        remove_veh(_ch._handler_handle)
    _ch._handler_handle = None
    log.debug("[CRASH] VEH removed")
