"""FR-38 — regression tests for the macOS launchctl load return-code
check in :mod:`voice_typer.server.server_platform.autostart_macos`.

Pre-fix symptom: ``_enable_autostart_macos`` unconditionally returned
``True`` after the ``subprocess.run(["launchctl", "load", ...])`` call,
even when launchctl load FAILED (non-zero returncode, "Loader.Error"
in stderr, or TimeoutExpired). The renderer showed "Autostart enabled"
and the user rebooted to find Voice Typer didn't start — with no
diagnostic.

Post-fix: the function inspects ``CompletedProcess.returncode`` and
``stderr``. Returns False on non-zero returncode OR the known error
substrings ("Loader.Error", "exited with"). Returns False on
TimeoutExpired. Returns True ONLY on a clean launchctl load (rc=0, no
error substrings).

These tests run on any platform — they mock ``subprocess.run`` and the
``_pkg.get_autostart_dir()`` / ``Path.home()`` helpers so the test
doesn't actually write to ``$HOME/Library/LaunchAgents`` or invoke
``launchctl``.
"""

from __future__ import annotations

import subprocess
import sys

# PRE-IMPORT: ``_enable_autostart_macos`` calls
# ``from xml.sax.saxutils import escape`` at function-call time, which
# transitively imports ``urllib.request``. On a Linux test host,
# ``urllib.request`` only imports the macOS-only ``_scproxy`` extension
# when ``sys.platform == "darwin"``. The ``_setup_darwin_platform``
# helper below monkeypatches ``sys.platform`` to ``"darwin"``, so if
# ``urllib.request`` is imported AFTER that monkeypatch, Python tries
# to load ``_scproxy`` (which doesn't exist on Linux) and raises
# ``ModuleNotFoundError``. Pre-importing ``xml.sax.saxutils`` (and its
# transitive deps) here at module-load time — BEFORE any monkeypatch —
# caches them in ``sys.modules`` so the subsequent
# ``from xml.sax.saxutils import escape`` inside
# ``_enable_autostart_macos`` is a no-op sys.modules lookup.
import urllib.request  # noqa: F401  (side-effect: cache in sys.modules)
import xml.sax.saxutils  # noqa: F401  (side-effect: cache in sys.modules)
from unittest.mock import MagicMock


def _setup_darwin_platform(monkeypatch, tmp_path):
    """Pretend we're on macOS for the duration of the test.

    Returns ``(module, tmp_path)`` where ``module`` is the
    ``autostart_macos`` module under test.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    from voice_typer.server import server_platform

    monkeypatch.setattr(server_platform, "SYSTEM", "darwin")

    # Redirect Path.home() to a tmp dir so the plist is written there.
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server_platform.Path, "home", lambda: home)

    # Redirect _paths.config_dir() to tmp via the env override.
    config_dir = tmp_path / "config" / "voice-typer"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(config_dir))

    # ``_pkg.get_autostart_dir`` is routed through the server_platform
    # namespace — point it at the tmp LaunchAgents dir.
    autostart_dir = home / "Library" / "LaunchAgents"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: autostart_dir)

    # ``_pkg._os_uid`` — return a stable value.
    monkeypatch.setattr(server_platform, "_os_uid", lambda: 501)

    from voice_typer.server.server_platform import autostart_macos

    return autostart_macos


def _make_completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Build a ``subprocess.CompletedProcess``-like object."""
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# ─── FR-38: launchctl load success path ──────────────────────────────


class TestLaunchctlLoadSuccess:
    """FR-38: ``_enable_autostart_macos`` returns True on a clean
    launchctl load (rc=0, empty stderr)."""

    def test_returns_true_on_rc_0_clean_stderr(self, monkeypatch, tmp_path):
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(returncode=0, stderr=b""),
        )
        assert mod._enable_autostart_macos() is True

    def test_returns_true_on_rc_0_with_unrelated_stderr(self, monkeypatch, tmp_path):
        """launchctl sometimes writes informational messages to stderr
        that aren't errors (e.g. deprecation notices). These should NOT
        trigger the error-substring check."""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: _make_completed(returncode=0, stderr=b"some informational message"),
        )
        assert mod._enable_autostart_macos() is True


# ─── FR-38: launchctl load failure paths ─────────────────────────────


class TestLaunchctlLoadFailure:
    """FR-38: ``_enable_autostart_macos`` returns False on launchctl
    load failure (non-zero returncode OR error substrings OR timeout)."""

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
        """stderr contains "Loader.Error" → return False even if rc=0
        (defensive against the launchctl bug where returncode is 0 but
        stderr reports an error)."""
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
        """stderr contains "exited with" → return False (was True
        pre-fix)."""
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
        """``subprocess.TimeoutExpired`` → return False (was True
        pre-fix)."""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0] if args else "launchctl", timeout=5.0)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert mod._enable_autostart_macos() is False

    def test_returns_false_on_generic_exception(self, monkeypatch, tmp_path):
        """Any other Exception → return False (was True pre-fix).

        The mock only raises on the ``launchctl load`` call — the
        ``_system_python_can_import_launcher`` probe (which also calls
        ``subprocess.run``) returns a normal CompletedProcess so the
        probe doesn't propagate the RuntimeError."""
        mod = _setup_darwin_platform(monkeypatch, tmp_path)

        def selective_raise(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if cmd and "launchctl" in cmd[0]:
                raise RuntimeError("unexpected launchctl failure")
            # For the system-python probe (and any other subprocess.run
            # call), return a normal CompletedProcess so the probe
            # doesn't propagate the RuntimeError.
            return _make_completed(returncode=1, stderr=b"")

        monkeypatch.setattr(subprocess, "run", selective_raise)
        assert mod._enable_autostart_macos() is False

    def test_case_insensitive_loader_error_match(self, monkeypatch, tmp_path):
        """The substring check is case-insensitive — "loader.error"
        (lowercase) should also trigger the failure path."""
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


# ─── FR-38: existing source-level assertions still hold ─────────────


class TestSourceLevelInvariants:
    """FR-38: the existing source-level tests in
    ``tests/test_platform_and_config.py`` assert that
    ``_enable_autostart_macos`` source contains ``timeout=``. We
    verify the FR-38 fix preserves that invariant AND adds the new
    returncode / stderr inspection."""

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
        """FR-38: the function must NOT have a bare ``return True``
        immediately after the subprocess.run try/except (the pre-fix
        bug). We verify the actual ``return True`` STATEMENT (not the
        word inside a comment) comes AFTER the returncode check.

        We use a regex that matches ``return True`` as a Python
        statement (indented at the function-body level, not inside a
        comment or docstring) to find the actual return statement."""
        import inspect
        import re

        from voice_typer.server.server_platform import autostart_macos

        src = inspect.getsource(autostart_macos._enable_autostart_macos)

        # Find the actual ``return True`` statement (4-space indent at
        # function-body level, NOT inside a comment / docstring / inline
        # ``return True`` mention). Use MULTILINE so ``^`` matches at
        # the start of each line.
        return_true_match = re.search(r"^    return True\s*$", src, re.MULTILINE)
        assert return_true_match is not None, (
            "FR-38: must have an actual `return True` statement at the function-body indent level"
        )
        # Find the first ``completed.returncode`` reference (the
        # returncode inspection that gates the ``return True``).
        returncode_check_idx = src.find("completed.returncode")
        assert returncode_check_idx != -1, "FR-38: must reference completed.returncode"
        assert returncode_check_idx < return_true_match.start(), (
            "FR-38: the actual `return True` statement must come AFTER "
            "the returncode check (pre-fix bug was a bare `return True` "
            "immediately after the try/except, before any returncode "
            "inspection)"
        )
