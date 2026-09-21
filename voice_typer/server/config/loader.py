"""Config-load orchestrator + JSON-read / key-filter helpers.

This module holds the ``Config.load()`` body (extracted verbatim from
``config/__init__.py`` to chip away at the monolith)
plus the two small helpers it delegates to (``_read_raw_json`` and
``_filter_unknown_keys``).

The split is purely cosmetic, behavior is byte-for-byte identical to
the pre-extraction implementation. The ``Config.load`` classmethod in
``config/__init__.py`` is now a one-line delegator:

.. code-block:: python

    @classmethod
    def load(cls) -> "Config":
        return _load_config(cls)

 Likewise ``Config._read_raw_json`` and ``Config._filter_unknown_keys``
 delegate to ``_read_raw_json_impl`` / ``_filter_unknown_keys_impl``
 here.

Import-safety note
------------------
This module is imported at the TOP of ``config/__init__.py`` (alongside
``config.coercion`` / ``config.sanitization``). To avoid a circular
import, this module's TOP-OF-FILE imports only touch leaf modules
(``config_validators``, ``config_internals.{paths,migrations}``,
``secure_file_io``). The handful of names that live in
``config/__init__.py`` itself (currently just
``_default_hotkey_for_platform``) are imported LAZILY inside
``_load_config``: by call-time, ``config/__init__.py`` is fully
initialized, so the late import succeeds.
"""

import itertools
import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from voice_typer.server.config_internals.migrations import (
    _CURRENT_SCHEMA_VERSION,
    _backup_before_migration_impl,
    _run_migrations,
)
from voice_typer.server.config_validators import (
    _make_custom_theme_validator,
    _validate_hotkey,
    cross_platform_hotkey_warnings,
)

if TYPE_CHECKING:  # pragma: no cover, typing-only, never imported at runtime
    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")

# Monotonic counter mixed into the ``config.json.corrupt-<ts>-<pid>-<ns>``
_CONFIG_QUARANTINE_SUFFIX_SEQ: "itertools.count" = itertools.count()

# NOTE: ``_config_dir`` and ``_secure_read_text`` are NOT imported at the

#: Dedupe key for the unknown-key WARNING, keyed by ``(config_file, keys)``.
_unknown_key_warnings: set[tuple[str, frozenset[str]]] = set()
_unknown_key_warnings_lock = threading.Lock()

#: Dedupe set for the ``validate_config`` WARNING lines, keyed by the
_validate_config_warnings: set[str] = set()
_validate_config_warnings_lock = threading.Lock()

#: Legacy enum VALUES remapped to their live successors BEFORE validation
_LEGACY_ENUM_REMAPS: dict[str, dict[str, str]] = {
    "noise_suppression_method": {
        "deepfilternet": "gtcrn",
        "speex": "rnnoise",
    },
}


def _read_raw_json_impl(config_file) -> dict | None:
    """Read + parse ``config_file`` as JSON; return the parsed dict (or None).

    Extracted verbatim from ``Config._read_raw_json``. Uses
    :func:`_secure_read_text` (SEC-002 / SEC-audit-011) to prevent
    symlink-TOCTOU attacks when reading ``config.json``.

    Returns ``None`` if the parsed JSON is not a dict (a valid JSON
    scalar like ``null`` / ``true`` / ``42`` / ``"x"`` / ``[]`` is
    not a valid config). The caller raises ``TypeError`` so the
    outer ``except`` in ``_load_config`` catches it, logs a WARNING, and
    moves the corrupt file aside.
    """
    # Late lookup so tests that monkeypatch
    from voice_typer.server.config import _secure_read_text

    # SEC-002 / SEC-audit-011: use _secure_read_text to prevent
    raw_text = _secure_read_text(config_file)
    parsed = json.loads(raw_text)
    # a valid JSON scalar (null/true/42/"x"/[]) is
    if not isinstance(parsed, dict):
        return None
    return parsed


# Keys written into ``config.json`` by subsystems that are NOT Config
_KNOWN_EXTERNAL_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        # credential_store legacy-keyring-service-name cutover flag
        "service_name_migrated_com_voicetyper_keyring",
        # credential_store keyring-unavailable diagnostic flag
        "secrets_migrated_keyring_was_unavailable",
    }
)


def _filter_unknown_keys_impl(cls, parsed: dict, config_file) -> dict:
    """Filter unknown keys from ``parsed``; log a WARNING for each dropped key.

    Extracted verbatim from ``Config._filter_unknown_keys``.  : log a
    WARNING if the on-disk config contains keys this build doesn't
    recognize.  These keys are silently dropped by the filter.

    The warning is emitted at most once per ``(config_file, key-set)``
    per process (see :data:`_unknown_key_warnings`) because
    ``Config.load()`` runs multiple times during startup, without
    dedupe the same line appeared 4× within milliseconds.
    """
    # log a WARNING if the on-disk config contains
    unknown_keys = set(parsed) - set(cls.__dataclass_fields__) - _KNOWN_EXTERNAL_CONFIG_KEYS
    if unknown_keys:
        dedupe_key = (str(config_file), frozenset(unknown_keys))
        with _unknown_key_warnings_lock:
            first_time = dedupe_key not in _unknown_key_warnings
            if first_time:
                _unknown_key_warnings.add(dedupe_key)
        if first_time:
            # NOTE: key names are deliberately NOT included here, the PII
            _schema = parsed.get("schema_version", 0)
            _newer_config = isinstance(_schema, int) and _schema > _CURRENT_SCHEMA_VERSION
            if _newer_config:
                log.warning(
                    "[CONFIG] ignoring %d unrecognized settings in %s (config "
                    "schema %s is newer than this build supports); they will be "
                    "removed on the next save.",
                    len(unknown_keys),
                    config_file,
                    _schema,
                )
            else:
                log.warning(
                    "[CONFIG] ignoring %d unrecognized settings in %s (unknown "
                    "to this build); removing them from the file now.",
                    len(unknown_keys),
                    config_file,
                )
                _remove_unknown_keys_from_disk(config_file, unknown_keys)
    return {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}


def _remove_unknown_keys_from_disk(config_file, unknown_keys) -> None:
    """Best-effort removal of unknown keys from the on-disk config.

    Runs at most once per (file, key-set) per process (the caller
    dedupes) and ONLY when the file's ``schema_version`` is not newer
    than this build, for genuinely newer-version configs the keys are
    preserved for the build that knows them. Re-reads the file fresh so
    a concurrent ``Config.save()`` (another process, or the IPC server)
    is not clobbered, then rewrites atomically with the same
    ``json.dumps(..., indent=2)`` format ``Config.save()`` uses.
    Best-effort: any failure only costs the cleanup, never the load.
    """
    try:
        from voice_typer.server.config import _secure_atomic_write, _secure_read_text

        raw = _secure_read_text(config_file)
        fresh = json.loads(raw)
        if not isinstance(fresh, dict):
            return
        pruned = {k: v for k, v in fresh.items() if k not in unknown_keys}
        _secure_atomic_write(config_file, json.dumps(pruned, indent=2), durability=False)
        log.debug(
            "[CONFIG] removed unrecognized settings from %s: %s",
            config_file,
            ", ".join(sorted(unknown_keys)),
        )
    except Exception:
        # The load must never fail because the cleanup did. Log at DEBUG
        log.debug("[CONFIG] could not remove unrecognized settings from %s", config_file, exc_info=True)


def _load_config(cls) -> "Config":
    """Load config from disk, or return defaults.

    Extracted verbatim from ``Config.load``. See ``Config.load``'s
    docstring for the full failure-mode enumeration + rationale.

    Parameters
    ----------
    cls
        The :class:`Config` class (passed explicitly so this function
        can call ``cls()`` to construct a default instance and
        ``cls.<classmethod>(...)`` for the helper delegators).
    """
    # Late import to avoid a circular dependency at module-load time:
    from voice_typer.server.config import (
        _config_dir,
        _default_hotkey_for_platform,
        _secure_read_text,
    )

    config_file = _config_dir() / "config.json"
    if not config_file.exists():
        return cls()
    try:
        parsed = _read_raw_json_impl(config_file)
        if parsed is None:
            # _read_raw_json_impl already logged the TypeError; raise
            raise TypeError(f"config root must be a JSON object, got {type(parsed).__name__}")
        data = _filter_unknown_keys_impl(cls, parsed, config_file)

        # M3: Schema versioning and migration
        loaded_version = data.get("schema_version", 0)
        # track whether any migration ran.
        migrations_ran = False
        # If the on-disk schema_version is
        if isinstance(loaded_version, int) and loaded_version > _CURRENT_SCHEMA_VERSION:
            log.warning(
                "[CONFIG] config schema_version=%d is newer than supported=%d, "
                "some fields may be dropped (preserving on-disk version)",
                loaded_version,
                _CURRENT_SCHEMA_VERSION,
            )
            final_schema_version = loaded_version
            # versioned backup BEFORE the in-memory data
            cls._backup_before_downgrade(config_file, loaded_version, data)
        else:
            data, final_schema_version, migrations_ran = _run_migrations(data, loaded_version, config_file)
        data["schema_version"] = final_schema_version

        _backup_before_migration_impl(config_file, loaded_version)

        cls._coerce_streaming_fields(data)
        cls._coerce_max_recording_time(data)
        cls._validate_model_path(data)
        cls._validate_qwen_model_path(data)
        cls._validate_corrections_path(data)
        cls._validate_privacy_consents(data)
        # Pack auto-update always-on: heal legacy False, ignore on-disk off.
        data["offline_pack_consent"] = True

        # Remap legacy enum VALUES to their live successors BEFORE
        for _field, _remap in _LEGACY_ENUM_REMAPS.items():
            _legacy_value = data.get(_field)
            if isinstance(_legacy_value, str) and _legacy_value in _remap:
                _live_value = _remap[_legacy_value]
                log.info(
                    "[CONFIG] %s=%r is a legacy value, remapping to %r",
                    _field,
                    _legacy_value,
                    _live_value,
                )
                data.setdefault("_load_warnings", []).append(
                    f"Config field {_field!r} migrated from legacy value {_legacy_value!r} to {_live_value!r}"
                )
                data[_field] = _live_value

        # credential_store integration.
        try:
            from voice_typer.server import credential_store

            if not data.get("secrets_migrated", False):
                migrated_count = credential_store.migrate_secrets_to_keyring()
                if migrated_count > 0:
                    log.info(
                        "[CONFIG] migrated %d plaintext API keys to OS keychain",
                        migrated_count,
                    )
                # re-read the on-disk ``secrets_migrated`` flag
                try:
                    on_disk_text = _secure_read_text(config_file)
                    on_disk_data = json.loads(on_disk_text)
                    if isinstance(on_disk_data, dict):
                        data["secrets_migrated"] = bool(on_disk_data.get("secrets_migrated", False))
                    else:
                        # Corrupt or non-dict on-disk JSON —
                        data["secrets_migrated"] = True
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as re_err:
                    # Best-effort: if re-reading fails (concurrent
                    log.debug(
                        "[CONFIG] could not re-read on-disk "
                        "secrets_migrated flag after migrate (%s), "
                        "defaulting in-memory flag to True",
                        type(re_err).__name__,
                    )
                    data["secrets_migrated"] = True
            else:
                # Already migrated in a prior session, preserve
                data["secrets_migrated"] = True

            # Resolve keyring:// references to real values.
            for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
                value = data.get(field_name, "")
                if isinstance(value, str) and value.startswith(credential_store.KEYRING_REF_PREFIX):
                    real_value = credential_store.load_secret(provider)
                    if real_value:
                        data[field_name] = real_value
                    else:
                        # Reference points to keyring but keyring
                        log.warning(
                            "[CONFIG] %s field has keyring:// reference "
                            "but keyring returned no value, clearing (secret lost)",
                            field_name,
                        )
                        data[field_name] = ""
        except Exception as e:
            # Don't let credential_store issues break config
            log.warning(
                "[CONFIG] credential_store integration failed: %s, continuing with config.json values as-is",
                type(e).__name__,
            )

        # H1: Validate non-numeric fields before construction
        data = cls._validate_non_numeric_fields(data)

        # validate hotkeys against the reserved-shortcut
        default_hotkey = _default_hotkey_for_platform()
        for hotkey_field in ("hotkey", "repaste_hotkey"):
            value = data.get(hotkey_field)
            if not isinstance(value, str) or value == "":
                continue
            err = _validate_hotkey(value)
            if err is not None:
                log.warning(
                    "[CONFIG] %s=%r rejected by hotkey validator (%s) -- resetting to platform default %r",
                    hotkey_field,
                    value,
                    err,
                    default_hotkey,
                )
                data.setdefault("_load_warnings", []).append(
                    f"Config field {hotkey_field!r}={value!r} rejected by "
                    f"hotkey validator ({err}) -- reset to {default_hotkey!r}"
                )
                data[hotkey_field] = default_hotkey

        # validate ``custom_theme`` on load (mirrors the IPC
        if "custom_theme" in data and data["custom_theme"] is not None:
            _theme_err = _make_custom_theme_validator()(data["custom_theme"])
            if _theme_err is not None:
                log.warning(
                    "[CONFIG] custom_theme validation failed on load (%s), resetting to None",
                    _theme_err,
                )
                data.setdefault("_load_warnings", []).append(
                    f"custom_theme validation failed on load ({_theme_err}), reset to None"
                )
                data["custom_theme"] = None

        # extract load warnings before construction
        load_warnings = data.pop("_load_warnings", [])

        instance = cls(**data)
        load_warnings.extend(cross_platform_hotkey_warnings(instance))
        instance.last_load_warnings = load_warnings

        # AUDIO-PRESET-LOAD-FIX: apply the audio preset's filter
        try:
            from voice_typer.server.audio_presets import apply_preset

            apply_preset(instance.audio_preset, instance)
        except Exception as preset_exc:
            log.warning(
                "[CONFIG] apply_preset on load failed: %s: %s",
                type(preset_exc).__name__,
                preset_exc,
                exc_info=True,
            )
            instance.last_load_warnings.append(
                f"apply_preset({instance.audio_preset!r}) on load failed: {type(preset_exc).__name__}: {preset_exc}"
            )

        # invoke the full-config validator
        try:
            from voice_typer.server.config_validators import validate_config

            full_config_errors = validate_config(instance)
            if full_config_errors:
                with _validate_config_warnings_lock:
                    unseen = [e for e in full_config_errors if e not in _validate_config_warnings]
                    _validate_config_warnings.update(full_config_errors)
                for _err in unseen:
                    log.warning("[CONFIG] validate_config: %s", _err)
                instance.last_load_warnings.extend(f"validate_config: {_err}" for _err in full_config_errors)
        except Exception:
            log.debug("[CONFIG] validate_config on load failed", exc_info=True)

        # ``validate_config`` above only APPENDS warnings —
        try:
            cls._reset_invalid_enum_fields(instance)
        except Exception:
            log.debug("[CONFIG] _reset_invalid_enum_fields on load failed", exc_info=True)

        # persist the bumped schema_version eagerly so
        if migrations_ran:
            try:
                _post_migration_save_ok = instance.save()
            except Exception as post_mig_exc:
                log.warning(
                    "[CONFIG] eager post-migration save raised %s: %s, migrations will re-run on next launch",
                    type(post_mig_exc).__name__,
                    post_mig_exc,
                    exc_info=True,
                )
                instance.last_load_warnings.append(
                    f"post-migration save raised {type(post_mig_exc).__name__}: "
                    f"{post_mig_exc}, migrations will re-run on next launch"
                )
            else:
                if not _post_migration_save_ok:
                    log.warning("[CONFIG] post-migration save failed, migrations will re-run on next launch")
                    instance.last_load_warnings.append(
                        "post-migration save failed, migrations will re-run on next launch"
                    )

        # Re-apply user-configured trusted hosts to the
        try:
            trusted_hosts = instance.trusted_extra_hosts
            if trusted_hosts:
                from voice_typer.server._secrets import extend_url_allowlist

                extend_url_allowlist(trusted_hosts, caller="config.load")
        except Exception as _allowlist_exc:
            log.warning(
                "[CONFIG] could not re-apply trusted_extra_hosts to URL allowlist: %s",
                type(_allowlist_exc).__name__,
            )

        return instance
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        # enumerated failure modes -- see the docstring.
        log.warning(
            "[CONFIG] %s loading config %s: %s. Using defaults.",
            type(e).__name__,
            config_file,
            e,
        )
        # best-effort move the corrupt config aside so
        try:
            # the previous ``int(time.time())`` suffix
            _ns = (time.time_ns() % 1_000_000 + next(_CONFIG_QUARANTINE_SUFFIX_SEQ)) % 1_000_000
            corrupt_backup = config_file.parent / f"config.json.corrupt-{int(time.time())}-{os.getpid()}-{_ns}"
            config_file.replace(corrupt_backup)
            log.warning(
                "[CONFIG] moved corrupt config %s -> %s for forensic recovery",
                config_file,
                corrupt_backup,
            )
        except OSError as move_exc:
            log.debug(
                "[CONFIG] could not move corrupt config %s aside: %s",
                config_file,
                move_exc,
            )
        return cls()
