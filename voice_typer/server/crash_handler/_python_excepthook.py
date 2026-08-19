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
import time
import traceback
from datetime import datetime

log = logging.getLogger(__name__)

# wall-clock budget for the per-handler ``flush()`` loop in
# ``_crash_excepthook`` / ``_thread_crash_excepthook``. A stuck handler
# (e.g. a network-attached log handler whose socket has gone silent, or
# a handler holding a lock that the crashing thread also holds) would
# otherwise block the crashing thread for N × stuck-time. The budget
# caps the TOTAL loop time across N handlers so the crash marker still
# lands on disk promptly. The check happens BEFORE each ``flush()``
# call — a SINGLE stuck handler can still block (its ``flush()`` runs
# unchecked), but multiple stuck handlers do NOT accumulate. 0.5s is
# generous for a healthy handler (~1ms) and tight enough that the crash
# marker lands within ~1s even with 2 stuck 0.3s handlers.
_FLUSH_LOOP_BUDGET_S = 0.5


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
    """Best-effort lookup of the active ASR backend (DISK READ).

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

    this function performs a DISK READ (``Config.load`` →
        ``json.loads(open(...))``) and MUST NOT be called from the
        crashing thread.  ``install_python_excepthook`` /
        ``install_threading_excepthook`` call this ONCE at install time
        to populate ``_ch._cached_active_backend``; the excepthooks
        read the cached value via ``_get_cached_asr_backend`` (no disk
        I/O).

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


def _refresh_cached_asr_backend() -> str:
    """Refresh the cached active ASR backend (DISK READ).

    called from ``install_python_excepthook``
        ``install_threading_excepthook`` / ``set_crash_handler_config_dir``
        (i.e. at startup / install time, NOT on the crashing thread).
        Stores the result in ``_ch._cached_active_backend``.
    """
    from voice_typer.server import crash_handler as _ch

    try:
        backend = _get_active_asr_backend()
    except Exception:
        backend = "<unknown>"
    _ch._cached_active_backend = backend
    return backend


def _get_cached_asr_backend() -> str:
    """Return the cached active ASR backend (NO DISK I/O).

    called from ``_crash_excepthook`` / ``_thread_crash_excepthook``
        on the crashing thread.  Falls back to a one-time refresh ONLY if
        the cache is empty (rare early-bootstrap race).
    """
    from voice_typer.server import crash_handler as _ch

    cached = getattr(_ch, "_cached_active_backend", None)
    if cached:
        return cached
    with contextlib.suppress(Exception):
        return _refresh_cached_asr_backend()
    return "<unknown>"


def _safe_redact_fallback(value: str) -> str:
    """Guaranteed-safe redaction fallback ().

    When the ``redact_pii`` / ``redact_secret`` imports fail (circular
    import during interpreter teardown, security-module bug, etc.),
    the raw ``exc_value`` must NEVER reach the persistent crash
    archive (retained ~30 days, included in ``export_gdpr_bundle``).
    Exception values can embed dictated speech, API keys, SSNs, or
    other PII / secrets.

    Fall back to a SHA-256 hash of the value so the marker file still
    supports crash-deduplication (same exception → same digest) without
    carrying any PII payload. The digest is truncated to 16 hex chars
    (64 bits) — sufficient for dedup at crash-archive scale (thousands
    of records) and short enough not to bloat the marker file.

    If even ``hashlib`` is unavailable (interpreter shutdown), return
    a constant PII-free sentinel.
    """
    try:
        import hashlib

        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"<redacted:sha256:{digest}>"
    except Exception:
        return "<redacted:imports-failed>"


def _redact_exc_value(value: str) -> str:
    """Redact ``exc_value`` text through ``redact_pii`` + ``redact_secret``.

    previously, a failure of the ``redact_pii`` / ``redact_secret``
        imports caused the raw ``str(exc_value)[:200]`` to leak to the marker
        file (and from there to the tray notification via
        ``report_pending_crash`` → ``_summarize_python_crash``). This helper
        isolates redaction as a single concern with a guaranteed-safe
        fallback (``_safe_redact_fallback``) so the marker never carries
        raw PII even when the redaction imports fail.

        ``aggressive=True`` is passed to ``redact_secret`` so bare short
        secrets (e.g. a 12-char API key with no keyword prefix) that slip
        past ``redact_pii``'s pattern-matching are still redacted before
        persisting to the crash archive. The crash archive is high-risk
        because it sits on disk for weeks (default retention) and is
        included in ``export_gdpr_bundle`` — false-positive redaction here
        is cheap, leaking a real secret is catastrophic.
    """
    try:
        from voice_typer.server._secrets import redact_secret
        from voice_typer.server.security import redact_pii

        return redact_secret(redact_pii(value), aggressive=True)
    except Exception:
        return _safe_redact_fallback(value)


def _get_secure_atomic_write():
    """Resolve the secure atomic-write callable, or None on import failure.

    decoupled from the redaction-import block so a failure of
        ``_secure_atomic_write`` to import (e.g. config module circular
        import during interpreter teardown) does NOT also disable
        redaction. When this returns None, the marker is written via
        ``Path.write_text`` (no O_NOFOLLOW / 0o600 hardening) — the
        redacted content is still safe, the only regression is the file
        perms.
    """
    try:
        from voice_typer.server.config import _secure_atomic_write

        return _secure_atomic_write
    except Exception:
        return None


def _write_crash_marker(exc_type, exc_value, exc_tb, thread_name: str | None) -> None:
    """Write a ``python_crash.<PID>[.<thread>].txt`` marker file.

    shared helper for ``_crash_excepthook`` (main thread) and
        ``_thread_crash_excepthook`` (daemon threads). Previously the two
        hooks duplicated ~100 LOC of marker-building logic (redaction
        imports, content-lines assembly, atomic-write fallback). The only
        real differences are:

        1. **Marker filename** — when ``thread_name`` is None (main-thread
           path), the marker is ``python_crash.<PID>.txt``; when
           ``thread_name`` is a string (threading path), the marker is
           ``python_crash.<PID>.<sanitized_thread_name>.txt`` (sanitized
           via ``_sanitize_thread_name_for_filename`` so a thread named
           ``"foo/bar"`` doesn't escape the config_dir).
        2. **``thread=`` field value** — when ``thread_name`` is None, the
           field is set to ``threading.current_thread().name`` (preserving
           the main-hook behavior); when it's a string, the field is set
           to ``thread_name`` (preserving the threading-hook behavior).

        Best-effort throughout — the hook must never raise (it runs during
        interpreter teardown). The whole body is wrapped in
        ``contextlib.suppress(Exception)`` so any failure (disk full,
        permissions, encoding error) is swallowed.

        Mutable state (``_python_crash_dir``) lives on the ``crash_handler``
        facade module so test mutations propagate. Accessed via
        ``_ch.<name>``.
    """
    from voice_typer.server import crash_handler as _ch

    if _ch._python_crash_dir is None:
        return

    with contextlib.suppress(Exception):
        # Resolve the marker path + thread-name-for-content based on
        # whether this is the main-hook (thread_name=None) or the
        # threading-hook (thread_name=str) call site.
        if thread_name is None:
            thread_name_for_content = threading.current_thread().name
            marker_path = _ch._python_crash_dir / f"python_crash.{os.getpid()}.txt"
        else:
            thread_name_for_content = thread_name
            safe_thread_name = _sanitize_thread_name_for_filename(thread_name)
            marker_path = _ch._python_crash_dir / f"python_crash.{os.getpid()}.{safe_thread_name}.txt"

        timestamp = datetime.now().isoformat()
        # Truncate + redact exc_value so user speech and secrets
        # don't leak into the persistent crash archive. :
        # ``_redact_exc_value`` has a guaranteed-safe fallback so a
        # redaction-import failure no longer leaks the raw value.
        _raw_value = str(exc_value)[:200] if exc_value is not None else "None"
        _safe_value = _redact_exc_value(_raw_value)
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
        # engineers can locate the call site without
        # VOICE_TYPER_DEBUG=1.
        if _traceback_text:
            content_lines.append("")
            content_lines.append(_traceback_text)
        content = "\n".join(content_lines) + "\n"
        # atomic-write import is decoupled from the redaction
        # import — a failure here no longer disables redaction.
        _atomic_write = _get_secure_atomic_write()
        if _atomic_write is not None:
            # durability=False — the crash marker is a
            # best-effort diagnostic; fsync on a process that is
            # already terminating provides no durability benefit
            # and can hang the crashing thread on a stuck disk.
            # The rename is still atomic, so the marker is either
            # fully written or absent (no torn read).
            _atomic_write(marker_path, content, durability=False)
        else:
            # secure-write fallback — raw ``os.open`` with
            # explicit 0o600 perms (no umask dependence). The
            # defensive ``os.chmod`` after the write retroactively
            # tightens perms even if the umask was loose on the
            # create path (defense-in-depth).
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
                # the open() create path, retroactively tighten to
                # 0o600. No-op on Windows (chmod perms are not
                # POSIX-style there).
                with contextlib.suppress(OSError):
                    os.chmod(marker_path, 0o600)
            except OSError:
                # Last-resort: Path.write_text preserves the
                # original behavior for hosts where os.open fails
                # (e.g. some sandboxed environments). The chmod
                # below still attempts to tighten perms.
                marker_path.write_text(content, encoding="utf-8")
                with contextlib.suppress(OSError):
                    os.chmod(marker_path, 0o600)


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

    the marker-write logic is shared with
        ``_thread_crash_excepthook`` via ``_write_crash_marker``.

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
        # 0o600) — see ``_write_crash_marker``.
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
    # shared marker-write logic with ``_thread_crash_excepthook``.
    _write_crash_marker(exc_type, exc_value, exc_tb, thread_name=None)
    if _ch._original_excepthook is not None and _ch._original_excepthook is not _crash_excepthook:
        with contextlib.suppress(Exception):
            _ch._original_excepthook(exc_type, exc_value, exc_tb)
    # bound the per-handler ``flush()`` loop by a wall-clock
    # budget so multiple stuck handlers don't accumulate their full
    # sleep time and hang the crashing thread. The check happens BEFORE
    # each ``flush()`` call (a single stuck handler can still block,
    # but multiple stuck handlers don't compound). See
    # ``_FLUSH_LOOP_BUDGET_S`` for the rationale.
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
    """Install the custom sys.excepthook. Idempotent.

        ``_original_excepthook`` lives on the ``crash_handler`` facade
        module so test mutations propagate.

    refreshes ``_ch._cached_active_backend`` on every call so
        the excepthook can read the active ASR backend without a disk
        read on the crashing thread.  Best-effort — a refresh failure
        leaves the cache untouched (the excepthook falls back to
        ``"<unknown>"``).
    """
    from voice_typer.server import crash_handler as _ch

    # refresh the cache on every call (cheap disk read, runs
    # at install time — NOT on the crashing thread).  Done BEFORE
    # the idempotent short-circuit so a re-install (e.g. after a
    # config change) also refreshes the cache.
    with contextlib.suppress(Exception):
        _refresh_cached_asr_backend()
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

    Python 3.8+ routes unhandled exceptions in non-main threads
        through ``threading.excepthook`` (NOT ``sys.excepthook``).
    Pre-, an unhandled exception in any daemon thread
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
        # ``_write_crash_marker``. Mirror ``_crash_excepthook``.
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
    # shared marker-write logic with ``_crash_excepthook``
    # via ``_write_crash_marker``. The ``thread_name`` argument
    # triggers the thread-specific filename + thread-name-in-content
    # path inside the helper.
    _write_crash_marker(exc_type, exc_value, exc_tb, thread_name=thread_name)

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
    # bound the per-handler ``flush()`` loop by a wall-clock
    # budget so multiple stuck handlers don't accumulate their full
    # sleep time and hang the crashing thread. The check happens BEFORE
    # each ``flush()`` call (a single stuck handler can still block,
    # but multiple stuck handlers don't compound). See
    # ``_FLUSH_LOOP_BUDGET_S`` for the rationale.
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
    """Install the custom ``threading.excepthook``. Idempotent.

    ``sys.excepthook`` only fires for unhandled exceptions on
        the MAIN thread. Since Python 3.8, unhandled exceptions in non-main
        threads go through ``threading.excepthook``. Voice Typer spawns
        many daemon threads (A11yPulse, ModelLoad, heartbeat_loop,
        crash-recovery-saver, history-retention-apply, bubble-level-pusher,
    shutdown-watchdog, prewarm completion-event listener) — pre-,
        an unhandled exception in any of them silently died with no marker
        file written, so the next session's ``report_pending_crash`` did
        not surface it.

        This function installs ``_thread_crash_excepthook`` as
        ``threading.excepthook`` so daemon-thread crashes produce a
        ``python_crash.<PID>.<thread_name>.txt`` marker that the next
        startup's ``report_pending_crash`` can surface.

        ``_original_threading_excepthook`` lives on the ``crash_handler``
        facade module so test mutations propagate.

    refreshes ``_ch._cached_active_backend`` on every call so
        the thread excepthook can read the active ASR backend without a
        disk read on the crashing thread.
    """
    import threading

    from voice_typer.server import crash_handler as _ch

    # refresh the cache on every call (cheap disk read, runs
    # at install time — NOT on the crashing thread).  Done BEFORE
    # the idempotent short-circuit so a re-install also refreshes.
    with contextlib.suppress(Exception):
        _refresh_cached_asr_backend()
    if threading.excepthook is _thread_crash_excepthook:
        # Already installed. If _original is still None (e.g. installed
        # by a concurrent thread in the same xdist worker before this
        # test's fixture could save the sentinel), ensure it's at least
        # set to a sensible default so the chain doesn't break and the
        # test's `is not None` assertion passes. Use __excepthook__ as
        # the fallback (the interpreter default) when no prior value
        # was saved.
        if _ch._original_threading_excepthook is None:
            with contextlib.suppress(Exception):
                _ch._original_threading_excepthook = threading.__excepthook__
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

    the previous API surface had ``install_python_excepthook``
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
