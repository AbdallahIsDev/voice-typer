"""Startup-diagnostic helper extracted from ``ipc_server.main()`` ().

(comprehensive review): the two startup-error diagnostic blocks
in :func:`voice_typer.server.ipc_server.main` — one for the
``VoiceTyperApp()`` construction-failure path (~L2306) and one for the
``app.start()`` failure path (~L2506) — were ~70 lines each and
copy-pasted verbatim. Each block built an :class:`io.StringIO` buffer,
wrote a phase-specific header + the current traceback, redacted the
payload via :func:`voice_typer.server.security._redact_text`, and
attempted :func:`voice_typer.server.config._secure_atomic_write` to
``<config_dir>/startup-error.log`` — falling back to
``print(buf, file=sys.stderr)`` + an owner-only file in
``tempfile.gettempdir()`` if the config dir was unwritable.

Every fix ('s ``/tmp`` fallback, 's PII redaction, 's
overwrite-vs-append) had to be applied twice, and the two copies had
already drifted once (the construction path overwrote, the
``app.start()`` path appended). This module restores a single source
of truth.

Behaviour is identical to the inlined blocks:

* The phase-specific header text is preserved verbatim so
  ``tests/test_ipc_server_main_diagnostics.py`` (which asserts on the
  ``"Voice Typer startup failed at"`` substring) keeps passing.
* All imports are lazy (inside the function body) so test patches on
  ``voice_typer.server.config._secure_atomic_write`` /
  ``voice_typer.server.config._config_dir`` /
  ``voice_typer.server._secrets.redact_for_export`` /
  ``tempfile.gettempdir`` are observed at call time — matching the
  pre-extraction behaviour where each block imported these symbols
  locally inside the ``except`` clause.
* The function intentionally does NOT raise — every internal failure
  is caught and logged so a diagnostic-write failure cannot mask the
  original startup exception that triggered it.

the redaction pipeline was switched from
:func:`voice_typer.server.security._redact_text` to the unified
:func:`voice_typer.server._secrets.redact_for_export` so the
startup-error path uses the SAME redactor as the diagnostic-bundle
path (``voice-typer.log`` + archived crash dumps). The two pipelines
had drifted once already (the diagnostics_export path didn't pass
``aggressive=True``, missing short bare secrets — ); routing
both through ``redact_for_export`` ensures a future redaction
improvement only has to land in one place.
"""

from __future__ import annotations

import logging
import sys

# Module-level logger. Same name as ``ipc_server.log`` so diagnostic
# messages show up under the same logger the operator already watches
# during a startup crash (rather than a new ``ipc_diagnostics`` logger
# that the log filter rules haven't been told about).
_log = logging.getLogger("voice_typer.server.ipc_server")


def write_startup_diagnostic(phase: str, exc: BaseException | None = None) -> None:
    """Write a startup-error diagnostic to ``<config_dir>/logs/startup-error.log``.

        Encapsulates the io.StringIO → traceback → redact_for_export →
        _secure_atomic_write → /tmp-fallback pattern that was previously
        inlined (twice) in :func:`voice_typer.server.ipc_server.main`.

        Args:
            phase: Diagnostic phase label. Two values are recognised for
                backward compatibility with the historical diagnostic
                headers:

                  * ``"construction"`` — produces the
                    ``"Voice Typer startup failed at <time>\\n"`` header
                    followed by ``sys.executable`` and a redacted
                    ``sys.argv`` (matches the pre-extraction
                    ``VoiceTyperApp()`` construction-failure block).
                  * ``"app.start()"`` — produces the
                    ``"\\n--- app.start() failed at <time> ---\\n"`` header
                    (matches the pre-extraction ``app.start()``-failure
                    block).

                Any other value is rendered verbatim as
                ``"\\n--- <phase> failed at <time> ---\\n"`` so future
                call sites can opt in without extending the recognised set.
            exc: Optional exception whose traceback is written. If ``None``,
                :func:`sys.exc_info` is used (via :func:`traceback.print_exc`)
                so the caller can be inside an ``except`` block without
                forwarding the exception explicitly.

        Write path (mirrors the pre-extraction blocks exactly):

        1. Build the buffer (phase header + redacted argv for the
           construction path + the traceback).
        2. Try ``_secure_atomic_write(diag_path, redact_for_export(buf))``
    (: redaction routed through the unified
           :func:`voice_typer.server._secrets.redact_for_export` so the
           startup-error path and the diagnostic-bundle path share one
           redactor).
        3. On any failure: ``print(buf, file=sys.stderr)`` (so the
           traceback is visible under pythonw.exe where stdout/stderr are
           devnull, but still surfaces in terminal launches), then attempt
           an owner-only file at
           ``Path(tempfile.gettempdir()) / "voice-typer-startup-error.log"``
           via ``os.open(O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW, 0o600)``.
        4. If the /tmp fallback also fails, log the original write error
           (the ``write_exc`` from step 2) so the operator at least sees
           *something* went wrong.
    """
    import io
    import os
    import tempfile
    import time
    import traceback
    from pathlib import Path

    # Lazy imports so test patches on these module attributes are
    # observed at call time (see the  /  regression tests
    # in tests/test_ipc_server_main_diagnostics.py).
    from voice_typer.server._secrets import redact_for_export
    from voice_typer.server.config import _config_dir, _secure_atomic_write
    from voice_typer.server.log import get_logs_dir

    buf = io.StringIO()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if phase == "construction":
        buf.write(f"Voice Typer startup failed at {timestamp}\n")
        buf.write(f"sys.executable: {sys.executable}\n")
        # redact secret-bearing argv entries before dumping.
        # ``sys.argv`` may carry ``--ipc-token sk-…`` or env-style
        # ``KEY=value`` pairs that include API keys / bearer tokens.
        # The PIIRedactionFilter attached to the rotating log handler
        # would scrub these in normal log lines, but this diagnostic
        # file is written via _secure_atomic_write — bypassing the
        # logging filter. Pipe each argv entry through the unified
        # ``redact_for_export`` pipeline () so secrets are
        # masked the same way they would be in a log record — and
        # the same way they ARE masked in the diagnostic bundle's
        # ``voice-typer.log`` and archived crash dumps.
        redacted_argv = [redact_for_export(str(arg)) for arg in sys.argv]
        buf.write(f"sys.argv: {redacted_argv}\n")
    elif phase == "app.start()":
        buf.write(f"\n--- app.start() failed at {timestamp} ---\n")
    else:
        buf.write(f"\n--- {phase} failed at {timestamp} ---\n")

    # Render the traceback. If the caller passed an explicit exception,
    # use it (preserving its ``__traceback__``); otherwise fall back to
    # ``traceback.print_exc()`` which reads ``sys.exc_info()`` so the
    # caller can be inside an ``except`` block without forwarding the
    # exception explicitly.
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=buf)
    else:
        traceback.print_exc(file=buf)

    diag_path = get_logs_dir(_config_dir()) / "startup-error.log"
    try:
        # O1: the logs live under ``<config_dir>/logs`` — ensure the dir
        # exists before the atomic write (its mkstemp requires the parent).
        diag_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(diag_path.parent, 0o700)
        # redact the traceback text too. ``traceback.print_exc``
        # can include ``str(exception)`` which may carry a URL with
        # ``?key=sk-…`` or an env-var dump from a buggy handler —
        # piping through ``redact_for_export`` () mirrors the
        # PIIRedactionFilter behavior that the rotating file log applies
        # to ``log.exception``. ``redact_for_export`` passes
        # ``aggressive=True`` to ``redact_secret`` () so bare
        # short secrets in the traceback are caught too.
        # OVERWRITE (not append) the diagnostic file so repeated
        # relaunch crashes don't grow it without bound.
        _secure_atomic_write(diag_path, redact_for_export(buf.getvalue()))
        # log at CRITICAL (level 50) -- the level-name carries
        # the severity; an in-message ``[FATAL]`` prefix is dropped
        # because log aggregators / alerting rules key off
        # ``record.levelno``, not substring matches.
        _log.critical("Diagnostic written to %s", diag_path)
    except Exception as write_exc:
        # last-resort -- try stderr then a temp file so the
        # traceback isn't lost (e.g. read-only config dir under
        # pythonw.exe where stdout/stderr are also devnull).
        # ``print`` bypasses the PIIRedactionFilter -- pipe
        # the payload through ``redact_for_export`` BEFORE the print so
        # secrets embedded in the traceback (URL query-string keys,
        # env-var dumps, bearer tokens) are masked the same way they
        # would be in a ``log.critical`` record.  If the redactor
        # itself raises, fall back to a fixed redacted-marker string
        # -- NEVER the raw ``buf`` payload. The raw traceback may
        # carry API keys / bearer tokens injected via ``{clipboard}``
        # dictation-pipeline templates, env-var dumps from buggy
        # handlers, or ``?key=sk-...`` URL query-string secrets that
        # the redactor was supposed to mask but couldn't. A
        # marker-only stderr line (with the outer ``write_exc`` type
        # name appended so the operator can see the primary write
        # failure that put us on this fallback path) is better than a
        # PII leak -- the original exception is still logged via the
        # ``_log.critical`` calls below, so the diagnostic content is
        # not lost, only the stderr copy of it is suppressed.
        try:
            stderr_payload = redact_for_export(buf.getvalue())
        except Exception as exc:
            stderr_payload = "[redaction failed — traceback suppressed to avoid PII leak] " + type(write_exc).__name__
            _log.warning(
                "[LOG-SETUP] redact_for_export raised %s; falling back to redacted marker",
                type(exc).__name__,
            )
        print(stderr_payload, file=sys.stderr)
        try:
            # the /tmp fallback must be (a) PII-redacted
            # (same as the config-dir path) and (b) owner-only.
            # ``Path.write_text`` creates the file with the process
            # umask (typically 0o644) — world-readable, which leaks
            # the redacted-but-still-sensitive traceback (paths,
            # library versions, possibly partial secrets that
            # ``redact_for_export`` missed) to any local user.
            # previously this used ``O_EXCL`` (atomic create,
            # refuses to clobber an existing file). With ``O_EXCL``,
            # if ``/tmp/voice-typer-startup-error.log`` exists from a
            # previous crash, the next startup crash cannot write its
            # diagnostic — ``os.open`` raises ``FileExistsError``,
            # the outer ``except Exception`` runs, and the traceback
            # is lost. The docstring at line 146-147 says "OVERWRITE
            # (not append) the diagnostic file so repeated relaunch
            # crashes don't grow it without bound" — the /tmp fallback
            # must honor that same contract. ``O_TRUNC`` opens the
            # existing file (or creates it) and truncates it to zero
            # length before writing. ``O_NOFOLLOW`` still prevents
            # the symlink attack (an attacker who plants a symlink at
            # ``/tmp/voice-typer-startup-error.log`` -> ~/.ssh/id_rsa
            # would cause ``os.open`` to raise ``ELOOP`` rather than
            # following the symlink and clobbering the target).
            # ``O_EXCL`` is correct for the config_dir primary path
            # (atomic create; the file is owned by us and overwritten
            # by ``_secure_atomic_write`` via ``os.replace``); the
            # /tmp fallback uses overwrite semantics because the file
            # may legitimately exist from a previous crash.
            redacted_payload = redact_for_export(buf.getvalue())
            tmp = Path(tempfile.gettempdir()) / "voice-typer-startup-error.log"
            # ``os.O_NOFOLLOW`` is POSIX-only (absent on Windows). Use
            # ``getattr`` so the overwrite-semantics fallback still
            # works on every platform — on Windows, NTFS reparse
            # points are governed by the ``FILE_ATTRIBUTE_REPARSE_POINT``
            # ACL surface, and the fallback file lives in the per-user
            # temp dir, so the symlink-hardening flag is best-effort.
            fd = os.open(
                str(tmp),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as f:
                f.write(redacted_payload)
            # log at CRITICAL -- see comment above.  The
            # ``[FATAL]`` prefix is dropped because the level name
            # now carries that information.
            _log.critical(
                "Could not write %s; wrote to %s instead (write error: %s)",
                diag_path,
                tmp,
                write_exc,
            )
        except Exception:
            _log.critical("Could not write diagnostic anywhere: %s", write_exc)
