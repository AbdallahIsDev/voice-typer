"""AP-8 regression: sidecar WS ``process_request`` must reject browser"""

from __future__ import annotations

import contextlib

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402
from websockets.datastructures import Headers  # noqa: E402
from websockets.exceptions import InvalidStatus  # noqa: E402
from websockets.http11 import Request  # noqa: E402


async def _pick_port() -> int:
    """Bind and immediately close a 127.0.0.1:0 socket to grab a free port."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _handler(ws) -> None:
    """Minimal handler: park until the client closes or errors out."""
    with contextlib.suppress(Exception):
        await ws.recv()


@pytest.mark.asyncio
async def test_origin_header_present_is_rejected_with_403() -> None:
    """A client that sends ``Origin: https://attacker.example`` MUST be"""
    port = await _pick_port()

    async with serve(
        _handler,
        "127.0.0.1",
        port,
        process_request=sidecar_ws._reject_browser_origins,
    ):
        with pytest.raises(InvalidStatus) as exc_info:
            await connect(
                f"ws://127.0.0.1:{port}",
                additional_headers={"Origin": "https://attacker.example"},
                open_timeout=5,
            )

        response = exc_info.value.response
        assert response.status_code == 403, (
            f"expected HTTP 403 for browser-originated handshake, got {response.status_code}"
        )


@pytest.mark.asyncio
async def test_origin_header_absent_is_allowed() -> None:
    """``process_request`` gate and complete the WS handshake."""
    port = await _pick_port()

    async with serve(
        _handler,
        "127.0.0.1",
        port,
        process_request=sidecar_ws._reject_browser_origins,
    ):
        # No additional_headers → no Origin header on the wire.
        ws = await connect(f"ws://127.0.0.1:{port}", open_timeout=5)
        try:
            pass
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_origin_header_empty_string_is_rejected() -> None:
    """MUST be rejected. This locks in the contract that we test presence,"""
    port = await _pick_port()

    async with serve(
        _handler,
        "127.0.0.1",
        port,
        process_request=sidecar_ws._reject_browser_origins,
    ):
        with pytest.raises(InvalidStatus) as exc_info:
            await connect(
                f"ws://127.0.0.1:{port}",
                additional_headers={"Origin": ""},
                open_timeout=5,
            )

        assert exc_info.value.response.status_code == 403


@pytest.mark.asyncio
async def test_reject_browser_origins_allows_missing_origin() -> None:
    """
    Direct unit test: a request with no ``Origin`` header returns
    ``None`` (allow). Locks the contract at the API level without
    """
    request = Request(path="/", headers=Headers())
    response = await sidecar_ws._reject_browser_origins(None, request)
    assert response is None, "missing Origin must be allowed (return None)"


@pytest.mark.asyncio
async def test_reject_browser_origins_rejects_present_origin() -> None:
    """403 :class:`Response`. Locks the contract at the API level without"""
    request = Request(
        path="/",
        headers=Headers({"Origin": "https://attacker.example"}),
    )
    response = await sidecar_ws._reject_browser_origins(None, request)
    assert response is not None, "present Origin must be rejected"
    assert response.status_code == 403
    # The body is the explanatory message, confirms the rejection path.
    assert b"origin not allowed" in response.body


def test_run_passes_process_request_to_serve(monkeypatch) -> None:
    """``sidecar_ws.run()`` MUST pass ``process_request`` to ``serve``."""
    captured: dict = {}

    class _FakeServer:
        sockets = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    def _fake_serve(handler, host, port, **kwargs):
        captured["handler"] = handler
        captured["host"] = host
        captured["port"] = port
        captured["kwargs"] = kwargs
        return _FakeServer()

    # Stub the lazy imports inside run().
    import sys
    import types

    fake_ws_mod = types.ModuleType("websockets")
    fake_asyncio_mod = types.ModuleType("websockets.asyncio")
    fake_server_mod = types.ModuleType("websockets.asyncio.server")
    fake_server_mod.serve = _fake_serve
    fake_asyncio_mod.server = fake_server_mod
    fake_ws_mod.asyncio = fake_asyncio_mod
    monkeypatch.setitem(sys.modules, "websockets", fake_ws_mod)
    monkeypatch.setitem(sys.modules, "websockets.asyncio", fake_asyncio_mod)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", fake_server_mod)

    from voice_typer.server import ipc_server

    fake_server = ipc_server.IPCServer.__new__(ipc_server.IPCServer)
    from voice_typer.server.sidecar_ws_internals import dispatch as _dispatch_mod

    monkeypatch.setattr(_dispatch_mod, "_make_dispatch", lambda s: lambda *a, **k: None)

    rc = sidecar_ws.run(fake_server)

    assert rc == 3, f"expected run() to return 3 (no-sockets branch) for the stubbed serve, got rc={rc}"
    assert "process_request" in captured["kwargs"], "run() must pass process_request= to serve()"
    assert captured["kwargs"]["process_request"] is sidecar_ws._reject_browser_origins, (
        "process_request must be exactly sidecar_ws._reject_browser_origins"
    )
