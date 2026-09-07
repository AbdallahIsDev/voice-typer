"""Shared auth-handshake helpers for the TCP and sidecar-WS transports.

The TCP handshake (``ipc/transport_tcp.py``'s
``_handle_tcp_connection``) and the Tauri sidecar handshake
(``sidecar_ws.py``'s ``_authenticate``) implemented the SAME contract
twice — read the first frame, validate ``type == "auth"``, extract the
bearer token, compare it constant-time — and the two copies had
already drifted once (TCP rejects a ``protocol_version`` mismatch with
a structured error envelope; WS only warns). Before this module every
bug fix to the validation contract needed a coordinated edit in BOTH
files.

This module is the single source of truth for the transport-independent
parts of the handshake:

- :func:`extract_auth_token` — frame-shape validation + token
  extraction (the ADR-0020 §3 / ADR-0014 first-frame contract:
  ``{"type": "auth", "token": "<token>"}``).
- :func:`tokens_equal` — constant-time token comparison via
  :func:`hmac.compare_digest` (used purely as a timing-safe comparison
  helper; there is no key derivation, signing, or per-message MAC — see
  ``sidecar_ws._authenticate`` for the compensating controls).
- :data:`AUTH_READ_TIMEOUT_SECONDS` — the shared auth-read deadline
  (seconds) both transports enforce before dropping a silent client.

The transports keep their transport-specific concerns local
(asyncio/timeout handling, the ``protocol_version`` check, the error /
close behavior, and logging vocabulary). Only the names above are
shared, so a fix to the validation contract lands in ONE module.
"""

from __future__ import annotations

import hmac

# Auth-read deadline (seconds), shared by BOTH IPC transports: a client
# that connects but never sends the auth frame must not hold the
# connection (and its dispatcher slot) indefinitely. The sidecar WS
# handshake (``sidecar_ws._authenticate``) and the TCP handshake
# (``ipc/transport_tcp.py::_handle_tcp_connection``) both import this
# constant so the auth-deadline budget cannot drift between the two
# transports (previously each carried its own 5.0 with a comment
# requiring manual sync).
AUTH_READ_TIMEOUT_SECONDS = 5.0


def extract_auth_token(frame: object) -> str | None:
    """Validate an auth frame and return its bearer token.

    Per ADR-0020 §3 / ADR-0014, the client's first frame must be::

        {"type": "auth", "token": "<token>"}

    Returns ``None`` (and performs NO comparison) when ``frame`` is not
    a dict, is not ``type == "auth"``, or carries a missing / non-str /
    empty token. Callers treat ``None`` as an auth rejection and keep
    their transport-specific rejection behavior (error envelope / close).

    The isinstance guards make the helper safe for hostile non-dict
    JSON values (``42``, ``[1, 2, 3]``, ``"hi"``) — ``.get`` is only
    ever called on a real dict.
    """
    if not isinstance(frame, dict):
        return None
    if frame.get("type") != "auth":
        return None
    token = frame.get("token", "")
    if not isinstance(token, str) or not token:
        return None
    return token


def tokens_equal(provided: str, expected: str) -> bool:
    """Constant-time comparison of two bearer tokens.

    Wraps :func:`hmac.compare_digest` — used purely as a timing-safe
    *comparison* helper (byte-exact: rejects whitespace-padded /
    substring tricks). Both transports MUST route their token
    comparison through this function so the constant-time guarantee
    cannot silently degrade to a plain ``==`` in one of them.
    """
    return hmac.compare_digest(provided, expected)
