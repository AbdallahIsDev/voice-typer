"""Patch-path contract (C-ARCH-2 canonical form): this module OWNS
(``_authenticate``); the canonical module re-exports the function so
the direct-call surface (``sidecar_ws._authenticate(ws)``, the
``inspect.getsource`` pins (tests/test_sidecar_ws_bearer_token_doc.py)
``_authenticate``. The canonical ``_handle_connection_inner`` resolves
(``_handshake_mod._authenticate(...)``), so a ``monkeypatch.setattr``
``_AUTH_TIMEOUT_SECONDS`` from the canonical module object at CALL
read/patch surface ``sidecar_ws._AUTH_TIMEOUT_SECONDS``, asserted
(``expected_token`` / ``provided`` / the raw frame) is NEVER
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from voice_typer.server._paths import IPC_TOKEN_ENV_VAR
from voice_typer.server.ipc.auth import extract_auth_token, tokens_equal
from voice_typer.server.ipc.protocol_version import PROTOCOL_VERSION

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")


async def _authenticate(websocket) -> bool:
    """Read the first WS frame and validate the bearer token.

    One-shot bearer-token check, NOT an HMAC scheme (no per-message MAC);
    compensating controls are loopback-only bind + ephemeral port + rotation.
    Returns ``True`` if authenticated, ``False`` if rejected.
    """
    # at CALL time (C-ARCH-2 canonical patch form). The mig15-17
    from voice_typer.server import sidecar_ws as _canonical

    deadline = _canonical._AUTH_TIMEOUT_SECONDS

    expected_token = os.environ.get(IPC_TOKEN_ENV_VAR, "")
    if not expected_token:
        log.error(
            "[SIDECAR-WS] VOICE_TYPER_IPC_TOKEN not set, refusing to "
            "accept connections (the host must always set this env var)."
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=deadline)
    except asyncio.TimeoutError:
        log.warning("[SIDECAR-WS] auth frame timeout, closing connection")
        return False
    except Exception:
        log.warning("[SIDECAR-WS] auth frame read failed", exc_info=True)
        return False

    try:
        if isinstance(first_raw, bytes):
            first_raw = first_raw.decode("utf-8")
        first = json.loads(first_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("[SIDECAR-WS] auth frame is not valid JSON")
        return False

    if not isinstance(first, dict) or first.get("type") != "auth":
        log.warning("[SIDECAR-WS] first frame is not an auth frame")
        return False

    # Shared with the TCP transport: ``extract_auth_token``
    provided = extract_auth_token(first)
    if provided is None:
        log.warning("[SIDECAR-WS] auth frame missing token")
        return False

    if not tokens_equal(provided, expected_token):
        log.warning("[SIDECAR-WS] auth token mismatch, rejecting")
        return False

    # detect host/sidecar protocol-version skew at handshake
    host_protocol = first.get("protocol_version")
    if host_protocol is not None:
        try:
            host_protocol_int = int(host_protocol)
        except (TypeError, ValueError):
            log.warning(
                "[SIDECAR-WS] auth frame protocol_version is not an int: %r",
                host_protocol,
            )
        else:
            if host_protocol_int != PROTOCOL_VERSION:
                log.warning(
                    "[SIDECAR-WS] protocol version skew: host=%d sidecar=%d (continuing, field is advisory)",
                    host_protocol_int,
                    PROTOCOL_VERSION,
                )

    log.info("[SIDECAR-WS] auth accepted")
    return True
