"""Plaintext fallback read/write (config.json).

Owns the "plaintext fallback read/write" concern. When no usable
keyring backend is available, secrets are stored as plaintext in
``config.json`` (with ``0o600`` perms on POSIX, enforced by
:func:`voice_typer.server.config._secure_atomic_write`).

The :data:`_plaintext_config_cache` dict lives in
:mod:`voice_typer.server.credential_store._backend` (it's part of the
"global caches" concern); the read/write helpers here look it up via
``_cs._plaintext_config_cache`` so a test that does
``monkeypatch.setattr(credential_store, "_plaintext_config_cache", {})``
sees the *patched* dict populated/cleared by these helpers — preserving
the cache-coherence invariants that the GDPR-delete regression suite
asserts.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ._redact import _redact_sensitive
from ._schema import KEYRING_REF_PREFIX, PROVIDER_TO_CONFIG_FIELD, log

#: Look up the package module so :data:`_plaintext_config_cache` (which
#: tests monkey-patch on ``voice_typer.server.credential_store``) is
#: the same dict object these helpers read/write.
_cs = sys.modules["voice_typer.server.credential_store"]


def _read_plaintext_fallback(provider: str) -> str | None:
    """Read a secret from config.json's flat ``<provider>_api_key`` field.

    Returns None if config.json doesn't exist, the field is missing, or
    the field contains a ``keyring://`` reference token (the real value
    lives in keychain — caller should have tried keyring first).
    """
    try:
        import os

        from voice_typer.server.config import _config_dir, _secure_read_text

        config_file = _config_dir() / "config.json"
        if not config_file.exists():
            return None
        config_file_str = str(config_file)
        # Check mtime cache before re-reading + re-parsing config.json.
        # Config.load() calls load_secret() for each of the 5 providers;
        # without this cache, each call re-opens and re-parses the file.
        try:
            mtime_ns = os.stat(config_file).st_mtime_ns
        except OSError:
            mtime_ns = 0
        # ``_plaintext_config_cache`` is monkey-patched by tests via
        # ``monkeypatch.setattr(credential_store, "_plaintext_config_cache", ...)``
        # — look it up on the package module at call time so the
        # patched dict is the one we read/write here.
        plaintext_config_cache = _cs._plaintext_config_cache
        cached = plaintext_config_cache.get(config_file_str)
        if cached is not None and cached[0] == mtime_ns:
            data = cached[1]
        else:
            data = json.loads(_secure_read_text(config_file))
            plaintext_config_cache[config_file_str] = (mtime_ns, data)
    except Exception as e:
        # A parse failure here means config.json is corrupt (or
        # unreadable) — the user has no way to notice this at DEBUG
        # level (which is off by default). WARNING surfaces it in the
        # default log level so the user can manually recover.
        log.warning(
            "[CREDENTIAL_STORE] plaintext fallback read failed for provider=%s: %s",
            provider,
            _redact_sensitive(str(e)),
        )
        return None

    field = PROVIDER_TO_CONFIG_FIELD.get(provider)
    if not field:
        return None
    # Guard against non-string ``api_key`` values that may appear in a
    # hand-edited or corrupted config.json. Mirror the migration path's
    # ``isinstance(value, str)`` guard.
    if not isinstance(data, dict):
        log.warning(
            "[CREDENTIAL_STORE] plaintext fallback: config.json root is not a dict (type=%s) — skipping provider=%s",
            type(data).__name__,
            provider,
        )
        return None
    value = data.get(field, "")
    if not isinstance(value, str):
        if value == "" or value is None:
            return None
        log.warning(
            "[CREDENTIAL_STORE] plaintext fallback: provider=%s field=%s has non-string value (type=%s) — skipping",
            provider,
            field,
            type(value).__name__,
        )
        return None
    if not value:
        return None
    if value.startswith(KEYRING_REF_PREFIX):
        # Reference token — real value is in keychain. Caller should
        # have tried keyring already; if it returned None, the secret
        # is genuinely missing.
        return None
    return value


def _write_plaintext_fallback(provider: str, value: str, *, caller_holds_config_lock: bool = False) -> bool:
    """Write a secret (or empty string) to config.json's flat api_key field.

    Reads config.json, updates the single field, and writes it back via
    ``_secure_atomic_write`` (which enforces ``0o600`` on POSIX).
    Preserves all other config fields. On any I/O error, logs and
    returns ``False`` — never raises.

    The read-modify-write is wrapped in ``_acquire_config_lock()``
    (the same cross-process lock used by ``Config.save()`` and
    ``migrate_secrets_to_keyring``). When ``caller_holds_config_lock``
    is ``True``, the lock re-acquisition is SKIPPED (``fcntl.flock`` is
    per-open-file-description, NOT per-fd — a second ``LOCK_EX`` on a
    fresh fd in the same process would deadlock).
    """
    try:
        from voice_typer.server.config import (
            _acquire_config_lock,
            _config_dir,
            _secure_atomic_write,
            _secure_read_text,
        )

        config_file = _config_dir() / "config.json"

        def _do_read_modify_write() -> bool:
            data: dict[str, Any] = {}
            if config_file.exists():
                try:
                    data = json.loads(_secure_read_text(config_file))
                    if not isinstance(data, dict):
                        # Mirror the migration path. Silently
                        # overwriting with {} would destroy the corrupt
                        # file's recoverable content. Skip the write so
                        # the user can manually recover, and return
                        # False so store_secret surfaces a "failed"
                        # outcome.
                        log.warning(
                            "[CREDENTIAL_STORE] config.json root is not a dict — "
                            "skipping write to preserve existing data for manual recovery"
                        )
                        return False
                except Exception as e:
                    log.error(
                        "[CREDENTIAL_STORE] config.json parse failed — refusing to overwrite; "
                        "preserving corrupt file for recovery: %s",
                        _redact_sensitive(str(e)),
                    )
                    return False
            field = PROVIDER_TO_CONFIG_FIELD.get(provider)
            if not field:
                # Unknown provider — no-op (not a failure).
                return True
            if value:
                data[field] = value
            elif field in data:
                # Clear the field rather than leaving a stale value.
                data[field] = ""
            else:
                # Field not present and we're clearing — nothing to do.
                return True
            _secure_atomic_write(config_file, json.dumps(data, indent=2))
            return True

        if caller_holds_config_lock:
            # Caller (Config._save_unlocked) already holds the
            # cross-process lock — re-acquiring would deadlock.
            ok = _do_read_modify_write()
        else:
            # Hold the cross-process lock for the full
            # read-modify-write so concurrent Config.save() / migration
            # can't clobber our change.
            with _acquire_config_lock():
                ok = _do_read_modify_write()
        if ok and value:
            log.info(
                "[CREDENTIAL_STORE] wrote plaintext fallback for provider=%s (len=%d) to config.json",
                provider,
                len(value),
            )
        return ok
    except Exception as e:
        log.error(
            "[CREDENTIAL_STORE] plaintext fallback write failed for provider=%s: %s",
            provider,
            _redact_sensitive(str(e)),
        )
        return False


__all__ = ["_read_plaintext_fallback", "_write_plaintext_fallback"]
