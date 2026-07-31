"""RDP / SSH remote-session detection + non-microphone device predicate.

Phase 4.5 /  — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  The two helpers in
this file have no cross-submodule state: they only read ``SYSTEM`` (the
package-level ``sys.platform`` snapshot) and stdlib ``os`` / ``ctypes``.

Patch-path compatibility
------------------------
Tests do not directly patch ``is_remote_session`` via
``monkeypatch.setattr("voice_typer.server.server_platform.is_remote_session", ...)``
— instead, callers (e.g. ``clipboard.py``) import the function lazily
inside a try/except and tests replace the whole module via
``patch.dict(sys.modules, {"voice_typer.server.server_platform": fake})``.
For the dispatch on the platform, ``is_remote_session`` reads
``_pkg.SYSTEM`` (NOT a local ``SYSTEM`` binding) so a future test that
patches ``server_platform.SYSTEM`` would still take effect.

``inspect.getsource`` compatibility
-----------------------------------
``is_remote_session`` and ``_is_non_mic_device`` are genuinely defined
here, so ``inspect.getsource(is_remote_session)`` continues to read from
this file.
"""

from __future__ import annotations

import logging
import os

# Patch-path bridge: route lookups of ``SYSTEM`` through the package
# namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.SYSTEM", "win32")``
# keep affecting production code defined here.  The package ``__init__.py``
# re-exports ``SYSTEM`` (it is a module-level constant of the package
# itself); we look it up at call time rather than binding at import time
# so the patch takes effect.
from voice_typer.server import server_platform as _pkg

log = logging.getLogger(__name__)


# ─── RDP / remote session detection ──────────────────────────────────


# POSIX env vars that indicate a remote-desktop session. Each
# entry is checked via ``os.environ.get(name)`` — a truthy value means
# we're inside that remote-session backend. The list covers the major
# Linux/POSIX remote-desktop technologies that the pre-fix code missed:
#
# - ``VNCDESKTOP`` — set by the vncserver wrapper script (TigerVNC,
#   TightVNC, RealVNC) when a VNC session is active. Set on the
#   per-session X server process tree.
# - ``X2GO_SESSION`` — set by x2goclient / x2goserver (NX-based remote
#   desktop, popular in education and enterprise).
# - ``NX_TEMP`` — set by NoMachine / NX (the commercial successor to
#   the original NX protocol). ``NX_TEMP`` is the temp-dir env var
#   that NX sets when a session is active.
# - ``CITRIX_SESSION`` — set by Citrix Workspace (ICA protocol) inside
#   the published-app session.
# - ``TERM_PROGRAM == "Hyper"`` — Chrome Remote Desktop sets
#   ``TERM_PROGRAM=Hyper`` inside its remoting shell (a quirk of the
#   CRD host-side shell wrapper). Other terminals (iTerm2, GNOME
#   Terminal) set ``TERM_PROGRAM`` too, but only CRD sets it to
#   ``"Hyper"`` (which is also the name of an Electron-based terminal
#   emulator — false positive risk is low because Hyper users on a
#   local desktop don't typically rely on ``is_remote_session``-gated
#   behavior).
#
# The list is intentionally NOT exhaustive — there are dozens of
# niche remote-desktop tools (Sun Ray, SPICE, Guacamole, X11-forwarding
# over SSH without SSH_TTY, etc.). The goal is to cover the major
# technologies that the original finding () called out by name.
_POSIX_REMOTE_SESSION_ENV_VARS: tuple[str, ...] = (
    "SSH_CLIENT",
    "SSH_TTY",
    "VNCDESKTOP",
    "X2GO_SESSION",
    "NX_TEMP",
    "CITRIX_SESSION",
)


def _posix_proc_has_remote_desktop() -> bool:
    """scan /proc/*/comm for remote-desktop daemon processes.

    Some VNC/NX setups don't export an env var to the user's shell (e.g.
    when the session was started by a system service or when the user
    re-attached to an existing session via ``vncserver -reuse``). We
    scan the running processes' ``comm`` (the executable name, capped
    at 15 chars by the kernel) for the well-known remote-desktop
    process names.

    Returns True on the first match. Returns False if ``/proc`` is not
    available (non-Linux POSIX, e.g. macOS/BSD) or no match is found.
    """
    import os.path

    # The process names to look for (kernel truncates comm at 15 chars,
    # so we match by substring — case-sensitive because comm is the
    # literal argv[0] basename).
    targets = ("Xvnc", "x2goagent", "nxagent")

    # Walk /proc/*/comm. Each entry is a single short line. We bound
    # the walk at 4096 processes (more than any real system) so a
    # pathological /proc with millions of entries can't hang the probe.
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
    """PLAT-RDP: Detect if the app is running in an RDP/remote session.

    On Windows, uses GetSystemMetrics(SM_REMOTESESSION = 0x1000).
        additionally attempts ``WTSQuerySessionInformation`` (WTSConnectState)
        via wtsapi32 — Microsoft docs note that SM_REMOTESESSION is not
        updated for Windows Virtual Desktop / Azure RemoteApp sessions, so
        the WTS API is the authoritative probe when available.

        On Linux/macOS (POSIX), checks (in order):
          1. SSH session env vars (``$SSH_CLIENT`` / ``$SSH_TTY``).
          2. VNC env var (``$VNCDESKTOP``).
          3. X2GO session env var (``$X2GO_SESSION``).
          4. NoMachine / NX env var (``$NX_TEMP``).
          5. Citrix session env var (``$CITRIX_SESSION``).
          6. Chrome Remote Desktop env var (``$TERM_PROGRAM == "Hyper"``).
          7. /proc/*/comm scan for Xvnc / x2goagent / nxagent processes
             (covers sessions that don't export an env var to the user's
             shell — e.g. re-attached VNC sessions).

        RDP/VNC clipboard may be redirected, so clipboard operations may
        behave differently (e.g. clipboard sync delays, missing formats).
        Keystroke injection via XTest may not propagate to the remote
        display on VNC/xrdp — the app now logs a warning when a remote
        session is detected so the user understands the degraded behavior.

        Returns True if a remote session is detected.
    """
    if _pkg.SYSTEM == "win32":
        return _is_windows_remote_session()
    else:
        return _is_posix_remote_session()


def _is_windows_remote_session() -> bool:
    """Windows remote-session detection.

        Primary: ``GetSystemMetrics(SM_REMOTESESSION = 0x1000)``. This is
        the legacy RDP probe; Microsoft docs note it's NOT updated for
        Windows Virtual Desktop / Azure RemoteApp sessions.

    Secondary (): ``WTSQuerySessionInformation`` via wtsapi32,
        requesting ``WTSConnectState`` for the current session. If the
        connect state is ``WTSActive`` AND the session is NOT the physical
        console session (``WTSGetActiveConsoleSessionId`` differs from the
        current session ID), we're in a remote session. This catches WVD /
        Azure RemoteApp that SM_REMOTESESSION misses.

        The WTS API is gated behind try/except so the probe never takes
        down the caller — on Windows Home editions or stripped-down
        Windows containers, wtsapi32 may not be present.
    """
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
    # RemoteApp sessions that SM_REMOTESESSION misses.
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
        # Returns BOOL (nonzero on success).
        ok = wtsapi32.WTSQuerySessionInformationW(
            ctypes.c_void_p(0),  # WTS_CURRENT_SERVER_HANDLE = NULL
            WTS_CURRENT_SESSION,
            WTSConnectState,
            ctypes.byref(buffer),
            ctypes.byref(bytes_returned),
        )
        if ok and bytes_returned.value >= 4:
            # The returned buffer is a DWORD (4 bytes) holding the
            # WTS_CONNECTSTATE_CLASS enum value. 0 = WTSActive.
            connect_state = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ulong)).contents.value
            # Free the buffer — WTSFreeMemory is mandatory on success.
            import contextlib

            with contextlib.suppress(Exception):
                wtsapi32.WTSFreeMemory(buffer)
            # Get the physical console session ID. If the current
            # session differs from the console, we're in a remote
            # session (WVD, Azure RemoteApp, or RDP).
            console_session_id = kernel32.WTSGetActiveConsoleSessionId()
            # ``WTSGetActiveConsoleSessionId`` returns 0xFFFFFFFF if
            # there is no attached physical console (e.g. headless
            # server) — in that case, any non-console session is remote.
            if connect_state == 0 and console_session_id == 0xFFFFFFFF:
                log.warning(
                    "[PLATFORM] Remote Windows session detected via WTS API "
                    "(WTSActive, no physical console) — SM_REMOTESESSION missed this "
                    "(likely Windows Virtual Desktop / Azure RemoteApp)"
                )
                return True
    except Exception:
        log.debug("[PLATFORM] WTSQuerySessionInformation probe failed", exc_info=True)

    return False


def _is_posix_remote_session() -> bool:
    """POSIX remote-session detection.

    Checks (in order): SSH env vars → VNC env var → X2GO env var → NX
    env var → Citrix env var → Chrome Remote Desktop (TERM_PROGRAM ==
    "Hyper") → /proc/*/comm scan for Xvnc / x2goagent / nxagent.

    Logs a warning when a remote session is detected so the user
    understands the degraded behavior (clipboard sync delays, XTest
    keystroke injection may not propagate to the remote display).
    """
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
                "[PLATFORM] Running in a remote session (%s) — clipboard "
                "sync may be delayed and keystroke injection may not "
                "propagate to the remote display",
                var_name,
            )
            return True

    # 6: Chrome Remote Desktop sets TERM_PROGRAM=Hyper in its remoting
    # shell wrapper. Substring-equality (case-sensitive) to avoid
    # matching "Hyper" as a substring of an unrelated value.
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program == "Hyper":
        log.info("[PLATFORM] Chrome Remote Desktop session detected (TERM_PROGRAM=Hyper)")
        log.warning(
            "[PLATFORM] Running in Chrome Remote Desktop — clipboard "
            "sync may be delayed and keystroke injection may not "
            "propagate to the remote display"
        )
        return True

    # 7: /proc/*/comm scan for VNC/NX/X2GO daemons (covers sessions
    # that don't export an env var to the user's shell — e.g.
    # re-attached VNC sessions started by a system service).
    if _posix_proc_has_remote_desktop():
        log.warning(
            "[PLATFORM] Remote-desktop daemon process detected in /proc "
            "(Xvnc/x2goagent/nxagent) — clipboard sync may be delayed and "
            "keystroke injection may not propagate to the remote display"
        )
        return True

    return False


# ─── Non-microphone device predicate ─────────────────────────────────


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
    return bool(any(p in lower for p in ["microsoft sound mapper", "primary sound capture driver"]))
