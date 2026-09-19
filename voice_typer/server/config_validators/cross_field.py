"""Cross-field config validators."""

from __future__ import annotations

from voice_typer.server.config_validators.hotkey import (
    _RESERVED_HOTKEYS,
    _check_alt_shift,
    _check_os_shell_combos,
    _check_platform_reserved,
    _parse_hotkey_parts,
    _platform_key,
)

# cross-field hotkey conflict check and cross-platform

# The hotkey fields whose values must not collide.  ``push_to_talk_hotkey``
_HOTKEY_FIELD_NAMES: tuple[str, ...] = ("hotkey", "repaste_hotkey")


def _check_cross_field_hotkey_conflicts(
    field_values: dict[str, str | None],
) -> list[str]:
    """Detect duplicate hotkey assignments across the hotkey fields.

    Returns
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    # Map canonical spec string -> list of field names that have it.
    seen: dict[str, list[str]] = {}
    for field_name in _HOTKEY_FIELD_NAMES:
        value = field_values.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        spec = parse_hotkey(value)
        if spec.is_empty:
            continue
        canonical = spec.to_spec_string()
        seen.setdefault(canonical, []).append(field_name)

    errors: list[str] = []
    for canonical, fields in seen.items():
        if len(fields) > 1:
            # If 3 fields all share the same value, report two conflicts
            for other in fields[1:]:
                errors.append(f"Hotkey conflict: {canonical} is assigned to both '{fields[0]}' and '{other}'")
    return errors


# Cloud/LLM cross-field config field names that participate in the
_CLOUD_CONSENT_FIELD_NAMES: tuple[str, ...] = (
    "cloud_openai_consent",
    "cloud_groq_consent",
    "cloud_deepgram_consent",
)


def _check_cross_field_cloud_config(
    field_values: dict[str, object],
) -> list[str]:
    """cross-field cloud/LLM config consistency check.

    Returns
    """
    errors: list[str] = []

    # Cloud URL + key must be both set or both empty.
    has_url = "cloud_api_url" in field_values
    has_key = "cloud_api_key" in field_values
    if has_url and has_key:
        url_val = field_values.get("cloud_api_url")
        key_val = field_values.get("cloud_api_key")
        url_set = isinstance(url_val, str) and url_val.strip() != ""
        key_set = isinstance(key_val, str) and key_val.strip() != ""
        if url_set and not key_set:
            errors.append("cloud_api_key is required when cloud_api_url is set")
        if key_set and not url_set:
            errors.append("cloud_api_url is required when cloud_api_key is set")

    # LLM polish requires an API key (when both fields are in the update).
    if "llm_polish" in field_values and "llm_api_key" in field_values:
        polish_val = field_values.get("llm_polish")
        key_val = field_values.get("llm_api_key")
        key_set = isinstance(key_val, str) and key_val.strip() != ""
        if polish_val is True and not key_set:
            errors.append("llm_api_key is required when llm_polish is True")

    # LLM polish requires explicit consent (when both fields are in the update).
    if "llm_polish" in field_values and "llm_polish_consent" in field_values:
        polish_val = field_values.get("llm_polish")
        consent_val = field_values.get("llm_polish_consent")
        if polish_val is True and consent_val is not True:
            errors.append("llm_polish_consent must be True when llm_polish is True")

    # NOTE: no ``cloud_*_consent`` → ``cloud_api_key`` check. Consent

    return errors


def _cross_platform_hotkey_warning(value: str, field_name: str) -> str | None:
    """Return a portability warning if ``value`` is reserved on a non-current platform.

    Returns ``None`` if the value is valid on every non-current platform
    """
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    parts = _parse_hotkey_parts(value)
    if not parts:
        return None
    current_platform = _platform_key()
    for platform in _RESERVED_HOTKEYS:
        if platform == current_platform:
            continue
        # Run the same platform-specific stage helpers that
        err = _check_platform_reserved(normalized, platform)
        if err is None:
            err = _check_os_shell_combos(parts, platform)
        if err is None:
            err = _check_alt_shift(parts, platform)
        if err is not None:
            return f"{field_name} ({value!r}) is {err}, this config will not be portable to that platform"
    return None


def cross_platform_hotkey_warnings(cfg: object) -> list[str]:
    """Produce portability warnings for every hotkey field on ``cfg``.

    Returns
    """
    warnings: list[str] = []
    for field_name in _HOTKEY_FIELD_NAMES:
        try:
            value = getattr(cfg, field_name)
        except AttributeError:
            continue
        if value is None:
            continue
        warning = _cross_platform_hotkey_warning(value, field_name)
        if warning is not None:
            warnings.append(warning)
    return warnings


__all__ = [
    "_HOTKEY_FIELD_NAMES",
    "_CLOUD_CONSENT_FIELD_NAMES",
    "_check_cross_field_hotkey_conflicts",
    "_check_cross_field_cloud_config",
    "_cross_platform_hotkey_warning",
    "cross_platform_hotkey_warnings",
]
