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

import contextlib
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

from voice_typer.server import _paths
from voice_typer.server.platform_utils import is_linux, is_macos

log = logging.getLogger(__name__)

PREWARM_LABEL = "com.voicetyper.prewarm"


def _prewarm_python() -> str:
    """Return the Python interpreter to use for prewarm on POSIX.

    Uses sys.executable (the interpreter that's running the app).
    On macOS/Linux there's no pythonw/cpython distinction worth tracking.

    ADR-0020 §5: under the Tauri sidecar path (TAURI_SIDECAR=1 or
    VOICE_TYPER_PREWARM_EXE env set), the prewarm helper is a frozen
    Nuitka exe — there is no Python interpreter to invoke. Delegate to
    :func:`voice_typer.server.prewarm_resolver.resolve_prewarm_exe`
    which returns the frozen exe path (preferred) or the dev-fallback
    Python command line. When the resolver returns a frozen exe path,
    :func:`_prewarm_args` returns an empty list (the exe IS the module).
    """
    if os.environ.get("TAURI_SIDECAR") == "1" or os.environ.get("VOICE_TYPER_PREWARM_EXE"):
        from voice_typer.server.prewarm_resolver import resolve_prewarm_exe

        resolved = resolve_prewarm_exe()
        if resolved is None:
            return sys.executable  # dev fallback
        # If the resolver returned a frozen exe path (no " -m "), return
        # it as-is. Otherwise (dev fallback with " -m "), extract the
        # python interpreter path.
        if " -m " not in resolved:
            return resolved  # frozen exe path
        # Dev fallback — extract the interpreter from the command line.
        # Format: "<path>" -m voice_typer.server.prewarm
        # use ``shlex.split`` instead of the prior
        # ``resolved.split(" ", 1)[0].strip('"')`` so a Python path
        # containing spaces (common on macOS `/Users/My Name/...`)
        # parses correctly.
        try:
            return shlex.split(resolved, posix=True)[0]
        except (ValueError, IndexError):
            return sys.executable
    return sys.executable


def _prewarm_args() -> list[str]:
    """Return the args list for the prewarm command.

    ADR-0020 §5: under the Tauri sidecar path with a frozen exe, the
    args list is empty (the frozen exe IS the module). Under the dev
    fallback or Electron path, the args are ``-m voice_typer.server.prewarm``.
    """
    if os.environ.get("TAURI_SIDECAR") == "1" or os.environ.get("VOICE_TYPER_PREWARM_EXE"):
        from voice_typer.server.prewarm_resolver import resolve_prewarm_exe

        resolved = resolve_prewarm_exe()
        if resolved and " -m " not in resolved:
            return []  # frozen exe — no args
    return ["-m", "voice_typer.server.prewarm"]


# ─── macOS ─────────────────────────────────────────────────────────────


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PREWARM_LABEL}.plist"


def _build_macos_plist() -> str:
    """Build the LaunchAgent plist for the prewarm task.

    RunAtLoad fires at every login (equivalent to Windows LogonTrigger).
    ProcessType=Background lowers the process priority (equivalent to
    Windows PROCESS_MODE_BACKGROUND_BEGIN).

    built via ``xml.etree.ElementTree`` so all five XML
    special characters are escaped by the stdlib. The prior f-string +
    ``xml.sax.saxutils.escape`` builder only escaped &, <, >.
    """
    import xml.etree.ElementTree as ET

    python = _prewarm_python()
    args = _prewarm_args()
    log_path = _paths.prewarm_launchagent_log()

    # build the <dict> body with ElementTree so escaping
    # is automatic for all 5 XML special characters. We assemble the
    # XML body via ElementTree and prepend the DOCTYPE + <plist>
    # wrapper manually (ElementTree's default serialization omits the
    # DOCTYPE, which launchctl accepts but plutil warns about).
    def _text(parent, key, value):
        k = ET.SubElement(parent, "key")
        k.text = key
        s = ET.SubElement(parent, "string")
        s.text = value

    dict_el = ET.Element("dict")
    _text(dict_el, "Label", PREWARM_LABEL)
    prog_key = ET.SubElement(dict_el, "key")
    prog_key.text = "ProgramArguments"
    arr = ET.SubElement(dict_el, "array")
    for a in [python, *args]:
        s = ET.SubElement(arr, "string")
        s.text = a
    for key, value in (
        ("RunAtLoad", "true"),
        ("KeepAlive", "false"),
        ("ProcessType", "Background"),
        ("StandardOutPath", str(log_path)),
        ("StandardErrorPath", str(log_path)),
    ):
        if key in ("RunAtLoad", "KeepAlive"):
            k = ET.SubElement(dict_el, "key")
            k.text = key
            ET.SubElement(dict_el, value)
        else:
            _text(dict_el, key, value)
    body = ET.tostring(dict_el, encoding="unicode")
    body = body.replace("<true />", "<true/>").replace("<false />", "<false/>")
    body = "\n".join("    " + line if line else line for line in body.splitlines())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        f"{body}\n"
        "</plist>\n"
    )


def _register_prewarm_macos() -> bool:
    """Register the prewarm LaunchAgent. Returns True on success."""
    try:
        from voice_typer.server.secure_file_io import _secure_atomic_write

        plist_path = _macos_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        # use ``_secure_atomic_write`` so the plist is
        # written atomically (temp file + ``os.replace``). The prior
        # ``Path.write_text`` did truncate-then-write.
        _secure_atomic_write(plist_path, _build_macos_plist(), durability=False)
        # Restrict plist file permissions to owner-only on POSIX.
        # The LaunchAgent plist contains the Python interpreter path and
        # arguments; restricting to 0o600 prevents other users from
        # reading or modifying the launch configuration.
        with contextlib.suppress(OSError):
            plist_path.chmod(0o600)
        # Try to load it immediately so it takes effect this session.
        try:
            subprocess.run(
                ["launchctl", "load", str(plist_path)],
                check=False,
                capture_output=True,
                timeout=10,
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
                    check=False,
                    capture_output=True,
                    timeout=10,
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


def _systemd_escape_arg(token: str) -> str:
    """Escape a single ExecStart token for systemd's literal syntax.

    systemd's ``ExecStart=`` parses the command line with
    a quote-aware tokenizer. We surround the token with double quotes
    and backslash-escape any literal ``"`` or ``\\`` inside. Newlines
    and other control chars are REJECTED (systemd unit files are
    line-based — a newline in an ExecStart token would inject a new
    unit-file directive, a privilege-escalation vector if the token
    came from an env var).
    """
    if "\n" in token or "\r" in token:
        raise ValueError(
            "systemd ExecStart token contains a newline — refusing to "
            "build a unit file that would inject a new directive"
        )
    for ch in token:
        if ord(ch) < 0x20:
            raise ValueError(
                f"systemd ExecStart token contains control char 0x{ord(ch):02x} "
                "— refusing to build a unit file with non-printable bytes"
            )
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_linux_service() -> str:
    """Build the systemd user service unit for prewarm."""
    python = _prewarm_python()
    args = _prewarm_args()
    # validate + escape each ExecStart token to prevent
    # directive injection via env-var-controlled paths.
    exec_tokens = [_systemd_escape_arg(python)] + [_systemd_escape_arg(a) for a in args]
    exec_start = " ".join(exec_tokens)
    return f"""[Unit]
Description=Voice Typer cache prewarm (torch + model weights)
After=network.target

[Service]
Type=oneshot
ExecStart={exec_start}
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
        from voice_typer.server.secure_file_io import _secure_atomic_write

        unit_dir = _linux_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        # write both unit files atomically (temp +
        # ``os.replace``). The prior ``Path.write_text`` was
        # truncate-then-write.
        _secure_atomic_write(_linux_service_path(), _build_linux_service(), durability=False)
        _secure_atomic_write(_linux_timer_path(), _build_linux_timer(), durability=False)
        # systemd user unit files are written to
        # ~/.config/systemd/user/ which is a per-user directory.
        # Restrictive permissions (0o600) are NOT applied here because:
        # 1. The directory is already per-user (not world-readable).
        # 2. systemd requires the unit files to be readable by the
        #    user's systemd instance, and overly restrictive permissions
        #    can cause systemd to silently skip the unit on some distros.
        # Try to enable + start the timer so it takes effect this session.
        # also ``start`` the timer (best-effort) so prewarm
        # fires on the current session, not just the next boot.
        for cmd in (
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "voice-typer-prewarm.timer"],
            ["systemctl", "--user", "start", "voice-typer-prewarm.timer"],
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
        # also stop the SERVICE unit (not just the timer)
        # so an in-flight oneshot prewarm run is terminated before we
        # unlink the unit files.
        for cmd in (
            ["systemctl", "--user", "disable", "voice-typer-prewarm.timer"],
            ["systemctl", "--user", "stop", "voice-typer-prewarm.timer"],
            ["systemctl", "--user", "stop", "voice-typer-prewarm.service"],
        ):
            with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError):
                subprocess.run(cmd, check=False, capture_output=True, timeout=10)
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


# ─── : systemd user unit for the MAIN app (not just prewarm) ─────


def _linux_app_service_path() -> Path:
    """Path to the main app's systemd user service unit."""
    return _linux_unit_dir() / "voice-typer.service"


def _build_linux_app_service() -> str:
    """Build the systemd user service unit for the main app.

    Unlike the prewarm service (Type=oneshot), the main app is a
    long-running process (Type=simple) with Restart=on-failure so
    systemd supervises it and restarts after crashes.

    ExecStart uses ``voice_typer.server.autostart_launcher --hidden``
    rather than the bare ``voice_typer.server.ipc_server`` backend. The
    launcher is the same entry point the OS autostart (.desktop / LaunchAgent
    / Run key) uses — it spawns the Tauri/Electron frontend AND the IPC
    backend, with ``VT_START_HIDDEN=1`` so the tray starts minimized. The
    bare ``ipc_server`` module is backend-only (no tray, no UI), so the
    prior ExecStart produced a supervised process with no user-visible
    surface — a regression if a user manually ``enable``d the unit.

    TODO(sd_notify): the launcher does not yet emit systemd ``READY=1`` /
    ``WATCHDOG=1`` keepalives, so we deliberately omit ``WatchdogSec=`` —
    adding it now would cause systemd to mark the unit failed after the
    first watchdog interval (the backend never calls ``sd_notify``).
    Adding ``Type=notify`` + ``WatchdogSec=30`` is a future enhancement
    gated on a ``sd_notify`` Python binding in the launcher.
    """
    python = sys.executable
    # run the autostart launcher (which orchestrates the frontend
    # + backend) instead of the bare ipc_server. ``--hidden`` keeps the
    # tray minimized on first start, matching the .desktop autostart path.
    return f"""[Unit]
Description=Voice Typer dictation service
After=graphical-session.target

[Service]
Type=simple
ExecStart={python} -m voice_typer.server.autostart_launcher --hidden
Restart=on-failure
RestartSec=5s
# Lower CPU priority so dictation never starves foreground apps
CPUWeight=20

[Install]
WantedBy=default.target
"""


def register_linux_app_service() -> bool:
    """Register the main app as a systemd user service.

    Writes the unit file to ~/.config/systemd/user/voice-typer.service
    and runs `systemctl --user daemon-reload`. Does NOT auto-enable —
    the user must run `systemctl --user enable voice-typer.service`
    to switch from .desktop autostart to systemd supervision.

    Returns True on success, False on failure.
    """
    if not is_linux():
        return False
    try:
        from voice_typer.server.secure_file_io import _secure_atomic_write

        unit_dir = _linux_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        service_path = _linux_app_service_path()
        # Atomic write (temp + os.replace) — mirrors the sibling
        # _register_prewarm_linux helper at lines 331-339 which already
        # routes both its unit-file writes through
        # _secure_atomic_write(..., durability=False). Pre-fix this call
        # site used Path.write_text (truncate-then-write), so a crash
        # mid-write could leave a half-truncated systemd unit file that
        # systemctl --user daemon-reload would refuse to load.
        _secure_atomic_write(service_path, _build_linux_app_service(), durability=False)
        import subprocess

        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        log.info("[PLAT-019] systemd user unit written to %s", service_path)
        log.info("[PLAT-019] Enable with: systemctl --user enable voice-typer.service")
        return True
    except Exception as exc:
        log.warning("[PLAT-019] Failed to register systemd user unit: %s", exc)
        return False


def is_linux_app_service_registered() -> bool:
    """Check if the main app systemd user unit exists."""
    return _linux_app_service_path().exists()
