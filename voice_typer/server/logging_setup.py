"""Process logging bootstrap; C-LOG-1 line format + session= once on first line."""

import logging
import os
import sys
from pathlib import Path

from voice_typer.server import crash_handler as _crash_handler
from voice_typer.server.config import _config_dir, _migrate_from_legacy
from voice_typer.server.env_validation import _validate_env_vars

log = logging.getLogger(__name__)

# Deferred startup-banner state. ``_setup_logging()`` stages the banner
_startup_banner_state: dict[str, object] | None = None


def _setup_logging():
    """Configure logging (delegates to ``log.setup_logging``)."""
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
    global _startup_banner_state
    _startup_banner_state = {
        "config_dir": config_dir,
        "debug": debug,
        "quiet": quiet,
        "session_id": _session_id,
    }
    _crash_handler.set_crash_handler_config_dir(config_dir)


def _emit_startup_banner() -> None:
    """Called from ``VoiceTyperApp.__init__`` after the ``APP starting``"""
    global _startup_banner_state
    state = _startup_banner_state
    if state is None:
        # ``_setup_logging()`` has not run (e.g. direct app construction
        return
    _startup_banner_state = None

    _raw_config_dir = state["config_dir"]
    # ``_startup_banner_state`` is a ``dict[str, object]``, restore the
    config_dir: Path | None = _raw_config_dir if isinstance(_raw_config_dir, Path) else None
    debug = bool(state["debug"])
    quiet = bool(state["quiet"])
    _session_id = state["session_id"]

    # emit a startup banner so operators can see at a glance
    from voice_typer.server.log import get_log_file_path

    _log_file = get_log_file_path(config_dir)
    _json_mode = os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in (
        "1",
        "true",
        "yes",
    )
    # Report the level that actually gates what lands in the log file —
    _root_level = logging.WARNING
    for _handler in logging.getLogger("voice_typer").handlers:
        if _handler.level != logging.NOTSET:
            _root_level = _handler.level
            break
    # in quiet mode, the voice_typer logger is at WARNING. The banner
    _banner_level = logging.WARNING if quiet else logging.INFO
    log.log(
        _banner_level,
        "[STARTUP] logging initialized: file=%s | level=%s | json=%s | debug=%s | quiet=%s | session=%s",
        _log_file,
        logging.getLevelName(_root_level),
        _json_mode,
        debug,
        quiet,
        _session_id,
    )

    # One-time PII heads-up when debug logging is enabled.
    if debug:
        log.warning(
            "[STARTUP] Debug logging is enabled (VOICE_TYPER_DEBUG=1), "
            "DEBUG records may include sensitive context (file paths, "
            "device names, hostnames) beyond what PII redaction covers. "
            "Do not share the log file publicly without review; disable "
            "VOICE_TYPER_DEBUG for everyday use."
        )

    # Install BEFORE any C extensions load so the handler catches
    _crash_handler.install_crash_handler()
