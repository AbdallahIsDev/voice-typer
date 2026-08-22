"""Encrypted credential store for API keys via the OS keychain.

API keys for cloud providers (OpenAI / Groq / Deepgram) and the
LLM polishing service are stored via the ``keyring`` library, which
auto-selects the appropriate OS-native backend at runtime:

  - Windows: Windows Credential Manager
  - macOS:   Keychain
  - Linux:   Secret Service (libsecret / GNOME Keyring / KWallet)

When no usable backend is available (most commonly on a headless Linux
container without ``gnome-keyring-daemon`` and ``python-dbus``), the
store falls back to the legacy behavior: plaintext in ``config.json``
with ``0o600`` permissions on POSIX.

Design notes
------------

- ``config.json`` never contains the actual secret when keyring is
  available. Instead it stores a *reference token* of the form
  ``"keyring://<provider>"`` in the existing flat ``<provider>_api_key``
  field. The real value only leaves the keychain in the Python process
  that needs it (``cloud_engines.py`` / ``llm_polish.py``).

- ``store_secret`` never raises — it logs a warning and falls back to
  plaintext on any keyring failure. This means a broken D-Bus or a
  locked Keychain never prevents the user from saving their API key.

- Secret values are NEVER logged. Only metadata (provider name, value
  length, keyring-vs-fallback status) appears in log messages. Defense
  in depth: keyring exception messages are passed through
  :func:`_redact_sensitive` before being logged or surfaced to the
  renderer via ``get_keyring_status``.

- **Reference-token unforgeability**: the ``keyring://<provider>``
  suffix in a reference token is NEVER used to look up the secret.
  ``Config.load()`` iterates :data:`PROVIDER_TO_CONFIG_FIELD` and calls
  ``load_secret(provider)`` with the provider matched to the *field*
  (``CONFIG_FIELD_TO_PROVIDER``), ignoring the token's suffix. A
  malicious ``config.json`` that puts ``"keyring://llm"`` in
  ``openai_api_key`` cannot trick the loader into returning the LLM
  secret.

- **Two-instance migration race (closed)**: the ``secrets_migrated``
  flag in ``config.json`` is guarded by an exclusive cross-process
  lock (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows)
  acquired on ``config.json.lock``. :func:`migrate_secrets_to_keyring`
  acquires the lock before reading config.json, RE-READS the file once
  the lock is held (so a concurrent migration that completed while we
  waited is observed), and only then proceeds with the
  read-migrate-write sequence.

Package layout
--------------

This package was split (from a single ~2132-line module) into seven
submodules organized by concern:

- :mod:`._schema`    — constants & provider map.
- :mod:`._redact`    — defense-in-depth redaction patterns.
- :mod:`._outcome`   — thread-local outcome recording.
- :mod:`._backend`   — keyring availability probing + global caches.
- :mod:`._plaintext` — plaintext fallback read/write.
- :mod:`._crud`      — secret CRUD operations.
- :mod:`._migration` — cross-process lock + migration logic.

The public API surface is preserved 1:1 via re-exports below. Tests
that monkey-patch module-level symbols (e.g.
``monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)``)
continue to work because the consuming call sites look those symbols up
on the package module (``_cs.<NAME>``) at call time, not via bare-name
global lookup against the submodule that defines them.

Mutable-scalar propagation
--------------------------

Several module-level scalars in :mod:`._backend` are *re-bound* at
runtime (e.g. ``_orphaned_thread_count`` is incremented when a keyring
I/O worker is orphaned; ``_keyring_available_cache`` flips from
``None`` to ``True`` / ``False`` after the first probe). A static
``from ._backend import _orphaned_thread_count`` would snapshot the
initial value (``0``) and stay stale — readers accessing
``credential_store._orphaned_thread_count`` would always see ``0``
regardless of how many orphans accumulated.

These re-bound scalars are intentionally NOT statically re-exported
below. They are surfaced via the PEP 562 ``__getattr__`` hook at the
bottom of this module, which lazily delegates each lookup to the
owning submodule (``_backend`` / ``_outcome`` / ``_migration``) so
runtime re-binds propagate to callers reading the value via the
package module. Functions, constants, locks, and in-place-mutated
containers (dicts) have stable identity and remain statically imported
for attribute-access speed.

Cross-platform testing notes are in ``docs/security/credential-store.md``.
"""

from __future__ import annotations

# ── Backend: timeout isolation + caches + probe ──────────────────────────
# Bare re-exports — functions / constants / locks / in-place-mutated
# containers only. Re-bound scalars (``_orphaned_thread_count``,
# ``_consecutive_timeouts``, ``_wedged_until``, ``_keyring_available_cache``,
# ``_keyring_backend_name_cache``, ``_keyring_last_probe_ts``,
# ``_keyring_reason_cache``) are deliberately OMITTED here so the
# ``__getattr__`` hook below is invoked for them (static imports would
# snapshot the initial value and stay stale when the submodule rebinds
# the name — see the module docstring's "Mutable-scalar propagation"
# section). Tests that monkeypatch these names still work because
# ``monkeypatch.setattr`` writes to this package's ``__dict__`` directly,
# shadowing the ``__getattr__`` fallback.
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

# ── CRUD ──────────────────────────────────────────────────────────────────
from ._crud import clear_in_memory_secrets, delete_secret, load_secret, store_secret

# ── Migration ─────────────────────────────────────────────────────────────
from ._migration import (
    _MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS,
    _MIGRATION_LOCK_TIMEOUT_SECONDS,
    _acquire_migration_lock,
    _is_windows,
    _migrate_legacy_service_names_locked,
    _migrate_secrets_to_keyring_locked,
    migrate_secrets_to_keyring,
)

# ── Outcome recording ────────────────────────────────────────────────────
from ._outcome import (
    _last_store_outcome,
    _set_last_store_outcome,
    last_store_outcome,
)

# ── Plaintext fallback ───────────────────────────────────────────────────
from ._plaintext import (
    _read_plaintext_fallback,
    _write_plaintext_fallback,
)

# ── Redaction ────────────────────────────────────────────────────────────
from ._redact import _PATH_RE, _redact_sensitive

# ── Schema: constants & provider map ─────────────────────────────────────
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
    # ── Public API (stable, externally contracted) ──────────────────────
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
    # package module — see ``_backend._cs = sys.modules[...]`` pattern).
    # Listed here so ruff treats the static imports above as intentional
    # re-exports (F401) and so ``from voice_typer.server.credential_store
    # import *`` surfaces every name a test or sibling module might reach
    # for. Mutable scalars (``_orphaned_thread_count`` etc.) are surfaced
    # via ``__getattr__`` below — they are still listed here so
    # ``hasattr(credential_store, name)`` and ``dir(credential_store)``
    # both report them.
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
    """Lazy attribute lookup for mutable module globals from submodules.

    Module-level scalars in :mod:`._backend`, :mod:`._outcome`, and
    :mod:`._migration` are re-bound at runtime (e.g.
    ``_orphaned_thread_count`` is incremented when a keyring I/O thread
    is orphaned; ``_keyring_available_cache`` is set to ``True`` /
    ``False`` after the first probe). Static imports
    (``from ._backend import _orphaned_thread_count``) snapshot the
    initial value and stay stale — ``credential_store._orphaned_thread_count``
    would always read ``0`` regardless of how many orphans accumulated.

    This PEP 562 hook delegates each lookup to the owning submodule so
    mutations propagate. It is only invoked for names NOT already in
    this module's ``__dict__`` (functions, constants, locks, and
    in-place-mutated containers remain statically imported above for
    speed); ``monkeypatch.setattr`` writes to ``__dict__`` directly
    and so still overrides this fallback during tests.
    """
    from . import _backend, _migration, _outcome

    for mod in (_backend, _outcome, _migration):
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
