"""Autostart spawn environment."""

from __future__ import annotations

import logging
import os

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def _spawn_flags(hidden: bool = False) -> dict:
    """Platform-specific kwargs for spawning a child process."""
    kwargs: dict = {}
    if is_windows():
        if hidden:
            # CREATE_NO_WINDOW (0x08000000) prevents a console from
            kwargs["creationflags"] = 0x08000000
        # else: no creation flags, processes get normal console
    else:
        # Detach into a new session so the child survives this launcher.
        kwargs["start_new_session"] = True
    return kwargs


def _launcher_child_env() -> dict[str, str]:
    """Build the base env dict for Tauri children whose output is redirected to log files."""
    env = dict(os.environ)
    env["FORCE_COLOR"] = "0"
    env["NO_COLOR"] = "1"
    env["CLICOLOR"] = "0"
    env["RUST_LOG_STDERR"] = "0"
    env["npm_config_loglevel"] = "silent"
    return env


# substring markers for "sensitive" env var names. When a child process
_SENSITIVE_ENV_MARKERS = (
    "_API_KEY",
    "_SECRET",
    "_TOKEN",
    "_PASSWORD",
    "_CREDENTIAL",
    "AWS_SECRET_ACCESS_KEY",
)


def _redact_sensitive_env_keys(env: dict[str, str]) -> list[str]:
    """Return the NAMES of env keys that look sensitive."""
    return sorted(key for key in env if any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS))


def _log_sensitive_env_keys(env: dict[str, str], *, context: str) -> None:
    """Log (at INFO) the names of sensitive env keys present in ``env``."""
    sensitive = _redact_sensitive_env_keys(env)
    if sensitive:
        log.info(
            "[ENV] %s: child inherits %d sensitive env keys (values redacted): %s",
            context,
            len(sensitive),
            ", ".join(sensitive),
        )
