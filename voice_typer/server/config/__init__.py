"""Configuration management with platform-aware storage.

W3-A5 / AC-131 continuation: the dataclass field declarations were
moved to :mod:`voice_typer.server.config._schema` (the
``_ConfigSchema`` base class) and the lifecycle / load / save method
delegators were moved to :mod:`voice_typer.server.config._lifecycle`
(the ``_ConfigLifecycleMixin``). This file is now a thin entry that
imports those, declares the final ``Config`` dataclass (combining the
two via multiple inheritance), and re-exports the legacy public
symbols so the pre-split import paths keep resolving.

Earlier splits (W1-A2 / AC-131) had already moved:
  - default-value constants → :mod:`voice_typer.server.config._defaults`
  - top-level accessors (purge_user_data, …) → :mod:`voice_typer.server.config._accessors`
  - save / write logic → :mod:`voice_typer.server.config._saving`
  - migration helpers → :mod:`voice_typer.server.config._migration`
  - schema constants + impls → :mod:`voice_typer.server.config._schema`
  - systemroot shim → :mod:`voice_typer.server.config._systemroot`
  - load orchestrator → :mod:`voice_typer.server.config.loader`
  - load-time coercion helpers → :mod:`voice_typer.server.config.coercion`
  - field-validation / warning helpers → :mod:`voice_typer.server.config.sanitization`

This module re-exports every symbol those leaf modules define so
``from voice_typer.server.config import X`` keeps working unchanged.
"""

# sunset policy: cross-reference tags (ARCH-/CR-/G4-/H12/SEC-/RW-/GT-
# DE-/PVT-/XV-/XZ-) are historical rationale for fix-waves that landed
# in prior sessions. They are intentionally retained as a defensive
# trace of WHY a line exists, but future contributors SHOULD NOT add
# new tag-style comments here — use a single-line
# "# FIX-NNN: see PR <link>" pointer instead.

import json  # noqa: F401 — re-exported for callers / tests
import logging
import threading  # noqa: F401 — re-exported (referenced by Config.set_mutation_lock docstring)
import time  # noqa: F401 — re-exported for callers / tests
import types  # noqa: F401 — re-exported (used by schema impls via wildcard)
from dataclasses import asdict, dataclass, field  # noqa: F401 — re-exported + used by Config
from pathlib import Path  # noqa: F401 — re-exported
from typing import Any, ClassVar, Literal  # noqa: F401 — re-exported

# deferred imports for shared canonical constants used as Config field
# defaults (the field declarations live in ``_schema._ConfigSchema``,
# which has its own copies of these imports; they are re-exported here
# for callers / parity tests that import them via the ``config``
# module namespace).
from voice_typer.server._audio_constants import (
    _DEFAULT_SMART_DUCK_POLL_MS,  # noqa: E402,F401
    WHISPER_SAMPLE_RATE,  # noqa: F401 — re-exported
)
from voice_typer.server._paths import DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL  # noqa: E402,F401

# Single source of truth for the user-data file inventories used by
# ``purge_user_data`` (re-exported below) and by the GDPR
# ``delete_all_personal_data`` / ``export_gdpr_bundle`` paths (in
# ``service/privacy.py``). See ``_user_data_files.py`` for the
# per-file rationale and the canonical ``*_FILENAME`` imports.
from voice_typer.server._user_data_files import (  # noqa: F401 — re-exported
    _LEGACY_RECOVERY_FILENAME,
    _RECOVERY_FILENAME,
    _USER_DATA_FILES,
)
from voice_typer.server.config._accessors import (  # noqa: F401 — re-exported for callers
    _legacy_voice_typer_dir,
    _prune_kept_backups,
    purge_all_user_data,
    purge_user_data,
)

# W1-A2 / AC-131: monolith split. The following concerns were moved
# into sibling modules so ``__init__.py`` stays focused on the Config
# dataclass + re-exports. Each module is imported eagerly here so the
# legacy public API continues to resolve via
# ``from voice_typer.server.config import X``:
from voice_typer.server.config._defaults import (  # noqa: F401 — re-exported for callers
    _USER_DATA_DIRS,
    DEFAULT_CLIPBOARD_RESTORE_DELAY_MS,
    DEFAULT_HOTKEY,
    _default_hotkey_for_platform,
)

# W3-A5 / AC-131 continuation: lifecycle mixin extracted from this
# module. Provides ``__post_init__`` / ``__setattr__`` /
# ``set_mutation_lock`` / ``save`` / ``load`` / ``_coerce_*`` /
# ``_validate_*`` / ``config_dir`` (etc.) as thin delegators to the
# sibling leaf modules. ``Config`` inherits these methods via
# multiple-inheritance — callers see the same public API.
from voice_typer.server.config._lifecycle import (  # noqa: F401 — re-exported + used by Config inheritance
    _ConfigLifecycleMixin,
)
from voice_typer.server.config._migration import (  # noqa: F401 — re-exported + used by lifecycle mixin
    _backup_before_downgrade_impl,
)
from voice_typer.server.config._saving import (  # noqa: F401 — re-exported + used by lifecycle mixin
    _enforce_windows_owner_only_acl,
    _save_impl,
    _save_strict_impl,
    _save_unlocked_impl,
    _save_with_mutation_lock_impl,
    _warmup_keyring_probe_impl,
)
from voice_typer.server.config._schema import (  # noqa: F401 — re-exported + used by Config inheritance
    _ENUM_FIELDS_TO_RESET_ON_LOAD,
    _SECRET_FIELD_NAMES_FALLBACK,
    _ConfigSchema,
    _reset_invalid_enum_fields_impl,
    _secret_field_names_impl,
)
from voice_typer.server.config._systemroot import (  # noqa: F401 — re-export
    _validate_systemroot,
)

# Load-time data-dict transforms + non-numeric field validation helpers
# extracted from this module to keep the Config dataclass focused on
# schema declaration + load/save orchestration. The Config classmethods
# of the same names (now inherited from ``_ConfigLifecycleMixin``) are
# thin delegators that forward to these module-level functions.
from voice_typer.server.config.coercion import (  # noqa: F401 — re-exported for Config classmethod delegators
    _coerce_max_recording_time,
    _coerce_streaming_fields,
    _validate_corrections_path,
    _validate_model_path,
    _validate_privacy_consents,
    _validate_qwen_model_path,
)

# Config.load() orchestrator + JSON-read / key-filter helpers extracted
# from this module to chip away at the monolith.
from voice_typer.server.config.loader import (  # noqa: F401 — re-exported for Config classmethod delegators
    _filter_unknown_keys_impl,
    _load_config,
    _read_raw_json_impl,
)
from voice_typer.server.config.sanitization import (  # noqa: F401 — re-exported for Config classmethod delegators
    _derive_field_type_registry as _sanitization_derive_field_type_registry,
    _validate_non_numeric_fields as _sanitization_validate_non_numeric_fields,
    _warn_and_coerce as _sanitization_warn_and_coerce,
    _warn_and_reset as _sanitization_warn_and_reset,
)
from voice_typer.server.config_internals.migrations import (  # noqa: F401 — backward-compat re-export
    _CURRENT_SCHEMA_VERSION,
    _MIGRATIONS,
    _backup_before_migration_impl,
    _migrate_to_v2,
    _migrate_to_v3,
    _run_migrations,
)
from voice_typer.server.config_internals.paths import (  # noqa: F401 — backward-compat re-export
    _CONFIG_LOCK_TIMEOUT_SECONDS,
    _acquire_config_lock,
    _config_dir,
    _migrate_from_legacy,
    _reset_config_dir_cache,
    # ``_validate_systemroot`` is also re-exported via
    # ``config._systemroot`` above; both bindings point at the SAME
    # function object (``_systemroot.py`` is itself a thin re-export
    # shim that imports from ``config_internals.paths``). The explicit
    # re-import here is omitted to avoid ruff F811.
)

# path-safety helpers are re-exported via the dedicated
# ``config_path_safety`` module so future contributors can grep for
# path-traversal guards in one place.
from voice_typer.server.config_path_safety import (  # noqa: F401 — backward-compat re-export
    _is_path_within,
    _validate_import_path,
    _validate_path_safety,
)

# canonical bounds + default for ``max_recording_time_seconds``.
# Defined in ``config_validators.py`` (the import-safe leaf module) and
# re-imported here so this module + the IPC validator share a single
# source of truth.
from voice_typer.server.config_validators import (  # noqa: F401 — backward-compat re-export  # noqa: E402,F401 — re-exported so tests / parity checks can import from voice_typer.server.config
    MAX_RECORDING_TIME_SECONDS_DEFAULT,
    MAX_RECORDING_TIME_SECONDS_MAX,
    MAX_RECORDING_TIME_SECONDS_MIN,
    # canonical lower bounds for the streaming-overlap / -guard seconds.
    STREAMING_LEFT_OVERLAP_SECONDS_MIN,
    STREAMING_RIGHT_GUARD_SECONDS_MIN,
    _validate_hotkey,
    cross_platform_hotkey_warnings,
)
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE  # noqa: F401 — re-exported

# ``is_macos`` is re-exported (not used directly in this module) so
# ``config_internals.paths._is_macos()`` can look it up via
# ``voice_typer.server.config.is_macos`` (the lazy-import shim in
# ``paths.py`` was written assuming this attribute exists). Without
# this re-export, ``_is_macos()`` raises ``AttributeError`` in
# production on non-Windows platforms (Linux fresh-install without the
# legacy ``~/.voice-typer`` dir, or macOS), which breaks ``_config_dir()``
# and every caller — including :func:`purge_user_data` and
# :func:`purge_all_user_data`.
from voice_typer.server.platform_utils import (  # noqa: F401 — is_macos re-exported for paths._is_macos()
    is_macos,
    is_windows,
)
from voice_typer.server.secure_file_io import (  # noqa: F401 — backward-compat re-export
    _secure_atomic_write,
    _secure_read_text,
)

log = logging.getLogger("voice_typer.server.config")

# Module-level flag recording whether
# :meth:`Config._warmup_keyring_probe` has been called. The impl in
# ``_saving.py`` mutates this attribute via
# ``voice_typer.server.config._warmup_called = True`` so the flag update
# lands in this module's globals (tests read it back here).
_warmup_called: bool = False

# Windows-only: config directories whose owner-only ACL has ALREADY been
# enforced in this process. ``_enforce_windows_owner_only_acl`` (in
# ``_saving.py``) skips the (expensive, ~210ms) ``icacls`` subprocess for
# files whose parent dir is in this set.
_windows_owner_only_acl_verified: set[str] = set()


@dataclass
class Config(_ConfigSchema, _ConfigLifecycleMixin):
    """Application configuration.

    W3-A5 / AC-131 continuation: the field declarations live in
    :class:`voice_typer.server.config._schema._ConfigSchema` and the
    lifecycle / load / save method delegators live in
    :class:`voice_typer.server.config._lifecycle._ConfigLifecycleMixin`.
    This class combines the two via multiple-inheritance; the
    ``@dataclass`` decorator picks up the inherited field declarations
    via ``__dataclass_fields__`` so callers see the same public API
    (``Config(schema_version=1, hotkey='x', ...)``,
    ``cfg.save()``, ``Config.load()``, ``cfg._secret_field_names()``
    etc.).

    Pre-split callers/tests that did ``isinstance(cfg, Config)`` /
    ``Config.__module__ == 'voice_typer.server.config'`` continue to
    work — ``Config`` is still defined in this module (it just has
    an empty body now).
    """

    # The body is intentionally empty. Field declarations live on
    # ``_ConfigSchema``; method delegators live on
    # ``_ConfigLifecycleMixin``. The dataclass decorator processes
    # inherited fields via MRO. Adding fields here would require
    # they have defaults (since ``_ConfigSchema``'s fields all do).


# ──────────────────────────────────────────────────────────────────────────
# validator block moved to ``config_validators.py``.
# The explicit import below mirrors ``config_validators.__all__``
# exactly (minus ``ALLOWED_USER_MODELS``, which is already imported
# at the top of this file for use by ``Config.load()`` via the schema
# base class). Re-importing it here would trip ruff F811 (redefinition
# of unused name) without changing the module's public surface, so it
# is intentionally omitted from this list.
#
# If a future change to ``config_validators.__all__`` adds a new symbol
# that callers expect to reach via
# ``from voice_typer.server.config import …``, it MUST be added to
# this list explicitly — that's the whole point of replacing the
# wildcard.
# ──────────────────────────────────────────────────────────────────────────
from voice_typer.server.config_validators import (  # noqa: E402,F401 — backward-compat bottom-of-file re-export
    _MAX_API_KEY_LEN,
    _MAX_STRING_LEN,
    _VALIDATOR_API_KEY,
    _VALIDATOR_API_URL,
    _VALIDATOR_CLOUD_MODEL,
    _VALIDATOR_HOTKEY,
    _VALIDATOR_LANGUAGE,
    _VALIDATOR_LLM_API_URL,
    _VALIDATOR_LLM_MODEL,
    _VALIDATOR_MICROPHONE,
    _VALIDATOR_PUSH_TO_TALK_HOTKEY,
    _VALIDATOR_REPASTE_HOTKEY,
    IPC_CONFIG_ALLOWLIST,
    FieldSpec,
    ValidatorFn,
    _bool_validator,
    _is_float_or_int_not_bool,
    _is_int_not_bool,
    _is_str,
    _make_custom_theme_validator,
    _make_enum_validator,
    _make_float_validator,
    _make_int_validator,
    _make_optional_float_validator,
    _make_optional_int_validator,
    _make_optional_str_validator,
    _make_str_validator,
    _make_url_validator,
    validate_config,
    validate_config_update,
)
