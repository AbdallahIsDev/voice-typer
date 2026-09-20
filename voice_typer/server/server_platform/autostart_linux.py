"""Linux autostart, freedesktop ``.desktop`` entry."""

from __future__ import annotations

import logging
from pathlib import Path

# Patch-path bridge: ``_autostart_mod`` binds the owning sibling
from voice_typer.server.branding import APP_NAME
from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)


def _enable_autostart_linux() -> bool:
    autostart_dir = _autostart_mod.get_autostart_dir()
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = autostart_dir / "voice-typer.desktop"

    # _autostart_command() returns each space-containing argument already
    exec_field = _autostart_mod._autostart_command()

    # align the autostart .desktop Icon with the bundled template
    desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Background voice-to-text utility
Exec={exec_field}
Icon=voice-typer
Hidden=false
Terminal=false
NoDisplay=true
"""
    # Atomic write (temp + os.replace) so a crash mid-write cannot
    from voice_typer.server.secure_file_io import _secure_atomic_write

    _secure_atomic_write(desktop_path, desktop_content, durability=False)
    # SEC-003: .desktop autostart files are written to a shared XDG
    log.info("[CONFIG] Autostart enabled (Linux): %s", desktop_path)
    return True


def _disable_autostart_linux() -> bool:
    desktop_path = _autostart_mod.get_autostart_dir() / "voice-typer.desktop"
    if desktop_path.exists():
        desktop_path.unlink()
    log.info("[CONFIG] Autostart disabled (Linux)")
    return True


def _desktop_exec_path_exists(desktop_path: Path) -> bool:
    """Validate that a .desktop entry's ``Exec=`` program exists on disk."""
    try:
        content = desktop_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.debug("[AUTOSTART] Linux .desktop unreadable, treating as valid: %s", desktop_path)
        return True
    exec_line = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("Exec="):
            exec_line = stripped[len("Exec=") :].strip()
            break
    if not exec_line:
        log.debug("[AUTOSTART] Linux .desktop has no Exec= line, treating as valid: %s", desktop_path)
        return True
    # Stale-migration: Electron / pip-era / phantom-launcher shapes are
    try:
        if _autostart_mod._is_legacy_stale_autostart_reference(exec_line):
            log.warning(
                "[AUTOSTART] Linux .desktop references legacy runtime, treating autostart as disabled: %s",
                desktop_path,
            )
            return False
        if _autostart_mod._references_missing_launcher_script(exec_line):
            log.warning(
                "[AUTOSTART] Linux .desktop references missing launcher script, treating autostart as disabled: %s",
                desktop_path,
            )
            return False
        if _autostart_mod._launcher_entry_superseded(exec_line):
            log.warning(
                "[AUTOSTART] Linux .desktop targets the Python launcher while a packaged "
                "Tauri binary is installed, treating autostart as disabled (migrates "
                "to direct-binary on next sync): %s",
                desktop_path,
            )
            return False
    except Exception:
        pass
    # Extract the leading program token per the freedesktop Exec
    program = ""
    in_quotes = False
    for ch in exec_line:
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch.isspace() and not in_quotes:
            break
        program += ch
    if not program or in_quotes:
        # Malformed / unbalanced quote, conservatively valid (can't
        log.debug("[AUTOSTART] Linux .desktop Exec unparseable, treating as valid: %s", desktop_path)
        return True
    if not Path(program).exists():
        log.warning(
            "[AUTOSTART] Linux .desktop references missing program path: %s, treating autostart as disabled",
            program,
        )
        return False
    return True


def _is_autostart_linux() -> bool:
    """True if the .desktop entry exists AND its ``Exec=`` program"""
    desktop_path = _autostart_mod.get_autostart_dir() / "voice-typer.desktop"
    if not desktop_path.exists():
        return False
    return _desktop_exec_path_exists(desktop_path)
