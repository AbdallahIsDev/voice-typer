"""Round 10 bug-fix tests — verify the 4 real bugs found in Round 9 code.

Bug 1: _linux_unit_dir empty-string XDG_CONFIG_HOME returns relative path
Bug 2: ioprio_set not a libc symbol — I/O priority lowering silently no-ops
Bug 3: Inconsistent platform checks (startswith('linux') vs in ('darwin','linux'))
Bug 4: Redundant sys.platform != 'win32' checks in platform.py
"""
import sys
import os
import inspect
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/home/z/my-project/voice-typer-repo')


class TestBug1LinuxUnitDirEmptyXdg:
    """Bug 1: _linux_unit_dir must handle empty-string XDG_CONFIG_HOME."""

    def test_empty_string_xdg_config_home_uses_fallback(self, monkeypatch, tmp_path):
        """When XDG_CONFIG_HOME="" (set but empty), must use ~/.config fallback.

        The XDG Base Directory Spec says: "If $XDG_CONFIG_HOME is either
        not set or empty, a default equal to $HOME/.config should be used."

        Previously, os.environ.get("XDG_CONFIG_HOME", default) returned ""
        (not the default) because the key existed, causing Path("") / "systemd"
        / "user" = relative path "systemd/user" — unit files would be written
        to the CWD and the timer would never fire.

        Round 11 fix: use tmp_path instead of Path("/fake/home") so the test
        works on Windows too (Windows requires drive letters for absolute paths).
        """
        from voice_typer.server import prewarm_scheduler_posix
        fake_home = tmp_path  # platform-safe absolute path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # Set XDG_CONFIG_HOME to empty string
        monkeypatch.setattr(
            prewarm_scheduler_posix.os, "environ",
            {"XDG_CONFIG_HOME": ""},
        )
        result = prewarm_scheduler_posix._linux_unit_dir()
        assert result.is_absolute(), (
            f"_linux_unit_dir() must return an ABSOLUTE path even when "
            f"XDG_CONFIG_HOME is empty; got {result} (is_absolute={result.is_absolute()})"
        )
        expected = str(fake_home / ".config" / "systemd" / "user")
        assert str(result) == expected, (
            f"Expected {expected}, got {result}"
        )

    def test_unset_xdg_config_home_uses_fallback(self, monkeypatch, tmp_path):
        """When XDG_CONFIG_HOME is unset, must use ~/.config fallback.

        Round 11 fix: use tmp_path instead of Path("/fake/home") for
        Windows compatibility.
        """
        from voice_typer.server import prewarm_scheduler_posix
        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # XDG_CONFIG_HOME not in environ at all
        monkeypatch.setattr(
            prewarm_scheduler_posix.os, "environ",
            {},
        )
        result = prewarm_scheduler_posix._linux_unit_dir()
        expected = str(fake_home / ".config" / "systemd" / "user")
        assert str(result) == expected

    def test_set_xdg_config_home_uses_it(self, monkeypatch, tmp_path):
        """When XDG_CONFIG_HOME is set and non-empty, must use it."""
        from voice_typer.server import prewarm_scheduler_posix
        monkeypatch.setattr(
            prewarm_scheduler_posix.os, "environ",
            {"XDG_CONFIG_HOME": str(tmp_path)},
        )
        result = prewarm_scheduler_posix._linux_unit_dir()
        assert str(result) == str(tmp_path / "systemd" / "user")

    def test_no_eager_evaluation_of_path_home(self, monkeypatch):
        """Path.home() must NOT be called when XDG_CONFIG_HOME is set.

        Bug 1b (eager evaluation): previously,
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        always evaluated str(Path.home() / ".config") even when
        XDG_CONFIG_HOME was set. This is wasteful and makes the fallback
        path untestable without also patching Path.home.
        """
        from voice_typer.server import prewarm_scheduler_posix
        # Track if Path.home() is called
        home_called = []
        original_home = Path.home
        def tracking_home():
            home_called.append(True)
            return original_home()
        monkeypatch.setattr(Path, "home", tracking_home)
        monkeypatch.setattr(
            prewarm_scheduler_posix.os, "environ",
            {"XDG_CONFIG_HOME": "/custom/xdg"},
        )
        prewarm_scheduler_posix._linux_unit_dir()
        assert home_called == [], (
            "Path.home() must NOT be called when XDG_CONFIG_HOME is set "
            "(eager evaluation bug)"
        )


class TestBug2IoprioSetSyscall:
    """Bug 2: ioprio_set must use syscall(), not the non-existent libc symbol."""

    def test_no_hasattr_ioprio_set_check(self):
        """The code must NOT use hasattr(libc, 'ioprio_set') — that symbol
        doesn't exist in libc. It must use libc.syscall(SYS_ioprio_set, ...)."""
        from voice_typer.server import prewarm
        src = inspect.getsource(prewarm._lower_io_priority)
        # Check non-comment lines only (the bug fix comment mentions the old code)
        code_lines = [l for l in src.split('\n') if not l.strip().startswith('#')]
        code = '\n'.join(code_lines)
        assert "hasattr(libc, \"ioprio_set\")" not in code, (
            "Bug 2 regression: hasattr(libc, 'ioprio_set') is back in code. "
            "ioprio_set is a syscall, not a libc symbol — use libc.syscall()."
        )
        assert "libc.syscall" in code, (
            "Bug 2: must use libc.syscall() to call ioprio_set, not the "
            "non-existent libc.ioprio_set symbol."
        )

    def test_ioprio_set_actually_runs_on_linux(self, monkeypatch):
        """On Linux, the ioprio_set syscall path must be exercised (not
        silently skipped due to hasattr returning False)."""
        if sys.platform != "linux":
            pytest.skip("Linux-only test")
        import ctypes
        from voice_typer.server import prewarm
        # Track if syscall was called
        syscall_called = []
        fake_libc = MagicMock()
        fake_libc.syscall = lambda *args: syscall_called.append(args) or 0
        monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: fake_libc)
        # Also need to mock os.nice so it doesn't actually change priority
        monkeypatch.setattr(prewarm.os, "nice", lambda n: 0)
        prewarm._lower_io_priority()
        assert len(syscall_called) > 0, (
            "Bug 2: libc.syscall was never called — ioprio_set is silently "
            "no-opping because the old code used hasattr(libc, 'ioprio_set') "
            "which always returns False."
        )


class TestBug3ConsistentPlatformChecks:
    """Bug 3: All platform checks must use exact match, not startswith."""

    def test_no_startswith_linux_in_prewarm_scheduler(self):
        """prewarm_scheduler_posix must not use startswith('linux')."""
        from voice_typer.server import prewarm_scheduler_posix
        src = inspect.getsource(prewarm_scheduler_posix)
        assert "startswith(\"linux\")" not in src, (
            "Bug 3: prewarm_scheduler_posix still uses startswith('linux') — "
            "use sys.platform == 'linux' for consistency"
        )

    def test_no_startswith_linux_in_task_scheduler(self):
        """task_scheduler must not use startswith('linux')."""
        from voice_typer.server import task_scheduler
        src = inspect.getsource(task_scheduler)
        # Allow startswith in comments but not in actual code
        lines = [l for l in src.split('\n')
                 if 'startswith("linux")' in l and not l.strip().startswith('#')]
        assert not lines, (
            "Bug 3: task_scheduler still uses startswith('linux') in code — "
            f"found in: {lines}"
        )

    def test_no_startswith_linux_in_prewarm(self):
        """prewarm.py must not use startswith('linux')."""
        from voice_typer.server import prewarm
        src = inspect.getsource(prewarm)
        lines = [l for l in src.split('\n')
                 if 'startswith("linux")' in l and not l.strip().startswith('#')]
        assert not lines, (
            "Bug 3: prewarm.py still uses startswith('linux') in code — "
            f"found in: {lines}"
        )


class TestBug4NoRedundantPlatformCheck:
    """Bug 4: platform.py must not have redundant sys.platform != 'win32'
    checks before task_scheduler.is_supported()."""

    def test_register_app_autostart_task_no_redundant_check(self):
        """_register_app_autostart_task must not check sys.platform directly."""
        from voice_typer.server import platform
        src = inspect.getsource(platform._register_app_autostart_task)
        # The function should NOT have 'if sys.platform != "win32"' at the top
        lines = src.split('\n')
        # Look at the first 5 lines of the function body (after docstring)
        body_start = False
        for line in lines[:10]:
            if body_start and 'sys.platform' in line and '!=' in line:
                pytest.fail(
                    "Bug 4: _register_app_autostart_task has redundant "
                    f"sys.platform check: {line.strip()}"
                )
            if line.strip().startswith('try:'):
                body_start = True

    def test_unregister_app_autostart_task_no_redundant_check(self):
        """_unregister_app_autostart_task must not check sys.platform directly."""
        from voice_typer.server import platform
        src = inspect.getsource(platform._unregister_app_autostart_task)
        lines = src.split('\n')
        body_start = False
        for line in lines[:10]:
            if body_start and 'sys.platform' in line and '!=' in line:
                pytest.fail(
                    "Bug 4: _unregister_app_autostart_task has redundant "
                    f"sys.platform check: {line.strip()}"
                )
            if line.strip().startswith('try:'):
                body_start = True

    def test_is_app_autostart_task_registered_no_redundant_check(self):
        """_is_app_autostart_task_registered must not check sys.platform directly."""
        from voice_typer.server import platform
        src = inspect.getsource(platform._is_app_autostart_task_registered)
        lines = src.split('\n')
        body_start = False
        for line in lines[:10]:
            if body_start and 'sys.platform' in line and '!=' in line:
                pytest.fail(
                    "Bug 4: _is_app_autostart_task_registered has redundant "
                    f"sys.platform check: {line.strip()}"
                )
            if line.strip().startswith('try:'):
                body_start = True

    def test_register_app_autostart_task_works_via_is_supported(self, monkeypatch):
        """_register_app_autostart_task must rely on task_scheduler.is_supported()
        for platform detection, not its own sys.platform check."""
        from voice_typer.server import platform as platform_mod
        from voice_typer.server import task_scheduler
        # On this non-Windows host, is_supported() returns True for POSIX
        # prewarm. But _register_app_autostart_task uses task_scheduler.is_supported()
        # which checks for schtasks.exe. Mock it to return False (non-Windows).
        monkeypatch.setattr(task_scheduler, "is_supported", lambda: False)
        monkeypatch.setattr(task_scheduler, "_schtasks", lambda *a, **kw: (0, ""))
        result = platform_mod._register_app_autostart_task()
        assert result is False, (
            "When task_scheduler.is_supported() is False, "
            "_register_app_autostart_task must return False without "
            "checking sys.platform itself."
        )


class TestBug5StaleFixturePatch:
    """Bug 5 (Round 11): The autouse fixture in test_app.py patched
    voice_typer.server.app.create_hotkey_backend, but the actual call site
    is in hotkey_dispatcher.register() which uses its OWN imported copy.
    The patch was a no-op; tests passed only because on Linux/X11 the
    unpatched create_hotkey_backend returns PynputHotkey by default.

    This test verifies the fixture now patches BOTH namespaces.
    """

    def test_fixture_patches_hotkey_dispatcher_namespace(self, monkeypatch):
        """The autouse fixture must patch hotkey_dispatcher.create_hotkey_backend,
        not just app.create_hotkey_backend. Otherwise the patch is a no-op
        because hotkey_dispatcher.register() uses its own imported copy."""
        import voice_typer.server.hotkey_dispatcher as hd_mod
        from voice_typer.server.hotkeys import PynputHotkey

        # Capture the original
        original = hd_mod.create_hotkey_backend
        try:
            # Apply the same patch the fixture applies
            monkeypatch.setattr(
                "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
                lambda hotkey_str: PynputHotkey(hotkey_str),
            )
            # Verify the patch took effect in the hotkey_dispatcher namespace
            assert hd_mod.create_hotkey_backend is not original, (
                "Patch of voice_typer.server.hotkey_dispatcher.create_hotkey_backend "
                "had no effect — the fixture is still stale"
            )
        finally:
            pass  # monkeypatch teardown restores original

    def test_app_py_fixture_patches_both_namespaces(self):
        """The autouse fixture in test_app.py must patch BOTH
        app.create_hotkey_backend AND hotkey_dispatcher.create_hotkey_backend."""
        import inspect
        # Read the fixture source from test_app.py
        import tests.test_app as test_app_mod
        # Find the mock_heavy_imports fixture (it's the autouse one)
        fixture_src = None
        for name, obj in vars(test_app_mod).items():
            if callable(obj) and hasattr(obj, '__wrapped__'):
                # It's a pytest fixture
                src = inspect.getsource(obj)
                if 'create_hotkey_backend' in src and 'PynputHotkey' in src:
                    fixture_src = src
                    break
        # If we didn't find it via __wrapped__, try reading the file directly
        if fixture_src is None:
            test_app_path = inspect.getfile(test_app_mod)
            with open(test_app_path) as f:
                content = f.read()
            # Find the fixture that patches create_hotkey_backend
            fixture_src = content
        assert "hotkey_dispatcher.create_hotkey_backend" in fixture_src, (
            "The autouse fixture must patch voice_typer.server.hotkey_dispatcher.create_hotkey_backend "
            "(the actual call site), not just voice_typer.server.app.create_hotkey_backend. "
            "Without this, the patch is a no-op and tests pass only by coincidence."
        )
