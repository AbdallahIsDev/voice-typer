"""AP-18: regression tests for atomic writes in autostart / prewarm /
sentinel / onboarding-counter / prewarm-log file registration.

Six call sites previously used ``Path.write_text`` (truncate-then-write,
follows symlinks) for OS-level autostart files and crash-recovery
sentinels. They now route through ``_secure_atomic_write`` (temp +
``os.replace``) with ``durability=False`` so a crash mid-write cannot
leave a half-truncated file that the OS / crash-recovery / circuit
breaker mis-parses on next boot.

Each test spies on ``_secure_atomic_write`` (via ``wraps=`` so the real
function still runs and downstream ``chmod`` / file-existence probes
keep working) AND on ``Path.write_text`` (to assert the legacy
non-atomic call was NOT used).

The spy patches the canonical definition in
``voice_typer.server.secure_file_io`` — every call site imports the
helper lazily via ``from voice_typer.server.secure_file_io import
_secure_atomic_write`` so the patched attribute is what the call site
sees at runtime.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import voice_typer.server.secure_file_io as sio
from voice_typer.server import server_platform as platform_mod

# ──────────────────────────────────────────────────────────────────
# 1. macOS autostart plist — _enable_autostart_macos
# ──────────────────────────────────────────────────────────────────


class TestAutostartMacOsAtomicWrite:
    """``_enable_autostart_macos`` must use ``_secure_atomic_write`` for
    the plist (not ``Path.write_text``) and keep the defense-in-depth
    ``chmod(0o600)`` AFTER the atomic write."""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(platform_mod, "SYSTEM", "darwin")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(platform_mod, "_os_uid", lambda: 501)

        # Fake launchctl load (returncode=0, no stderr) so the function
        # returns True without actually invoking launchd.
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
        assert Path(called_path).name == "com.voicetyper.plist"
        # Path.write_text must NOT have been called on the plist path
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == "com.voicetyper.plist":
                pytest.fail("Path.write_text must NOT be used for the plist; use _secure_atomic_write instead")

    def test_chmod_0o600_preserved_after_atomic_write(self, monkeypatch, tmp_path):
        """The defense-in-depth ``plist_path.chmod(0o600)`` must still
        run AFTER the atomic write so the file's final perms are 0o600
        (the atomic write itself creates the temp file with default
        umask, so chmod is needed to tighten)."""
        monkeypatch.setattr(platform_mod, "SYSTEM", "darwin")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(platform_mod, "_os_uid", lambda: 501)

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

        plist_path = tmp_path / "com.voicetyper.plist"
        assert (plist_path).exists(), "plist must be written by _secure_atomic_write"
        # chmod(0o600) must have been called on the plist path.
        assert any(p == plist_path and m == 0o600 for p, m in chmod_calls), (
            f"expected chmod(0o600) on plist; got: {chmod_calls}"
        )


# ──────────────────────────────────────────────────────────────────
# 2. Linux .desktop entry — _enable_autostart_linux
# ──────────────────────────────────────────────────────────────────


class TestAutostartLinuxAtomicWrite:
    """``_enable_autostart_linux`` must use ``_secure_atomic_write`` for
    the ``.desktop`` file and NOT apply ``chmod(0o600)`` (DEs must be
    able to read the file)."""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            platform_mod,
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
        assert Path(called_path).name == "voice-typer.desktop"
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == "voice-typer.desktop":
                pytest.fail("Path.write_text must NOT be used for the .desktop file; use _secure_atomic_write instead")

    def test_no_chmod_0o600_applied(self, monkeypatch, tmp_path):
        """``chmod(0o600)`` must NOT be called on the .desktop file —
        desktop environments must be able to read it."""
        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            platform_mod,
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

        desktop_path = tmp_path / "voice-typer.desktop"
        assert not any(p == desktop_path and m == 0o600 for p, m in chmod_calls), (
            f"chmod(0o600) must NOT be applied to .desktop file; got: {chmod_calls}"
        )


# ──────────────────────────────────────────────────────────────────
# 3. systemd user unit — register_linux_app_service
# ──────────────────────────────────────────────────────────────────


class TestPrewarmLinuxAppServiceAtomicWrite:
    """``register_linux_app_service`` must use ``_secure_atomic_write``
    for the systemd user unit (mirrors the sibling
    ``_register_prewarm_linux`` helper at lines 331-339 which already
    routes through ``_secure_atomic_write(..., durability=False)``)."""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        from voice_typer.server import prewarm_scheduler_posix as psp

        # Force the Linux branch.
        monkeypatch.setattr(psp, "is_linux", lambda: True)
        monkeypatch.setattr(psp, "_linux_unit_dir", lambda: tmp_path)
        monkeypatch.setattr(psp, "_linux_app_service_path", lambda: tmp_path / "voice-typer.service")

        # Stub systemctl daemon-reload so the function doesn't actually
        # shell out.
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
            result = psp.register_linux_app_service()

        assert result is True
        assert spy_atomic.called, "_secure_atomic_write must be called for the systemd user unit"
        assert spy_atomic.call_args.kwargs.get("durability") is False
        called_path = spy_atomic.call_args.args[0]
        assert Path(called_path).name == "voice-typer.service"
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == "voice-typer.service":
                pytest.fail("Path.write_text must NOT be used for the systemd unit; use _secure_atomic_write instead")


# ──────────────────────────────────────────────────────────────────
# 4. dictation-in-flight sentinel — DictationPipeline.run
# ──────────────────────────────────────────────────────────────────


class TestDictationPipelineSentinelAtomicWrite:
    """The ``.dictation-in-flight`` sentinel (consumed by
    ``crash_recovery``) must be written atomically so a crash mid-write
    cannot leave a truncated cycle id that crash_recovery misparses."""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        from voice_typer.server import _paths as paths_mod
        from voice_typer.server import dictation_pipeline as dp
        from voice_typer.server import log as log_mod

        # Force the config dir to tmp_path so the sentinel lands there.
        monkeypatch.setattr(paths_mod, "config_dir", lambda: tmp_path)

        # The .run() method is heavy — short-circuit right after the
        # sentinel write by making set_correlation_id raise. The
        # sentinel write itself is wrapped in contextlib.suppress so
        # the spy returns None (no exception) and execution proceeds
        # to set_correlation_id, which then raises.
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
            pipeline.run(b"audio", 1.0, 0.5, "cycle-7f3a", None)

        assert spy_atomic.called, "_secure_atomic_write must be called for the .dictation-in-flight sentinel"
        assert spy_atomic.call_args.kwargs.get("durability") is False
        called_path, called_content = spy_atomic.call_args.args[:2]
        assert Path(called_path).name == ".dictation-in-flight"
        assert called_content == "cycle-7f3a", "sentinel content must be the str(cycle_id) verbatim"
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == ".dictation-in-flight":
                pytest.fail("Path.write_text must NOT be used for the sentinel; use _secure_atomic_write instead")


# ──────────────────────────────────────────────────────────────────
# 5. onboarding fail counter — _write_onboarding_fail_count
# ──────────────────────────────────────────────────────────────────


class TestOnboardingFailCountAtomicWrite:
    """``_write_onboarding_fail_count`` must use ``_secure_atomic_write``
    so a truncated file does not reset the circuit-breaker counter to 0
    on the next startup."""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        from voice_typer.server import startup_sequence as ss

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
        # payload is JSON; verify the counter fields round-trip.
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
        """Sanity: after the atomic write, ``_read_onboarding_fail_count``
        reads back the same values (no data loss)."""
        from voice_typer.server import startup_sequence as ss

        monkeypatch.setattr(ss, "_config_dir", lambda: tmp_path)

        ss._write_onboarding_fail_count(2, 1700000000.0)

        count, last_fail_ts = ss._read_onboarding_fail_count()
        assert count == 2
        assert last_fail_ts == 1700000000.0


# ──────────────────────────────────────────────────────────────────
# 6. prewarm.log placeholder — _handle_open_prewarm_log
# ──────────────────────────────────────────────────────────────────


class TestPrewarmLogPlaceholderAtomicWrite:
    """``_handle_open_prewarm_log`` must use ``_secure_atomic_write`` for
    the placeholder ``prewarm.log`` file when it doesn't exist yet."""

    def test_uses_secure_atomic_write_with_durability_false(self, monkeypatch, tmp_path):
        from voice_typer.server import config as config_mod
        from voice_typer.server.handlers.status_handlers import StatusHandlersMixin

        # Force the config dir to tmp_path.
        monkeypatch.setattr(config_mod, "_config_dir", lambda: tmp_path)

        # Build a minimal handler instance (the mixin only needs ``self``
        # — _handle_open_prewarm_log doesn't reference self.app/service).
        handler = StatusHandlersMixin.__new__(StatusHandlersMixin)

        # Stub all OS-open branches so the handler returns early after
        # writing the placeholder.
        from voice_typer.server import platform_utils as pu

        monkeypatch.setattr(pu, "is_windows", lambda: False)
        monkeypatch.setattr(pu, "is_macos", lambda: False)
        monkeypatch.setattr(pu, "is_linux", lambda: False)

        real_fn = sio._secure_atomic_write
        with (
            patch.object(sio, "_secure_atomic_write", wraps=real_fn) as spy_atomic,
            patch.object(Path, "write_text", autospec=True) as spy_write_text,
        ):
            resp = {"type": "prewarm_log", "data": {}}
            handler._handle_open_prewarm_log(None, resp)

        assert spy_atomic.called, "_secure_atomic_write must be called for the prewarm.log placeholder"
        assert spy_atomic.call_args.kwargs.get("durability") is False
        called_path = spy_atomic.call_args.args[0]
        assert Path(called_path).name == "prewarm.log"
        for c in spy_write_text.call_args_list:
            self_arg = c.args[0] if c.args else c.kwargs.get("self")
            if self_arg is not None and Path(self_arg).name == "prewarm.log":
                pytest.fail(
                    "Path.write_text must NOT be used for the prewarm.log placeholder; use _secure_atomic_write instead"
                )
