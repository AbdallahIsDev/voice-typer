"""S-7: Regression tests for the removal of ``shell=True`` fallbacks.

Background
----------
Last-resort fallbacks in 4 production paths previously used
``subprocess.Popen(..., shell=True)`` to launch ``npm run dev`` /
``npm run build``.  This is a security (shell injection) and
correctness (breaks on paths with spaces) hazard.  S-7 replaces those
fallbacks with ``shutil.which``-resolved binary paths and a
``log + skip`` policy when the binary truly cannot be resolved.

The 4 sites covered:
- ``voice_typer/server/electron_launcher.py`` :: ``launch_electron_frontend``
- ``voice_typer/server/autostart_launcher.py`` :: ``_spawn_npm_run_dev``
- ``voice_typer/server/tray_window.py`` :: ``open_electron_window``
- ``voice_typer/server/_electron_build.py`` :: ``_build_electron`` +
  the shared ``_npm_command`` helper.

Test strategy
-------------
1. **AST scan**: each of the 4 source files must not contain any
   ``Call`` node with a ``shell=True`` keyword argument.  This is
   robust against mentions of the literal string in comments /
   docstrings (which are intentionally kept for context).
2. **Unit tests for ``_npm_command``**: verify the cross-platform
   resolution matrix (POSIX-with-npm, POSIX-without-npm,
   Windows-with-npm, Windows-with-npm.cmd-only, Windows-without-npm).
3. **Per-site behavioural tests**: when ``_npm_command`` returns
   ``None``, the site MUST log and skip — never call ``Popen`` /
   ``run`` with ``shell=True``.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "voice_typer" / "server"

# The 4 production files that previously had ``shell=True`` fallbacks.
S7_SOURCE_FILES = [
    SERVER_DIR / "electron_launcher.py",
    SERVER_DIR / "autostart_launcher.py",
    SERVER_DIR / "tray_window.py",
    SERVER_DIR / "_electron_build.py",
]


def _shell_true_call_count(source: str) -> int:
    """Return the number of ``Call`` nodes with ``shell=True`` keyword.

    Uses :mod:`ast` so mentions in comments and docstrings are not
    counted.  Only an actual keyword argument ``shell=True`` (with the
    literal ``True`` constant) is flagged.
    """
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                count += 1
    return count


# ── 1. AST scan: no shell=True in any of the 4 source files ────────────


class TestNoShellTrueInSource:
    """S-7: none of the 4 production files may call with ``shell=True``."""

    @pytest.mark.parametrize(
        "src_path",
        S7_SOURCE_FILES,
        ids=[p.name for p in S7_SOURCE_FILES],
    )
    def test_no_shell_true_kwarg(self, src_path: Path):
        assert src_path.exists(), f"missing source file: {src_path}"
        src = src_path.read_text(encoding="utf-8")
        count = _shell_true_call_count(src)
        assert count == 0, (
            f"S-7: {src_path.name} still contains {count} call(s) with "
            f"shell=True — all such fallbacks must be replaced with "
            f"shutil.which-resolved binary paths."
        )


# ── 2. Unit tests for the shared _npm_command helper ───────────────────


class TestNpmCommandResolution:
    """S-7: ``_npm_command`` resolves npm via ``shutil.which`` cross-platform."""

    def test_posix_returns_resolved_path(self, monkeypatch):
        """On POSIX, when ``shutil.which("npm")`` finds npm, return [path, run, script]."""
        from voice_typer.server import _electron_build as eb

        monkeypatch.setattr(eb, "is_windows", lambda: False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)
        assert eb._npm_command("dev") == ["/usr/bin/npm", "run", "dev"]

    def test_posix_returns_list_when_which_misses(self, monkeypatch):
        """On POSIX, when ``shutil.which("npm")`` returns None, still return a list.

        Popen's PATH lookup may still find npm — no shell needed.
        """
        from voice_typer.server import _electron_build as eb

        monkeypatch.setattr(eb, "is_windows", lambda: False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert eb._npm_command("build") == ["npm", "run", "build"]

    def test_windows_returns_resolved_path(self, monkeypatch):
        """On Windows, when ``shutil.which("npm")`` finds npm.cmd, return [path, run, script].

        ``shutil.which("npm")`` on Windows consults PATHEXT and resolves
        to ``npm.cmd`` automatically — that's the common case.
        """
        from voice_typer.server import _electron_build as eb

        monkeypatch.setattr(eb, "is_windows", lambda: True)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: r"C:\Program Files\nodejs\npm.cmd" if name == "npm" else None,
        )
        assert eb._npm_command("dev") == [
            r"C:\Program Files\nodejs\npm.cmd",
            "run",
            "dev",
        ]

    def test_windows_falls_back_to_explicit_npm_cmd(self, monkeypatch):
        """On Windows with misconfigured PATHEXT, ``shutil.which("npm.cmd")`` is tried.

        Belt-and-suspenders: ``shutil.which("npm")`` should already find
        ``npm.cmd`` via PATHEXT, but if PATH/PATHEXT is misconfigured we
        try the ``.cmd`` extension explicitly.  The returned path is a
        list form (no shell).
        """
        from voice_typer.server import _electron_build as eb

        monkeypatch.setattr(eb, "is_windows", lambda: True)

        def fake_which(name: str):
            # shutil.which("npm") misses (PATHEXT misconfigured),
            # but shutil.which("npm.cmd") finds it.
            if name == "npm.cmd":
                return r"C:\Node\npm.cmd"
            return None

        monkeypatch.setattr("shutil.which", fake_which)
        assert eb._npm_command("dev") == [r"C:\Node\npm.cmd", "run", "dev"]

    def test_windows_returns_none_when_truly_unresolvable(self, monkeypatch):
        """On Windows when both ``shutil.which`` calls miss, return None.

        Caller MUST log and skip — no shell=True fallback.
        """
        from voice_typer.server import _electron_build as eb

        monkeypatch.setattr(eb, "is_windows", lambda: True)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert eb._npm_command("dev") is None

    def test_default_script_is_dev(self, monkeypatch):
        """Default ``script`` argument is ``"dev"`` (back-compat)."""
        from voice_typer.server import _electron_build as eb

        monkeypatch.setattr(eb, "is_windows", lambda: False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm")
        assert eb._npm_command() == ["/usr/bin/npm", "run", "dev"]


# ── 3a. electron_launcher.launch_electron_frontend ─────────────────────


class TestElectronLauncherShellTrueRemoved:
    """S-7: ``launch_electron_frontend`` no longer falls back to shell=True."""

    def test_uses_resolved_npm_path_when_available(self, monkeypatch):
        """When ``_npm_command`` returns a list, Popen is called with that list.

        Verifies the resolved binary path is forwarded to Popen unchanged
        and ``shell`` is NOT set to True.
        """
        from voice_typer.server import electron_launcher as el

        monkeypatch.setattr(el, "_electron_binary", lambda: None)
        monkeypatch.setattr(
            el,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        resolved = ["/resolved/npm", "run", "dev"]
        monkeypatch.setattr(el, "_npm_command", lambda script="dev": resolved)

        captured: dict = {}

        def fake_popen(argv, env=None, **kwargs):
            captured["argv"] = list(argv)
            captured["shell"] = kwargs.get("shell", False)
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        pid = el.launch_electron_frontend(9876, "deadbeef" * 8)
        assert pid == 4242
        assert captured["argv"] == resolved
        assert captured["shell"] is False

    def test_returns_none_when_npm_unresolvable(self, monkeypatch):
        """When ``_npm_command`` returns None, return None without spawning a shell.

        Previously this fell back to ``Popen("npm run dev", shell=True)``.
        S-7: we log and bail instead.
        """
        from voice_typer.server import electron_launcher as el

        monkeypatch.setattr(el, "_electron_binary", lambda: None)
        monkeypatch.setattr(
            el,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        monkeypatch.setattr(el, "_npm_command", lambda script="dev": None)

        popen_calls: list = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: popen_calls.append((a, kw)) or MagicMock(pid=1),
        )
        pid = el.launch_electron_frontend(9876, "deadbeef" * 8)
        assert pid is None
        # CRITICAL: Popen must NOT have been called with shell=True.
        assert popen_calls == [], (
            f"S-7: when _npm_command returns None, Popen must not be called at all (got {popen_calls!r})."
        )


# ── 3b. autostart_launcher._spawn_npm_run_dev ──────────────────────────


class TestAutostartLauncherShellTrueRemoved:
    """S-7: ``_spawn_npm_run_dev`` no longer falls back to shell=True."""

    def test_uses_resolved_npm_path_when_available(self, monkeypatch):
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(
            al,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        resolved = ["/resolved/npm", "run", "dev"]
        monkeypatch.setattr(al, "_npm_command", lambda script="dev": resolved)

        captured: dict = {}

        def fake_popen(argv, env=None, **kwargs):
            captured["argv"] = list(argv)
            captured["shell"] = kwargs.get("shell", False)
            proc = MagicMock()
            proc.pid = 1234
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        child = al._spawn_npm_run_dev(hidden=False)
        assert child is not None
        assert captured["argv"] == resolved
        assert captured["shell"] is False

    def test_returns_none_when_npm_unresolvable(self, monkeypatch):
        """When ``_npm_command`` returns None, return None without spawning a shell."""
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(
            al,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        monkeypatch.setattr(al, "_npm_command", lambda script="dev": None)

        popen_calls: list = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: popen_calls.append((a, kw)) or MagicMock(pid=1),
        )
        child = al._spawn_npm_run_dev(hidden=False)
        assert child is None
        assert popen_calls == [], (
            f"S-7: when _npm_command returns None, Popen must not be called at all (got {popen_calls!r})."
        )


# ── 3c. tray_window.open_electron_window ───────────────────────────────


class TestTrayWindowShellTrueRemoved:
    """S-7: ``open_electron_window`` no longer falls back to shell=True."""

    def _force_dev_mode_branch(self, monkeypatch):
        """Make ``open_electron_window`` skip TCP push, Win32 focus, and build-first.

        Returns the mocked ``subprocess.Popen`` so the test can inspect
        the call args.
        """
        import voice_typer.server.tray_window as tw

        # 1. TCP push fails.
        # B-1: tray_window now calls event_bus.publish directly.
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: False,
        )
        # 2. Win32 focus fails.
        monkeypatch.setattr(tw, "bring_electron_to_front", lambda: False)
        # 3. Build-first path fails.
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._ensure_built_and_launch",
            lambda hidden=False: False,
        )
        # 4. EO-16 duplicate-launch gate disabled — these tests exercise
        #    the dev-fallback path, not the pgrep probe. Without this,
        #    ``_electron_process_is_running()`` spawns a real
        #    ``pgrep -f <APP_NAME>`` subprocess on POSIX CI, which trips
        #    ``test_skips_when_npm_unresolvable``'s strict "no Popen at
        #    all" assertion. The gate itself has dedicated coverage in
        #    ``tests/test_tray.py::TestElectronDuplicateLaunchGate``.
        monkeypatch.setattr(tw, "_electron_process_is_running", lambda: False)

    def test_uses_resolved_npm_path_when_available(self, monkeypatch):
        """When ``_npm_command`` returns a list, Popen is called with that list."""
        from voice_typer.server import tray_window as tw

        self._force_dev_mode_branch(monkeypatch)

        resolved = ["/resolved/npm", "run", "dev"]
        monkeypatch.setattr(
            "voice_typer.server._electron_build._npm_command",
            lambda script="dev": resolved,
        )

        captured: dict = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["shell"] = kwargs.get("shell", False)
            return MagicMock(pid=9999)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        tw.open_electron_window()
        assert captured["argv"] == resolved
        assert captured["shell"] is False

    def test_skips_when_npm_unresolvable(self, monkeypatch):
        """When ``_npm_command`` returns None, log and skip — no shell=True.

        Previously this fell back to ``Popen("npm run dev", shell=True)``.
        S-7: we log and return without spawning.
        """
        from voice_typer.server import tray_window as tw

        self._force_dev_mode_branch(monkeypatch)
        monkeypatch.setattr(
            "voice_typer.server._electron_build._npm_command",
            lambda script="dev": None,
        )

        popen_calls: list = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: popen_calls.append((a, kw)) or MagicMock(pid=1),
        )
        # Should not raise.
        tw.open_electron_window()
        assert popen_calls == [], (
            f"S-7: when _npm_command returns None, Popen must not be called at all (got {popen_calls!r})."
        )


# ── 3d. _electron_build._build_electron (removed) ────────────────────


class TestBuildElectronRemoved:
    """S-7 follow-up: ``_build_electron`` (npm run build at launch time)
    was removed from :mod:`voice_typer.server._electron_build`.

    The launcher no longer builds the Electron app from source at launch
    — packaged installs ship pre-built bundles (``out/main/index.js``)
    and the dev path uses ``npm run dev``.  ``_ensure_built_and_launch``
    now fails fast (no subprocess build, hence no shell=True risk)
    when the pre-built main entry is missing.
    """

    def test_build_electron_no_longer_defined(self):
        """Removal guard: the auto-build function must NOT exist — a
        regression to build-on-launch (with its subprocess + shell
        handling) is a security regression."""
        from voice_typer.server import _electron_build as eb

        assert not hasattr(eb, "_build_electron")

    def test_ensure_built_and_launch_does_not_build_when_missing(self, monkeypatch):
        """When the pre-built main entry is absent, the launcher fails
        fast instead of invoking ``npm run build``."""
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(al, "_electron_binary", lambda: "/fake/electron")
        monkeypatch.setattr(al, "_main_entry_built", lambda: False)
        # If _ensure_built_and_launch attempted a build it would raise
        # AttributeError (the module has no _build_electron) — returning
        # False without a build is the required behaviour.
        assert al._ensure_built_and_launch(hidden=True) is False
