"""Linux autostart — freedesktop ``.desktop`` entry.

Phase 4.5 /  — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Implements the
three Linux autostart primitives:

  - :func:`_enable_autostart_linux` — write
    ``<XDG_CONFIG_HOME or ~/.config>/autostart/voice-typer.desktop``.
  - :func:`_disable_autostart_linux` — ``unlink()`` the .desktop file.
  - :func:`_is_autostart_linux` — file-existence probe on the .desktop.

Patch-path compatibility
------------------------
Tests patch ``get_autostart_dir`` and ``_autostart_command`` via
``monkeypatch.setattr(platform_mod, "X", lambda: ...)`` (in
:mod:`tests.test_platform`).  Both are looked up via ``_pkg.X()`` at
call time so the patches take effect.

``inspect.getsource`` compatibility
-----------------------------------
``_enable_autostart_linux`` / ``_disable_autostart_linux`` /
``_is_autostart_linux`` are genuinely defined here, so
``inspect.getsource(_enable_autostart_linux)`` continues to read from
this file.
"""

from __future__ import annotations

import logging

# Patch-path bridge: route lookups of ``get_autostart_dir`` and
# ``_autostart_command`` through the package namespace so test patches
# of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
# keep affecting production code defined here.
from voice_typer.server import server_platform as _pkg
from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


def _enable_autostart_linux() -> bool:
    autostart_dir = _pkg.get_autostart_dir()
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = autostart_dir / "voice-typer.desktop"

    # _autostart_command() returns each space-containing argument already
    # double-quoted per the Desktop Entry Spec's Exec quoting rules
    # (https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html).
    # Use the command VERBATIM — stripping quotes corrupts the first arg
    # (e.g. "/usr/bin/python3" "/path/launcher.py" -> python3" "/path...).
    exec_field = _pkg._autostart_command()

    # align the autostart .desktop Icon with the bundled template
    # (src-tauri/voice-typer.desktop.template uses `Icon=voice-typer`).
    # Pre-fix this wrote `Icon=audio-input-microphone`, causing the same
    # app to show two different icons (one in autostart, one in the
    # Start Menu / launcher). The Exec field is intentionally different
    # from the template: autostart launches `python launcher.py --hidden
    # --delay 15` (hidden at login) whereas the template's Exec points at
    # the interactive `voice-typer-tauri` app binary — aligning Exec
    # would break the hidden-autostart behavior, so only Icon is aligned.
    # The template carries a matching `#` comment block explaining the
    # reverse direction; keep both comments in sync if this changes.
    desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Background voice-to-text utility
Exec={exec_field}
Icon=voice-typer
Hidden=false
NoDisplay=true
"""
    # Atomic write (temp + os.replace) so a crash mid-write cannot
    # leave a half-truncated .desktop file that the desktop
    # environment silently skips on next login. durability=False
    # matches the existing prewarm/autostart pattern — these files do
    # not need fsync. No chmod(0o600) is applied: desktop environments
    # must be able to read the .desktop file (see comment below).
    from voice_typer.server.secure_file_io import _secure_atomic_write

    _secure_atomic_write(desktop_path, desktop_content, durability=False)
    # SEC-003: .desktop autostart files are written to a shared XDG
    # autostart directory (e.g. ~/.config/autostart/).  Restrictive
    # permissions (0o600) are NOT applied here because:
    # 1. The autostart directory is per-user and already private.
    # 2. Desktop environments must be able to read the .desktop file
    #    to launch the app at login — overly restrictive permissions
    #    can cause the autostart entry to be silently skipped.
    log.info("[CONFIG] Autostart enabled (Linux): %s", desktop_path)
    return True


def _disable_autostart_linux() -> bool:
    desktop_path = _pkg.get_autostart_dir() / "voice-typer.desktop"
    if desktop_path.exists():
        desktop_path.unlink()
    log.info("[CONFIG] Autostart disabled (Linux)")
    return True


def _is_autostart_linux() -> bool:
    return (_pkg.get_autostart_dir() / "voice-typer.desktop").exists()
