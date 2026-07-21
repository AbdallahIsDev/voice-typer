"""Config-directory resolution and schema-version migration helpers.

CR-28 (config.py split): this module was extracted from
``voice_typer.server.config``.  The symbols here are re-exported from
``config.py`` so existing call sites — ``app.py``,
``logging_setup.py``, ``credential_store.py``, ``_paths.py``, and the
CR-28 test suite — keep working unchanged.

Behavior is byte-level preserved from the originals in ``config.py``
(same signatures, same logic, same return values, same exception
behaviour).  Two structural changes only:

1. ``_config_dir`` and ``_migrate_from_legacy`` now look up
   ``is_windows`` / ``is_macos`` via the ``config`` module at call
   time (function-level lookup) so tests that monkeypatch
   ``config.is_windows`` / ``config.is_macos`` (see
   ``tests/tauri/mig15|16|17/test_faster_whisper_*.py``) continue to
   drive the platform branches on the Linux CI runner.
2. ``_validate_path_safety`` is imported from the new
   ``path_safety`` module (which itself imports ``_config_dir`` from
   this module at function level to avoid a circular top-level
   dependency).
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from voice_typer.server.path_safety import _validate_path_safety

log = logging.getLogger("voice_typer.server.config")


def _config_dir() -> Path:
    """Get the voice-typer data directory.

    NEW-CLI-004: honors VOICE_TYPER_CONFIG_DIR env var.
    NEW-XPLAT-001: uses platform-appropriate paths instead of always
    ``~/.voice-typer``.  On Windows this is ``%APPDATA%/voice-typer``,
    on macOS ``~/Library/Application Support/voice-typer``, on Linux
    ``$XDG_DATA_HOME/voice-typer`` (falling back to
    ``~/.local/share/voice-typer``).  The legacy ``~/.voice-typer`` is
    still checked first for migration — existing users' data is
    automatically found and used.

    SEC-005: user-supplied env vars are validated for path traversal.
    """
    # CR-28: look up is_windows/is_macos from the config module so
    # tests that monkeypatch ``config.is_windows`` / ``config.is_macos``
    # (see ``tests/tauri/mig15|16|17/test_faster_whisper_*.py``)
    # continue to drive the platform branches after the extraction.
    from voice_typer.server import config as _cfg

    custom = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if custom:
        custom_path = Path(custom)
        # SEC-005: validate that custom path doesn't traverse above home
        try:
            _validate_path_safety(custom_path, Path.home())
        except ValueError:
            log.warning("[CONFIG] VOICE_TYPER_CONFIG_DIR path traversal detected: %s", custom)
            # Fall through to default paths
        else:
            return custom_path

    # NEW-XPLAT-001: check for legacy ~/.voice-typer first (migration
    # path — existing users keep their data where it is).
    legacy = Path.home() / ".voice-typer"
    if legacy.exists():
        return legacy

    # Platform-specific paths for new installations.
    if _cfg.is_windows():
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata) / "voice-typer"
            # SEC-005: validate APPDATA-derived path
            try:
                _validate_path_safety(appdata_path, Path.home())
            except ValueError:
                log.warning("[CONFIG] APPDATA path traversal detected: %s", appdata)
            else:
                return appdata_path
    elif _cfg.is_macos():
        return Path.home() / "Library" / "Application Support" / "voice-typer"
    else:
        # Linux / FreeBSD: honor XDG_DATA_HOME.
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            xdg_path = Path(xdg) / "voice-typer"
            # SEC-005: validate XDG_DATA_HOME-derived path
            try:
                _validate_path_safety(xdg_path, Path.home())
            except ValueError:
                log.warning("[CONFIG] XDG_DATA_HOME path traversal detected: %s", xdg)
            else:
                return xdg_path
        return Path.home() / ".local" / "share" / "voice-typer"

    # Fallback for any platform where the above checks didn't return.
    return legacy


def _migrate_from_legacy():
    """One-time migration from old platform-specific location (e.g. %APPDATA%)."""
    # CR-28: same is_windows/is_macos lookup pattern as _config_dir.
    from voice_typer.server import config as _cfg

    if _cfg.is_windows():
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif _cfg.is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    if not legacy.exists() or legacy.resolve() == _config_dir().resolve():
        return
    target = _config_dir()
    if target.exists():
        return

    shutil.copytree(legacy, target, dirs_exist_ok=True)
    log.info("[CONFIG] Migrated data from %s to %s", legacy, target)


_CURRENT_SCHEMA_VERSION = 2

# NEW-DEAD-018: _MIGRATIONS infrastructure for schema version migrations.
# ADR 0007: v2 migrates old audio preset names and deprecated fields.
_MIGRATIONS: dict[int, Any] = {}


def _migrate_to_v2(data: dict) -> dict:
    """Migrate config from schema v1 to v2 (ADR 0007 — filter chain).

    Changes:
    - Rename audio_preset "recommended" → "auto", "none" → "off"
    - If noise_filter_enabled was False, set audio_preset="off"
    - If noise_filter_rnnoise was True and no method set, set method="rnnoise"
    - Old noise_filter_gate_threshold (linear) is left in place; the new
      gate uses open/close dB thresholds with OBS-style defaults.
    - normalize_audio / normalize_target_peak left in place (ignored at runtime).
    """
    # Rename old preset names
    preset = data.get("audio_preset", "auto")
    if preset == "recommended":
        data["audio_preset"] = "auto"
    elif preset == "none":
        data["audio_preset"] = "off"

    # If noise_filter_enabled was False, switch to "off" preset
    if data.get("noise_filter_enabled") is False and "audio_preset" not in data:
        data["audio_preset"] = "off"

    # If RNNoise was explicitly enabled, ensure method is set
    if data.get("noise_filter_rnnoise") is True and "noise_suppression_method" not in data:
        data["noise_suppression_method"] = "rnnoise"

    return data


_MIGRATIONS[2] = _migrate_to_v2
