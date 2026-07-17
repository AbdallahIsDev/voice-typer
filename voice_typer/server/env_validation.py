"""PLAT-008: Environment variable validation.

Extracted from ``voice_typer/server/app.py`` (REF-3). Re-exported from
``app.py`` as ``_validate_env_vars`` so existing callers (notably
``voice_typer.server.logging_setup._setup_logging``) and tests that do
``from voice_typer.server.app import _validate_env_vars`` keep working.

SEC-audit-011: this module calls ``_validate_systemroot`` from
:mod:`voice_typer.server.config` to reject attacker-controlled SystemRoot
values that could enable DLL injection.
"""

import logging
import os
import re

log = logging.getLogger(__name__)


def _validate_env_vars() -> None:
    """PLAT-008: Validate all consumed environment variables.

    Rejects values that don't match expected patterns. Logs warnings
    for invalid values and resets them to safe defaults.
    """

    _bool_vars = {"VOICE_TYPER_QUIET", "VOICE_TYPER_DEBUG", "VOICE_TYPER_NO_TRAY", "VOICE_TYPER_STREAMING"}
    _bool_pattern = re.compile(r"^(1|0|true|false|yes|no)$", re.IGNORECASE)
    _token_pattern = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
    _path_pattern = re.compile(r"^[^\0]+$")  # no null bytes

    for var in _bool_vars:
        val = os.environ.get(var)
        if val is not None and not _bool_pattern.match(val):
            log.warning(
                "[ENV] Invalid value for %s=%r -- expected boolean (1/0/true/false/yes/no). Resetting to empty.",
                var,
                val,
            )
            os.environ.pop(var, None)

    restart_val = os.environ.get("VOICE_TYPER_RESTART")
    if restart_val is not None and not _token_pattern.match(restart_val):
        log.warning(
            (
                "[ENV] Invalid value for VOICE_TYPER_RESTART=<redacted> -- "
                "expected alphanumeric token. Resetting to empty."
            ),
        )
        os.environ.pop("VOICE_TYPER_RESTART", None)

    config_dir = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if config_dir is not None and (not _path_pattern.match(config_dir) or len(config_dir) > 4096):
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=%r -- expected valid path. Resetting to empty.",
            config_dir,
        )
        os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)

    ipc_token = os.environ.get("VOICE_TYPER_IPC_TOKEN")
    if ipc_token is not None and not _token_pattern.match(ipc_token):
        log.warning(
            (
                "[ENV] Invalid value for VOICE_TYPER_IPC_TOKEN=<redacted> -- "
                "expected alphanumeric token. Resetting to empty."
            ),
        )
        os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)

    # SEC-audit-011: Validate SystemRoot on Windows to prevent DLL injection
    from voice_typer.server.config import _validate_systemroot

    _validate_systemroot()

    # PLAT-008: Validate HF_HOME is a valid path if set
    hf_home = os.environ.get("HF_HOME")
    if hf_home is not None and (not _path_pattern.match(hf_home) or len(hf_home) > 4096):
        log.warning(
            "[ENV] Invalid value for HF_HOME=%r -- expected valid path. Resetting to empty.",
            hf_home,
        )
        os.environ.pop("HF_HOME", None)
