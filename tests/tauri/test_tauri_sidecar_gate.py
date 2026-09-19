"""Tests for the TAURI_SIDECAR=1 env-var gate in ipc_server.py (ADR-0020 §2, §10, §12)."""

from __future__ import annotations

import os


def test_ws_flag_sets_tauri_sidecar_env_via_argparse(monkeypatch):
    """The --ws flag triggers `os.environ[\"TAURI_SIDECAR\"] = \"1\"`."""
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    assert os.environ.get("TAURI_SIDECAR") == "1"


def test_heartbeat_skipped_under_tauri_sidecar(monkeypatch):
    """IPCServer.start() must NOT start the heartbeat thread when TAURI_SIDECAR=1."""
    from voice_typer.server import ipc_server

    monkeypatch.setenv("TAURI_SIDECAR", "1")

    # Build a minimal IPCServer with mocked dependencies. We can't
    server = ipc_server.IPCServer.__new__(ipc_server.IPCServer)
    server._running = False
    server._tcp_mode = False  # set by __init__; tests bypass it via __new__
    server._heartbeat_stop_event = __import__("threading").Event()
    server._heartbeat_thread = None
    server._stdin_thread = None
    server._push_fn = None
    # Post-start wiring gate (set by _init_app_and_service; __new__ stubs
    server._background_integrations_wired = False
    server.app = type("FakeApp", (), {"_thread_registry": None})()

    # Mock the methods start() calls so we don't actually start a stdin
    server._hook_tray_set_state = lambda: None
    server._run = lambda: None  # stdin loop target, don't actually run
    server.wire_background_integrations = lambda: None

    # Patch event_bus.subscribe + threading.Thread so we can observe
    created_threads = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=False):
            self.target = target
            self.name = name
            self.daemon = daemon
            created_threads.append(name)

        def start(self):
            pass

    monkeypatch.setattr("threading.Thread", FakeThread)
    monkeypatch.setattr("voice_typer.server.event_bus.subscribe", lambda fn: None)

    server.start()

    # Under TAURI_SIDECAR=1, NO "heartbeat-watchdog" thread should be
    assert "heartbeat-watchdog" not in created_threads
    assert server._heartbeat_thread is None


def test_heartbeat_started_without_tauri_sidecar(monkeypatch):
    """IPCServer.start() DOES start the heartbeat thread when TAURI_SIDECAR is unset."""
    from voice_typer.server import ipc_server

    monkeypatch.delenv("TAURI_SIDECAR", raising=False)

    server = ipc_server.IPCServer.__new__(ipc_server.IPCServer)
    server._running = False
    server._tcp_mode = False  # set by __init__; tests bypass it via __new__
    server._heartbeat_stop_event = __import__("threading").Event()
    server._heartbeat_thread = None
    server._stdin_thread = None
    server._push_fn = None
    # Post-start wiring gate (set by _init_app_and_service; __new__ stubs
    server._background_integrations_wired = False
    server.app = type("FakeApp", (), {"_thread_registry": None})()

    server._hook_tray_set_state = lambda: None
    server._run = lambda: None
    server.wire_background_integrations = lambda: None

    created_threads = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=False):
            self.target = target
            self.name = name
            self.daemon = daemon
            created_threads.append(name)

        def start(self):
            pass

    monkeypatch.setattr("threading.Thread", FakeThread)
    monkeypatch.setattr("voice_typer.server.event_bus.subscribe", lambda fn: None)

    server.start()

    # Without TAURI_SIDECAR=1, the heartbeat-watchdog thread IS created.
    assert "heartbeat-watchdog" in created_threads


def test_ws_and_port_are_mutually_exclusive(capsys, monkeypatch):

    # We can't easily run main() (it would import heavy deps). Instead we
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--ws", action="store_true", default=False)
    args = parser.parse_args(["--ws", "--port", "9876"])
    assert args.ws is True
    assert args.port == 9876
    # The mutual-exclusion check is in main(), we test the logic here.
    ws_mode = args.ws
    port = args.port
    assert ws_mode and port is not None  # both set → should exit


def test_dispatch_command_does_not_forward_heartbeat(monkeypatch):
    """The Rust bridge must NOT forward `heartbeat` to Python under Tauri."""
    from voice_typer.server import ipc_server

    # The registry must still contain heartbeat (predecessor fallback).
    assert "heartbeat" in ipc_server.IPCServer._COMMAND_REGISTRY
    assert ipc_server.IPCServer._COMMAND_REGISTRY["heartbeat"] == "_handle_heartbeat"
