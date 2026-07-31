"""Transport-neutral config sanitization for IPC transmission.

 the canonical implementation of ``sanitize_config_for_ipc``
lives here so the service layer (:mod:`voice_typer.server.service`) does not
have to reach DOWN into the IPC transport layer
(:mod:`voice_typer.server.ipc_server`) to redact secrets from a config
payload.  Both the service layer and the IPC layer should import from this
module.

Historical note: this logic was previously extracted from
``ipc_server.py`` into :mod:`voice_typer.server.ipc.history_bounds` during
 (Phase 4.5 split).  That module still re-exports
``_sanitize_config_for_ipc`` for backwards-compat with any external
importer, but new code should import :func:`sanitize_config_for_ipc`
(public name) from here.

this module is now the canonical home for the underscore-prefixed
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

import dataclasses
import logging
from typing import Any

log = logging.getLogger("voice_typer.server.config_sanitizer")


# ``SECRET_CONFIG_FIELDS`` is now STRUCTURALLY DERIVED from
# ``credential_store.PROVIDER_TO_CONFIG_FIELD.values()`` at import time
# (not a hand-maintained frozenset). Previously the two lists were
# maintained independently — if a contributor added a new provider to
# ``PROVIDER_TO_CONFIG_FIELD`` (e.g. ``"mistral": "mistral_api_key"``)
# but forgot to add the matching entry to ``SECRET_CONFIG_FIELDS``,
# the sanitizer would echo the new API key in plaintext over the
# loopback IPC socket (SEC-003 regression). The structural link makes
# the invariant self-enforcing.
#
# The import is lazy (function-local) inside the ``_derive_secret_fields``
# helper below to avoid a circular import at module load:
# ``credential_store`` imports from ``voice_typer.server.config`` (which
# re-exports from here) inside ``migrate_secrets_to_keyring``. Importing
# ``credential_store`` at module top would pull in ``config`` → this
# module → ``credential_store`` → ``config``. The function-local import
# breaks the cycle (the import only fires when ``_derive_secret_fields()``
# is called during this module's top-level execution, by which point
# ``config``'s import is not yet in flight on this call stack).
#
# We compute it eagerly at import time so it's available as a module
# attribute (and so import-time typos in ``PROVIDER_TO_CONFIG_FIELD``
# surface immediately).
#
# FAIL-CLOSED: the helper does NOT fall back to a hardcoded literal
# frozenset on import failure. A silent fallback to a stale 5-field
# set would leave any newly added provider's API key un-redacted and
# echoed in plaintext over IPC (SEC-003 regression). Instead the helper
# logs ``CRITICAL`` and re-raises — the application refuses to start
# with broken secret redaction, which is the intended fail-closed
# behavior.
def _derive_secret_fields() -> frozenset[str]:
    """Derive SECRET_CONFIG_FIELDS from credential_store (fail-closed).

    Wrapped in a function so the lazy import of ``credential_store``
    happens AFTER this module's top-level body has finished executing
    (avoiding the circular import described above).

    SECURITY (fail-closed): if the import of
    ``PROVIDER_TO_CONFIG_FIELD`` fails for ANY reason (broken install,
    sandbox without the package, partial-import during test
    collection, future refactor that breaks the import path), we log
    ``CRITICAL`` and RE-RAISE. We do NOT fall back to a hardcoded
    literal: a silent fallback to a stale 5-field set would leave any
    newly added provider's API key un-redacted and echoed in plaintext
    over the loopback IPC socket (SEC-003 regression). Failing the
    import loudly surfaces the breakage immediately at startup, which
    is strictly safer than silently degrading the redaction boundary.
    Callers that depend on ``SECRET_CONFIG_FIELDS`` (the IPC server,
    crash recovery, the service layer) will fail to import this
    module, and the application will refuse to start with broken
    secret redaction — the intended fail-closed behavior.
    """
    try:
        from voice_typer.server.credential_store import PROVIDER_TO_CONFIG_FIELD

        return frozenset(PROVIDER_TO_CONFIG_FIELD.values())
    except Exception as exc:
        # Fail-closed: do NOT fall back to a hardcoded literal set.
        # A silent fallback would mask a broken install / sandbox and
        # could leave newly added provider API keys un-redacted over
        # IPC (SEC-003 regression). Re-raise so the breakage is loud
        # and immediate at startup.
        log.critical(
            "[CONFIG-SANITIZER] Failed to import PROVIDER_TO_CONFIG_FIELD "
            "from credential_store — secret field redaction may be "
            "incomplete. Refusing to fall back to a hardcoded literal "
            "(fail-closed). Original error: %s",
            exc,
        )
        raise


SECRET_CONFIG_FIELDS: frozenset[str] = _derive_secret_fields()

# underscore-prefixed alias kept for backward compat with
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

    Returns a dict containing ONLY the declared dataclass fields of
    ``config`` (via :func:`dataclasses.asdict`), with secret fields
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

    previously used ``dict(config.__dict__)`` which leaked
    transient / private attributes that are NOT dataclass fields
    (``_last_saved_bytes``, ``last_load_warnings``, etc.). The
    sanitizer is a security boundary: it should be DENYLIST-BY-DEFAULT
    (only declared dataclass fields pass) rather than ALLOWLIST-BY-
    DEFAULT (everything passes; redact list explicit). Switching to
    :func:`dataclasses.asdict` enforces that the output is exactly the
    set of declared Config fields — no more, no less.
    """
    # ``dataclasses.asdict`` returns a deep-copied dict of ONLY
    # the declared dataclass fields. ``ClassVar`` fields (e.g.
    # ``_mutation_lock``) and plain instance attributes set in
    # ``__post_init__`` (``_last_saved_bytes``, ``last_load_warnings``)
    # are EXCLUDED automatically — they're not in
    # ``Config.__dataclass_fields__``.
    out: dict[str, Any] = dataclasses.asdict(config)
    for k in SECRET_CONFIG_FIELDS:
        if k in out:
            v = out[k]
            out[k] = REDACTED_SENTINEL if v else v
    return out


# underscore-prefixed alias for backward compat with
# :mod:`voice_typer.server.ipc.history_bounds` and any external importer
# that already uses the underscore form. Same callable object — alias,
# not a wrapper.
_sanitize_config_for_ipc = sanitize_config_for_ipc


__all__ = [
    "sanitize_config_for_ipc",
    "SECRET_CONFIG_FIELDS",
    "REDACTED_SENTINEL",
    # underscore aliases (backward-compat re-exports).
    "_sanitize_config_for_ipc",
    "_SECRET_CONFIG_FIELDS",
]
