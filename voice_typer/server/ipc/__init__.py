"""IPC package: auth, validation, rate-limit, history bounds leaves."""

# Eagerly import leaf submodules, these don't trigger handler imports.
from .auth import extract_auth_token, tokens_equal
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
    # auth
    "extract_auth_token",
    "tokens_equal",
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
