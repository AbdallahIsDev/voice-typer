"""STARTUP-5: POSIX prewarm scheduler (macOS LaunchAgent + Linux systemd user timer).

Previously prewarm was Windows-only — the `fast_startup` config toggle
silently did nothing on macOS/Linux. This module provides the equivalent
scheduling layer for POSIX platforms.

The prewarm script itself (`voice_typer.server.prewarm`) is already
portable (pure Python file reads); only the scheduling layer was
Windows-specific.

Platform coverage:
    macOS: LaunchAgent at ~/Library/LaunchAgents/com.voicetyper.prewarm.plist
           with RunAtLoad=true (fires at every login).
    Linux: systemd user timer at
           ~/.config/systemd/user/voice-typer-prewarm.{service,timer}
           with OnBootSec=10s (fires 10s after the system boots, once
           the user session is active).

Both call: ``python3 -m voice_typer.server.prewarm``
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("voice_typer.prewarm_scheduler_posix")

PREWARM_LABEL = "com.voicetyper.prewarm"


def _prewarm_python() -> str:
    """Return the Python interpreter to use for prewarm on POSIX.

    Uses sys.executable (the interpreter that's running the app).
    On macOS/Linux there's no pythonw/cpython distinction worth tracking.
    """
    return sys.executable


def _prewarm_args() -> list[str]:
    """Return the args list for the prewarm command."""
    return ["-m", "voice_typer.server.prewarm"]


# ─── macOS ─────────────────────────────────────────────────────────────


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PREWARM_LABEL}.plist"


def _build_macos_plist() -> str:
    """Build the LaunchAgent plist for the prewarm task.

    RunAtLoad fires at every login (equivalent to Windows LogonTrigger).
    ProcessType=Background lowers the process priority (equivalent to
    Windows PROCESS_MODE_BACKGROUND_BEGIN).
    """
    from xml.sax.saxutils import escape
    python = _prewarm_python()
    args = _prewarm_args()
    args_xml = "\n".join(
        f"        <string>{escape(a)}</string>" for a in args
    )
    log_path = Path.home() / ".voice-typer" / "prewarm-launchagent.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PREWARM_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(python)}</string>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>{escape(str(log_path))}</string>
    <key>StandardErrorPath</key>
    <string>{escape(str(log_path))}</string>
</dict>
</plist>
"""


def _register_prewarm_macos() -> bool:
    """Register the prewarm LaunchAgent. Returns True on success."""
    try:
        plist_path = _macos_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(_build_macos_plist())
        # Try to load it immediately so it takes effect this session.
        try:
            subprocess.run(
                ["launchctl", "load", str(plist_path)],
                check=False, capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.debug("[PREWARM-POSIX] launchctl load failed (non-fatal): %s", e)
        log.info("[PREWARM-POSIX] macOS LaunchAgent registered: %s", plist_path)
        return True
    except Exception as e:
        log.warning("[PREWARM-POSIX] macOS LaunchAgent registration failed: %s", e)
        return False


def _unregister_prewarm_macos() -> bool:
    """Remove the prewarm LaunchAgent. Returns True on success."""
    plist_path = _macos_plist_path()
    try:
        if plist_path.exists():
            try:
                subprocess.run(
                    ["launchctl", "unload", str(plist_path)],
                    check=False, capture_output=True, timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                log.debug("[PREWARM-POSIX] launchctl unload failed (non-fatal): %s", e)
            plist_path.unlink()
            log.info("[PREWARM-POSIX] macOS LaunchAgent removed")
        return True
    except Exception as e:
        log.warning("[PREWARM-POSIX] macOS LaunchAgent removal failed: %s", e)
        return False


def _is_prewarm_registered_macos() -> bool:
    """True if the macOS LaunchAgent plist exists."""
    return _macos_plist_path().exists()


# ─── Linux (systemd user timer) ────────────────────────────────────────


def _linux_unit_dir() -> Path:
    """Return the systemd user unit directory.

    Uses $XDG_CONFIG_HOME if set AND non-empty (per the XDG Base Directory
    Spec: "If $XDG_CONFIG_HOME is either not set or empty, a default equal
    to $HOME/.config should be used."). Otherwise falls back to
    ~/.config/systemd/user.

    Bug fix: previously used os.environ.get("XDG_CONFIG_HOME", default)
    which has TWO problems:
    1. Eager evaluation: str(Path.home() / ".config") is always computed
       even when XDG_CONFIG_HOME is set (wasteful, less testable).
    2. Empty-string bug: if XDG_CONFIG_HOME="" (set but empty),
       os.environ.get returns "" (not the default), causing Path("") to
       produce a RELATIVE path "systemd/user" — the unit files would be
       written to the current working directory instead of the user's
       config directory, and the timer would never fire.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if not xdg:  # handles both None (unset) and "" (empty string)
        xdg = str(Path.home() / ".config")
    return Path(xdg) / "systemd" / "user"


def _linux_service_path() -> Path:
    return _linux_unit_dir() / "voice-typer-prewarm.service"


def _linux_timer_path() -> Path:
    return _linux_unit_dir() / "voice-typer-prewarm.timer"


def _build_linux_service() -> str:
    """Build the systemd user service unit for prewarm."""
    python = _prewarm_python()
    args = " ".join(_prewarm_args())
    return f"""[Unit]
Description=Voice Typer cache prewarm (torch + model weights)
After=network.target

[Service]
Type=oneshot
ExecStart={python} {args}
# Lower I/O and CPU priority so prewarm never disturbs the user
# (equivalent to Windows PROCESS_MODE_BACKGROUND_BEGIN).
IOSchedulingClass=idle
Nice=10
"""


def _build_linux_timer() -> str:
    """Build the systemd user timer unit for prewarm.

    OnBootSec fires 10 s after the system boots; OnUnitActiveSec re-runs
    every 4 hours while the user is idle (equivalent to the Windows
    IdleTrigger).
    """
    return """[Unit]
Description=Run Voice Typer cache prewarm at boot + periodically

[Timer]
OnBootSec=10s
OnUnitActiveSec=4h
Unit=voice-typer-prewarm.service

[Install]
WantedBy=timers.target
"""


def _register_prewarm_linux() -> bool:
    """Register the prewarm systemd user timer. Returns True on success."""
    try:
        unit_dir = _linux_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        _linux_service_path().write_text(_build_linux_service())
        _linux_timer_path().write_text(_build_linux_timer())
        # Try to enable + start the timer so it takes effect this session.
        for cmd in (
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "voice-typer-prewarm.timer"],
        ):
            try:
                subprocess.run(cmd, check=False, capture_output=True, timeout=10)
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                log.debug("[PREWARM-POSIX] systemctl %s failed (non-fatal): %s", cmd, e)
        log.info("[PREWARM-POSIX] Linux systemd user timer registered")
        return True
    except Exception as e:
        log.warning("[PREWARM-POSIX] Linux timer registration failed: %s", e)
        return False


def _unregister_prewarm_linux() -> bool:
    """Remove the prewarm systemd user timer. Returns True on success."""
    try:
        # Try to stop + disable first (best-effort).
        for cmd in (
            ["systemctl", "--user", "disable", "voice-typer-prewarm.timer"],
            ["systemctl", "--user", "stop", "voice-typer-prewarm.timer"],
        ):
            try:
                subprocess.run(cmd, check=False, capture_output=True, timeout=10)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        removed = False
        if _linux_timer_path().exists():
            _linux_timer_path().unlink()
            removed = True
        if _linux_service_path().exists():
            _linux_service_path().unlink()
            removed = True
        if removed:
            log.info("[PREWARM-POSIX] Linux systemd user timer removed")
        return True
    except Exception as e:
        log.warning("[PREWARM-POSIX] Linux timer removal failed: %s", e)
        return False


def _is_prewarm_registered_linux() -> bool:
    """True if the Linux systemd timer unit exists."""
    return _linux_timer_path().exists() or _linux_service_path().exists()


# ─── Public cross-platform API ─────────────────────────────────────────


def is_supported() -> bool:
    """Return True if POSIX prewarm scheduling is supported here."""
    return sys.platform in ("darwin", "linux")


def is_prewarm_registered() -> bool:
    """Return True if prewarm is registered via the POSIX scheduler."""
    if sys.platform == "darwin":
        return _is_prewarm_registered_macos()
    if sys.platform == "linux":
        return _is_prewarm_registered_linux()
    return False


def register_prewarm_task() -> bool:
    """Register the prewarm task via the POSIX scheduler. Returns True on success."""
    if sys.platform == "darwin":
        return _register_prewarm_macos()
    if sys.platform == "linux":
        return _register_prewarm_linux()
    return False


def unregister_prewarm_task() -> bool:
    """Remove the prewarm task from the POSIX scheduler. Returns True on success."""
    if sys.platform == "darwin":
        return _unregister_prewarm_macos()
    if sys.platform == "linux":
        return _unregister_prewarm_linux()
    return False
