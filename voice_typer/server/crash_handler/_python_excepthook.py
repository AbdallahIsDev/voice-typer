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


def _sanitize_thread_name_for_filename(name: str) -> str:
    """Map a thread name to a filename-safe token.

    Thread names are arbitrary strings — a C extension or a test could
    spawn a thread named ``"foo/bar"`` or ``"..\\.."`` which would
    either escape the config_dir or collide with the ``python_crash.*``
    glob pattern used by ``report_pending_crash``.  This helper
    restricts the name to ``[A-Za-z0-9_-]`` and falls back to
    ``"thread"`` when nothing usable remains.

    Returned tokens are truncated to 40 chars so the marker filename
    (``python_crash.<PID>.<thread_name>.txt``) stays filesystem-portable
    across POSIX and Windows.
    """
    if not name:
        return "thread"
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    safe = safe.strip("_") or "thread"
    return safe[:40]


def _thread_crash_excepthook(args) -> None:
    """Custom ``threading.excepthook`` for unhandled thread exceptions.

    FR-14: Python 3.8+ routes unhandled exceptions in non-main threads
    through ``threading.excepthook`` (NOT ``sys.excepthook``).
    Pre-FR-14, an unhandled exception in any daemon thread
    (A11yPulse, ModelLoad, heartbeat_loop, crash-recovery-saver,
    history-retention-apply, bubble-level-pusher, shutdown-watchdog,
    prewarm completion-event listener) silently died — no
    ``python_crash.<PID>.txt`` marker was written, so the next
    session's ``report_pending_crash`` did not surface it.

    This hook mirrors ``_crash_excepthook`` for the threading path:
    1. Logs at CRITICAL with thread name + redacted exc_type.
    2. Writes a ``python_crash.<PID>.<thread_name>.txt`` marker file
       using the same ``redact_pii`` + ``redact_secret`` pipeline
       (so dictated speech / API keys in the exception value are
       scrubbed before persisting to the crash archive).
    3. Chains to the previously-installed ``threading.excepthook``
       so the default stderr path still fires (which is /dev/null
       under bundled sidecar / pythonw.exe — no duplicate user-visible
       output, just defense-in-depth).

    Best-effort throughout — the hook must never raise (it runs during
    interpreter teardown, where any failure masks the original error).

    Mutable state (``_original_threading_excepthook``) lives on the
    ``crash_handler`` facade module so test mutations propagate.
    Accessed via ``_ch.<name>``.
    """
    from voice_typer.server import crash_handler as _ch

    # Defensive: ``threading.ExceptHookArgs`` is a namedtuple, but a
    # poorly-behaved test or monkeypatch could pass a bare tuple/dict.
    # Extract fields defensively so a TypeError here does not mask the
    # original thread crash.
    try:
        exc_type = args.exc_type
        exc_value = args.exc_value
        exc_tb = args.exc_traceback
        thread = getattr(args, "thread", None)
    except AttributeError:
        return  # nothing we can safely do; bail out silently

    # Resolve the thread name defensively — ``thread`` may be None or
    # already finalized during interpreter shutdown.
    thread_name = "thread"
    with contextlib.suppress(Exception):
        if thread is not None:
            thread_name = thread.name or "thread"

    with contextlib.suppress(Exception):
        # Log ONLY ``exc_type.__name__`` at CRITICAL — never
        # ``exc_value`` (exception values can embed dictated speech
        # or other PII that PIIRedactionFilter only catches via
        # structured patterns). The redacted ``exc_value`` is
        # persisted ONLY to the marker file (already 0o600) — see
        # the ``_safe_value`` block below. Mirror ``_crash_excepthook``.
        type_name = exc_type.__name__ if exc_type is not None else "Unknown"
        log.critical(
            "[CRASH] Unhandled exception in thread %r: %s",
            thread_name,
            type_name,
        )
        # PII-safe redacted traceback — same pipeline as the main hook.
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
    # session's report_pending_crash can surface it. Best-effort —
    # same pattern as ``_crash_excepthook``.
    if _ch._python_crash_dir is not None:
        with contextlib.suppress(Exception):
            try:
                from voice_typer.server._secrets import redact_secret
                from voice_typer.server.config import _secure_atomic_write
                from voice_typer.server.security import redact_pii

                _atomic_write = _secure_atomic_write

                def _redact(s):
                    return redact_secret(redact_pii(s), aggressive=True)

            except Exception:
                _atomic_write = None

                def _redact(s):
                    return s

            safe_thread_name = _sanitize_thread_name_for_filename(thread_name)
            marker_path = _ch._python_crash_dir / f"python_crash.{os.getpid()}.{safe_thread_name}.txt"
            timestamp = datetime.now().isoformat()
            _raw_value = str(exc_value)[:200] if exc_value is not None else "None"
            _safe_value = _redact(_raw_value)
            _traceback_text = _format_redacted_traceback(exc_tb)
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
                f"app_version={_app_version}",
                f"python_version={_python_version}",
                f"os_version={_os_version}",
                f"asr_backend={_asr_backend}",
            ]
            if _traceback_text:
                content_lines.append("")
                content_lines.append(_traceback_text)
            content = "\n".join(content_lines) + "\n"
            if _atomic_write is not None:
                _atomic_write(marker_path, content)
            else:
                marker_path.write_text(content, encoding="utf-8")

    # Chain to the previously-installed threading.excepthook (typically
    # the interpreter default, which prints to stderr — /dev/null under
    # bundled sidecar). Defensive: the previous hook could be None or
    # could raise during interpreter shutdown.
    original = getattr(_ch, "_original_threading_excepthook", None)
    if original is not None and original is not _thread_crash_excepthook:
        with contextlib.suppress(Exception):
            original(args)
    # Flush all handlers so the CRITICAL record lands on disk before
    # the (potentially crashing) thread exits.
    for handler in logging.getLogger("voice_typer").handlers:
        with contextlib.suppress(Exception):
            handler.flush()


def install_threading_excepthook() -> None:
    """Install the custom ``threading.excepthook``. Idempotent.

    FR-14: ``sys.excepthook`` only fires for unhandled exceptions on
    the MAIN thread. Since Python 3.8, unhandled exceptions in non-main
    threads go through ``threading.excepthook``. Voice Typer spawns
    many daemon threads (A11yPulse, ModelLoad, heartbeat_loop,
    crash-recovery-saver, history-retention-apply, bubble-level-pusher,
    shutdown-watchdog, prewarm completion-event listener) — pre-FR-14,
    an unhandled exception in any of them silently died with no marker
    file written, so the next session's ``report_pending_crash`` did
    not surface it.

    This function installs ``_thread_crash_excepthook`` as
    ``threading.excepthook`` so daemon-thread crashes produce a
    ``python_crash.<PID>.<thread_name>.txt`` marker that the next
    startup's ``report_pending_crash`` can surface.

    ``_original_threading_excepthook`` lives on the ``crash_handler``
    facade module so test mutations propagate.
    """
    import threading

    from voice_typer.server import crash_handler as _ch

    if threading.excepthook is _thread_crash_excepthook:
        return
    _ch._original_threading_excepthook = threading.excepthook
    threading.excepthook = _thread_crash_excepthook


def remove_threading_excepthook() -> None:
    """Restore the original ``threading.excepthook``. Idempotent.

    Symmetric with ``install_threading_excepthook`` — the remove
    counterpart closes the install/remove pair so test cleanup is
    possible. Calling without a prior install is a no-op (the restore
    falls through to the interpreter's default ``threading.excepthook``,
    which prints the exception + thread name to stderr).
    """
    import threading

    from voice_typer.server import crash_handler as _ch

    original = getattr(_ch, "_original_threading_excepthook", None)
    if original is not None:
        threading.excepthook = original
        _ch._original_threading_excepthook = None  # type: ignore[assignment]
    elif threading.excepthook is _thread_crash_excepthook:
        # install was called via a test path that didn't track
        # ``_original_threading_excepthook``; fall back to the
        # interpreter's documented bootstrap default.
        threading.excepthook = threading.__excepthook__


def remove_python_excepthook() -> None:
    """Restore the original ``sys.excepthook``. Idempotent.

    AC-90: the previous API surface had ``install_python_excepthook``
    but no removal counterpart, which made the crash hook a
    one-way ratchet. Tests that want to assert the excepthook runs
    exactly once across a session had to manually save/restore
    ``sys.excepthook`` because there was no canonical "tear down"
    entry point. Mirrors ``remove_crash_handler`` for the Windows
    VEH (and the two are now symmetric — both install/remove pairs
    are part of the public ``crash_handler`` facade).

    Calling this without a prior ``install_python_excepthook`` is
    a no-op (the restore falls through to ``sys.__excepthook__``,
    which Python guarantees is the original interpreter default).
    """
    from voice_typer.server import crash_handler as _ch

    original = getattr(_ch, "_original_excepthook", None)
    if original is not None:
        sys.excepthook = original
        _ch._original_excepthook = None  # type: ignore[assignment]
    elif sys.excepthook is _crash_excepthook:
        # install was called via a test path that didn't track
        # _original_excepthook; fall back to the interpreter's
        # documented bootstrap default.
        sys.excepthook = sys.__excepthook__


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
