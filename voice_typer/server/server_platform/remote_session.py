"""RDP / SSH remote-session detection + non-microphone device predicate."""

from __future__ import annotations

import logging
import os
import re

# Patch-path bridge: read ``SYSTEM`` through the owning
from voice_typer.server.server_platform import platform_flags as _platform_flags

log = logging.getLogger(__name__)


# POSIX env vars that indicate a remote-desktop session. Each
_POSIX_REMOTE_SESSION_ENV_VARS: tuple[str, ...] = (
    "SSH_CLIENT",
    "SSH_TTY",
    "VNCDESKTOP",
    "X2GO_SESSION",
    "NX_TEMP",
    "CITRIX_SESSION",
)


def _posix_proc_has_remote_desktop() -> bool:
    """scan /proc/*/comm for remote-desktop daemon processes."""
    import os.path

    # The process names to look for (kernel truncates comm at 15 chars,
    targets = ("Xvnc", "x2goagent", "nxagent")

    # Walk /proc/*/comm. Each entry is a single short line. We bound
    try:
        proc_entries = os.listdir("/proc")
    except (OSError, FileNotFoundError):
        return False

    checked = 0
    for entry in proc_entries:
        if not entry.isdigit():
            continue
        checked += 1
        if checked > 4096:
            break
        comm_path = f"/proc/{entry}/comm"
        try:
            with open(comm_path, encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
        except (OSError, FileNotFoundError):
            continue
        if any(target in comm for target in targets):
            return True
    return False


def is_remote_session() -> bool:
    """PLAT-RDP: Detect if the app is running in an RDP/remote session."""
    if _platform_flags.SYSTEM == "win32":
        return _is_windows_remote_session()
    else:
        return _is_posix_remote_session()


def _is_windows_remote_session() -> bool:
    """Windows remote-session detection."""
    try:
        import ctypes
    except Exception:
        log.debug("[PLATFORM] ctypes not importable for Windows remote-session probe")
        return False

    # Primary: SM_REMOTESESSION = 0x1000
    try:
        result = ctypes.windll.user32.GetSystemMetrics(0x1000)
        if result:
            log.info("[PLATFORM] RDP/remote session detected (SM_REMOTESESSION=%d)", result)
            return True
    except Exception:
        log.debug("[PLATFORM] SM_REMOTESESSION probe failed", exc_info=True)

    # Secondary (): WTSQuerySessionInformation for WVD / Azure
    try:
        wtsapi32 = ctypes.windll.wtsapi32
        kernel32 = ctypes.windll.kernel32

        # WTS_CURRENT_SESSION = -1 (the calling session).
        WTS_CURRENT_SESSION = -1  # noqa: N806
        # WTSConnectState class index = 8 (from the WTS_INFO_CLASS enum).
        WTSConnectState = 8  # noqa: N806

        buffer = ctypes.c_void_p()
        bytes_returned = ctypes.c_ulong(0)
        # WTSQuerySessionInformationW(handle, session, class, &buffer, &bytes)
        ok = wtsapi32.WTSQuerySessionInformationW(
            ctypes.c_void_p(0),  # WTS_CURRENT_SERVER_HANDLE = NULL
            WTS_CURRENT_SESSION,
            WTSConnectState,
            ctypes.byref(buffer),
            ctypes.byref(bytes_returned),
        )
        if ok and bytes_returned.value >= 4:
            # The returned buffer is a DWORD (4 bytes) holding the
            connect_state = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ulong)).contents.value
            # Free the buffer. WTSFreeMemory is mandatory on success.
            import contextlib

            with contextlib.suppress(Exception):
                wtsapi32.WTSFreeMemory(buffer)
            # Get the physical console session ID. If the current
            console_session_id = kernel32.WTSGetActiveConsoleSessionId()
            # ``WTSGetActiveConsoleSessionId`` returns 0xFFFFFFFF if
            if connect_state == 0 and console_session_id == 0xFFFFFFFF:
                log.warning(
                    "[PLATFORM] Remote Windows session detected via WTS API "
                    "(WTSActive, no physical console). SM_REMOTESESSION missed this "
                    "(likely Windows Virtual Desktop / Azure RemoteApp)"
                )
                return True
    except Exception:
        log.debug("[PLATFORM] WTSQuerySessionInformation probe failed", exc_info=True)

    return False


def _is_posix_remote_session() -> bool:
    """POSIX remote-session detection."""
    # 1-5: env-var checks for the major remote-desktop backends.
    for var_name in _POSIX_REMOTE_SESSION_ENV_VARS:
        value = os.environ.get(var_name)
        if value:
            log.info(
                "[PLATFORM] Remote session detected ($%s=%r)",
                var_name,
                value,
            )
            log.warning(
                "[PLATFORM] Running in a remote session (%s), clipboard "
                "sync may be delayed and keystroke injection may not "
                "propagate to the remote display",
                var_name,
            )
            return True

    # 6: Chrome Remote Desktop sets TERM_PROGRAM=Hyper in its remoting
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program == "Hyper":
        log.info("[PLATFORM] Chrome Remote Desktop session detected (TERM_PROGRAM=Hyper)")
        log.warning(
            "[PLATFORM] Running in Chrome Remote Desktop, clipboard "
            "sync may be delayed and keystroke injection may not "
            "propagate to the remote display"
        )
        return True

    # 7: /proc/*/comm scan for VNC/NX/X2GO daemons (covers sessions
    if _posix_proc_has_remote_desktop():
        log.warning(
            "[PLATFORM] Remote-desktop daemon process detected in /proc "
            "(Xvnc/x2goagent/nxagent), clipboard sync may be delayed and "
            "keystroke injection may not propagate to the remote display"
        )
        return True

    return False


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
    return bool(any(p in lower for p in ["microsoft sound mapper", "primary sound capture driver"]))


# Generic WASAPI/CoreAudio/PulseAudio endpoint words that appear as the
_GENERIC_ENDPOINT_LABELS = frozenset(
    {
        "microphone",
        "mic",
        "input",
        "output",
        "recording",
        "capture",
        "line",
        "aux",
        "auxiliary",
        "digital",
        "default",
        "speakers",
    }
)

# Matches empty / whitespace-only parenthetical groups: ``"()"``, ``"( )"``.
_EMPTY_PAREN_GROUP_RE = re.compile(r"\(\s*\)")


def _is_invalid_device_name(name: object) -> bool:
    """Return True if *name* is not a usable microphone display name."""
    if not isinstance(name, str):
        return True
    stripped = name.strip()
    if not stripped:
        return True
    if not any(ch.isalnum() for ch in stripped):
        return True
    reduced = _EMPTY_PAREN_GROUP_RE.sub("", stripped).strip()
    if not reduced or not any(ch.isalnum() for ch in reduced):
        return True
    return reduced.lower() in _GENERIC_ENDPOINT_LABELS
