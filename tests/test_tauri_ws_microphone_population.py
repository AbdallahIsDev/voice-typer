"""Regression pins for the Tauri ``--ws`` sidecar microphone-list gap."""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_MIC_LIST = "voice_typer.server.server_platform.microphone_list"

_AUTH_TOKEN = "test-token-" + "a" * 32

_DEVICES = [
    {
        "index": 0,
        "name": "Mic Alpha",
        "max_input_channels": 2,
        "hostapi": 0,
        "default_samplerate": 48000,
    },
    {
        "index": 1,
        "name": "Mic Beta",
        "max_input_channels": 1,
        "hostapi": 0,
        "default_samplerate": 44100,
    },
]
_HOSTAPIS = [{"name": "PulseAudio", "default_input_device": 0}]


def _install_fake_sounddevice(monkeypatch) -> None:
    """Install a working fake ``sounddevice`` with the module devices."""
    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = lambda *args, **kwargs: (
        _DEVICES[0] if kwargs.get("kind") == "input" else _DEVICES
    )
    fake_sd.query_hostapis.return_value = _HOSTAPIS
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)


@pytest.fixture
def ws_mode_app(tmp_config_dir, monkeypatch):
    """A ``LausuApp`` shaped exactly like the ``--ws`` sidecar's."""
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _AUTH_TOKEN)
    from voice_typer.server.app import LausuApp

    return LausuApp()


@pytest.fixture(autouse=True)
def _reset_mic_cache():
    """Reset the microphone-list TTL cache around every test."""
    from voice_typer.server.server_platform import microphone_list as _ml

    _ml.invalidate_microphone_list_cache()
    yield
    _ml.invalidate_microphone_list_cache()


def _build_ws_mode_server(app):
    """Build + start the IPC server exactly like entrypoint's ws branch."""
    from voice_typer.server.providers import build_ipc_server

    server = build_ipc_server(app)
    server._tcp_mode = True
    server.start()
    return server


class TestWsSidecarMicPopulation:
    """The fixed contract: the ws-mode process shape populates the list."""

    def test_ws_mode_shape_serves_devices_to_renderer(self, ws_mode_app, monkeypatch):
        """Renderer-visible outcome of the FIXED ws-mode shape: devices."""
        import threading
        import time

        _install_fake_sounddevice(monkeypatch)
        server = _build_ws_mode_server(ws_mode_app)
        # Mirror the fixed entrypoint: the ws branch launches the app
        _startup = threading.Thread(
            target=ws_mode_app._do_startup,
            name="ws-sidecar-startup",
            daemon=True,
        )
        _startup.start()
        try:
            _deadline = time.monotonic() + 10.0
            while not getattr(ws_mode_app, "_microphones", None):
                if time.monotonic() > _deadline:
                    raise AssertionError(
                        "ws-mode startup background work did not populate "
                        "app._microphones within 10s (renderer-visible defect "
                        "would return: empty Microphone page)"
                    )
                time.sleep(0.05)
            resp: dict = {}
            server._handle_get_microphones(None, resp)
            assert resp["type"] == "microphones"
            assert resp["data"] != [], (
                "ws-mode process shape must serve app._microphones verbatim; "
                "the startup background work populates it so the renderer's "
                "Microphone page lists the devices"
            )
        finally:
            server.stop()

    def test_enumeration_works_in_ws_mode_shape(self, ws_mode_app, monkeypatch):
        """Control: the enumeration itself is NOT the failure."""
        from voice_typer.server.server_platform import microphone_list as _ml

        _install_fake_sounddevice(monkeypatch)
        mics = _ml.list_microphones()
        assert len(mics) == 2
        assert {m["name"] for m in mics} == {"Mic Alpha", "Mic Beta"}


class TestWsSidecarMicPopulationControl:
    """The missing link: running the phase-6 task populates everything."""

    def test_load_microphones_makes_list_renderer_visible(self, ws_mode_app, monkeypatch):
        """Running the exact phase-6 task heals the renderer-visible list."""
        from voice_typer.server import startup_tasks

        _install_fake_sounddevice(monkeypatch)
        server = _build_ws_mode_server(ws_mode_app)
        try:
            before: dict = {}
            server._handle_get_microphones(None, before)
            assert before["data"] == []

            startup_tasks.load_microphones(ws_mode_app)

            after: dict = {}
            server._handle_get_microphones(None, after)
            assert len(after["data"]) == 2, "after load_microphones the handler must serve the enumerated devices"
            assert {m["name"] for m in after["data"]} == {"Mic Alpha", "Mic Beta"}
        finally:
            server.stop()


class TestWsSidecarMicRoundTrip:
    """Full WebSocket transport round-trip (the path the Tauri host uses)."""

    @staticmethod
    async def _recv_until_id(client, request_id: int, timeout: float = 5.0) -> dict:
        """Receive frames until the dispatch response with *request_id*."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no response frame with id={request_id}")
            raw = await asyncio.wait_for(client.recv(), timeout=remaining)
            frame = json.loads(raw)
            if isinstance(frame, dict) and frame.get("id") == request_id:
                return frame

    async def test_get_microphones_round_trip_empty_then_populated(self, ws_mode_app, monkeypatch):
        """End-to-end over the real WS sidecar transport."""
        from voice_typer.server import sidecar_ws, startup_tasks

        _install_fake_sounddevice(monkeypatch)
        server = _build_ws_mode_server(ws_mode_app)
        dispatch = sidecar_ws._make_dispatch(server)

        async def _handler(ws):
            await sidecar_ws._handle_connection(ws, server, dispatch)

        try:
            import websockets.asyncio.server as ws_server

            async with ws_server.serve(_handler, "127.0.0.1", 0) as srv:
                port = srv.sockets[0].getsockname()[1]
                async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(json.dumps({"type": "auth", "token": _AUTH_TOKEN}))

                    await client.send(json.dumps({"type": "get_microphones", "data": {}, "id": 101}))
                    resp1 = await self._recv_until_id(client, 101)
                    assert resp1["type"] == "microphones"
                    assert resp1["data"] == [], (
                        "ws transport must serve app._microphones verbatim; before "
                        "the startup background work's first population the list "
                        "is [] (the renderer's boot-race retry backoff covers it)"
                    )

                    # The one missing link, the phase-6 population task.
                    startup_tasks.load_microphones(ws_mode_app)

                    await client.send(json.dumps({"type": "get_microphones", "data": {}, "id": 102}))
                    resp2 = await self._recv_until_id(client, 102)
                    assert len(resp2["data"]) == 2
                    assert {m["name"] for m in resp2["data"]} == {"Mic Alpha", "Mic Beta"}
        finally:
            server.stop()
