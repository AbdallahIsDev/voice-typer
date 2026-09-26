"""IPC diagnostics helpers."""

from __future__ import annotations

import logging
import sys

from voice_typer.server.branding import APP_NAME

# Module-level logger. Same name as ``ipc_server.log`` so diagnostic
_log = logging.getLogger("voice_typer.server.ipc_server")


def write_startup_diagnostic(phase: str, exc: BaseException | None = None) -> None:
    """``LausuApp()`` construction-failure block)."""
    import io
    import os
    import tempfile
    import time
    import traceback
    from pathlib import Path

    # Lazy imports so test patches on these module attributes are
    from voice_typer.server._secrets import redact_for_export
    from voice_typer.server.config import _config_dir, _secure_atomic_write
    from voice_typer.server.log import get_logs_dir

    buf = io.StringIO()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if phase == "construction":
        buf.write(f"{APP_NAME} startup failed at {timestamp}\n")
        buf.write(f"sys.executable: {sys.executable}\n")
        # redact secret-bearing argv entries before dumping.
        redacted_argv = [redact_for_export(str(arg)) for arg in sys.argv]
        buf.write(f"sys.argv: {redacted_argv}\n")
    elif phase == "app.start()":
        buf.write(f"\n--- app.start() failed at {timestamp} ---\n")
    else:
        buf.write(f"\n--- {phase} failed at {timestamp} ---\n")

    # Render the traceback. If the caller passed an explicit exception,
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=buf)
    else:
        traceback.print_exc(file=buf)

    diag_path = get_logs_dir(_config_dir()) / "startup-error.log"
    try:
        # O1: the logs live under ``<config_dir>/logs``. Ensure the dir
        diag_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(diag_path.parent, 0o700)
        # redact the traceback text too. ``traceback.print_exc``
        _secure_atomic_write(diag_path, redact_for_export(buf.getvalue()))
        # log at CRITICAL (level 50) -- the level-name carries
        _log.critical("Diagnostic written to %s", diag_path)
    except Exception as write_exc:
        # last-resort -- try stderr then a temp file so the
        try:
            stderr_payload = redact_for_export(buf.getvalue())
        except Exception as exc:
            stderr_payload = "[redaction failed, traceback suppressed to avoid PII leak] " + type(write_exc).__name__
            _log.warning(
                "[LOG-SETUP] redact_for_export raised %s; falling back to redacted marker",
                type(exc).__name__,
            )
        print(stderr_payload, file=sys.stderr)
        try:
            # the /tmp fallback must be (a) PII-redacted
            redacted_payload = redact_for_export(buf.getvalue())
            tmp = Path(tempfile.gettempdir()) / "lausu-startup-error.log"
            # ``os.O_NOFOLLOW`` is POSIX-only (absent on Windows). Use
            fd = os.open(
                str(tmp),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as f:
                f.write(redacted_payload)
            # log at CRITICAL -- see comment above.  The
            _log.critical(
                "Could not write %s; wrote to %s instead (write error: %s)",
                diag_path,
                tmp,
                write_exc,
            )
        except Exception:
            _log.critical("Could not write diagnostic anywhere: %s", write_exc)
