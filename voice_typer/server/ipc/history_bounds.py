# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""History-DB bounds and config sanitization helpers.

Phase 4.5 / ARCH-045 — extracted from the original ``ipc_server.py``
god-module.  Contains:

- :func:`_bound_history_limit` / :func:`_bound_history_offset` —
  clamp caller-supplied history pagination params to a safe range
  (SEC-010).
- :func:`_sanitize_config_for_ipc` — return a copy of
  ``config.__dict__`` with secret fields redacted (SEC-003).

SEC-003: ``get_config`` must NOT echo secret fields back to the IPC
client.  Even though the IPC socket is loopback-only, any local process
can connect to it (see SEC-018 for the auth fix).  We return a
sanitized view where API keys are replaced with a presence indicator so
the renderer can render "key configured" UI without ever holding the
actual key value.
"""

# Fields whose values are secrets and must never be echoed back.
_SECRET_CONFIG_FIELDS = frozenset(
    {
        "cloud_api_key",
        "openai_api_key",
        "groq_api_key",
        "deepgram_api_key",
        "llm_api_key",
    }
)

# Sentinel returned in place of a secret value.  The renderer treats
# this as "key is set, do not display" — it must NOT treat this as the
# actual key value (which would be a regression of SEC-003).
_REDACTED_SENTINEL = "<redacted>"


# SEC-010: maximum number of history rows a single IPC call can
# materialize.  Without this cap, ``{"limit": 100000000}`` would
# force SQLite to scan and the dispatcher to materialize a million
# rows before slicing — a trivial DoS.
_HISTORY_LIMIT_MAX = 500
_HISTORY_LIMIT_DEFAULT = 50


def _bound_history_limit(raw) -> int:
    """Clamp a caller-supplied history ``limit`` to a safe range.

    Accepts ints, floats, and numeric strings (the renderer sometimes
    sends strings from form inputs).  Rejects anything else with the
    default.  Result is always in ``[1, _HISTORY_LIMIT_MAX]``.
    """
    if raw is None:
        return _HISTORY_LIMIT_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _HISTORY_LIMIT_DEFAULT
    return max(1, min(v, _HISTORY_LIMIT_MAX))


def _bound_history_offset(raw) -> int:
    """Clamp a caller-supplied history ``offset`` to a non-negative int."""
    if raw is None:
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def _sanitize_config_for_ipc(config) -> dict:
    """Return a copy of ``config.__dict__`` with secret fields redacted.

    A secret field is any field in :data:`_SECRET_CONFIG_FIELDS`.  If
    the field's value is truthy (a key was set), it is replaced with
    ``"<redacted>"``.  If falsy (empty string or None), the original
    value (``""`` / ``None``) is preserved so the renderer can
    distinguish "no key set" from "key set but hidden".
    """
    out = config.__dict__.copy()
    for k in _SECRET_CONFIG_FIELDS:
        if k in out:
            v = out[k]
            out[k] = _REDACTED_SENTINEL if v else v
    return out


__all__ = [
    "_bound_history_limit",
    "_bound_history_offset",
    "_sanitize_config_for_ipc",
    "_SECRET_CONFIG_FIELDS",
    "_REDACTED_SENTINEL",
    "_HISTORY_LIMIT_MAX",
    "_HISTORY_LIMIT_DEFAULT",
]
