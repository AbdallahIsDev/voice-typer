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
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

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
        # SEC-003: Restrict plist file permissions to owner-only on POSIX.
        # The LaunchAgent plist contains the Python interpreter path and
        # arguments; restricting to 0o600 prevents other users from
        # reading or modifying the launch configuration.
        try:
            plist_path.chmod(0o600)
        except OSError:
            pass
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

    OnBootSec fires once 10 s after the system boots.  Linux prewarm is
    boot-only, matching the Windows LogonTrigger-only design (PREWARM-001)
    — after the first run the OS file cache is already warm, so periodic
    re-runs would be pure wasted I/O (and under memory pressure actively
    harmful, re-reading ~6 GB of files the OS had just evicted).
    """
    return """[Unit]
Description=Run Voice Typer cache prewarm at boot

[Timer]
OnBootSec=10s
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
        # SEC-003: systemd user unit files are written to
        # ~/.config/systemd/user/ which is a per-user directory.
        # Restrictive permissions (0o600) are NOT applied here because:
        # 1. The directory is already per-user (not world-readable).
        # 2. systemd requires the unit files to be readable by the
        #    user's systemd instance, and overly restrictive permissions
        #    can cause systemd to silently skip the unit on some distros.
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
    return is_macos() or is_linux()


def is_prewarm_registered() -> bool:
    """Return True if prewarm is registered via the POSIX scheduler."""
    if is_macos():
        return _is_prewarm_registered_macos()
    if is_linux():
        return _is_prewarm_registered_linux()
    return False


def register_prewarm_task() -> bool:
    """Register the prewarm task via the POSIX scheduler. Returns True on success."""
    if is_macos():
        return _register_prewarm_macos()
    if is_linux():
        return _register_prewarm_linux()
    return False


def unregister_prewarm_task() -> bool:
    """Remove the prewarm task from the POSIX scheduler. Returns True on success."""
    if is_macos():
        return _unregister_prewarm_macos()
    if is_linux():
        return _unregister_prewarm_linux()
    return False


# ─── PLAT-019: systemd user unit for the MAIN app (not just prewarm) ─────


def _linux_app_service_path() -> Path:
    """Path to the main app's systemd user service unit."""
    return _linux_unit_dir() / "voice-typer.service"


def _build_linux_app_service() -> str:
    """PLAT-019: Build the systemd user service unit for the main app.

    Unlike the prewarm service (Type=oneshot), the main app is a
    long-running process (Type=simple) with Restart=on-failure so
    systemd supervises it and restarts after crashes.
    """
    import sys as _sys
    python = _sys.executable
    # Run the IPC server (the main entry point)
    return f"""[Unit]
Description=Voice Typer dictation service
After=graphical-session.target

[Service]
Type=simple
ExecStart={python} -m voice_typer.server.ipc_server
Restart=on-failure
RestartSec=5s
# Lower CPU priority so dictation never starves foreground apps
CPUWeight=20

[Install]
WantedBy=default.target
"""


def register_linux_app_service() -> bool:
    """PLAT-019: Register the main app as a systemd user service.

    Writes the unit file to ~/.config/systemd/user/voice-typer.service
    and runs `systemctl --user daemon-reload`. Does NOT auto-enable —
    the user must run `systemctl --user enable voice-typer.service`
    to switch from .desktop autostart to systemd supervision.

    Returns True on success, False on failure.
    """
    if not is_linux():
        return False
    try:
        unit_dir = _linux_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        service_path = _linux_app_service_path()
        service_path.write_text(_build_linux_app_service(), encoding="utf-8")
        import subprocess
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False, capture_output=True, timeout=5,
        )
        log.info("[PLAT-019] systemd user unit written to %s", service_path)
        log.info("[PLAT-019] Enable with: systemctl --user enable voice-typer.service")
        return True
    except Exception as exc:
        log.warning("[PLAT-019] Failed to register systemd user unit: %s", exc)
        return False


def is_linux_app_service_registered() -> bool:
    """PLAT-019: Check if the main app systemd user unit exists."""
    return _linux_app_service_path().exists()
