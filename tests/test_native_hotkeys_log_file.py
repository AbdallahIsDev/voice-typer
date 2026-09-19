"""Tests for the ``--log-file`` wiring in the native hotkey spawn command."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import native_hotkeys
from voice_typer.server.native_hotkeys import LinuxEvdevHotkey


def _setup_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")


def _fake_binary(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-native"
    fake_bin.write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
    fake_bin.chmod(0o755)
    return fake_bin


def _patch_binary_path(monkeypatch: pytest.MonkeyPatch, fake_bin: Path | None) -> None:
    """Patch both the binary_path module AND the base module's"""
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.get_native_binary_path",
        lambda: fake_bin,
    )
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.base.get_native_binary_path",
        lambda: fake_bin,
    )


def _patch_verify_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """verifier LOCALLY from ``binary_path``, so the module attribute (not"""
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
        lambda _p: True,
    )


def _spawn_and_capture_cmd(backend: LinuxEvdevHotkey) -> list[str]:
    """Run ``_spawn_process`` with Popen stubbed; return the argv it got."""
    popen_calls: list = []
    with patch(
        "subprocess.Popen",
        lambda *a, **k: popen_calls.append((a, k)) or MagicMock(),
    ):
        backend._spawn_process()
    # The cmd is positional argv[0] of the Popen call.
    return list(popen_calls[0][0][0])


class TestSpawnCommandIncludesLogFile:
    def test_cmd_has_log_file_flag_after_hotkey_spec(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)
        # Hermetic config dir: the stable log lands under tmp logs.
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        _paths_mod._reset_config_dir_cache()
        request.addfinalizer(_paths_mod._reset_config_dir_cache)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)

        b = LinuxEvdevHotkey("<caps_lock>")
        try:
            cmd = _spawn_and_capture_cmd(b)
        finally:
            with __import__("contextlib").suppress(Exception):
                b.stop()

        expected_log = tmp_path / "logs" / "native-linux.log"
        assert cmd[:2] == [str(fake_bin), "<caps_lock>"]
        assert cmd[2:] == ["--log-file", str(expected_log)], (
            f"spawn command must append --log-file with the resolved path; got {cmd!r}"
        )
        assert expected_log.parent.is_dir()
        assert b._native_log_path == expected_log

    def test_flag_omitted_when_home_unresolvable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)

        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setattr(_spawn_mod, "_resolve_canonical_logs_dir", lambda: None)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)

        b = LinuxEvdevHotkey("<caps_lock>")
        try:
            cmd = _spawn_and_capture_cmd(b)
        finally:
            with __import__("contextlib").suppress(Exception):
                b.stop()

        assert cmd == [str(fake_bin), "<caps_lock>"], (
            f"spawn command must omit --log-file entirely when the log path is unresolvable; got {cmd!r}"
        )
        assert b._native_log_path is None

    def test_path_computation_failure_does_not_break_spawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Diagnostics must never block the hotkey: an unexpected"""
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)

        def _exploding_path() -> Path | None:
            raise AssertionError("unexpected failure in log-path computation")

        b = LinuxEvdevHotkey("<caps_lock>")
        monkeypatch.setattr(b, "_compute_native_log_path", _exploding_path)
        try:
            cmd = _spawn_and_capture_cmd(b)
        finally:
            with __import__("contextlib").suppress(Exception):
                b.stop()

        assert cmd == [str(fake_bin), "<caps_lock>"], (
            f"spawn must still happen (without --log-file) when the log-path computation raises; got {cmd!r}"
        )

    def test_memoised_path_reused_across_respawns(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request
    ) -> None:
        """The watchdog respawns via ``stop()`` + ``start()`` →"""
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        _paths_mod._reset_config_dir_cache()
        request.addfinalizer(_paths_mod._reset_config_dir_cache)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)

        popen_cmds: list[list[str]] = []
        b = LinuxEvdevHotkey("<caps_lock>")
        with patch(
            "subprocess.Popen",
            lambda *a, **k: popen_cmds.append(list(a[0])) or MagicMock(),
        ):
            try:
                b._spawn_process()
                b._process = None  # reset so the second spawn doesn't bail
                b._failed = False
                b._spawn_process()
            except Exception:
                pass
            finally:
                with __import__("contextlib").suppress(Exception):
                    b.stop()

        assert len(popen_cmds) == 2
        assert popen_cmds[0][2:] == popen_cmds[1][2:] == ["--log-file", str(b._native_log_path)]


class TestStableNameAndLegacySweep:
    def test_stable_name_has_no_pid(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request) -> None:
        """The stable name is ``native-<backend>.log`` with no PID suffix,"""
        _setup_linux(monkeypatch)
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        _paths_mod._reset_config_dir_cache()
        request.addfinalizer(_paths_mod._reset_config_dir_cache)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)

        b = LinuxEvdevHotkey("<caps_lock>")
        try:
            path = b._compute_native_log_path()
        finally:
            with __import__("contextlib").suppress(Exception):
                b.stop()
        assert path is not None
        assert path.name == "native-linux.log"

    def test_legacy_per_pid_orphans_swept(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request) -> None:
        """Legacy ``native-<backend>-<pid>.log`` files in the canonical"""
        _setup_linux(monkeypatch)
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        _paths_mod._reset_config_dir_cache()
        request.addfinalizer(_paths_mod._reset_config_dir_cache)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        orphans = [
            logs_dir / "native-windows-2124.log",
            logs_dir / "native-linux-2980.log",
            logs_dir / "native-macos-8176.log",
        ]
        for o in orphans:
            o.write_text("legacy orphan", encoding="utf-8")
        stable = logs_dir / "native-linux.log"
        stable.write_text("current", encoding="utf-8")

        b = LinuxEvdevHotkey("<caps_lock>")
        try:
            path = b._compute_native_log_path()
        finally:
            with __import__("contextlib").suppress(Exception):
                b.stop()

        assert path == stable
        for o in orphans:
            assert not o.exists(), f"legacy orphan {o.name} must be swept"
        assert stable.exists()
        assert stable.read_text(encoding="utf-8") == "current"

    def test_sweep_ignores_non_legacy_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The sweep only deletes per-PID matches; unrelated logs survive."""
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        keepers = [logs_dir / "voice-typer.log", logs_dir / "native-linux.log"]
        for k in keepers:
            k.write_text("keep", encoding="utf-8")
        legacy = logs_dir / "native-windows-17068.log"
        legacy.write_text("orphan", encoding="utf-8")
        near_miss = logs_dir / "native-windows-beta.log"
        near_miss.write_text("keep", encoding="utf-8")

        monkeypatch.setattr(_spawn_mod, "_resolve_canonical_logs_dir", lambda: logs_dir)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)
        _spawn_mod._sweep_legacy_per_pid_logs(keep=logs_dir / "native-linux.log")

        assert not legacy.exists()
        for k in keepers + [near_miss]:
            assert k.exists()
