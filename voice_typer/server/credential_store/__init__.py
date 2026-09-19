"""Encrypted credential store for API keys via the OS keychain."""

from __future__ import annotations

import sys
from types import ModuleType

# Bare re-exports, functions / constants / locks / in-place-mutated
from ._backend import (
    _KEYRING_ORPHAN_WARN_THRESHOLD,
    _KEYRING_REPROBE_INTERVAL_SECONDS,
    _KEYRING_TIMEOUT_SECONDS,
    _KEYRING_WEDGE_COOLDOWN_S,
    _clear_plaintext_config_cache,
    _keyring_probe_lock,
    _keyring_state_lock,
    _plaintext_config_cache,
    _probe_keyring,
    _reset_keyring_cache,
    _run_keyring_call,
    get_keyring_status,
    is_keyring_available,
)
from ._crud import clear_in_memory_secrets, delete_secret, load_secret, store_secret
from ._migration import (
    _MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS,
    _MIGRATION_LOCK_TIMEOUT_SECONDS,
    _acquire_migration_lock,
    _is_windows,
    _migrate_legacy_service_names_locked,
    _migrate_secrets_to_keyring_locked,
    migrate_secrets_to_keyring,
)
from ._outcome import (
    _last_store_outcome,
    _set_last_store_outcome,
    last_store_outcome,
)
from ._plaintext import (
    _read_plaintext_fallback,
    _write_plaintext_fallback,
)
from ._redact import _PATH_RE, _redact_sensitive
from ._schema import (
    _KNOWN_PROVIDERS_HISTORY,
    _LEGACY_KEYRING_SERVICE_NAMES,
    _REASON_MAX_LEN,
    _SERVICE_NAME_MIGRATED_FLAG,
    _T,
    CONFIG_FIELD_TO_PROVIDER,
    KEYRING_REF_PREFIX,
    KEYRING_SERVICE_NAME,
    PROVIDER_TO_CONFIG_FIELD,
    log,
)

__all__ = [
    "KEYRING_REF_PREFIX",
    "KEYRING_SERVICE_NAME",
    "PROVIDER_TO_CONFIG_FIELD",
    "CONFIG_FIELD_TO_PROVIDER",
    "clear_in_memory_secrets",
    "delete_secret",
    "get_keyring_status",
    "is_keyring_available",
    "load_secret",
    "migrate_secrets_to_keyring",
    "store_secret",
    # ── Re-exported internals (tests monkeypatch / inspect these via the
    "_KNOWN_PROVIDERS_HISTORY",
    "_LEGACY_KEYRING_SERVICE_NAMES",
    "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS",
    "_MIGRATION_LOCK_TIMEOUT_SECONDS",
    "_PATH_RE",
    "_REASON_MAX_LEN",
    "_SERVICE_NAME_MIGRATED_FLAG",
    "_T",
    "_acquire_migration_lock",
    "_clear_plaintext_config_cache",
    "_consecutive_timeouts",
    "_is_windows",
    "_keyring_available_cache",
    "_keyring_backend_name_cache",
    "_keyring_last_probe_ts",
    "_keyring_probe_lock",
    "_keyring_reason_cache",
    "_keyring_state_lock",
    "_last_store_outcome",
    "_KEYRING_ORPHAN_WARN_THRESHOLD",
    "_KEYRING_REPROBE_INTERVAL_SECONDS",
    "_KEYRING_TIMEOUT_SECONDS",
    "_KEYRING_WEDGE_COOLDOWN_S",
    "_migrate_legacy_service_names_locked",
    "_migrate_secrets_to_keyring_locked",
    "_orphaned_thread_count",
    "_plaintext_config_cache",
    "_probe_keyring",
    "_read_plaintext_fallback",
    "_redact_sensitive",
    "_reset_keyring_cache",
    "_run_keyring_call",
    "_set_last_store_outcome",
    "_wedged_until",
    "_write_plaintext_fallback",
    "last_store_outcome",
    "log",
]


def __getattr__(name: str):
    """Lazy attribute lookup for mutable module globals from submodules."""
    from . import _backend, _migration, _outcome

    for mod in (_backend, _outcome, _migration):
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _PackageFacade(ModuleType):
    """Write-through attribute assignment for submodule-owned globals."""

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self.__dict__:
            from . import _backend, _migration, _outcome

            for mod in (_backend, _outcome, _migration):
                if hasattr(mod, name):
                    setattr(mod, name, value)
                    return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _PackageFacade
