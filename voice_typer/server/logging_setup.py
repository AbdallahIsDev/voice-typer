"""Logging configuration extracted from ``voice_typer/server/app.py`` (REF-3).

Re-exported from ``app.py`` as ``_setup_logging`` so existing callers
(``voice_typer.server.ipc_server.main``,
``voice_typer.server.prewarm.run``, and tests that monkeypatch
``voice_typer.server.app._setup_logging``) keep working unchanged.
"""

import logging
import os
import sys

from voice_typer.server import crash_handler as _crash_handler
from voice_typer.server.config import _config_dir, _migrate_from_legacy
from voice_typer.server.env_validation import _validate_env_vars

log = logging.getLogger(__name__)

# Deferred startup-banner state. ``_setup_logging()`` stages the banner
# values (session id, resolved log file, root level, JSON/debug/quiet
# flags) here, and ``_emit_startup_banner()`` — called later, from
# ``VoiceTyperApp.__init__`` right AFTER the ``APP starting`` line — emits
# the ``[STARTUP] logging initialized`` banner and installs the crash
# handler.  Emitting both AFTER the ``starting`` banner keeps the startup
# log ordered as: ``APP starting`` → ``[STARTUP] logging initialized`` →
# ``[CRASH] Windows VEH installed``.
_startup_banner_state: dict[str, object] | None = None


def _setup_logging():
    """Configure logging (delegates to ``log.setup_logging``).

    Structure overview:
          1. Redirect stdin/stdout/stderr to devnull under pythonw.exe
          2. One-time legacy config migration
          3. Generate session ID for structured logging
    4. Set up RotatingFileHandler ()
          5. Apply session + PII redaction filters
          6. Fix stderr encoding for Unicode
          7. Optional colored stderr StreamHandler
    8. : VOICE_TYPER_QUIET env var for reduced verbosity
    """
    from voice_typer.server.log import setup_logging as _setup_logging_shared

    # One-time migration from legacy platform config dir
    _migrate_from_legacy()

    config_dir = _config_dir()

    # Point huggingface_hub cache under .voice-typer/ instead of ~/.cache/
    os.environ.setdefault("HF_HOME", str(config_dir / "huggingface"))

    debug = os.environ.get("VOICE_TYPER_DEBUG", "").lower() in ("1", "true", "yes")
    quiet = os.environ.get("VOICE_TYPER_QUIET", "").lower() in ("1", "true", "yes")
    port_mode = "--port" in sys.argv

    # The 8-char session id anchors this process's session. It is
    # printed ONCE in the banner below (the first line of the session)
    # so every subsequent line implicitly belongs to this session
    # without repeating the id on each line (C-LOG-1 keeps per-line
    # output clean; the banner is the single mention).
    _session_id = _setup_logging_shared(
        config_dir,
        debug=debug,
        quiet=quiet,
        port_mode=port_mode,
    )

    # validate environment variables before consuming them
    _validate_env_vars()

    # detect container environments and warn about unavailable features
    from voice_typer.server.container_detect import warn_if_in_container

    warn_if_in_container()

    # Stage the startup-banner state and configure the crash handler's
    # config dir. The ``[STARTUP] logging initialized`` banner itself and
    # the crash-handler install are DEFERRED to ``_emit_startup_banner()``,
    # called from ``VoiceTyperApp.__init__`` right AFTER the ``APP
    # starting`` line so the startup log reads ``APP starting`` →
    # ``[STARTUP] logging initialized`` → ``[CRASH] Windows VEH installed``.
    global _startup_banner_state
    _startup_banner_state = {
        "config_dir": config_dir,
        "debug": debug,
        "quiet": quiet,
        "session_id": _session_id,
    }
    _crash_handler.set_crash_handler_config_dir(config_dir)


def _emit_startup_banner() -> None:
    """Emit the ``[STARTUP] logging initialized`` banner and install the
    crash handler.

    Called from ``VoiceTyperApp.__init__`` after the ``APP starting``
    banner so the startup log is ordered ``APP starting`` → banner → VEH.
    """
    global _startup_banner_state
    state = _startup_banner_state
    if state is None:
        # ``_setup_logging()`` has not run (e.g. direct app construction
        # in a test without the entrypoint). Nothing to emit.
        return
    _startup_banner_state = None

    config_dir = state["config_dir"]
    debug = bool(state["debug"])
    quiet = bool(state["quiet"])
    _session_id = state["session_id"]

    # emit a startup banner so operators can see at a glance
    # which logging configuration took effect (file path, root level,
    # JSON mode, debug flag, quiet flag).  Logged at INFO so
    # it appears in the rotating file log under the default
    # configuration (file handler sits at INFO per ).  The
    # session id is included exactly ONCE here, as the trailing
    # ``session=`` field of the banner — the very first line of the
    # session — so it is never repeated per-line (C-LOG-1).
    # use get_log_file_path() instead of hardcoded literal so the
    # banner reflects the actual log file (voice-typer.log for main,
    # prewarm.log for the prewarm process).
    from voice_typer.server.log import get_log_file_path

    _log_file = get_log_file_path(config_dir)
    _json_mode = os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in (
        "1",
        "true",
        "yes",
    )
    # Report the level that actually gates what lands in the log file —
    # the rotating file handler's level — not the ``voice_typer`` logger
    # level, which ``setup_logging`` pins at DEBUG unconditionally (the
    # handler is the real gate; the logger stays at DEBUG so child
    # loggers can emit DEBUG records when ``VOICE_TYPER_DEBUG=1`` is
    # set). Pre-fix the banner read the logger level, so every default
    # run printed ``level=DEBUG, debug=False`` — accurate internals but
    # contradictory-looking, and it implied DEBUG records were being
    # written when the file handler was actually filtering them to INFO.
    # The file handler is added to the ``voice_typer`` logger first
    # (log/setup_logging), so the first handler with a level is it.
    _root_level = logging.WARNING
    for _handler in logging.getLogger("voice_typer").handlers:
        if _handler.level != logging.NOTSET:
            _root_level = _handler.level
            break
    # in quiet mode, the voice_typer logger is at WARNING. The banner
    # is logged at INFO, which is BELOW WARNING — the logger-level filter
    # would drop it before any handler is consulted. Log at WARNING when
    # quiet=True so the banner survives the filter and is written to disk.
    _banner_level = logging.WARNING if quiet else logging.INFO
    log.log(
        _banner_level,
        "[STARTUP] logging initialized: file=%s, level=%s, json=%s, debug=%s, quiet=%s, session=%s",
        _log_file,
        logging.getLevelName(_root_level),
        _json_mode,
        debug,
        quiet,
        _session_id,
    )

    # ── Windows VEH + Python excepthook: capture silent crashes ─────
    # Install BEFORE any C extensions load so the handler catches
    # crashes inside ctranslate2 / faster-whisper / sounddevice.
    _crash_handler.install_crash_handler()
