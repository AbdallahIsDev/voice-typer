"""Shared auth-handshake helpers for the sidecar-WS transport."""

from __future__ import annotations

import hmac

# Auth-read deadline (seconds), enforced by the sidecar-WS handshake: a
AUTH_READ_TIMEOUT_SECONDS = 5.0


def extract_auth_token(frame: object) -> str | None:
    """Validate an auth frame and return its bearer token.

    Returns ``None`` (and performs NO comparison) when ``frame`` is not
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
    """Constant-time comparison of two bearer tokens."""
    return hmac.compare_digest(provided, expected)
