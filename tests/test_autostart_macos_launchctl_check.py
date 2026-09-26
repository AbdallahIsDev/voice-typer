"""FR-38: regression tests for the macOS launchctl load return-code"""

from __future__ import annotations

import subprocess
import sys

# PRE-IMPORT: ``_enable_autostart_macos`` calls
import urllib.request  # noqa: F401  (side-effect: cache in sys.modules)
import xml.sax.saxutils  # noqa: F401  (side-effect: cache in sys.modules)
from unittest.mock import MagicMock


def _setup_darwin_platform(monkeypatch, tmp_path):
    """Pretend we're on macOS for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "darwin")
    from voice_typer.server import server_platform
    from voice_typer.server.server_platform import autostart as autostart_mod, platform_flags

    monkeypatch.setattr(platform_flags, "SYSTEM", "darwin")

    # Redirect Path.home() to a tmp dir so the plist is written there.
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server_platform.Path, "home", lambda: home)

    # Redirect _paths.config_dir() to tmp via the env override.
    config_dir = tmp_path / "config" / "lausu"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(config_dir))

    # ``_autostart_mod.get_autostart_dir`` is owned by the autostart
    autostart_dir = home / "Library" / "LaunchAgents"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: autostart_dir)

    # ``_os_uid`` is owned by ``autostart_macos``, return a stable value.
    from voice_typer.server.server_platform import autostart_macos

    monkeypatch.setattr(autostart_macos, "_os_uid", lambda: 501)

    return autostart_macos


def _make_completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Build a ``subprocess.CompletedProcess``-like object."""
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestLaunchctlLoadSuccess:
    """FR-38: ``_enable_autostart_macos`` returns True on a clean"""

    def test_returns_true_on_rc_0_clean_stderr(self, monkeypatch, tmp_path):
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(returncode=0, stderr=b""),
        )
        assert mod._enable_autostart_macos() is True

    def test_returns_true_on_rc_0_with_unrelated_stderr(self, monkeypatch, tmp_path):
        """launchctl sometimes writes informational messages to stderr"""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(returncode=0, stderr=b"some informational message"),
        )
        assert mod._enable_autostart_macos() is True


class TestLaunchctlLoadFailure:
    """FR-38: ``_enable_autostart_macos`` returns False on launchctl"""

    def test_returns_false_on_nonzero_returncode(self, monkeypatch, tmp_path):
        """Non-zero returncode → return False (was True pre-fix)."""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(returncode=1, stderr=b"some launchctl error"),
        )
        assert mod._enable_autostart_macos() is False

    def test_returns_false_on_loader_error_substring(self, monkeypatch, tmp_path):
        """stderr contains \"Loader.Error\" → return False even if rc=0"""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(
                returncode=0,
                stderr=b"some_path: Loader.Error: no such file",
            ),
        )
        assert mod._enable_autostart_macos() is False

    def test_returns_false_on_exited_with_substring(self, monkeypatch, tmp_path):
        """stderr contains \"exited with\" → return False (was True"""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(
                returncode=0,
                stderr=b"Job exited with status 1",
            ),
        )
        assert mod._enable_autostart_macos() is False

    def test_returns_false_on_timeout_expired(self, monkeypatch, tmp_path):
        """``subprocess.TimeoutExpired`` → return False (was True"""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0] if args else "launchctl", timeout=5.0)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert mod._enable_autostart_macos() is False

    def test_returns_false_on_generic_exception(self, monkeypatch, tmp_path):
        """Any other Exception → return False (was True pre-fix)."""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)

        def selective_raise(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if cmd and "launchctl" in cmd[0]:
                raise RuntimeError("unexpected launchctl failure")
            # For the system-python probe (and any other subprocess.run
            return _make_completed(returncode=1, stderr=b"")

        monkeypatch.setattr(subprocess, "run", selective_raise)
        assert mod._enable_autostart_macos() is False

    def test_case_insensitive_loader_error_match(self, monkeypatch, tmp_path):
        """The substring check is case-insensitive: \"loader.error\""""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(
                returncode=0,
                stderr=b"loader.error: something went wrong",
            ),
        )
        assert mod._enable_autostart_macos() is False


class TestSourceLevelInvariants:
    """``_enable_autostart_macos`` source contains ``timeout=``. We"""

    def test_source_contains_timeout(self):
        import inspect

        from voice_typer.server.server_platform import autostart_macos

        src = inspect.getsource(autostart_macos._enable_autostart_macos)
        assert "timeout=" in src, (
            "FR-38: must preserve the existing timeout= invariant from "
            "test_platform_and_config.py::TestMacosAutostartPlistWellFormed"
        )

    def test_source_contains_returncode_inspection(self):
        """FR-38: the source must inspect ``CompletedProcess.returncode``."""
        import inspect

        from voice_typer.server.server_platform import autostart_macos

        src = inspect.getsource(autostart_macos._enable_autostart_macos)
        assert "returncode" in src, "FR-38: _enable_autostart_macos must inspect CompletedProcess.returncode"
        assert "loader.error" in src.lower(), "FR-38: _enable_autostart_macos must check for 'Loader.Error' in stderr"

    def test_source_does_not_unconditionally_return_true(self):
        """FR-38: the function must NOT have a bare ``return True``"""
        import inspect
        import re

        from voice_typer.server.server_platform import autostart_macos

        src = inspect.getsource(autostart_macos._enable_autostart_macos)

        # Find the actual ``return True`` statement (4-space indent at
        return_true_match = re.search(r"^    return True\s*$", src, re.MULTILINE)
        assert return_true_match is not None, (
            "FR-38: must have an actual `return True` statement at the function-body indent level"
        )
        returncode_check_idx = src.find("completed.returncode")
        assert returncode_check_idx != -1, "FR-38: must reference completed.returncode"
        assert returncode_check_idx < return_true_match.start(), (
            "FR-38: the actual `return True` statement must come AFTER "
            "the returncode check (pre-fix bug was a bare `return True` "
            "immediately after the try/except, before any returncode "
            "inspection)"
        )
