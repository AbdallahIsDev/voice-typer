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

    def _dispatch(self, msg):
        self.calls.append(msg)
        return {"type": "result", "data": {"echo": msg.get("type")}}


async def client_side(port, token):
    async with websockets.connect(f"ws://127.0.0.1:{port}") as c:
        await c.send(json.dumps({"type": "auth", "token": token}))
        await c.send(json.dumps({"type": "get_status", "data": {}, "id": 7}))
        r1 = json.loads(await asyncio.wait_for(c.recv(), 3))
        await c.send(json.dumps({"type": "shutdown", "id": 8}))
        r2 = json.loads(await asyncio.wait_for(c.recv(), 3))
        return r1, r2


async def main():
    os.environ["VOICE_TYPER_IPC_TOKEN"] = "secret-token-123"
    server = FakeServer()
    got = {}

    def fake_emit(port):
        got["port"] = port
        sys.stdout.write(json.dumps({"event": "server_started", "port": port}) + "\n")
        sys.stdout.flush()

    sidecar_ws._emit_server_started = fake_emit
    task = asyncio.create_task(asyncio.to_thread(sidecar_ws.run, server))
    for _ in range(100):
        if "port" in got:
            break
        await asyncio.sleep(0.05)
    if "port" not in got:
        print("NO server_started emitted — FAIL")
        task.cancel()
        return
    port = got["port"]
    print("server_started port =", port)
    r1, r2 = await client_side(port, "secret-token-123")
    print("dispatch response:", r1)
    print("shutdown response:", r2)
    assert r1.get("type") == "result" and r1.get("id") == 7, r1
    assert r2.get("type") == "result" and r2.get("data", {}).get("ack") is True, r2
    print("sidecar_ws.run() REAL E2E: PASS")
    task.cancel()


asyncio.run(main())
