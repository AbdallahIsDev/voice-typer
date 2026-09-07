"""Tests for the ``--log-file`` wiring in the native hotkey spawn command.

All three native key-listener binaries (linux / windows / macos) parse a
``--log-file <path>`` flag and append timestamped diagnostic lines (init
steps, permission checks, device opens, hook installation) to that file.
``_SpawnMixin._spawn_process`` resolves the per-session log path via
``_compute_native_log_path`` (memoised, ``~/.voice-typer/logs/
native-<backend>-<pid>.log``) and appends ``["--log-file", path]`` to the
spawn command. The wiring is error-tolerant: when the path cannot be
resolved (no home, read-only home) — or its computation raises — the
binary is spawned WITHOUT the flag rather than failing the spawn.

These tests pin:
  1. the spawn command includes ``--log-file <resolved path>`` after the
     hotkey spec;
  2. the flag is omitted when the log path is unresolvable;
  3. an unexpected exception in the path computation does NOT break the
     spawn (spawn proceeds without the flag);
  4. the memoised path is reused across respawns (same flag value).
"""

from __future__ import annotations

import os
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
    """Patch both the binary_path module AND the base module's
    ``get_native_binary_path`` binding (the base module imports it via
    ``from .binary_path import get_native_binary_path``)."""
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.get_native_binary_path",
        lambda: fake_bin,
    )
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.base.get_native_binary_path",
        lambda: fake_bin,
    )


def _patch_verify_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the SHA-256 gate pass. ``_spawn_process`` imports the
    verifier LOCALLY from ``binary_path``, so the module attribute (not
    the package attribute) is the binding that must be patched."""
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
    def test_cmd_has_log_file_flag_after_hotkey_spec(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)
        # Redirect home to the test's tmp dir so the test never touches
        # the real user home.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        b = LinuxEvdevHotkey("<caps_lock>")
        try:
            cmd = _spawn_and_capture_cmd(b)
        finally:
            with __import__("contextlib").suppress(Exception):
                b.stop()

        expected_log = tmp_path / ".voice-typer" / "logs" / f"native-linux-{os.getpid()}.log"
        assert cmd[:2] == [str(fake_bin), "<caps_lock>"]
        assert cmd[2:] == ["--log-file", str(expected_log)], (
            f"spawn command must append --log-file with the resolved path; got {cmd!r}"
        )
        # The log directory is created eagerly so the binary can open the
        # file with fopen("a") on startup.
        assert expected_log.parent.is_dir()
        assert b._native_log_path == expected_log

    def test_flag_omitted_when_home_unresolvable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)

        def _no_home() -> Path:
            raise RuntimeError("home directory could not be resolved")

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: _no_home()))

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
        """Diagnostics must never block the hotkey: an unexpected
        exception from the path computation is logged at debug level and
        the spawn proceeds WITHOUT the flag."""
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

    def test_memoised_path_reused_across_respawns(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The watchdog respawns via ``stop()`` + ``start()`` →
        ``_spawn_process()``; the memoised path keeps every respawn of
        one backend appending to the same log file."""
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        _patch_verify_ok(monkeypatch)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

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
