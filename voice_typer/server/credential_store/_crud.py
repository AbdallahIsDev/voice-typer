"""Credential-store CRUD helpers."""

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
_cs = sys.modules["voice_typer.server.credential_store"]


def store_secret(provider: str, value: str, *, _caller_holds_config_lock: bool = False) -> bool:
    """Store a secret for ``provider`` in the OS keychain."""
    # Reject unknown providers BEFORE any other logic. A typo'd or
    if provider not in PROVIDER_TO_CONFIG_FIELD:
        log.warning(
            "[CREDENTIAL_STORE] rejecting store_secret for unknown provider=%r "
            "(not in PROVIDER_TO_CONFIG_FIELD), prevents orphaned OS-keychain entries",
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
        delete_secret(provider)
        # record the delete outcome so the IPC ack can
        _cs._set_last_store_outcome("deleted", None, provider=provider)
        return True

    # defensive type guard for truthy non-string values. The
    if not isinstance(value, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            log.warning(
                "[CREDENTIAL_STORE] received non-string value for provider=%s (type=%s), coercing to str",
                provider,
                type(value).__name__,
            )
            value = str(value)
        else:
            log.warning(
                "[CREDENTIAL_STORE] received non-string value for provider=%s (type=%s), rejecting",
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
        import keyring  # noqa: PLC0415, optional dependency, imported lazily

        # wrap set_password in a finite timeout so a hung
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
        # NEVER log the value, only metadata. The provider name is
        redacted_reason = _redact_sensitive(str(e))
        log.warning(
            "[CREDENTIAL_STORE] keyring store failed for provider=%s (len=%d): %s | "
            "falling back to plaintext in config.json",
            provider,
            len(value),
            redacted_reason,
        )
        # _write_plaintext_fallback returns bool, check it
        ok = _cs._write_plaintext_fallback(provider, value, caller_holds_config_lock=_caller_holds_config_lock)
        if not ok:
            # The plaintext fallback write failed, the secret was NOT
            _cs._set_last_store_outcome(
                "failed",
                f"plaintext fallback write failed after keyring error: {redacted_reason}",
                provider=provider,
            )
            return False
        # record the fallback outcome (with the redacted reason)
        _cs._set_last_store_outcome("plaintext", redacted_reason, provider=provider)
        return False


def load_secret(provider: str) -> str | None:
    """Load a secret for ``provider``."""
    try:
        if _cs.is_keyring_available():
            import keyring  # noqa: PLC0415, optional dependency, imported lazily

            # wrap get_password in a finite timeout so a hung
            value: str | None = _cs._run_keyring_call(keyring.get_password, KEYRING_SERVICE_NAME, provider)
            if value:
                # emit an INFO audit log so operators can
                log.info(
                    "[CREDENTIAL_STORE] loaded secret for provider=%s (len=%d) from keyring",
                    provider,
                    len(value),
                )
                return value
            # keyring returned None, secret not in keychain. Fall
    except Exception as e:
        # _redact_sensitive strips paths / API-key-like substrings from
        log.warning(
            "[CREDENTIAL_STORE] keyring load failed for provider=%s: %s, trying plaintext fallback in config.json",
            provider,
            _redact_sensitive(str(e)),
        )

    # Explicit annotation, the ``_cs`` facade lookup is untyped (Any).
    plaintext: str | None = _cs._read_plaintext_fallback(provider)
    return plaintext


def delete_secret(provider: str, config: Any = None) -> None:
    """Delete a secret from both keyring and config.json."""
    # Try keyring first
    try:
        if _cs.is_keyring_available():
            import keyring  # noqa: PLC0415, optional dependency, imported lazily

            try:
                # wrap delete_password in a finite timeout.
                _cs._run_keyring_call(keyring.delete_password, KEYRING_SERVICE_NAME, provider)
                log.info(
                    "[CREDENTIAL_STORE] deleted secret for provider=%s from keyring",
                    provider,
                )
            except Exception as e:
                # PasswordDeleteError is raised when the secret doesn't
                log.debug(
                    "[CREDENTIAL_STORE] keyring delete for provider=%s raised: %s",
                    provider,
                    _redact_sensitive(str(e)),
                )

            # Orphan cleanup: iterate _KNOWN_PROVIDERS_HISTORY and delete
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
        _cs._clear_plaintext_config_cache()
    except Exception as e:
        # A failure here means the plaintext
        log.warning(
            "[CREDENTIAL_STORE] credential for provider=%s may still be in config.json, manual cleanup required: %s",
            provider,
            _redact_sensitive(str(e)),
        )

    # also clear the in-memory Config attribute (when provided)
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
    """Zero every API-key attribute on the in-memory ``Config``."""
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
    _cs._clear_plaintext_config_cache()
    # ``Config._last_saved_bytes`` is the serialized JSON byte cache
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
