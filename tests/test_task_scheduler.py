"""Tests for the Windows Scheduled Task registration layer.

These mock ``subprocess.run`` (schtasks) so no real task is created.
Platform checks are monkeypatched so tests run on any OS.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import task_scheduler


@pytest.fixture(autouse=True)
def _force_supported(monkeypatch):
    """Pretend we're on Windows with schtasks.exe available for every test.

    STARTUP-5: register_prewarm_task now branches on sys.platform to
    delegate to prewarm_scheduler_posix on macOS/Linux. Force sys.platform
    to "win32" so the Windows path is exercised even on POSIX test hosts.
    """
    monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
    monkeypatch.setattr(task_scheduler.sys, "platform", "win32")


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
        # STARTUP-1: _prewarm_command now returns just the pythonw path
        # (the cmd.exe wrapper was removed to fix the ghost console window).
        monkeypatch.setattr(
            task_scheduler,
            "_prewarm_command",
            lambda: "C:\\path\\pythonw.exe",
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
        # STARTUP-1: _prewarm_command returns just the pythonw path now
        monkeypatch.setattr(
            task_scheduler,
            "_prewarm_command",
            lambda: "C:\\path\\pythonw.exe",
        )
        # Also prevent the HKCU Run-key fallback from succeeding on real
        # Windows — we want to test the schtasks failure path only.
        monkeypatch.setattr(
            task_scheduler,
            "_register_prewarm_registry",
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
            task_scheduler,
            "_unregister_prewarm_registry",
            lambda: False,
        )
        # Also prevent is_prewarm_registered from short-circuiting on a
        # real HKCU Run key that exists on the test machine.
        monkeypatch.setattr(
            task_scheduler,
            "is_prewarm_registered",
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

    def test_xml_contains_logon_trigger_not_boot_or_event(self):
        """PREWARM-FIX: a single LogonTrigger, no BootTrigger/EventTrigger.

        The earlier ADR-0009 design used BootTrigger + EventTrigger (Event
        ID 12, "OS started") so prewarm would run at system boot, BEFORE
        the user logged on. That is fundamentally incompatible with how
        this task runs: it uses InteractiveToken (the current interactive
        user) and pythonw.exe, both of which REQUIRE a live interactive
        session. Boot/Event triggers fire pre-logon, so the task could
        never start and sat at Last Result 0x41303 ("never run").

        A LogonTrigger fires once the user has logged on — an interactive
        session now exists — so pythonw starts reliably and warms the
        cache well before the app is opened. The in-process boot sentinel
        (prewarm._already_warmed) dedups any re-fire on session unlock, so
        the work happens at most once per boot. Fast Startup still fires
        the LogonTrigger on the user's next logon, preserving coverage.
        """
        # STARTUP-1: _build_task_xml now takes the pythonw path directly
        xml = task_scheduler._build_task_xml("C:\\path\\pythonw.exe")
        # PREWARM-FIX: LogonTrigger present (fires at user logon, when an
        # interactive session exists so pythonw/InteractiveToken can run).
        assert "LogonTrigger" in xml, (
            "PREWARM-FIX regression: LogonTrigger is missing — prewarm won't run at logon (its only reliable trigger)"
        )
        # LogonTrigger must carry a delay element (STARTUP-2: _LOGON_DELAY,
        # PT0S — fire at logon+0 so prewarm gets a head start).
        assert "<Delay>" in xml, "PREWARM-FIX regression: LogonTrigger <Delay> is missing"
        # PREWARM-FIX: BootTrigger/EventTrigger MUST be absent — they fire
        # pre-logon where InteractiveToken + pythonw cannot launch, which
        # left the task permanently at Last Result 0x41303 ("never run").
        assert "BootTrigger" not in xml, (
            "PREWARM-FIX regression: BootTrigger is back — it fires pre-logon where the interactive task cannot start"
        )
        assert "EventTrigger" not in xml, (
            "PREWARM-FIX regression: EventTrigger is back — it fires pre-logon where the interactive task cannot start"
        )
        # PREWARM-001: IdleTrigger stays absent (it fired 5+ times per
        # session, re-reading ~6 GB of already-cached files).
        assert "IdleTrigger" not in xml, (
            "PREWARM-001 regression: IdleTrigger is back, prewarm will run 5+ times per session again"
        )
        # Issue 2: IdleSettings block was vestigial (no effect once
        # IdleTrigger was gone) and misleading — assert it stays gone.
        assert "IdleSettings" not in xml, (
            "Issue 2 regression: <IdleSettings> is back — it is vestigial "
            "(no IdleTrigger) and misleads readers into thinking the task "
            "still has an idle behaviour"
        )

    def test_xml_contains_hidden_and_background_settings(self):
        xml = task_scheduler._build_task_xml("C:\\path\\pythonw.exe")
        assert "<Hidden>true</Hidden>" in xml
        # ExecutionTimeLimit prevents runaway prewarms.
        assert "ExecutionTimeLimit" in xml

    def test_startup1_xml_uses_pythonw_directly_no_cmd_wrapper(self):
        """STARTUP-1: <Command> is pythonw.exe directly (NOT cmd.exe /c).

        The previous cmd.exe /c wrapper kept the cmd host alive for the
        ~10 min prewarm run, showing a ghost console window. pythonw.exe
        has no console by design, so no window appears.
        """
        xml = task_scheduler._build_task_xml("C:\\path\\pythonw.exe")
        assert "cmd.exe" not in xml, "STARTUP-1 regression: cmd.exe wrapper is back, ghost console window will reappear"
        assert "/c " not in xml
        assert "C:\\path\\pythonw.exe" in xml
        # The prewarm module flag must be in <Arguments>
        assert "-m voice_typer.server.prewarm" in xml

    def test_xml_is_valid_xml(self):
        """The generated string parses as well-formed XML."""
        import xml.etree.ElementTree as ET

        xml = task_scheduler._build_task_xml("C:\\pythonw.exe")
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
        # STARTUP-1: _prewarm_command returns just the pythonw path now
        # (the cmd.exe wrapper and -m flag were moved into _build_task_xml).
        cmd = task_scheduler._prewarm_command()
        assert cmd is not None
        assert str(venv_py.resolve()) == cmd

    def test_falls_back_to_sys_pythonw(self, monkeypatch, tmp_path):
        """No venv → pythonw.exe next to sys.executable."""
        fake_home = tmp_path
        # No venv pythonw at this path — fallback to sys.executable sibling.
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # STARTUP-1: returns just the path; on non-Windows test envs,
        # pythonw.exe doesn't exist next to sys.executable so we fall
        # through to sys.executable itself.
        cmd = task_scheduler._prewarm_command()
        assert cmd is not None
        # On Windows, pythonw.exe next to sys.executable would be returned.
        # On non-Windows test envs, sys.executable itself is the fallback.
        expected_pythonw = str(Path(sys.executable).parent / "pythonw.exe")
        if Path(expected_pythonw).exists():
            assert expected_pythonw == cmd
        else:
            # Fallback path: sys.executable
            assert sys.executable == cmd
