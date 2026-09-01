"""Regression pins for the Tauri ``--ws`` sidecar microphone-list gap.

USER-VISIBLE DEFECT (Windows host report, 2026-08-30): inside the Tauri
app the Microphone page is completely empty — no microphones are listed
— while the same machine's Electron app and the OS list them fine.

ROOT CAUSE (code-verified):

``ipc/entrypoint.py:main()``'s ``--ws`` (Tauri sidecar) branch calls
``sidecar_ws.run(server)`` and then ``sys.exit(_ws_exit)`` — it NEVER
reaches ``app.start()`` at the bottom of the loop. ``app.start()`` is
the only caller of ``tray.start(bg_work=self._do_startup)``
(app.py:1092), which is the only launcher of the
``StartupSequence`` (app.py:1137) — and its phase 6
(``startup_sequence/_phases_late.py``) is the only production caller of
``startup_tasks.load_microphones``, the only writer of
``app._microphones`` at startup. The ``refresh_microphones`` IPC route
was removed during the Tauri migration, so NOTHING in the ws-mode
process ever populates the microphone registry: ``get_microphones``
serves ``app._microphones`` verbatim and therefore always answers
``[]``. The renderer's empty-list retry backoff (1/2/4/8 s in
``useMicrophoneData.ts``) exhausts and the page stays permanently
empty. The Electron host spawns the backend WITHOUT ``--ws`` (TCP
``--port`` path), ``app.start()`` runs, phase 6 populates the list,
and the page works — the exact divergence the user reported.

These tests pin the causal chain so the fix (run the startup
background work in the ``--ws`` entrypoint branch) is verifiable:

1. The ws-mode process shape — built EXACTLY the way the FIXED
   ``ipc/entrypoint.py`` builds it (``VoiceTyperApp`` →
   ``build_ipc_server`` → ``server._tcp_mode = True`` →
   ``server.start()`` → startup background work launched on the
   ws-sidecar-startup daemon thread) — serves the POPULATED device
   list to the renderer once phase 6 completes.
2. The population flows over both the handler and the real WebSocket
   transport; before the first population the list is ``[]`` (the
   pre-first-population snapshot the renderer's boot-race retry
   backoff is designed to tolerate).

FIX LANDED: ``ipc/entrypoint.py``'s ws branch now launches the full
``app.start()`` on a ``ws-sidecar-startup`` daemon thread BEFORE
``sidecar_ws.run()`` blocks (source-pinned by
``tests/server/test_ipc_entrypoint.py::TestWsModeStartupLaunch``).
The expectations below verify the FIXED contract.
"""

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

# Two input devices on the platform's canonical host API. On Linux the
# canonical API is PulseAudio (``_preferred_host_api_substring``), so
# both records survive host-API canonicalization.
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
    """Install a working fake ``sounddevice`` with the module devices.

    Mirrors the fake-sounddevice convention of
    ``tests/test_microphone_list_cache.py``: the fake enumerates the
    two devices above, so PortAudio-level enumeration SUCCEEDS inside
    the test process. Any test using this helper therefore proves the
    mic list is empty for reasons OTHER than a failing device query.
    """
    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = lambda *args, **kwargs: (
        _DEVICES[0] if kwargs.get("kind") == "input" else _DEVICES
    )
    fake_sd.query_hostapis.return_value = _HOSTAPIS
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)


@pytest.fixture
def ws_mode_app(tmp_config_dir, monkeypatch):
    """A ``VoiceTyperApp`` shaped exactly like the ``--ws`` sidecar's.

    Same mocking discipline as ``app_for_startup`` in
    ``tests/test_startup_sequence.py`` (autostart platform helpers
    stubbed; everything else real), plus the two env vars the ws-mode
    entrypoint sets: TAURI_SIDECAR=1 (parse_ipc_args) and the WS auth
    token (set by the Rust host before spawn). The fixed entrypoint
    ALSO launches the app startup background work on a daemon thread
    in this mode (see the module docstring) — tests that need that
    work mirror the launch themselves.
    """
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _AUTH_TOKEN)
    from voice_typer.server.app import VoiceTyperApp

    return VoiceTyperApp()


@pytest.fixture(autouse=True)
def _reset_mic_cache():
    """Reset the microphone-list TTL cache around every test.

    Guarantees the fake sounddevice installed by each test is actually
    queried (no cached result from a previous test leaks in) and that
    no cached fake result leaks out.
    """
    from voice_typer.server.server_platform import microphone_list as _ml

    _ml.invalidate_microphone_list_cache()
    yield
    _ml.invalidate_microphone_list_cache()


def _build_ws_mode_server(app):
    """Build + start the IPC server exactly like entrypoint's ws branch.

    ``ipc/entrypoint.py`` does, in order: ``server._tcp_mode = True``
    (unconditional, skips the stdin listener) then ``server.start()``
    — and in ws mode never proceeds to ``app.start()``. The returned
    server is the full production composition (real service layer over
    the real app), so ``get_microphones`` responses below are the
    exact bytes a Tauri renderer would receive.
    """
    from voice_typer.server.providers import build_ipc_server

    server = build_ipc_server(app)
    server._tcp_mode = True
    server.start()
    return server


class TestWsSidecarMicPopulation:
    """The fixed contract: the ws-mode process shape populates the list."""

    def test_ws_mode_shape_serves_devices_to_renderer(self, ws_mode_app, monkeypatch):
        """Renderer-visible outcome of the FIXED ws-mode shape: devices.

        PortAudio enumeration WORKS in this process (fake sounddevice
        lists 2 devices), the IPC server is fully started — and the
        entrypoint's ws branch now launches the same startup background
        work (``app.start`` on a daemon thread) that the Electron path
        runs, so the phase-6 ``load_microphones`` task populates
        ``app._microphones`` and the handler serves the devices. This
        test replicates that launch exactly (startup background work
        running concurrently with the WS server) and polls for the
        population — pinning the renderer-visible fix contract.
        """
        import threading
        import time

        _install_fake_sounddevice(monkeypatch)
        server = _build_ws_mode_server(ws_mode_app)
        # Mirror the fixed entrypoint: the ws branch launches the app
        # startup work on a daemon thread. app.start() parks in
        # tray.run()'s unavailable-path drain loop (TAURI_SIDECAR=1),
        # so the thread never joins — poll for the population instead.
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
        """Control: the enumeration itself is NOT the failure.

        ``list_microphones()`` returns the full device list inside the
        same process shape — proving the empty page is caused by the
        missing population trigger, not by PortAudio/DDL/device-query
        failure in the sidecar context.
        """
        from voice_typer.server.server_platform import microphone_list as _ml

        _install_fake_sounddevice(monkeypatch)
        mics = _ml.list_microphones()
        assert len(mics) == 2
        assert {m["name"] for m in mics} == {"Mic Alpha", "Mic Beta"}


class TestWsSidecarMicPopulationControl:
    """The missing link: running the phase-6 task populates everything."""

    def test_load_microphones_makes_list_renderer_visible(self, ws_mode_app, monkeypatch):
        """Running the exact phase-6 task heals the renderer-visible list.

        ``startup_tasks.load_microphones`` is the task
        ``_phase_6_autostart_prewarm_mics`` schedules (the only
        production writer of ``app._microphones``). Calling it in the
        ws-mode shape immediately changes the ``get_microphones``
        response — pinning that the population trigger is the ONE
        missing link in the Tauri sidecar.
        """
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
        """Receive frames until the dispatch response with *request_id*.

        The sidecar pushes server-initiated events (``ready``,
        ``state_changed``, ``tray_menu``, ``tray_state``, …) around the
        dispatch response; skip everything that does not carry the
        correlation id.
        """
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
        """End-to-end over the real WS sidecar transport.

        1. Client authenticates (as the Rust host does) and dispatches
           ``get_microphones`` while the startup background work has
           not populated the list yet — the response reflects
           ``app._microphones`` verbatim (``[]`` — the pre-first-
           population snapshot the renderer's boot-race retry backoff
           is designed to tolerate).
        2. The phase-6 population task (now launched by the entrypoint's
           ws branch via the startup daemon thread) running in the
           SAME live process makes the NEXT dispatch return the
           devices — proving the renderer sees the list the moment the
           startup work runs.
        """
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

                    # The one missing link — the phase-6 population task.
                    startup_tasks.load_microphones(ws_mode_app)

                    await client.send(json.dumps({"type": "get_microphones", "data": {}, "id": 102}))
                    resp2 = await self._recv_until_id(client, 102)
                    assert len(resp2["data"]) == 2
                    assert {m["name"] for m in resp2["data"]} == {"Mic Alpha", "Mic Beta"}
        finally:
            server.stop()
