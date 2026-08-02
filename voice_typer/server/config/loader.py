"""Config-load orchestrator + JSON-read / key-filter helpers.

This module holds the ``Config.load()`` body (extracted verbatim from
``config/__init__.py`` to chip away at the monolith)
plus the two small helpers it delegates to (``_read_raw_json`` and
``_filter_unknown_keys``).

The split is purely cosmetic — behavior is byte-for-byte identical to
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
``_load_config`` — by call-time, ``config/__init__.py`` is fully
initialized, so the late import succeeds.
"""

import json
import logging
import os
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

if TYPE_CHECKING:  # pragma: no cover — typing-only, never imported at runtime
    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")

# NOTE: ``_config_dir`` and ``_secure_read_text`` are NOT imported at the
# top of this module. They are re-exported by ``config/__init__.py`` and
# are routinely monkeypatched by tests via
# ``monkeypatch.setattr("voice_typer.server.config._config_dir", ...)``
# (see tests/test_config_load_corruption.py,
# tests/test_config_migration_schema_version.py, tests/test_config_pep604_union.py).
# If we bound them at top-of-file here, the monkeypatch on the
# ``config`` module's globals would NOT take effect inside this module's
# functions (each module has its own globals dict). Looking them up
# lazily via ``import voice_typer.server.config as _cfg`` inside each
# function ensures the patched binding is picked up at call time.


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
    # ``voice_typer.server.config._secure_read_text`` take effect.
    from voice_typer.server.config import _secure_read_text

    # SEC-002 / SEC-audit-011: use _secure_read_text to prevent
    # symlink-TOCTOU attacks when reading config.json
    raw_text = _secure_read_text(config_file)
    parsed = json.loads(raw_text)
    # a valid JSON scalar (null/true/42/"x"/[]) is
    # not a valid config — raise TypeError with a clear
    # message so the failure mode is visible in the WARNING
    # log below (and matches the caught tuple).  Without
    # this, ``parsed.items()`` on a non-dict would raise
    # AttributeError, which we deliberately let propagate.
    if not isinstance(parsed, dict):
        return None
    return parsed


def _filter_unknown_keys_impl(cls, parsed: dict, config_file) -> dict:
    """Filter unknown keys from ``parsed``; log a WARNING for each dropped key.

    Extracted verbatim from ``Config._filter_unknown_keys``.  : log a
    WARNING if the on-disk config contains keys this build doesn't
    recognize.  These keys are silently dropped by the filter.
    """
    # log a WARNING if the on-disk config contains
    # keys this build doesn't recognize.  These keys are
    # silently dropped by the filter below.
    unknown_keys = set(parsed) - set(cls.__dataclass_fields__)
    if unknown_keys:
        log.warning(
            "[CONFIG] dropped %d unknown key(s) from %s: %s",
            len(unknown_keys),
            config_file,
            ", ".join(sorted(unknown_keys)),
        )
    return {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}


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
    # ``config/__init__.py`` imports this module at its top, but
    # ``_default_hotkey_for_platform``, ``_config_dir``, and
    # ``_secure_read_text`` are defined further down in
    # ``config/__init__.py`` (or re-exported by it). By call-time the
    # parent module is fully initialized, so the late import succeeds.
    # The late lookup ALSO ensures tests that monkeypatch
    # ``voice_typer.server.config._config_dir`` /
    # ``voice_typer.server.config._secure_read_text`` take effect here.
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
            # it here so the outer except catches + moves the
            # corrupt file aside (matching the original behavior).
            raise TypeError(f"config root must be a JSON object, got {type(parsed).__name__}")
        data = _filter_unknown_keys_impl(cls, parsed, config_file)

        # M3: Schema versioning and migration
        loaded_version = data.get("schema_version", 0)
        # track whether any migration ran.
        migrations_ran = False
        # SCHEMA-2 (MED-J): if the on-disk schema_version is
        # NEWER than this build supports, log a warning so the
        # user knows some fields may be dropped (we filter
        # unknown keys via ``cls._filter_unknown_keys``).  Do NOT
        # downgrade the on-disk version — preserving the higher
        # value means a future build that supports it can read
        # the fields back, and the user gets an honest signal
        # that they ran an older build against a newer config
        # rather than silently losing the version metadata.
        if isinstance(loaded_version, int) and loaded_version > _CURRENT_SCHEMA_VERSION:
            log.warning(
                "[CONFIG] config schema_version=%d is newer than supported=%d — "
                "some fields may be dropped (preserving on-disk version)",
                loaded_version,
                _CURRENT_SCHEMA_VERSION,
            )
            final_schema_version = loaded_version
            # versioned backup BEFORE the in-memory data
            # (with higher-version fields filtered out) gets written
            # back to disk by the next Config.save(). See
            # ``_backup_before_downgrade`` for the full rationale.
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

        # credential_store integration.
        # 1. If secrets haven't been migrated yet, run the
        #    one-time migration (plaintext → keyring). This
        #    modifies config.json on disk but NOT our in-memory
        #    `data` dict — the in-memory dict still has the
        #    plaintext values (which is what we want, so the
        #    constructed Config instance has real values for
        #    cloud_engines / llm_polish to use).
        # 2. Set the in-memory flag so the constructed Config
        #    carries it forward (and the next save() persists it).
        # 3. Resolve any ``keyring://<provider>`` reference
        #    tokens to real values via credential_store.load_secret.
        #    This handles the case where migration was done in a
        #    prior session (config.json on disk has references,
        #    real values live in keychain).
        try:
            from voice_typer.server import credential_store

            if not data.get("secrets_migrated", False):
                migrated_count = credential_store.migrate_secrets_to_keyring()
                if migrated_count > 0:
                    log.info(
                        "[CONFIG] RW-01: migrated %d plaintext API key(s) to OS keychain",
                        migrated_count,
                    )
                # re-read the on-disk ``secrets_migrated`` flag
                # AFTER ``migrate_secrets_to_keyring`` returns so we
                # pick up the  deferral state. The migrate
                # function modifies ``config.json`` on disk but does
                # NOT touch the in-memory ``data`` dict — so the
                # in-memory dict is stale w.r.t. the on-disk flag.
                # If keyring was unavailable AND real plaintext was
                # skipped, the on-disk flag stays UNSET (only the
                # diagnostic ``secrets_migrated_keyring_was_unavailable``
                # is written). We MUST NOT clobber this — otherwise
                # the next ``Config.save()`` (which uses
                # ``asdict(self)``) persists ``secrets_migrated=True``
                # to disk, the next launch sees True and skips
                # migration entirely, and the plaintext API key
                # stays in config.json forever — defeating the
                # encryption-at-rest goal. Re-reading the on-disk
                # state is the authoritative way to know whether
                # migration actually completed (option (b) of the
                #  fix prescription).
                try:
                    on_disk_text = _secure_read_text(config_file)
                    on_disk_data = json.loads(on_disk_text)
                    if isinstance(on_disk_data, dict):
                        data["secrets_migrated"] = bool(on_disk_data.get("secrets_migrated", False))
                    else:
                        # Corrupt or non-dict on-disk JSON —
                        # conservatively set True so the next launch
                        # doesn't keep retrying migrate (the migrate
                        # function already handled the corrupt case
                        # and returned 0).
                        data["secrets_migrated"] = True
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as re_err:
                    # Best-effort: if re-reading fails (concurrent
                    # writer, race, etc.), fall back to True. The
                    # migrate function itself succeeded in writing
                    # whatever state it intended; the in-memory dict
                    # already has the plaintext values for
                    # cloud_engines / llm_polish to use.
                    log.debug(
                        "[CONFIG] RW-01: could not re-read on-disk "
                        "secrets_migrated flag after migrate (%s) — "
                        "defaulting in-memory flag to True",
                        type(re_err).__name__,
                    )
                    data["secrets_migrated"] = True
            else:
                # Already migrated in a prior session — preserve
                # the in-memory flag (which came from the on-disk
                # config.json we just read).
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
                        # has nothing — secret is lost (e.g. user
                        # wiped their keychain). Clear the field
                        # so the renderer shows "not configured"
                        # instead of leaking the reference token.
                        log.warning(
                            "[CONFIG] RW-01: %s field has keyring:// reference "
                            "but keyring returned no value — clearing (secret lost)",
                            field_name,
                        )
                        data[field_name] = ""
        except Exception as e:
            # Don't let credential_store issues break config
            # load — fall through with whatever values we have.
            # log only the exception TYPE (not the message) —
            # credential_store exceptions can echo the secret value
            # being loaded, which would leak into log files.
            log.warning(
                "[CONFIG] RW-01: credential_store integration failed: %s — continuing with config.json values as-is",
                type(e).__name__,
            )

        # H1: Validate non-numeric fields before construction
        data = cls._validate_non_numeric_fields(data)

        # validate hotkeys against the reserved-shortcut
        # denylist (mirrors the IPC set_config validation).
        # Config.load() previously bypassed this check -- a
        # stale or hand-edited config with "hotkey": "<ctrl>+<c>"
        # would steal Ctrl+C from every app on startup.  On
        # validation failure we reset the offending hotkey to
        # the platform default (<caps_lock>) and append a
        # warning to _load_warnings.
        default_hotkey = _default_hotkey_for_platform()
        for hotkey_field in ("hotkey", "push_to_talk_hotkey", "repaste_hotkey"):
            value = data.get(hotkey_field)
            # An empty push_to_talk_hotkey means "same as
            # toggle" -- skip empty strings.
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
        # set_config validation via ``_make_custom_theme_validator``).
        # Previously, a hand-edited or corrupt ``custom_theme`` dict
        # loaded without validation, causing schema drift between IPC
        # and disk paths. On validation failure, reset the field to
        # its default (None) and append a warning to
        # ``last_load_warnings`` so the user knows the field was reset.
        if "custom_theme" in data and data["custom_theme"] is not None:
            _theme_err = _make_custom_theme_validator()(data["custom_theme"])
            if _theme_err is not None:
                log.warning(
                    "[CONFIG] custom_theme validation failed on load (%s) — resetting to None",
                    _theme_err,
                )
                data.setdefault("_load_warnings", []).append(
                    f"custom_theme validation failed on load ({_theme_err}) — reset to None"
                )
                data["custom_theme"] = None

        # extract load warnings before construction
        # (cls(**data) would fail on the _load_warnings key)
        load_warnings = data.pop("_load_warnings", [])

        instance = cls(**data)
        load_warnings.extend(cross_platform_hotkey_warnings(instance))
        instance.last_load_warnings = load_warnings

        # AUDIO-PRESET-LOAD-FIX: apply the audio preset's filter
        # toggles on every load.
        #
        # pre-fix the failure was swallowed at DEBUG. A
        # preset-application failure means the user's audio filter
        # chain doesn't match their preset selection (e.g. a stale
        # preset name from a downgrade, or a preset module import
        # error after a botched package install). The user has no
        # way to know their audio filters are wrong because the
        # error was invisible. Promote to WARNING + append to
        # ``instance.last_load_warnings`` so the renderer surfaces
        # a "your audio preset couldn't be applied" notice.
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
        # (``validate_config``) so a hand-edited or migrated
        # config.json with out-of-range values (e.g. a stale
        # ``noise_suppression_method="speex"`` from before the
        # enum was tightened, or a future legacy ``audio_preset``
        # alias surviving a botched migration) is surfaced via
        # ``instance.last_load_warnings`` instead of being silently
        # loaded. Pre-fix, ``validate_config`` existed but was
        # never called from any production path (the IPC
        # ``set_config`` validator only sees the delta pushed by
        # the renderer, never the full on-disk config).
        try:
            from voice_typer.server.config_validators import validate_config

            full_config_errors = validate_config(instance)
            if full_config_errors:
                for _err in full_config_errors:
                    log.warning("[CONFIG] validate_config: %s", _err)
                instance.last_load_warnings.extend(f"validate_config: {_err}" for _err in full_config_errors)
        except Exception:
            log.debug("[CONFIG] validate_config on load failed", exc_info=True)

        # ``validate_config`` above only APPENDS warnings —
        # it does NOT mutate the field. A hand-edited
        # ``asr_backend="invalid"`` survives ``Config.load()``
        # verbatim, propagates to runtime code, and either crashes
        # a dispatch dict (``KeyError``) or silently takes the
        # wrong branch. ``_reset_invalid_enum_fields`` closes the
        # gap by resetting the high-impact ``Literal[...]`` enum
        # fields (asr_backend / recording_mode / bubble_position /
        # bubble_behavior / tray_left_click_action / theme_mode /
        # theme_preset / audio_preset / noise_suppression_method)
        # to their dataclass defaults when the on-disk value is
        # not in the Literal's allowed set. Each reset appends a
        # warning to ``instance.last_load_warnings`` so the
        # renderer (via the sanitizer's ``last_load_warnings`` key)
        # can surface a "your config was corrected" toast.
        #
        # Best-effort: a failure inside the reset helper must NOT
        # propagate (the config still loads — the invalid value
        # would just persist, matching the pre-fix behavior). The
        # ``validate_config`` call above already logged the
        # invalid value; the user has a signal even if this reset
        # is skipped.
        try:
            cls._reset_invalid_enum_fields(instance)
        except Exception:
            log.debug("[CONFIG] _reset_invalid_enum_fields on load failed", exc_info=True)

        # persist the bumped schema_version eagerly so
        # the next launch doesn't re-run the same migrations
        # (and re-trigger any bugs in a migrator that already
        # raised).  The save() is best-effort.
        #
        # pre-fix, the return value of ``instance.save()``
        # was silently discarded. A failed post-migration save
        # (e.g. read-only filesystem, disk full, permission denied
        # after a sudo-installed package upgrade) means the bumped
        # ``schema_version`` never lands on disk — the next launch
        # sees the OLD version and re-runs the same migrations
        # (which may have side effects like double-appending to
        # list fields or double-migrating keys). Promote to
        # WARNING + append to ``instance.last_load_warnings`` so
        # the renderer surfaces a "your migration couldn't be
        # persisted — migrations will re-run on next launch" notice.
        if migrations_ran:
            try:
                _post_migration_save_ok = instance.save()
            except Exception as post_mig_exc:
                log.warning(
                    "[CONFIG] eager post-migration save raised %s: %s — migrations will re-run on next launch",
                    type(post_mig_exc).__name__,
                    post_mig_exc,
                    exc_info=True,
                )
                instance.last_load_warnings.append(
                    f"post-migration save raised {type(post_mig_exc).__name__}: "
                    f"{post_mig_exc} — migrations will re-run on next launch"
                )
            else:
                if not _post_migration_save_ok:
                    log.warning("[CONFIG] post-migration save failed — migrations will re-run on next launch")
                    instance.last_load_warnings.append(
                        "post-migration save failed — migrations will re-run on next launch"
                    )

        # XZ-SEC-05: re-apply user-configured trusted hosts to the
        # runtime URL allowlist. The persisted ``trusted_extra_hosts``
        # list is the config.json-driven path for self-hosted
        # LLM/ASR endpoints (the env-var bootstrap in ``_secrets.py``
        # covers the process-startup path). Best-effort: an allowlist
        # failure must not break config load.
        try:
            trusted_hosts = instance.trusted_extra_hosts
            if trusted_hosts:
                from voice_typer.server._secrets import extend_url_allowlist

                extend_url_allowlist(trusted_hosts, caller="config.load")
        except Exception as _allowlist_exc:
            log.warning(
                "[CONFIG] XZ-SEC-05: could not re-apply trusted_extra_hosts to URL allowlist: %s",
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
        # the user can recover their settings manually from the
        # .corrupt-<timestamp> backup.  Without this, the next
        # Config.save() would atomically overwrite the corrupt
        # file with defaults, destroying any chance of forensic
        # recovery.  Path.replace is atomic.  Best-effort.
        try:
            # the previous ``int(time.time())`` suffix
            # had 1-second resolution — two corrupt loads in the
            # same second (e.g. the renderer triggers a quick
            # config reload + the backend independently tries to
            # load it during startup) silently overwrote each
            # other via ``Path.replace``, destroying the first
            # corrupt file's forensic recovery point. Adding the
            # PID disambiguates same-second loads from DIFFERENT
            # processes (the common race during backend restart);
            # for same-process same-second loads (very rare in
            # practice — requires a test loop or a hot-reload
            # dev environment) we additionally append
            # ``time.time_ns()`` mod 1_000_000 (microsecond
            # fraction) so even back-to-back calls produce unique
            # filenames. ``Path.replace`` is still atomic per
            # call, so the worst case if the suffix collides is
            # the previous-behavior overwrite (no corruption,
            # just lost-forensics — strictly better than before).
            corrupt_backup = (
                config_file.parent
                / f"config.json.corrupt-{int(time.time())}-{os.getpid()}-{time.time_ns() % 1_000_000}"
            )
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
