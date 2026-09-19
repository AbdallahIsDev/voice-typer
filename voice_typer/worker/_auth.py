"""Worker WS auth helpers."""

from __future__ import annotations

import contextlib
import json
import logging
import os

from voice_typer.server._paths import IPC_TOKEN_ENV_VAR
from voice_typer.server.ipc.auth import AUTH_READ_TIMEOUT_SECONDS

log = logging.getLogger("voice_typer.worker")

# Auth frame timeout (seconds). A client that connects but never sends
# the auth frame must not hold the connection indefinitely, the
# budget is single-sourced as ``AUTH_READ_TIMEOUT_SECONDS`` in
# :mod:`voice_typer.server.ipc.auth` and imported by ALL THREE
# handshake implementations (the TCP path in
# ``ipc/transport_tcp.py``, the sidecar WS path in
# ``sidecar_ws_internals/handshake.py``, and this worker WS path), so
# the transports agree on the auth-deadline budget without any manual
# sync. The local alias below preserves the historical
# ``_AUTH_TIMEOUT_SECONDS`` name this module's ``_authenticate``
# reads (no test patches it, kept purely as the established name).
_AUTH_TIMEOUT_SECONDS = AUTH_READ_TIMEOUT_SECONDS


async def _authenticate(websocket) -> bool:  # noqa: ANN001 - websockets type is imported lazily
    """Read the first WS frame and validate the bearer token.

    Per ADR-0020 §3, the client's first frame must be::

        {"type": "auth", "token": "<token>"}

    The token is compared constant-time against the
    ``VOICE_TYPER_IPC_TOKEN`` env var via the shared
    :func:`voice_typer.server.ipc.auth.tokens_equal` helper (so a fix
    to the comparison contract lands in ONE module used by both the
    slim-core sidecar and the worker).

    Returns ``True`` if authenticated, ``False`` if rejected. On
    rejection the caller sends an ``auth_failed`` error envelope and
    closes the socket with code 1008 (mirrors
    :func:`sidecar_ws._authenticate`).
    """
    import asyncio

    from voice_typer.server.ipc.auth import extract_auth_token, tokens_equal

    expected_token = os.environ.get(IPC_TOKEN_ENV_VAR, "")
    if not expected_token:
        log.error(
            "[WORKER] %s not set: refusing to accept connections (the host must always set this env var).",
            IPC_TOKEN_ENV_VAR,
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=_AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning("[WORKER] auth frame timeout: closing connection")
        return False
    except Exception:
        log.warning("[WORKER] auth frame read failed", exc_info=True)
        return False

    try:
        if isinstance(first_raw, bytes):
            first_raw = first_raw.decode("utf-8")
        first = json.loads(first_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("[WORKER] auth frame is not valid JSON")
        return False

    provided = extract_auth_token(first)
    if provided is None:
        log.warning("[WORKER] auth frame missing token or wrong shape")
        return False

    if not tokens_equal(provided, expected_token):
        log.warning("[WORKER] auth frame token mismatch: rejecting")
        return False

    return True


async def _send_auth_failed_and_close(websocket) -> None:  # noqa: ANN001
    """Send the ``auth_failed`` error envelope, then close with 1008.

    Mirrors the slim-core sidecar's WS path (see
    ``test_sidecar_ws_auth_failed.py`` for the cross-transport parity
    contract). Both calls are wrapped in ``contextlib.suppress(Exception)``
    so a half-closed socket (client RST after sending bad token) does
    not crash the handler before the authoritative close runs.
    """
    envelope = json.dumps(
        {
            "type": "error",
            "data": {
                "code": "auth_failed",
                "message": "authentication failed",
            },
        }
    )
    with contextlib.suppress(Exception):
        await websocket.send(envelope)
    with contextlib.suppress(Exception):
        await websocket.close(code=1008)
