"""Targeted tests for two review.md findings addressed in this wave:"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock


def _make_service_and_app(tmp_config_dir, monkeypatch):
    """Build a VoiceTyperService backed by a mock app for apply_config tests."""
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    @contextlib.contextmanager
    def _fake_lock():
        yield

    app = MagicMock()
    app._config_mutation_lock = _fake_lock()
    app.config = Config()
    app.config.audio_preset = "auto"
    app.config.save = MagicMock(return_value=True)
    app.config.save_strict = MagicMock(return_value=None)
    app.clipboard = MagicMock()
    app.tray = MagicMock()
    app.tray.invalidate_menu_cache = MagicMock()
    app._llm_polisher = None
    app.hotkeys = MagicMock()
    app.recorder = MagicMock()
    app._busy_event = MagicMock()
    app._busy_event.is_set = MagicMock(return_value=True)
    app._shutting_down = False

    service = VoiceTyperService(app)

    import voice_typer.server.credential_store as cs

    monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})

    return service, app


class TestApplyPresetAutoSwitchToCustom:
    """current preset is a named preset (not ``\"custom\"``)."""

    def test_individual_toggle_switches_preset_to_custom(self, tmp_config_dir, monkeypatch):
        """Setting ``noise_filter_highpass=False`` while preset is"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        assert app.config.audio_preset == "auto"

        service.apply_config({"noise_filter_highpass": False})

        assert app.config.audio_preset == "custom", (
            "setting an individual noise_filter_* toggle while "
            "audio_preset is a named preset (e.g. 'auto') must auto-switch "
            "audio_preset to 'custom', otherwise Config.load() will call "
            "apply_preset('auto', instance) on next restart and silently "
            "revert the user's toggle to the preset's value."
        )
        assert app.config.noise_filter_highpass is False, "the user's individual toggle value must be preserved."

    def test_individual_toggle_no_switch_when_already_custom(self, tmp_config_dir, monkeypatch):
        """individual toggle should NOT add ``audio_preset`` to updates"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "custom"

        # Use a spy on save_strict to verify the dirty-check still
        service.apply_config({"noise_filter_gate": False})

        assert app.config.audio_preset == "custom"
        assert app.config.noise_filter_gate is False
        app.config.save_strict.assert_called_once_with()

    def test_individual_toggle_no_switch_when_preset_explicitly_set(self, tmp_config_dir, monkeypatch):
        """When the user explicitly sets ``audio_preset`` in the same"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "auto"

        service.apply_config(
            {
                "audio_preset": "studio",
                "noise_filter_highpass": True,
            }
        )

        assert app.config.audio_preset == "studio", (
            "when audio_preset is explicitly in updates, the user is picking a preset, the auto-switch must NOT fire."
        )

    def test_non_preset_key_does_not_trigger_switch(self, tmp_config_dir, monkeypatch):
        """
        Setting a config key that ``apply_preset`` does NOT touch
        ``audio_presets.PRESETS``) must NOT trigger the auto-switch.
        """
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "auto"

        service.apply_config({"vad_filter_enabled": False})

        assert app.config.audio_preset == "auto", (
            "vad_filter_enabled is not in the preset's "
            "overwrite set (see audio_presets.PRESETS), so changing it "
            "must NOT auto-switch audio_preset to custom."
        )
        assert app.config.vad_filter_enabled is False

    def test_multiple_individual_toggles_switch_to_custom(self, tmp_config_dir, monkeypatch):
        """Setting multiple individual toggles in one IPC call still"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "noisy_room"

        service.apply_config(
            {
                "noise_filter_highpass": False,
                "noise_filter_gate": False,
                "noise_filter_eq": False,
            }
        )

        assert app.config.audio_preset == "custom"
        assert app.config.noise_filter_highpass is False
        assert app.config.noise_filter_gate is False
        assert app.config.noise_filter_eq is False


class TestEnforceWindowsOwnerOnlyAcl:
    """``_enforce_windows_owner_only_acl`` restricts file/dir"""

    def test_noop_on_non_windows(self, monkeypatch, tmp_path):
        """On non-Windows platforms, the helper must be a no-op —"""
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: False)
        import subprocess

        call_count = {"n": 0}
        original_run = subprocess.run

        def _spy_run(*args, **kwargs):
            call_count["n"] += 1
            return original_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _spy_run)

        config_mod._enforce_windows_owner_only_acl(tmp_path / "test.txt")
        assert call_count["n"] == 0, (
            "on non-Windows, _enforce_windows_owner_only_acl must be a "
            "no-op, POSIX uses os.chmod(path, 0o600) elsewhere."
        )

    def test_calls_icacls_with_correct_args_on_windows(self, monkeypatch, tmp_path):
        """On Windows, the helper must invoke ``icacls <path>"""
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setenv("USERNAME", "testuser")
        monkeypatch.delenv("USER", raising=False)

        captured: list = []

        def _fake_run(cmd, **kwargs):
            captured.append((cmd, kwargs))
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = "Successfully processed 1 files"
            return result

        import subprocess

        monkeypatch.setattr(subprocess, "run", _fake_run)

        target = tmp_path / "config.json"
        target.write_text("{}")
        config_mod._enforce_windows_owner_only_acl(target)

        assert len(captured) == 1, "icacls must be invoked exactly once"
        cmd, kwargs = captured[0]
        assert cmd[0] == "icacls", "first arg must be the icacls binary"
        assert str(target) in cmd, "path must be in the icacls args"
        assert "/inheritance:r" in cmd, (
            "must remove inherited ACEs so a shared parent dir's DACL doesn't grant world-read access."
        )
        assert "/grant:r" in cmd, "must replace (not merge) explicit grants so only the current user is granted."
        assert "testuser:F" in cmd, "must grant Full control to the current user (USERNAME env var)."
        # Use list form (not shell=True) so cmd.exe metacharacter
        assert kwargs.get("shell") is not True, (
            "must NOT use shell=True, passing a list to subprocess.run without shell=True sidesteps cmd.exe injection."
        )

    def test_skips_when_username_env_var_empty(self, monkeypatch, tmp_path):
        """grant to)."""
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.delenv("USER", raising=False)

        call_count = {"n": 0}

        import subprocess

        def _fake_run(*args, **kwargs):
            call_count["n"] += 1
            return MagicMock(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Must NOT raise.
        config_mod._enforce_windows_owner_only_acl(tmp_path / "test.txt")
        assert call_count["n"] == 0, "when USERNAME is empty, icacls must not be invoked, there is no user to grant to."

    def test_does_not_raise_on_icacls_failure(self, monkeypatch, tmp_path):
        """When ``icacls`` returns non-zero exit code (e.g. permission"""
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setenv("USERNAME", "testuser")

        import subprocess

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 5  # access denied
            result.stderr = "Access is denied."
            result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Must NOT raise.
        config_mod._enforce_windows_owner_only_acl(tmp_path / "test.txt")

    def test_does_not_raise_on_subprocess_error(self, monkeypatch, tmp_path):
        """When ``subprocess.run`` itself raises (e.g. ``icacls`` not on"""
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setenv("USERNAME", "testuser")

        import subprocess

        def _fake_run(cmd, **kwargs):
            raise FileNotFoundError("icacls not on PATH")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Must NOT raise.
        config_mod._enforce_windows_owner_only_acl(tmp_path / "test.txt")

    def test_save_invokes_acl_helper_on_windows(self, monkeypatch, tmp_config_dir):
        """``save()`` after the cross-process lock is acquired) must call"""
        from voice_typer.server import config as config_mod
        from voice_typer.server.config import Config

        # Simulate Windows so the ``if is_windows():`` / ``else:``
        monkeypatch.setattr(config_mod, "is_windows", lambda: True)

        targets: list[str] = []

        def _spy_acl(path):
            targets.append(str(path))
            return True

        monkeypatch.setattr(config_mod, "_enforce_windows_owner_only_acl", _spy_acl)

        # Write a pre-existing config.json so the .bak backup branch fires.
        config_file = tmp_config_dir / "config.json"
        config_file.write_text('{"schema_version": 3}')

        cfg = Config()
        # Call ``_save_unlocked`` directly to bypass the cross-process
        result = cfg._save_unlocked()
        assert result is True, "_save_unlocked should report success"

        # The config DIR must NOT be in the list: ``_save_unlocked`` is
        assert not any(str(tmp_config_dir) == t for t in targets), (
            "Config._save_unlocked must NOT call _enforce_windows_owner_only_acl "
            f"on the config directory {tmp_config_dir} (dir-wide icacls while "
            "the lock file is open breaks subsequent saves on Python < 3.11.13; "
            "the dir is tightened by save() before the lock). "
            f"Got calls: {targets}"
        )
        assert any(t.endswith("config.json") and not t.endswith(".bak") for t in targets), (
            "Config._save_unlocked must call _enforce_windows_owner_only_acl "
            f"on the config.json file. Got calls: {targets}"
        )
        assert any(t.endswith("config.json.bak") for t in targets), (
            "Config._save_unlocked must call _enforce_windows_owner_only_acl "
            "on the config.json.bak file (the .bak also contains plaintext "
            f"API keys). Got calls: {targets}"
        )

    def test_save_tightens_config_dir_before_lock(self, monkeypatch, tmp_config_dir):
        """``Config.save()`` must tighten the config DIR's ACL BEFORE"""
        import contextlib
        import shutil

        from voice_typer.server import config as config_mod
        from voice_typer.server.config import Config

        order: list[str] = []

        @contextlib.contextmanager
        def _fake_lock():
            order.append("lock")
            yield

        def _spy_acl(path):
            order.append(f"acl:{path}")
            return True

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setattr(config_mod, "_acquire_config_lock", _fake_lock)
        monkeypatch.setattr(config_mod, "_enforce_windows_owner_only_acl", _spy_acl)

        # Simulate a fresh install: the config dir does not exist yet,
        shutil.rmtree(tmp_config_dir, ignore_errors=True)

        cfg = Config()
        assert cfg.save() is True

        assert order, "save() must invoke _enforce_windows_owner_only_acl on Windows"
        # The config dir must be tightened FIRST, before the lock is
        assert order[0] == f"acl:{tmp_config_dir}", (
            "Config.save() must tighten the config DIR before acquiring the "
            f"cross-process lock. Expected first ACL call to be {tmp_config_dir}, "
            f"got {order}"
        )
        assert order.index("lock") > order.index(f"acl:{tmp_config_dir}"), (
            f"The dir ACL must be enforced before the lock file is opened. Got order: {order}"
        )

        # The dir ACL must run ONCE per config dir (only when save()
        cfg.hotkey = "<f9>"  # mark dirty so the second save is real
        assert cfg.save() is True
        assert order.count(f"acl:{tmp_config_dir}") == 1, (
            f"The config DIR must be tightened exactly once (only when save() creates the directory); got {order}"
        )
