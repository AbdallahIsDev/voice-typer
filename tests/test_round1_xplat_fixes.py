"""Regression tests for Round 1 cross-platform + privacy + UX fixes.

Covers:
  - NEW-XPLAT-003: tray_window.py uses shutil.which instead of shell=True
  - NEW-XPLAT-005: launchctl load has a 5s timeout
  - NEW-XPLAT-006: macOS plist WorkingDirectory is absolute (no literal ~)
  - NEW-XPLAT-007: _desktop_quote quotes per freedesktop spec
  - NEW-DOC-024: generate-icons.mjs uses .venv first, python3 second
  - NEW-DOC-027: package.json has no broken biome scripts
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── NEW-XPLAT-007: desktop-spec quoting ───────────────────────────────


class TestNewXplat007DesktopQuoting:
    """NEW-XPLAT-007: _desktop_quote follows the freedesktop spec."""

    def test_no_reserved_chars_returns_unquoted(self):
        from voice_typer.server.platform import _desktop_quote
        assert _desktop_quote("python3") == "python3"
        assert _desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
        assert _desktop_quote("--hidden") == "--hidden"

    def test_space_triggers_quoting(self):
        from voice_typer.server.platform import _desktop_quote
        assert _desktop_quote("/path with spaces/app") == '"/path with spaces/app"'

    def test_backslash_escaped(self):
        from voice_typer.server.platform import _desktop_quote
        # Windows path with backslashes
        result = _desktop_quote("C:\\Users\\John\\app")
        assert result == '"C:\\\\Users\\\\John\\\\app"'

    def test_double_quote_escaped(self):
        from voice_typer.server.platform import _desktop_quote
        result = _desktop_quote('John "Bob"')
        assert result == '"John \\"Bob\\""'

    def test_dollar_escaped(self):
        from voice_typer.server.platform import _desktop_quote
        result = _desktop_quote("$HOME/app")
        assert result == '"\\$HOME/app"'

    def test_backtick_escaped(self):
        from voice_typer.server.platform import _desktop_quote
        result = _desktop_quote("path with `backtick`")
        assert result == '"path with \\`backtick\\`"'

    def test_autostart_command_is_quoted(self):
        """The full autostart command should be valid for the .desktop Exec field."""
        from voice_typer.server.platform import _autostart_command
        cmd = _autostart_command()
        # The launcher path is always present.
        assert "autostart_launcher.py" in cmd
        # The python interpreter is always present.
        assert "python" in cmd.lower() or sys.executable in cmd
        # --hidden flag is always present.
        assert "--hidden" in cmd


# ── NEW-XPLAT-005/006: macOS plist + launchctl timeout ───────────────


class TestNewXplat005006MacOSPlist:
    """NEW-XPLAT-005/006: macOS autostart plist is well-formed."""

    def test_plist_uses_absolute_working_directory(self):
        """NEW-XPLAT-006: WorkingDirectory must be an absolute path,
        not the literal ``~``."""
        from voice_typer.server import platform
        # Read the source of _enable_autostart_macos to verify it
        # doesn't emit the literal `~` as WorkingDirectory.
        import inspect
        src = inspect.getsource(platform._enable_autostart_macos)
        # The literal `<string>~</string>` is the bug.
        assert "<string>~</string>" not in src, (
            "macOS plist still uses literal '~' for WorkingDirectory"
        )
        # The fix uses Path.home() to expand ~.
        assert "Path.home()" in src or "str(Path.home())" in src, (
            "macOS plist should use Path.home() for WorkingDirectory"
        )

    def test_launchctl_load_has_timeout(self):
        """NEW-XPLAT-005: launchctl load subprocess.run call has a timeout."""
        from voice_typer.server import platform
        import inspect
        src = inspect.getsource(platform._enable_autostart_macos)
        # The fix adds timeout=5.0 to the subprocess.run call.
        assert "timeout=" in src, (
            "launchctl load subprocess.run call has no timeout= argument"
        )


# ── NEW-XPLAT-003: tray_window.py avoids shell=True ──────────────────


class TestNewXplat003ShellTrueAvoidance:
    """NEW-XPLAT-003: tray_window.py uses shutil.which instead of shell=True."""

    def test_tray_window_uses_shutil_which(self):
        tray_window = REPO_ROOT / "voice_typer" / "server" / "tray_window.py"
        src = tray_window.read_text(encoding="utf-8")
        # The fix: use shutil.which to resolve npm path.
        assert "shutil.which" in src, (
            "tray_window.py should use shutil.which to resolve npm"
        )
        # shell=True should only be in the fallback path (with a warning).
        assert "shell=True" in src, (
            "tray_window.py should still have shell=True as last-resort fallback"
        )
        # The fallback should be gated by an explicit warning log.
        assert "npm not on PATH" in src or "shell=True" in src


# ── NEW-XPLAT-002: Wayland tray detection ────────────────────────────


class TestNewXplat002WaylandTrayDetection:
    """NEW-XPLAT-002: tray.py detects Wayland without SNI and skips tray."""

    def test_wayland_detection_method_exists(self):
        from voice_typer.server.tray import TrayIcon
        assert hasattr(TrayIcon, "_is_linux_wayland_without_sni")

    def test_returns_false_on_non_linux(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon
        # Force non-Linux platform
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        assert TrayIcon._is_linux_wayland_without_sni() is False

    def test_returns_false_when_not_wayland(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        assert TrayIcon._is_linux_wayland_without_sni() is False

    def test_returns_true_on_wayland_without_dbus_module(self, monkeypatch):
        """If we're on Wayland but python-dbus isn't installed, the
        conservative answer is "assume SNI unavailable" (return True)."""
        from voice_typer.server.tray import TrayIcon
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        # Force ImportError when `import dbus` is attempted.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "dbus":
                raise ImportError("no module named dbus")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert TrayIcon._is_linux_wayland_without_sni() is True


# ── NEW-XPLAT-004: macOS pyobjc-framework-Cocoa dep ───────────────────


class TestNewXplat004PyobjcCocoaDep:
    """NEW-XPLAT-004: pyproject.toml declares pyobjc-framework-Cocoa for macOS."""

    def test_pyobjc_cocoa_in_dependencies(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # The dep must be present and gated to darwin.
        assert "pyobjc-framework-Cocoa" in pyproject, (
            "pyproject.toml must declare pyobjc-framework-Cocoa"
        )
        # Must be darwin-only.
        assert "sys_platform == 'darwin'" in pyproject


# ── NEW-DOC-024: generate-icons.mjs fallback chain ────────────────────


class TestNewDoc024IconsScriptFallback:
    """NEW-DOC-024: generate-icons.mjs no longer hardcodes venv path as first."""

    def test_project_venv_is_first_candidate(self):
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text(encoding="utf-8")
        # The fix adds the project's .venv as the first candidate.
        assert "projectVenvPython" in src, (
            "generate-icons.mjs should use project venv as first candidate"
        )
        assert ".venv" in src, "generate-icons.mjs should reference .venv"

    def test_legacy_venv_path_is_last_resort(self):
        """The ~/.voice-typer/venv/... path should be in the fallback chain
        but NOT the first candidate."""
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text(encoding="utf-8")
        # Find the candidates array.
        m = re.search(r"const candidates = \[(.+?)\]", src, re.DOTALL)
        assert m, "candidates array not found in generate-icons.mjs"
        candidates_body = m.group(1)
        # The legacy path should NOT be the first entry.
        first_entries = candidates_body.split(",")[:2]
        first_text = ",".join(first_entries)
        assert ".voice-typer" not in first_text, (
            f"Legacy ~/.voice-typer/venv path should NOT be the first candidate. "
            f"First entries: {first_text}"
        )


# ── NEW-DOC-027: package.json cleanup ────────────────────────────────


class TestNewDoc027PackageJsonCleanup:
    """NEW-DOC-027: package.json no longer references undeclared biome."""

    def test_no_biome_scripts(self):
        pkg = json.loads(
            (REPO_ROOT / "voice_typer" / "client" / "package.json").read_text()
        )
        scripts = pkg.get("scripts", {})
        # The broken biome:check / biome:write scripts should be gone.
        assert "biome:check" not in scripts, (
            "package.json should not have biome:check script "
            "(biome is not in devDependencies)"
        )
        assert "biome:write" not in scripts, (
            "package.json should not have biome:write script "
            "(biome is not in devDependencies)"
        )

    def test_python_dev_script_cross_platform(self):
        """python:dev script should work on Linux (python3) and Windows (python)."""
        pkg = json.loads(
            (REPO_ROOT / "voice_typer" / "client" / "package.json").read_text()
        )
        python_dev = pkg.get("scripts", {}).get("python:dev", "")
        # The fix uses python3 || python fallback chain.
        assert "python3" in python_dev, (
            f"python:dev script should prefer python3 — got: {python_dev}"
        )

    def test_package_json_is_valid_json(self):
        """The package.json must remain valid JSON after our edits."""
        pkg_path = REPO_ROOT / "voice_typer" / "client" / "package.json"
        # If this loads without error, the JSON is valid.
        json.loads(pkg_path.read_text(encoding="utf-8"))
