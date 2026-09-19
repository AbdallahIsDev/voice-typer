"""Scalar field validators: type, length, range, enum, URL, and theme."""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Callable
from typing import TypeGuard
from urllib.parse import urlparse

# A field validator returns ``None`` on success or a human-readable error

ValidatorFn = Callable[[object], str | None]
# widened to accept either a single ``type`` (e.g. ``str``, ``bool``)
FieldSpec = tuple[type | tuple[type, ...], ValidatorFn]


def _is_str(v: object) -> TypeGuard[str]:
    return isinstance(v, str)


def _is_int_not_bool(v: object) -> TypeGuard[int]:
    # bool is a subclass of int in Python; reject it explicitly so that
    return isinstance(v, int) and not isinstance(v, bool)


def _is_float_or_int_not_bool(v: object) -> TypeGuard[float]:
    # Accept ints on the numeric tower (they're valid floats), but still
    return isinstance(v, int | float) and not isinstance(v, bool)


# Sane upper bound for any single string field.  API keys, URLs, hotkey
_MAX_STRING_LEN = 8192

# API keys can be longer than typical strings (some Bearer tokens exceed
_MAX_API_KEY_LEN = 16384

# Shared error-message templates for the string validator family.
_ERR_MUST_BE_STRING = "must be a string, got {type_name}"
_ERR_EXCEEDS_MAX_LEN = "exceeds maximum length {max_len}, got length {actual_len}"
_ERR_CONTROL_CHAR = "contains control character (ord={ord})"


def _make_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return _ERR_MUST_BE_STRING.format(type_name=type(v).__name__)
        if len(v) > max_len:
            # include the actual length so the operator can see how
            return _ERR_EXCEEDS_MAX_LEN.format(max_len=max_len, actual_len=len(v))
        # Reject C0 control characters (0x00-0x1F), DEL (0x7F), AND C1
        for ch in v:
            o = ord(ch)
            if o < 0x20 or 0x7F <= o <= 0x9F:
                return _ERR_CONTROL_CHAR.format(ord=o)
        return None

    return _validate


def _make_optional_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    # Deduplicated: a None value short-circuits to success, and every
    inner = _make_str_validator(max_len)

    def _validate(v: object) -> str | None:
        if v is None:
            return None
        return inner(v)

    return _validate


def _bool_validator(v: object) -> str | None:
    if not isinstance(v, bool):
        return f"must be a boolean, got {type(v).__name__}"
    return None


def _make_int_validator(*, lo: int, hi: int) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_int_not_bool(v):
            return f"must be an integer, got {type(v).__name__}"
        if v < lo or v > hi:
            # include the actual value, ints are non-PII and the
            return f"must be in [{lo}, {hi}], got {v}"
        return None

    return _validate


def _make_optional_int_validator(*, lo: int, hi: int) -> ValidatorFn:
    # Mirrors :func:`_make_optional_str_validator`: short-circuit
    inner = _make_int_validator(lo=lo, hi=hi)

    def _validate(v: object) -> str | None:
        if v is None:
            return None
        return inner(v)

    return _validate


def _make_float_validator(*, lo: float, hi: float) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_float_or_int_not_bool(v):
            return f"must be a number, got {type(v).__name__}"
        # NaN defeats the range check below (both ``v < lo``
        if math.isnan(v) or math.isinf(v):
            return f"must be a finite number, got {v}"
        if v < lo or v > hi:
            # include the actual value, floats are non-PII.
            return f"must be in [{lo}, {hi}], got {v}"
        return None

    return _validate


def _make_optional_float_validator(*, lo: float, hi: float) -> ValidatorFn:
    # Mirrors :func:`_make_optional_int_validator` for Optional[float]
    inner = _make_float_validator(lo=lo, hi=hi)

    def _validate(v: object) -> str | None:
        if v is None:
            return None
        return inner(v)

    return _validate


def _make_enum_validator(allowed: frozenset[str]) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if v not in allowed:
            # include the actual value via ``{v!r}`` so the
            return f"must be one of {sorted(allowed)}, got {v!r}"
        return None

    return _validate


def _make_custom_theme_validator() -> ValidatorFn:
    """``IPC_CONFIG_ALLOWLIST["custom_theme"]`` entry is also widened to"""
    key_keys = {"--background", "--foreground", "--primary", "--bg-subtle", "--border", "--text-muted"}

    def _validate(v: object) -> str | None:
        # ``None`` is the canonical "clear custom theme" value —
        if v is None:
            return None
        if not isinstance(v, dict):
            # include the actual type (mirror line 128's pattern).
            return f"must be a dict, got {type(v).__name__}"
        # cap the top-level dict size to prevent a malicious
        if len(v) > 64:
            return "too many top-level keys"
        for mode in ("light", "dark"):
            mode_dict = v.get(mode)
            if not isinstance(mode_dict, dict):
                return f"field {mode!r} must be a dict"
            # bound the per-mode dict size.  The legitimate
            if len(mode_dict) > 64:
                return f"{mode} has too many keys"
            for key in key_keys:
                val = mode_dict.get(key)
                if not isinstance(val, str):
                    return f"{mode}.{key} must be a string, got {type(val).__name__}"
                # bound the color value length.  Legitimate
                if len(val) > 32:
                    return f"{mode}.{key} color value too long"
                if not val.startswith("#"):
                    return f"{mode}.{key} must be a hex colour (#rrggbb)"
                # Basic hex validation: # followed by 6 hex digits, optionally 8 for alpha
                hex_part = val[1:]
                if len(hex_part) not in (6, 8):
                    return f"{mode}.{key} must be 6 or 8 hex digits (got {len(hex_part)})"
                try:
                    int(hex_part, 16)
                except ValueError:
                    return f"{mode}.{key} is not a valid hex colour"
        return None

    return _validate


# Canonical enums + bool keys for the linux_window_buttons validator below.
_LINUX_WINDOW_BUTTON_MODES = frozenset({"system", "custom"})
_LINUX_WINDOW_BUTTON_SIDES = frozenset({"left", "right"})
_LINUX_WINDOW_BUTTON_BOOL_KEYS = ("show_minimize", "show_maximize", "show_close")


def _make_linux_window_buttons_validator() -> ValidatorFn:
    """Validate the Linux window-button customization dict."""

    def _validate(v: object) -> str | None:
        if not isinstance(v, dict):
            return f"must be a dict, got {type(v).__name__}"
        # The legitimate shape is exactly 5 keys; 8 is a generous bound
        if len(v) > 8:
            return "too many keys"
        allowed_keys = {"mode", "side", *_LINUX_WINDOW_BUTTON_BOOL_KEYS}
        unknown = set(v) - allowed_keys
        if unknown:
            return f"unknown keys: {sorted(unknown)}"
        mode = v.get("mode")
        if mode not in _LINUX_WINDOW_BUTTON_MODES:
            return f"mode must be one of {sorted(_LINUX_WINDOW_BUTTON_MODES)}, got {mode!r}"
        side = v.get("side")
        if side not in _LINUX_WINDOW_BUTTON_SIDES:
            return f"side must be one of {sorted(_LINUX_WINDOW_BUTTON_SIDES)}, got {side!r}"
        for key in _LINUX_WINDOW_BUTTON_BOOL_KEYS:
            val = v.get(key)
            if not isinstance(val, bool):
                return f"{key} must be a bool, got {type(val).__name__}"
        return None

    return _validate


def _make_url_validator(
    *,
    allow_empty: bool = False,
    max_len: int = _MAX_STRING_LEN,
    require_https: bool = True,
) -> ValidatorFn:
    """Validate an HTTPs URL."""

    _loopback_hosts = frozenset({"localhost", "127.0.0.1", "::1"})

    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        # Strip leading/trailing whitespace BEFORE any further
        stripped = v.strip()
        if stripped != v:
            v = stripped
        if len(v) > max_len:
            # include actual length (URL fields can hold API keys
            return f"exceeds maximum length {max_len}, got length {len(v)}"
        if v == "":
            if allow_empty:
                return None
            return "must not be empty"
        # Scan for C0 / DEL / C1 control characters BEFORE
        for ch in v:
            o = ord(ch)
            if o < 0x20 or 0x7F <= o <= 0x9F:
                return f"contains control character (ord={o})"
        try:
            parsed = urlparse(v)
        except (ValueError, TypeError) as e:
            return f"is not a valid URL: {e}"
        if parsed.scheme not in ("http", "https"):
            return f"must use http or https scheme (got {parsed.scheme!r})"
        host = (parsed.hostname or "").lower()
        if not host:
            return "must include a network location (host)"
        # SECRET-1 (MED-M): reject URLs with embedded credentials.
        if parsed.username or parsed.password:
            return "URL must not contain embedded credentials. Use the api_key field"
        # close the defense-in-depth gap, reject cleartext
        if require_https and parsed.scheme == "http" and host not in _loopback_hosts:
            return f"must use HTTPS for non-loopback host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1)"
        return None

    return _validate


def _validate_trusted_extra_hosts(value: object) -> str | None:
    """Validate the ``trusted_extra_hosts`` config field."""
    if not isinstance(value, list):
        return "trusted_extra_hosts must be a list of hostnames"
    seen = set()
    for entry in value:
        if not isinstance(entry, str):
            return "trusted_extra_hosts entries must be strings"
        # Reject scheme/path/whitespace on the RAW value first
        if not entry.strip() or "://" in entry or "/" in entry or " " in entry:
            return (
                f"trusted_extra_hosts entry {entry!r} must be a bare hostname "
                f"(no scheme, path, or spaces): e.g. 'my-vllm.lan'"
            )
        raw = entry.strip()
        # Colon-bearing entries MUST be genuine IPv6 literals, a bare
        if ":" in raw:
            # Bracketed IPv6 (with optional ``:port``): ``[fc00::1]`` or
            if raw.startswith("["):
                closing = raw.find("]")
                if closing <= 0:
                    return f"trusted_extra_hosts entry {entry!r} is not a valid IPv6 literal"
                inner = raw[1:closing]
                try:
                    ipaddress.ip_address(inner)
                except ValueError:
                    return f"trusted_extra_hosts entry {entry!r} is not a valid IPv6 literal"
                host = inner
            elif raw.count(":") > 1:
                # Bare IPv6 literal: ``fc00::1``.
                try:
                    ipaddress.ip_address(raw)
                except ValueError:
                    return f"trusted_extra_hosts entry {entry!r} is not a valid IPv6 literal"
                host = raw
            else:
                # Single-colon, not bracketed, not valid IPv6, a
                return f"trusted_extra_hosts entry {entry!r} contains invalid characters"
        else:
            # Generic hostname / IPv4: strip the ``:port`` (none present).
            host = raw
        host = host.strip().lower()
        if not host:
            return f"trusted_extra_hosts entry {entry!r} is empty after normalization"
        if not all(c.isalnum() or c in "-._" or c == ":" for c in host):
            return f"trusted_extra_hosts entry {entry!r} contains invalid characters"
        if host in seen:
            return f"trusted_extra_hosts contains duplicate entry {entry!r}"
        seen.add(host)
    return None


__all__ = [
    # Type aliases
    "ValidatorFn",
    "FieldSpec",
    # Predicate helpers
    "_is_str",
    "_is_int_not_bool",
    "_is_float_or_int_not_bool",
    # Length caps and error templates
    "_MAX_STRING_LEN",
    "_MAX_API_KEY_LEN",
    "_ERR_MUST_BE_STRING",
    "_ERR_EXCEEDS_MAX_LEN",
    "_ERR_CONTROL_CHAR",
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
    # Standalone validators
    "_validate_trusted_extra_hosts",
]
