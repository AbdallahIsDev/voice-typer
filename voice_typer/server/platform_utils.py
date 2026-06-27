"""Platform utilities — centralized platform detection.

CQ-029: Replaces scattered sys.platform checks with centralized functions.
Import these instead of writing `if sys.platform == "win32":` directly.

Usage:
    from voice_typer.server.platform_utils import is_windows, is_macos, is_linux
"""

import logging
import sys

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


# PLAT-008: Environment variable validation lives in ``app.py::_validate_env_vars``.
# A previous schema-driven implementation (``validate_env_vars`` +
# ``_init_env_var_schema`` + ``_ENV_VAR_SCHEMA``) lived here but was
# never called from any production code path — it was dead code that
# duplicated the inline implementation in ``app.py``. The dead code was
# removed to eliminate the parallel-systems maintenance hazard (Q5:
# parallel systems; Q10: not clean). The inline ``_validate_env_vars``
# in ``app.py`` is the single source of truth for env-var validation.
# See FORENSIC_REVIEW_COMPLETE.md → PLAT-008.
