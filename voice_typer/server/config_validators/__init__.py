"""Pure input validators for IPC ``set_config`` payloads — package root.

This package was extracted from the original monolithic
``config_validators.py`` (1899 LOC) into focused submodules so each
concern has its own file:

* :mod:`voice_typer.server.config_validators.scalar` — scalar field
  validators (type / length / range / enum / URL / theme / hostname-list).
* :mod:`voice_typer.server.config_validators.hotkey` — reserved-shortcut
  denylist + the 9 ``_check_*`` stage helpers + :func:`_validate_hotkey`.
* :mod:`voice_typer.server.config_validators.language` — Whisper
  language-code allowlist + :func:`_validate_language` (split into
  shape-check / membership-check / error-formatter sub-functions).
* :mod:`voice_typer.server.config_validators.cross_field` — cross-field
  hotkey-conflict and cloud-config-consistency checks, plus the
  cross-platform hotkey portability warnings.
* :mod:`voice_typer.server.config_validators.allowlist` — the
  SEC-002 ``IPC_CONFIG_ALLOWLIST`` registry + supporting constants
  (``ALLOWED_USER_MODELS``, ``NOISE_SUPPRESSION_METHODS``, the
  ``MAX_RECORDING_TIME_SECONDS_*`` / ``STREAMING_*`` bounds, the
  pre-built ``_VALIDATOR_*`` instances).
* :mod:`voice_typer.server.config_validators.entry_points` — the two
  main entry points :func:`validate_config_update` (IPC ``set_config``
  delta validator) and :func:`validate_config` (whole-config load-time
  choke-point).  The cross-field helpers are looked up via this
  package's namespace at call time so ``monkeypatch`` /
  ``unittest.mock.patch`` on ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
  (see ``tests/test_config_validators_hotkey_nonstring.py``) keeps
  working — the entry-point functions deliberately avoid binding those
  helpers via a top-of-module ``from .cross_field import …``.

This ``__init__.py`` is the assembly point: it pulls every public name
back up to the ``voice_typer.server.config_validators`` namespace so
existing imports (``from voice_typer.server.config_validators import
validate_config``, ``… import IPC_CONFIG_ALLOWLIST``, etc.) continue
to work unchanged.  The package-level ``IPC_CONFIG_ALLOWLIST`` attribute
is the SEC-002 NON-NEGOTIABLE security contract (see
``AGENTS.md`` §6.3 / ``CONTRIBUTING.md`` §6.3) — its import path stays
put because this shim re-exports it from
:mod:`voice_typer.server.config_validators.allowlist`.

Every function in the package is *pure*: it takes a value (or, for the
factories, a spec like ``(lo, hi)``) and returns either ``None`` (success)
or a human-readable error string.  The only side effect in the whole
package is a single ``log.warning`` call inside
:func:`validate_config_update` when an unknown field is silently dropped
— matching the original behaviour in ``config.py``.

The package is import-safe: it does **not** import from
:mod:`voice_typer.server.config`, so it cannot participate in a circular
import.  ``config.py`` (and its split submodules under
``voice_typer/server/config/``) imports from this package (for
``ALLOWED_USER_MODELS``, the bounds constants, and the validators
themselves) and re-exports everything else via explicit ``from
.config_validators import …`` blocks at the bottom of
``config/__init__.py`` for backward compatibility.
"""

from __future__ import annotations

import sys as _sys  # noqa: F401  # test patch target (tests mutate cv._sys.platform → sys.platform)

from voice_typer.server.config_validators import allowlist as _allowlist_module

# ──────────────────────────────────────────────────────────────────────────
# SEC-002 allowlist + supporting constants.
#
# These used to live directly in this ``__init__.py`` (862 LOC).  They
# were extracted into :mod:`voice_typer.server.config_validators.allowlist`
# so the security-critical allowlist has its own focused home.  The
# re-export below keeps the public import path stable: every existing
# caller (``config/coercion.py``, ``config/__init__.py``,
# ``config_applier.py``, ``audio_filters/noise_suppressor.py``, the
# parity tests under ``tests/test_*allowlist*.py``) continues to import
# these names from ``voice_typer.server.config_validators`` exactly as
# before.
# ──────────────────────────────────────────────────────────────────────────
from voice_typer.server.config_validators.allowlist import (  # noqa: F401
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
    _VALIDATOR_TRUSTED_HOSTS,
    ALLOWED_USER_MODELS,
    MAX_RECORDING_TIME_SECONDS_DEFAULT,
    MAX_RECORDING_TIME_SECONDS_MAX,
    MAX_RECORDING_TIME_SECONDS_MIN,
    NOISE_SUPPRESSION_METHODS,
    STREAMING_LEFT_OVERLAP_SECONDS_MIN,
    STREAMING_RIGHT_GUARD_SECONDS_MIN,
)

# ──────────────────────────────────────────────────────────────────────────
# Submodule re-exports.  Importing these names into the package namespace
# means callers can keep using
# ``from voice_typer.server.config_validators import _validate_hotkey``
# (or any other symbol) exactly as before.  It also means
# :func:`validate_config` / :func:`validate_config_update` (in
# :mod:`voice_typer.server.config_validators.entry_points`) can reference
# the cross-field helpers via the package globals — which is essential
# because the regression tests in
# ``tests/test_config_validators_hotkey_nonstring.py`` monkeypatch
# ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
# and expect :func:`validate_config` to see the patched binding
# (the entry-point functions look the helpers up via a lazy import from
# the package namespace at call time, so this re-export is the load-bearing
# glue — see the docstring of :mod:`voice_typer.server.config_validators.entry_points`).
# ──────────────────────────────────────────────────────────────────────────
from voice_typer.server.config_validators.cross_field import (  # noqa: F401
    _CLOUD_CONSENT_FIELD_NAMES,
    _HOTKEY_FIELD_NAMES,
    _check_cross_field_cloud_config,
    _check_cross_field_hotkey_conflicts,
    _cross_platform_hotkey_warning,
    cross_platform_hotkey_warnings,
)
from voice_typer.server.config_validators.entry_points import (  # noqa: F401
    log,
    validate_config,
    validate_config_update,
)
from voice_typer.server.config_validators.hotkey import (  # noqa: F401
    _BLOCKED_CTRL_LETTERS,
    _HOTKEY_MODIFIERS,
    _RESERVED_HOTKEYS,
    _UNIVERSAL_RESERVED_HOTKEYS,
    _check_alt_shift,
    _check_basic_shape,
    _check_ctrl_letter,
    _check_multi_non_modifier,
    _check_os_shell_combos,
    _check_platform_reserved,
    _check_shift_letter,
    _check_single_alphanumeric,
    _check_universal_reserved,
    _parse_hotkey_parts,
    _platform_key,
    _validate_hotkey,
)
from voice_typer.server.config_validators.language import (
    _ALLOWED_LANGUAGES,
    _ALLOWED_LANGUAGES_SOURCE,
    _LANGUAGE_BASE_VALIDATOR,
    _validate_language,
)
from voice_typer.server.config_validators.scalar import (  # noqa: F401
    _MAX_API_KEY_LEN,
    _MAX_STRING_LEN,
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
    _validate_trusted_extra_hosts,
)

# Re-export ``IPC_CONFIG_ALLOWLIST`` with an explicit parameterised
# annotation so static type-checkers (and ``typing.get_type_hints(cv)``
# — exercised by
# ``tests/test_config.py::test_ipc_config_allowlist_is_dict_of_fieldspec``)
# see the ``dict[str, FieldSpec]`` hint on the package namespace. This is
# an annotated ALIAS ASSIGNMENT, not a bare re-annotation: it binds the
# package attribute to the SAME dict object defined in ``.allowlist``
# (identity preserved — this exact registry is the SEC-002 source of
# truth), while registering the hint in ``__annotations__``. A previous
# attempt used a bare annotation-only statement, which mypy flags as a
# no-redef redefinition of an imported name.
IPC_CONFIG_ALLOWLIST: dict[str, FieldSpec] = _allowlist_module.IPC_CONFIG_ALLOWLIST


# ──────────────────────────────────────────────────────────────────────────
# explicit ``__all__`` so the wildcard re-export in
# ``config.py`` (``from .config_validators import *``) brings through
# every validator symbol — including the underscore-prefixed factory
# helpers — preserving the pre-refactor import surface.
# ──────────────────────────────────────────────────────────────────────────
__all__ = [
    # Constants
    "ALLOWED_USER_MODELS",
    "NOISE_SUPPRESSION_METHODS",
    "_MAX_STRING_LEN",
    "_MAX_API_KEY_LEN",
    # Type aliases
    "ValidatorFn",
    "FieldSpec",
    # Predicate helpers
    "_is_str",
    "_is_int_not_bool",
    "_is_float_or_int_not_bool",
    # Validator factories
    "_make_str_validator",
    "_make_optional_str_validator",
    "_bool_validator",
    "_make_int_validator",
    "_make_optional_int_validator",
    "_make_float_validator",
    "_make_optional_float_validator",
    "_make_enum_validator",
    "_make_custom_theme_validator",
    "_make_url_validator",
    # Pre-built validator instances
    "_VALIDATOR_HOTKEY",
    "_VALIDATOR_LANGUAGE",
    "_VALIDATOR_API_KEY",
    "_VALIDATOR_API_URL",
    "_VALIDATOR_LLM_API_URL",
    "_VALIDATOR_LLM_MODEL",
    "_VALIDATOR_REPASTE_HOTKEY",
    "_VALIDATOR_MICROPHONE",
    "_VALIDATOR_PUSH_TO_TALK_HOTKEY",
    "_VALIDATOR_CLOUD_MODEL",
    # Public API
    "IPC_CONFIG_ALLOWLIST",
    "validate_config_update",
    "validate_config",
    # extracted hotkey validation stage helpers (:
    # reconciled with actual function names — the prior list referenced
    # 9 nonexistent symbols that caused F822 × 9 hard-fail in CI).
    "_check_basic_shape",
    "_check_universal_reserved",
    "_check_platform_reserved",
    "_check_single_alphanumeric",
    "_check_multi_non_modifier",
    "_check_os_shell_combos",
    "_check_alt_shift",
    "_check_ctrl_letter",
    "_check_shift_letter",
    # cross-field hotkey conflict check.
    "_HOTKEY_FIELD_NAMES",
    "_check_cross_field_hotkey_conflicts",
    # cross-platform hotkey portability warnings.
    "_cross_platform_hotkey_warning",
    "cross_platform_hotkey_warnings",
    # language code validator + allowlist.
    "_ALLOWED_LANGUAGES",
    "_ALLOWED_LANGUAGES_SOURCE",
    "_LANGUAGE_BASE_VALIDATOR",
    "_validate_language",
]
