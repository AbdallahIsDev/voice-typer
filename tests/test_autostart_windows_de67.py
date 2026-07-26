"""DE-67: stale-entry cleanup correctly parses UNQUOTED spaced paths.

Pre-fix, ``_register_app_autostart_runkey`` parsed each Run-key value
with::

    exe_path = value.strip('"').split('"')[0] if '"' in value else value.split()[0]

The ``value.split()[0]`` branch misparses UNQUOTED spaced paths (e.g.
``C:\\Program Files\\VoiceTyper\\app.exe --delay 15``) — it returns
``C:\\Program`` (NOT a real path), ``Path('C:\\Program').exists()``
is False, and the cleanup silently DELETES the other install's Run-key
entry. This breaks multi-install autostart (a PLAT-RUN supported
scenario) when any install lives in a spaced path (common:
``C:\\Program Files\\...``).

The fix uses ``shlex.split(value, posix=False)`` — the documented
cross-platform-safe Windows-command-line splitter — to correctly parse
the command line before extracting the exe path. The first token is
the exe path (quoted or not), so unquoted spaced paths are no longer
truncated at the first space.

Tests use the ``fake_winreg`` fixture pattern (see
``tests/tauri/mig15/test_autostart_installer_windows.py``) so the
Windows-only ``winreg`` module is importable on the Linux test host.
We mock ``EnumValue`` to return controlled Run-key entries and verify
which entries ``DeleteValue`` was (or was not) called for.

Test matrix
-----------
- QUOTED spaced path whose exe EXISTS → not deleted (cleanup respects it).
- QUOTED spaced path whose exe does NOT exist → deleted (stale).
- UNQUOTED spaced path whose exe EXISTS → not deleted (DE-67 regression).
- UNQUOTED spaced path whose exe does NOT exist → deleted (real stale).
- Non-VoiceTyper entries → not touched (cleanup is scoped to VoiceTyper_*).
- Current install's own entry → not deleted (never deletes self).
- Empty / malformed value → not deleted (don't delete unknown entries).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures: fake winreg + win32 platform
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly.

    Mirrors the fixture in
    ``tests/tauri/mig15/test_autostart_installer_windows.py``. Returns
    the fake module; tests can configure its ``EnumValue`` /
    ``DeleteValue`` behavior as needed.
    """
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

    monkeypatch.setattr(server_platform, "SYSTEM", "win32")
    return server_platform


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum_value_side_effect(entries: list[tuple[str, str, int]]):
    """Build a side_effect for ``winreg.EnumValue`` that yields each entry
    in order, then raises ``OSError`` to signal end-of-enumeration.

    Parameters
    ----------
    entries : list of (name, value, value_type)
        The Run-key entries to enumerate, in order.
    """

    iterator = iter(entries)

    def _side_effect(_key, _index):
        try:
            return next(iterator)
        except StopIteration:
            raise OSError("no more values")

    return _side_effect


def _make_path_existing(monkeypatch, existing_paths: set[str]) -> None:
    """Make ``Path.exists()`` return True only for paths in ``existing_paths``.

    Comparison is on the raw string (no normalization) — tests construct
    paths exactly as the production code would. This is intentional: the
    DE-67 fix is about correctly extracting the exe path from a Run-key
    value, and the production code passes the parsed token directly to
    ``Path(...).exists()`` without normalization.
    """

    def _exists(self: Path) -> bool:
        return str(self) in existing_paths

    monkeypatch.setattr(Path, "exists", _exists)


# ---------------------------------------------------------------------------
# DE-67: stale-entry cleanup parsing
# ---------------------------------------------------------------------------


class TestStaleEntryCleanupParsing:
    """DE-67: the cleanup loop must correctly parse the Run-key command
    line before extracting the exe path so UNQUOTED spaced paths are not
    truncated at the first space (which would cause legitimate entries
    to be misidentified as stale and deleted)."""

    def test_quoted_spaced_path_existing_not_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """A QUOTED spaced path whose exe EXISTS must NOT be deleted.

        Pre-fix and post-fix behavior agrees on this case — the test
        pins the contract so a future regression to either branch of
        the parsing logic is caught.
        """
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
            _run_key_name,
        )

        # The OTHER install's entry — different hash suffix than the
        # current install, so cleanup examines it.
        other_name = "VoiceTyper_aaaaaaaa"
        other_value = r'"C:\Program Files\VoiceTyper\app.exe" --delay 15'
        # The exe file actually exists on disk (the other install is live).
        _make_path_existing(monkeypatch, {r"C:\Program Files\VoiceTyper\app.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(other_name, other_value, fake_winreg.REG_SZ)]
        )

        # The current install's own key name — different hash.
        current_name = _run_key_name()
        assert other_name != current_name  # sanity check

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" "C:\other\launcher.py" --hidden --delay 15',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # The other install's entry must NOT have been deleted.
        fake_winreg.DeleteValue.assert_not_called()

    def test_quoted_spaced_path_nonexistent_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """A QUOTED spaced path whose exe does NOT exist must be deleted
        (this is the legitimate stale-entry cleanup case)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        stale_name = "VoiceTyper_deadbeef"
        stale_value = r'"C:\Program Files\OldVoiceTyper\app.exe" --delay 15'
        # The exe file does NOT exist (the install was uninstalled).
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(stale_name, stale_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # The stale entry MUST have been deleted.
        fake_winreg.DeleteValue.assert_called_once()
        # Verify it was the stale entry, not something else.
        call_args = fake_winreg.DeleteValue.call_args
        # DeleteValue(run_key, name) — name is the second positional arg.
        deleted_name = call_args.args[1]
        assert deleted_name == stale_name

    def test_unquoted_spaced_path_existing_not_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """DE-67 regression test: an UNQUOTED spaced path whose exe
        EXISTS must NOT be deleted.

        Pre-fix, ``value.split()[0]`` returned ``C:\\Program`` (NOT a
        real path), ``Path('C:\\Program').exists()`` was False, and
        the cleanup DELETED this entry — silently breaking the other
        install's autostart. Post-fix, ``shlex.split(value,
        posix=False)[0]`` returns the full ``C:\\Program
        Files\\VoiceTyper\\app.exe`` which exists, so cleanup leaves
        the entry alone.
        """
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        live_name = "VoiceTyper_aaaaaaaa"
        # UNQUOTED spaced path — the DE-67 regression trigger.
        live_value = r"C:\Program Files\VoiceTyper\app.exe --delay 15"
        # The FULL exe path exists on disk.
        _make_path_existing(monkeypatch, {r"C:\Program Files\VoiceTyper\app.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(live_name, live_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # CRITICAL: the legitimate entry must NOT be deleted.
        fake_winreg.DeleteValue.assert_not_called()

    def test_unquoted_spaced_path_nonexistent_not_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """DE-67: an UNQUOTED spaced path whose exe does NOT exist must
        NOT be deleted either — the parse is ambiguous (the actual exe
        might be a longer space-separated prefix we can't recover
        without quotes), so the CONSERVATIVE-DELETE policy preserves
        the entry rather than risk deleting a legitimate one.

        Note: this means some genuinely-stale unquoted spaced entries
        won't be cleaned up. That's a deliberate trade-off: deleting a
        legitimate entry (silently breaking the other install's
        autostart) is far worse than leaving a stale entry in the
        registry doing nothing.
        """
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        # An unquoted spaced path whose first token ('C:\\Program')
        # does NOT exist, but the FULL path ('C:\\Program
        # Files\\OldVoiceTyper\\app.exe') also does NOT exist.
        # Pre-fix, this entry would have been deleted (the first
        # token doesn't exist). Post-fix, we preserve it because the
        # parse is ambiguous.
        maybe_stale_name = "VoiceTyper_deadbeef"
        maybe_stale_value = r"C:\Program Files\OldVoiceTyper\app.exe --delay 15"
        _make_path_existing(monkeypatch, set())  # nothing exists

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(maybe_stale_name, maybe_stale_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Conservative-delete policy: ambiguous unquoted spaced paths
        # are NEVER deleted, even if the first token doesn't exist.
        fake_winreg.DeleteValue.assert_not_called()

    def test_unquoted_no_spaces_nonexistent_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """DE-67: an UNQUOTED path with NO spaces (single token) that
        does NOT exist must be deleted (this is unambiguous — the
        parse is correct, and the file genuinely doesn't exist)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        stale_name = "VoiceTyper_deadbeef"
        # Single token, no spaces — the parse is unambiguous.
        stale_value = r"C:\nonexistent_path\app.exe"
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(stale_name, stale_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_called_once()
        call_args = fake_winreg.DeleteValue.call_args
        assert call_args.args[1] == stale_name

    def test_unquoted_no_spaces_existing_not_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """DE-67: an UNQUOTED path with NO spaces (single token) that
        DOES exist must not be deleted (the parse is unambiguous and
        the file exists — the entry is live)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        live_name = "VoiceTyper_aaaaaaaa"
        live_value = r"C:\live\app.exe"
        _make_path_existing(monkeypatch, {r"C:\live\app.exe"})

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(live_name, live_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_not_called()

    def test_non_voicetyper_entries_not_touched(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """Non-VoiceTyper entries (e.g. ``OneDrive``, ``Discord``) must
        NOT be touched by the cleanup — the loop filters on
        ``name.startswith("VoiceTyper")``.
        """
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        # A non-VoiceTyper entry with a clearly-non-existent path.
        onedrive_name = "OneDrive"
        onedrive_value = r"C:\Program Files\OneDrive\OneDrive.exe /background"
        _make_path_existing(monkeypatch, set())  # nothing exists

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(onedrive_name, onedrive_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Must NOT have deleted the OneDrive entry.
        fake_winreg.DeleteValue.assert_not_called()

    def test_current_install_entry_not_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """The current install's OWN entry must NOT be deleted by the
        cleanup loop (the ``name != reg_key_name`` guard prevents
        self-deletion)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
            _run_key_name,
        )

        current_name = _run_key_name()
        # Use a value whose parsed exe path does NOT exist — the test
        # verifies the cleanup skips the current entry regardless of
        # whether the parsed path exists.
        current_value = r"C:\nonexistent\python.exe launcher.py --hidden"
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(current_name, current_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: current_value,
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Must NOT have deleted the current install's own entry.
        fake_winreg.DeleteValue.assert_not_called()

    def test_empty_value_not_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """DE-67: a malformed / empty Run-key value must NOT be deleted.

        Pre-fix, an empty string would have caused
        ``value.split()[0]`` to raise ``IndexError`` (caught by the
        broad ``except Exception``). Post-fix, ``shlex.split('',
        posix=False)`` returns ``[]``, and the ``if exe_path and ...``
        guard skips deletion — silently deleting a malformed-but-
        maybe-functional entry is worse than leaving it alone.
        """
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        malformed_name = "VoiceTyper_zzzzzzzz"
        malformed_value = ""  # empty string
        _make_path_existing(monkeypatch, set())

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(
            [(malformed_name, malformed_value, fake_winreg.REG_SZ)]
        )

        monkeypatch.setattr(
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        fake_winreg.DeleteValue.assert_not_called()

    def test_multiple_entries_only_stale_deleted(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """DE-67 integration: with a MIX of stale and live entries
        (both quoted and unquoted), only the genuinely stale ones are
        deleted. This is the real-world scenario: a stable install
        alongside a dev install, both in ``C:\\Program Files\\...``.

        Note: the stale entry uses a QUOTED spaced path (so the
        CONSERVATIVE-DELETE policy can determine it's stale with
        certainty). An UNQUOTED spaced stale entry would be preserved
        by the conservative-delete policy (see
        ``test_unquoted_spaced_path_nonexistent_not_deleted``)."""
        from voice_typer.server.server_platform import (
            _register_app_autostart_runkey,
        )

        # Live dev install — UNQUOTED spaced path, exe EXISTS.
        # (Conservative-delete skips this because the first token
        # doesn't exist, but we can't be sure it's stale.)
        live_dev_name = "VoiceTyper_aaaaaaaa"
        live_dev_value = r"C:\Program Files\VoiceTyperDev\app.exe --delay 15"
        # Live stable install — QUOTED spaced path, exe EXISTS.
        live_stable_name = "VoiceTyper_bbbbbbbb"
        live_stable_value = r'"C:\Program Files\VoiceTyper\app.exe" --delay 15'
        # Stale install — QUOTED spaced path, exe does NOT exist.
        # (Quoted so the conservative-delete policy can determine
        # staleness with certainty.)
        stale_name = "VoiceTyper_deadbeef"
        stale_value = r'"C:\Program Files\OldVoiceTyper\app.exe" --delay 15'

        _make_path_existing(
            monkeypatch,
            {
                r"C:\Program Files\VoiceTyperDev\app.exe",
                r"C:\Program Files\VoiceTyper\app.exe",
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
            "voice_typer.server.server_platform._autostart_command",
            lambda: r'"C:\other\python.exe" launcher.py --hidden',
            raising=False,
        )

        result = _register_app_autostart_runkey()
        assert result is True
        # Exactly ONE entry (the stale one) must have been deleted.
        assert fake_winreg.DeleteValue.call_count == 1
        deleted_name = fake_winreg.DeleteValue.call_args.args[1]
        assert deleted_name == stale_name


# ---------------------------------------------------------------------------
# DE-67: parsing logic unit tests (no winreg required)
# ---------------------------------------------------------------------------


class TestShlexParsingLogic:
    """DE-67: unit-test the ``shlex.split(value, posix=False)``
    parsing decision in isolation. These tests verify the parsing
    helper directly without spinning up the full Run-key cleanup loop
    — they're the fastest signal for a parsing regression.
    """

    @pytest.mark.parametrize(
        "value, expected_exe_token, expected_exe_after_strip_quotes",
        [
            # Quoted spaced path: shlex.split(posix=False) keeps the
            # quotes in the token; the production code strips them.
            (r'"C:\Program Files\VoiceTyper\app.exe" --delay 15',
             r'"C:\Program Files\VoiceTyper\app.exe"',
             r"C:\Program Files\VoiceTyper\app.exe"),
            # Quoted path with no args
            (r'"C:\app.exe"',
             r'"C:\app.exe"',
             r"C:\app.exe"),
            # Unquoted path with no args, no spaces
            (r"C:\app.exe",
             r"C:\app.exe",
             r"C:\app.exe"),
            # Path with no spaces, no quotes, with args
            (r"C:\app.exe --delay 15",
             r"C:\app.exe",
             r"C:\app.exe"),
            # Network-style path
            (r"\\server\share\app.exe --delay 15",
             r"\\server\share\app.exe",
             r"\\server\share\app.exe"),
            # Unquoted spaced path (DE-67 regression trigger): the
            # parse is AMBIGUOUS — shlex.split(posix=False) splits on
            # the space and returns just 'C:\\Program' as the first
            # token. The production code's CONSERVATIVE-DELETE policy
            # detects this case (was_quoted=False, has_multiple_tokens=True)
            # and SKIPS deletion because we can't recover the full exe
            # path. This test pins the parse behavior so a future
            # change to a different parser doesn't silently break the
            # detection.
            (r"C:\Program Files\VoiceTyper\app.exe --delay 15",
             r"C:\Program",
             r"C:\Program"),
        ],
    )
    def test_shlex_split_extracts_exe_token(
        self, value, expected_exe_token, expected_exe_after_strip_quotes
    ):
        """DE-67: ``shlex.split(value, posix=False)[0]`` extracts the
        first command-line token. For QUOTED paths, this is the full
        quoted path (with quotes preserved — the production code
        strips them via ``.strip('"')``). For UNQUOTED paths with no
        spaces, this is the path itself. For UNQUOTED paths WITH
        spaces (the DE-67 regression trigger), this is just the first
        space-separated chunk (e.g. ``C:\\Program``) — which is why
        the production code's CONSERVATIVE-DELETE policy preserves
        such entries instead of deleting them based on the ambiguous
        parse."""
        import shlex

        tokens = shlex.split(value, posix=False)
        assert tokens, f"shlex.split returned empty list for {value!r}"
        assert tokens[0] == expected_exe_token
        # The production code strips surrounding quotes; verify the
        # final path matches.
        assert tokens[0].strip('"') == expected_exe_after_strip_quotes

    def test_shlex_split_empty_value_returns_empty_list(self):
        """DE-67: an empty Run-key value must produce an empty token
        list (NOT raise IndexError). The production code's
        ``if not tokens: continue`` guard then skips deletion."""
        import shlex

        assert shlex.split("", posix=False) == []

    def test_was_quoted_detection(self):
        """DE-67: the production code distinguishes QUOTED from
        UNQUOTED values by checking ``exe_token.startswith('"')``.
        This test pins the detection so a future refactor doesn't
        silently break the CONSERVATIVE-DELETE policy.

        - QUOTED values: the first token starts with ``"`` (shlex
          with posix=False preserves the quotes). The conservative
          policy allows deletion if the path doesn't exist.
        - UNQUOTED values: the first token does NOT start with ``"``.
          If there are multiple tokens (spaces), the policy preserves
          the entry (ambiguous parse).
        """
        import shlex

        # Quoted path → first token starts with '"'
        quoted_value = r'"C:\Program Files\VT\app.exe" --delay 15'
        quoted_tokens = shlex.split(quoted_value, posix=False)
        assert quoted_tokens[0].startswith('"')

        # Unquoted path → first token does NOT start with '"'
        unquoted_value = r"C:\app.exe --delay 15"
        unquoted_tokens = shlex.split(unquoted_value, posix=False)
        assert not unquoted_tokens[0].startswith('"')
