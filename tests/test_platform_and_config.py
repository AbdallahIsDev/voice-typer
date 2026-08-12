"""Tests for platform detection, config directory, autostart, desktop quoting,
and cross-platform behavior."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDesktopQuoteFollowsFreedesktopSpec:
    """_desktop_quote follows the freedesktop spec."""

    def test_no_reserved_chars_returns_unquoted(self):
        from voice_typer.server.server_platform import _desktop_quote

        assert _desktop_quote("python3") == "python3"
        assert _desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
        assert _desktop_quote("--hidden") == "--hidden"

    def test_space_triggers_quoting(self):
        from voice_typer.server.server_platform import _desktop_quote

        assert _desktop_quote("/path with spaces/app") == '"/path with spaces/app"'

    def test_backslash_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote

        result = _desktop_quote("C:\\Users\\John\\app")
        assert result == '"C:\\\\Users\\\\John\\\\app"'

    def test_double_quote_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote

        result = _desktop_quote('John "Bob"')
        assert result == '"John \\"Bob\\""'

    def test_dollar_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote

        result = _desktop_quote("$HOME/app")
        assert result == '"\\$HOME/app"'

    def test_backtick_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote

        result = _desktop_quote("path with `backtick`")
        assert result == '"path with \\`backtick\\`"'

    def test_newline_rejected(self):
        """XZ-R6-AS-04: args containing a newline/carriage-return are
        rejected with ValueError — a literal newline inside a quoted
        Exec field would still terminate the line and inject a new
        .desktop field, so no amount of quoting can make it safe.
        """
        from voice_typer.server.server_platform import _desktop_quote

        with pytest.raises(ValueError):
            _desktop_quote("evil\nName=Foo")
        with pytest.raises(ValueError):
            _desktop_quote("evil\rName=Foo")
        # Normal args still quote/escape as before.
        assert _desktop_quote("/path with spaces/app") == '"/path with spaces/app"'

    def test_autostart_command_is_quoted(self):
        from voice_typer.server.server_platform import _autostart_command

        cmd = _autostart_command()
        assert "autostart_launcher.py" in cmd
        assert "python" in cmd.lower() or sys.executable in cmd
        assert "--hidden" in cmd


class TestMacosAutostartPlistWellFormed:
    """macOS autostart plist is well-formed."""

    def test_plist_uses_absolute_working_directory(self):
        from voice_typer.server import server_platform as platform

        src = inspect.getsource(platform._enable_autostart_macos)
        assert "<string>~</string>" not in src
        assert "Path.home()" in src or "str(Path.home())" in src

    def test_launchctl_load_has_timeout(self):
        from voice_typer.server import server_platform as platform

        src = inspect.getsource(platform._enable_autostart_macos)
        assert "timeout=" in src


class TestTrayWindowUsesShutilWhichNotShellTrue:
    """tray_window.py uses shutil.which instead of shell=True.

    S-7: Last-resort ``shell=True`` fallback was removed from
    ``tray_window.open_electron_window``.  The function now resolves
    npm via the shared :func:`_electron_build._npm_command` helper
    (which uses ``shutil.which`` with PATHEXT on Windows) and logs +
    skips when npm truly cannot be resolved.
    """

    def test_tray_window_uses_shutil_which(self):
        import ast

        tray_window = REPO_ROOT / "voice_typer" / "server" / "tray_window.py"
        src = tray_window.read_text(encoding="utf-8")
        # Either the shared ``_npm_command`` helper (preferred) or an
        # inline ``shutil.which`` call is acceptable — both resolve the
        # binary path explicitly so we don't need a shell.
        assert "_npm_command" in src or "shutil.which" in src
        # S-7: no ``shell=True`` keyword argument may appear in any
        # subprocess call.  AST-based check is robust against mentions
        # in comments/docstrings (which are kept for context).
        tree = ast.parse(src)
        shell_true_calls: list[ast.Call] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    shell_true_calls.append(node)
        assert not shell_true_calls, (
            "S-7: tray_window.py must not pass shell=True to any call; "
            f"found {len(shell_true_calls)} offending call(s)."
        )
        assert "npm not on PATH" in src


class TestTrayDetectsWaylandWithoutSni:
    """tray.py detects Wayland without SNI and skips tray."""

    def test_wayland_detection_method_exists(self):
        from voice_typer.server.tray import TrayIcon

        assert hasattr(TrayIcon, "_is_linux_wayland_without_sni")

    def test_returns_false_on_non_linux(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        assert TrayIcon._is_linux_wayland_without_sni() is False

    def test_returns_false_when_not_wayland(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        assert TrayIcon._is_linux_wayland_without_sni() is False

    def test_returns_true_on_wayland_without_dbus_module(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "dbus":
                raise ImportError("no module named dbus")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert TrayIcon._is_linux_wayland_without_sni() is True


class TestPyprojectDeclaresPyobjcCocoaForMacos:
    """pyproject.toml declares pyobjc-framework-Cocoa for macOS."""

    def test_pyobjc_cocoa_in_dependencies(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "pyobjc-framework-Cocoa" in pyproject
        assert "sys_platform == 'darwin'" in pyproject


class TestTrayThreadAttributeNaming:
    """Wayland early-return path uses self._bg_thread (canonical name)."""

    def test_no_bg_work_thread_attribute(self):
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(encoding="utf-8")
        assert "_bg_work_thread" not in tray_py

    def test_bg_thread_used_in_all_three_paths(self):
        """All start paths funnel through the ``_launch_bg_work`` helper,
        which is the single place that assigns ``self._bg_thread``.

        Phase 6 refactor extracted the 4 near-duplicate
        ``if self._bg_work_fn: threading.Thread(...).start()`` blocks
        into ``_launch_bg_work()`` (daemon thread + store on
        ``self._bg_thread``). The test pins the canonical attribute
        name and the shared-launch shape: exactly one assignment site
        for ``self._bg_thread`` and at least 3 callers of the helper
        (the ``VOICE_TYPER_NO_TRAY``, Wayland-without-SNI, pystray
        ``OSError``, and normal start paths).
        """
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(encoding="utf-8")
        # The canonical thread attribute is assigned in exactly ONE place
        # (the shared launch helper) — no drift between paths.
        assert tray_py.count("self._bg_thread = threading.Thread") == 1
        # All start paths call the shared helper.
        assert tray_py.count("_launch_bg_work()") >= 3


class TestElectronUserDataPathMatchesConfigDir:
    """Electron's userData matches Python's config dir."""

    def test_main_sets_user_data_path(self):
        # REF-2 split: userData wiring moved from main/index.ts to
        # bootstrap.ts (bootstrapRuntime sets app.setPath("userData")).
        bootstrap_ts = (REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "bootstrap.ts").read_text(
            encoding="utf-8"
        )
        assert 'app.setPath("userData"' in bootstrap_ts

    def test_main_mirrors_python_config_dir_logic(self):
        # REF-2 split: the config-dir resolver moved to single_instance.ts
        # (computeConfigDir mirrors Python's _config_dir).
        single_instance_ts = (REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "single_instance.ts").read_text(
            encoding="utf-8"
        )
        assert "VOICE_TYPER_CONFIG_DIR" in single_instance_ts
        assert ".voice-typer" in single_instance_ts
        assert "APPDATA" in single_instance_ts
        assert "Application Support" in single_instance_ts
        assert "XDG_DATA_HOME" in single_instance_ts

    def test_gitignore_does_not_ignore_scripts_build(self):
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "/build/" in gitignore
        lines = [ln.strip() for ln in gitignore.splitlines()]
        for line in lines:
            if line == "build/":
                pytest.fail(".gitignore still has unanchored 'build/' pattern")

    def test_sync_versions_script_exists(self):
        script = REPO_ROOT / "scripts" / "build" / "sync_versions.py"
        assert script.exists()


class TestLinuxUnitDirHandlesEmptyXdgConfigHome:
    """_linux_unit_dir handles empty-string XDG_CONFIG_HOME."""

    def test_empty_string_xdg_config_home_uses_fallback(self, monkeypatch, tmp_path):
        from voice_typer.server import prewarm_scheduler_posix

        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(
            prewarm_scheduler_posix.os,
            "environ",
            {"XDG_CONFIG_HOME": ""},
        )
        result = prewarm_scheduler_posix._linux_unit_dir()
        assert result.is_absolute()
        expected = str(fake_home / ".config" / "systemd" / "user")
        assert str(result) == expected

    def test_unset_xdg_config_home_uses_fallback(self, monkeypatch, tmp_path):
        from voice_typer.server import prewarm_scheduler_posix

        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(prewarm_scheduler_posix.os, "environ", {})
        result = prewarm_scheduler_posix._linux_unit_dir()
        expected = str(fake_home / ".config" / "systemd" / "user")
        assert str(result) == expected

    def test_set_xdg_config_home_uses_it(self, monkeypatch, tmp_path):
        from voice_typer.server import prewarm_scheduler_posix

        monkeypatch.setattr(
            prewarm_scheduler_posix.os,
            "environ",
            {"XDG_CONFIG_HOME": str(tmp_path)},
        )
        result = prewarm_scheduler_posix._linux_unit_dir()
        assert str(result) == str(tmp_path / "systemd" / "user")

    def test_no_eager_evaluation_of_path_home(self, monkeypatch):
        from voice_typer.server import prewarm_scheduler_posix

        home_called = []
        original_home = Path.home

        def tracking_home():
            home_called.append(True)
            return original_home()

        monkeypatch.setattr(Path, "home", tracking_home)
        monkeypatch.setattr(
            prewarm_scheduler_posix.os,
            "environ",
            {"XDG_CONFIG_HOME": "/custom/xdg"},
        )
        prewarm_scheduler_posix._linux_unit_dir()
        assert home_called == []


class TestIoprioSetUsesSyscallNotLibcSymbol:
    """ioprio_set uses syscall(), not the non-existent libc symbol."""

    def test_no_hasattr_ioprio_set_check(self):
        from voice_typer.server import prewarm

        src = inspect.getsource(prewarm._lower_io_priority)
        code_lines = [ln for ln in src.split("\n") if not ln.strip().startswith("#")]
        code = "\n".join(code_lines)
        assert 'hasattr(libc, "ioprio_set")' not in code
        assert "libc.syscall" in code

    @pytest.mark.skipif(
        sys.platform != "linux",
        reason="Linux-only: ioprio_set syscall is Linux-specific (syscall number is Linux-defined)",
    )
    def test_ioprio_set_actually_runs_on_linux(self, monkeypatch):
        import ctypes

        from voice_typer.server import prewarm

        syscall_called = []
        fake_libc = MagicMock()
        fake_libc.syscall = lambda *args: syscall_called.append(args) or 0
        monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: fake_libc)
        monkeypatch.setattr(prewarm.os, "nice", lambda n: 0)
        prewarm._lower_io_priority()
        assert len(syscall_called) > 0


class TestPlatformChecksUseExactMatchNotStartswith:
    """All platform checks use exact match, not startswith."""

    def test_no_startswith_linux_in_prewarm_scheduler(self):
        from voice_typer.server import prewarm_scheduler_posix

        src = inspect.getsource(prewarm_scheduler_posix)
        assert 'startswith("linux")' not in src

    def test_no_startswith_linux_in_task_scheduler(self):
        from voice_typer.server import task_scheduler

        src = inspect.getsource(task_scheduler)
        lines = [ln for ln in src.split("\n") if 'startswith("linux")' in ln and not ln.strip().startswith("#")]
        assert not lines

    def test_no_startswith_linux_in_prewarm(self):
        from voice_typer.server import prewarm

        src = inspect.getsource(prewarm)
        lines = [ln for ln in src.split("\n") if 'startswith("linux")' in ln and not ln.strip().startswith("#")]
        assert not lines


class TestNoRedundantPlatformCheckBeforeTaskScheduler:
    """Platform checks delegate to task_scheduler.is_supported()."""

    def test_register_app_autostart_task_no_redundant_check(self):
        from voice_typer.server import server_platform as platform

        src = inspect.getsource(platform._register_app_autostart_task)
        lines = src.split("\n")
        body_start = False
        for line in lines[:10]:
            if body_start and "sys.platform" in line and "!=" in line:
                pytest.fail(f"Redundant platform check: {line.strip()}")
            if line.strip().startswith("try:"):
                body_start = True

    def test_unregister_app_autostart_task_no_redundant_check(self):
        from voice_typer.server import server_platform as platform

        src = inspect.getsource(platform._unregister_app_autostart_task)
        lines = src.split("\n")
        body_start = False
        for line in lines[:10]:
            if body_start and "sys.platform" in line and "!=" in line:
                pytest.fail(f"Redundant platform check: {line.strip()}")
            if line.strip().startswith("try:"):
                body_start = True

    def test_is_app_autostart_task_registered_no_redundant_check(self):
        from voice_typer.server import server_platform as platform

        src = inspect.getsource(platform._is_app_autostart_task_registered)
        lines = src.split("\n")
        body_start = False
        for line in lines[:10]:
            if body_start and "sys.platform" in line and "!=" in line:
                pytest.fail(f"Redundant platform check: {line.strip()}")
            if line.strip().startswith("try:"):
                body_start = True

    def test_register_app_autostart_task_works_via_is_supported(self, monkeypatch):
        from voice_typer.server import server_platform as platform_mod, task_scheduler

        monkeypatch.setattr(task_scheduler, "is_supported", lambda: False)
        monkeypatch.setattr(task_scheduler, "_schtasks", lambda *a, **kw: (0, ""))
        result = platform_mod._register_app_autostart_task()
        assert result is False


class TestConsoleHandlerPythonw:
    """_install_win32_console_handler skips pythonw.exe."""

    def test_skipped_on_pythonw(self, monkeypatch):
        # Phase 7: the pythonw skip lives in
        # signal_handlers.install_win32_console_handler (the module-level
        # function the ShutdownController delegate calls). Test the real
        # skip path with a fake controller carrying a bare _app.
        from voice_typer.server import signal_handlers

        class _FakeApp:
            pass

        class _FakeController:
            _app = _FakeApp()

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", "C:\\Python312\\pythonw.exe")
        # Must not raise and must not install a console handler.
        signal_handlers.install_win32_console_handler(_FakeController())
        assert not hasattr(_FakeController._app, "_console_handler")


class TestConfigDirIsPlatformAware:
    """_config_dir() uses platform-aware paths."""

    def test_config_dir_checks_platform(self):
        from voice_typer.server.config import _config_dir

        source = inspect.getsource(_config_dir)
        assert "sys.platform" in source or "platform" in source
        assert "APPDATA" in source
        assert "XDG_DATA_HOME" in source
        assert "Library" in source and "Application Support" in source

    def test_legacy_path_migration(self):
        from voice_typer.server.config import _config_dir

        source = inspect.getsource(_config_dir)
        assert "legacy" in source


class TestAutoPunctuationDefaultsTrue:
    """auto_punctuation defaults to True."""

    def test_auto_punctuation_defaults_true(self):
        from voice_typer.server.config import Config

        cfg = Config()
        assert cfg.auto_punctuation is True


class TestEscCancelDefaultsTrue:
    """esc_cancel_enabled defaults to True."""

    def test_esc_cancel_defaults_true(self):
        from voice_typer.server.config import Config

        cfg = Config()
        assert cfg.esc_cancel_enabled is True


class TestResetToDefaultsPreservesOnboardingCompleted:
    """Reset to Defaults preserves onboarding_completed."""

    def test_reset_skips_onboarding(self):
        settings = (
            REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        ).read_text(encoding="utf-8")
        # resetToDefaults skips onboarding_completed via the hoisted
        # CONFIG_PROTECTED_KEYS blocklist (excluded from factory reset).
        assert "onboarding_completed" in settings
        assert "CONFIG_PROTECTED_KEYS" in settings
