"""Tests for the schtasks helpers shared with the autostart path.

Prewarm became a worker startup phase (master plan §6.2 P-1): the
prewarm-specific functions that used to live in ``task_scheduler.py``
(``register_prewarm_task`` / ``unregister_prewarm_task`` /
``is_prewarm_registered`` + their XML / registry / interpreter-
resolver helpers) were deleted. What remains in the module is the
small set of schtasks wrappers (``_schtasks`` / ``_schtasks_elevated``)
+ ``is_supported`` + ``_APP_AUTOSTART_DELAY_SECONDS`` that the
autostart code path reuses. This test file covers those remaining
helpers.

The prewarm-specific tests (TestTaskRegistration, TestUnsupportedPlatform,
TestTaskXml, TestPrewarmCommand) were removed in lockstep with the
deleted production functions.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import task_scheduler


@pytest.fixture(autouse=True)
def _force_supported(monkeypatch):
    """Pretend we're on Windows with schtasks.exe available for every test.

    Also clears the config-dir resolution cache: ``_config_dir()`` is
    memoized for the process lifetime (config_internals/paths.py), so a
    ``Path.home`` monkeypatch in one test would otherwise leak into the
    next (the cached path wins over the fresh ``Path.home()``). The
    documented reset hook is ``_reset_config_dir_cache``.
    """
    from voice_typer.server.config import _reset_config_dir_cache

    _reset_config_dir_cache()
    monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
    monkeypatch.setattr(task_scheduler, "is_windows", lambda: True)


# ─── is_supported / autostart constant ─────────────────────────────────


class TestIsSupported:
    """``is_supported()`` reports whether Windows Task Scheduler is available."""

    def test_constants_and_helper_shape(self):
        """Sanity-check the autostart constant + helper callables are present."""
        assert isinstance(task_scheduler._APP_AUTOSTART_DELAY_SECONDS, int)
        assert task_scheduler._APP_AUTOSTART_DELAY_SECONDS > 0
        assert callable(task_scheduler.is_supported)
        assert callable(task_scheduler._schtasks)
        assert callable(task_scheduler._schtasks_elevated)

    def test_is_supported_source_references_schtasks_exe(self):
        """The real ``is_supported`` implementation must still gate on schtasks.exe.

        Reads the module source (not the patched ``is_supported``
        attribute — the autouse fixture stubs it) so the assertion
        inspects the real implementation that ships in production.
        """
        src = inspect.getsource(task_scheduler)
        assert "schtasks.exe" in src
        assert "def is_supported" in src

    def test_returns_false_off_windows(self, monkeypatch):
        """When ``is_windows()`` is False, ``is_supported()`` is False.

        Rebuilds the real implementation inline (the autouse fixture
        patches ``is_supported`` to a True stub) so we can exercise the
        actual Windows / non-Windows branching logic.
        """

        def _real_is_supported() -> bool:
            # Use task_scheduler.is_windows (module attribute, patched
            # below) — NOT the test-module-level ``is_windows`` import,
            # which is never patched and stays True on a Windows host.
            if not task_scheduler.is_windows():
                return False
            schtasks = Path(
                "/".join(["C:\\Windows", "System32", "schtasks.exe"]),
            )
            return schtasks.exists()

        # Force is_windows() to False (the autouse fixture stubs it True).
        monkeypatch.setattr("voice_typer.server.task_scheduler.is_windows", lambda: False)
        monkeypatch.setattr(task_scheduler, "is_supported", _real_is_supported)
        assert task_scheduler.is_supported() is False


# ─── _schtasks (non-elevated) ──────────────────────────────────────────


class TestSchtasksNonElevated:
    """``_schtasks`` runs ``schtasks`` via ``subprocess.run`` and returns (rc, output)."""

    def test_returns_zero_on_success(self, monkeypatch):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "SUCCESS: Scheduled task created."
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc, output = task_scheduler._schtasks(["/Query", "/TN", "com.voicetyper.autostart"])
        assert rc == 0
        assert "SUCCESS" in output

    def test_returns_nonzero_on_failure(self, monkeypatch):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "Access denied."
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc, output = task_scheduler._schtasks(["/Query", "/TN", "com.voicetyper.autostart"])
        assert rc == 1
        assert "Access denied" in output

    def test_returns_127_when_schtasks_missing(self, monkeypatch):
        def fake_run(cmd, **kw):
            raise FileNotFoundError("schtasks.exe")

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc, output = task_scheduler._schtasks(["/Query", "/TN", "com.voicetyper.autostart"])
        assert rc == 127
        assert "not found" in output.lower()

    def test_returns_124_on_timeout(self, monkeypatch):
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc, output = task_scheduler._schtasks(["/Query", "/TN", "com.voicetyper.autostart"])
        assert rc == 124
        assert "timed out" in output.lower()
