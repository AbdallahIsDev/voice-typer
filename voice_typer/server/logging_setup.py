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

    session_id = _setup_logging_shared(
        config_dir,
        debug=debug,
        quiet=quiet,
        port_mode=port_mode,
    )

    # emit a startup banner so operators can see at a glance
    # which logging configuration took effect (file path, root level,
    # JSON mode, debug flag, quiet flag, session id).  Logged at INFO so
    # it appears in the rotating file log under the default
    # configuration (file handler sits at INFO per ).  The
    # session_id is the 8-char hex returned by ``setup_logging``;
    # ``os.environ.get("VOICE_TYPER_LOG_JSON")`` is re-checked here
    # (rather than introspected from ``log``) so the banner stays
    # accurate even if ``setup_logging`` is changed to compute JSON
    # mode from a different source in the future.
    # use get_log_file_path() instead of hardcoded literal so the
    # banner reflects the actual log file (which may be voice-typer-prewarm.log
    # for the prewarm process after the  fix).
    from voice_typer.server.log import get_log_file_path

    _log_file = get_log_file_path(config_dir)
    _json_mode = os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in (
        "1",
        "true",
        "yes",
    )
    # read the voice_typer logger level (which setup_logging
    # actually configures) instead of the true root logger level (which is
    # always WARNING=30 and never modified by setup_logging). Pre-fix, the
    # banner always reported level=WARNING regardless of debug/quiet flags.
    _root_level = logging.getLogger("voice_typer").level
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
        session_id,
    )

    # validate environment variables before consuming them
    _validate_env_vars()

    # detect container environments and warn about unavailable features
    from voice_typer.server.container_detect import warn_if_in_container

    warn_if_in_container()

    # ── Windows VEH + Python excepthook: capture silent crashes ─────
    # Install BEFORE any C extensions load so the handler catches
    # crashes inside ctranslate2 / faster-whisper / sounddevice.
    _crash_handler.set_crash_handler_config_dir(config_dir)
    _crash_handler.install_crash_handler()
