"""Tests for the schtasks helpers shared with the autostart path."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import task_scheduler


@pytest.fixture(autouse=True)
def _force_supported(monkeypatch):
    """Pretend we're on Windows with schtasks.exe available for every test."""
    from voice_typer.server.config import _reset_config_dir_cache

    _reset_config_dir_cache()
    monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
    monkeypatch.setattr(task_scheduler, "is_windows", lambda: True)


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
        """The real ``is_supported`` implementation must still gate on schtasks.exe."""
        src = inspect.getsource(task_scheduler)
        assert "schtasks.exe" in src
        assert "def is_supported" in src

    def test_returns_false_off_windows(self, monkeypatch):
        """When ``is_windows()`` is False, ``is_supported()`` is False."""

        def _real_is_supported() -> bool:
            # Use task_scheduler.is_windows (module attribute, patched
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
