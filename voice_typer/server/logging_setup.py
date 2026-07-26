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

    CQ-007: Structure overview:
      1. Redirect stdin/stdout/stderr to devnull under pythonw.exe
      2. One-time legacy config migration
      3. Generate session ID for structured logging
      4. Set up RotatingFileHandler (PROD-016)
      5. Apply session + PII redaction filters
      6. Fix stderr encoding for Unicode
      7. Optional colored stderr StreamHandler
      8. PROD-020: VOICE_TYPER_QUIET env var for reduced verbosity
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

    # GT-B1-15: emit a startup banner so operators can see at a glance
    # which logging configuration took effect (file path, root level,
    # JSON mode, debug flag, quiet flag, session id).  Logged at INFO so
    # it appears in the rotating file log under the default
    # configuration (file handler sits at INFO per G4-H-35).  The
    # session_id is the 8-char hex returned by ``setup_logging``;
    # ``os.environ.get("VOICE_TYPER_LOG_JSON")`` is re-checked here
    # (rather than introspected from ``log``) so the banner stays
    # accurate even if ``setup_logging`` is changed to compute JSON
    # mode from a different source in the future.
    _log_file = config_dir / "voice-typer.log"
    _json_mode = os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in (
        "1",
        "true",
        "yes",
    )
    _root_level = logging.getLogger().level
    log.info(
        "[STARTUP] logging initialized: file=%s, level=%s, json=%s, debug=%s, quiet=%s, session=%s",
        _log_file,
        logging.getLevelName(_root_level),
        _json_mode,
        debug,
        quiet,
        session_id,
    )

    # PLAT-008: validate environment variables before consuming them
    _validate_env_vars()

    # PLAT-021: detect container environments and warn about unavailable features
    from voice_typer.server.container_detect import warn_if_in_container

    warn_if_in_container()

    # ── Windows VEH + Python excepthook: capture silent crashes ─────
    # Install BEFORE any C extensions load so the handler catches
    # crashes inside ctranslate2 / faster-whisper / sounddevice.
    _crash_handler.set_crash_handler_config_dir(config_dir)
    _crash_handler.install_crash_handler()
