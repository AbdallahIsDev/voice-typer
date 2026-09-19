"""Focused tests for config-path security hardening."""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import MagicMock

import pytest


@contextlib.contextmanager
def _capture_config_warnings():
    """Attach a direct handler to the config logger."""
    logger = logging.getLogger("voice_typer.server.config")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    prev_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


class TestPathTraversalLogsRedactEnvValues:
    """raw env-var value (username / path disclosure in support logs)."""

    pytestmark = pytest.mark.real_config_dir

    def _reset_config_dir(self):
        from voice_typer.server.config_internals.paths import _reset_config_dir_cache

        _reset_config_dir_cache()

    def test_voice_typer_config_dir_traversal_log_is_redacted(self, tmp_path, monkeypatch):
        from voice_typer.server.config_internals import paths as paths_mod

        secret_path = "/etc/../../home/alice/secret-user-dir"
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", secret_path)
        monkeypatch.setattr(
            paths_mod,
            "_validate_path_safety",
            MagicMock(side_effect=ValueError("Path traversal detected")),
        )
        monkeypatch.setattr(paths_mod, "_get_legacy_voice_typer_dir", lambda: tmp_path / "missing-legacy")
        self._reset_config_dir()
        try:
            with _capture_config_warnings() as messages:
                # Call the module-level function directly (the real
                paths_mod._config_dir()
            traversal = [m for m in messages if "traversal" in m.lower()]
            assert traversal, f"expected a path-traversal WARNING, got {messages}"
            for msg in traversal:
                assert secret_path not in msg, f"raw env value leaked into log: {msg!r}"
                assert "<redacted>" in msg, f"expected <redacted> marker in: {msg!r}"
        finally:
            monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
            self._reset_config_dir()

    def test_appdata_traversal_log_is_redacted(self, tmp_path, monkeypatch):
        from voice_typer.server.config_internals import paths as paths_mod

        secret_appdata = r"C:\Users\alice\AppData\Roaming"
        monkeypatch.setenv("APPDATA", secret_appdata)
        monkeypatch.setattr(paths_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(paths_mod, "_is_macos", lambda: False)
        monkeypatch.setattr(paths_mod, "_get_legacy_voice_typer_dir", lambda: tmp_path / "missing-legacy")
        monkeypatch.setattr(
            paths_mod,
            "_validate_path_safety",
            MagicMock(side_effect=ValueError("Path traversal detected")),
        )
        monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
        self._reset_config_dir()
        try:
            with _capture_config_warnings() as messages:
                paths_mod._config_dir()
            appdata_msgs = [m for m in messages if "APPDATA" in m]
            assert appdata_msgs, f"expected an APPDATA path-traversal WARNING, got {messages}"
            for msg in appdata_msgs:
                assert secret_appdata not in msg, f"raw APPDATA leaked into log: {msg!r}"
                assert "<redacted>" in msg, f"expected <redacted> marker in: {msg!r}"
        finally:
            monkeypatch.delenv("APPDATA", raising=False)
            self._reset_config_dir()

    def test_xdg_data_home_traversal_log_is_redacted(self, tmp_path, monkeypatch):
        from voice_typer.server.config_internals import paths as paths_mod

        secret_xdg = "/shared/alice/data"
        monkeypatch.setenv("XDG_DATA_HOME", secret_xdg)
        monkeypatch.setattr(paths_mod, "_is_windows", lambda: False)
        monkeypatch.setattr(paths_mod, "_is_macos", lambda: False)
        monkeypatch.setattr(paths_mod, "_get_legacy_voice_typer_dir", lambda: tmp_path / "missing-legacy")
        monkeypatch.setattr(
            paths_mod,
            "_validate_path_safety",
            MagicMock(side_effect=ValueError("Path traversal detected")),
        )
        monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
        self._reset_config_dir()
        try:
            with _capture_config_warnings() as messages:
                paths_mod._config_dir()
            xdg_msgs = [m for m in messages if "XDG_DATA_HOME" in m]
            assert xdg_msgs, f"expected an XDG_DATA_HOME path-traversal WARNING, got {messages}"
            for msg in xdg_msgs:
                assert secret_xdg not in msg, f"raw XDG_DATA_HOME leaked into log: {msg!r}"
                assert "<redacted>" in msg, f"expected <redacted> marker in: {msg!r}"
        finally:
            monkeypatch.delenv("XDG_DATA_HOME", raising=False)
            self._reset_config_dir()


class _ReusableFakeLock:
    """Reusable no-op stand-in for ``app._config_mutation_lock``."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_service_and_app(tmp_config_dir, monkeypatch):
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    app = MagicMock()
    app._config_mutation_lock = _ReusableFakeLock()
    app.config = Config()
    app.config.audio_preset = "auto"
    app.config.save = MagicMock(return_value=True)
    app.config.save_strict = MagicMock(return_value=None)
    app.clipboard = MagicMock()
    app.tray = MagicMock()
    app.tray.notify = MagicMock()
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


class TestApplyConfigRejectsNonAllowlistedKeys:
    """SEC-002 defense-in-depth: non-allowlisted keys raise, they never"""

    def test_raises_value_error_on_non_allowlisted_key(self, tmp_config_dir, monkeypatch):
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        with pytest.raises(ValueError, match="SEC-002"):
            service.apply_config({"noise_filter_enabled": False})
        # Must NOT have mutated the live Config.
        assert app.config.noise_filter_enabled is True

    def test_raises_on_trusted_path_field(self, tmp_config_dir, monkeypatch):
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        with pytest.raises(ValueError, match="SEC-002"):
            service.apply_config({"schema_version": 99})
        assert app.config.schema_version != 99

    def test_allowlisted_key_still_accepted(self, tmp_config_dir, monkeypatch):
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        # Must not raise.
        service.apply_config({"vad_filter_enabled": False})
        assert app.config.vad_filter_enabled is False


class TestAclEnforcementFailureSurfacing:
    def test_failure_is_recorded_and_not_raise(self, monkeypatch, tmp_path):
        from voice_typer.server import config as config_mod
        from voice_typer.server.config._saving import (
            _enforce_windows_owner_only_acl,
            acl_enforcement_failures,
        )

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setenv("USERNAME", "testuser")
        acl_enforcement_failures.clear()

        import subprocess

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 5
            result.stderr = "Access is denied."
            result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", _fake_run)

        target = tmp_path / "config.json"
        target.write_text("{}")
        assert _enforce_windows_owner_only_acl(target) is False
        assert str(target) in acl_enforcement_failures

    def test_success_clears_failure(self, monkeypatch, tmp_path):
        from voice_typer.server import config as config_mod
        from voice_typer.server.config._saving import (
            _enforce_windows_owner_only_acl,
            acl_enforcement_failures,
        )

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setenv("USERNAME", "testuser")
        monkeypatch.setattr(
            config_mod,
            "_windows_owner_only_acl_verified",
            set(),
        )
        target = tmp_path / "config.json"
        target.write_text("{}")
        acl_enforcement_failures.add(str(target))

        import subprocess

        def _fake_run(cmd, **kwargs):
            return MagicMock(returncode=0, stderr="", stdout="ok")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        assert _enforce_windows_owner_only_acl(target) is True
        assert str(target) not in acl_enforcement_failures

    def test_apply_config_notifies_tray_once_on_acl_failure(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.config_applier as applier_mod
        from voice_typer.server.config._saving import acl_enforcement_failures

        applier_mod._acl_enforcement_failure_notified = False
        acl_enforcement_failures.clear()
        acl_enforcement_failures.add(str(tmp_config_dir / "config.json"))
        try:
            service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
            service.apply_config({"vad_filter_enabled": True})
            assert app.tray.notify.call_count == 1
            # Second apply must NOT re-notify (one-shot per process).
            service.apply_config({"vad_filter_enabled": False})
            assert app.tray.notify.call_count == 1
        finally:
            applier_mod._acl_enforcement_failure_notified = False
            acl_enforcement_failures.clear()

    def test_apply_config_does_not_notify_when_no_failure(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.config_applier as applier_mod
        from voice_typer.server.config._saving import acl_enforcement_failures

        applier_mod._acl_enforcement_failure_notified = False
        acl_enforcement_failures.clear()
        try:
            service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
            service.apply_config({"vad_filter_enabled": True})
            app.tray.notify.assert_not_called()
        finally:
            applier_mod._acl_enforcement_failure_notified = False


class TestCrashRecoveryWindowsAcl:
    def test_save_sync_applies_acl_on_windows(self, monkeypatch, tmp_path):
        from voice_typer.server import crash_recovery as facade

        monkeypatch.setattr(facade, "is_windows", lambda: True)

        acl_targets: list = []

        def _fake_acl(path):
            acl_targets.append(str(path))
            return True

        monkeypatch.setattr(
            "voice_typer.server.config._enforce_windows_owner_only_acl",
            _fake_acl,
        )

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=tmp_path)
        cr.add("secret dictated text", pasted=False)
        cr._save_sync()
        assert any(t.endswith("recovery.json") for t in acl_targets), (
            f"expected ACL enforcement on recovery.json, got {acl_targets}"
        )

    def test_quarantine_applies_acl_on_windows(self, monkeypatch, tmp_path):
        from voice_typer.server import crash_recovery as facade

        monkeypatch.setattr(facade, "is_windows", lambda: True)

        # Write a corrupt recovery file first.
        recovery_path = tmp_path / "recovery.json"
        recovery_path.write_text("{not valid json")

        acl_targets: list = []

        def _fake_acl(path):
            acl_targets.append(str(path))
            return True

        monkeypatch.setattr(
            "voice_typer.server.config._enforce_windows_owner_only_acl",
            _fake_acl,
        )

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=tmp_path)
        cr._quarantine_corrupt()
        assert any(".corrupt." in t for t in acl_targets), (
            f"expected ACL enforcement on quarantine destination, got {acl_targets}"
        )

    def test_acl_helper_noop_on_non_windows(self, monkeypatch, tmp_path):
        from voice_typer.server import crash_recovery as facade

        monkeypatch.setattr(facade, "is_windows", lambda: False)
        called = {"n": 0}

        def _fake_acl(path):
            called["n"] += 1
            return True

        monkeypatch.setattr(
            "voice_typer.server.config._enforce_windows_owner_only_acl",
            _fake_acl,
        )

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=tmp_path)
        cr.add("text", pasted=False)
        cr._save_sync()
        assert called["n"] == 0, "ACL helper must not run on non-Windows"
