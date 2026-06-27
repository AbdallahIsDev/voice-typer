"""Platform-specific adapters: autostart, microphone listing, volume backend."""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SYSTEM = sys.platform  # "win32", "darwin", "linux"


# ─── RDP / remote session detection ──────────────────────────────────


def is_remote_session() -> bool:
    """PLAT-RDP: Detect if the app is running in an RDP/remote session.

    On Windows, uses GetSystemMetrics(SM_REMOTESESSION = 0x1000).
    On Linux, checks $SSH_CLIENT or $SSH_TTY.
    RDP clipboard may be redirected, so clipboard operations may behave
    differently (e.g. clipboard sync delays, missing formats).

    Returns True if a remote session is detected.
    """
    if SYSTEM == "win32":
        try:
            import ctypes
            # SM_REMOTESESSION = 0x1000
            result = ctypes.windll.user32.GetSystemMetrics(0x1000)
            if result:
                log.info("[PLATFORM] RDP/remote session detected (SM_REMOTESESSION=%d)", result)
                return True
        except Exception:
            pass
        return False
    else:
        # Linux/macOS: check for SSH session
        if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
            log.info("[PLATFORM] SSH session detected (SSH_CLIENT/SSH_TTY set)")
            return True
        return False


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
            "is_bluetooth": bool,  # AUDIO-BT: True if Bluetooth/HFP device
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
            # AUDIO-BT: detect Bluetooth devices by name or sample rate.
            # Bluetooth HFP (Hands-Free Profile) devices typically have
            # "Bluetooth", "HFP", or "Hands-Free" in the device name,
            # and operate at 8 or 16 kHz sample rate.
            dev_name_lower = dev["name"].lower()
            is_bluetooth = (
                any(kw in dev_name_lower for kw in ("bluetooth", "hfp", "hands-free"))
                or dev.get("default_samplerate", 0) in (8000, 16000)
            )
            devices.append({
                "id": str(i),
                "index": i,
                "name": dev["name"],
                "host_api": host_api,
                "channels": dev["max_input_channels"],
                "default": i == default_index,
                "is_bluetooth": is_bluetooth,
            })
            if is_bluetooth:
                log.warning(
                    "[PLATFORM] Bluetooth/HFP device detected: %s "
                    "(sample_rate=%s). Audio quality may be limited. "
                    "Consider disabling the hands-free telephony profile "
                    "in Bluetooth settings for better quality.",
                    dev["name"], dev.get("default_samplerate", "?"),
                )
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

def _desktop_quote(arg: str) -> str:
    """Quote ``arg`` per the freedesktop Desktop Entry Spec's Exec rules.

    The spec (https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html)
    says: "If an argument contains a reserved character then the argument
    must be quoted in its entirety. Reserved characters are space, tab,
    newline, double quote, single quote, backslash, greater-than sign,
    less-than sign, tilde, pipe character, ampersand, semicolon, dollar
    sign, asterisk, question mark, hash, parentheses."

    Within a quoted string, the characters ``" \\ ` $`` must be escaped
    with a backslash.

    NEW-XPLAT-007: previously the code just wrapped paths in double
    quotes without escaping backslashes or quotes — so a path like
    ``C:\\Users\\John "Bob"\\app`` would corrupt the .desktop Exec
    field.  We now do proper spec-compliant quoting.
    """
    reserved = set(' \t\n"\'\\><~|&;$*?#()')
    if not any(c in reserved for c in arg):
        return arg  # no quoting needed
    # Escape backslash, double-quote, backtick, dollar per spec.
    escaped = (
        arg.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


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

    STARTUP-2: also passes ``--delay 30`` so the launcher waits 30 s
    before spawning Electron. This gives the prewarm task (which now
    fires at logon+0 s) a head start on warming the OS file cache,
    so the app's cold imports of torch/transformers hit RAM instead
    of contending with prewarm on disk.

    NEW-XPLAT-007: the result is properly quoted per the freedesktop
    Desktop Entry Spec's Exec-quoting rules so paths containing
    spaces, apostrophes, or other reserved characters (e.g.
    ``/home/john doe/voice-typer``) survive XFCE's and KDE's
    .desktop file parsers without truncation.
    """
    # The launcher lives next to this module (voice_typer/server/).
    launcher = Path(__file__).resolve().parent / "autostart_launcher.py"
    # Build the argument list, then quote each arg per the desktop spec.
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        python_bin = str(pythonw) if pythonw.exists() else sys.executable
        args = [python_bin, str(launcher), "--hidden", "--delay", "30"]
    else:
        # macOS / Linux: use the current interpreter.
        args = [sys.executable, str(launcher), "--hidden"]

    # PLAT-VENV: When registering autostart, use the system Python
    # interpreter path instead of sys.executable if inside a virtualenv.
    # sys.prefix != sys.base_prefix detects virtualenv/venv.
    # PyInstaller builds have sys.prefix == sys.base_prefix so this
    # only affects development setups.
    python_exe = sys.executable
    if sys.prefix != sys.base_prefix:
        # We're inside a virtualenv — try to find the system Python
        import shutil
        base_python = "python3" if sys.platform != "win32" else "python.exe"
        system_python = shutil.which(base_python)
        if system_python:
            log.info(
                "[AUTOSTART] Running inside venv (%s); using system Python: %s",
                python_exe, system_python,
            )
            # Replace the python binary in the args
            args = [system_python if a == python_exe else a for a in args]
        else:
            log.warning(
                "[AUTOSTART] Running inside venv (%s) and system Python not "
                "found on PATH. Using venv Python — autostart may break if "
                "the venv is deleted.",
                python_exe,
            )
    return " ".join(_desktop_quote(arg) for arg in args)


def get_autostart_dir() -> Path:
    if SYSTEM == "win32":
        return (
            Path(os.environ.get("APPDATA", Path.home()))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )
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

# STARTUP-7: Task Scheduler logon trigger fires earlier and more
# predictably than HKCU Run keys (which are gated by Windows Explorer's
# startup sequencing). We prefer the Task Scheduler path; HKCU Run key
# remains as a fallback for the locked-task scenario.
_APP_AUTOSTART_TASK_NAME = "VoiceTyperAutostart"


def _enable_autostart_windows() -> bool:
    """STARTUP-7: register app autostart via Task Scheduler (preferred)
    or HKCU Run key (fallback). Task Scheduler logon triggers fire
    earlier than Run keys, reducing the ~33 s pre-app delay.
    """
    # Try Task Scheduler first (earlier, more predictable timing).
    if _register_app_autostart_task():
        # Clean up any stale Run-key entry from a previous install.
        _unregister_app_autostart_runkey()
        return True
    # Fall back to HKCU Run key if Task Scheduler is unavailable/locked.
    log.warning("[CONFIG] Task Scheduler autostart failed; falling back to HKCU Run key")
    return _register_app_autostart_runkey()


def _disable_autostart_windows() -> bool:
    """STARTUP-7: remove app autostart from BOTH Task Scheduler and Run key."""
    removed_task = _unregister_app_autostart_task()
    removed_reg = _unregister_app_autostart_runkey()
    return removed_task or removed_reg


def _is_autostart_windows() -> bool:
    """STARTUP-7: True if autostart is registered via EITHER mechanism."""
    return _is_app_autostart_task_registered() or _is_app_autostart_runkey_registered()


# ── Task Scheduler autostart (preferred) ──────────────────────────────


def _app_autostart_command_and_args() -> tuple[str, str]:
    """Return (pythonw_path, arguments) for the app autostart task.

    STARTUP-7: same launcher + --hidden + --delay 30 as the Run-key path,
    but split into Command + Arguments for the Task Scheduler XML so we
    avoid the cmd.exe wrapper (mirrors the prewarm task fix from Round 8).

    PLAT-VENV: Uses system Python if running inside a virtualenv.
    """
    launcher = Path(__file__).resolve().parent / "autostart_launcher.py"
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    python_bin = str(pythonw) if pythonw.exists() else sys.executable

    # PLAT-VENV: detect virtualenv and use system Python instead
    if sys.prefix != sys.base_prefix:
        import shutil
        base_python = "python3" if sys.platform != "win32" else "python.exe"
        system_python = shutil.which(base_python)
        if system_python:
            python_bin = system_python
    args = f'"{launcher}" --hidden --delay 30'
    return python_bin, args


def _build_app_autostart_task_xml() -> str:
    """Build the Task Scheduler XML for the app autostart task.

    STARTUP-7: fires at logon with PT0S delay (Run keys fire ~33 s
    after logon; Task Scheduler logon triggers fire immediately).
    The launcher's --delay 30 flag gives prewarm a head start on
    warming the OS file cache.
    """
    import xml.etree.ElementTree as ET
    python_exe, arguments = _app_autostart_command_and_args()

    root = ET.Element("Task", {
        "version": "1.4",
        "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task",
    })
    reg = ET.SubElement(root, "RegistrationInfo")
    desc = ET.SubElement(reg, "Description")
    desc.text = (
        "Launches Voice Typer at user logon. Safe to disable or delete; "
        "the app can still be started manually from the Start Menu."
    )
    uri = ET.SubElement(reg, "URI")
    uri.text = f"\\{_APP_AUTOSTART_TASK_NAME}"

    triggers = ET.SubElement(root, "Triggers")
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    # PT0S: fire immediately at logon (launcher's --delay 30 handles
    # the prewarm head-start; no Task Scheduler delay needed).
    ET.SubElement(logon, "Delay").text = "PT0S"

    principal = ET.SubElement(root, "Principals")
    princ = ET.SubElement(principal, "Principal", {"id": "Author"})
    ET.SubElement(princ, "LogonType").text = "InteractiveToken"
    ET.SubElement(princ, "RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "ExecutionTimeLimit").text = "PT0S"  # no limit
    ET.SubElement(settings, "Hidden").text = "true"
    ET.SubElement(settings, "RunOnlyIfNetworkAvailable").text = "false"
    ET.SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    ET.SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"

    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    exec_el = ET.SubElement(actions, "Exec")
    # STARTUP-1 lesson: use pythonw.exe directly, no cmd.exe wrapper.
    ET.SubElement(exec_el, "Command").text = python_exe
    ET.SubElement(exec_el, "Arguments").text = arguments

    return ET.tostring(root, encoding="unicode")


def _register_app_autostart_task() -> bool:
    """Register the app autostart Task Scheduler task. Returns True on success.

    Bug fix: removed redundant sys.platform != 'win32' check —
    task_scheduler.is_supported() already handles platform detection
    and is the single source of truth. The redundant check made the
    function untestable on non-Windows without monkeypatching sys.platform.
    """
    try:
        from voice_typer.server import task_scheduler
        if not task_scheduler.is_supported():
            return False
        xml_def = _build_app_autostart_task_xml()
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(xml_def)
            temp_xml = tf.name
        try:
            try:
                task_scheduler._schtasks(["/Delete", "/TN", _APP_AUTOSTART_TASK_NAME, "/F"], capture=True)
            except Exception:
                pass
            rc, output = task_scheduler._schtasks(
                ["/Create", "/TN", _APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                capture=True,
            )
            if rc == 0:
                log.info("[CONFIG] App autostart registered via Task Scheduler (logon trigger)")
                return True
            log.warning("[CONFIG] Task Scheduler autostart registration failed: %s", output.strip())
            return False
        finally:
            try:
                os.unlink(temp_xml)
            except OSError:
                pass
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart registration raised: %s", e)
        return False


def _unregister_app_autostart_task() -> bool:
    """Remove the app autostart Task Scheduler task. Returns True on success.

    Bug fix: removed redundant sys.platform != 'win32' check.
    """
    try:
        from voice_typer.server import task_scheduler
        if not task_scheduler.is_supported():
            return False
        rc, output = task_scheduler._schtasks(["/Delete", "/TN", _APP_AUTOSTART_TASK_NAME, "/F"], capture=True)
        if rc == 0:
            log.info("[CONFIG] App autostart Task Scheduler task removed")
            return True
        if "cannot find" in output.lower() or "does not exist" in output.lower():
            return True  # already absent
        return False
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart removal raised: %s", e)
        return False


def _is_app_autostart_task_registered() -> bool:
    """True if the app autostart Task Scheduler task exists.

    Bug fix: removed redundant sys.platform != 'win32' check.
    """
    try:
        from voice_typer.server import task_scheduler
        if not task_scheduler.is_supported():
            return False
        rc, _ = task_scheduler._schtasks(["/Query", "/TN", _APP_AUTOSTART_TASK_NAME, "/XML"])
        return rc == 0
    except Exception:
        return False


# ── HKCU Run-key autostart (fallback) ─────────────────────────────────


def _run_key_name() -> str:
    """PLAT-RUN: Return a deterministic registry key name based on install path.

    Uses a hash of sys.executable so different installs (e.g. stable vs
    dev) don't conflict, and stale entries from removed installs can be
    cleaned up.
    """
    import hashlib
    install_hash = hashlib.sha256(sys.executable.encode()).hexdigest()[:8]
    return f"VoiceTyper_{install_hash}"


def _register_app_autostart_runkey() -> bool:
    """Register app autostart via HKCU Run key (admin-free fallback)."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: Use deterministic key name based on install path
    # to prevent conflicting entries from different installs
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        try:
            cmd = _autostart_command()
            winreg.SetValueEx(key, reg_key_name, 0, winreg.REG_SZ, cmd)
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart enabled via HKCU Run key (fallback): %s", cmd)

        # PLAT-RUN: Clean stale entries whose path no longer exists
        try:
            run_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run",
                                     0, winreg.KEY_ALL_ACCESS)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(run_key, i)
                    if name.startswith("VoiceTyper") and name != reg_key_name:
                        # Check if the path still exists
                        if isinstance(value, str):
                            exe_path = value.strip('"').split('"')[0] if '"' in value else value.split()[0]
                            if not Path(exe_path).exists():
                                winreg.DeleteValue(run_key, name)
                                log.info("[AUTOSTART] Removed stale entry: %s", name)
                                continue
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(run_key)
        except Exception:
            pass

        return True
    except OSError as e:
        log.warning("[CONFIG] HKCU Run key autostart failed: %s", e)
        return False


def _unregister_app_autostart_runkey() -> bool:
    """Remove app autostart from HKCU Run key."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, reg_key_name)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart removed from HKCU Run key")
        return True
    except OSError:
        return False


def _is_app_autostart_runkey_registered() -> bool:
    """True if the HKCU Run key has the VoiceTyper entry."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, reg_key_name)
            return bool(val)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ─── macOS ─────────────────────────────────────────────────────────────

def _enable_autostart_macos() -> bool:
    from xml.sax.saxutils import escape
    plist_dir = get_autostart_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.voicetyper.plist"
    launcher = Path(__file__).resolve().parent / "autostart_launcher.py"

    # NEW-XPLAT-006: previously the plist's ``WorkingDirectory`` was
    # set to the literal string ``~``.  launchd does NOT expand ``~``
    # in plist values — the WorkingDirectory must be an absolute path.
    # The literal ``~`` caused launchd to fail to chdir into anything
    # (silently on some macOS versions, noisily on others), so the
    # autostarted Python process inherited launchd's ``/`` working
    # directory — which in turn made relative file operations in
    # autostart_launcher.py resolve to the wrong place.
    working_dir = str(Path.home())

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
    <string>{escape(working_dir)}</string>
    <key>StandardOutPath</key>
    <string>{escape(str(Path.home() / ".voice-typer" / "autostart.log"))}</string>
    <key>StandardErrorPath</key>
    <string>{escape(str(Path.home() / ".voice-typer" / "autostart.log"))}</string>
</dict>
</plist>"""
    plist_path.write_text(plist_content)
    plist_path.chmod(0o600)
    # NEW-PRIV-002: ensure the log directory exists with private perms
    log_dir = Path.home() / ".voice-typer"
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(log_dir, 0o700)
    except OSError:
        pass
    try:
        import subprocess
        # NEW-XPLAT-005: previously ``launchctl load`` had no timeout,
        # so a hung launchd (rare but possible after a macOS upgrade
        # or in a stuck boot) would block this thread forever.  The
        # 5-second timeout matches what the Apple docs say is the
        # upper bound for a healthy launchctl load.
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            check=False,
            capture_output=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        log.warning("[CONFIG] launchctl load timed out after 5s — launchd may be unresponsive")
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

    Uses the pre-rendered logo PNG (from ``client/scripts/logo.svg``,
    rendered by ``generate-icons.mjs``).  Saves to
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


def is_windows() -> bool:
    """CQ-029: Check if running on Windows."""
    import sys
    return sys.platform == "win32"

def is_macos() -> bool:
    """CQ-029: Check if running on macOS."""
    import sys
    return sys.platform == "darwin"

def is_linux() -> bool:
    """CQ-029: Check if running on Linux."""
    import sys
    return sys.platform == "linux"

