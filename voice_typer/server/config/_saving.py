"""Config save-path implementations (atomic write + ACL + warmup)."""

import json
import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover, typing-only, never imported at runtime
    from pathlib import Path

    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")

# Paths whose owner-only ACL enforcement FAILED this process. Read by
acl_enforcement_failures: set[str] = set()


def _enforce_windows_owner_only_acl(path: "Path | str") -> bool:
    """Uses ``icacls`` to remove inherited ACEs (``/inheritance:r``) and
    grant the current user full control (``/grant:r``). This is a
    """
    import voice_typer.server.config as _cfg

    if not _cfg.is_windows():
        return True
    # Fast path: already-tightened dir inherits owner-only DACL (skip icacls).
    parent_dir = str(_cfg.Path(path).parent)
    if parent_dir in _cfg._windows_owner_only_acl_verified:
        return True
    import subprocess

    username = os.environ.get("USERNAME") or os.environ.get("USER")
    if not username:
        log.warning(
            "[CONFIG] cannot enforce Windows ACL on %s: USERNAME env var is empty "
            "(config files may be readable by other local users)",
            path,
        )
        acl_enforcement_failures.add(str(path))
        return False
    try:
        # icacls list-form (no shell): /inheritance:r + grant user:F only.
        cmd = [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{username}:F",
        ]
        # CREATE_NO_WINDOW: icacls is console-subsystem; avoid conhost flash.
        from voice_typer.server.server_platform.autostart import (
            _windows_create_no_window_flags,
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=_windows_create_no_window_flags(),
        )
        if result.returncode != 0:
            log.warning(
                "[CONFIG] icacls ACL enforcement failed on %s (rc=%d): %s "
                "(plaintext secrets in this file may remain readable by "
                "other local users on a multi-user machine)",
                path,
                result.returncode,
                (result.stderr or "").strip()[:200],
            )
            acl_enforcement_failures.add(str(path))
            return False
        acl_enforcement_failures.discard(str(path))
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(
            "[CONFIG] icacls ACL enforcement error on %s: %s "
            "(plaintext secrets in this file may remain readable by "
            "other local users on a multi-user machine)",
            path,
            e,
        )
        acl_enforcement_failures.add(str(path))
        return False


def _save_impl(cfg: "Config") -> bool:
    """Save config to disk atomically via temp file + os.replace.
    Returns True on success, False on failure. Errors are logged but
    """
    import voice_typer.server.config as _cfg

    # NOTE: see docs/code-notes/security-config.md#config-save-acl
    if _cfg.is_windows() and (cfg._dirty or cfg._last_saved_bytes is None):
        try:
            config_dir = _cfg._config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            # Tighten the config DIR's ACL on the FIRST save of this
            if str(config_dir) not in _cfg._windows_owner_only_acl_verified and _cfg._enforce_windows_owner_only_acl(
                config_dir
            ):
                _cfg._windows_owner_only_acl_verified.add(str(config_dir))
        except Exception:
            # Best-effort hardening, never block the save (see the
            pass
    try:
        with _cfg._acquire_config_lock():
            return cfg._save_with_mutation_lock()
    except TimeoutError as e:
        log.warning("[CONFIG] %s", e)
        return False
    except (OSError, PermissionError) as e:
        log.exception("[CONFIG] Failed to save config: %s", e)
        return False
    except (TypeError, ValueError) as e:
        # ``json.dumps`` (called inside
        log.exception("[CONFIG] Failed to serialize config for save: %s", e)
        return False


def _save_with_mutation_lock_impl(cfg: "Config") -> bool:
    """Acquire the mutation lock (if set) and delegate to
    :func:`_save_unlocked_impl`.
    """
    lock = cfg._mutation_lock
    if lock is None:
        return cfg._save_unlocked()
    with lock:
        return cfg._save_unlocked()


def _save_unlocked_impl(cfg: "Config") -> bool:
    """Best-effort single-slot backup of the existing config.json BEFORE
    we overwrite it. The backup preserves the EXACT bytes that were on
    """
    import voice_typer.server.config as _cfg

    # Dirty-flag short-circuit. If no persisted field has
    if not cfg._dirty and cfg._last_saved_bytes is not None:
        return True
    path = _cfg._config_dir()
    path.mkdir(parents=True, exist_ok=True)
    if not _cfg.is_windows():
        try:
            os.chmod(path, 0o700)
        except OSError as e:
            log.warning("[CONFIG] Failed to chmod config dir: %s", e)
    # The config DIR's ACL is tightened in ``save()`` BEFORE the
    config_file = path / "config.json"
    data = asdict(cfg)
    # Reset the ``_secrets_routed_in_save`` flag at the
    object.__setattr__(cfg, "_secrets_routed_in_save", False)
    # route API key fields through credential_store.
    try:
        from voice_typer.server import credential_store

        if credential_store.is_keyring_available():
            for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
                value = data.get(field_name, "")
                # defensive type guard for non-string api_key
                if not isinstance(value, str):
                    if not value:
                        # Falsy (None, 0, [], {}, ""), nothing
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        log.warning(
                            "[CONFIG] DE-23: %s field has non-string value (type=%s), coercing to str",
                            field_name,
                            type(value).__name__,
                        )
                        value = str(value)
                        data[field_name] = value
                    else:
                        log.warning(
                            "[CONFIG] DE-23: %s field has non-string value (type=%s)"
                            ": skipping credential_store routing",
                            field_name,
                            type(value).__name__,
                        )
                        continue
                if value and not value.startswith(credential_store.KEYRING_REF_PREFIX):
                    # pass ``_caller_holds_config_lock=True``
                    stored_to_keyring = credential_store.store_secret(provider, value, _caller_holds_config_lock=True)
                    if stored_to_keyring:
                        data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"
                    # else: leave data[field_name] as the plaintext value —
        object.__setattr__(cfg, "_secrets_routed_in_save", True)
    except Exception as e:
        # log only the exception TYPE (not the message) —
        log.warning(
            "[CONFIG] credential_store routing failed: %s, writing config with current api_key values",
            type(e).__name__,
        )
        # Leave ``_secrets_routed_in_save`` at False (set
    content = json.dumps(data, indent=2)
    content_bytes = content.encode("utf-8")

    # Skip the write ENTIRELY when the new content matches the
    if cfg._last_saved_bytes is not None and cfg._last_saved_bytes == content_bytes:
        # Clear the dirty flag here too, the content
        object.__setattr__(cfg, "_dirty", False)
        return True

    # Short-circuit the entire backup block when the new
    if cfg._last_saved_bytes != content_bytes and config_file.exists():
        # best-effort backup before overwrite.
        try:
            if cfg._last_saved_bytes is not None:
                # Use the cached bytes from the last
                existing_bytes = cfg._last_saved_bytes
                existing_text = existing_bytes.decode("utf-8")
            else:
                # Fallback: first save (cache is None) —
                existing_text = _cfg._secure_read_text(config_file)
                existing_bytes = existing_text.encode("utf-8")
            if existing_bytes != content_bytes:
                bak_path = path / "config.json.bak"
                # also route the .bak WRITE through
                _cfg._secure_atomic_write(bak_path, existing_text)
                if not _cfg.is_windows():
                    try:
                        os.chmod(bak_path, 0o600)
                    except OSError as e:
                        log.debug("[CONFIG] Failed to chmod config.json.bak: %s", e)
                else:
                    # enforce owner-only ACL on the
                    _cfg._enforce_windows_owner_only_acl(bak_path)
        except (OSError, ValueError) as e:
            # the SEC-002 inode-changed-during-read guard (symlink
            log.debug(
                "[CONFIG] Failed to back up existing config.json to config.json.bak: %s",
                e,
            )

    _cfg._secure_atomic_write(config_file, content)
    if _cfg.is_windows():
        # ``_secure_atomic_write`` creates the temp
        _cfg._enforce_windows_owner_only_acl(config_file)
    # record the bytes we just persisted so the next
    object.__setattr__(cfg, "_last_saved_bytes", content_bytes)
    # Clear the dirty flag, the in-memory state now
    object.__setattr__(cfg, "_dirty", False)
    return True


def _save_strict_impl(cfg: "Config") -> None:
    """Wraps ``save()`` and raises :class:`RuntimeError` if the
    underlying save returned ``False`` (which indicates an
    """
    ok = cfg.save()
    if not ok:
        raise RuntimeError("failed to persist config to disk")


def _warmup_keyring_probe_impl() -> None:
    """Eagerly probe ``credential_store.is_keyring_available()``
    once at app startup so the FIRST ``save`` call does not pay
    """
    import voice_typer.server.config as _cfg

    if _cfg._warmup_called:
        # Idempotent: a prior call already populated the
        return
    from voice_typer.server import credential_store

    # Touch the probe, the result is cached inside
    credential_store.is_keyring_available()
    _cfg._warmup_called = True
