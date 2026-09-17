"""Shared spawn-environment helpers for the autostart launcher.

Extracted from ``_electron_build.py`` during the Electron/TCP
removal so the Tauri spawn paths keep the env-construction and
sensitive-key audit primitives without pulling in Electron-specific
code.

Callers:
- ``autostart/tauri_spawn.py``: ``_launcher_child_env``, ``_spawn_flags``
- ``autostart/focus.py``: ``_launcher_child_env``, ``_spawn_flags``
- ``autostart/_spawn.py``: ``_log_sensitive_env_keys``
"""

from __future__ import annotations

import logging
import os

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def _spawn_flags(hidden: bool = False) -> dict:
    """Platform-specific kwargs for spawning a child process.

    Parameters
    ----------
    hidden : bool
        If ``True`` (autostart at login), prevents console windows from
        flashing on Windows by adding ``CREATE_NO_WINDOW``.  If
        ``False`` (default, used by the desktop-shortcut path), Windows
        child processes get normal process creation.

    On POSIX, the child is detached into a new session
    (``start_new_session=True``) so it survives the launcher process
    exiting: this is required for both the autostart path (the
    launcher exits immediately after spawning) and the standalone
    backend path (the backend may exit before the host does).
    """
    kwargs: dict = {}
    if is_windows():
        if hidden:
            # CREATE_NO_WINDOW (0x08000000) prevents a console from
            # flashing during autostart (the user is logging in, not
            # clicking a shortcut).
            kwargs["creationflags"] = 0x08000000
        # else: no creation flags, processes get normal console
        # behavior, which lets `npm run dev` open its own console.
    else:
        # Detach into a new session so the child survives this launcher.
        kwargs["start_new_session"] = True
    return kwargs


def _launcher_child_env() -> dict[str, str]:
    """Build the base env dict for Tauri children whose output is redirected to log files.

    The child's stdout/stderr land in the Tauri log files. These
    tweaks keep those files clean (matching the plain-text format of
    ``voice-typer.log``):

    - ``FORCE_COLOR=0``: some JS tooling (vite/rollup/chalk) force-
      enables ANSI colour even when stdout is NOT a TTY; this disables
      it so no escape codes reach the log file.
    - ``NO_COLOR=1``: the de-facto cross-ecosystem no-ANSI contract
      (no-color.org) honoured by Rust console crates / CLI tooling that
      ignores ``FORCE_COLOR`` (the Tauri host is a Rust binary).
    - ``CLICOLOR=0``: the BSD/macOS convention for tools that honour
      ``CLICOLOR`` instead of ``NO_COLOR``.
    - ``RUST_LOG_STDERR=0``: the Rust host's ``CombinedLogger``
      mirrors its entire rotating-file stream
      (``voice-typer-rust.log``) to stderr when ``RUST_LOG_STDERR=1``
      is inherited. Force it off so ``tauri-stderr.log`` stays clean.
    - ``npm_config_loglevel=silent``: suppress npm's banner notices.

    Callers apply their own overrides on top (``VT_FOCUS_ONLY`` /
    IPC token env vars). A terminal run of ``cargo tauri dev`` does
    NOT go through this helper, so interactive sessions keep colours
    and the ``RUST_LOG_STDERR`` escape hatch.
    """
    env = dict(os.environ)
    env["FORCE_COLOR"] = "0"
    env["NO_COLOR"] = "1"
    env["CLICOLOR"] = "0"
    env["RUST_LOG_STDERR"] = "0"
    env["npm_config_loglevel"] = "silent"
    return env


# substring markers for "sensitive" env var names. When a child process
# inherits the parent's env (intentional, same-app restart), we log
# ONLY the key names matching one of these markers so a future leak in a
# downstream log is auditable. Values are NEVER printed. The list is
# intentionally conservative, it catches the common SaaS API-key
# conventions (OPENAI_API_KEY, ANTHROPIC_API_KEY, HF_TOKEN,
# GEMINI_API_KEY, AZURE_SPEECH_KEY, etc.) and OS-level secrets
# (AWS_SECRET_ACCESS_KEY, *_PASSWORD) without flagging benign vars
# (PATH, HOME, LANG, etc.).
_SENSITIVE_ENV_MARKERS = (
    "_API_KEY",
    "_SECRET",
    "_TOKEN",
    "_PASSWORD",
    "_CREDENTIAL",
    "AWS_SECRET_ACCESS_KEY",
)


def _redact_sensitive_env_keys(env: dict[str, str]) -> list[str]:
    """Return the NAMES of env keys that look sensitive.

    Helper used by the autostart launchers right after
    ``env = dict(os.environ)`` to surface (without values) which
    sensitive-looking env vars the child will inherit. The list is
    intended for an audit log line, it is NOT a security control.
    """
    return sorted(key for key in env if any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS))


def _log_sensitive_env_keys(env: dict[str, str], *, context: str) -> None:
    """Log (at INFO) the names of sensitive env keys present in ``env``.

    Only the KEY NAMES are logged, values are never printed. If no
    sensitive keys are present, nothing is logged (avoids log noise on
    the common case).
    """
    sensitive = _redact_sensitive_env_keys(env)
    if sensitive:
        log.info(
            "[ENV] %s: child inherits %d sensitive env keys (values redacted): %s",
            context,
            len(sensitive),
            ", ".join(sensitive),
        )
