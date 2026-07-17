import asyncio
import json
import os
import sys

import websockets
from voice_typer.server import sidecar_ws


class FakeApp:
    def quit(self):
        pass


class FakeServer:
    def __init__(self):
        self.app = FakeApp()
        self.calls = []
        self.ready_events = []
        # CR-4: sidecar_ws._handle_connection now reads/writes this on
        # the server instance (per-instance, was module-level global).
        self._ready_emitted = False

    def _dispatch(self, msg):
        self.calls.append(msg)
        return {"type": "result", "data": {"echo": msg.get("type")}}


import voice_typer.server.event_bus as eb

_orig_publish = eb.publish


def spy(event):
    if event.get("type") == "ready":
        server.ready_events.append(event)
    return _orig_publish(event)


eb.publish = spy


async def client_side(port, token):
    async with websockets.connect(f"ws://127.0.0.1:{port}") as c:
        await c.send(json.dumps({"type": "auth", "token": token}))
        await asyncio.sleep(0.2)
        await c.send(json.dumps({"type": "get_status", "data": {}, "id": 7}))
        r1 = json.loads(await asyncio.wait_for(c.recv(), 3))
        return r1


async def main():
    os.environ["VOICE_TYPER_IPC_TOKEN"] = "tok-123"
    global server
    server = FakeServer()
    got = {}

    def fake_emit(port):
        got["port"] = port
        sys.stdout.write(json.dumps({"event": "server_started", "port": port}) + "\n")
        sys.stdout.flush()

    sidecar_ws._emit_server_started = fake_emit
    # CR-4: reset per-instance flag on the FakeServer before run().
    server._ready_emitted = False
    task = asyncio.create_task(asyncio.to_thread(sidecar_ws.run, server))
    for _ in range(100):
        if "port" in got:
            break
        await asyncio.sleep(0.05)
    r1 = await client_side(got["port"], "tok-123")
    print("dispatch:", r1)
    print("ready published to event_bus:", server.ready_events)
    assert r1["id"] == 7
    assert server.ready_events, "ready event NOT emitted via event_bus — REGRESSION"
    print("WS bridge + ready-event: PASS")
    task.cancel()


asyncio.run(main())
