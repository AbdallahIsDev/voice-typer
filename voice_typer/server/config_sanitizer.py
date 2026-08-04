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


def _redact_load_warning(warning: object) -> str:
    """Redact a single ``last_load_warnings`` entry for safe IPC transit.

    Load warnings are produced by ``Config.load()`` from a variety of
    sources (``validate_config``, ``apply_preset``, the per-field
    reset helpers). They may legitimately embed field values from
    ``config.json`` — e.g. an invalid ``asr_backend='invalid_backend'``
    produces ``"validate_config: asr_backend: must be one of [...],
    got 'invalid_backend'"``. Most of the time those values are
    innocuous enum strings, but a warning can also surface a
    malformed API key, a URL with embedded credentials, or a path
    containing the user's home directory.

    To stay strictly DENYLIST-BY-DEFAULT on the IPC boundary (the
    contract documented in the module docstring), each warning is:

    1. Coerced to ``str`` (warnings are nominally ``str`` but defensive
       coercion keeps a stray non-str entry from breaking the redaction
       pipeline).
    2. Truncated to 200 chars. A pathologically long warning (e.g. a
       ``custom_theme`` dict that failed validation and was stringified)
       would otherwise inflate the IPC payload and clutter the
       renderer's toast UI. 200 chars is enough for any realistic
       single-line warning and keeps the payload bounded.
    3. Run through :func:`voice_typer.server.security.redact_pii`,
       which (per its docstring) is a true single-call redaction helper
       that ALSO applies :func:`redact_secret` (API keys / bearer
       tokens) and :func:`redact_url` (URL userinfo). All three
       redactions are idempotent on already-redacted text.
    4. Run through :func:`voice_typer.server._secrets._redact_home_path`
       so filesystem paths containing the user's home directory (e.g.
       a ``legacy = Path.home() / ".voice-typer"`` migration message)
       are stripped of the home prefix. ``_redact_home_path`` is a
       NO-OP for paths that don't start with the configured home, so
       innocuous enum strings (e.g. ``"parakeet"``) pass through
       unchanged. Applied AFTER ``redact_pii`` so the latter's
       already-redacted text (e.g. ``[redacted]``) isn't mistakenly
       treated as a path.
    """
    text = str(warning)
    # Truncate FIRST so a multi-megabyte ``custom_theme`` dict dump
    # doesn't get passed through the regex redaction pipeline before
    # being cut down. The truncation point is generous (200 chars
    # covers any realistic single-line warning) and adds an ellipsis
    # so the renderer can show "warning was truncated" UI feedback.
    if len(text) > 200:
        text = text[:200] + "…"
    try:
        from voice_typer.server.security import redact_pii

        text = redact_pii(text)
    except Exception:
        # If the redaction pipeline itself raises (e.g. a regex
        # catastrophic backtracking on a pathological input, or the
        # security module is partially imported in a test sandbox),
        # fall back to the truncated text. NEVER raise from the
        # sanitizer — that would prevent the entire config payload
        # from reaching the renderer, which is strictly worse than
        # shipping an under-redacted warning.
        log.debug("[CONFIG-SANITIZER] redact_pii on load warning failed", exc_info=True)
    # Belt-and-suspenders: also strip any home-directory prefix
    # that slipped past ``redact_pii`` (which only handles PII
    # patterns, not filesystem paths). ``_redact_home_path`` is a
    # no-op when the path doesn't start with the configured home
    # so innocuous enum strings pass through untouched.
    try:
        from voice_typer.server._secrets import _redact_home_path

        text = _redact_home_path(text)
    except Exception:
        log.debug(
            "[CONFIG-SANITIZER] _redact_home_path on load warning failed",
            exc_info=True,
        )
    return text


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

    The ONE deliberate exception to "dataclass fields only" is
    ``last_load_warnings``. It's a plain instance attribute (set via
    ``object.__setattr__`` in :meth:`Config.__post_init__`), so
    :func:`dataclasses.asdict` excludes it. Without surfacing it, the
    renderer NEVER learns that the just-loaded config had invalid
    values — the user editing ``config.json`` by hand gets no toast,
    no IPC error, no UI banner. The renderer can act on these
    warnings (display a "Config loaded with N warnings" toast,
    highlight the offending field in the Settings UI, etc.). Each
    warning is run through :func:`_redact_load_warning` (truncate +
    :func:`redact_pii`) before transmission because warnings can
    embed field values that may themselves be sensitive (e.g. a
    malformed API key echoed back in an error message).
    """
    # ``dataclasses.asdict`` returns a deep-copied dict of ONLY
    # the declared dataclass fields. ``ClassVar`` fields (e.g.
    # ``_mutation_lock``) and plain instance attributes set in
    # ``__post_init__`` (``_last_saved_bytes``) are EXCLUDED
    # automatically — they're not in ``Config.__dataclass_fields__``.
    # ``FR-20`` regression guard
    # (tests/test_config_sanitizer.py::TestNoTransientAttributesLeaked)
    # pins this behavior for ``_last_saved_bytes`` and the
    # ``_mutation_lock`` ClassVar: the sanitizer must NOT leak
    # them to the IPC boundary — a same-user local process that
    # calls ``get_config`` should not learn about prior config
    # writes or schema-version migration details.
    #
    # ``last_load_warnings`` is the ONE deliberate exception. It's a
    # plain instance attribute set in ``__post_init__`` + ``load()``,
    # so ``dataclasses.asdict`` excludes it. We surface it (as a
    # redacted list) so the renderer can act on warnings that arose
    # during the just-completed load (display a "Config loaded with
    # N warnings" toast, highlight the offending field, etc.). The
    # raw values can embed field contents (e.g. an invalid
    # ``asr_backend='invalid_backend'`` is included verbatim in the
    # warning message), so each entry is run through
    # :func:`_redact_load_warning` (truncate + PII/URL/secret
    # redaction) before transmission.
    out: dict[str, Any] = dataclasses.asdict(config)
    for k in SECRET_CONFIG_FIELDS:
        if k in out:
            v = out[k]
            out[k] = REDACTED_SENTINEL if v else v
    # ``last_load_warnings`` is a list of strings (set in
    # ``Config.load()`` and its sub-paths). Default to an empty
    # list so the renderer can always rely on the key being
    # present and iterable, even on a fresh ``Config()`` (where
    # ``__post_init__`` initialises the attribute to ``None``).
    raw_warnings = getattr(config, "last_load_warnings", None) or []
    # Build a NEW list (not a slice) so the caller can't mutate
    # the in-memory ``config`` instance's state via the returned
    # dict's reference.
    out["last_load_warnings"] = [_redact_load_warning(w) for w in raw_warnings]
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
