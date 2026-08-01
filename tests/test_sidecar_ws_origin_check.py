"""AP-8 regression: sidecar WS ``process_request`` must reject browser
``Origin`` headers to close the CSWSH slot-starvation vector.

Before AP-8, ``sidecar_ws.run()`` bound ``websockets.serve(...)`` without
an ``origins=`` allowlist or a ``process_request=`` callback. The
``websockets`` library does **not** validate the ``Origin`` header by
default — a malicious web page in the user's browser could call
``new WebSocket("ws://127.0.0.1:<port>")`` and the sidecar would accept
the handshake and park the connection in the 5s auth-wait window. A
single page can do this many times concurrently and starve the
single-connection slot (ADR-0020 §6) so the legitimate Rust host cannot
connect.

AP-8 adds ``_reject_browser_origins`` as the ``process_request`` callback
on ``websockets.asyncio.server.serve``. The contract:

* If the ``Origin`` header is **absent** → ``return None`` (allow the
  handshake; the Rust host never sends an Origin header because it uses
  a raw TCP socket, not a browser).
* If the ``Origin`` header is **present** → ``return Response(403, ...)``
  to abort the handshake before the auth window even opens.

These tests stand up a real ``websockets.asyncio.server.serve`` on
``127.0.0.1:0`` with the new callback, then connect with a real
``websockets.asyncio.client.connect`` client:

1. **Origin present** → handshake MUST fail with HTTP 403.
2. **Origin absent** → handshake MUST succeed (then we close cleanly).
3. **Callback unit test** — direct call on a synthetic request, no
   network, to lock in the allow/deny contract at the API level.
"""

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

# ── Helpers ────────────────────────────────────────────────────────────


async def _pick_port() -> int:
    """Bind and immediately close a 127.0.0.1:0 socket to grab a free port.

    There is a TOCTOU window between the close and the ``serve`` rebind,
    but the tests run sequentially inside one event loop and the loop
    keeps the port hot; this is the standard pattern when you cannot
    pass ``sock=`` directly.
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _handler(ws) -> None:
    """Minimal handler: park until the client closes or errors out.

    For the allow-case test we only need the handshake to succeed; we
    never send an auth frame, so ``recv()`` simply blocks until the
    client disconnects. ``serve`` then tears the connection down.
    """
    with contextlib.suppress(Exception):
        await ws.recv()


# ── Tests: real serve() + real connect() ───────────────────────────────


@pytest.mark.asyncio
async def test_origin_header_present_is_rejected_with_403() -> None:
    """A client that sends ``Origin: https://attacker.example`` MUST be
    rejected at the HTTP layer (403) — the WS handshake never completes
    and the auth-wait slot is never consumed.

    This is the browser-attacker scenario: a malicious page calls
    ``new WebSocket("ws://127.0.0.1:<port>")`` and the browser auto-
    attaches an ``Origin`` header. The 403 short-circuits the handshake
    before ``_handler`` is even entered.
    """
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
    """A client that sends NO ``Origin`` header MUST pass the
    ``process_request`` gate and complete the WS handshake.

    The Rust host (Tauri ``externalBin``) opens its WS client with a raw
    TCP socket and never sends an ``Origin`` header — this is the
    legitimate-traffic path. The handshake succeeds; the auth frame
    still gates access afterwards.
    """
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
            # If we got here, the handshake completed (HTTP 101) — the
            # process_request callback returned None (allowed). Reaching
            # this line is the success signal. Close cleanly so the test
            # does not hang.
            pass
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_origin_header_empty_string_is_rejected() -> None:
    """An empty-string ``Origin`` header is still a *present* header and
    MUST be rejected. This locks in the contract that we test presence,
    not truthiness — a browser cannot bypass the check by sending
    ``Origin: `` (empty).
    """
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


# ── Tests: callback unit test (no network) ─────────────────────────────


@pytest.mark.asyncio
async def test_reject_browser_origins_allows_missing_origin() -> None:
    """Direct unit test: a request with no ``Origin`` header returns
    ``None`` (allow). Locks the contract at the API level without
    standing up a real server.
    """
    request = Request(path="/", headers=Headers())
    response = await sidecar_ws._reject_browser_origins(None, request)
    assert response is None, "missing Origin must be allowed (return None)"


@pytest.mark.asyncio
async def test_reject_browser_origins_rejects_present_origin() -> None:
    """Direct unit test: a request with ANY ``Origin`` header returns a
    403 :class:`Response`. Locks the contract at the API level without
    standing up a real server.
    """
    request = Request(
        path="/",
        headers=Headers({"Origin": "https://attacker.example"}),
    )
    response = await sidecar_ws._reject_browser_origins(None, request)
    assert response is not None, "present Origin must be rejected"
    assert response.status_code == 403
    # The body is the explanatory message — confirms the rejection path.
    assert b"origin not allowed" in response.body


# ── Test: contract is wired into run() ─────────────────────────────────


def test_run_passes_process_request_to_serve(monkeypatch) -> None:
    """``sidecar_ws.run()`` MUST pass ``process_request`` to ``serve``.

    This guards against a regression where the callback is added but
    accidentally not wired into the ``serve(...)`` call (e.g. someone
    reverts the kwargs). The fake ``serve`` records the kwargs it was
    called with; we assert ``process_request`` is the
    ``_reject_browser_origins`` coroutine function.
    """
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

    # run() calls asyncio.run(_main()) and _main awaits serve(). We let
    # _main hit the "no sockets bound" branch (FakeServer.sockets == [])
    # so it returns 3 immediately after the serve() call — that is
    # enough to capture the kwargs without standing up a real loop.
    from voice_typer.server import ipc_server

    fake_server = ipc_server.IPCServer.__new__(ipc_server.IPCServer)
    # _make_dispatch touches server internals; stub it out.
    monkeypatch.setattr(sidecar_ws, "_make_dispatch", lambda s: lambda *a, **k: None)

    rc = sidecar_ws.run(fake_server)

    assert rc == 3, f"expected run() to return 3 (no-sockets branch) for the stubbed serve, got rc={rc}"
    assert "process_request" in captured["kwargs"], "run() must pass process_request= to serve()"
    assert captured["kwargs"]["process_request"] is sidecar_ws._reject_browser_origins, (
        "process_request must be exactly sidecar_ws._reject_browser_origins"
    )
