"""Cross-process lock + secret migration logic."""

from __future__ import annotations

import contextlib
import json
import sys
import time

from ._backend import _run_keyring_call
from ._redact import _redact_sensitive
from ._schema import (
    _LEGACY_KEYRING_SERVICE_NAMES,
    _SERVICE_NAME_MIGRATED_FLAG,
    KEYRING_REF_PREFIX,
    KEYRING_SERVICE_NAME,
    PROVIDER_TO_CONFIG_FIELD,
    log,
)

#: Look up the package module so monkey-patched symbols resolve on the
_cs = sys.modules["voice_typer.server.credential_store"]

#: Deadline for the migration cross-process lock. Mirrors
_MIGRATION_LOCK_TIMEOUT_SECONDS = 5.0

#: Once the migration lock wait passes this threshold, emit a single
_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS = 2.0


def _is_windows() -> bool:
    """Local platform check, delegates to ``platform_utils.is_windows``."""
    from voice_typer.server.platform_utils import is_windows

    return is_windows()


def _acquire_migration_lock(lock_file):
    """Acquire an exclusive cross-process lock."""
    import os

    migration_lock_timeout_seconds = _cs._MIGRATION_LOCK_TIMEOUT_SECONDS
    migration_lock_slow_wait_warn_seconds = _cs._MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS
    is_windows = _cs._is_windows

    # Open with O_CREAT so the lock file exists on first run. Use
    if not is_windows():
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    else:
        # Windows: msvcrt.locking needs a file handle from os.open()
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)

    lock_fd = os.fdopen(fd, "r+b")
    try:
        if not is_windows():
            import errno
            import fcntl

            deadline = time.monotonic() + migration_lock_timeout_seconds
            wait_start = time.monotonic()
            warned_slow = False
            while True:
                try:
                    # LOCK_NB makes the call non-blocking so we can
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        # Any other flock failure: re-raise so the
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"migration lock not acquired within "
                            f"{migration_lock_timeout_seconds}s, another "
                            f"process is holding {_redact_sensitive(str(lock_file))}"
                        ) from e
                    if not warned_slow and time.monotonic() - wait_start > migration_lock_slow_wait_warn_seconds:
                        # redact ``lock_file``: the path contains the
                        log.warning(
                            "[CREDENTIAL_STORE] migration lock wait on %s "
                            "exceeds %.1fs, another process may be wedging "
                            "config.json.lock (will time out in %.1fs)",
                            _redact_sensitive(str(lock_file)),
                            migration_lock_slow_wait_warn_seconds,
                            max(0.0, deadline - time.monotonic()),
                        )
                        warned_slow = True
                    time.sleep(0.05)
        else:
            import msvcrt

            deadline = time.monotonic() + migration_lock_timeout_seconds
            wait_start = time.monotonic()
            warned_slow = False
            warned_final = False
            while True:
                try:
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    timed_out = time.monotonic() >= deadline
                    if timed_out:
                        # Fail-OPEN stance on the Windows branch: log a
                        if not warned_final:
                            log.warning(
                                "[CREDENTIAL_STORE] Windows migration "
                                "lock acquire timed out after %ss on "
                                "%s, race possible if another process "
                                "is also migrating secrets to keyring "
                                "(last error: %s). Proceeding fail-open "
                                "to avoid blocking startup; check for "
                                "concurrent secret-migration attempts.",
                                migration_lock_timeout_seconds,
                                _redact_sensitive(str(lock_file)),
                                _redact_sensitive(str(e)),
                            )
                            warned_final = True
                        break
                    if not warned_slow and time.monotonic() - wait_start > migration_lock_slow_wait_warn_seconds:
                        log.warning(
                            "[CREDENTIAL_STORE] migration lock wait on %s "
                            "exceeds %.1fs, another process may be wedging "
                            "config.json.lock (will time out in %.1fs)",
                            _redact_sensitive(str(lock_file)),
                            migration_lock_slow_wait_warn_seconds,
                            max(0.0, deadline - time.monotonic()),
                        )
                        warned_slow = True
                    time.sleep(0.05)
    except Exception:
        # Any unexpected failure (NOT the documented Windows lock
        lock_fd.close()
        raise
    return lock_fd


def migrate_secrets_to_keyring() -> int:
    """One-time migration of plaintext API keys to the OS keychain.

    Returns
    """
    try:
        from voice_typer.server.config import (
            _config_dir,
        )
    except Exception as e:
        log.error(
            "[CREDENTIAL_STORE] migration: cannot import config helpers: %s",
            _redact_sensitive(str(e)),
        )
        return 0

    config_file = _config_dir() / "config.json"
    lock_file = _config_dir() / "config.json.lock"

    # Acquire the cross-process lock BEFORE we inspect config.json.
    with contextlib.suppress(OSError):
        _config_dir().mkdir(parents=True, exist_ok=True)

    try:
        lock_fd = _acquire_migration_lock(lock_file)
    except Exception as e:
        # ABORT migration when the lock can't be acquired (e.g. POSIX
        log.warning(
            "[CREDENTIAL_STORE] migration: could not acquire lock on %s "
            "(%s), ABORTING migration to avoid racing with the lock holder. "
            "The next launch will retry; if the lock is permanently wedged, "
            "manually delete the lock file.",
            _redact_sensitive(str(lock_file)),
            _redact_sensitive(str(e)),
        )
        try:
            from voice_typer.server.config import (
                _secure_atomic_write,
                _secure_read_text,
            )

            # Record the DEFERRAL, never success. Setting
            if config_file.exists():
                existing = json.loads(_secure_read_text(config_file))
                if isinstance(existing, dict) and not existing.get("secrets_migrated", False):
                    existing["secrets_migrated_keyring_was_unavailable"] = True
                    _secure_atomic_write(config_file, json.dumps(existing, indent=2))
            else:
                _secure_atomic_write(
                    config_file,
                    json.dumps({"secrets_migrated_keyring_was_unavailable": True}, indent=2),
                )
        except Exception as write_err:
            log.debug(
                "[CREDENTIAL_STORE] migration: could not record "
                "lock-abort deferral diagnostic after lock-acquire failure: %s",
                _redact_sensitive(str(write_err)),
            )
        return 0

    try:
        return _migrate_secrets_to_keyring_locked(config_file)
    finally:
        if lock_fd is not None:
            with contextlib.suppress(OSError):
                lock_fd.close()


def _migrate_secrets_to_keyring_locked(config_file) -> int:
    """Body of :func:`migrate_secrets_to_keyring`: assumes the lock is held."""
    from voice_typer.server.config import (
        _secure_atomic_write,
        _secure_read_text,
    )

    # Re-check whether config.json exists NOW that we hold the lock.
    if not config_file.exists():
        # No config to migrate, mark as migrated so we don't keep
        try:
            _secure_atomic_write(
                config_file,
                json.dumps({"secrets_migrated": True}, indent=2),
            )
        except Exception as e:
            log.debug(
                "[CREDENTIAL_STORE] migration: cannot create empty config: %s",
                _redact_sensitive(str(e)),
            )
        return 0

    try:
        data = json.loads(_secure_read_text(config_file))
        if not isinstance(data, dict):
            log.warning("[CREDENTIAL_STORE] migration: config.json root is not a dict, skipping")
            return 0
    except Exception as e:
        log.warning(
            "[CREDENTIAL_STORE] migration: cannot parse config.json: %s",
            _redact_sensitive(str(e)),
        )
        return 0

    # ``secrets_migrated`` early-return so it's not blocked by a prior
    service_name_migrated_this_run = False
    if not data.get(_SERVICE_NAME_MIGRATED_FLAG, False):
        # ``is_keyring_available`` is monkey-patched by tests, look it
        if _cs.is_keyring_available():
            _migrate_legacy_service_names_locked()
            data[_SERVICE_NAME_MIGRATED_FLAG] = True
            service_name_migrated_this_run = True
        else:
            log.info("[CREDENTIAL_STORE] migration: deferring legacy service-name cutover, keyring unavailable")

    # Re-check the secrets_migrated flag NOW that we hold the lock.
    if data.get("secrets_migrated", False):
        log.debug("[CREDENTIAL_STORE] migration: secrets_migrated flag already set, skipping")
        if service_name_migrated_this_run:
            try:
                _secure_atomic_write(config_file, json.dumps(data, indent=2))
            except Exception as e:
                log.error(
                    "[CREDENTIAL_STORE] migration: failed to persist %s flag: %s",
                    _SERVICE_NAME_MIGRATED_FLAG,
                    _redact_sensitive(str(e)),
                )
        return 0

    migrated = 0
    keyring_ok = _cs.is_keyring_available()
    # Track whether we skipped any REAL plaintext secret because
    skipped_plaintext = False

    for provider, field_name in PROVIDER_TO_CONFIG_FIELD.items():
        value = data.get(field_name, "")
        # Guard against non-string ``api_key`` values that may appear
        if not isinstance(value, str):
            if value == "" or value is None:
                continue
            log.warning(
                "[CREDENTIAL_STORE] migration: provider=%s field=%s has non-string value (type=%s), skipping",
                provider,
                field_name,
                type(value).__name__,
            )
            continue
        if not value or value.startswith(KEYRING_REF_PREFIX):
            # Empty or already a reference, nothing to migrate.
            continue

        if not keyring_ok:
            # Keyring unavailable, leave the plaintext value in place.
            log.info(
                "[CREDENTIAL_STORE] migration: keyring unavailable, keeping provider=%s in plaintext (len=%d)",
                provider,
                len(value),
            )
            skipped_plaintext = True
            continue

        try:
            import keyring  # type: ignore[import-not-found]

            # Wrap set_password in a finite timeout. On timeout we
            _run_keyring_call(keyring.set_password, KEYRING_SERVICE_NAME, provider, value)
            log.info(
                "[CREDENTIAL_STORE] migration: moved provider=%s (len=%d) from config.json to keyring",
                provider,
                len(value),
            )
            # Replace the plaintext with a reference token.
            data[field_name] = f"{KEYRING_REF_PREFIX}{provider}"
            migrated += 1
        except Exception as e:
            # Mid-migration failure: the plaintext for this provider
            log.warning(
                "[CREDENTIAL_STORE] migration: failed to move provider=%s to keyring: %s, keeping plaintext",
                provider,
                _redact_sensitive(str(e)),
            )
            skipped_plaintext = True
            continue

    # Gate ``secrets_migrated`` on whether we actually had to skip any
    if skipped_plaintext:
        # Defer migration, record diagnostic so the operator knows.
        data["secrets_migrated_keyring_was_unavailable"] = True
    else:
        # Either keyring was available and migration succeeded, or
        data["secrets_migrated"] = True
        # Clear any stale diagnostic flag from a prior unavailable-keyring run.
        data.pop("secrets_migrated_keyring_was_unavailable", None)
    try:
        _secure_atomic_write(config_file, json.dumps(data, indent=2))
    except Exception as e:
        log.error(
            "[CREDENTIAL_STORE] migration: failed to save migrated config: %s",
            _redact_sensitive(str(e)),
        )
        # Don't return 0, the secrets were stored in keyring

    return migrated


def _migrate_legacy_service_names_locked() -> int:
    """Copy keyring entries from legacy service names to the current"""
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception as e:
        log.debug(
            "[CREDENTIAL_STORE] legacy service-name cutover: keyring import failed: %s",
            _redact_sensitive(str(e)),
        )
        return 0

    copied = 0
    for legacy_name in _LEGACY_KEYRING_SERVICE_NAMES:
        for provider in PROVIDER_TO_CONFIG_FIELD:
            try:
                value = _run_keyring_call(keyring.get_password, legacy_name, provider)
            except Exception as e:
                log.debug(
                    "[CREDENTIAL_STORE] legacy cutover: get_password(service=%s, provider=%s) raised: %s, skipping",
                    legacy_name,
                    provider,
                    _redact_sensitive(str(e)),
                )
                continue
            if not value:
                continue
            try:
                _run_keyring_call(keyring.set_password, KEYRING_SERVICE_NAME, provider, value)
                log.info(
                    "[CREDENTIAL_STORE] legacy cutover: copied provider=%s "
                    "from legacy service=%s to current service=%s (len=%d)",
                    provider,
                    legacy_name,
                    KEYRING_SERVICE_NAME,
                    len(value),
                )
                copied += 1
            except Exception as e:
                log.warning(
                    "[CREDENTIAL_STORE] legacy cutover: set_password(service=%s, "
                    "provider=%s) raised, keeping legacy entry under %s: %s",
                    KEYRING_SERVICE_NAME,
                    provider,
                    legacy_name,
                    _redact_sensitive(str(e)),
                )
                continue
            try:
                _run_keyring_call(keyring.delete_password, legacy_name, provider)
            except Exception as e:
                log.debug(
                    "[CREDENTIAL_STORE] legacy cutover: delete_password("
                    "service=%s, provider=%s) raised: %s, stale legacy entry "
                    "left in place",
                    legacy_name,
                    provider,
                    _redact_sensitive(str(e)),
                )
    return copied


__all__ = [
    "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS",
    "_MIGRATION_LOCK_TIMEOUT_SECONDS",
    "_acquire_migration_lock",
    "_is_windows",
    "_migrate_legacy_service_names_locked",
    "_migrate_secrets_to_keyring_locked",
    "migrate_secrets_to_keyring",
]
