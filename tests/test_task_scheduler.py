"""Tests for the Windows Scheduled Task registration layer.

These mock ``subprocess.run`` (schtasks) so no real task is created.
Platform checks are monkeypatched so tests run on any OS.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_typer.server import task_scheduler


@pytest.fixture(autouse=True)
def _force_supported(monkeypatch):
    """Pretend we're on Windows with schtasks.exe available for every test."""
    monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)


# ─── Registration / query / deletion ────────────────────────────────────


class TestTaskRegistration:
    """register / unregister / is_registered round-trip via mocked schtasks."""

    def test_register_succeeds(self, monkeypatch, tmp_path):
        """A successful schtasks /Create → returns True."""
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = "SUCCESS: Scheduled task created."
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            task_scheduler, "_prewarm_command",
            lambda: '"python.exe" -m voice_typer.server.prewarm',
        )

        assert task_scheduler.register_prewarm_task() is True
        # Confirm schtasks was called with /Create /XML ... /F
        assert any("/Create" in c for c in calls)
        assert any("/TN" in c and task_scheduler.TASK_NAME in c for c in calls)

    def test_register_returns_false_on_schtasks_failure(self, monkeypatch):
        """If schtasks returns non-zero, registration fails gracefully."""
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "Access denied."
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            task_scheduler, "_prewarm_command",
            lambda: '"python.exe" -m voice_typer.server.prewarm',
        )
        # Also prevent the HKCU Run-key fallback from succeeding on real
        # Windows — we want to test the schtasks failure path only.
        monkeypatch.setattr(
            task_scheduler, "_register_prewarm_registry",
            lambda _cmd: False,
        )
        assert task_scheduler.register_prewarm_task() is False

    def test_register_returns_false_when_command_unresolvable(self, monkeypatch):
        """If the prewarm command can't be resolved, bail before calling schtasks."""
        monkeypatch.setattr(task_scheduler, "_prewarm_command", lambda: None)
        # subprocess.run should NOT be called.
        called = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
        assert task_scheduler.register_prewarm_task() is False
        assert called == []

    def test_unregister_succeeds(self, monkeypatch):
        """schtasks /Delete returns 0 → True."""
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert task_scheduler.unregister_prewarm_task() is True

    def test_unregister_already_absent_is_success(self, monkeypatch):
        """rc=1 with 'cannot find' message → still True (idempotent)."""
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "ERROR: The system cannot find the file specified."
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert task_scheduler.unregister_prewarm_task() is True

    def test_unregister_failure_is_false(self, monkeypatch):
        """rc=1 with a non-'cannot find' error → False."""
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "ERROR: Access denied."
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        # Prevent the HKCU Run-key cleanup from succeeding on real Windows.
        monkeypatch.setattr(
            task_scheduler, "_unregister_prewarm_registry",
            lambda: False,
        )
        # Also prevent is_prewarm_registered from short-circuiting on a
        # real HKCU Run key that exists on the test machine.
        monkeypatch.setattr(
            task_scheduler, "is_prewarm_registered",
            lambda: True,
        )
        assert task_scheduler.unregister_prewarm_task() is False

    def test_is_registered_true_when_query_succeeds(self, monkeypatch):
        """schtasks /Query returns 0 → task exists."""
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert task_scheduler.is_prewarm_registered() is True

    def test_is_registered_false_when_query_fails(self, monkeypatch):
        """schtasks /Query returns non-zero → task absent."""
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "task not found"
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert task_scheduler.is_prewarm_registered() is False


# ─── Unsupported platform ──────────────────────────────────────────────


class TestUnsupportedPlatform:
    """When is_supported() is False, everything no-ops."""

    def test_all_ops_return_false_when_unsupported(self, monkeypatch):
        monkeypatch.setattr(task_scheduler, "is_supported", lambda: False)
        assert task_scheduler.is_prewarm_registered() is False
        assert task_scheduler.register_prewarm_task() is False
        assert task_scheduler.unregister_prewarm_task() is False


# ─── Task XML generation ────────────────────────────────────────────────


class TestTaskXml:
    """The generated XML is well-formed and contains required elements."""

    def test_xml_contains_logon_and_idle_triggers(self):
        xml = task_scheduler._build_task_xml(
            '"python.exe" -m voice_typer.server.prewarm'
        )
        assert "LogonTrigger" in xml
        assert "IdleTrigger" in xml
        assert "PT45S" in xml  # logon delay

    def test_xml_contains_hidden_and_background_settings(self):
        xml = task_scheduler._build_task_xml(
            '"python.exe" -m voice_typer.server.prewarm'
        )
        assert "<Hidden>true</Hidden>" in xml
        # ExecutionTimeLimit prevents runaway prewarms.
        assert "ExecutionTimeLimit" in xml

    def test_xml_uses_cmd_exe_wrapper(self):
        """The action invokes cmd.exe /c so quoting is robust."""
        xml = task_scheduler._build_task_xml(
            '"C:\\path\\python.exe" -m voice_typer.server.prewarm'
        )
        assert "cmd.exe" in xml
        assert "/c " in xml

    def test_xml_is_valid_xml(self):
        """The generated string parses as well-formed XML."""
        import xml.etree.ElementTree as ET
        xml = task_scheduler._build_task_xml('"python.exe" -m foo')
        # Should not raise ParseError.
        root = ET.fromstring(xml)
        assert root.tag.endswith("Task")


# ─── Command resolution ────────────────────────────────────────────────


class TestPrewarmCommand:
    """_prewarm_command prefers the app venv's pythonw, falls back to
    sys.executable's sibling pythonw."""

    def test_prefers_venv_pythonw(self, monkeypatch, tmp_path):
        """If ~/.voice-typer/venv/Scripts/pythonw.exe exists, use it."""
        fake_home = tmp_path
        venv_py = fake_home / ".voice-typer" / "venv" / "Scripts" / "pythonw.exe"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("")  # exists() must return True
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        cmd = task_scheduler._prewarm_command()
        assert "prewarm" in cmd
        assert str(venv_py.resolve()) in cmd.replace('"', '')

    def test_falls_back_to_sys_pythonw(self, monkeypatch, tmp_path):
        """No venv → pythonw.exe next to sys.executable."""
        fake_home = tmp_path
        # No venv pythonw at this path — fallback to sys.executable sibling.
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        cmd = task_scheduler._prewarm_command()
        expected_pythonw = str(Path(sys.executable).parent / "pythonw.exe")
        assert expected_pythonw in cmd.replace('"', '')
