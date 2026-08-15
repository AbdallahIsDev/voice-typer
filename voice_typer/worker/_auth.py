"""Auth handshake for the worker WS server (master plan §7.2).

This module is an intentional extraction from ``voice_typer/worker/__main__.py``
per E3 (no spaghetti entry files). The auth handshake is a focused
concern: read the first WS frame, validate the bearer token via the
shared :func:`voice_typer.server.ipc.auth.tokens_equal` helper (which
wraps ``hmac.compare_digest``), and on rejection emit an
``auth_failed`` envelope + close the socket with code 1008.

The contract mirrors :mod:`voice_typer.server.ipc.auth` and the
slim-core sidecar's ``sidecar_ws._authenticate`` (ADR-0020 §3 /
ADR-0014) so the host's respawn scheduler can branch on
``code == "auth_failed"`` uniformly across both transports.

Auth model (master plan §7.2 — same as the slim-core sidecar):

This is a **one-shot bearer-token** check, NOT an HMAC scheme.
``hmac.compare_digest`` is used purely as a constant-time *comparison*
helper (no key derivation, no signing, no per-message MAC, no nonce /
replay protection — same as the slim-core sidecar, see
:mod:`voice_typer.server.ipc.auth`). Compensating controls:

- **Loopback-only bind**: ``127.0.0.1:0`` — never exposed to the network.
- **Ephemeral port**: chosen by the OS at worker startup and reported to
  the host over stdout; not predictable ahead of time.
- **Per-launch token rotation**: the host generates a fresh token via
  ``secrets.token_bytes(32)`` on every worker spawn.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os

from voice_typer.server._paths import IPC_TOKEN_ENV_VAR

log = logging.getLogger("voice_typer.worker")

# Auth frame timeout (seconds). A client that connects but never sends
# the auth frame must not hold the connection indefinitely — matches
# the slim-core sidecar's ``_AUTH_TIMEOUT_SECONDS`` (5.0s) so the two
# transports agree on the auth-deadline budget.
_AUTH_TIMEOUT_SECONDS = 5.0


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
            "[WORKER] %s not set — refusing to accept connections (the host must always set this env var).",
            IPC_TOKEN_ENV_VAR,
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=_AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning("[WORKER] auth frame timeout — closing connection")
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
        log.warning("[WORKER] auth frame token mismatch — rejecting")
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
