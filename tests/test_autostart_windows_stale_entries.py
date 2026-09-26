"""DE-67: stale-entry cleanup correctly parses UNQUOTED spaced paths."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly."""
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 0x80000001
    fake.KEY_SET_VALUE = 0x0002
    fake.KEY_READ = 0x20019
    fake.KEY_ALL_ACCESS = 0xF003F
    fake.REG_SZ = 1
    fake.OpenKey = MagicMock(return_value=MagicMock())
    fake.SetValueEx = MagicMock()
    fake.QueryValueEx = MagicMock(return_value=("cmd", 1))
    fake.DeleteValue = MagicMock()
    fake.CloseKey = MagicMock()
    # Default: no Run-key values to enumerate.
    fake.EnumValue = MagicMock(side_effect=OSError("no more values"))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


@pytest.fixture
def win32_platform(monkeypatch, fake_winreg):
    """Pretend we're on Windows for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "win32")
    from voice_typer.server import server_platform
    from voice_typer.server.server_platform import platform_flags

    monkeypatch.setattr(platform_flags, "SYSTEM", "win32")
    return server_platform


def _enum_value_side_effect(entries: list[tuple[str, str, int]]):
    """Build a side_effect for ``winreg.EnumValue`` that yields each entry"""

    iterator = iter(entries)

    def _side_effect(_key, _index):
        try:
            return next(iterator)
        except StopIteration:
            raise OSError("no more values") from None

    return _side_effect


def _make_path_existing(monkeypatch, existing_paths: set[str]) -> None:
    """Make ``Path.exists()`` return True only for paths in ``existing_paths``."""

    def _exists(self: Path) -> bool:
        return str(self) in existing_paths

    monkeypatch.setattr(Path, "exists", _exists)


class TestStaleEntryCleanupParsing:
    """DE-67: the cleanup loop must correctly parse the Run-key command"""

    def test_quoted_spaced_path_existing_not_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """A QUOTED spaced path whose exe EXISTS must NOT be deleted."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
            _run_key_name,
        )

        other_name = "Lausu_aaaaaaaa"
        other_value = r'"C:\Program Files\Lausu\app.exe" --delay 15'
        # The exe file actually exists on disk (the other install is live).
        _make_path_existing(monkeypatch, {r"C:\Program Files\Lausu\app.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(other_name, other_value, fake_winreg.REG_SZ)])

        # The current install's own key name, different hash.
        current_name = _run_key_name()
        assert other_name != current_name  # sanity check

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" "C:\other\launcher.py" --hidden --delay 15',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # The other install's entry must NOT have been deleted.
        fake_winreg.DeleteValue.assert_not_called()

    def test_quoted_spaced_path_nonexistent_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """A QUOTED spaced path whose exe does NOT exist must be deleted"""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        stale_name = "Lausu_deadbeef"
        stale_value = r'"C:\Program Files\OldLausu\app.exe" --delay 15'
        # The exe file does NOT exist (the install was uninstalled).
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(stale_name, stale_value, fake_winreg.REG_SZ)])

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # The stale entry MUST have been deleted.
        fake_winreg.DeleteValue.assert_called_once()
        # Verify it was the stale entry, not something else.
        call_args = fake_winreg.DeleteValue.call_args
        # DeleteValue(run_key, name), name is the second positional arg.
        deleted_name = call_args.args[1]
        assert deleted_name == stale_name

    def test_unquoted_spaced_path_existing_not_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """
        DE-67 regression test: an UNQUOTED spaced path whose exe
        EXISTS must NOT be deleted.
        """
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        live_name = "Lausu_aaaaaaaa"
        # UNQUOTED spaced path, the  regression trigger.
        live_value = r"C:\Program Files\Lausu\app.exe --delay 15"
        # The FULL exe path exists on disk.
        _make_path_existing(monkeypatch, {r"C:\Program Files\Lausu\app.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(live_name, live_value, fake_winreg.REG_SZ)])

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # CRITICAL: the legitimate entry must NOT be deleted.
        fake_winreg.DeleteValue.assert_not_called()

    def test_doubled_backslash_value_deleted_even_when_path_exists(self, monkeypatch, fake_winreg, win32_platform):
        """BP-128: a freedesktop-quoting-mangled value (doubled"""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        stale_name = "Lausu_deadbeef"
        stale_value = r'"C:\\Users\\11\\.lausu\\venv\\Scripts\\pythonw.exe" --hidden --delay 15'
        # The real ``Path.exists()`` collapses the doubled separators,
        _make_path_existing(monkeypatch, {r"C:\\Users\\11\\.lausu\\venv\\Scripts\\pythonw.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(stale_name, stale_value, fake_winreg.REG_SZ)])

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_called_once()
        call_args = fake_winreg.DeleteValue.call_args
        deleted_name = call_args.args[1]
        assert deleted_name == stale_name

    def test_unquoted_spaced_path_nonexistent_not_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """DE-67: an UNQUOTED spaced path whose exe does NOT exist must"""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        # An unquoted spaced path whose first token ('C:\\Program')
        maybe_stale_name = "Lausu_deadbeef"
        maybe_stale_value = r"C:\Program Files\OldLausu\app.exe --delay 15"
        _make_path_existing(monkeypatch, set())  # nothing exists

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(maybe_stale_name, maybe_stale_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Conservative-delete policy: ambiguous unquoted spaced paths
        fake_winreg.DeleteValue.assert_not_called()

    def test_unquoted_no_spaces_nonexistent_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """parse is correct, and the file genuinely doesn't exist)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        stale_name = "Lausu_deadbeef"
        # Single token, no spaces, the parse is unambiguous.
        stale_value = r"C:\nonexistent_path\app.exe"
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(stale_name, stale_value, fake_winreg.REG_SZ)])

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_called_once()
        call_args = fake_winreg.DeleteValue.call_args
        assert call_args.args[1] == stale_name

    def test_unquoted_no_spaces_existing_not_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """the file exists, the entry is live)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        live_name = "Lausu_aaaaaaaa"
        live_value = r"C:\live\app.exe"
        _make_path_existing(monkeypatch, {r"C:\live\app.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(live_name, live_value, fake_winreg.REG_SZ)])

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_not_called()

    def test_non_lausu_entries_not_touched(self, monkeypatch, fake_winreg, win32_platform):
        """Non-Lausu entries (e.g. ``OneDrive``, ``Discord``) must"""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        # A non-Lausu entry with a clearly-non-existent path.
        onedrive_name = "OneDrive"
        onedrive_value = r"C:\Program Files\OneDrive\OneDrive.exe /background"
        _make_path_existing(monkeypatch, set())  # nothing exists

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(onedrive_name, onedrive_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Must NOT have deleted the OneDrive entry.
        fake_winreg.DeleteValue.assert_not_called()

    def test_current_install_entry_not_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """cleanup loop (the ``name != reg_key_name`` guard prevents"""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
            _run_key_name,
        )

        current_name = _run_key_name()
        # Use a value whose parsed exe path does NOT exist, the test
        current_value = r"C:\nonexistent\python.exe launcher.py --hidden"
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(current_name, current_value, fake_winreg.REG_SZ)])

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: current_value,
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Must NOT have deleted the current install's own entry.
        fake_winreg.DeleteValue.assert_not_called()

    def test_empty_value_not_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """DE-67: a malformed / empty Run-key value must NOT be deleted."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        malformed_name = "Lausu_zzzzzzzz"
        malformed_value = ""  # empty string
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(malformed_name, malformed_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_not_called()

    def test_multiple_entries_only_stale_deleted(self, monkeypatch, fake_winreg, win32_platform):
        """DE-67 integration: with a MIX of stale and live entries"""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        # Live dev install. UNQUOTED spaced path, exe EXISTS.
        live_dev_name = "Lausu_aaaaaaaa"
        live_dev_value = r"C:\Program Files\LausuDev\app.exe --delay 15"
        # Live stable install. QUOTED spaced path, exe EXISTS.
        live_stable_name = "Lausu_bbbbbbbb"
        live_stable_value = r'"C:\Program Files\Lausu\app.exe" --delay 15'
        # Stale install. QUOTED spaced path, exe does NOT exist.
        stale_name = "Lausu_deadbeef"
        stale_value = r'"C:\Program Files\OldLausu\app.exe" --delay 15'

        _make_path_existing(
            monkeypatch,
            {
                r"C:\Program Files\LausuDev\app.exe",
                r"C:\Program Files\Lausu\app.exe",
            },
        )

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [
                (live_dev_name, live_dev_value, fake_winreg.REG_SZ),
                (live_stable_name, live_stable_value, fake_winreg.REG_SZ),
                (stale_name, stale_value, fake_winreg.REG_SZ),
            ]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Exactly ONE entry (the stale one) must have been deleted.
        assert fake_winreg.DeleteValue.call_count == 1
        deleted_name = fake_winreg.DeleteValue.call_args.args[1]
        assert deleted_name == stale_name


class TestShlexParsingLogic:
    """DE-67: unit-test the ``shlex.split(value, posix=False)``"""

    @pytest.mark.parametrize(
        "value, expected_exe_token, expected_exe_after_strip_quotes",
        [
            (
                r'"C:\Program Files\Lausu\app.exe" --delay 15',
                r'"C:\Program Files\Lausu\app.exe"',
                r"C:\Program Files\Lausu\app.exe",
            ),
            # Quoted path with no args
            (r'"C:\app.exe"', r'"C:\app.exe"', r"C:\app.exe"),
            # Unquoted path with no args, no spaces
            (r"C:\app.exe", r"C:\app.exe", r"C:\app.exe"),
            # Path with no spaces, no quotes, with args
            (r"C:\app.exe --delay 15", r"C:\app.exe", r"C:\app.exe"),
            # Network-style path
            (r"\\server\share\app.exe --delay 15", r"\\server\share\app.exe", r"\\server\share\app.exe"),
            (r"C:\Program Files\Lausu\app.exe --delay 15", r"C:\Program", r"C:\Program"),
        ],
    )
    def test_shlex_split_extracts_exe_token(self, value, expected_exe_token, expected_exe_after_strip_quotes):
        """first command-line token. For QUOTED paths, this is the full"""
        import shlex

        tokens = shlex.split(value, posix=False)
        assert tokens, f"shlex.split returned empty list for {value!r}"
        assert tokens[0] == expected_exe_token
        assert tokens[0].strip('"') == expected_exe_after_strip_quotes

    def test_shlex_split_empty_value_returns_empty_list(self):
        """DE-67: an empty Run-key value must produce an empty token"""
        import shlex

        assert shlex.split("", posix=False) == []

    def test_was_quoted_detection(self):
        """UNQUOTED values by checking ``exe_token.startswith('\"')``."""
        import shlex

        # Quoted path → first token starts with '"'
        quoted_value = r'"C:\Program Files\VT\app.exe" --delay 15'
        quoted_tokens = shlex.split(quoted_value, posix=False)
        assert quoted_tokens[0].startswith('"')

        # Unquoted path → first token does NOT start with '"'
        unquoted_value = r"C:\app.exe --delay 15"
        unquoted_tokens = shlex.split(unquoted_value, posix=False)
        assert not unquoted_tokens[0].startswith('"')
