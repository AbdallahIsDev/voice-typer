"""Cross-field config validators.

Extracted from the original monolithic ``config_validators.py`` (package
split).  These validators layer on top of the per-field validators
in :mod:`voice_typer.server.config_validators.scalar` and
:mod:`voice_typer.server.config_validators.hotkey`:

* :func:`_check_cross_field_hotkey_conflicts` — detects when two of the
  three hotkey fields (``hotkey``, ``repaste_hotkey``,
  ``push_to_talk_hotkey``) are assigned the same value.  Called from both
  :func:`validate_config_update` and :func:`validate_config` so the
  conflict is caught at IPC-write time AND at config-load time.

* :func:`_check_cross_field_cloud_config` — catches inconsistencies
  between paired cloud/LLM config fields (URL without key, polish without
  key, polish without consent, consent without key).

* :func:`cross_platform_hotkey_warnings` — checks each hotkey value
  against the reserved lists of EVERY non-current platform and returns
  warning strings (NOT errors — the hotkey is valid on the user's
  current platform).  Callers (e.g. ``Config.load()`` in
  ``voice_typer/server/config/__init__.py``) should append the returned
  strings to ``Config._load_warnings`` / ``last_load_warnings`` so the
  UI can surface them as non-blocking portability notices.
"""

from __future__ import annotations

from voice_typer.server.config_validators.hotkey import (
    _RESERVED_HOTKEYS,
    _check_platform_reserved,
    _platform_key,
)

# ──────────────────────────────────────────────────────────────────────────
# cross-field hotkey conflict check and cross-platform
# portability warnings.
#
# The per-field ``_validate_hotkey`` (in hotkey.py) only consults the
# *current* platform's reserved list and only sees one hotkey field at a
# time.  These two helpers layer on top of it:
#
# func:`_check_cross_field_hotkey_conflicts`: detects when
#     two of the three hotkey fields (``hotkey``, ``repaste_hotkey``,
#     ``push_to_talk_hotkey``) are assigned the same value.  Called from
#     both :func:`validate_config_update` and :func:`validate_config` so
#     the conflict is caught at IPC-write time AND at config-load time.
#
# func:`cross_platform_hotkey_warnings` (): checks each hotkey
#     value against the reserved lists of EVERY non-current platform and
#     returns warning strings (NOT errors — the hotkey is valid on the
#     user's current platform).  Callers (e.g. ``Config.load()`` in
#     ``config.py``) should append the returned strings to
#     ``Config._load_warnings`` / ``last_load_warnings`` so the UI can
#     surface them as non-blocking portability notices.
# ──────────────────────────────────────────────────────────────────────────

# The three hotkey fields whose values must not collide.  Note that
# ``push_to_talk_hotkey`` is NOT in :data:`IPC_CONFIG_ALLOWLIST` (removed
# per the SEC-002 audit — see comment at the allowlist entry for
# ``repaste_hotkey``), so the IPC path's cross-field check will only see
# fields that survive the per-field validator (i.e. ``hotkey`` and
# ``repaste_hotkey``).  The full-config validator (:func:`validate_config`)
# DOES see all three fields via ``getattr(cfg, name)``, so a hand-edited
# config.json that sets a conflicting ``push_to_talk_hotkey`` is still
# caught at load time.
_HOTKEY_FIELD_NAMES: tuple[str, ...] = ("hotkey", "repaste_hotkey", "push_to_talk_hotkey")


def _check_cross_field_hotkey_conflicts(
    field_values: dict[str, str | None],
) -> list[str]:
    """Detect duplicate hotkey assignments across the 3 hotkey fields.

    without this cross-field check, a user could set
        ``hotkey=<ctrl>+<space>`` AND ``push_to_talk_hotkey=<ctrl>+<space>``
        simultaneously and the second assignment would silently overwrite
        the first when both listeners register with pynput / Win32
        ``RegisterHotKey`` / macOS ``CGEventTap``.

        Parameters
        ----------
        field_values
            A mapping from hotkey field name (``"hotkey"``,
            ``"repaste_hotkey"``, ``"push_to_talk_hotkey"``) to its current
            value (or ``None`` if not set).  Unknown field names are
            ignored; missing field names are treated as ``None``.

        Returns
        -------
        list[str]
            A list of human-readable error strings, one per conflicting
            pair.  Empty list means no conflicts.  Each error names BOTH
            conflicting fields so the user can decide which one to change,
            and includes the canonical hotkey spec (so ``<ctrl>+<space>``
            and ``<ctrl>+<SPACE>`` are recognised as the same hotkey).
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    # Map canonical spec string -> list of field names that have it.
    # Skip empty/None values: a None hotkey means "not set", and two
    # unset hotkeys don't conflict.
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
            # (fields[0] vs fields[1], fields[0] vs fields[2]) so the
            # user sees every collision involving the first field.
            for other in fields[1:]:
                errors.append(f"Hotkey conflict: {canonical} is assigned to both '{fields[0]}' and '{other}'")
    return errors


# Cloud/LLM cross-field config field names that participate in the
# consistency check. Used by both :func:`validate_config_update`
# (delta-only check) and :func:`validate_config` (full-config check).
_CLOUD_CONSENT_FIELD_NAMES: tuple[str, ...] = (
    "cloud_openai_consent",
    "cloud_groq_consent",
    "cloud_deepgram_consent",
)


def _check_cross_field_cloud_config(
    field_values: dict[str, object],
) -> list[str]:
    """cross-field cloud/LLM config consistency check.

    Catches inconsistencies between paired cloud/LLM config fields at
    IPC save time (and at config-load time via :func:`validate_config`)
    so the user doesn't discover the inconsistency at transcribe time
    (when ``cloud_engines.CloudEngine.transcribe`` raises
    ``CloudConfigError``).

    The check fires ONLY when BOTH related fields are present in
    ``field_values`` — for the IPC ``set_config`` path, the renderer
    may push only ONE of the two paired fields (e.g. just
    ``cloud_api_url`` without ``cloud_api_key``), and the other field
    may already be set in the saved config. False positives here would
    break the common "update one field at a time" UX.

    Parameters
    ----------
    field_values
        A mapping from field name to its current value. Only fields
        present in this dict participate in the cross-field check
        (missing fields are treated as "not in this update" and
        skipped — they may be set in the saved config).

    Returns
    -------
    list[str]
        A list of human-readable error strings, one per
        inconsistency. Empty list means the config is consistent.
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

    # Any cloud_*_consent=True requires cloud_api_key (when both the
    # consent flag and the key are in the update).
    if has_key:
        key_val = field_values.get("cloud_api_key")
        key_set = isinstance(key_val, str) and key_val.strip() != ""
        if not key_set:
            for consent_field in _CLOUD_CONSENT_FIELD_NAMES:
                if consent_field in field_values and field_values.get(consent_field) is True:
                    errors.append(f"cloud_api_key is required when {consent_field} is True")

    return errors


def _cross_platform_hotkey_warning(value: str, field_name: str) -> str | None:
    """Return a portability warning if ``value`` is reserved on a non-current platform.

    ``_validate_hotkey`` only consults the *current* platform's
        reserved list (via :func:`_platform_key`), so a hotkey like
        ``<cmd>+<q>`` passes on Linux but quits apps on macOS.  This helper
        checks the value against EVERY platform's reserved list (except the
        current one, which is already enforced by ``_validate_hotkey`` as a
        hard rejection) and returns a warning string for the first non-current
        conflict found.

        Returns ``None`` if the value is valid on every non-current platform
        (or if the value is empty / not a string).

        The warning is informational only — the hotkey may be perfectly valid
        on the user's current platform, and rejecting it would deny the user
        the freedom to set platform-specific shortcuts.  The warning just
        alerts them that the config won't be portable to the named platform.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    current_platform = _platform_key()
    for platform in _RESERVED_HOTKEYS:
        if platform == current_platform:
            continue
        err = _check_platform_reserved(normalized, platform)
        if err is not None:
            return f"{field_name} ({value!r}) is {err} — this config will not be portable to that platform"
    return None


def cross_platform_hotkey_warnings(cfg: object) -> list[str]:
    """Produce portability warnings for every hotkey field on ``cfg``.

    this is the warnings counterpart of :func:`validate_config`.
        It checks each of the 3 hotkey fields (``hotkey``, ``repaste_hotkey``,
        ``push_to_talk_hotkey``) against the reserved lists of every
        non-current platform and returns a list of human-readable warning
        strings.

        Callers (e.g. ``Config.load()`` in :mod:`voice_typer.server.config`)
        should append the returned strings to ``Config._load_warnings`` /
        ``last_load_warnings`` so the UI can surface them as non-blocking
        notices.  The mechanism mirrors how :func:`validate_config` errors
        are surfaced (see the docstring of :func:`validate_config` for the
        coordination note with agent 2-a / SA11).

        Warnings (NOT errors) are emitted because the hotkey may be perfectly
        valid on the user's current platform — rejecting it would deny the
        user the freedom to set platform-specific shortcuts.  The warning
        just alerts them that the config won't be portable.

        Parameters
        ----------
        cfg
            A :class:`Config` dataclass instance (duck-typed — only
            ``getattr`` is used, so any object exposing the hotkey fields
            as attributes works for testing).

        Returns
        -------
        list[str]
            A list of warning strings, one per non-current platform conflict.
            Empty list means the config is portable (or no hotkeys are set).
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
