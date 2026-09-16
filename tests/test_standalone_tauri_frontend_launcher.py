"""Tests for the standalone-mode Tauri frontend launcher (MO-110).

When the backend runs standalone (``voice-typer`` from a terminal), it
auto-picks a port, generates a session token, and launches the frontend
host with the adopt env so the host CONNECTS to the running backend
instead of spawning a second one. Under a Tauri install the frontend is
the native ``voice-typer-tauri`` binary, launched via
``tauri_spawn.launch_tauri_frontend_standalone``:

- the SAME ``VT_PYTHON_PORT`` / ``VT_IPC_TOKEN`` /
  ``VOICE_TYPER_IPC_TOKEN`` env trio the Electron launcher exports
  (``electron_launcher.launch_electron_frontend``), consumed by the
  host's ``adopted_backend_env`` helper (``sidecar/spawn.rs``);
- the fail-closed ``verify_tauri_binary_or_skip`` integrity gate BEFORE
  spawning (a tampered binary must never be launched);
- ``None`` on spawn failure so the caller falls back to the Electron
  launcher path.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest
from voice_typer.server.autostart import tauri_spawn

_TAURI_LOG_FILES_STUB = {
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "stdin": subprocess.DEVNULL,
}


@pytest.fixture(autouse=True)
def _bypass_integrity_gate(monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
        lambda path: True,
    )


def test_standalone_launcher_exports_adopt_env(monkeypatch):
    """The launcher must export the full adopt trio (port + both token
    forms) so the Rust host's ``adopted_backend_env`` resolves it."""
    captured: dict = {}

    def fake_popen(cmd, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = dict(env or {})
        return MagicMock(pid=4242)

    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher._tauri_log_files",
        lambda: dict(_TAURI_LOG_FILES_STUB),
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    pid = tauri_spawn.launch_tauri_frontend_standalone(
        "C:/fake/voice-typer-tauri.exe", port=9911, token="tok-standalone"
    )

    assert pid == 4242
    assert captured["env"]["VT_PYTHON_PORT"] == "9911"
    assert captured["env"]["VT_IPC_TOKEN"] == "tok-standalone"
    assert captured["env"]["VOICE_TYPER_IPC_TOKEN"] == "tok-standalone"
    # The single-instance plugin owns focus/fresh-start under Tauri; no
    # VT_FOCUS_ONLY-style lean-spawn flags on this path.
    assert "VT_FOCUS_ONLY" not in captured["env"]


def test_standalone_launcher_fails_closed_on_bad_binary(monkeypatch):
    """A binary that fails integrity verification must NOT be spawned
    (fail-closed) and the launcher returns None so the caller can fall
    back to the Electron path."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
        lambda path: False,
    )
    spawned = []

    def fake_popen(*args, **kwargs):
        spawned.append(args)
        return MagicMock(pid=1)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    pid = tauri_spawn.launch_tauri_frontend_standalone("C:/fake/tampered.exe", port=9911, token="tok")

    assert pid is None
    assert spawned == [], "spawn must never be attempted for a failing binary"


def test_standalone_launcher_returns_none_on_spawn_failure(monkeypatch):
    """A Popen failure (missing binary, perms) must return None, not
    raise into the backend's startup path."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher._tauri_log_files",
        lambda: dict(_TAURI_LOG_FILES_STUB),
    )

    def fake_popen(*args, **kwargs):
        raise FileNotFoundError("binary vanished")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    pid = tauri_spawn.launch_tauri_frontend_standalone("C:/fake/voice-typer-tauri.exe", port=9911, token="tok")

    assert pid is None
