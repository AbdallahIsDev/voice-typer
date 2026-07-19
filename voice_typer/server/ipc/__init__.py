# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""IPC server package — JSON-lines IPC over stdin/stdout OR TCP.

Phase 4.5 / ARCH-045 — this file was previously a 2,565-line god-module
(``voice_typer/server/ipc_server.py``); it has been split into a
package with one module per concern:

- :func:`_validate_dict_payload` (IPC payload validation) — :mod:`.validation`
- :func:`_pick_available_port` + :class:`_TCPLineIO` (TCP transport) —
  :mod:`.transport`
- :class:`_RateLimiter` + :func:`_get_rate_limiter` (per-connection
  rate limiter) — :mod:`.rate_limiter`
- :func:`_bound_history_limit` / :func:`_bound_history_offset` /
  :func:`_sanitize_config_for_ipc` (history bounds + config sanitizer) —
  :mod:`.history_bounds`
- :func:`_push_event_now` (push-event publisher) — :mod:`.push_events`
- :class:`IPCServer` (the main IPC server class) — :mod:`.server`
- :func:`_set_process_metadata` (process metadata setter) —
  :mod:`.process_meta`
- :func:`main` (CLI entry point) — :mod:`.main`

This ``__init__.py`` re-exports every public name that the original
module exposed so existing imports of the form
``from voice_typer.server.ipc_server import X`` keep working without
modification (the thin ``ipc_server.py`` shim re-exports the same
names from this package).

Lazy loading for ``IPCServer`` and ``main``
-------------------------------------------
The handler mixins (under ``voice_typer/server/handlers/``) do
``from voice_typer.server.ipc_server import log, _validate_dict_payload, ...``
at module load time.  If this ``__init__.py`` eagerly imported
:mod:`.server` (which triggers the mixin imports), the chain would be:

    shim → ipc/__init__.py → ipc/server.py → handlers →
        from voice_typer.server.ipc_server import log, ...

At the point the handlers run their import, the shim has not yet
bound ``log`` (it's still in the middle of importing from
:mod:`.server`), causing a circular ``ImportError``.

To break the cycle, this ``__init__.py`` eagerly imports only the
LEAF submodules (those that don't depend on :mod:`.server` or
:mod:`.main`).  ``IPCServer`` and ``main`` are lazy-loaded via
``__getattr__`` so the shim can finish binding all the helper names
before :mod:`.server` is loaded.
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
from .process_meta import _set_process_metadata
from .push_events import _push_event_now
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

# IPCServer and main are lazy-loaded via __getattr__ below to avoid a
# circular import: ipc.server imports the handler mixins, which import
# helpers from voice_typer.server.ipc_server (the shim).  The shim must
# finish binding the helper names BEFORE ipc.server is loaded.

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
    # push_events
    "_push_event_now",
    # server
    "IPCServer",
    # process_meta
    "_set_process_metadata",
    # main
    "main",
]


# Names that are lazy-loaded on first attribute access.  Each entry
# maps the public name to ``(submodule_dotted_path, attr_name)``.
_LAZY_NAMES = {
    "IPCServer": ("voice_typer.server.ipc.server", "IPCServer"),
    "main": ("voice_typer.server.ipc.main", "main"),
}


def __getattr__(name):
    """Lazy-load ``IPCServer`` and ``main`` on first attribute access.

    See the module docstring for the rationale (avoiding a circular
    import with the handler mixins).  Once loaded, the value is cached
    in ``globals()`` so subsequent accesses skip ``__getattr__``.
    """
    if name in _LAZY_NAMES:
        import importlib

        module_path, attr_name = _LAZY_NAMES[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr_name)
        globals()[name] = value  # cache for future access
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
