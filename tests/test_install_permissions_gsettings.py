"""Tests for the Caps Lock option-merge logic in
``scripts/linux/install_permissions.py`` .

Background: prior to /19/20, ``install_permissions.py`` clobbered
the user's existing XKB options when enabling ``caps:none`` — e.g. a
GNOME user with ``['altwin:swap_alt_win']`` would have that option
silently dropped, replaced with just ``['caps:none']``. The fix reads
the existing value via ``gsettings get`` / ``configparser`` / sway-config
scan, merges ``caps:none`` in (deduped, order-preserving), and captures
the original in the manifest so the uninstaller can restore it via
``gsettings set`` / kxkbrc rewrite / sway config rewrite (instead of
``gsettings reset``, which would lose user customization).

These tests exercise:

1. Pure unit tests for the option-merge helpers
   (``_parse_gsettings_array``, ``_format_gsettings_array``,
   ``_parse_comma_options``, ``_format_comma_options``,
   ``_merge_option``, ``_find_sway_xkb_options_lines``).
2. GNOME flow : mocked ``gsettings get`` returns an existing
   options array; ``configure_caps_lock_neutralization('gnome', ...)``
   must call ``gsettings set`` with the MERGED value, and capture the
   original raw string in ``result['gnome_xkb_options_original']``.
3. KDE flow : a real kxkbrc file in tmp_path with an existing
   ``Options=altwin:swap_alt_win`` line; the function must merge
   ``caps:none`` in via configparser and capture the original.
4. Sway flow : a real sway config file with an existing
   ``input * xkb_options altwin:swap_alt_win`` line; the function must
   merge ``caps:none`` in, leave a restore-marker comment, and capture
   the original line.
5. Uninstall restore (/19/20): given a manifest with the captured
   originals, ``_restore_gnome_xkb_options`` / ``_restore_kde_kxkbrc_options``
   / ``_restore_sway_config_options`` must restore the user's prior state.
6. ``install()`` must ``fail(5, ...)`` when
   ``get_target_user()`` returns None (instead of silently continuing
   with ``username = "root"``).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SCRIPT = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"


def _load_install_permissions_module():
    """Load ``scripts/linux/install_permissions.py`` as an isolated module."""
    spec = importlib.util.spec_from_file_location("install_permissions_gsettings_under_test", _INSTALL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ip_module():
    """Load the install_permissions.py module once per test module run."""
    if not _INSTALL_SCRIPT.is_file():
        pytest.skip("install_permissions.py not found (not a Linux build)")
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux-only test (script uses grp / pwd modules)")
    return _load_install_permissions_module()


# ─── Helper unit tests ─────────────────────────────────────────────────────


class TestParseGsettingsArray:
    """``_parse_gsettings_array`` correctly parses GVariant-style array literals."""

    def test_empty_array_literal(self, ip_module):
        """``@as []`` (GVariant empty array of type ``as``) → empty list."""
        assert ip_module._parse_gsettings_array("@as []") == []

    def test_empty_string(self, ip_module):
        """Empty input → empty list."""
        assert ip_module._parse_gsettings_array("") == []

    def test_single_option(self, ip_module):
        """``['caps:none']`` → ['caps:none']."""
        assert ip_module._parse_gsettings_array("['caps:none']") == ["caps:none"]

    def test_multiple_options(self, ip_module):
        """Multiple options preserved in order."""
        result = ip_module._parse_gsettings_array("['altwin:swap_alt_win', 'caps:none']")
        assert result == ["altwin:swap_alt_win", "caps:none"]

    def test_whitespace_only(self, ip_module):
        """Whitespace-only input → empty list."""
        assert ip_module._parse_gsettings_array("   ") == []


class TestFormatGsettingsArray:
    """``_format_gsettings_array`` produces a GVariant-compatible literal."""

    def test_empty_list(self, ip_module):
        """Empty list → ``[]``."""
        assert ip_module._format_gsettings_array([]) == "[]"

    def test_single_item(self, ip_module):
        assert ip_module._format_gsettings_array(["caps:none"]) == "['caps:none']"

    def test_multiple_items(self, ip_module):
        result = ip_module._format_gsettings_array(["altwin:swap_alt_win", "caps:none"])
        assert result == "['altwin:swap_alt_win', 'caps:none']"


class TestParseCommaOptions:
    """``_parse_comma_options`` parses comma-separated XKB option strings."""

    def test_empty(self, ip_module):
        assert ip_module._parse_comma_options("") == []

    def test_single(self, ip_module):
        assert ip_module._parse_comma_options("caps:none") == ["caps:none"]

    def test_multiple(self, ip_module):
        assert ip_module._parse_comma_options("caps:none,altwin:swap_alt_win") == [
            "caps:none",
            "altwin:swap_alt_win",
        ]

    def test_whitespace_stripped(self, ip_module):
        assert ip_module._parse_comma_options(" caps:none , altwin:swap_alt_win ") == [
            "caps:none",
            "altwin:swap_alt_win",
        ]


class TestFormatCommaOptions:
    """``_format_comma_options`` produces a comma-separated string."""

    def test_empty(self, ip_module):
        assert ip_module._format_comma_options([]) == ""

    def test_single(self, ip_module):
        assert ip_module._format_comma_options(["caps:none"]) == "caps:none"

    def test_multiple(self, ip_module):
        assert ip_module._format_comma_options(["caps:none", "altwin:swap_alt_win"]) == "caps:none,altwin:swap_alt_win"


class TestMergeOption:
    """``_merge_option`` is deduped and order-preserving."""

    def test_appends_when_absent(self, ip_module):
        assert ip_module._merge_option(["altwin:swap_alt_win"], "caps:none") == [
            "altwin:swap_alt_win",
            "caps:none",
        ]

    def test_dedupes_when_present(self, ip_module):
        """If ``new`` is already in ``existing``, the list is unchanged."""
        assert ip_module._merge_option(["caps:none"], "caps:none") == ["caps:none"]

    def test_preserves_order(self, ip_module):
        existing = ["a", "b", "c"]
        assert ip_module._merge_option(existing, "d") == ["a", "b", "c", "d"]

    def test_does_not_mutate_input(self, ip_module):
        existing = ["a"]
        ip_module._merge_option(existing, "b")
        assert existing == ["a"], "merge must not mutate the input list"


class TestFindSwayXkbOptionsLines:
    """``_find_sway_xkb_options_lines`` locates ``input * xkb_options`` directives."""

    def test_no_match(self, ip_module):
        lines = ["# comment\n", "set $mod Mod4\n", "bindsym Mod4+Return exec foot\n"]
        assert ip_module._find_sway_xkb_options_lines(lines) == []

    def test_single_match(self, ip_module):
        lines = [
            "set $mod Mod4\n",
            "input * xkb_options caps:none\n",
            "bindsym Mod4+Return exec foot\n",
        ]
        assert ip_module._find_sway_xkb_options_lines(lines) == [1]

    def test_multiple_matches(self, ip_module):
        lines = [
            "input * xkb_options caps:none\n",
            "input * xkb_options altwin:swap_alt_win\n",
        ]
        assert ip_module._find_sway_xkb_options_lines(lines) == [0, 1]

    def test_skips_comments(self, ip_module):
        """Commented-out ``input * xkb_options`` lines are NOT matched."""
        lines = ["# input * xkb_options caps:none\n"]
        assert ip_module._find_sway_xkb_options_lines(lines) == []

    def test_skips_unrelated_input_directives(self, ip_module):
        """``input type:keyboard ...`` (without ``*``) is NOT matched."""
        lines = ["input type:keyboard xkb_options caps:none\n"]
        assert ip_module._find_sway_xkb_options_lines(lines) == []


# ─── GNOME flow  ─────────────────────────────────────────────────────


class TestGnomeFlow:
    """``configure_caps_lock_neutralization('gnome', ...)`` merges existing options."""

    def test_merges_into_existing_options(self, ip_module, monkeypatch):
        """When ``gsettings get`` returns existing options, ``gsettings set``
        receives the MERGED array (original + caps:none)."""
        # Arrange: fake ``gsettings get`` returns an existing option, fake
        # ``gsettings set`` captures the value passed.
        captured_set_calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            # Return a fake CompletedProcess for ``gsettings get``; the
            # module's GNOME branch uses subprocess.run directly (not the
            # module's ``run`` helper) for the get call.
            if "get" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="['altwin:swap_alt_win']",
                    stderr="",
                )
            # The ``gsettings set`` call goes through the module's ``run``
            # helper — capture it.
            captured_set_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        # The GNOME branch calls ``subprocess.run`` directly for the get,
        # and ``run(...)`` for the set. Patch both.
        monkeypatch.setattr(ip_module.subprocess, "run", fake_run)
        monkeypatch.setattr(ip_module, "run", fake_run)

        # Act
        result = ip_module.configure_caps_lock_neutralization("gnome", "alice")

        # Assert: result tracks modification + captures original.
        assert result["gnome_settings_modified"] is True
        assert result["gnome_xkb_options_original"] == "['altwin:swap_alt_win']"

        # Assert: ``gsettings set`` was called with the MERGED value
        # (existing + caps:none, deduped, order-preserving).
        set_calls_with_set = [c for c in captured_set_calls if "set" in c]
        assert len(set_calls_with_set) == 1, f"expected 1 gsettings set call, got {set_calls_with_set}"
        set_call = set_calls_with_set[0]
        assert "set" in set_call
        assert "org.gnome.desktop.input-sources" in set_call
        # The merged value passed as the last argument.
        assert "['altwin:swap_alt_win', 'caps:none']" in set_call

    def test_appends_when_no_prior_options(self, ip_module, monkeypatch):
        """When ``gsettings get`` returns ``@as []`` (empty), set to just caps:none."""
        captured_set_calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            if "get" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="@as []", stderr="")
            captured_set_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(ip_module.subprocess, "run", fake_run)
        monkeypatch.setattr(ip_module, "run", fake_run)

        result = ip_module.configure_caps_lock_neutralization("gnome", "alice")

        assert result["gnome_xkb_options_original"] == "@as []"
        set_call = captured_set_calls[0]
        assert "['caps:none']" in set_call

    def test_dedupes_when_caps_none_already_present(self, ip_module, monkeypatch):
        """When ``caps:none`` is already in the existing options, the merged
        value contains it exactly once (no duplicate)."""
        captured_set_calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            if "get" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="['caps:none', 'altwin:swap_alt_win']",
                    stderr="",
                )
            captured_set_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(ip_module.subprocess, "run", fake_run)
        monkeypatch.setattr(ip_module, "run", fake_run)

        ip_module.configure_caps_lock_neutralization("gnome", "alice")

        set_call = captured_set_calls[0]
        # The merged value must contain 'caps:none' exactly once.
        merged_str = set_call[-1]
        assert merged_str.count("caps:none") == 1, (
            f"caps:none must appear exactly once in merged value, got: {merged_str}"
        )


# ─── KDE flow  ──────────────────────────────────────────────────────


class TestKdeFlow:
    """``configure_caps_lock_neutralization('kde', ...)`` merges via configparser."""

    def test_merges_into_existing_kxkbrc(self, ip_module, monkeypatch, tmp_path):
        """When kxkbrc has ``Options=altwin:swap_alt_win``, the merged
        file has ``Options=altwin:swap_alt_win,caps:none``."""
        # Arrange: fake home dir with a real kxkbrc.
        fake_home = tmp_path
        kxkbrc = fake_home / ".config" / "kxkbrc"
        kxkbrc.parent.mkdir(parents=True)
        kxkbrc.write_text("[Layout]\nLayoutList=us\nOptions=altwin:swap_alt_win\nUse=true\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        # shutil.chown would fail in tests (no root); stub it.
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        # Act
        result = ip_module.configure_caps_lock_neutralization("kde", "alice")

        # Assert: file was rewritten with merged Options.
        new_text = kxkbrc.read_text()
        assert "Options=altwin:swap_alt_win,caps:none" in new_text, (
            f"merged Options= not found in rewritten kxkbrc:\n{new_text}"
        )
        # Other keys preserved.
        assert "LayoutList=us" in new_text
        assert "Use=true" in new_text
        # Result captures original.
        assert result["kde_config_modified"] is True
        assert result["kde_xkb_options_original"] == "altwin:swap_alt_win"

    def test_appends_when_no_options_key(self, ip_module, monkeypatch, tmp_path):
        """When kxkbrc has no ``Options=`` key, the merged file adds one."""
        fake_home = tmp_path
        kxkbrc = fake_home / ".config" / "kxkbrc"
        kxkbrc.parent.mkdir(parents=True)
        kxkbrc.write_text("[Layout]\nLayoutList=us\nUse=true\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        result = ip_module.configure_caps_lock_neutralization("kde", "alice")

        new_text = kxkbrc.read_text()
        assert "Options=caps:none" in new_text
        # Other keys preserved.
        assert "LayoutList=us" in new_text
        assert result["kde_xkb_options_original"] == ""

    def test_dedupes_when_caps_none_already_present(self, ip_module, monkeypatch, tmp_path):
        """When ``Options=`` already contains ``caps:none``, the merged file
        contains it exactly once."""
        fake_home = tmp_path
        kxkbrc = fake_home / ".config" / "kxkbrc"
        kxkbrc.parent.mkdir(parents=True)
        kxkbrc.write_text("[Layout]\nOptions=caps:none,altwin:swap_alt_win\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        ip_module.configure_caps_lock_neutralization("kde", "alice")

        new_text = kxkbrc.read_text()
        # ``caps:none`` must appear exactly once in the Options= value.
        # Find the Options= line.
        options_line = next(
            (line for line in new_text.splitlines() if line.startswith("Options=")),
            None,
        )
        assert options_line is not None, f"Options= line missing:\n{new_text}"
        assert options_line.count("caps:none") == 1, f"caps:none must appear exactly once, got: {options_line}"


# ─── Sway flow  ─────────────────────────────────────────────────────


class TestSwayFlow:
    """``configure_caps_lock_neutralization('sway', ...)`` scans and merges."""

    def test_replaces_existing_xkb_options_line(self, ip_module, monkeypatch, tmp_path):
        """When sway config has ``input * xkb_options altwin:swap_alt_win``,
        the merged file has ``input * xkb_options altwin:swap_alt_win,caps:none``,
        and the original line is preserved as a restore-marker comment."""
        fake_home = tmp_path
        sway_config = fake_home / ".config" / "sway" / "config"
        sway_config.parent.mkdir(parents=True)
        sway_config.write_text(
            "set $mod Mod4\ninput * xkb_options altwin:swap_alt_win\nbindsym Mod4+Return exec foot\n"
        )
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        result = ip_module.configure_caps_lock_neutralization("sway", "alice")

        new_text = sway_config.read_text()
        # Merged line present.
        assert "input * xkb_options altwin:swap_alt_win,caps:none" in new_text, (
            f"merged xkb_options line not found:\n{new_text}"
        )
        # Original line preserved as restore-marker comment.
        assert "# Voice Typer (original, preserved for restore): input * xkb_options altwin:swap_alt_win" in new_text
        # Non-xkb_options lines preserved.
        assert "set $mod Mod4" in new_text
        assert "bindsym Mod4+Return exec foot" in new_text
        # Result captures original.
        assert result["sway_config_modified"] is True
        assert result["sway_xkb_options_original"] == "input * xkb_options altwin:swap_alt_win"

    def test_appends_block_when_no_existing_line(self, ip_module, monkeypatch, tmp_path):
        """When sway config has no ``input * xkb_options`` line, Voice Typer
        appends its marker block."""
        fake_home = tmp_path
        sway_config = fake_home / ".config" / "sway" / "config"
        sway_config.parent.mkdir(parents=True)
        sway_config.write_text("set $mod Mod4\nbindsym Mod4+Return exec foot\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        result = ip_module.configure_caps_lock_neutralization("sway", "alice")

        new_text = sway_config.read_text()
        assert "# Voice Typer — Caps Lock neutralization" in new_text
        assert "input * xkb_options caps:none" in new_text
        # Existing content preserved.
        assert "set $mod Mod4" in new_text
        # Result captures empty original (no prior line).
        assert result["sway_xkb_options_original"] == ""

    def test_dedupes_when_caps_none_already_present(self, ip_module, monkeypatch, tmp_path):
        """When ``input * xkb_options`` already contains ``caps:none``, the
        merged line contains it exactly once."""
        fake_home = tmp_path
        sway_config = fake_home / ".config" / "sway" / "config"
        sway_config.parent.mkdir(parents=True)
        sway_config.write_text("input * xkb_options caps:none,altwin:swap_alt_win\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        ip_module.configure_caps_lock_neutralization("sway", "alice")

        new_text = sway_config.read_text()
        # The active (non-commented) line must contain caps:none exactly once.
        active_lines = [line for line in new_text.splitlines() if line.strip().startswith("input * xkb_options")]
        assert len(active_lines) == 1, f"expected 1 active xkb_options line, got {active_lines}"
        assert active_lines[0].count("caps:none") == 1


# ─── Uninstall restore  ─────────────────────────────


class TestUninstallRestore:
    """The uninstaller restores saved originals instead of resetting."""

    def test_gnome_restore_uses_set_with_saved_value(self, ip_module, monkeypatch):
        """``_restore_gnome_xkb_options`` calls ``gsettings set`` with the saved value."""
        captured: list[list[str]] = []
        monkeypatch.setattr(
            ip_module,
            "run",
            lambda cmd, check=True: (
                captured.append(cmd) or subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            ),
        )

        manifest = {
            "target_user": "alice",
            "caps_lock_originals": {"gnome_xkb_options": "['altwin:swap_alt_win']"},
        }
        ip_module._restore_gnome_xkb_options(manifest)

        assert len(captured) == 1
        cmd = captured[0]
        assert "gsettings" in cmd
        assert "set" in cmd
        assert "['altwin:swap_alt_win']" in cmd

    def test_gnome_restore_falls_back_to_reset_when_no_saved_value(self, ip_module, monkeypatch):
        """When no original was saved, falls back to ``gsettings reset``."""
        captured: list[list[str]] = []
        monkeypatch.setattr(
            ip_module,
            "run",
            lambda cmd, check=True: (
                captured.append(cmd) or subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            ),
        )

        manifest = {
            "target_user": "alice",
            "caps_lock_originals": {"gnome_xkb_options": ""},
        }
        ip_module._restore_gnome_xkb_options(manifest)

        assert len(captured) == 1
        cmd = captured[0]
        assert "reset" in cmd

    def test_kde_restore_writes_back_original_options(self, ip_module, monkeypatch, tmp_path):
        """``_restore_kde_kxkbrc_options`` rewrites the kxkbrc with the saved Options= value."""
        fake_home = tmp_path
        kxkbrc = fake_home / ".config" / "kxkbrc"
        kxkbrc.parent.mkdir(parents=True)
        # Post-install state: merged options.
        kxkbrc.write_text("[Layout]\nOptions=altwin:swap_alt_win,caps:none\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        manifest = {
            "target_user": "alice",
            "caps_lock_originals": {"kde_xkb_options": "altwin:swap_alt_win"},
        }
        ip_module._restore_kde_kxkbrc_options(manifest)

        new_text = kxkbrc.read_text()
        assert "Options=altwin:swap_alt_win" in new_text
        # caps:none must be gone (restored to original).
        assert "caps:none" not in new_text

    def test_kde_restore_removes_options_key_when_no_prior_value(self, ip_module, monkeypatch, tmp_path):
        """When no original was saved, removes the Options= key entirely."""
        fake_home = tmp_path
        kxkbrc = fake_home / ".config" / "kxkbrc"
        kxkbrc.parent.mkdir(parents=True)
        kxkbrc.write_text("[Layout]\nOptions=caps:none\nLayoutList=us\n")
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        manifest = {
            "target_user": "alice",
            "caps_lock_originals": {"kde_xkb_options": ""},
        }
        ip_module._restore_kde_kxkbrc_options(manifest)

        new_text = kxkbrc.read_text()
        assert "Options=" not in new_text, f"Options= key should be removed, got:\n{new_text}"
        # Other keys preserved.
        assert "LayoutList=us" in new_text

    def test_sway_restore_replaces_merged_line_with_original(self, ip_module, monkeypatch, tmp_path):
        """``_restore_sway_config_options`` replaces the merged line with the saved original."""
        fake_home = tmp_path
        sway_config = fake_home / ".config" / "sway" / "config"
        sway_config.parent.mkdir(parents=True)
        # Post-install state: restore-marker comment + merged line.
        sway_config.write_text(
            "set $mod Mod4\n"
            "# Voice Typer (original, preserved for restore): input * xkb_options altwin:swap_alt_win\n"
            "input * xkb_options altwin:swap_alt_win,caps:none\n"
            "bindsym Mod4+Return exec foot\n"
        )
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        manifest = {
            "target_user": "alice",
            "caps_lock_originals": {"sway_xkb_options_line": "input * xkb_options altwin:swap_alt_win"},
        }
        ip_module._restore_sway_config_options(manifest)

        new_text = sway_config.read_text()
        # Original line restored.
        assert "input * xkb_options altwin:swap_alt_win" in new_text
        # Merged line gone.
        assert "altwin:swap_alt_win,caps:none" not in new_text
        # Restore-marker comment gone.
        assert "Voice Typer (original" not in new_text
        # Other lines preserved.
        assert "set $mod Mod4" in new_text
        assert "bindsym Mod4+Return exec foot" in new_text

    def test_sway_restore_removes_block_when_no_prior_line(self, ip_module, monkeypatch, tmp_path):
        """When no original line was saved (append-mode install), removes the marker block."""
        fake_home = tmp_path
        sway_config = fake_home / ".config" / "sway" / "config"
        sway_config.parent.mkdir(parents=True)
        # Post-install state: marker + appended xkb_options line.
        sway_config.write_text(
            "set $mod Mod4\n"
            "# Voice Typer — Caps Lock neutralization\n"
            "input * xkb_options caps:none\n"
            "bindsym Mod4+Return exec foot\n"
        )
        fake_pw = type("FakePw", (), {"pw_dir": str(fake_home), "pw_uid": 1000, "pw_gid": 1000})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)
        monkeypatch.setattr(ip_module.shutil, "chown", lambda *a, **k: None)

        manifest = {
            "target_user": "alice",
            "caps_lock_originals": {"sway_xkb_options_line": ""},
        }
        ip_module._restore_sway_config_options(manifest)

        new_text = sway_config.read_text()
        # Marker block removed.
        assert "# Voice Typer — Caps Lock neutralization" not in new_text
        assert "input * xkb_options caps:none" not in new_text
        # Other lines preserved.
        assert "set $mod Mod4" in new_text
        assert "bindsym Mod4+Return exec foot" in new_text


# ─── Manifest schema (/19/20) ─────────────────────────────────────────


class TestManifestSchema:
    """``write_manifest`` records the originals for restore-on-uninstall."""

    def test_manifest_includes_caps_lock_originals(self, ip_module, monkeypatch, tmp_path):
        """The manifest v2 schema includes ``caps_lock_originals``."""
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(ip_module, "MANIFEST_PATH", manifest_path)
        monkeypatch.setattr(ip_module, "MANIFEST_DIR", tmp_path)

        session_info = {
            "session_type": "gnome",
            "xkb_conf_installed": False,
            "gnome_settings_modified": True,
            "kde_config_modified": False,
            "sway_config_modified": False,
            "gnome_xkb_options_original": "['altwin:swap_alt_win']",
            "kde_xkb_options_original": "",
            "sway_xkb_options_original": "",
        }
        ip_module.write_manifest("alice", session_info, None, None)

        manifest = json.loads(manifest_path.read_text())
        assert manifest["version"] == 2
        assert manifest["target_user"] == "alice"
        assert manifest["caps_lock_originals"]["gnome_xkb_options"] == "['altwin:swap_alt_win']"
        assert manifest["caps_lock_originals"]["kde_xkb_options"] == ""
        assert manifest["caps_lock_originals"]["sway_xkb_options_line"] == ""


# ─── install() fails fast on no target user ────────────────────────


class TestGp131NoTargetUserFails:
    """``install()`` must ``fail(5, ...)`` when no target user is detected."""

    def test_install_fails_with_code_5_when_no_user(self, ip_module, monkeypatch):
        """When ``get_target_user()`` returns None, ``install()`` exits 5."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)
        monkeypatch.setattr(ip_module, "get_target_user", lambda: None)

        # Capture ``fail()`` calls — don't actually ``sys.exit``.
        captured_fail: list[tuple[int, str]] = []

        def fake_fail(code, msg):
            captured_fail.append((code, msg))

        monkeypatch.setattr(ip_module, "fail", fake_fail)

        # Stub out setup_polkit_stable_path so install() doesn't try to
        # actually create /usr/share/voice-typer/scripts/.
        monkeypatch.setattr(ip_module, "setup_polkit_stable_path", lambda: None)

        ip_module.install()

        # Assert fail was called with code 5 and the expected message.
        assert len(captured_fail) == 1, f"expected 1 fail() call, got {captured_fail}"
        code, msg = captured_fail[0]
        assert code == 5
        assert "no target user detected" in msg
        assert "PKEXEC_UID=$(id -u)" in msg
