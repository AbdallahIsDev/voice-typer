"""FR-40 — regression tests for the extended remote-session detection
in :mod:`voice_typer.server.server_platform.remote_session`.

Pre-fix symptom: ``is_remote_session`` only detected SSH (POSIX:
``$SSH_CLIENT`` / ``$SSH_TTY``) and Windows RDP (``SM_REMOTESESSION``).
VNC, xrdp, NX/NoMachine, Citrix ICA, X2Go, Chrome Remote Desktop, and
X11-forwarding-over-SSH-without-tty were all missed — clipboard and
keystroke-injection behavior silently misbehaved on those backends.

Post-fix: POSIX also checks ``$VNCDESKTOP``, ``$X2GO_SESSION``,
``$NX_TEMP``, ``$CITRIX_SESSION``, ``$TERM_PROGRAM == "Hyper"`` (Chrome
Remote Desktop), and scans ``/proc/*/comm`` for ``Xvnc`` / ``x2goagent``
/ ``nxagent`` processes. Windows additionally calls
``WTSQuerySessionInformation`` for WVD / Azure RemoteApp sessions that
``SM_REMOTESESSION`` misses. A warning is logged when a remote session
is detected.

These tests run on any platform — they patch ``_pkg.SYSTEM`` to
exercise the POSIX and Windows branches, and clear all remote-session
env vars to ensure a deterministic baseline.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

# All POSIX env vars the FR-40 fix checks. Tests clear these to ensure
# a deterministic baseline.
_ALL_POSIX_REMOTE_ENV_VARS = (
    "SSH_CLIENT",
    "SSH_TTY",
    "VNCDESKTOP",
    "X2GO_SESSION",
    "NX_TEMP",
    "CITRIX_SESSION",
    "TERM_PROGRAM",
)


def _clear_all_remote_env(monkeypatch):
    """Clear all POSIX remote-session env vars so the test starts from
    a deterministic baseline (no leftover SSH_CLIENT from the test
    runner, etc.)."""
    for var in _ALL_POSIX_REMOTE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ─── FR-40: POSIX env-var detection ──────────────────────────────────


class TestPosixEnvVarDetection:
    """FR-40: ``is_remote_session`` detects the major POSIX
    remote-desktop backends via env vars."""

    def test_detects_vncdesktop(self, monkeypatch):
        """``$VNCDESKTOP`` set → returns True (was False pre-fix)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("VNCDESKTOP", "my-vnc-session")
        # Patch the /proc scan to return False so we isolate the
        # env-var check.
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is True

    def test_detects_x2go_session(self, monkeypatch):
        """``$X2GO_SESSION`` set → returns True."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("X2GO_SESSION", "x2go-1234")
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is True

    def test_detects_nx_temp(self, monkeypatch):
        """``$NX_TEMP`` set → returns True (NoMachine / NX)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("NX_TEMP", "/tmp/nx-abc")
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is True

    def test_detects_citrix_session(self, monkeypatch):
        """``$CITRIX_SESSION`` set → returns True."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("CITRIX_SESSION", "ica-1234")
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is True

    def test_detects_chrome_remote_desktop_via_term_program(self, monkeypatch):
        """``$TERM_PROGRAM == "Hyper"`` → returns True (Chrome Remote
        Desktop's remoting shell wrapper sets this)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("TERM_PROGRAM", "Hyper")
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is True

    def test_does_not_match_unrelated_term_program(self, monkeypatch):
        """``$TERM_PROGRAM == "iTerm.app"`` (or any non-"Hyper" value)
        → returns False (no false positive)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is False

    def test_preserves_existing_ssh_detection(self, monkeypatch):
        """FR-40 must NOT regress the existing SSH detection
        (``$SSH_CLIENT`` / ``$SSH_TTY``)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        monkeypatch.setenv("SSH_CLIENT", "10.0.0.1 12345 22")
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is True


# ─── FR-40: POSIX /proc/*/comm scan ──────────────────────────────────


class TestPosixProcScan:
    """FR-40: ``is_remote_session`` scans ``/proc/*/comm`` for VNC / NX
    / X2GO daemon processes (covers sessions that don't export an env
    var to the user's shell — e.g. re-attached VNC sessions)."""

    def test_detects_xvnc_process(self, monkeypatch, tmp_path):
        """A ``/proc/<pid>/comm`` file containing "Xvnc" → returns
        True (was False pre-fix)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")

        # Build a fake /proc with one process whose comm is "Xvnc".
        fake_proc = tmp_path / "proc"
        fake_proc.mkdir()
        (fake_proc / "12345").mkdir()
        (fake_proc / "12345" / "comm").write_text("Xvnc\n")
        # Add a non-numeric entry to verify the scan skips it.
        (fake_proc / "self").mkdir()

        # Patch os.listdir to read from the fake /proc.
        real_listdir = os.listdir

        def fake_listdir(path):
            if str(path) == "/proc":
                return real_listdir(str(fake_proc))
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", fake_listdir)

        # Patch open() so /proc/<pid>/comm reads from the fake path.
        real_open = open

        def fake_open(path, *args, **kwargs):
            path_str = str(path)
            if path_str.startswith("/proc/") and path_str.endswith("/comm"):
                # Map /proc/<pid>/comm to <fake_proc>/<pid>/comm.
                pid_part = path_str[len("/proc/") : -len("/comm")]
                fake_path = fake_proc / pid_part / "comm"
                return real_open(fake_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        import builtins

        monkeypatch.setattr(builtins, "open", fake_open)

        assert remote_session.is_remote_session() is True

    def test_does_not_match_unrelated_process(self, monkeypatch, tmp_path):
        """A ``/proc/<pid>/comm`` file containing an unrelated process
        name (e.g. "python3") → returns False."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")

        fake_proc = tmp_path / "proc"
        fake_proc.mkdir()
        (fake_proc / "12345").mkdir()
        (fake_proc / "12345" / "comm").write_text("python3\n")

        real_listdir = os.listdir

        def fake_listdir(path):
            if str(path) == "/proc":
                return real_listdir(str(fake_proc))
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", fake_listdir)

        real_open = open

        def fake_open(path, *args, **kwargs):
            path_str = str(path)
            if path_str.startswith("/proc/") and path_str.endswith("/comm"):
                pid_part = path_str[len("/proc/") : -len("/comm")]
                fake_path = fake_proc / pid_part / "comm"
                return real_open(fake_path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        import builtins

        monkeypatch.setattr(builtins, "open", fake_open)

        assert remote_session.is_remote_session() is False

    def test_proc_scan_handles_missing_proc(self, monkeypatch):
        """If ``/proc`` doesn't exist (non-Linux POSIX like macOS/BSD),
        the scan returns False without raising."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "darwin")

        def raise_oserror(path):
            raise FileNotFoundError(f"no such dir: {path}")

        monkeypatch.setattr(os, "listdir", raise_oserror)
        # No env vars set → returns False (the /proc scan handles the
        # missing /proc gracefully).
        assert remote_session.is_remote_session() is False


# ─── FR-40: Windows detection (mocked) ───────────────────────────────


class TestWindowsDetection:
    """FR-40: ``is_remote_session`` on Windows uses
    ``SM_REMOTESESSION`` AND the new ``WTSQuerySessionInformation``
    fallback (for WVD / Azure RemoteApp)."""

    def test_returns_true_when_sm_remotesession_set(self, monkeypatch):
        """Primary probe: ``SM_REMOTESESSION`` non-zero → True."""
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "win32")

        # Mock ctypes.windll.user32.GetSystemMetrics to return 1
        # (SM_REMOTESESSION set).
        mock_ctypes = MagicMock()
        mock_ctypes.windll.user32.GetSystemMetrics.return_value = 1
        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)

        assert remote_session.is_remote_session() is True

    def test_returns_false_when_sm_remotesession_zero_and_no_wts(self, monkeypatch):
        """SM_REMOTESESSION=0 AND WTS API unavailable / not a remote
        session → False."""
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "win32")

        mock_ctypes = MagicMock()
        # SM_REMOTESESSION = 0 (not remote).
        mock_ctypes.windll.user32.GetSystemMetrics.return_value = 0
        # WTSQuerySessionInformationW returns 0 (failure) → no WTS
        # signal.
        mock_ctypes.windll.wtsapi32.WTSQuerySessionInformationW.return_value = 0
        # ``bytes_returned.value`` is 0 → no buffer to inspect.
        bytes_returned = MagicMock()
        bytes_returned.value = 0
        mock_ctypes.windll.wtsapi32.WTSQuerySessionInformationW.return_value = 0
        # Patch ctypes.byref and ctypes.c_void_p / c_ulong to return
        # MagicMocks (the production code uses them as out-params).
        monkeypatch.setattr(mock_ctypes, "byref", lambda x: x)
        monkeypatch.setattr(mock_ctypes, "c_void_p", lambda *a, **kw: MagicMock())
        monkeypatch.setattr(mock_ctypes, "c_ulong", lambda *a, **kw: MagicMock(value=0))
        monkeypatch.setattr(mock_ctypes, "cast", lambda *a, **kw: MagicMock())
        monkeypatch.setattr(mock_ctypes, "POINTER", lambda *a, **kw: MagicMock())
        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)

        assert remote_session.is_remote_session() is False

    def test_wts_fallback_detects_wvd_session(self, monkeypatch):
        """FR-40: WTS API fallback detects WVD / Azure RemoteApp
        sessions that SM_REMOTESESSION misses.

        Scenario: SM_REMOTESESSION=0 (missed WVD), but
        WTSQuerySessionInformation returns connect_state=WTSActive (0)
        AND WTSGetActiveConsoleSessionId returns 0xFFFFFFFF (no
        physical console) → remote session detected.
        """
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "win32")

        mock_ctypes = MagicMock()
        # SM_REMOTESESSION = 0 (WVD missed).
        mock_ctypes.windll.user32.GetSystemMetrics.return_value = 0
        # WTSQuerySessionInformationW returns 1 (success).
        mock_ctypes.windll.wtsapi32.WTSQuerySessionInformationW.return_value = 1
        # WTSGetActiveConsoleSessionId returns 0xFFFFFFFF (no physical
        # console — headless / WVD session).
        mock_ctypes.windll.kernel32.WTSGetActiveConsoleSessionId.return_value = 0xFFFFFFFF

        # ``bytes_returned.value`` must be >= 4 (DWORD size).
        bytes_returned = MagicMock()
        bytes_returned.value = 4
        # ``connect_state`` is the DWORD value (0 = WTSActive).
        connect_state_mock = MagicMock()
        connect_state_mock.value = 0
        contents_mock = MagicMock()
        contents_mock.value = 0
        # Patch ctypes helpers.
        monkeypatch.setattr(mock_ctypes, "byref", lambda x: x)
        monkeypatch.setattr(mock_ctypes, "c_void_p", lambda *a, **kw: MagicMock())
        monkeypatch.setattr(mock_ctypes, "c_ulong", lambda *a, **kw: bytes_returned)
        # ``ctypes.cast(buffer, POINTER(c_ulong)).contents.value`` → 0
        # (WTSActive). We make ``cast`` return a MagicMock whose
        # ``.contents.value`` is 0.
        cast_result = MagicMock()
        cast_result.contents.value = 0
        monkeypatch.setattr(mock_ctypes, "cast", lambda *a, **kw: cast_result)
        monkeypatch.setattr(mock_ctypes, "POINTER", lambda *a, **kw: MagicMock())
        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)

        assert remote_session.is_remote_session() is True


# ─── FR-40: no false positive on local Linux session ─────────────────


class TestNoFalsePositiveLocalSession:
    """FR-40: a local (non-remote) Linux session must NOT be flagged
    as remote."""

    def test_returns_false_on_local_linux_session(self, monkeypatch):
        """No remote-session env vars set, no VNC/NX/X2GO processes →
        returns False (no false positive)."""
        _clear_all_remote_env(monkeypatch)
        from voice_typer.server.server_platform import remote_session

        monkeypatch.setattr(remote_session._pkg, "SYSTEM", "linux")
        # Patch the /proc scan to return False (no VNC/NX/X2GO
        # processes — the test environment likely doesn't have any,
        # but we patch to be deterministic).
        monkeypatch.setattr(remote_session, "_posix_proc_has_remote_desktop", lambda: False)
        assert remote_session.is_remote_session() is False
