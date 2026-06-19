"""Platform-specific adapters: autostart, microphone listing, volume backend."""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SYSTEM = sys.platform  # "win32", "darwin", "linux"


# ─── Volume backend factory ────────────────────────────────────────────


def get_volume_backend() -> Optional["object"]:
    """Return the appropriate :class:`VolumeBackend` for this platform.

    Returns ``None`` if the platform is not supported (no backend class
    exists).  The returned backend is **not yet initialised** — the
    caller must call ``initialize()`` to verify that native libraries
    are available.

    Selection:
      - ``win32``  → :class:`WinVolumeBackend` (pycaw)
      - ``darwin`` → :class:`MacVolumeBackend` (CoreAudio / osascript)
      - ``linux``  → :class:`LinuxVolumeBackend` (pactl → wpctl → amixer)
    """
    try:
        if SYSTEM == "win32":
            from voice_typer.server.volume_backends import WinVolumeBackend
            return WinVolumeBackend()
        elif SYSTEM == "darwin":
            from voice_typer.server.volume_backends import MacVolumeBackend
            return MacVolumeBackend()
        elif SYSTEM == "linux":
            from voice_typer.server.volume_backends import LinuxVolumeBackend
            return LinuxVolumeBackend()
        else:
            log.debug("[VOLUME] Unsupported platform: %s", SYSTEM)
            return None
    except Exception as exc:
        log.warning("[VOLUME] Failed to create backend for %s: %s", SYSTEM, exc)
        return None


# ─── Microphone helpers ────────────────────────────────────────────────

def _is_non_mic_device(name: str) -> bool:
    """Return True if the device name matches a known non-microphone input pattern."""
    lower = name.lower().strip()

    # Loopback / what-u-hear devices (captures speaker output, useless for voice)
    if any(p in lower for p in ["stereo mix", "what u hear", "wave out mix", "mono mix"]):
        return True

    # Physical line input jacks (silent unless something is plugged in)
    if any(p in lower for p in ["line in", "line input"]):
        return True

    # Auxiliary input
    if lower in ("aux", "auxiliary") or lower.startswith("aux ") or lower.startswith("auxiliary "):
        return True

    # System virtual devices that just mirror the default device
    # (redundant with "System Default" menu option)
    if any(p in lower for p in ["microsoft sound mapper", "primary sound capture driver"]):
        return True

    return False


def list_microphones() -> list[dict]:
    """Return available input devices with stable identifiers.

    Each dict:
        {
            "id": str,          # stable identifier (device index as string)
            "index": int,       # sounddevice device index
            "name": str,        # display name
            "host_api": str,    # host API name (e.g. "Windows WASAPI")
            "channels": int,    # max input channels
            "default": bool,    # True if system default input device
        }
    Returns empty list on failure.
    """
    try:
        import sounddevice as sd
        default_input = sd.query_devices(kind="input")
        default_index = default_input["index"] if default_input else -1
        devices = []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue
            if _is_non_mic_device(dev["name"]):
                continue
            host_api = ""
            try:
                host_api_idx = dev.get("hostapi", 0)
                host_api = sd.query_hostapis(host_api_idx)["name"]
            except Exception:
                pass
            devices.append({
                "id": str(i),
                "index": i,
                "name": dev["name"],
                "host_api": host_api,
                "channels": dev["max_input_channels"],
                "default": i == default_index,
            })
        return devices
    except Exception:
        log.debug("Could not enumerate microphones", exc_info=True)
        return []


def find_microphone_by_name(partial_name: str) -> Optional[dict]:
    """Find a microphone whose name contains *partial_name* (case-insensitive)."""
    lower = partial_name.lower()
    for mic in list_microphones():
        if lower in mic["name"].lower():
            return mic
    return None


def find_microphone_by_id(mic_id: str) -> Optional[dict]:
    """Find a microphone by its stable ID (device index string)."""
    for mic in list_microphones():
        if mic["id"] == mic_id:
            return mic
    return None


# ─── Autostart ─────────────────────────────────────────────────────────

def _autostart_command() -> str:
    """Build the command that the OS autostart entry should run.

    Runs the universal ``autostart_launcher`` module with ``--hidden``,
    which:
      • if the app is already running → focuses its window via the
        single-instance lock and exits (idempotent re-login, etc.);
      • if not running → spawns ``npm run dev`` with ``VT_START_HIDDEN=1``,
        so Electron starts its dashboard HIDDEN (tray + bubble still work)
        instead of popping a window over the user's desktop at login.

    On Windows, prefers ``pythonw.exe`` (no console window) when
    available so login doesn't flash a console.  Falls back to
    ``python.exe`` if ``pythonw.exe`` is absent.
    """
    # The launcher lives next to this module (voice_typer/server/).
    launcher = Path(__file__).resolve().parent / "autostart_launcher.py"
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        python_bin = pythonw if pythonw.exists() else Path(sys.executable)
        return f'"{python_bin}" "{launcher}" --hidden'
    # macOS / Linux: use the current interpreter, quoted.
    return f'"{sys.executable}" "{launcher}" --hidden'


def get_autostart_dir() -> Path:
    if SYSTEM == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    elif SYSTEM == "darwin":
        return Path.home() / "Library" / "LaunchAgents"
    else:
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"


def enable_autostart() -> bool:
    try:
        if SYSTEM == "win32":
            return _enable_autostart_windows()
        elif SYSTEM == "darwin":
            return _enable_autostart_macos()
        else:
            return _enable_autostart_linux()
    except Exception as e:
        log.error("[CONFIG] Failed to enable autostart: %s", e)
        return False


def disable_autostart() -> bool:
    try:
        if SYSTEM == "win32":
            return _disable_autostart_windows()
        elif SYSTEM == "darwin":
            return _disable_autostart_macos()
        else:
            return _disable_autostart_linux()
    except Exception as e:
        log.error("[CONFIG] Failed to disable autostart: %s", e)
        return False


def is_autostart_enabled() -> bool:
    try:
        if SYSTEM == "win32":
            return _is_autostart_windows()
        elif SYSTEM == "darwin":
            return _is_autostart_macos()
        else:
            return _is_autostart_linux()
    except Exception:
        return False


# ─── Windows ───────────────────────────────────────────────────────────

def _enable_autostart_windows() -> bool:
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE,
    )
    try:
        cmd = _autostart_command()
        winreg.SetValueEx(key, "VoiceTyper", 0, winreg.REG_SZ, cmd)
    finally:
        winreg.CloseKey(key)
    log.info("[CONFIG] Autostart enabled (Windows): %s", cmd)
    return True


def _disable_autostart_windows() -> bool:
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE,
    )
    try:
        winreg.DeleteValue(key, "VoiceTyper")
    except FileNotFoundError:
        pass
    winreg.CloseKey(key)
    log.info("[CONFIG] Autostart disabled (Windows)")
    return True


def _is_autostart_windows() -> bool:
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, "VoiceTyper")
            return bool(val)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False


# ─── macOS ─────────────────────────────────────────────────────────────

def _enable_autostart_macos() -> bool:
    from xml.sax.saxutils import escape
    plist_dir = get_autostart_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.voicetyper.plist"
    launcher = Path(__file__).resolve().parent / "autostart_launcher.py"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voicetyper</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(sys.executable)}</string>
        <string>{escape(str(launcher))}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>WorkingDirectory</key>
    <string>~</string>
    <key>StandardOutPath</key>
    <string>/tmp/voice-typer-autostart.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/voice-typer-autostart.log</string>
</dict>
</plist>"""
    plist_path.write_text(plist_content)
    plist_path.chmod(0o644)
    try:
        import subprocess
        subprocess.run(["launchctl", "load", str(plist_path)], check=False, capture_output=True)
    except Exception as e:
        log.warning("[CONFIG] launchctl load failed: %s", e)
    log.info("[CONFIG] Autostart enabled (macOS): %s", plist_path)
    return True


def _disable_autostart_macos() -> bool:
    import subprocess
    plist_path = get_autostart_dir() / "com.voicetyper.plist"
    # Unload the running job BEFORE deleting the plist, otherwise the
    # job keeps running until next logout even though it's "disabled".
    # Prefer the modern `launchctl bootout` (macOS 10.10+) and fall back
    # to the legacy `launchctl remove` for older systems.  Both are
    # best-effort — failure here just means the job lingers until logout.
    label = "com.voicetyper"
    for args in (
        ["launchctl", "bootout", f"gui/{_os_uid()}/{label}"],
        ["launchctl", "remove", label],
    ):
        try:
            subprocess.run(
                args, check=False, capture_output=True, timeout=5,
            )
        except Exception:
            pass
    if plist_path.exists():
        plist_path.unlink()
    log.info("[CONFIG] Autostart disabled (macOS)")
    return True


def _os_uid() -> int:
    """Return the current user's numeric uid (for launchctl bootout target)."""
    try:
        return os.getuid()
    except (AttributeError, OSError):
        return 501  # default first user on macOS


def _is_autostart_macos() -> bool:
    return (get_autostart_dir() / "com.voicetyper.plist").exists()


# ─── Linux ─────────────────────────────────────────────────────────────

def _enable_autostart_linux() -> bool:
    autostart_dir = get_autostart_dir()
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = autostart_dir / "voice-typer.desktop"

    # _autostart_command() returns each space-containing argument already
    # double-quoted per the Desktop Entry Spec's Exec quoting rules
    # (https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html).
    # Use the command VERBATIM — stripping quotes corrupts the first arg
    # (e.g. "/usr/bin/python3" "/path/launcher.py" -> python3" "/path...).
    exec_field = _autostart_command()

    desktop_content = f"""[Desktop Entry]
Type=Application
Name=Voice Typer
Comment=Background voice-to-text utility
Exec={exec_field}
Icon=audio-input-microphone
Hidden=false
NoDisplay=true
"""
    desktop_path.write_text(desktop_content)
    log.info("[CONFIG] Autostart enabled (Linux): %s", desktop_path)
    return True


def _disable_autostart_linux() -> bool:
    desktop_path = get_autostart_dir() / "voice-typer.desktop"
    if desktop_path.exists():
        desktop_path.unlink()
    log.info("[CONFIG] Autostart disabled (Linux)")
    return True


def _is_autostart_linux() -> bool:
    return (get_autostart_dir() / "voice-typer.desktop").exists()


# ─── Launcher shortcut ────────────────────────────────────────────────

def _generate_icon_ico() -> Optional[Path]:
    """Generate a logo .ico file for the shortcut icon.

    Uses the pre-rendered vt_logo.svg PNG.  Saves to
    ``%APPDATA%/voice-typer/icon.ico`` and returns the path, or None
    on failure.
    """
    try:
        from PIL import Image
    except ImportError:
        log.debug("PIL not available — cannot generate icon")
        return None

    icon_png = Path(__file__).resolve().parent / "assets" / "logo-256.png"
    if not icon_png.exists():
        log.debug("Pre-rendered logo PNG not found — cannot generate icon")
        return None

    appdata = Path(os.environ.get("APPDATA", Path.home()))
    icon_dir = appdata / "voice-typer"
    icon_dir.mkdir(parents=True, exist_ok=True)
    ico_path = icon_dir / "icon.ico"

    img = Image.open(str(icon_png)).convert("RGBA")

    try:
        img.save(str(ico_path), format="ICO", sizes=[(256, 256)])
        log.info("[STARTUP] Shortcut icon saved: %s", ico_path)
        return ico_path
    except OSError as e:
        log.warning("[STARTUP] Failed to save icon: %s", e)
        return None


def _universal_launcher_path() -> Path:
    """Path to autostart_launcher.py — the single universal launch entry point."""
    return Path(__file__).resolve().parent / "autostart_launcher.py"


def _start_menu_programs_dir() -> Path:
    """Windows Start Menu → Programs directory for the current user.

    Shortcuts placed here are discoverable via Start Menu search, so the
    user can type "Voice Typer" to open/focus the app.
    """
    return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _create_lnk_shortcut(
    lnk_path: Path,
    target: str,
    arguments: str,
    icon_ico: Optional[Path],
    description: str,
) -> bool:
    """Create a single .lnk shortcut. Returns True on success.

    Tries win32com first (fast, native COM).  Falls back to a PowerShell
    script written to a temp file — always available on Windows, no extra
    packages needed, and avoids string-escaping problems.
    """
    # 1) win32com path (native COM, fastest).
    try:
        import win32com.client  # noqa: F811

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        shortcut.Targetpath = target
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = str(Path.home())
        shortcut.Description = description
        if icon_ico:
            shortcut.IconLocation = str(icon_ico)
        shortcut.save()
        return True
    except ImportError:
        log.debug("[STARTUP] win32com unavailable — trying PowerShell fallback")
    except OSError as e:
        log.warning("[STARTUP] Failed to create .lnk (%s): %s", lnk_path, e)
        return False

    # 2) PowerShell fallback — write a temp .ps1 to avoid escaping issues.
    import os as _os
    import subprocess
    import tempfile

    tmp = None
    try:

        def _q(s):
            """Double every ``"`` for embedding in a PS double-quoted string."""
            return str(s).replace('"', '""')

        lines = [
            "$s = New-Object -ComObject WScript.Shell",
            f'$l = $s.CreateShortcut("{_q(lnk_path)}")',
            f'$l.TargetPath = "{_q(target)}"',
            # arguments already has surrounding double quotes — use _q() escaping
            f'$l.Arguments = "{_q(arguments)}"',
            f'$l.Description = "{_q(description)}"',
            f'$l.WorkingDirectory = "{_q(Path.home())}"',
        ]
        if icon_ico:
            lines.append(f'$l.IconLocation = "{_q(icon_ico)}"')
        lines.append("$l.Save()")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write("\n".join(lines))
            tmp = f.name

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp],
            check=True, capture_output=True, timeout=30,
        )
        log.info("[STARTUP] .lnk created via PowerShell fallback: %s", lnk_path)
        return True
    except Exception as e:
        log.warning("[STARTUP] PowerShell .lnk creation failed: %s", e)
        return False
    finally:
        if tmp is not None:
            try:
                _os.unlink(tmp)
            except OSError:
                pass


def create_launcher_shortcut() -> Optional[Path]:
    """Create Desktop + Start Menu shortcuts for Voice Typer.

    Both shortcuts point at the **universal launcher** (autostart_launcher.py)
    WITHOUT ``--hidden``, so a user click:
      • if the app is already running → focuses its window (via the
        Electron single-instance lock), no second instance;
      • if not running → starts Electron + backend with the dashboard visible.

    This fixes the old bug where the desktop shortcut ran the backend ONLY
    (``pythonw -m voice_typer``), which meant the bubble overlay never
    appeared and Electron never connected to that process.

    Returns the path to the Desktop shortcut (the primary one), or None on
    unsupported platforms / failure.
    """
    if SYSTEM != "win32":
        log.info("[STARTUP] Launcher shortcut only supported on Windows")
        return None

    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.exists():
        log.warning("[STARTUP] pythonw.exe not found at %s — cannot create console-free launcher", pythonw)
        return None

    launcher = _universal_launcher_path()
    desktop = Path.home() / "Desktop"
    start_menu = _start_menu_programs_dir()
    icon_ico = _generate_icon_ico()

    # Primary: Desktop .lnk pointing at the universal launcher (no --hidden).
    primary_path: Optional[Path] = None
    lnk_desktop = desktop / "Voice Typer.lnk"
    if _create_lnk_shortcut(
        lnk_desktop,
        target=str(pythonw),
        arguments=f'"{launcher}"',
        icon_ico=icon_ico,
        description="Voice Typer — voice-to-text dictation",
    ):
        log.info("[STARTUP] Desktop .lnk created: %s", lnk_desktop)
        primary_path = lnk_desktop
    else:
        log.warning("[STARTUP] Could not create desktop .lnk — install pywin32 or check logs")

    # Secondary: Start Menu copy so Start search finds "Voice Typer".
    try:
        start_menu.mkdir(parents=True, exist_ok=True)
        lnk_start = start_menu / "Voice Typer.lnk"
        if _create_lnk_shortcut(
            lnk_start,
            target=str(pythonw),
            arguments=f'"{launcher}"',
            icon_ico=icon_ico,
            description="Voice Typer — voice-to-text dictation",
        ):
            log.info("[STARTUP] Start Menu .lnk created: %s", lnk_start)
    except OSError as e:
        log.debug("[STARTUP] Start Menu shortcut skipped: %s", e)

    return primary_path
