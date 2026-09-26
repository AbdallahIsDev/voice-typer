"""AP-18: regression tests for atomic writes in autostart / prewarm /"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import voice_typer.server.secure_file_io as sio
from voice_typer.server import server_platform as platform_mod
from voice_typer.server.server_platform import (
    autostart as autostart_mod,
    autostart_macos as macos_mod,
    platform_flags as flags_mod,
)


class TestAutostartMacOsAtomicWrite:
    """the plist (not ``Path.write_text``) and keep the defense-in-depth"""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(flags_mod, "SYSTEM", "darwin")
        monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(macos_mod, "_os_uid", lambda: 501)

        # Fake launchctl load (returncode=0, no stderr) so the function
        def _fake_run(args, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = b""
            r.stderr = b""
            return r

        monkeypatch.setattr(subprocess, "run", _fake_run)

        real_fn = sio._secure_atomic_write
        with (
            patch.object(sio, "_secure_atomic_write", wraps=real_fn) as spy_atomic,
            patch.object(Path, "write_text", autospec=True) as spy_write_text,
        ):
            result = platform_mod._enable_autostart_macos()

        assert result is True, "macOS autostart enable should succeed with faked launchctl"
        # _secure_atomic_write called with durability=False
        assert spy_atomic.called, "_secure_atomic_write must be called for the plist"
        call_kwargs = spy_atomic.call_args.kwargs
        assert call_kwargs.get("durability") is False, (
            "macOS autostart plist must be written with durability=False "
            "(matches the existing prewarm/autostart pattern)"
        )
        called_path = spy_atomic.call_args.args[0]
        assert Path(called_path).name == "com.Lausu.plist"
        # Path.write_text must NOT have been called on the plist path
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == "com.Lausu.plist":
                pytest.fail("Path.write_text must NOT be used for the plist; use _secure_atomic_write instead")

    def test_chmod_0o600_preserved_after_atomic_write(self, monkeypatch, tmp_path):
        """The defense-in-depth ``plist_path.chmod(0o600)`` must still"""
        monkeypatch.setattr(flags_mod, "SYSTEM", "darwin")
        monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(macos_mod, "_os_uid", lambda: 501)

        def _fake_run(args, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = b""
            r.stderr = b""
            return r

        monkeypatch.setattr(subprocess, "run", _fake_run)

        chmod_calls: list[tuple[Path, int]] = []
        real_chmod = Path.chmod

        def _spy_chmod(self_path, mode):
            chmod_calls.append((self_path, mode))
            return real_chmod(self_path, mode)

        monkeypatch.setattr(Path, "chmod", _spy_chmod)

        assert platform_mod._enable_autostart_macos() is True

        plist_path = tmp_path / "com.Lausu.plist"
        assert (plist_path).exists(), "plist must be written by _secure_atomic_write"
        assert any(p == plist_path and m == 0o600 for p, m in chmod_calls), (
            f"expected chmod(0o600) on plist; got: {chmod_calls}"
        )


class TestAutostartLinuxAtomicWrite:
    """the ``.desktop`` file and NOT apply ``chmod(0o600)`` (DEs must be"""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(flags_mod, "SYSTEM", "linux")
        monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            autostart_mod,
            "_autostart_command",
            lambda: '"/usr/bin/python3" "/opt/voice_typer/launcher.py"',
        )

        real_fn = sio._secure_atomic_write
        with (
            patch.object(sio, "_secure_atomic_write", wraps=real_fn) as spy_atomic,
            patch.object(Path, "write_text", autospec=True) as spy_write_text,
        ):
            result = platform_mod._enable_autostart_linux()

        assert result is True
        assert spy_atomic.called, "_secure_atomic_write must be called for the .desktop"
        assert spy_atomic.call_args.kwargs.get("durability") is False
        called_path = spy_atomic.call_args.args[0]
        assert Path(called_path).name == "lausu.desktop"
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == "lausu.desktop":
                pytest.fail("Path.write_text must NOT be used for the .desktop file; use _secure_atomic_write instead")

    def test_no_chmod_0o600_applied(self, monkeypatch, tmp_path):
        """``chmod(0o600)`` must NOT be called on the .desktop file —"""
        monkeypatch.setattr(flags_mod, "SYSTEM", "linux")
        monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            autostart_mod,
            "_autostart_command",
            lambda: '"/usr/bin/python3" "/opt/voice_typer/launcher.py"',
        )

        chmod_calls: list[tuple[Path, int]] = []
        real_chmod = Path.chmod

        def _spy_chmod(self_path, mode):
            chmod_calls.append((self_path, mode))
            return real_chmod(self_path, mode)

        monkeypatch.setattr(Path, "chmod", _spy_chmod)

        assert platform_mod._enable_autostart_linux() is True

        desktop_path = tmp_path / "lausu.desktop"
        assert not any(p == desktop_path and m == 0o600 for p, m in chmod_calls), (
            f"chmod(0o600) must NOT be applied to .desktop file; got: {chmod_calls}"
        )


class TestDictationPipelineSentinelAtomicWrite:
    """``crash_recovery``) must be written atomically so a crash mid-write"""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        from voice_typer.server import _paths as paths_mod, dictation_pipeline as dp, log as log_mod

        # Force the config dir to tmp_path so the sentinel lands there.
        monkeypatch.setattr(paths_mod, "config_dir", lambda: tmp_path)

        def _boom(cid):
            raise RuntimeError("test-short-circuit")

        monkeypatch.setattr(log_mod, "set_correlation_id", _boom)

        app = MagicMock()
        pipeline = dp.DictationPipeline(app)

        real_fn = sio._secure_atomic_write
        with (
            patch.object(sio, "_secure_atomic_write", wraps=real_fn) as spy_atomic,
            patch.object(Path, "write_text", autospec=True) as spy_write_text,
            pytest.raises(RuntimeError, match="test-short-circuit"),
        ):
            pipeline.run(b"audio", 1.0, 0.5, "cycle-7f3a")

        assert spy_atomic.called, "_secure_atomic_write must be called for the .dictation-in-flight sentinel"
        assert spy_atomic.call_args.kwargs.get("durability") is False
        called_path, called_content = spy_atomic.call_args.args[:2]
        assert Path(called_path).name == ".dictation-in-flight"
        assert called_content == "cycle-7f3a", "sentinel content must be the str(cycle_id) verbatim"
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == ".dictation-in-flight":
                pytest.fail("Path.write_text must NOT be used for the sentinel; use _secure_atomic_write instead")


class TestOnboardingFailCountAtomicWrite:
    """``_write_onboarding_fail_count`` must use ``_secure_atomic_write``"""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        from voice_typer.server.startup_sequence import _phases_early as ss

        status_path = tmp_path / ".onboarding_status.json"
        monkeypatch.setattr(ss, "_config_dir", lambda: tmp_path)

        real_fn = sio._secure_atomic_write
        with (
            patch.object(sio, "_secure_atomic_write", wraps=real_fn) as spy_atomic,
            patch.object(Path, "write_text", autospec=True) as spy_write_text,
        ):
            ss._write_onboarding_fail_count(3, 1700000000.0)

        assert spy_atomic.called, "_secure_atomic_write must be called for the onboarding fail counter"
        assert spy_atomic.call_args.kwargs.get("durability") is False
        called_path, called_content = spy_atomic.call_args.args[:2]
        assert Path(called_path) == status_path
        import json

        payload = json.loads(called_content)
        assert payload["fail_count"] == 3
        assert payload["last_fail_ts"] == 1700000000.0
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg) == status_path:
                pytest.fail(
                    "Path.write_text must NOT be used for the onboarding counter; use _secure_atomic_write instead"
                )

    def test_persistence_round_trips(self, monkeypatch, tmp_path):
        """Sanity: after the atomic write, ``_read_onboarding_fail_count``"""
        from voice_typer.server.startup_sequence import _phases_early as ss

        monkeypatch.setattr(ss, "_config_dir", lambda: tmp_path)

        ss._write_onboarding_fail_count(2, 1700000000.0)

        count, last_fail_ts = ss._read_onboarding_fail_count()
        assert count == 2
        assert last_fail_ts == 1700000000.0
