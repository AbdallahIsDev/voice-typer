"""AUTOSTART-LEGACY: one-time cleanup of legacy same-install autostart entries.

PLAT-RUN renamed the Windows autostart entries from fixed strings (and
later from a ``sys.executable``-derived hash) to a stable install-path
hash. Upgraded installs can therefore carry legacy ``VoiceTyper*`` Run-key
values, ``VoiceTyperAutostart*`` scheduled tasks, and ``VoiceTyper*.bat``
Startup-folder files that ALL point at the same install and ALL fire at
logon — duplicate autostart.

``sweep_legacy_autostart_entries`` removes those once per install
(marker-gated, so steady-state startup cost is a single ``Path.exists()``
check) while preserving:

  - the current install's own entry (``_run_key_name`` /
    ``_APP_AUTOSTART_TASK_NAME`` / ``_startup_bat_name``), and
  - other installs' entries (different launcher path → different
    install → preserved — PLAT-RUN multi-install support).

Tests use the ``fake_winreg`` fixture pattern (see
``tests/test_autostart_windows_stale_entries.py``) so the Windows-only
``winreg`` module is importable on non-Windows hosts. ``conftest.py``
blocks the REAL ``winreg`` module globally, so run-key sweep tests inject
the fake and additionally stub ``server_platform.is_windows`` to False —
on a real Windows dev host ``is_windows()`` is True and
``task_scheduler.is_supported()`` is True, which would otherwise spawn
real PowerShell and touch the real Task Scheduler / Startup folder during
pytest. The task/bat sweep unit tests stub their own subprocess /
scheduler / startup-dir surfaces.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures: fake winreg
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly.

    Mirrors the fixture in
    ``tests/test_autostart_windows_stale_entries.py``. Returns the fake
    module; tests can configure its ``EnumValue`` / ``DeleteValue``
    behavior as needed.
    """
    fake = types.ModuleType("winreg")
    # ``object.__setattr__`` (not plain assignment) so pyrefly, which
    # rejects unknown attributes on ``ModuleType``, accepts the fake;
    # ruff's B010 flags ``setattr`` with a constant value, so the
    # ``object.__setattr__`` form is the one that satisfies both.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum_value_side_effect(entries: list[tuple[str, str, int]]):
    """Build a side_effect for ``winreg.EnumValue`` that yields each entry
    in order, then raises ``OSError`` to signal end-of-enumeration.
    """
    iterator = iter(entries)

    def _side_effect(_key, _index):
        try:
            return next(iterator)
        except StopIteration:
            raise OSError("no more values") from None

    return _side_effect


def _this_install_command(monkeypatch) -> tuple[str, str]:
    """Return ``(launcher_path, command_line)`` where the command line is a
    realistic legacy autostart command that targets THIS install (the
    current repo's ``autostart_launcher.py`` embedded in the arguments).
    """
    from voice_typer.server import server_platform as _pkg

    launcher = _pkg._install_identifier()
    cmd = f'"{sys.executable}" "{launcher}" --hidden --delay 15'
    return launcher, cmd


# ---------------------------------------------------------------------------
# Run-key legacy sweep (orchestrator-level)
# ---------------------------------------------------------------------------


class TestRunKeyLegacySweep:
    """``sweep_legacy_autostart_entries`` must remove legacy ``VoiceTyper*``
    Run-key values that point at THIS install while preserving the current
    entry and other installs' entries.
    """

    @staticmethod
    def _make_sweep_inert(monkeypatch):
        """Keep the task/bat sweeps out of run-key tests.

        On a real Windows dev host ``server_platform.is_windows()`` is
        True and ``task_scheduler.is_supported()`` is True, so the
        orchestrator would spawn real PowerShell + enumerate the real
        Startup folder during pytest. Stub them so only the (fake-winreg)
        run-key sweep runs.
        """
        from voice_typer.server import server_platform as _pkg

        monkeypatch.setattr(_pkg, "is_windows", lambda: False)
        monkeypatch.setattr(_pkg, "_resolve_tauri_binary_for_autostart", lambda: None)

    def test_removes_legacy_same_install_runkey(self, tmp_path, monkeypatch, fake_winreg):
        """A legacy ``VoiceTyper_<oldhash>`` value whose command embeds this
        install's launcher is removed (duplicate autostart cleanup)."""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        _launcher, cmd = _this_install_command(monkeypatch)
        legacy_name = "VoiceTyper_5a1b2c3d"
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(legacy_name, cmd, fake_winreg.REG_SZ)])

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is True
        assert result["removed"]["runkeys"] == [legacy_name]
        fake_winreg.DeleteValue.assert_called_once()
        # DeleteValue(run_key, name) — the value name is the 2nd positional arg.
        assert fake_winreg.DeleteValue.call_args.args[1] == legacy_name

    def test_preserves_other_install_runkey(self, tmp_path, monkeypatch, fake_winreg):
        """A legacy-named value whose command points at a DIFFERENT install
        (different launcher, different exe) must NOT be deleted — the sweep
        is scoped to this install (multi-install support)."""
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
        """The current install's own entry (``_run_key_name()``) is never
        deleted even though its command targets this install."""
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
        """Non-``VoiceTyper`` entries and empty/malformed values are never
        deleted (the sweep is scoped to ``VoiceTyper*`` names and only acts
        when it can positively identify the target install)."""
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
        """After the first sweep, a per-install marker file is written and a
        second call is a no-op — the expensive task enumeration is paid
        once, and steady-state startup cost is a single ``Path.exists()``."""
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

        # Re-arm EnumValue so a buggy second sweep WOULD see entries — the
        # marker must still short-circuit it.
        fake_winreg.DeleteValue.reset_mock()
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(legacy_name, cmd, fake_winreg.REG_SZ)])
        second = sweep_legacy_autostart_entries(tmp_path)
        assert second["swept"] is False
        fake_winreg.DeleteValue.assert_not_called()

    def test_sweep_is_inert_when_winreg_unavailable(self, tmp_path, monkeypatch):
        """Without winreg (non-Windows CI, or the conftest block), the sweep
        is a no-op and does NOT write the marker — so a real Windows run
        later still sweeps."""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        # No fake_winreg injected → conftest's sys.modules["winreg"] = None
        # makes the probe raise ImportError.
        result = sweep_legacy_autostart_entries(tmp_path)
        assert result["swept"] is False
        assert not list(tmp_path.glob("autostart-sweep-*.done"))

    def test_removes_v1_marker_files_even_when_v2_marker_exists(self, tmp_path, monkeypatch, fake_winreg):
        """Leftover v1 sweep markers (``autostart-sweep-<hash>.done``) are
        deleted on every call — even when the v2 marker already exists
        (a pre-fix install where the sweep short-circuits) — while the v2
        marker itself and the winreg gate are untouched."""
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        self._make_sweep_inert(monkeypatch)
        v1_a = tmp_path / "autostart-sweep-3c3067b1.done"
        v1_b = tmp_path / "autostart-sweep-9ce2ff97.done"
        v1_a.write_text("", encoding="utf-8")
        v1_b.write_text("", encoding="utf-8")
        v2_marker = tmp_path / f"autostart-sweep-v2-{_pkg._install_hash()}.done"
        v2_marker.write_text("", encoding="utf-8")

        # v2 marker exists → the registry/task sweep short-circuits, but
        # the v1-marker cleanup must still run (pure filesystem work).
        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is False
        assert not v1_a.exists(), "v1 marker must be cleaned even when the v2 marker exists"
        assert not v1_b.exists(), "v1 marker must be cleaned even when the v2 marker exists"
        assert v2_marker.exists(), "the v2 marker itself must never be deleted"
        assert list(tmp_path.glob("autostart-sweep-*.done")) == [v2_marker]

    def test_removes_v1_marker_files_without_winreg(self, tmp_path):
        """The v1-marker cleanup is pure filesystem work — it runs even
        when winreg is unavailable (non-Windows host / conftest block)."""
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        v1 = tmp_path / "autostart-sweep-e36cc7e3.done"
        v1.write_text("", encoding="utf-8")

        result = sweep_legacy_autostart_entries(tmp_path)

        assert result["swept"] is False
        assert not v1.exists(), "v1 marker must be cleaned without winreg"
        assert not list(tmp_path.glob("autostart-sweep-*.done"))


# ---------------------------------------------------------------------------
# Task Scheduler legacy sweep (unit-level)
# ---------------------------------------------------------------------------


class TestTaskSweep:
    """``_sweep_legacy_tasks`` must remove legacy ``VoiceTyperAutostart*``
    tasks whose command targets this install, while preserving other
    installs' tasks and the current task name."""

    def _install_task_fakes(self, monkeypatch, xml_for_task):
        """Stub the platform + scheduler + PowerShell surfaces so the unit
        test never touches the real Task Scheduler / subprocess."""
        from voice_typer.server import server_platform as _pkg, task_scheduler as _ts

        monkeypatch.setattr(_pkg, "is_windows", lambda: True)
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
        """Legacy tasks whose <Arguments> embed this install's launcher are
        deleted; the CURRENT task name is never touched."""
        from voice_typer.server import server_platform as _pkg
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_tasks

        launcher = _pkg._install_identifier()
        current_name = _pkg._APP_AUTOSTART_TASK_NAME

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
        """If the PowerShell enumeration fails (non-zero rc), the sweep
        returns ``None`` so the marker-gated orchestrator retries on the
        next startup instead of permanently skipping the task portion."""
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_tasks

        delete_calls = self._install_task_fakes(monkeypatch, lambda _name: "")
        fake_run = MagicMock()
        fake_run.returncode = 1  # PowerShell enumeration failed
        fake_run.stdout = ""
        monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_run)

        assert _sweep_legacy_tasks() is None
        assert delete_calls == []

    def test_sweep_skips_marker_when_task_enumeration_fails(self, tmp_path, monkeypatch, fake_winreg):
        """The completion marker is NOT written when the task enumeration
        fails — the sweep is retried on the next startup (a transient
        PowerShell failure can't permanently skip the task cleanup)."""
        from voice_typer.server import server_platform as _pkg, task_scheduler as _ts
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        # Fake winreg makes the orchestrator's probe pass; simulate a
        # Windows host so the task sweep actually attempts to run.
        monkeypatch.setattr(_pkg, "is_windows", lambda: True)
        monkeypatch.setattr(_pkg, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(_pkg, "_resolve_tauri_binary_for_autostart", lambda: None)
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


# ---------------------------------------------------------------------------
# Startup-folder .bat legacy sweep (unit-level)
# ---------------------------------------------------------------------------


class TestStartupBatSweep:
    """``_sweep_legacy_startup_bats`` must remove legacy ``VoiceTyper*.bat``
    files whose content targets this install, preserving the current file
    and other installs' files."""

    def _install_bat_fakes(self, monkeypatch, autostart_dir: Path):
        from voice_typer.server import server_platform as _pkg

        monkeypatch.setattr(_pkg, "is_windows", lambda: True)
        monkeypatch.setattr(_pkg, "get_autostart_dir", lambda: autostart_dir)
        monkeypatch.setattr(_pkg, "_resolve_tauri_binary_for_autostart", lambda: None)
        return _pkg

    def test_removes_same_install_legacy_bat(self, tmp_path, monkeypatch):
        """A legacy ``VoiceTyper_<oldhash>.bat`` whose content embeds this
        install's launcher is deleted; the current .bat and other installs'
        .bat files survive."""
        from voice_typer.server.server_platform.autostart_windows import _sweep_legacy_startup_bats

        _pkg = self._install_bat_fakes(monkeypatch, tmp_path)
        launcher = _pkg._install_identifier()

        legacy = tmp_path / "VoiceTyper_deadbeef.bat"
        legacy_cmd = f'start "" /B "{sys.executable}" "{launcher}" --hidden --delay 15'
        legacy.write_text(
            f"@echo off\r\nset VT_START_HIDDEN=1\r\n{legacy_cmd}\r\n",
            encoding="utf-8",
        )
        current = tmp_path / _pkg._startup_bat_name()
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


# ---------------------------------------------------------------------------
# sync_autostart hook
# ---------------------------------------------------------------------------


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
        monkeypatch.setattr(_pkg, "is_autostart_enabled", lambda: True)

        app = MagicMock()
        app.config.autostart = True
        result = sync_autostart(app)

        assert captured.get("config_dir") == str(tmp_config_dir), (
            "sync_autostart must pass the (test-isolated) config dir to the sweep"
        )
        # autostart was already in sync (config True, OS True) → no re-enable.
        assert result["registered"] is True
