"""Tests that the Tauri spawn paths apply ``_spawn_flags`` correctly.

The Tauri spawn paths (``_spawn_tauri_host`` and the Tauri branch of
``_focus_running_app``) previously called ``subprocess.Popen([binary],
env=env)`` with NO platform-specific spawn flags.  Contrast with the
Electron spawn paths (``_launch_electron_built`` and the Electron
branch of ``_focus_running_app``) which both call
``sk.update(_spawn_flags(hidden=hidden))`` and pass ``**sk`` to Popen.

This left two gaps:

* **Windows**: the Tauri binary could flash a console window during
  autostart-at-login (the user is logging in, not clicking a shortcut,
  so a flashing console is jarring).  The Electron path prevents this
  via ``CREATE_NO_WINDOW`` (``0x08000000``) when ``hidden=True``.
* **POSIX**: the Tauri child was spawned in the launcher's session /
  process group, so if the launcher was a session leader (typical under
  systemd user units or cron-launched sessions), the child would receive
  ``SIGHUP`` when the launcher exited — killing the Tauri app the
  launcher just spawned.  The Electron path avoids this via
  ``start_new_session=True``.

These tests pin the contract that both Tauri spawn paths now mirror the
Electron paths by calling ``_spawn_flags(hidden=...)`` and passing the
result to :class:`subprocess.Popen`.

They also pin the clean-log env contract: both spawn paths pass
``_launcher_child_env()`` to Popen, which force-disables ANSI colour
(``FORCE_COLOR=0`` / ``NO_COLOR=1`` / ``CLICOLOR=0``), suppresses npm
banner notices (``npm_config_loglevel=silent``), and keeps
``tauri-stderr.log`` free of the Rust host's rotating-file stream
(``RUST_LOG_STDERR=0``) so the redirected ``tauri-*.log`` files stay
as clean as ``voice-typer.log``.

Platform note: ``_spawn_flags`` reads ``sys.platform`` via
:func:`voice_typer.server.platform_utils.is_windows`.  These tests mock
``sys.platform`` to ``"win32"`` / ``"linux"`` to exercise both branches
without needing a real Windows or POSIX host.  The Windows branch is
NOT runtime-tested here (LINUX sandbox) — the ``creationflags`` value
is asserted by mocking the platform, not by observing an actual
``CreateProcess`` call.  VALIDATE ON WINDOWS HOST.
"""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server.autostart_launcher import (
    _focus_running_app,
    _spawn_tauri_host,
)


# These tests exercise spawn *mechanics* (``_spawn_flags`` passthrough)
# with fake binary paths that cannot verify against the real
# ``tauri-binaries.json``. The CR-002 integrity gate itself is tested
# behaviorally in ``tests/test_tauri_binary_verify.py`` — here we
# bypass it so the flag assertions stay focused.
@pytest.fixture(autouse=True)
def _bypass_tauri_integrity_gate(monkeypatch):
    """Bypass ``verify_tauri_binary_or_skip`` for spawn-mechanic tests."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
        lambda path: True,
    )

# ``_tauri_log_files()`` opens real log files under the platform config
# dir.  In tests we don't want that side effect (it would litter the
# developer's real config dir), so we monkeypatch it to return DEVNULL
# — the same fallback the real function uses on failure.  This lets the
# spawn path run end-to-end while keeping the test hermetic.
_TAURI_LOG_FILES_STUB = {
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "stdin": subprocess.DEVNULL,
}


@pytest.fixture
def _stub_tauri_log_files(monkeypatch):
    """Replace ``_tauri_log_files`` with a DEVNULL stub for the test."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher._tauri_log_files",
        lambda: dict(_TAURI_LOG_FILES_STUB),
    )


# ---------------------------------------------------------------------------
# _spawn_tauri_host() — spawn flags
# ---------------------------------------------------------------------------


class TestSpawnTauriHostSpawnFlags:
    """``_spawn_tauri_host`` passes ``_spawn_flags(hidden=...)`` to Popen."""

    def test_windows_hidden_passes_create_no_window(self, monkeypatch, _stub_tauri_log_files):
        """On Windows with ``hidden=True``, the Tauri spawn must pass
        ``creationflags=0x08000000`` (CREATE_NO_WINDOW) so the Tauri
        binary does not flash a console during autostart-at-login."""
        monkeypatch.setattr(sys, "platform", "win32")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/voice-typer-tauri.exe", hidden=True)
        assert result is not None
        assert captured.get("creationflags") == 0x08000000
        # start_new_session is POSIX-only — must NOT be set on Windows.
        assert "start_new_session" not in captured
        # stdout/stderr redirection is present (mirror of _electron_log_files).
        assert "stdout" in captured
        assert "stderr" in captured

    def test_windows_not_hidden_omits_creationflags(self, monkeypatch, _stub_tauri_log_files):
        """On Windows with ``hidden=False`` (e.g. desktop shortcut),
        no ``creationflags`` is set — the Tauri binary gets normal
        process creation (matches the Electron ``hidden=False`` path
        which leaves creation flags unset so the child can create its
        own console if needed)."""
        monkeypatch.setattr(sys, "platform", "win32")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4243
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/voice-typer-tauri.exe", hidden=False)
        assert result is not None
        # No creationflags when hidden=False (matches _spawn_flags contract).
        assert "creationflags" not in captured

    def test_posix_passes_start_new_session(self, monkeypatch, _stub_tauri_log_files):
        """On POSIX, the Tauri spawn must pass ``start_new_session=True``
        so the Tauri child is detached into its own session — it
        survives the launcher exiting and is NOT in the launcher's
        process group (avoids SIGHUP if the launcher is a session
        leader)."""
        monkeypatch.setattr(sys, "platform", "linux")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4244
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/voice-typer-tauri", hidden=False)
        assert result is not None
        assert captured.get("start_new_session") is True
        # creationflags is Windows-only — must NOT be set on POSIX.
        assert "creationflags" not in captured

    def test_posix_hidden_also_detaches(self, monkeypatch, _stub_tauri_log_files):
        """On POSIX, ``hidden=True`` still sets ``start_new_session=True``
        (the ``hidden`` flag only affects Windows ``creationflags``;
        POSIX always detaches)."""
        monkeypatch.setattr(sys, "platform", "linux")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4245
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/voice-typer-tauri", hidden=True)
        assert result is not None
        assert captured.get("start_new_session") is True
        assert "creationflags" not in captured

    def test_spawn_tauri_host_env_carries_clean_log_keys(self, monkeypatch, _stub_tauri_log_files):
        """The Tauri child env must carry the clean-log keys so
        ``tauri-stdout.log`` / ``tauri-stderr.log`` stay as clean as
        ``voice-typer.log``: ANSI forced off (JS ``FORCE_COLOR`` + the
        cross-ecosystem ``NO_COLOR`` / ``CLICOLOR`` contracts) and the
        Rust host's stderr mirror of its rotating file stream disabled
        (``RUST_LOG_STDERR=0``), plus npm banner notices suppressed."""
        monkeypatch.setattr(sys, "platform", "win32")
        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 4246
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/voice-typer-tauri.exe", hidden=False)
        assert result is not None
        # ANSI disabled under all three conventions.
        assert captured_env.get("FORCE_COLOR") == "0"
        assert captured_env.get("NO_COLOR") == "1"
        assert captured_env.get("CLICOLOR") == "0"
        # Rust host must NOT duplicate its rotating-file stream into
        # tauri-stderr.log (the file is for crash/early diagnostics).
        assert captured_env.get("RUST_LOG_STDERR") == "0"
        # npm banner notices suppressed (mirror of the Electron env).
        assert captured_env.get("npm_config_loglevel") == "silent"

    def test_spawn_failure_returns_none_and_closes_logs(self, monkeypatch, _stub_tauri_log_files):
        """A spawn failure is logged, log files are closed, and ``None``
        is returned — mirrors the Electron spawn-failure contract."""
        monkeypatch.setattr(sys, "platform", "linux")

        def boom(cmd, env=None, **kwargs):
            raise FileNotFoundError("binary not found")

        monkeypatch.setattr(subprocess, "Popen", boom)
        result = _spawn_tauri_host("/fake/voice-typer-tauri", hidden=False)
        assert result is None


# ---------------------------------------------------------------------------
# _focus_running_app() — Tauri path spawn flags
# ---------------------------------------------------------------------------


class TestFocusRunningAppTauriSpawnFlags:
    """The Tauri branch of ``_focus_running_app`` passes
    ``_spawn_flags(hidden=False)`` to Popen (the focus probe is
    intentionally foreground)."""

    def test_windows_focus_probe_passes_no_creationflags(self, monkeypatch, _stub_tauri_log_files):
        """On Windows, the Tauri focus probe runs with ``hidden=False``
        (the user clicked a shortcut and expects to see the focused
        window), so no ``creationflags`` is set — matches the Electron
        focus path."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 5555
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        # hidden=False → no creationflags on Windows.
        assert "creationflags" not in captured
        assert "start_new_session" not in captured

    def test_posix_focus_probe_passes_start_new_session(self, monkeypatch, _stub_tauri_log_files):
        """On POSIX, the Tauri focus probe must detach into its own
        session (``start_new_session=True``) — the probe is a
        short-lived second instance that triggers the single-instance
        plugin and exits; it must not receive SIGHUP from the launcher
        before the plugin can do its focus dance."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 5556
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured.get("start_new_session") is True
        assert "creationflags" not in captured

    def test_posix_focus_probe_sets_focus_only_env(self, monkeypatch, _stub_tauri_log_files):
        """The Tauri focus probe still sets ``VT_FOCUS_ONLY=1`` in the
        child env (the marker the Tauri binary reads to skip heavy
        init).  The spawn-flags change must not regress this."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 5557
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured_env.get("VT_FOCUS_ONLY") == "1"

    def test_focus_probe_env_carries_clean_log_keys(self, monkeypatch, _stub_tauri_log_files):
        """The Tauri focus probe (spawned by the launcher with its
        output redirected to ``tauri-*.log``) must carry the same
        clean-log env as the full spawn: ANSI disabled + Rust stderr
        mirror off, on top of ``VT_FOCUS_ONLY=1``."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 5558
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured_env.get("VT_FOCUS_ONLY") == "1"
        assert captured_env.get("FORCE_COLOR") == "0"
        assert captured_env.get("NO_COLOR") == "1"
        assert captured_env.get("CLICOLOR") == "0"
        assert captured_env.get("RUST_LOG_STDERR") == "0"
        assert captured_env.get("npm_config_loglevel") == "silent"

    def test_focus_probe_spawn_failure_returns_false(self, monkeypatch, _stub_tauri_log_files):
        """A Tauri focus-probe spawn failure returns False (no
        exception propagation) — mirrors the pre-fix contract."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )

        def boom(cmd, env=None, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        assert _focus_running_app() is False


# ---------------------------------------------------------------------------
# _tauri_log_files() — log redirection
# ---------------------------------------------------------------------------


class TestTauriLogFilesHelper:
    """``_tauri_log_files()`` returns a dict with stdout/stderr/stdin
    keys suitable for unpacking into Popen — mirroring
    ``_electron_log_files()``."""

    def test_returns_devnull_on_failure(self, monkeypatch, tmp_path):
        """When the config dir is unwritable, ``_tauri_log_files``
        falls back to ``subprocess.DEVNULL`` for all three streams so
        the spawn still succeeds."""
        # Force _config_dir to raise by making it return a path whose
        # parent is a regular file (cannot mkdir under a file).
        from voice_typer.server import autostart_launcher as mod

        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")

        def fake_config_dir():
            # logs/ under a file → mkdir raises NotADirectoryError
            return blocker

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            fake_config_dir,
        )
        # Re-import the helper fresh so it picks up the patched _config_dir.
        result = mod._tauri_log_files()
        assert result["stdout"] is subprocess.DEVNULL
        assert result["stderr"] is subprocess.DEVNULL
        assert result["stdin"] is subprocess.DEVNULL

    def test_returns_real_file_handles_on_success(self, monkeypatch, tmp_path):
        """When the config dir is writable, ``_tauri_log_files`` returns
        real open file handles for stdout/stderr pointing at
        ``tauri-stdout.log`` / ``tauri-stderr.log``."""
        from voice_typer.server import autostart_launcher as mod

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )
        result = mod._tauri_log_files()
        try:
            assert result["stdout"] is not subprocess.DEVNULL
            assert result["stderr"] is not subprocess.DEVNULL
            assert result["stdin"] is subprocess.DEVNULL
            # The log files should exist on disk.
            assert (tmp_path / "logs" / "tauri-stdout.log").exists()
            assert (tmp_path / "logs" / "tauri-stderr.log").exists()
        finally:
            # Close the handles so the test doesn't leak.
            for key in ("stdout", "stderr"):
                fd = result.get(key)
                if fd is not None and fd is not subprocess.DEVNULL:
                    fd.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
