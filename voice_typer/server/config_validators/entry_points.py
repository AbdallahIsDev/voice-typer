""":data:`IPC_CONFIG_ALLOWLIST` (silently dropping unknown keys with a
IPC ``set_config`` entry points, :func:`validate_config_update`
``config_validators/__init__.py`` so the two IPC / load-time entry
* :func:`validate_config_update`: the IPC ``set_config`` payload
  single ``log.warning`` side effect) and runs every per-field
Both functions are pure (apart from the single ``log.warning`` call
silently dropped, matching the original behaviour in ``config.py``).
regression tests in ``tests/test_config_validators_hotkey_nonstring.py``
``monkeypatch`` /
``unittest.mock.patch`` ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
``from .cross_field import …`` at the top of this module, the patch on
mirrors the existing call-time lookup in ``config/loader.py``.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.config_validators.allowlist import IPC_CONFIG_ALLOWLIST
from voice_typer.server.config_validators.cross_field import (
    _CLOUD_CONSENT_FIELD_NAMES,
    _HOTKEY_FIELD_NAMES,
)

log = logging.getLogger("voice_typer.server.config_validators")


def validate_config_update(data: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """:data:`IPC_CONFIG_ALLOWLIST` and whose values passed their"""
    validated: dict[str, object] = {}
    errors: list[str] = []
    for k, v in data.items():
        spec = IPC_CONFIG_ALLOWLIST.get(k)
        if spec is None:
            # Unknown key, silently drop.
            log.warning("[CONFIG] set_config dropped unknown key %r", k)
            continue
        expected_type, validator = spec
        # Type-check first (cheap), then run the field-specific validator
        type_ok: bool
        if isinstance(expected_type, tuple):
            type_ok = isinstance(v, expected_type)
        elif expected_type is bool:
            type_ok = isinstance(v, bool)
        elif expected_type is int:
            type_ok = isinstance(v, int) and not isinstance(v, bool)
        elif expected_type is float:
            type_ok = isinstance(v, int | float) and not isinstance(v, bool)
        elif expected_type is str:
            type_ok = isinstance(v, str)
        else:
            # Should never happen for the current allowlist.
            type_ok = isinstance(v, expected_type)
        if not type_ok:
            type_name = (
                " or ".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            errors.append(f"field {k!r} must be {type_name}, got {type(v).__name__}")
            # accumulate ALL errors, do not break on first.
            continue
        err = validator(v)
        if err is not None:
            errors.append(f"field {k!r} {err}")
            # accumulate ALL errors, do not break on first.
            continue
        validated[k] = v
    # cross-field hotkey conflict check.  Only fields that
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        if name in validated:
            raw = validated[name]
            hotkey_values[name] = raw if isinstance(raw, str) else None
        else:
            hotkey_values[name] = None
    # Lazy-import the cross-field helpers from the package namespace so
    from voice_typer.server.config_validators import (
        _check_cross_field_cloud_config,
        _check_cross_field_hotkey_conflicts,
    )

    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # cross-field cloud/LLM config consistency check.
    cloud_field_values: dict[str, object] = {}
    for cloud_name in (
        "cloud_api_url",
        "cloud_api_key",
        "llm_polish",
        "llm_api_key",
        "llm_polish_consent",
        *_CLOUD_CONSENT_FIELD_NAMES,
    ):
        if cloud_name in validated:
            cloud_field_values[cloud_name] = validated[cloud_name]
    errors.extend(_check_cross_field_cloud_config(cloud_field_values))
    return validated, errors


def validate_config(cfg: object) -> list[str]:
    """:data:`IPC_CONFIG_ALLOWLIST`."""
    errors: list[str] = []
    for key, (_field_type, validator) in IPC_CONFIG_ALLOWLIST.items():
        try:
            value = getattr(cfg, key)
        except AttributeError:
            # Field isn't present on the object, treat as "not set"
            continue
        if value is None:
            continue
        err = validator(value)
        if err:
            errors.append(f"{key}: {err}")
    # cross-field hotkey conflict check on the FULL config.
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        try:
            # narrow the ``getattr`` result explicitly so the
            raw = getattr(cfg, name)
            hotkey_values[name] = raw if isinstance(raw, str) else None
        except AttributeError:
            hotkey_values[name] = None
    # Lazy-import the cross-field helpers from the package namespace so
    from voice_typer.server.config_validators import (
        _check_cross_field_cloud_config,
        _check_cross_field_hotkey_conflicts,
    )

    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # cross-field cloud/LLM config consistency check
    cloud_field_values: dict[str, object] = {}
    for cloud_name in (
        "cloud_api_url",
        "cloud_api_key",
        "llm_polish",
        "llm_api_key",
        "llm_polish_consent",
        *_CLOUD_CONSENT_FIELD_NAMES,
    ):
        # Field isn't present on the object, treat as "not set"
        with contextlib.suppress(AttributeError):
            cloud_field_values[cloud_name] = getattr(cfg, cloud_name)
    errors.extend(_check_cross_field_cloud_config(cloud_field_values))
    return errors
