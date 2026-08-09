"""Targeted tests for two review.md findings addressed in this wave:

  - ``apply_preset`` silently reverts user toggles.
    When a user submits an IPC ``set_config`` for an individual
    ``noise_filter_*`` toggle while ``audio_preset`` is a named preset
    (auto / studio / noisy_room / off), the next ``Config.load()`` would
    call ``apply_preset`` again and silently revert the user's toggle to
    the preset's value. The fix in ``config_applier.apply_config`` auto-
    switches ``audio_preset`` to ``"custom"`` (with an INFO log) so the
    user's individual toggle survives a restart.

  - Windows config file ACLs not enforced.
    ``_secure_atomic_write``'s ``tempfile.mkstemp`` inherits the parent
    dir's DACL on Windows, so a shared ``%APPDATA%`` or
    ``VOICE_TYPER_CONFIG_DIR`` would leave ``config.json`` (with
    plaintext API keys when the keyring is unavailable) world-readable.
    The fix adds ``_enforce_windows_owner_only_acl`` in ``config.py``
    which runs ``icacls /inheritance:r /grant:r "%USERNAME%:F"`` on the
    config dir, ``config.json``, and ``config.json.bak`` after each
    write. Best-effort — logs a warning on failure but does NOT raise.

These tests are intentionally focused — they verify the specific
behavior change introduced by each fix, not the full surface area
(which is already covered by the existing test suite).
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

# ── apply_preset silently reverts user toggles ────────────


def _make_service_and_app(tmp_config_dir, monkeypatch):
    """Build a VoiceTyperService backed by a mock app for apply_config tests.

    Mirrors the fixture pattern in ``tests/test_config_applier_lazy.py``
    so the dirty-check has a real Config instance to compare against.
    """
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

    # credential_store pre-route: no api_key fields in these tests,
    # but the import path is exercised — stub the mapping to empty so
    # nothing is routed.
    import voice_typer.server.credential_store as cs

    monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})

    return service, app


class TestApplyPresetAutoSwitchToCustom:
    """``apply_config`` auto-switches ``audio_preset`` to
    ``"custom"`` when an individual filter toggle is set while the
    current preset is a named preset (not ``"custom"``)."""

    def test_individual_toggle_switches_preset_to_custom(self, tmp_config_dir, monkeypatch):
        """Setting ``noise_filter_highpass=False`` while preset is
        ``"auto"`` should auto-switch ``audio_preset`` to ``"custom"``
        so ``Config.load()``'s ``apply_preset`` call doesn't revert it."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        assert app.config.audio_preset == "auto"

        service.apply_config({"noise_filter_highpass": False})

        assert app.config.audio_preset == "custom", (
            "setting an individual noise_filter_* toggle while "
            "audio_preset is a named preset (e.g. 'auto') must auto-switch "
            "audio_preset to 'custom' — otherwise Config.load() will call "
            "apply_preset('auto', instance) on next restart and silently "
            "revert the user's toggle to the preset's value."
        )
        assert app.config.noise_filter_highpass is False, "the user's individual toggle value must be preserved."

    def test_individual_toggle_no_switch_when_already_custom(self, tmp_config_dir, monkeypatch):
        """When ``audio_preset`` is already ``"custom"``, setting an
        individual toggle should NOT add ``audio_preset`` to updates
        (no-op — already custom)."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "custom"

        # Use a spy on save_strict to verify the dirty-check still
        # fires (the toggle actually changed).
        service.apply_config({"noise_filter_gate": False})

        assert app.config.audio_preset == "custom"
        assert app.config.noise_filter_gate is False
        # save_strict should have been called because the toggle value
        # actually changed (the auto-switch is a no-op here).
        app.config.save_strict.assert_called_once_with()

    def test_individual_toggle_no_switch_when_preset_explicitly_set(self, tmp_config_dir, monkeypatch):
        """When the user explicitly sets ``audio_preset`` in the same
        update, the preset's toggles are the intent — do NOT auto-switch."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "auto"

        # User picks "studio" preset AND sets an individual toggle in
        # the same IPC call. The preset choice wins — the toggle is
        # part of the same "I want this preset" intent.
        service.apply_config(
            {
                "audio_preset": "studio",
                "noise_filter_highpass": True,
            }
        )

        assert app.config.audio_preset == "studio", (
            "when audio_preset is explicitly in updates, the user is picking a preset — the auto-switch must NOT fire."
        )

    def test_non_preset_key_does_not_trigger_switch(self, tmp_config_dir, monkeypatch):
        """Setting a config key that ``apply_preset`` does NOT touch
        (e.g. ``noise_filter_enabled``, which is not in
        ``audio_presets.PRESETS``) must NOT trigger the auto-switch."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app.config.audio_preset = "auto"

        # noise_filter_enabled is NOT in _PRESET_OVERRIDE_KEYS — it's
        # not overwritten by apply_preset (it's only set when audio_preset
        # changes via apply_config_side_effects, not by Config.load()).
        service.apply_config({"noise_filter_enabled": False})

        assert app.config.audio_preset == "auto", (
            "noise_filter_enabled is not in the preset's "
            "overwrite set (see audio_presets.PRESETS), so changing it "
            "must NOT auto-switch audio_preset to custom."
        )
        assert app.config.noise_filter_enabled is False

    def test_multiple_individual_toggles_switch_to_custom(self, tmp_config_dir, monkeypatch):
        """Setting multiple individual toggles in one IPC call still
        switches ``audio_preset`` to ``"custom"`` exactly once."""
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


# ── Windows config file ACLs not enforced ─────────────────


class TestEnforceWindowsOwnerOnlyAcl:
    """``_enforce_windows_owner_only_acl`` restricts file/dir
    ACL to the current user on Windows via ``icacls /inheritance:r
    /grant:r "%USERNAME%:F"``."""

    def test_noop_on_non_windows(self, monkeypatch, tmp_path):
        """On non-Windows platforms, the helper must be a no-op —
        POSIX uses ``os.chmod`` elsewhere in ``config.py``."""
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: False)
        # subprocess should NOT be imported (much less called).
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
            "no-op — POSIX uses os.chmod(path, 0o600) elsewhere."
        )

    def test_calls_icacls_with_correct_args_on_windows(self, monkeypatch, tmp_path):
        """On Windows, the helper must invoke ``icacls <path>
        /inheritance:r /grant:r <user>:F`` via ``subprocess.run``."""
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
        # injection is impossible even if USERNAME contains shell specials.
        assert kwargs.get("shell") is not True, (
            "must NOT use shell=True — passing a list to subprocess.run without shell=True sidesteps cmd.exe injection."
        )

    def test_skips_when_username_env_var_empty(self, monkeypatch, tmp_path):
        """When ``USERNAME`` and ``USER`` env vars are both empty, the
        helper must log a warning and skip the icacls call (no user to
        grant to)."""
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
        assert call_count["n"] == 0, (
            "when USERNAME is empty, icacls must not be invoked — there is no user to grant to."
        )

    def test_does_not_raise_on_icacls_failure(self, monkeypatch, tmp_path):
        """When ``icacls`` returns non-zero exit code (e.g. permission
        denied, file locked), the helper must log a warning but NOT
        raise — so save() is not broken in a restricted environment."""
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
        """When ``subprocess.run`` itself raises (e.g. ``icacls`` not on
        PATH → FileNotFoundError), the helper must log and NOT raise."""
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
        """integration: ``Config._save_unlocked`` (the body of
        ``save()`` after the cross-process lock is acquired) must call
        ``_enforce_windows_owner_only_acl`` on the config file and (if
        a backup is written) the .bak file — but ONLY on Windows, and
        NEVER on the config directory (the dir ACL is tightened by
        ``save()`` BEFORE the lock is acquired — see
        ``test_save_tightens_config_dir_before_lock``). We call
        ``_save_unlocked`` directly to skip the cross-process lock
        (which tries to import ``msvcrt`` on Windows). We simulate
        Windows by patching ``config.is_windows`` so the Windows-only
        call sites fire; the spy replaces the helper so no real
        ``icacls`` is invoked."""
        from voice_typer.server import config as config_mod
        from voice_typer.server.config import Config

        # Simulate Windows so the ``if is_windows():`` / ``else:``
        # branches in ``_save_unlocked`` that call the ACL helper fire.
        # NOTE: only patches ``config.is_windows`` — ``secure_file_io``
        # has its own import and stays non-Windows, so the actual file
        # write proceeds via the POSIX path (which works on the Linux
        # test platform too).
        monkeypatch.setattr(config_mod, "is_windows", lambda: True)

        targets: list[str] = []

        def _spy_acl(path):
            targets.append(str(path))

        monkeypatch.setattr(config_mod, "_enforce_windows_owner_only_acl", _spy_acl)

        # Write a pre-existing config.json so the .bak backup branch fires.
        config_file = tmp_config_dir / "config.json"
        config_file.write_text('{"schema_version": 3}')

        cfg = Config()
        # Call ``_save_unlocked`` directly to bypass the cross-process
        # file lock (which uses ``msvcrt`` on Windows — unavailable on
        # the Linux test platform).
        result = cfg._save_unlocked()
        assert result is True, "_save_unlocked should report success"

        # The spy should be called on:
        #   1. the config.json.bak (after _secure_atomic_write of the bak)
        #   2. the config.json (after _secure_atomic_write of the main file)
        #
        # The config DIR must NOT be in the list: ``_save_unlocked`` is
        # always called with ``config.json.lock`` held open, and
        # ``icacls <dir>`` while the lock file is open poisons it on
        # Python < 3.11.13 (no FILE_SHARE_DELETE in os.open), failing
        # every subsequent save() in the process. The dir ACL is
        # tightened by ``save()`` BEFORE the lock is acquired.
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
        """``Config.save()`` must tighten the config DIR's ACL BEFORE
        the cross-process lock is acquired: ``icacls <dir>`` while
        ``config.json.lock`` is held open poisons the lock file on
        Python < 3.11.13 (no FILE_SHARE_DELETE in os.open), failing
        every subsequent save in the process. Tightening the dir first
        also means the ``tempfile.mkstemp`` tmp file inherits the
        owner-only dir DACL, so ``config.json`` is never
        shared-readable even in the brief window between ``os.replace``
        and the per-file icacls.

        The lock is replaced with a no-op spy context manager so the
        assertion runs on any platform (the real lock branches on
        ``config.is_windows`` — the same function object — which this
        test patches to True). The dir ACL must run exactly ONCE (only
        when ``save()`` creates the directory): re-running the
        dir-wide icacls on a dir that already exists is both pointless
        and unsafe (dir icacls while files in the dir are open poisons
        them on Python < 3.11.13)."""
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

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setattr(config_mod, "_acquire_config_lock", _fake_lock)
        monkeypatch.setattr(config_mod, "_enforce_windows_owner_only_acl", _spy_acl)

        # Simulate a fresh install: the config dir does not exist yet,
        # so save() creates it AND tightens it before anything is
        # written. (pytest's tmp_path fixture pre-creates the dir.)
        shutil.rmtree(tmp_config_dir, ignore_errors=True)

        cfg = Config()
        assert cfg.save() is True

        assert order, "save() must invoke _enforce_windows_owner_only_acl on Windows"
        # The config dir must be tightened FIRST — before the lock is
        # acquired and before any file write — so the tmp file created
        # by _secure_atomic_write inherits the owner-only dir DACL.
        assert order[0] == f"acl:{tmp_config_dir}", (
            "Config.save() must tighten the config DIR before acquiring the "
            f"cross-process lock. Expected first ACL call to be {tmp_config_dir}, "
            f"got {order}"
        )
        assert order.index("lock") > order.index(f"acl:{tmp_config_dir}"), (
            "The dir ACL must be enforced before the lock file is opened. "
            f"Got order: {order}"
        )

        # The dir ACL must run ONCE per config dir (only when save()
        # creates the directory): after the first save the dir exists,
        # and re-running the dir-wide icacls on an existing dir is both
        # pointless and unsafe (it poisons open files on
        # Python < 3.11.13).
        cfg.hotkey = "<f9>"  # mark dirty so the second save is real
        assert cfg.save() is True
        assert order.count(f"acl:{tmp_config_dir}") == 1, (
            "The config DIR must be tightened exactly once (only when save() "
            f"creates the directory); got {order}"
        )
