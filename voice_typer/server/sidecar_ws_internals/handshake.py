"""WS auth handshake: the one-shot bearer-token first-frame check.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`
(``_authenticate``); the canonical module re-exports the function so
the direct-call surface (``sidecar_ws._authenticate(ws)`` — the
mig15/mig16/mig17 ws_hmac suites, tests/tauri/test_sidecar_ws_unit.py,
tests/test_sidecar_ws_protocol_version.py,
tests/test_sidecar_ws_bearer_token_doc.py) and the
``inspect.getsource`` pins (tests/test_sidecar_ws_bearer_token_doc.py)
follow the re-exported function object unchanged.

Patch-path contract (C-ARCH-2 canonical form): this module OWNS
``_authenticate``. The canonical ``_handle_connection_inner`` resolves
it through the sibling MODULE-OBJECT read at call time
(``_handshake_mod._authenticate(...)``), so a ``monkeypatch.setattr``
on THIS module is observed by production. No test patches the
re-export on the canonical module.

The auth-read deadline is NOT owned here: the function resolves
``_AUTH_TIMEOUT_SECONDS`` from the canonical module object at CALL
time (see the comment at the read site) so the historical
read/patch surface ``sidecar_ws._AUTH_TIMEOUT_SECONDS`` — asserted
and patched by the mig15-17 ws_hmac suites and
tests/tauri/test_sidecar_ws_unit.py — keeps observing patches
exactly as it did when this body lived in sidecar_ws.py.

Token-leak guards (the mig15-17 ws_hmac source greps read THIS file
concatenated after the canonical sidecar_ws.py): every log call in
this module uses a static string — the token value
(``expected_token`` / ``provided`` / the raw frame) is NEVER
interpolated into a log message.
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
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")


async def _authenticate(websocket) -> bool:
    """Read the first WS frame and validate the bearer token.

    Per ADR-0020 §3, the client's first frame
    must be::

        {"type": "auth", "token": "<token>"}

    The token is compared with :func:`hmac.compare_digest` (constant
    time) against the ``VOICE_TYPER_IPC_TOKEN`` env var set by the
    Rust host at spawn. On mismatch, the socket is closed immediately
    and the connection is rejected (the host treats this as a crash
    → respawn with a fresh token, ADR-0020 §10).

    This is a **one-shot bearer-token** check, NOT an HMAC scheme:
    :func:`hmac.compare_digest` is used purely as a constant-time
    *comparison* helper — there is no key derivation, no signing, no
    per-message MAC, and no nonce/replay protection. Subsequent frames
    after the handshake skip re-auth (mirroring the TCP handshake-once
    model from ADR-0014). Compensating controls for the absence of
    per-message MAC are documented in the canonical module's
    top-level docstring (loopback-only bind + ephemeral port +
    per-respawn token rotation).

    Returns ``True`` if authenticated, ``False`` if rejected.

    DEDUP ()
    ----------------
    This function mirrors the TCP auth handshake in
    ``ipc/transport_tcp.py::_handle_tcp_connection`` (the
    ``if expected_token:`` block at ~L300-365).  BOTH transports
    implement the same contract:

    - Read the first frame/line.
    - Parse JSON.
    - Validate ``type == "auth"`` + ``isinstance(token, str)``.
    - ``hmac.compare_digest(token, expected_token)`` (constant-time).
    - Emit ``{"code":"auth_failed","message":"authentication failed"}``
      envelope on mismatch.
    - 5-second auth deadline.

    Differences are transport-primitive only (``websocket.recv()`` +
    ``asyncio.wait_for`` vs ``_TCPLineIO.readline()`` +
    ``conn.settimeout``).  Bug fixes to the validation contract are
    applied in ONE place: the shared helpers in
    :mod:`voice_typer.server.ipc.auth` (``extract_auth_token`` +
    ``tokens_equal``) are used by BOTH transports, so a fix to the
    frame-validation / constant-time comparison contract lands in a
    single module (extracted 2026-08-11; previously this note
    read "must be applied to BOTH call sites").
    """
    # Auth-read deadline: resolve the canonical module's value alias
    # at CALL time (C-ARCH-2 canonical patch form). The mig15-17
    # ws_hmac suites and tests/tauri/test_sidecar_ws_unit.py read /
    # patch ``sidecar_ws._AUTH_TIMEOUT_SECONDS``; routing the read
    # through the canonical module object keeps those patches observed
    # exactly as they were when this body lived in sidecar_ws.py (the
    # alias itself is the single-sourced
    # ``ipc.auth.AUTH_READ_TIMEOUT_SECONDS``).
    from voice_typer.server import sidecar_ws as _canonical

    deadline = _canonical._AUTH_TIMEOUT_SECONDS

    expected_token = os.environ.get(IPC_TOKEN_ENV_VAR, "")
    if not expected_token:
        log.error(
            "[SIDECAR-WS] VOICE_TYPER_IPC_TOKEN not set — refusing to "
            "accept connections (the host must always set this env var)."
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=deadline)
    except asyncio.TimeoutError:
        log.warning("[SIDECAR-WS] auth frame timeout — closing connection")
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
    # validates the frame shape + extracts the token; ``tokens_equal``
    # performs the constant-time ``hmac.compare_digest`` comparison
    # (see ``voice_typer.server.ipc.auth`` — a bug fix to either
    # concern lands in ONE module used by both transports).
    provided = extract_auth_token(first)
    if provided is None:
        log.warning("[SIDECAR-WS] auth frame missing token")
        return False

    if not tokens_equal(provided, expected_token):
        log.warning("[SIDECAR-WS] auth token mismatch — rejecting")
        return False

    # detect host/sidecar protocol-version skew at handshake
    # time. The Rust host (src-tauri/src/sidecar/ws.rs) now includes a
    # `protocol_version` integer in its auth frame. The field is
    # additive — older hosts that don't yet send it continue to function
    # (we just skip the check). When present and mismatched, log a
    # prominent WARNING so the mismatch is observable in diagnostics
    # before confusing partial-failure symptoms appear. We do NOT reject
    # the connection on mismatch because a misconfigured host should
    # still be able to authenticate (the version negotiation is
    # defense-in-depth, not a security gate). The TCP transport's
    # parallel check lives in ipc/transport_tcp.py ().
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
                    "[SIDECAR-WS] protocol version skew: host=%d sidecar=%d (continuing — field is advisory)",
                    host_protocol_int,
                    PROTOCOL_VERSION,
                )

    log.info("[SIDECAR-WS] auth accepted")
    return True
