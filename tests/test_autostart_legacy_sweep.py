"""AUTOSTART-LEGACY: one-time cleanup of legacy same-install autostart entries."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ``get_autostart_dir`` / ``is_autostart_enabled`` / the Task Scheduler
from voice_typer.server.server_platform import autostart as _ast


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly."""
    fake = types.ModuleType("winreg")
    object.__setattr__(fake, "HKEY_CURRENT_USER", 0x80000001)
    object.__setattr__(fake, "KEY_SET_VALUE", 0x0002)
    object.__setattr__(fake, "KEY_READ", 0x20019)
    object.__setattr__(fake, "KEY_ALL_ACCESS", 0xF003F)
    object.__setattr__(fake, "REG_SZ", 1)
    object.__setattr__(fake, "OpenKey", MagicMock(return_value=MagicMock()))
    object.__setattr__(fake, "SetValueEx", MagicMock())
    object.__setattr__(fake, "QueryValueEx", MagicMock(return_value=("cmd", 1)))
    object.__setattr__(fake, "DeleteValue", MagicMock())
    object.__setattr__(fake, "CloseKey", MagicMock())
    # Default: no Run-key values to enumerate.
    object.__setattr__(fake, "EnumValue", MagicMock(side_effect=OSError("no more values")))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


def _enum_value_side_effect(entries: list[tuple[str, str, int]]):
    """Build a side_effect for ``winreg.EnumValue`` that yields each entry"""
    iterator = iter(entries)

    def _side_effect(_key, _index):
        try:
            return next(iterator)
        except StopIteration:
            raise OSError("no more values") from None

    return _side_effect


def _this_install_command(monkeypatch) -> tuple[str, str]:
    """current repo's ``autostart_launcher.py`` embedded in the arguments)."""
    from voice_typer.server import server_platform as _pkg

    launcher = _pkg._install_identifier()
    cmd = f'"{sys.executable}" "{launcher}" --hidden --delay 15'
    return launcher, cmd


class TestRunKeyLegacySweep:
    """``sweep_legacy_autostart_entries`` must remove legacy ``VoiceTyper*``"""

    @staticmethod
    def _make_sweep_inert(monkeypatch):
        """Keep the task/bat sweeps out of run-key tests."""
        from voice_typer.server.server_platform import autostart_windows as _awindows

        monkeypatch.setattr(_awindows, "is_windows", lambda: False)
        monkeypatch.setattr(_ast, "_resolve_tauri_binary_for_autostart", lambda: None)

    def test_removes_legacy_same_install_runkey(self, tmp_path, monkeypatch, fake_winreg):
        """A legacy ``VoiceTyper_<oldhash>`` value whose command embeds this"""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        _launcher, cmd = _this_install_command(monkeypatch)
        legacy_name = "VoiceTyper_5a1b2c3d"
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(legacy_name, cmd, fake_winreg.REG_SZ)])

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is True
        assert result["removed"]["runkeys"] == [legacy_name]
        fake_winreg.DeleteValue.assert_called_once()
        # DeleteValue(run_key, name), the value name is the 2nd positional arg.
        assert fake_winreg.DeleteValue.call_args.args[1] == legacy_name

    def test_preserves_other_install_runkey(self, tmp_path, monkeypatch, fake_winreg):
        """A legacy-named value whose command points at a DIFFERENT install"""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        other_name = "VoiceTyper_99999999"
        other_value = (
            '"C:\\OtherInstall\\pythonw.exe" '
            '"C:\\OtherInstall\\voice_typer\\server\\autostart_launcher.py" '
            "--hidden --delay 15"
        )
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(other_name, other_value, fake_winreg.REG_SZ)])

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["removed"]["runkeys"] == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_preserves_current_install_runkey(self, tmp_path, monkeypatch, fake_winreg):
        """The current install's own entry (``_run_key_name()``) is never"""
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        _launcher, cmd = _this_install_command(monkeypatch)
        current_name = _pkg._run_key_name()
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(current_name, cmd, fake_winreg.REG_SZ)])

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["removed"]["runkeys"] == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_preserves_non_voicetyper_and_empty_values(self, tmp_path, monkeypatch, fake_winreg):
        """Non-``VoiceTyper`` entries and empty/malformed values are never"""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [
                ("OneDrive", "C:\\Users\\me\\OneDrive.exe", fake_winreg.REG_SZ),
                ("VoiceTyper_ab12cd34", "", fake_winreg.REG_SZ),
                ("VoiceTyper_ef56ab78", "not-a-command", fake_winreg.REG_SZ),
            ]
        )

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["removed"]["runkeys"] == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_sweep_is_one_time_marker_gated(self, tmp_path, monkeypatch, fake_winreg):
        """second call is a no-op, the expensive task enumeration is paid"""
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        _launcher, cmd = _this_install_command(monkeypatch)
        legacy_name = "VoiceTyper_5a1b2c3d"
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(legacy_name, cmd, fake_winreg.REG_SZ)])

        first = sweep_legacy_autostart_entries(tmp_path)
        assert first["swept"] is True
        assert first["removed"]["runkeys"] == [legacy_name]

        marker = tmp_path / f"autostart-sweep-v2-{_pkg._install_hash()}.done"
        assert marker.exists(), "per-install sweep marker must be written after the sweep"

        fake_winreg.DeleteValue.reset_mock()
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(legacy_name, cmd, fake_winreg.REG_SZ)])
        second = sweep_legacy_autostart_entries(tmp_path)
        assert second["swept"] is False
        fake_winreg.DeleteValue.assert_not_called()

    def test_sweep_is_inert_when_winreg_unavailable(self, tmp_path, monkeypatch):
        """Without winreg (non-Windows CI, or the conftest block), the sweep"""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        # No fake_winreg injected → conftest's sys.modules["winreg"] = None
        result = sweep_legacy_autostart_entries(tmp_path)
        assert result["swept"] is False
        assert not list(tmp_path.glob("autostart-sweep-*.done"))

    def test_removes_v1_marker_files_even_when_v2_marker_exists(self, tmp_path, monkeypatch, fake_winreg):
        """Leftover v1 sweep markers (``autostart-sweep-<hash>.done``) are"""
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        v1_a = tmp_path / "autostart-sweep-3c3067b1.done"
        v1_b = tmp_path / "autostart-sweep-9ce2ff97.done"
        v1_a.write_text("", encoding="utf-8")
        v1_b.write_text("", encoding="utf-8")
        v2_marker = tmp_path / f"autostart-sweep-v2-{_pkg._install_hash()}.done"
        v2_marker.write_text("", encoding="utf-8")

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is False
        assert not v1_a.exists(), "v1 marker must be cleaned even when the v2 marker exists"
        assert not v1_b.exists(), "v1 marker must be cleaned even when the v2 marker exists"
        assert v2_marker.exists(), "the v2 marker itself must never be deleted"
        assert list(tmp_path.glob("autostart-sweep-*.done")) == [v2_marker]

    def test_removes_v1_marker_files_without_winreg(self, tmp_path):
        """The v1-marker cleanup is pure filesystem work, it runs even"""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        v1 = tmp_path / "autostart-sweep-e36cc7e3.done"
        v1.write_text("", encoding="utf-8")

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is False
        assert not v1.exists(), "v1 marker must be cleaned without winreg"
        assert not list(tmp_path.glob("autostart-sweep-*.done"))


class TestTaskSweep:
    """``_sweep_legacy_tasks`` must remove legacy ``VoiceTyperAutostart*``"""

    def _install_task_fakes(self, monkeypatch, xml_for_task):
        """Stub the platform + scheduler + PowerShell surfaces so the unit"""
        from voice_typer.server import task_scheduler as _ts
        from voice_typer.server.server_platform import autostart_windows as _awindows

        monkeypatch.setattr(_awindows, "is_windows", lambda: True)
        monkeypatch.setattr(_ts, "is_supported", lambda: True)

        delete_calls: list[str] = []

        def _fake_schtasks(args, capture=False):
            if args[0] == "/Query":
                return 0, xml_for_task(args[2])
            if args[0] == "/Delete":
                delete_calls.append(args[2])
                return 0, ""
            return 1, ""

        monkeypatch.setattr(_ts, "_schtasks", _fake_schtasks)

        fake_run = MagicMock()
        fake_run.returncode = 0
        monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_run)
        return delete_calls

    @staticmethod
    def _task_xml(command: str, arguments: str) -> str:
        return (
            "<Task><Actions><Exec>"
            f"<Command>{command}</Command>"
            f"<Arguments>{arguments}</Arguments>"
            "</Exec></Actions></Task>"
        )

    def test_removes_same_install_legacy_tasks(self, monkeypatch):
        """Legacy tasks whose <Arguments> embed this install's launcher are"""
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_tasks

        launcher = _pkg._install_identifier()
        current_name = _ast._APP_AUTOSTART_TASK_NAME

        def _xml_for(name):
            if name == current_name:
                return self._task_xml("C:\\FakeOld\\pythonw.exe", f'"{launcher}" --hidden --delay 15')
            return self._task_xml("C:\\FakeOld\\pythonw.exe", f'"{launcher}" --hidden --delay 15')

        delete_calls = self._install_task_fakes(monkeypatch, _xml_for)
        fake_run = MagicMock()
        fake_run.returncode = 0
        fake_run.stdout = f"VoiceTyperAutostart_deadbeef\nVoiceTyperAutostart_cafebabe\n{current_name}\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_run)

        deleted = _sweep_legacy_tasks()

        assert deleted == ["VoiceTyperAutostart_deadbeef", "VoiceTyperAutostart_cafebabe"]
        assert deleted is not None  # type narrowing for pyrefly
        assert current_name not in deleted
        assert delete_calls == ["VoiceTyperAutostart_deadbeef", "VoiceTyperAutostart_cafebabe"]

    def test_preserves_other_install_tasks(self, monkeypatch):
        """Tasks whose command points at a DIFFERENT install are left alone."""
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_tasks

        other_xml = self._task_xml(
            "C:\\OtherInstall\\pythonw.exe",
            '"C:\\OtherInstall\\autostart_launcher.py" --hidden --delay 15',
        )
        delete_calls = self._install_task_fakes(monkeypatch, lambda _name: other_xml)
        fake_run = MagicMock()
        fake_run.returncode = 0
        fake_run.stdout = "VoiceTyperAutostart_deadbeef\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_run)

        deleted = _sweep_legacy_tasks()

        assert deleted == []
        assert delete_calls == []

    def test_task_sweep_returns_none_when_powershell_fails(self, monkeypatch):
        """If the PowerShell enumeration fails (non-zero rc), the sweep"""
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_tasks

        delete_calls = self._install_task_fakes(monkeypatch, lambda _name: "")
        fake_run = MagicMock()
        fake_run.returncode = 1  # PowerShell enumeration failed
        fake_run.stdout = ""
        monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_run)

        assert _sweep_legacy_tasks() is None
        assert delete_calls == []

    def test_sweep_skips_marker_when_task_enumeration_fails(self, tmp_path, monkeypatch, fake_winreg):
        """The completion marker is NOT written when the task enumeration"""
        from voice_typer.server import task_scheduler as _ts
        from voice_typer.server.server_platform import autostart_windows as _awindows, sweep_legacy_autostart_entries

        monkeypatch.setattr(_awindows, "is_windows", lambda: True)
        monkeypatch.setattr(_ast, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(_ast, "_resolve_tauri_binary_for_autostart", lambda: None)
        monkeypatch.setattr(_ts, "is_supported", lambda: True)
        fake_run = MagicMock()
        fake_run.returncode = 1
        fake_run.stdout = ""
        monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_run)

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is False
        assert not list(tmp_path.glob("autostart-sweep-*.done")), (
            "marker must NOT be written when the task enumeration failed"
        )


class TestStartupBatSweep:
    """``_sweep_legacy_startup_bats`` must remove legacy ``VoiceTyper*.bat``"""

    def _install_bat_fakes(self, monkeypatch, autostart_dir: Path):
        from voice_typer.server.server_platform import autostart_windows as _awindows

        monkeypatch.setattr(_awindows, "is_windows", lambda: True)
        monkeypatch.setattr(_ast, "get_autostart_dir", lambda: autostart_dir)
        monkeypatch.setattr(_ast, "_resolve_tauri_binary_for_autostart", lambda: None)
        return _awindows

    def test_removes_same_install_legacy_bat(self, tmp_path, monkeypatch):
        """A legacy ``VoiceTyper_<oldhash>.bat`` whose content embeds this"""
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_startup_bats

        _awin = self._install_bat_fakes(monkeypatch, tmp_path)
        launcher = _ast._install_identifier()

        legacy = tmp_path / "VoiceTyper_deadbeef.bat"
        legacy_cmd = f'start "" /B "{sys.executable}" "{launcher}" --hidden --delay 15'
        legacy.write_text(
            f"@echo off\r\nset VT_START_HIDDEN=1\r\n{legacy_cmd}\r\n",
            encoding="utf-8",
        )
        current = tmp_path / _awin._startup_bat_name()
        current.write_text(
            f'@echo off\r\nset VT_START_HIDDEN=1\r\nstart "" /B "{launcher}" --hidden --delay 15\r\n',
            encoding="utf-8",
        )
        other = tmp_path / "VoiceTyper_99999999.bat"
        other.write_text(
            '@echo off\r\nset VT_START_HIDDEN=1\r\nstart "" /B "C:\\OtherInstall\\app.exe" --hidden\r\n',
            encoding="utf-8",
        )

        deleted = _sweep_legacy_startup_bats()

        assert deleted == [legacy.name]
        assert not legacy.exists(), "legacy same-install .bat must be deleted"
        assert current.exists(), "current .bat must be preserved"
        assert other.exists(), "other install's .bat must be preserved"


class TestSyncAutostartHook:
    """``sync_autostart`` invokes the marker-gated sweep on every startup."""

    def test_sync_autostart_invokes_legacy_sweep(self, tmp_config_dir, monkeypatch):
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.startup_tasks import sync_autostart

        captured: dict = {}

        def _fake_sweep(config_dir):
            captured["config_dir"] = str(config_dir)
            return {"swept": True, "removed": {"runkeys": ["VoiceTyper_old"], "tasks": [], "bats": []}}

        monkeypatch.setattr(_pkg, "sweep_legacy_autostart_entries", _fake_sweep)
        monkeypatch.setattr(_ast, "is_autostart_enabled", lambda: True)

        app = MagicMock()
        app.config.autostart = True
        result = sync_autostart(app)

        assert captured.get("config_dir") == str(tmp_config_dir), (
            "sync_autostart must pass the (test-isolated) config dir to the sweep"
        )
        assert result["registered"] is True
