"""Platform utilities — centralized platform detection.

CQ-029: Replaces scattered sys.platform checks with centralized functions.
Import these instead of writing `if sys.platform == "win32":` directly.

Usage:
    from voice_typer.server.platform_utils import is_windows, is_macos, is_linux
"""

import logging
import os
import sys
from typing import Any, Callable, Optional

log = logging.getLogger("voice_typer.server.platform_utils")


def is_windows() -> bool:
    """Return True if running on Windows."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """Return True if running on macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Return True if running on Linux."""
    return sys.platform.startswith("linux")


def platform_name() -> str:
    """Return a human-readable platform name."""
    if is_windows():
        return "windows"
    elif is_macos():
        return "macos"
    elif is_linux():
        return "linux"
    else:
        return sys.platform


# ─── PLAT-008: Environment variable validation ─────────────────────────


# Schema: (name, type, default, validation_fn)
# type is one of "bool", "str", "path", "token"
# validation_fn returns True if valid, False if invalid.
_ENV_VAR_SCHEMA: list[tuple[str, str, Any, Callable[[str], bool]]] = []


def _init_env_var_schema() -> None:
    """PLAT-008: Build the environment variable validation schema."""
    import re

    bool_re = re.compile(r"^(1|0|true|false|yes|no)$", re.IGNORECASE)
    token_re = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
    path_re = re.compile(r'^[^\0]+$')

    _ENV_VAR_SCHEMA.clear()
    # Boolean vars
    for name in ("VOICE_TYPER_DEBUG", "VOICE_TYPER_STREAMING", "VOICE_TYPER_QUIET", "VOICE_TYPER_NO_TRAY"):
        _ENV_VAR_SCHEMA.append((name, "bool", None, lambda v, _re=bool_re: bool(_re.match(v))))
    # Token vars
    for name in ("VOICE_TYPER_RESTART", "VOICE_TYPER_IPC_TOKEN"):
        _ENV_VAR_SCHEMA.append((name, "token", None, lambda v, _re=token_re: bool(_re.match(v))))
    # Path vars
    for name in ("VOICE_TYPER_CONFIG_DIR", "HF_HOME"):
        _ENV_VAR_SCHEMA.append((name, "path", None, lambda v, _re=path_re: bool(_re.match(v)) and len(v) <= 4096))
    # SystemRoot (Windows only)
    if is_windows():
        _ENV_VAR_SCHEMA.append(("SystemRoot", "path", r"C:\Windows", lambda v, _re=path_re: bool(_re.match(v))))


def validate_env_vars() -> dict[str, Any]:
    """PLAT-008: Validate consumed environment variables at startup.

    For each env var, checks the value against the expected type and
    validation function. Logs a warning when validation fails and
    falls back to the default.

    Returns a dict of {var_name: validated_value} for all validated vars.
    """
    if not _ENV_VAR_SCHEMA:
        _init_env_var_schema()

    results: dict[str, Any] = {}
    for name, var_type, default, validate_fn in _ENV_VAR_SCHEMA:
        val = os.environ.get(name)
        if val is None:
            results[name] = default
            continue
        if not validate_fn(val):
            log.warning(
                "[PLATFORM_UTILS] Invalid value for %s=%r — expected %s. "
                "Falling back to default: %r",
                name, val, var_type, default,
            )
            if default is not None:
                os.environ[name] = default
            else:
                os.environ.pop(name, None)
            results[name] = default
        else:
            results[name] = val

    return results
