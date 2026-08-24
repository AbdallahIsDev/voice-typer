"""Scalar field validators: type, length, range, enum, URL, and theme.

Extracted from the original monolithic ``config_validators.py`` (package
split).  Every function here is *pure*: it takes a value (or, for
the factories, a spec like ``(lo, hi)``) and returns either ``None``
(success) or a human-readable error string.

The submodule is import-safe: it does **not** import from
:mod:`voice_typer.server.config`, so it cannot participate in a circular
import.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Callable
from typing import TypeGuard
from urllib.parse import urlparse

# Type helpers ──────────────────────────────────────────────────────────────
#
# A field validator returns ``None`` on success or a human-readable error
# string describing why the value is rejected.  ``expected_type`` is the
# concrete Python type the value must be an instance of — note that for
# bool fields we set ``expected_type=bool`` and rely on the fact that
# ``isinstance(True, int)`` is True but ``isinstance(1, bool)`` is False,
# so the int-vs-bool ambiguity is resolved by checking bool first in the
# dispatcher (see ``_validate_config_update``).

ValidatorFn = Callable[[object], str | None]
# widened to accept either a single ``type`` (e.g. ``str``, ``bool``)
# or a tuple of types (used by Optional fields such as ``microphone`` whose
# spec is ``(str, type(None))``). The previous ``tuple[type, ValidatorFn]``
# alias was too narrow and forced the bare ``dict`` annotation on
# ``IPC_CONFIG_ALLOWLIST``.
FieldSpec = tuple[type | tuple[type, ...], ValidatorFn]


def _is_str(v: object) -> TypeGuard[str]:
    return isinstance(v, str)


def _is_int_not_bool(v: object) -> TypeGuard[int]:
    # bool is a subclass of int in Python; reject it explicitly so that
    # ``max_recording_time_seconds=True`` doesn't silently become 1.
    return isinstance(v, int) and not isinstance(v, bool)


def _is_float_or_int_not_bool(v: object) -> TypeGuard[float]:
    # Accept ints on the numeric tower (they're valid floats), but still
    # reject bool.  This matches the dataclass field type ``float`` while
    # being friendly to JSON, which has no int/float distinction.
    return isinstance(v, int | float) and not isinstance(v, bool)


# Sane upper bound for any single string field.  API keys, URLs, hotkey
# strings and language codes are all well under this; anything bigger is
# either a bug or an attack.
_MAX_STRING_LEN = 8192

# API keys can be longer than typical strings (some Bearer tokens exceed
# 4 KB), so they get their own cap.
_MAX_API_KEY_LEN = 16384

# Shared error-message templates for the string validator family.
# Centralising them as module-level constants means
# :func:`_make_str_validator` and :func:`_make_optional_str_validator`
# cannot drift apart on wording (the optional variant previously had
# its own near-identical copy of every message). Both functions now
# format these templates with the same field values.
_ERR_MUST_BE_STRING = "must be a string, got {type_name}"
_ERR_EXCEEDS_MAX_LEN = "exceeds maximum length {max_len}, got length {actual_len}"
_ERR_CONTROL_CHAR = "contains control character (ord={ord})"


def _make_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return _ERR_MUST_BE_STRING.format(type_name=type(v).__name__)
        if len(v) > max_len:
            # include the actual length so the operator can see how
            # badly the cap was blown (e.g. a 50 KB hotkey string vs. one
            # that's 1 char over). Don't include the value itself —
            # string fields can hold API keys / PII.
            return _ERR_EXCEEDS_MAX_LEN.format(max_len=max_len, actual_len=len(v))
        # Reject C0 control characters (0x00-0x1F), DEL (0x7F), AND C1
        # control characters (0x80-0x9F). C1 escapes such as CSI (0x9B)
        # and OSC (0x9D) can reprogram a terminal, poison logs, and
        # corrupt crash dumps — same threat model as C0.
        for ch in v:
            o = ord(ch)
            if o < 0x20 or 0x7F <= o <= 0x9F:
                return _ERR_CONTROL_CHAR.format(ord=o)
        return None

    return _validate


def _make_optional_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    # Deduplicated: a None value short-circuits to success, and every
    # other case is delegated to ``_make_str_validator`` so the two
    # validators cannot drift apart on length / control-char / type
    # checks. Pre-refactor, this function was a 15-line near-copy of
    # ``_make_str_validator`` with its own (slightly different) error
    # strings — the only behavioural delta was accepting ``None``.
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
            # include the actual value — ints are non-PII and the
            # value is essential for diagnosing off-by-one / unit bugs.
            return f"must be in [{lo}, {hi}], got {v}"
        return None

    return _validate


def _make_optional_int_validator(*, lo: int, hi: int) -> ValidatorFn:
    # Mirrors :func:`_make_optional_str_validator`: short-circuit
    # ``None`` to success and delegate every other case to
    # :func:`_make_int_validator` so the two paths cannot drift on
    # range / type-error wording. Used by Optional[int] dataclass
    # fields like ``bubble_x`` / ``bubble_y`` / ``test_duration_seconds``
    # whose ``None`` sentinel means "not set — use the renderer default".
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
        # and ``v > hi`` are False for NaN), and ``json.loads`` accepts
        # ``NaN`` / ``Infinity`` as a non-standard extension, so a
        # hand-edited ``config.json`` could otherwise sneak NaN into a
        # float field and silently disable downstream comparisons.
        # ``math.isinf`` covers both +inf and -inf. Reject both before
        # the range check fires.
        if math.isnan(v) or math.isinf(v):
            return f"must be a finite number, got {v}"
        if v < lo or v > hi:
            # include the actual value — floats are non-PII.
            return f"must be in [{lo}, {hi}], got {v}"
        return None

    return _validate


def _make_optional_float_validator(*, lo: float, hi: float) -> ValidatorFn:
    # Mirrors :func:`_make_optional_int_validator` for Optional[float]
    # dataclass fields like ``bubble_scale``.
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
            # operator can see exactly what was rejected (including any
            # whitespace / case mismatch). Enum values are short option
            # strings, not PII.
            return f"must be one of {sorted(allowed)}, got {v!r}"
        return None

    return _validate


def _make_custom_theme_validator() -> ValidatorFn:
    """Validate a custom-theme dict: {light: {var: val, ...}, dark: {var: val, ...}}.

    ``None`` is now accepted as a valid value — the renderer's
        ``useTheme.ts`` sends ``custom_theme: null`` when the user clicks
        "Clear custom theme / revert to preset". Previously the validator
        rejected ``None`` with ``"must be a dict, got NoneType"`` and the
        user's clear action silently failed (server returned
        ``code: "invalid_field"`` while the local React state still held
        the cleared theme). The expected_type tuple on the
        ``IPC_CONFIG_ALLOWLIST["custom_theme"]`` entry is also widened to
        ``(dict, type(None))`` so the pre-validator type check passes for
        ``None`` before this validator runs.
    """
    key_keys = {"--background", "--foreground", "--primary", "--bg-subtle", "--border", "--text-muted"}

    def _validate(v: object) -> str | None:
        # ``None`` is the canonical "clear custom theme" value —
        # accept it without further checks. Config.load() / Config.save()
        # treat None as "the field is unset / revert to preset" (the
        # dataclass default for ``custom_theme`` is None).
        if v is None:
            return None
        if not isinstance(v, dict):
            # include the actual type (mirror line 128's pattern).
            # The value itself could be a long string, so we use type
            # name rather than the value.
            return f"must be a dict, got {type(v).__name__}"
        # cap the top-level dict size to prevent a malicious
        # or buggy config from sending a 10000-key theme dict (the
        # legitimate shape is exactly 2 keys: "light" and "dark").  64
        # is a generous upper bound that catches attacks without
        # rejecting any plausible hand-written theme.
        if len(v) > 64:
            return "too many top-level keys"
        for mode in ("light", "dark"):
            mode_dict = v.get(mode)
            if not isinstance(mode_dict, dict):
                return f"field {mode!r} must be a dict"
            # bound the per-mode dict size.  The legitimate
            # shape has 6 required CSS-variable keys; 64 leaves room
            # for future extensions while still rejecting pathological
            # inputs.
            if len(mode_dict) > 64:
                return f"{mode} has too many keys"
            for key in key_keys:
                val = mode_dict.get(key)
                if not isinstance(val, str):
                    return f"{mode}.{key} must be a string, got {type(val).__name__}"
                # bound the color value length.  Legitimate
                # hex colors are 7 chars (#RRGGBB) or 9 chars
                # (#RRGGBBAA); 32 is a generous upper bound that
                # catches malicious 1000-char strings without
                # rejecting anything legit.
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
    """Validate the Linux window-button customization dict.

    Expected shape (matches the ``linux_window_buttons`` dataclass default
    in ``config/_schema.py`` and the renderer's ``LinuxWindowButtonsConfig``):

        {"mode": "system" | "custom",
         "side": "left" | "right",
         "show_minimize": bool,
         "show_maximize": bool,
         "show_close": bool}

    All five keys are REQUIRED — the renderer always sends the complete
    object (it edits a full draft, never a partial patch), and requiring
    every key keeps a stale/partial write from silently half-configuring
    the title bar. Unknown extra keys are rejected so the shape cannot
    silently drift.
    """

    def _validate(v: object) -> str | None:
        if not isinstance(v, dict):
            return f"must be a dict, got {type(v).__name__}"
        # The legitimate shape is exactly 5 keys; 8 is a generous bound
        # that still rejects pathological 1000-key payloads.
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
    """Validate an HTTPs URL.

        Rejects non-string values, oversized values, and any URL whose scheme
        is not ``http`` or ``https``.  Empty string is accepted iff ``allow_empty``
        (used for fields where empty means "feature disabled").

    When ``require_https`` is True (default), non-loopback hosts
        must use HTTPS — HTTP is only permitted for loopback hosts
        (``localhost`` / ``127.0.0.1`` / ``::1``) so local development servers
        work.  This mirrors the request-time enforcement in
        ``voice_typer.server._secrets.require_https`` so a cleartext URL is
        rejected at ``set_config`` time, before it can ever reach config.

        SECRET-1 (MED-M): URLs with embedded credentials (``user:pass@host``)
        are rejected outright — the user must use the dedicated ``api_key``
        field instead.  Embedded credentials in URLs are a security
        anti-pattern: they end up in process lists (``ps aux``), shell
        history, log files, and browser history.  They also bypass the
        keyring-backed credential store (``credential_store``) which is the
        application's single source of truth for secrets.
    """

    _loopback_hosts = frozenset({"localhost", "127.0.0.1", "::1"})

    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        # Strip leading/trailing whitespace BEFORE any further
        # checks. A pasted URL like ``" https://api.openai.com "`` would
        # otherwise be rejected with a misleading
        # ``"must use http or https scheme (got '')"`` error because
        # ``urlparse`` sees an empty scheme on the leading-space value.
        # Mutate the local ``v`` so all downstream checks (length, empty,
        # urlparse, scheme, host) operate on the cleaned value.
        stripped = v.strip()
        if stripped != v:
            v = stripped
        if len(v) > max_len:
            # include actual length (URL fields can hold API keys
            # via query strings, so don't include the value itself).
            return f"exceeds maximum length {max_len}, got length {len(v)}"
        if v == "":
            if allow_empty:
                return None
            return "must not be empty"
        # Scan for C0 / DEL / C1 control characters BEFORE
        # ``urlparse`` runs. Mirrors the check in ``_make_str_validator``
        # so URL fields cannot smuggle C1 escapes (CSI=0x9B, OSC=0x9D)
        # past the string validator just because they happen to be
        # URL-shaped. C1 escapes can reprogram a terminal and poison
        # logs / crash dumps.
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
        # ``urlparse`` exposes ``username`` and ``password`` as separate
        # attributes; if either is non-empty, the URL has an authority
        # section of the form ``user:pass@host``.  Such URLs leak the
        # credentials into process listings, log files, and crash dumps,
        # and they bypass the credential_store (which is the canonical
        # place for API keys).  Reject the URL and point the user at the
        # dedicated api_key field.
        if parsed.username or parsed.password:
            return "URL must not contain embedded credentials — use the api_key field"
        # close the defense-in-depth gap — reject cleartext
        # HTTP for non-loopback hosts at config time, not just at call time.
        if require_https and parsed.scheme == "http" and host not in _loopback_hosts:
            return f"must use HTTPS for non-loopback host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1)"
        return None

    return _validate


def _validate_trusted_extra_hosts(value: object) -> str | None:
    """Validate the ``trusted_extra_hosts`` config field.

    Accepts a list of hostname strings (with or without a port — the
    port is stripped at allowlist-application time). Each entry must be
    a non-empty bare hostname: letters/digits/hyphens/dots only, no
    scheme (``https://``), no path, no spaces. Mirrors the normalization
    in ``_secrets.extend_url_allowlist`` (lowercase, port stripped)
    without resolving DNS (the SSRF IP-literal blocklist in
    ``_secrets._is_private_ip`` still applies at assert time).
    """
    if not isinstance(value, list):
        return "trusted_extra_hosts must be a list of hostnames"
    seen = set()
    for entry in value:
        if not isinstance(entry, str):
            return "trusted_extra_hosts entries must be strings"
        # Reject scheme/path/whitespace on the RAW value first
        # (``https://my-vllm.lan`` must be rejected before port-splitting
        # reduces it to a bare ``https``).
        if not entry.strip() or "://" in entry or "/" in entry or " " in entry:
            return (
                f"trusted_extra_hosts entry {entry!r} must be a bare hostname "
                f"(no scheme, path, or spaces) — e.g. 'my-vllm.lan'"
            )
        raw = entry.strip()
        # Colon-bearing entries MUST be genuine IPv6 literals — a bare
        # hostname containing ``:`` is invalid (IPv6 is the only legal
        # colon-bearing host form). Accept bracketed ``[fc00::1]:8080``
        # and bare ``fc00::1``.
        if ":" in raw:
            # Bracketed IPv6 (with optional ``:port``): ``[fc00::1]`` or
            # ``[fc00::1]:8080`` → inner must be a valid IPv6 literal.
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
                # Single-colon, not bracketed, not valid IPv6 — a
                # hostname with a stray colon is invalid.
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
