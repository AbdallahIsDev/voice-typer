"""Python-level excepthook + VEH install/remove.

``_format_redacted_traceback`` formats a traceback with PII-safe fields
only (file basename, line number, function name — no source line, no
argument values) so the ``python_crash.<PID>.txt`` marker file carries
no PII.

``_get_active_asr_backend`` is a best-effort lookup of the active ASR
backend (reads from the persisted Config, not the live registry, so it
works during interpreter shutdown).

``_crash_excepthook`` is the custom ``sys.excepthook``: logs the
exception at CRITICAL (with redaction), writes a
``python_crash.<PID>.txt`` marker file (so the next session's
``report_pending_crash`` can surface it), and chains to the original
hook.

``install_python_excepthook`` / ``install_crash_handler`` /
``remove_crash_handler`` wire up the Python excepthook and the Windows
VEH respectively. Both are no-ops on non-Windows (VEH path) and
idempotent (both paths).

Split out from the original monolithic ``crash_handler.py``.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import platform
import sys
import threading
import traceback
from datetime import datetime

log = logging.getLogger(__name__)


def _format_redacted_traceback(exc_tb) -> str:
    """Format a traceback with PII-safe fields only.

    Each frame is rendered as::

        File "<basename>", line <N>, in <func_name>

    The full source line (which can contain argument values, dict
    literals, f-string interpolations of user data, etc.) is OMITTED
    so the marker file carries no PII.  Only the file basename (not
    the full path, which can leak the user's home directory), the
    line number, and the function name are kept — enough for a
    support engineer to locate the offending code in the repo.

    Returns an empty string if ``exc_tb`` is ``None``.
    """
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
    """Best-effort lookup of the active ASR backend.

    Returns the backend name (e.g. ``"whisper"``, ``"parakeet"``,
    ``"qwen"``) or ``"<unknown>"`` if it can't be determined.  Called
    from the excepthook, which runs during interpreter shutdown, so
    every step is wrapped in ``try/except`` — a failure here must not
    mask the original crash.

    Reads from the persisted ``Config`` rather than the live
    ``AsrBackendRegistry`` because (a) the registry is owned by
    ``ModelManager`` (an app-level singleton, not module-level), and
    (b) during interpreter shutdown the registry's lock-held state
    may be partially dismantled.  The persisted config value is the
    stable, safe choice.

    This is a legitimate fresh-snapshot read — it runs during crash
    cleanup (interpreter shutdown) where the live ``app.config`` may
    be partially dismantled or its lock contaminated. A fresh disk
    read is the safe choice. Read-only — no mutation, no
    config-mutation lock required.
    """
    try:
        from voice_typer.server.config import Config

        cfg = Config.load()
        return str(getattr(cfg, "asr_backend", "<unknown>"))
    except Exception:
        return "<unknown>"


def _crash_excepthook(exc_type, exc_value, exc_tb) -> None:
    """Custom sys.excepthook for unhandled Python exceptions.

    Logs the exception to the voice-typer logger before chaining to
    the original hook.  Catches Python-level crashes (e.g., unhandled
    exceptions in threads) that would otherwise only appear on stderr.

    Also writes a ``python_crash.<PID>.txt`` marker file to the
    config_dir so the next session's ``report_pending_crash`` can
    surface the crash in the startup notification (alongside VEH
    crash diagnostics).  The marker contains the exception type,
    value, thread name, and timestamp — enough to diagnose the crash
    without re-running with a debugger attached.

    Mutable state (``_python_crash_dir``, ``_original_excepthook``)
    lives on the ``crash_handler`` facade module so test mutations
    propagate. Accessed via ``_ch.<name>``.
    """
    from voice_typer.server import crash_handler as _ch

    with contextlib.suppress(Exception):
        # Log ONLY ``exc_type.__name__`` at CRITICAL — never
        # ``exc_value``. Exception values can embed dictated speech
        # (e.g. ``ValueError("cannot process: " + transcribed_text)``)
        # or other PII that the PIIRedactionFilter (attached to log
        # handlers) only catches via structured patterns. The redacted
        # ``exc_value`` is persisted ONLY to the marker file (already
        # 0o600) — see the ``_safe_value`` block below.
        log.critical(
            "[CRASH] Unhandled Python exception: %s",
            exc_type.__name__ if exc_type is not None else "Unknown",
        )
        # Emit the PII-safe redacted traceback UNCONDITIONALLY so
        # support engineers can locate the call site without requiring
        # ``VOICE_TYPER_DEBUG=1``. ``_format_redacted_traceback``
        # emits only file basename + line number + function name — no
        # argument values, no source-line text, no full paths — so it
        # is safe to ship to the rotating log. The inner try/except is
        # defense-in-depth: traceback formatting must NEVER crash the
        # excepthook (it runs during interpreter shutdown for unhandled
        # exceptions, where any failure masks the original error).
        if exc_tb is not None:
            try:
                redacted_tb = _format_redacted_traceback(exc_tb)
                if redacted_tb:
                    log.critical("[CRASH] Redacted traceback (PII-safe):\n%s", redacted_tb)
            except Exception:
                pass  # Never let traceback formatting crash the excepthook
        # Full UNREDACTED traceback only when VOICE_TYPER_DEBUG=1
        # (operator opt-in for verbose diagnostics). This emits the raw
        # ``exc_info`` triple which CAN contain argument values / source
        # lines — so it stays gated.
        if os.environ.get("VOICE_TYPER_DEBUG", "") == "1":
            log.critical("[CRASH] Full traceback (VOICE_TYPER_DEBUG=1)", exc_info=(exc_type, exc_value, exc_tb))
    # Write a python_crash.<PID>.txt marker so the next session's
    # report_pending_crash can surface it.  Best-effort — the hook
    # must never raise (it runs during interpreter shutdown for
    # unhandled exceptions, where any failure masks the original
    # error).  Thread-safe: the PID suffix makes collisions extremely
    # unlikely, and the worst case is a single overwritten file.
    if _ch._python_crash_dir is not None:
        with contextlib.suppress(Exception):
            # Use _secure_atomic_write for atomic write + O_NOFOLLOW +
            # 0o600 on POSIX (was write_text with default umask 0644 =
            # world-readable on multi-user systems). Apply
            # redact_pii + redact_secret to exc_value before persisting
            # to the marker file (was raw str()).
            try:
                from voice_typer.server._secrets import redact_secret
                from voice_typer.server.config import _secure_atomic_write
                from voice_typer.server.security import redact_pii

                _atomic_write = _secure_atomic_write

                def _redact(s):
                    # aggressive=True so bare short secrets (e.g. a 12-char
                    # API key with no keyword prefix) that slip past
                    # redact_pii's pattern-matching are still redacted
                    # before persisting to the crash archive. The crash
                    # archive is high-risk because it sits on disk for
                    # weeks (default retention) and is included in
                    # export_gdpr_bundle — false-positive redaction here
                    # is cheap, leaking a real secret is catastrophic.
                    return redact_secret(redact_pii(s), aggressive=True)

            except Exception:
                _atomic_write = None

                def _redact(s):
                    return s

            marker_path = _ch._python_crash_dir / f"python_crash.{os.getpid()}.txt"
            thread_name = threading.current_thread().name
            timestamp = datetime.now().isoformat()
            # Truncate + redact exc_value so user speech and secrets
            # don't leak into the persistent crash archive.
            _raw_value = str(exc_value)[:200] if exc_value is not None else "None"
            _safe_value = _redact(_raw_value)
            # Redacted traceback (file basenames + line numbers +
            # function names, args stripped).  Frames carry no argument
            # values, so this is PII-safe.
            _traceback_text = _format_redacted_traceback(exc_tb)
            # Static context for triage — app/python/OS version + active
            # ASR backend.  Each lookup is best-effort.
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
            _asr_backend = _get_active_asr_backend()
            content_lines = [
                f"exc_type={exc_type.__name__ if exc_type is not None else 'Unknown'}",
                f"exc_value={_safe_value}",
                f"thread={thread_name}",
                f"timestamp={timestamp}",
                # Static triage context.
                f"app_version={_app_version}",
                f"python_version={_python_version}",
                f"os_version={_os_version}",
                f"asr_backend={_asr_backend}",
            ]
            # Append the redacted traceback unconditionally so support
            # engineers can locate the call site without
            # VOICE_TYPER_DEBUG=1.
            if _traceback_text:
                content_lines.append("")
                content_lines.append(_traceback_text)
            content = "\n".join(content_lines) + "\n"
            if _atomic_write is not None:
                _atomic_write(marker_path, content)
            else:
                marker_path.write_text(content, encoding="utf-8")
    if _ch._original_excepthook is not None and _ch._original_excepthook is not _crash_excepthook:
        with contextlib.suppress(Exception):
            _ch._original_excepthook(exc_type, exc_value, exc_tb)
    for handler in logging.getLogger("voice_typer").handlers:
        with contextlib.suppress(Exception):
            handler.flush()


def install_python_excepthook() -> None:
    """Install the custom sys.excepthook. Idempotent.

    ``_original_excepthook`` lives on the ``crash_handler`` facade
    module so test mutations propagate.
    """
    from voice_typer.server import crash_handler as _ch

    if sys.excepthook is _crash_excepthook:
        return
    _ch._original_excepthook = sys.excepthook
    sys.excepthook = _crash_excepthook


def install_crash_handler() -> bool:
    """Install the Windows Vectored Exception Handler.

    Must be called once at process startup, *before* any C extensions
    are loaded that could corrupt the heap.  Idempotent.

    On non-Windows, does nothing and returns False.

    ``_handler_handle`` + ``_kernel32`` + ``_vectored_handler`` live
    on the ``crash_handler`` facade module so test mutations propagate.
    """
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
                "[CRASH] Windows VEH installed — will capture silent crashes "
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
    """Remove the VEH handler. Idempotent.

    ``_handler_handle`` + ``_kernel32`` live on the ``crash_handler``
    facade module so test mutations propagate.
    """
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
