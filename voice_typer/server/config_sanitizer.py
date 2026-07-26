"""Transport-neutral config sanitization for IPC transmission.

EC-FIX-15 / EC-22: the canonical implementation of ``sanitize_config_for_ipc``
lives here so the service layer (:mod:`voice_typer.server.service`) does not
have to reach DOWN into the IPC transport layer
(:mod:`voice_typer.server.ipc_server`) to redact secrets from a config
payload.  Both the service layer and the IPC layer should import from this
module.

Historical note: this logic was previously extracted from
``ipc_server.py`` into :mod:`voice_typer.server.ipc.history_bounds` during
ARCH-045 (Phase 4.5 split).  That module still re-exports
``_sanitize_config_for_ipc`` for backwards-compat with any external
importer, but new code should import :func:`sanitize_config_for_ipc`
(public name) from here.

ZR-12: this module is now the canonical home for the underscore-prefixed
``_SECRET_CONFIG_FIELDS`` frozenset and ``_sanitize_config_for_ipc``
function too.  ``crash_recovery.py`` previously reached into
``ipc_server`` for ``_SECRET_CONFIG_FIELDS`` (a private IPC-server
implementation detail); it now imports from here so the dependency
direction is config-sanitizer → consumers (not consumers → ipc_server →
config-sanitizer).  ``ipc_server.py`` re-exports both names from here so
the existing import path (``from voice_typer.server.ipc_server import
_SECRET_CONFIG_FIELDS``) keeps working for any external consumer, but
new code should import from this module directly.

SEC-003: ``get_config`` must NOT echo secret fields back to the IPC
client.  Even though the IPC socket is loopback-only, any local process
can connect to it (see SEC-018 for the auth fix).  We return a
sanitized view where API keys are replaced with a presence indicator so
the renderer can render "key configured" UI without ever holding the
actual key value.
"""

from __future__ import annotations

from typing import Any

# Fields whose values are secrets and must never be echoed back.
SECRET_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "cloud_api_key",
        "openai_api_key",
        "groq_api_key",
        "deepgram_api_key",
        "llm_api_key",
    }
)

# ZR-12: underscore-prefixed alias kept for backward compat with
# ``crash_recovery.py`` (which imports ``_SECRET_CONFIG_FIELDS`` for its
# own config.json redaction path) and with
# :mod:`voice_typer.server.ipc.history_bounds` (which re-exports it
# under the underscore name). The two names refer to the SAME frozenset
# object — alias, not a copy — so adding a field to one automatically
# updates the other.
_SECRET_CONFIG_FIELDS: frozenset[str] = SECRET_CONFIG_FIELDS

# Sentinel returned in place of a secret value.  The renderer treats
# this as "key is set, do not display" — it must NOT treat this as the
# actual key value (which would be a regression of SEC-003).
REDACTED_SENTINEL = "<redacted>"


def sanitize_config_for_ipc(config: Any) -> dict[str, Any]:
    """Sanitize a Config object for safe IPC transmission to the renderer.

    Returns a shallow copy of ``config.__dict__`` with secret fields
    redacted (replaced with :data:`REDACTED_SENTINEL` when the field has
    a truthy value, preserved as the original falsy value otherwise so
    the renderer can distinguish "no key set" from "key set but
    hidden").  This is the canonical implementation — both
    :mod:`voice_typer.server.service` and
    :mod:`voice_typer.server.ipc_server` should import from here.

    A secret field is any field in :data:`SECRET_CONFIG_FIELDS`.  If the
    field's value is truthy (a key was set), it is replaced with
    ``"<redacted>"``.  If falsy (empty string or ``None``), the original
    value (``""`` / ``None``) is preserved.  Fields absent from the
    config object are left absent (not synthesized) — this keeps the
    function tolerant of older Config snapshots that lack a newer
    secret field.
    """
    out = dict(config.__dict__)
    for k in SECRET_CONFIG_FIELDS:
        if k in out:
            v = out[k]
            out[k] = REDACTED_SENTINEL if v else v
    return out


# ZR-12: underscore-prefixed alias for backward compat with
# :mod:`voice_typer.server.ipc.history_bounds` and any external importer
# that already uses the underscore form. Same callable object — alias,
# not a wrapper.
_sanitize_config_for_ipc = sanitize_config_for_ipc


__all__ = [
    "sanitize_config_for_ipc",
    "SECRET_CONFIG_FIELDS",
    "REDACTED_SENTINEL",
    # ZR-12: underscore aliases (backward-compat re-exports).
    "_sanitize_config_for_ipc",
    "_SECRET_CONFIG_FIELDS",
]
