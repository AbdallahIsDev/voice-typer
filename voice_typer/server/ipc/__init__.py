"""IPC server leaf-submodule package — validation, transport, rate-limiting, history bounds.

Phase 4.5 /  began a split of the original ``ipc_server.py``
god-module into a package with one module per concern.  The split was
abandoned mid-way and the parallel ``server.py`` / ``main.py`` /
``process_meta.py`` / ``push_events.py`` implementations became dead
code ().  This package now contains ONLY the leaf submodules that
are actually imported by the handler mixins:

- :func:`_validate_dict_payload` (IPC payload validation) — :mod:`.validation`
- :func:`_pick_available_port` + :class:`_TCPLineIO` (TCP transport) —
  :mod:`.transport`
- :class:`_RateLimiter` + :func:`_get_rate_limiter` (per-connection
  rate limiter) — :mod:`.rate_limiter`
- :func:`_bound_history_limit` / :func:`_bound_history_offset` /
  :func:`_sanitize_config_for_ipc` (history bounds + config sanitizer) —
  :mod:`.history_bounds`

The live ``IPCServer`` class, ``main`` entry point,
``_set_process_metadata`` and ``_push_event_now`` all live in the
canonical ``voice_typer.server.ipc_server`` module (the shim that
retains the full implementation).  This package does NOT re-export
them — import them from ``voice_typer.server.ipc_server`` directly.

This ``__init__.py`` eagerly imports the leaf submodules so callers
that do ``from voice_typer.server.ipc.validation import ...`` (the
handler mixins) get the canonical objects.  No lazy loading is needed
because none of the surviving submodules trigger the handler-mixin
import cycle that the original split was working around.
"""

# Eagerly import leaf submodules — these don't trigger handler imports.
from .history_bounds import (
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _REDACTED_SENTINEL,
    _SECRET_CONFIG_FIELDS,
    _bound_history_limit,
    _bound_history_offset,
    _sanitize_config_for_ipc,
)
from .rate_limiter import (
    _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS,
    _HEARTBEAT_INTERVAL_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
    _RATE_LIMIT_BURST,
    _RATE_LIMIT_BURST_WINDOW_SECONDS,
    _RATE_LIMIT_SUSTAINED,
    _RATE_LIMIT_WINDOW_SECONDS,
    _TCP_WRITE_TIMEOUT_SECONDS,
    _get_rate_limiter,
    _RateLimiter,
)
from .transport import _pick_available_port, _TCPLineIO
from .validation import _validate_dict_payload

__all__ = [
    # validation
    "_validate_dict_payload",
    # transport
    "_pick_available_port",
    "_TCPLineIO",
    # rate_limiter
    "_RateLimiter",
    "_get_rate_limiter",
    "_RATE_LIMIT_WINDOW_SECONDS",
    "_RATE_LIMIT_BURST_WINDOW_SECONDS",
    "_RATE_LIMIT_BURST",
    "_RATE_LIMIT_SUSTAINED",
    "_TCP_WRITE_TIMEOUT_SECONDS",
    "_HEARTBEAT_INTERVAL_SECONDS",
    "_HEARTBEAT_TIMEOUT_SECONDS",
    "_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
    # history_bounds
    "_bound_history_limit",
    "_bound_history_offset",
    "_sanitize_config_for_ipc",
    "_SECRET_CONFIG_FIELDS",
    "_REDACTED_SENTINEL",
    "_HISTORY_LIMIT_MAX",
    "_HISTORY_LIMIT_DEFAULT",
]
