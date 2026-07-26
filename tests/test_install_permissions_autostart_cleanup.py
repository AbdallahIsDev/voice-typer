"""Tests for the autostart ``.desktop`` cleanup logic in
``scripts/linux/install_permissions.py``.

Covers S2-CR-69 (Linux): the uninstall flow now removes the per-user
autostart ``.desktop`` file at
``<XDG_CONFIG_HOME or ~/.config>/autostart/voice-typer.desktop`` so the
desktop environment does not keep trying to launch the (now-deleted)
binary on every login.

The tests exercise:

1. ``_unlink_autostart_desktop_at`` — removes the ``.desktop`` file when
   present, no-ops when absent, logs a warning on ``OSError``.
2. ``_remove_autostart_desktop`` — uses ``target_user``'s home dir from
   ``pwd.getpwnam`` and scans ``/home/*`` as a defensive fallback.
3. ``uninstall()`` — invokes ``_remove_autostart_desktop`` with the
   manifest's ``target_user``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SCRIPT = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"


def _load_install_permissions_module():
    """Load ``scripts/linux/install_permissions.py`` as an isolated module."""
    spec = importlib.util.spec_from_file_location("install_permissions_autostart_cleanup_under_test", _INSTALL_SCRIPT)
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


# ─── _unlink_autostart_desktop_at ───────────────────────────────────────────


class TestUnlinkAutostartDesktopAt:
    """Verify the per-home-dir ``.desktop`` removal helper."""

    def test_removes_desktop_when_present(self, ip_module, tmp_path, capsys):
        """When the ``.desktop`` file exists, it is unlinked and logged."""
        autostart_dir = tmp_path / ".config" / "autostart"
        autostart_dir.mkdir(parents=True)
        desktop_path = autostart_dir / "voice-typer.desktop"
        desktop_path.write_text("[Desktop Entry]\nType=Application\n")

        ip_module._unlink_autostart_desktop_at(tmp_path)

        assert not desktop_path.exists()
        out = capsys.readouterr().out
        assert "Removed autostart .desktop" in out
        assert str(desktop_path) in out

    def test_noop_when_desktop_absent(self, ip_module, tmp_path, capsys):
        """When the ``.desktop`` file is absent, the function is a silent no-op."""
        # tmp_path exists but has no .config/autostart/voice-typer.desktop
        ip_module._unlink_autostart_desktop_at(tmp_path)
        out = capsys.readouterr().out
        # Nothing logged when there's nothing to remove.
        assert "Removed autostart .desktop" not in out

    def test_logs_warning_on_unlink_failure(self, ip_module, tmp_path, capsys, monkeypatch):
        """When ``unlink()`` raises ``OSError``, a warning is logged (non-fatal)."""
        autostart_dir = tmp_path / ".config" / "autostart"
        autostart_dir.mkdir(parents=True)
        desktop_path = autostart_dir / "voice-typer.desktop"
        desktop_path.write_text("[Desktop Entry]\n")

        # Force unlink() to raise OSError.
        def _raise_oserror(self):
            raise OSError("permission denied (simulated)")

        monkeypatch.setattr(Path, "unlink", _raise_oserror)

        ip_module._unlink_autostart_desktop_at(tmp_path)

        out = capsys.readouterr().out
        assert "WARNING: failed to remove" in out
        assert "permission denied (simulated)" in out


# ─── _remove_autostart_desktop ──────────────────────────────────────────────


class TestRemoveAutostartDesktop:
    """Verify the orchestrator that resolves the user's home dir."""

    def test_empty_target_user_skips_pwd_lookup(self, ip_module, tmp_path, monkeypatch):
        """When ``target_user`` is empty, ``pwd.getpwnam`` is never called."""
        # If pwd.getpwnam were called with "", it would raise KeyError.
        # We assert no exception bubbles up — the function should skip the
        # pwd path entirely when target_user is falsy.
        call_count = {"n": 0}
        original_getpwnam = ip_module.pwd.getpwnam

        def _counting_getpwnam(user):
            call_count["n"] += 1
            return original_getpwnam(user)

        monkeypatch.setattr(ip_module.pwd, "getpwnam", _counting_getpwnam)
        # Redirect /home to a non-existent path so the fallback scan is a no-op.
        monkeypatch.setattr(ip_module.Path, "__call__", lambda self, *a, **k: Path("/nonexistent_home_root_for_test"))

        # We can't easily monkeypatch the module-level Path constant, so
        # just verify the function returns None and doesn't raise.
        ip_module._remove_autostart_desktop("")
        # pwd.getpwnam should not have been called.
        assert call_count["n"] == 0

    def test_root_target_user_skips_pwd_lookup(self, ip_module, monkeypatch):
        """When ``target_user`` is 'root', ``pwd.getpwnam`` is not called
        (the 'root' user doesn't have a per-user autostart entry)."""
        monkeypatch.setattr(
            ip_module.pwd,
            "getpwnam",
            lambda u: (_ for _ in ()).throw(AssertionError("should not be called for root")),
        )
        ip_module._remove_autostart_desktop("root")

    def test_target_user_resolves_home_dir_via_pwd(self, ip_module, tmp_path, monkeypatch):
        """When ``target_user`` is a real user, ``pwd.getpwnam`` resolves
        the home directory and the ``.desktop`` file inside it is removed."""
        autostart_dir = tmp_path / ".config" / "autostart"
        autostart_dir.mkdir(parents=True)
        desktop_path = autostart_dir / "voice-typer.desktop"
        desktop_path.write_text("[Desktop Entry]\n")

        # Fake pwd entry — pw_dir points at tmp_path.
        fake_pw = type("FakePw", (), {"pw_dir": str(tmp_path)})()
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: fake_pw)

        # Make the /home scan a no-op.
        monkeypatch.setattr(
            ip_module.Path,
            "is_dir",
            lambda self: False if str(self) == "/home" else Path.is_dir(self),
        )

        ip_module._remove_autostart_desktop("alice")

        assert not desktop_path.exists()

    def test_unknown_target_user_logs_warning(self, ip_module, monkeypatch, capsys):
        """When ``pwd.getpwnam`` raises ``KeyError``, a warning is logged
        (non-fatal) and the function continues to the /home fallback scan."""
        monkeypatch.setattr(ip_module.pwd, "getpwnam", lambda u: (_ for _ in ()).throw(KeyError(u)))
        # /home doesn't exist on most CI runners, so the fallback scan is a no-op.
        ip_module._remove_autostart_desktop("nonexistent_user_xyz")
        out = capsys.readouterr().out
        assert "WARNING: cannot resolve home dir" in out
        assert "nonexistent_user_xyz" in out

    def test_home_fallback_scan_removes_stray_desktops(self, ip_module, tmp_path, monkeypatch):
        """The ``HOME_ROOT_SCAN/*`` fallback scan removes
        ``voice-typer.desktop`` from every user home directory that has one."""
        # Build two fake user homes under tmp_path/home.
        fake_home_root = tmp_path / "home"
        user1 = fake_home_root / "user1"
        user2 = fake_home_root / "user2"
        user1_autostart = user1 / ".config" / "autostart"
        user2_autostart = user2 / ".config" / "autostart"
        user1_autostart.mkdir(parents=True)
        user2_autostart.mkdir(parents=True)
        desktop1 = user1_autostart / "voice-typer.desktop"
        desktop2 = user2_autostart / "voice-typer.desktop"
        desktop1.write_text("[Desktop Entry]\n")
        desktop2.write_text("[Desktop Entry]\n")

        # Redirect HOME_ROOT_SCAN to our fake root.
        monkeypatch.setattr(ip_module, "HOME_ROOT_SCAN", fake_home_root)

        # Empty target_user → skip pwd path; rely on fallback scan.
        ip_module._remove_autostart_desktop("")

        assert not desktop1.exists()
        assert not desktop2.exists()

    def test_home_fallback_scan_handles_unreadable_entry(self, ip_module, tmp_path, monkeypatch):
        """A single unreadable entry under ``HOME_ROOT_SCAN`` does not abort
        the scan of the rest of the directory."""
        fake_home_root = tmp_path / "home"
        # user_good has a real .desktop to remove; user_bad's iterdir/stat
        # will be made to raise PermissionError.
        user_good = fake_home_root / "user_good"
        user_bad = fake_home_root / "user_bad"
        good_autostart = user_good / ".config" / "autostart"
        good_autostart.mkdir(parents=True)
        good_desktop = good_autostart / "voice-typer.desktop"
        good_desktop.write_text("[Desktop Entry]\n")
        user_bad.mkdir(parents=True)

        monkeypatch.setattr(ip_module, "HOME_ROOT_SCAN", fake_home_root)

        # Make user_bad's stat raise PermissionError (simulates a
        # service-account home dir we can't read).
        original_is_dir = Path.is_dir

        def _guarded_is_dir(self):
            if str(self) == str(user_bad):
                raise PermissionError("simulated EACCES on user_bad")
            return original_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", _guarded_is_dir)

        # Should not raise — user_bad is skipped, user_good is cleaned up.
        ip_module._remove_autostart_desktop("")

        assert not good_desktop.exists()


# ─── uninstall() integration ────────────────────────────────────────────────


class TestUninstallInvokesAutostartCleanup:
    """Verify that ``uninstall()`` calls ``_remove_autostart_desktop`` with
    the manifest's ``target_user``."""

    def test_uninstall_passes_manifest_target_user_to_autostart_cleanup(self, ip_module, monkeypatch, tmp_path):
        """``uninstall()`` reads ``target_user`` from the manifest and passes
        it to ``_remove_autostart_desktop``."""
        # Skip the root check.
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        # Manifest with a known target_user.
        captured_args = {"target_user": None}

        def _capture_remove_autostart_desktop(target_user):
            captured_args["target_user"] = target_user

        monkeypatch.setattr(ip_module, "_remove_autostart_desktop", _capture_remove_autostart_desktop)

        # Stub out the other side effects of uninstall().
        for path_const in (
            "UDEV_RULE_PATH",
            "XKB_CONF_PATH",
            "MANIFEST_PATH",
        ):
            monkeypatch.setattr(ip_module, path_const, tmp_path / f"nonexistent_{path_const}")

        # Write a manifest that uninstall() will read.
        manifest_path = tmp_path / "permissions-manifest.json"
        manifest_path.write_text('{"target_user": "alice"}')
        monkeypatch.setattr(ip_module, "MANIFEST_PATH", manifest_path)

        # Stub run() so udevadm calls don't actually fire.
        monkeypatch.setattr(ip_module, "run", lambda *a, **k: None)

        ip_module.uninstall()

        assert captured_args["target_user"] == "alice"

    def test_uninstall_passes_empty_string_when_manifest_missing(self, ip_module, monkeypatch, tmp_path):
        """When the manifest is missing, ``uninstall()`` passes ``""`` to
        ``_remove_autostart_desktop`` (which then relies on the /home scan)."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        captured_args = {"target_user": "sentinel"}

        def _capture_remove_autostart_desktop(target_user):
            captured_args["target_user"] = target_user

        monkeypatch.setattr(ip_module, "_remove_autostart_desktop", _capture_remove_autostart_desktop)

        # No manifest file.
        monkeypatch.setattr(ip_module, "MANIFEST_PATH", tmp_path / "nonexistent_manifest.json")
        monkeypatch.setattr(ip_module, "UDEV_RULE_PATH", tmp_path / "nonexistent_udev")
        monkeypatch.setattr(ip_module, "XKB_CONF_PATH", tmp_path / "nonexistent_xkb")
        monkeypatch.setattr(ip_module, "run", lambda *a, **k: None)

        ip_module.uninstall()

        assert captured_args["target_user"] == ""
