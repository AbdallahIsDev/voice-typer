"""Secret CRUD operations (store / load / delete / in-memory clear).

Owns the "secret CRUD" concern of the credential-store package
(split from the single ~2132-line ``credential_store.py`` module).
The four public operations re-exported by the package:

- :func:`store_secret` — store a secret in the OS keychain, falling
  back to plaintext in ``config.json`` when keyring is unavailable
  or errors (never raises),
- :func:`load_secret` — load a secret, trying keyring first,
- :func:`delete_secret` — best-effort delete from both stores +
  orphaned-keychain-entry cleanup,
- :func:`clear_in_memory_secrets` — zero every API-key attribute on
  the in-memory ``Config`` (GDPR Art. 17 support).

Monkeypatch contract: every helper owned by a SIBLING concern
(``is_keyring_available`` / ``_run_keyring_call`` /
``_keyring_backend_name_cache`` from :mod:`._backend`,
``_KNOWN_PROVIDERS_HISTORY`` from :mod:`._schema`,
``_read_plaintext_fallback`` / ``_write_plaintext_fallback`` from
:mod:`._plaintext`, ``_clear_plaintext_config_cache`` from
:mod:`._backend`, ``_set_last_store_outcome`` from :mod:`._outcome`)
is looked up on the PACKAGE module (``_cs.<NAME>``) at CALL time —
not bound statically here — so tests doing
``monkeypatch.setattr(credential_store, "<name>", ...)`` keep taking
effect through these functions (the same convention documented in the
package docstring and used by ``_plaintext.py``).
"""

from __future__ import annotations

import sys
from typing import Any

from ._redact import _redact_sensitive
from ._schema import (
    KEYRING_SERVICE_NAME,
    PROVIDER_TO_CONFIG_FIELD,
    log,
)

#: Look up the package module so sibling-concern helpers that tests
#: monkey-patch on ``voice_typer.server.credential_store`` are resolved
#: through the patched binding at call time (see module docstring).
_cs = sys.modules["voice_typer.server.credential_store"]


def store_secret(provider: str, value: str, *, _caller_holds_config_lock: bool = False) -> bool:
    """Store a secret for ``provider`` in the OS keychain.

    Parameters
    ----------
    provider : str
        Provider name (one of the keys in :data:`PROVIDER_TO_CONFIG_FIELD`).
    value : str
        The secret value to store. An empty string is treated as a
        delete request — the secret is removed from both keyring and
        the plaintext fallback.
    _caller_holds_config_lock : bool
        When ``True``, indicates the caller (e.g.
        ``Config._save_unlocked``) already holds the cross-process
        ``config.json.lock``. The plaintext-fallback write then SKIPS
        re-acquiring the lock (which would deadlock — fcntl.flock is
        per-open-file-description, NOT per-fd, so a second LOCK_EX on
        a fresh fd in the same process blocks forever). Defaults to
        ``False`` for backwards compat with all existing callers.

    Returns
    -------
    bool
        True if the secret was stored in keyring (or deleted via the
        empty-value path). False if keyring was unavailable or errored
        and the secret was written to config.json as a plaintext
        fallback (with ``0o600`` perms on POSIX).

        To surface *why* the store fell back to plaintext to the IPC
        caller, call :func:`last_store_outcome` immediately after this
        function returns on the same thread. It returns a dict
        ``{"stored_in": "keyring"|"plaintext"|"deleted"|"failed"|"unknown",
        "reason": str | None, "provider": str | None}`` matching the most
        recent call to ``store_secret`` on this thread. The boolean
        return value alone is preserved for backwards compat with every
        existing caller.

        ``"failed"`` indicates the secret was NOT saved anywhere —
        keyring failed AND the plaintext fallback also failed (e.g.
        corrupt config.json, disk error). The renderer should show a
        distinct error so the user knows their API key was dropped
        (vs. saved in plaintext).

    Notes
    -----
    This function NEVER raises. Any keyring error is caught, logged
    (with provider name + value length only — never the value itself),
    and the secret is written to config.json as a fallback. This means
    a broken D-Bus or locked Keychain never prevents the user from
    saving their API key.

    Thread-safety: the outcome record (read via
    :func:`last_store_outcome`) is thread-local, so concurrent
    ``store_secret`` calls on different IPC handler threads do not
    stomp each other's outcome. The IPC handler always calls
    ``store_secret`` and ``last_store_outcome`` on the same thread
    (no inter-thread hand-off).
    """
    # Reject unknown providers BEFORE any other logic. A typo'd or
    # deprecated provider name (e.g. "openai_v2", "OpenAI") would
    # otherwise be stored in the keychain under that name and never
    # cleaned up by the GDPR delete path (the privacy service iterates
    # PROVIDER_TO_CONFIG_FIELD, so an entry stored under a name NOT in
    # that map is an orphan). delete_secret iterates
    # _KNOWN_PROVIDERS_HISTORY to clean up PRE-EXISTING orphans, but
    # this validation prevents NEW orphans from being created in the
    # first place. The empty-value (delete) path is also rejected here
    # — callers who want to clear a stale orphaned entry must use
    # delete_secret directly (which iterates the history).
    if provider not in PROVIDER_TO_CONFIG_FIELD:
        log.warning(
            "[CREDENTIAL_STORE] rejecting store_secret for unknown provider=%r "
            "(not in PROVIDER_TO_CONFIG_FIELD) — prevents orphaned OS-keychain entries",
            provider,
        )
        _cs._set_last_store_outcome(
            "plaintext",
            f"unknown provider {provider!r}",
            provider=provider,
        )
        return False

    if not value:
        # Empty value = delete. Remove from both stores to keep them
        # in sync (the keyring might have a stale entry from a prior
        # successful store that we now want to clear).
        delete_secret(provider)
        # record the delete outcome so the IPC ack can
        # distinguish "stored in keyring" from "deleted" without
        # inspecting the value the caller passed (which we no longer
        # have by the time the ack is built).
        _cs._set_last_store_outcome("deleted", None, provider=provider)
        return True

    # defensive type guard for truthy non-string values. The
    # IPC layer validates ``value`` is a string before calling here,
    # but a buggy caller or a hand-edited config can leak a non-string
    # truthy value (e.g. int ``12345`` from an old config that stored
    # api_key as int, or a dict / list from a corrupted config.json).
    # Without this guard, ``len(value)`` in the ``except Exception``
    # branch below would raise ``TypeError`` (e.g. ``len(12345)``)
    # which propagates up through the IPC handler thread and crashes
    # the save.
    #
    # Coerce int/float (excluding bool, which is a subclass of int in
    # Python) to str — backward compat with old configs that stored
    # api_key as an int. Reject other non-string truthy types (dict,
    # list) with a warning + ``plaintext`` outcome (the secret is NOT
    # written — the caller must fix the config).
    if not isinstance(value, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            log.warning(
                "[CREDENTIAL_STORE] received non-string value"
                " for provider=%s (type=%s) — coercing to str",
                provider,
                type(value).__name__,
            )
            value = str(value)
        else:
            log.warning(
                "[CREDENTIAL_STORE] received non-string value"
                " for provider=%s (type=%s) — rejecting",
                provider,
                type(value).__name__,
            )
            _cs._set_last_store_outcome(
                "plaintext",
                f"non-string value type {type(value).__name__}",
                provider=provider,
            )
            return False

    try:
        if not _cs.is_keyring_available():
            raise RuntimeError("keyring backend not available")
        import keyring  # noqa: PLC0415 — optional dependency, imported lazily

        # wrap set_password in a finite timeout so a hung
        # D-Bus / Keychain on the IPC set_config thread doesn't stall
        # the server. On timeout we fall through to the plaintext
        # fallback (the except branch below).
        _cs._run_keyring_call(keyring.set_password, KEYRING_SERVICE_NAME, provider, value)
        log.info(
            "[CREDENTIAL_STORE] stored secret for provider=%s (len=%d) in keyring backend=%s",
            provider,
            len(value),
            _cs._keyring_backend_name_cache,
        )
        # record the success outcome.
        _cs._set_last_store_outcome("keyring", None, provider=provider)
        return True
    except Exception as e:
        # NEVER log the value — only metadata. The provider name is
        # not sensitive (it's "openai" / "groq" / etc.) and the length
        # is useful for debugging without revealing the secret.
        # _redact_sensitive strips paths / API-key-like substrings from
        # the exception text — defense in depth in case a buggy backend
        # embeds the value in its error message.
        redacted_reason = _redact_sensitive(str(e))
        log.warning(
            "[CREDENTIAL_STORE] keyring store failed for provider=%s (len=%d): %s — "
            "falling back to plaintext in config.json",
            provider,
            len(value),
            redacted_reason,
        )
        # _write_plaintext_fallback returns bool — check it
        # so we can surface a distinct "failed" outcome when the
        # plaintext fallback itself failed (e.g. corrupt config.json,
        # disk error). If it returned None and swallowed all errors
        # internally, store_secret could never detect a fallback
        # failure — the user's API key would be silently dropped
        # (not in keyring, not in config.json) while the outcome
        # still said "plaintext".
        ok = _cs._write_plaintext_fallback(provider, value, caller_holds_config_lock=_caller_holds_config_lock)
        if not ok:
            # The plaintext fallback write failed — the secret was NOT
            # saved anywhere. Surface a distinct "failed" outcome so
            # the renderer can tell the user their API key was not
            # saved (vs. saved in plaintext). The detailed reason was
            # already logged by _write_plaintext_fallback (with
            # redaction); the outcome reason is a short summary.
            _cs._set_last_store_outcome(
                "failed",
                f"plaintext fallback write failed after keyring error: {redacted_reason}",
                provider=provider,
            )
            return False
        # record the fallback outcome (with the redacted reason)
        # so the IPC handler can include the reason in the ack payload
        # the renderer shows to the user.
        _cs._set_last_store_outcome("plaintext", redacted_reason, provider=provider)
        return False


def load_secret(provider: str) -> str | None:
    """Load a secret for ``provider``.

    Tries keyring first. If keyring returns a value, returns it. If
    keyring is unavailable or returns None, falls back to reading from
    config.json's flat ``<provider>_api_key`` field.

    Returns
    -------
    str | None
        The secret value, or None if not found in either store.

    Notes
    -----
    Never raises. Any keyring error is caught and the fallback is
    attempted. If the fallback also fails (e.g. config.json missing),
    returns None — the caller (typically ``Config.load``) treats this
    as "no key configured".
    """
    try:
        if _cs.is_keyring_available():
            import keyring  # noqa: PLC0415 — optional dependency, imported lazily

            # wrap get_password in a finite timeout so a hung
            # D-Bus / Keychain on the Config.load() path doesn't stall
            # startup (load_secret runs once per provider × 5
            # providers). On timeout we fall through to the plaintext
            # fallback below. The explicit annotation keeps ``value``
            # typed (the facade attribute lookup on ``_cs`` is Any).
            value: str | None = _cs._run_keyring_call(keyring.get_password, KEYRING_SERVICE_NAME, provider)
            if value:
                # emit an INFO audit log so operators can
                # confirm secrets are being loaded from keyring (not
                # the plaintext fallback) at startup.
                log.info(
                    "[CREDENTIAL_STORE] loaded secret for provider=%s (len=%d) from keyring",
                    provider,
                    len(value),
                )
                return value
            # keyring returned None — secret not in keychain. Fall
            # through to plaintext fallback in case the user is
            # mid-migration (key added before keyring was available,
            # not yet migrated).
    except Exception as e:
        # _redact_sensitive strips paths / API-key-like substrings from
        # the exception text — defense in depth in case a buggy backend
        # embeds the value in its error message.
        log.warning(
            "[CREDENTIAL_STORE] keyring load failed for provider=%s: %s — trying plaintext fallback in config.json",
            provider,
            _redact_sensitive(str(e)),
        )

    # Explicit annotation — the ``_cs`` facade lookup is untyped (Any).
    plaintext: str | None = _cs._read_plaintext_fallback(provider)
    return plaintext


def delete_secret(provider: str, config: Any = None) -> None:
    """Delete a secret from both keyring and config.json.

    Never raises. Errors are logged at debug level (this is best-effort
    cleanup — a failure to delete from a broken keyring is not fatal,
    since the keyring is presumably already inaccessible).

    ``config`` is an optional in-memory ``Config`` dataclass instance.
    When provided, the corresponding ``<provider>_api_key`` attribute
    (see :data:`PROVIDER_TO_CONFIG_FIELD`) is reset to ``""`` so the
    running process stops seeing the old value. Without this, callers
    like the GDPR Art. 17 ``delete_all_personal_data`` handler would
    erase the on-disk / keychain secret but leave the in-memory
    ``Config`` attribute holding the plaintext value — meaning cloud
    engines and LLM polishers continue to use the "deleted" key until
    the process restarts. ``config`` is optional so existing callers
    (which only clear the on-disk store) keep working unchanged.
    """
    # Try keyring first
    try:
        if _cs.is_keyring_available():
            import keyring  # noqa: PLC0415 — optional dependency, imported lazily

            try:
                # wrap delete_password in a finite timeout.
                # delete_secret is best-effort cleanup (failure here is
                # non-fatal — the keyring is presumably already
                # inaccessible), so a timeout just logs at debug and
                # moves on.
                _cs._run_keyring_call(keyring.delete_password, KEYRING_SERVICE_NAME, provider)
                log.info(
                    "[CREDENTIAL_STORE] deleted secret for provider=%s from keyring",
                    provider,
                )
            except Exception as e:
                # PasswordDeleteError is raised when the secret doesn't
                # exist — that's fine, we're deleting anyway.
                log.debug(
                    "[CREDENTIAL_STORE] keyring delete for provider=%s raised: %s",
                    provider,
                    _redact_sensitive(str(e)),
                )

            # Orphan cleanup: iterate _KNOWN_PROVIDERS_HISTORY and delete
            # any historical / deprecated / typo'd provider-name entries
            # from the keychain. The privacy service's GDPR loop only
            # iterates PROVIDER_TO_CONFIG_FIELD (current providers), so
            # entries stored under names NOT in that map would otherwise
            # be orphans. We skip (a) the specific provider passed to
            # this call (already deleted above) and (b) current providers
            # (the privacy service's per-provider loop handles them —
            # re-deleting would be redundant). This runs on EVERY
            # delete_secret call (including the store_secret empty-value
            # path), so a single GDPR flow cleans up all known orphans
            # via the first delete_secret invocation; subsequent calls
            # re-attempt the same deletes (no-op, idempotent).
            for historical_provider in _cs._KNOWN_PROVIDERS_HISTORY:
                if historical_provider == provider:
                    continue  # already deleted above
                if historical_provider in PROVIDER_TO_CONFIG_FIELD:
                    continue  # privacy service's per-provider loop handles these
                try:
                    _cs._run_keyring_call(
                        keyring.delete_password,
                        KEYRING_SERVICE_NAME,
                        historical_provider,
                    )
                    log.info(
                        "[CREDENTIAL_STORE] deleted orphaned keychain entry for historical provider=%s",
                        historical_provider,
                    )
                except Exception as e:
                    log.debug(
                        "[CREDENTIAL_STORE] keychain delete for historical provider=%s raised: %s",
                        historical_provider,
                        _redact_sensitive(str(e)),
                    )
    except Exception as e:
        log.debug(
            "[CREDENTIAL_STORE] keyring delete failed for provider=%s: %s",
            provider,
            _redact_sensitive(str(e)),
        )

    # Also clear from config.json (plaintext fallback or stale reference)
    try:
        _cs._write_plaintext_fallback(provider, "")
        # invalidate the parsed-config cache so the
        # stale dict (which may still contain the plaintext key) is not
        # retained in process memory after the GDPR delete.
        _cs._clear_plaintext_config_cache()
    except Exception as e:
        # A failure here means the plaintext
        # credential is STILL on disk — the opposite of what the user
        # requested. This MUST be visible at default log levels (not
        # debug) so the user knows to manually clean up config.json.
        # Keyring-delete failures above remain at debug (best-effort
        # cleanup of an already-inaccessible backend is non-fatal).
        log.warning(
            "[CREDENTIAL_STORE] credential for provider=%s may still be in config.json — manual cleanup required: %s",
            provider,
            _redact_sensitive(str(e)),
        )

    # also clear the in-memory Config attribute (when provided)
    # so the running process stops seeing the old value. ``setattr`` on
    # a dataclass field is safe — the field is a plain ``str``. We wrap
    # it in try/except because ``config`` may be a ``MagicMock`` in
    # tests (where setattr silently no-ops on real attrs but we still
    # want the call to be observable for assertions) or a partial
    # object missing the attribute.
    if config is not None:
        field = PROVIDER_TO_CONFIG_FIELD.get(provider)
        if field is not None:
            try:
                setattr(config, field, "")
            except Exception as e:
                log.debug(
                    "[CREDENTIAL_STORE] in-memory Config clear for provider=%s (field=%s) failed: %s",
                    provider,
                    field,
                    _redact_sensitive(str(e)),
                )


def clear_in_memory_secrets(config: Any) -> int:
    """Zero every API-key attribute on the in-memory ``Config``.

    GDPR Art. 17 ``delete_all_personal_data`` calls this
    after iterating :func:`delete_secret` over every provider so the
    running Python process stops holding the plaintext API keys in
    memory. Without this, the keys survive the GDPR delete in the
    ``Config`` dataclass and continue to be used by ``cloud_engines``,
    ``llm_polish`` and ``dictation_pipeline`` until the process
    restarts.

    Iterates :data:`PROVIDER_TO_CONFIG_FIELD` and ``setattr``s each
    field to ``""``. Returns the number of fields that were cleared
    (always ``len(PROVIDER_TO_CONFIG_FIELD)`` on success — the count
    is returned so callers can log a meaningful "cleared N secrets"
    line and so a future regression that drops a provider from the
    map is visible in tests).

    Never raises — wraps each ``setattr`` in try/except so a single
    broken field (e.g. a frozen dataclass, an exotic ``__setattr__``
    override) doesn't abort the rest. Failures are logged at debug
    level (best-effort cleanup).
    """
    cleared = 0
    for provider, field in PROVIDER_TO_CONFIG_FIELD.items():
        try:
            setattr(config, field, "")
            cleared += 1
        except Exception as e:
            log.debug(
                "[CREDENTIAL_STORE] clear_in_memory_secrets: setattr(%s, '') failed for provider=%s: %s",
                field,
                provider,
                _redact_sensitive(str(e)),
            )
    # invalidate the parsed-config cache so the
    # stale dict (which may still contain plaintext API keys) is not
    # retained in process memory after the GDPR delete.
    _cs._clear_plaintext_config_cache()
    # ``Config._last_saved_bytes`` is the serialized JSON byte cache
    # populated by ``Config.save()``. It includes the
    # plaintext API key fields whenever keyring is unavailable (the
    # keyring replacement of value -> 'keyring://<provider>' only
    # happens when ``is_keyring_available()`` is True). The setattr
    # loop above does NOT touch this cache, so the plaintext bytes
    # would survive the GDPR delete until the next successful save()
    # (which may be never if the user does not change settings again).
    # ``object.__setattr__`` is used because the private-name write
    # must bypass dataclass ``__setattr__`` overrides.
    try:
        object.__setattr__(config, "_last_saved_bytes", None)
    except Exception as e:
        log.debug(
            "[CREDENTIAL_STORE] clear_in_memory_secrets: failed to clear _last_saved_bytes: %s",
            _redact_sensitive(str(e)),
        )
    return cleared


__all__ = [
    "clear_in_memory_secrets",
    "delete_secret",
    "load_secret",
    "store_secret",
]
