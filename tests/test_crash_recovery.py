"""Tests for voice_typer.crash_recovery — CrashRecovery add, save, clear, check."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def cr(recovery_dir):
    """Create a CrashRecovery instance with temp dir."""
    from voice_typer.crash_recovery import CrashRecovery
    return CrashRecovery(config_dir=recovery_dir)


class TestCrashRecoveryAdd:
    def test_add_entry(self, cr):
        cr.add("Hello world", pasted=False)
        assert cr.count == 1

    def test_add_multiple_entries(self, cr):
        cr.add("First", pasted=True)
        cr.add("Second", pasted=False)
        assert cr.count == 2

    def test_max_10_entries(self, cr):
        for i in range(15):
            cr.add(f"Entry {i}", pasted=False)
        assert cr.count == 10


class TestCrashRecoveryMarkPasted:
    def test_mark_latest_pasted(self, cr):
        cr.add("Hello", pasted=False)
        cr.mark_latest_pasted()
        entries = cr.get_all()
        assert entries[-1]["pasted"] is True

    def test_mark_pasted_by_index(self, cr):
        cr.add("First", pasted=False)
        cr.add("Second", pasted=False)
        assert cr.mark_pasted(0) is True
        entries = cr.get_all()
        assert entries[0]["pasted"] is True

    def test_mark_pasted_invalid_index(self, cr):
        assert cr.mark_pasted(99) is False


class TestCrashRecoveryUnpasted:
    def test_get_unpasted(self, cr):
        cr.add("Pasted", pasted=True)
        cr.add("Unpasted", pasted=False)
        unpasted = cr.get_unpasted()
        assert len(unpasted) == 1
        assert "Unpasted" in unpasted[0]["text"]


class TestCrashRecoveryCheckOnStartup:
    def test_check_returns_unpasted(self, cr):
        cr.add("Lost text", pasted=False)
        result = cr.check_on_startup()
        assert result is not None
        assert len(result) == 1

    def test_check_returns_none_when_all_pasted(self, cr):
        cr.add("Saved", pasted=True)
        result = cr.check_on_startup()
        assert result is None


class TestCrashRecoveryClear:
    def test_clear(self, cr):
        cr.add("Entry 1")
        cr.add("Entry 2")
        cr.clear()
        assert cr.count == 0


class TestCrashRecoveryPersistence:
    def test_persists_to_disk(self, recovery_dir):
        from voice_typer.crash_recovery import CrashRecovery
        cr1 = CrashRecovery(config_dir=recovery_dir)
        cr1.add("Persistent entry", pasted=False)
        del cr1

        cr2 = CrashRecovery(config_dir=recovery_dir)
        assert cr2.count == 1
        assert cr2.get_all()[0]["text"] == "Persistent entry"

    def test_empty_recovery_file(self, recovery_dir):
        from voice_typer.crash_recovery import CrashRecovery
        # Write an empty recovery file
        path = recovery_dir / "voice-typer-recovery.json"
        path.write_text('{"entries": []}', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
