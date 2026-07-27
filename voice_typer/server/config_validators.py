"""Pure input validators for IPC ``set_config`` payloads.

ARCH-REFAC-001: This module was extracted from ``config.py`` to keep the
config-loading code (parsing JSON, migrating legacy schemas, atomic
writes) separate from the input-validation logic that gates which fields
the Electron renderer is allowed to mutate and what values are
acceptable.

Every function here is *pure*: it takes a value (or, for the factories,
a spec like ``(lo, hi)``) and returns either ``None`` (success) or a
human-readable error string.  The only side effect in the whole module
is a single ``log.debug`` call inside :func:`validate_config_update`
when an unknown field is silently dropped — matching the original
behaviour in ``config.py``.

The module is import-safe: it does **not** import from
:mod:`voice_typer.server.config`, so it cannot participate in a circular
import.  ``config.py`` imports from this module (for
``ALLOWED_USER_MODELS``) and re-exports everything else via a wildcard
``from .config_validators import *`` at the bottom of ``config.py`` for
backward compatibility.
"""

import contextlib
import json as _json
import logging
import sys as _sys
from collections.abc import Callable
from pathlib import Path as _Path
from typing import TypeGuard
from urllib.parse import urlparse

log = logging.getLogger("voice_typer.server.config_validators")


# CR-38: extended to include the multilingual variants (tiny/small/medium,
# no .en suffix) that OnboardingController.MODEL_OPTIONS offers to users.
# Without these, non-English users who pick a multilingual model in
# onboarding silently get English-only Whisper after the first restart
# (Config.load() resets model_size to "small.en" because the multilingual
# name is not in the allowlist). large-v3 is intentionally NOT included
# because the existing test_load_normalizes_legacy_or_unsupported_model_to_small_en
# regression test pins it to normalize to "small.en" (legacy/unsupported).
ALLOWED_USER_MODELS: frozenset[str] = frozenset(
    {
        "tiny.en",
        "small.en",
        "medium.en",  # English-only Whisper
        "tiny",
        "small",
        "medium",  # Multilingual Whisper (CR-38)
        "qwen",
        "parakeet",  # Non-Whisper backends
    }
)


# ──────────────────────────────────────────────────────────────────────────
# G4-M-12: canonical noise-suppression backend enum.
#
# Previously this enum was duplicated in three places that had already
# drifted out of sync:
#   - ``config.py:911`` dataclass field comment advertised
#     ``"rnnoise" | "deepfilternet" | "speex" | "none"`` (``"speex"`` was
#     never implemented — there is no speex backend in
#     ``audio_filters/noise_suppressor.py``).
#   - ``config_validators.py:768`` IPC validator used
#     ``{"rnnoise", "deepfilternet", "none"}`` (correct set, but inlined
#     as a literal — easy to drift).
#   - ``audio_filters/noise_suppressor.py`` runtime fallback only
#     dispatched on ``"rnnoise"`` / ``"deepfilternet"`` / ``"none"``
#     (matches the IPC validator but not the dataclass comment).
#
# The canonical set is now defined ONCE here and re-exported via the
# wildcard ``from .config_validators import *`` in ``config.py``.
# ``audio_filters/noise_suppressor.py`` imports the constant directly
# (its agent — 2-g — is coordinated to swap its inlined literal for
# the imported constant and to drop the ``"speex"`` mention from its
# docstring). ``config.py`` agent 2-a is coordinated to drop
# ``"speex"`` from the dataclass comment at line 911.
#
# Use ``frozenset`` so callers can't accidentally mutate the canonical
# enum (an ``in`` check is the only supported operation).
# ──────────────────────────────────────────────────────────────────────────
NOISE_SUPPRESSION_METHODS: frozenset[str] = frozenset({"rnnoise", "deepfilternet", "none"})


# ──────────────────────────────────────────────────────────────────────────
# SEC-002: IPC `set_config` allowlist
#
# The IPC `set_config` command previously used `hasattr(config, k) +
# setattr(config, k, v)`, which accepted *any* Config field.  That let a
# loopback IPC caller swap `llm_api_url`, `cloud_api_url`,
# `openai_api_key`, etc., enabling data exfiltration and unauthorized
# use of paid API keys.
#
# `IPC_CONFIG_ALLOWLIST` is the explicit, reviewed list of fields the
# Electron renderer is permitted to mutate via `set_config`, together
# with per-field validators.  Anything not in this map is silently
# dropped (preserving the existing "unknown field" contract from
# `test_ignores_unknown_fields_without_crashing`).
#
# Fields deliberately excluded:
#   - `schema_version`           — managed by Config.load() migration path
#   - `wayland_warned`           — internal UX state, not user-tunable
#   - `onboarding_completed`     — set via the dedicated `complete_onboarding`
#                                   IPC command, not `set_config`
#   - `qwen_model_path`          — trusted-path, set by model download flow
#   - `parakeet_model_path`      — trusted-path, set by model download flow
#   - `corrections_path`         — trusted-path, set by file picker IPC
#
# When adding a field here, also add a test in
# `tests/test_server.py::TestDispatchSetConfigAllowlist`.
# ──────────────────────────────────────────────────────────────────────────


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
# GT-D1-6: widened to accept either a single ``type`` (e.g. ``str``, ``bool``)
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
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Sane upper bound for any single string field.  API keys, URLs, hotkey
# strings and language codes are all well under this; anything bigger is
# either a bug or an attack.
_MAX_STRING_LEN = 8192

# API keys can be longer than typical strings (some Bearer tokens exceed
# 4 KB), so they get their own cap.
_MAX_API_KEY_LEN = 16384


def _make_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if len(v) > max_len:
            # PVT-G5-071 (session-5): include the actual length so the
            # operator can see how badly the cap was blown (e.g. a 50 KB
            # hotkey string vs. one that's 1 char over). Don't include
            # the value itself — string fields can hold API keys / PII.
            return f"exceeds maximum length {max_len}, got length {len(v)}"
        # CR-26: Reject C0 control characters and DEL (0x7f) to prevent
        # log poisoning, header injection, and config.json truncation.
        for ch in v:
            o = ord(ch)
            if o < 0x20 or o == 0x7F:
                return f"contains control character (ord={o})"
        return None

    return _validate


def _make_optional_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if v is None:
            return None
        if not _is_str(v):
            return f"must be a string or null, got {type(v).__name__}"
        if len(v) > max_len:
            # PVT-G5-071 (session-5): include actual length (see
            # _make_str_validator for the rationale on why we don't
            # include the value).
            return f"exceeds maximum length {max_len}, got length {len(v)}"
        # CR-26: Reject C0 control characters and DEL (0x7f).
        for ch in v:
            o = ord(ch)
            if o < 0x20 or o == 0x7F:
                return f"contains control character (ord={o})"
        return None

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
            # PVT-G5-071 (session-5): include the actual value — ints
            # are non-PII and the value is essential for diagnosing
            # off-by-one / unit bugs.
            return f"must be in [{lo}, {hi}], got {v}"
        return None

    return _validate


def _make_float_validator(*, lo: float, hi: float) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_float_or_int_not_bool(v):
            return f"must be a number, got {type(v).__name__}"
        if v < lo or v > hi:
            # PVT-G5-071 (session-5): include the actual value — floats
            # are non-PII.
            return f"must be in [{lo}, {hi}], got {v}"
        return None

    return _validate


def _make_enum_validator(allowed: frozenset[str]) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if v not in allowed:
            # PVT-G5-071 (session-5): include the actual value via
            # ``{v!r}`` so the operator can see exactly what was
            # rejected (including any whitespace / case mismatch).
            # Enum values are short option strings, not PII.
            return f"must be one of {sorted(allowed)}, got {v!r}"
        return None

    return _validate


def _make_custom_theme_validator() -> ValidatorFn:
    """Validate a custom-theme dict: {light: {var: val, ...}, dark: {var: val, ...}}."""
    key_keys = {"--background", "--foreground", "--primary", "--bg-subtle", "--border", "--text-muted"}

    def _validate(v: object) -> str | None:
        if not isinstance(v, dict):
            # PVT-G5-071 (session-5): include the actual type (mirror
            # line 128's pattern). The value itself could be a long
            # string, so we use type name rather than the value.
            return f"must be a dict, got {type(v).__name__}"
        # XZ-14-15: cap the top-level dict size to prevent a malicious
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
            # XZ-14-15: bound the per-mode dict size.  The legitimate
            # shape has 6 required CSS-variable keys; 64 leaves room
            # for future extensions while still rejecting pathological
            # inputs.
            if len(mode_dict) > 64:
                return f"{mode} has too many keys"
            for key in key_keys:
                val = mode_dict.get(key)
                if not isinstance(val, str):
                    return f"{mode}.{key} must be a string, got {type(val).__name__}"
                # XZ-14-15: bound the color value length.  Legitimate
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


def _make_url_validator(
    *,
    allow_empty: bool = False,
    max_len: int = _MAX_STRING_LEN,
    require_https: bool = True,
) -> ValidatorFn:
    """Validate an HTTP(S) URL.

    Rejects non-string values, oversized values, and any URL whose scheme
    is not ``http`` or ``https``.  Empty string is accepted iff ``allow_empty``
    (used for fields where empty means "feature disabled").

    When ``require_https`` is True (default, NEW-SEC-003), non-loopback hosts
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
        if len(v) > max_len:
            # PVT-G5-071 (session-5): include actual length (URL fields
            # can hold API keys via query strings, so don't include the
            # value itself).
            return f"exceeds maximum length {max_len}, got length {len(v)}"
        if v == "":
            if allow_empty:
                return None
            return "must not be empty"
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
        # NEW-SEC-003: close the defense-in-depth gap — reject cleartext
        # HTTP for non-loopback hosts at config time, not just at call time.
        if require_https and parsed.scheme == "http" and host not in _loopback_hosts:
            return f"must use HTTPS for non-loopback host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1)"
        return None

    return _validate


# Validator combinations ────────────────────────────────────────────────────

# HOTKEY-VALIDATION-001: OS-reserved shortcuts that must never be assigned
# as global hotkeys.  This is the backend mirror of the frontend
# ``RESERVED_SHORTCUTS`` table in
# ``voice_typer/client/src/renderer/src/components/hotkey-validation.ts``.
#
# HOTKEY-SHARED-001 (Task 1.4): the reserved-shortcut tables are now loaded
# from a single canonical JSON file at
# ``voice_typer/server/hotkey_reserved.json``. Both the frontend (via Vite
# JSON import) and the backend (via ``json.load``) consume the SAME file,
# eliminating the "MUST be kept in sync" duplication problem. A CI test
# (``tests/test_hotkey_reserved_sync.py``) verifies the two in-memory
# structures are byte-identical.
#
# In addition to the per-platform explicit denylist, ``_validate_hotkey``
# applies the following blanket rules (mirroring the frontend):
#   - Win+* and Super+* are blocked on Windows and Linux respectively
#     (system-wide shell shortcuts).
#   - Alt+Tab, Alt+F4, Alt+Esc, Alt+Space are blocked on every platform
#     (window management).
#   - Alt+Shift is blocked on Windows (language switching).
#   - Ctrl+<common-letter> (c, v, x, z, a, s, y, w, f, p, n, o, t, l, r,
#     h, j, k, b, i, u, d, e, g, m, q) is blocked (Copy/Paste/Undo/Save/
#     etc.).  Ctrl+<F-key> and Ctrl+<special-key> are allowed.
#   - Shift+<letter> is blocked (interferes with capitalization).
#     Shift+<F-key> and Shift+<special-key> are allowed.
#
# All other combinations (including Alt+<letter>) are allowed by default.
# This is a denylist design, not a blanket rule design.

# HOTKEY-SHARED-001: load the canonical reserved-shortcut table from the
# JSON file. The file lives next to this module so the relative path is
# stable regardless of the working directory.
_RESERVED_DATA_PATH = _Path(__file__).resolve().parent / "hotkey_reserved.json"


def _load_reserved_data() -> dict:
    """Load and cache the reserved-hotkey JSON config.

    Returns a dict with keys:
        - ``universal_reserved``: list[str]
        - ``per_platform_reserved``: dict[str, list[str]]
        - ``blocked_ctrl_letters``: list[str]
        - ``modifiers``: list[str]
    """
    with _RESERVED_DATA_PATH.open("r", encoding="utf-8") as f:
        return _json.load(f)


_RESERVED_DATA = _load_reserved_data()

# Per-platform reserved shortcuts. Stored in the SAME format as user input
# (angle brackets, lowercase) so we can compare directly with
# ``value.lower()``. Built from the JSON file at module init.
_RESERVED_HOTKEYS: dict[str, set[str]] = {
    platform: set(entries) for platform, entries in _RESERVED_DATA["per_platform_reserved"].items()
}

# Universal window-management shortcuts blocked on EVERY platform.
# Alt+Tab, Alt+F4, Alt+Esc, Alt+Space are OS-level window management
# on Windows, macOS (with Alt=Option), and most Linux desktops.
# Stored in the SAME format as user input (angle brackets, lowercase)
# so we can compare directly with ``value.lower()``.
_UNIVERSAL_RESERVED_HOTKEYS = frozenset(_RESERVED_DATA["universal_reserved"])

# Common Ctrl+<letter> shortcuts that are universally expected by users
# (Copy, Paste, Undo, Save, Select All, etc.).  Mirrors the frontend
# behavior.  These are blocked regardless of platform.
_BLOCKED_CTRL_LETTERS = frozenset(_RESERVED_DATA["blocked_ctrl_letters"])

# Modifier keys recognized in the hotkey string (pynput-style, lowercase).
_HOTKEY_MODIFIERS = frozenset(_RESERVED_DATA["modifiers"])


def _platform_key() -> str:
    """Return the platform key for ``_RESERVED_HOTKEYS`` lookup."""
    if _sys.platform == "win32":
        return "win32"
    if _sys.platform == "darwin":
        return "darwin"
    return "linux"


def _parse_hotkey_parts(hotkey: str) -> list[str]:
    """Parse a hotkey string like ``"<ctrl>+<alt>+v"`` into ``["ctrl","alt","v"]``.

    RW-1 (Hotkey parser unification): this now delegates to the
    canonical :func:`voice_typer.server.hotkey_spec.parse_hotkey` and
    flattens the resulting :class:`HotkeySpec` (canonical modifiers
    followed by non-modifier keys) back into a flat list of
    lowercased tokens, preserving the original list-returning API.

    Behavioural changes versus the prior strip-and-split implementation:

    - Aliases are resolved (e.g. ``<control>`` → ``"ctrl"``,
      ``<globe>`` → ``"fn"``, ``<altgr>`` → ``"alt_gr"``).
    - Duplicate tokens are deduplicated (e.g. ``<ctrl>+<ctrl>+<v>``
      → ``["ctrl", "v"]``).
    - Modifiers are sorted alphabetically; non-modifier keys keep
      their original order.

    These changes are safe for the validator's consumers, which only
    use ``len(parts)``, ``parts[0]``, ``any(p in ... for p in parts)``,
    and ``[p for p in parts if p (not) in _HOTKEY_MODIFIERS]`` — all
    of which are insensitive to ordering, dedup, and alias resolution
    (every canonical modifier name is in ``_HOTKEY_MODIFIERS``).
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    spec = parse_hotkey(hotkey)
    if spec.is_empty:
        return []
    return list(spec.modifiers) + list(spec.keys)


def _check_basic_shape(value: object) -> str | None:
    """Stage 1: type / length / emptiness guards (shared by all hotkeys)."""
    if not isinstance(value, str):
        return f"must be a string, got {type(value).__name__}"
    if len(value) > 256:
        return "exceeds maximum length 256"
    if not value.strip():
        return "must not be empty"
    return None


def _check_universal_reserved(normalized: str) -> str | None:
    """Stage 2: OS / common-app shortcuts blocked on EVERY platform.

    Includes window-management shortcuts (Alt+Tab/F4/Esc/Space) and
    Enter-based combos (Enter, Ctrl+Enter, Shift+Enter) which interfere
    with typing, form submission, and messaging shortcuts.
    """
    if normalized in _UNIVERSAL_RESERVED_HOTKEYS:
        return "reserved — conflicts with operating system or common app shortcuts"
    return None


def _check_platform_reserved(normalized: str, platform: str) -> str | None:
    """Stage 3: per-platform OS-reserved shortcuts.

    On Linux the physical Windows key is reported as ``super`` by
    pynput / evdev, but a user (or a buggy renderer) may send ``<win>``
    instead. ``<win>`` and ``<super>`` are distinct tokens in the
    canonical parser (see ``hotkey_spec.py``), so an exact string match
    against the Linux reserved list (which uses ``<super>+<key>``)
    silently lets ``<win>+<l>`` through even though ``<super>+<l>`` is
    reserved (lock screen). Normalize ``<win>`` to ``<super>`` on Linux
    ONLY — Windows keeps its blanket Win+block (stage 5) and macOS
    doesn't use either name (its system modifier is ``cmd``).
    """
    reserved = _RESERVED_HOTKEYS.get(platform, set())
    if not reserved:
        return None
    lookup = normalized
    if platform == "linux":
        lookup = normalized.replace("<win>", "<super>")
    for r in reserved:
        if r == lookup:
            return f"reserved by operating system ({platform})"
    return None


def _check_single_alphanumeric(parts: list[str]) -> str | None:
    """Stage 4: reject a standalone single letter/digit (HOTKEY-VALIDATION-002).

    A standalone <a> would trigger dictation every time the user types
    'a'. Multi-key combos (Alt+Q, Ctrl+V) are NOT affected — they have
    2+ parts and are checked by the later stages.
    """
    if len(parts) == 1:
        sole = parts[0]
        if len(sole) == 1 and sole.isalnum():
            return f"single letters and digits can't be used as hotkeys — '{sole}' would interfere with typing"
    return None


def _check_multi_non_modifier(parts: list[str]) -> str | None:
    """Stage 5: reject combos with more than one non-modifier key.

    A global hotkey listener (pynput, the Windows low-level hook
    ``WH_KEYBOARD_LL``, the macOS ``CGEventTap``) registers a single
    non-modifier key plus zero-or-more modifiers. A combo like
    ``<a>+<b>`` would either fail to register, fire spuriously when
    either key is pressed alone, or require the user to press both keys
    simultaneously in a way that's indistinguishable from typing.

    This stage runs BEFORE the Ctrl+letter / Shift+letter blanket
    rules so a structurally invalid combo like ``<ctrl>+<a>+<b>``
    (which would otherwise match the Ctrl+A reserved-shortcut rule) is
    rejected with the structural error rather than the
    reserved-shortcut error.
    """
    non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
    if len(non_mods) > 1:
        return (
            f"hotkey has {len(non_mods)} non-modifier keys — "
            "at most one non-modifier key is supported (global hotkey "
            "listeners register a single non-modifier plus modifiers)"
        )
    return None


def _check_os_shell_combos(parts: list[str], platform: str) -> str | None:
    """Stage 6: Win+* / Super+* (Windows shell) and Cmd+<letter> (macOS).

    The Win/Super blanket block applies only on Windows (where the Win
    key is heavily reserved by the OS shell). On Linux, Super combos are
    deferred to the per-platform reserved list. Cmd+<letter> is blocked
    on macOS but Cmd+<F-key>/<special-key> are allowed.
    """
    has_win = any(p in ("win", "super") for p in parts)
    has_cmd = any(p in ("cmd", "cmd_l", "cmd_r") for p in parts)
    if has_win and platform == "win32":
        return "Windows key combinations are reserved by the OS shell"
    if has_cmd and platform == "darwin" and len(parts) > 1:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        for nm in non_mods:
            if len(nm) == 1 and nm.isalpha():
                return f"Cmd+{nm.upper()} is reserved by macOS / common apps"
    return None


def _check_alt_shift(parts: list[str], platform: str) -> str | None:
    """Stage 7: Alt+Shift block (Windows language switching)."""
    if platform == "win32":
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        has_alt = any(p.startswith("alt") for p in parts)
        has_shift = any(p.startswith("shift") for p in parts)
        if has_alt and has_shift and not non_mods:
            return "Alt+Shift is reserved by Windows for language switching"
    return None


def _check_ctrl_letter(parts: list[str]) -> str | None:
    """Stage 8: Ctrl+<common-letter> block (Copy/Paste/Undo/Save/etc.).

    Only applies to PURE Ctrl+<letter> — if another modifier is present
    (e.g. Ctrl+Alt+U), the combo is allowed because it doesn't conflict
    with the common app shortcuts.
    """
    has_ctrl = any(p.startswith("ctrl") for p in parts)
    if has_ctrl:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        modifiers_non_ctrl = [p for p in parts if p in _HOTKEY_MODIFIERS and not p.startswith("ctrl")]
        if not modifiers_non_ctrl:
            for nm in non_mods:
                if nm in _BLOCKED_CTRL_LETTERS:
                    return f"Ctrl+{nm.upper()} is a reserved application shortcut"
    return None


def _check_shift_letter(parts: list[str]) -> str | None:
    """Stage 9: Shift+<letter> block (interferes with capitalization).

    Only applies to PURE Shift+<letter> — if another modifier is present,
    the combo is allowed (e.g. Ctrl+Shift+Z = redo in many apps).
    """
    has_shift = any(p.startswith("shift") for p in parts)
    if has_shift:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        modifiers_non_shift = [p for p in parts if p in _HOTKEY_MODIFIERS and not p.startswith("shift")]
        if not modifiers_non_shift:
            for nm in non_mods:
                if len(nm) == 1 and (nm.isalpha() or nm.isdigit()):
                    return f"Shift+{nm.upper()} interferes with text capitalization or symbol input"
    return None


def _validate_hotkey(value: object) -> str | None:
    """Validate a hotkey string against the reserved-shortcut denylist.

    Returns ``None`` if valid, or a human-readable error string if invalid.
    Mirrors the frontend ``validateHotkey`` in ``hotkey-validation.ts``.

    HOTKEY-VALIDATION-001: this replaces the previous length-only check
    (``_make_str_validator(max_len=256)``) which accepted OS-reserved
    shortcuts like ``<alt>+<tab>`` and conflict-prone combos like
    ``<ctrl>+<c>``.

    ARCH-14: the 9 validation stages below are extracted into small
    ``_check_*`` helpers so the orchestrator stays readable. Each helper
    returns an error string or ``None``; the first non-``None`` wins,
    preserving the original short-circuit ordering exactly.
    """
    if (err := _check_basic_shape(value)) is not None:
        return err

    parts = _parse_hotkey_parts(value)
    if not parts:
        return "hotkey has no keys"

    # Strip leading/trailing whitespace BEFORE lowercasing so a padded
    # reserved hotkey like ``"  <alt>+<tab>  "`` matches the denylist
    # (the denylist entries are stored without padding). The parser
    # already strips whitespace between tokens, so internal whitespace
    # is unaffected.
    normalized = value.strip().lower()

    # Stages run in priority order; the first rejection wins.
    for check in (
        lambda: _check_universal_reserved(normalized),
        lambda: _check_platform_reserved(normalized, _platform_key()),
        lambda: _check_single_alphanumeric(parts),
        lambda: _check_multi_non_modifier(parts),
        lambda: _check_os_shell_combos(parts, _platform_key()),
        lambda: _check_alt_shift(parts, _platform_key()),
        lambda: _check_ctrl_letter(parts),
        lambda: _check_shift_letter(parts),
    ):
        if (err := check()) is not None:
            return err

    return None


# ──────────────────────────────────────────────────────────────────────────
# XZ-14-04 / XZ-14-05: cross-field hotkey conflict check and cross-platform
# portability warnings.
#
# The per-field ``_validate_hotkey`` (above) only consults the *current*
# platform's reserved list and only sees one hotkey field at a time.  These
# two helpers layer on top of it:
#
#   - :func:`_check_cross_field_hotkey_conflicts` (XZ-14-04): detects when
#     two of the three hotkey fields (``hotkey``, ``repaste_hotkey``,
#     ``push_to_talk_hotkey``) are assigned the same value.  Called from
#     both :func:`validate_config_update` and :func:`validate_config` so
#     the conflict is caught at IPC-write time AND at config-load time.
#
#   - :func:`cross_platform_hotkey_warnings` (XZ-14-05): checks each hotkey
#     value against the reserved lists of EVERY non-current platform and
#     returns warning strings (NOT errors — the hotkey is valid on the
#     user's current platform).  Callers (e.g. ``Config.load()`` in
#     ``config.py``) should append the returned strings to
#     ``Config._load_warnings`` / ``last_load_warnings`` so the UI can
#     surface them as non-blocking portability notices.
# ──────────────────────────────────────────────────────────────────────────

# The three hotkey fields whose values must not collide.  Note that
# ``push_to_talk_hotkey`` is NOT in :data:`IPC_CONFIG_ALLOWLIST` (removed
# per GT-F2-8 — see comment at the allowlist entry for ``repaste_hotkey``),
# so the IPC path's cross-field check will only see fields that survive
# the per-field validator (i.e. ``hotkey`` and ``repaste_hotkey``).  The
# full-config validator (:func:`validate_config`) DOES see all three
# fields via ``getattr(cfg, name)``, so a hand-edited config.json that
# sets a conflicting ``push_to_talk_hotkey`` is still caught at load time.
_HOTKEY_FIELD_NAMES: tuple[str, ...] = ("hotkey", "repaste_hotkey", "push_to_talk_hotkey")


def _check_cross_field_hotkey_conflicts(
    field_values: dict[str, str | None],
) -> list[str]:
    """Detect duplicate hotkey assignments across the 3 hotkey fields.

    XZ-14-04: without this cross-field check, a user could set
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
# PI-18 consistency check. Used by both :func:`validate_config_update`
# (delta-only check) and :func:`validate_config` (full-config check).
_CLOUD_CONSENT_FIELD_NAMES: tuple[str, ...] = (
    "cloud_openai_consent",
    "cloud_groq_consent",
    "cloud_deepgram_consent",
)


def _check_cross_field_cloud_config(
    field_values: dict[str, object],
) -> list[str]:
    """PI-18 / PI-24: cross-field cloud/LLM config consistency check.

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

    XZ-14-05: ``_validate_hotkey`` only consults the *current* platform's
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

    XZ-14-05: this is the warnings counterpart of :func:`validate_config`.
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


# ──────────────────────────────────────────────────────────────────────────
# XZ-14-08: recognized Whisper language codes.
#
# Previously ``_VALIDATOR_LANGUAGE`` was just ``_make_str_validator(max_len=16)``,
# which accepted any string up to 16 chars.  A typo like ``"english"`` or
# ``"zzzzz"`` would pass validation, persist to config.json, and surface as
# a cryptic Whisper load error at transcription time.  This allowlist
# catches such typos at ``set_config`` time with a clear, actionable error.
#
# Source: ``whisper.tokenizer.LANGUAGES`` (a dict of 2-letter ISO 639-1
# code → language name) if the ``whisper`` package is importable at module
# init.  Otherwise a hardcoded fallback covering the same 99 codes from
# openai-whisper's tokenizer.py (as of v20231117).  When whisper IS
# importable we use the live dict so any new languages added upstream are
# picked up automatically.
# ──────────────────────────────────────────────────────────────────────────
try:
    from whisper.tokenizer import LANGUAGES as _whisper_languages  # type: ignore[import-not-found]  # noqa: N811

    _ALLOWED_LANGUAGES: frozenset[str] = frozenset(_whisper_languages.keys())
    _ALLOWED_LANGUAGES_SOURCE = "whisper.tokenizer.LANGUAGES"
except ImportError:
    # Hardcoded fallback — the 99 codes from openai-whisper's tokenizer.py.
    # Kept in sync with the upstream list.  When whisper IS importable we
    # use the live dict (above) so new languages are picked up automatically.
    _ALLOWED_LANGUAGES = frozenset(
        {
            "en",
            "zh",
            "de",
            "es",
            "ru",
            "ko",
            "fr",
            "ja",
            "pt",
            "tr",
            "pl",
            "ca",
            "nl",
            "ar",
            "sv",
            "it",
            "id",
            "hi",
            "fi",
            "vi",
            "he",
            "uk",
            "el",
            "ms",
            "cs",
            "ro",
            "da",
            "hu",
            "ta",
            "no",
            "th",
            "ur",
            "hr",
            "bg",
            "lt",
            "la",
            "mi",
            "ml",
            "cy",
            "sk",
            "te",
            "fa",
            "lv",
            "bn",
            "sr",
            "az",
            "sl",
            "kn",
            "et",
            "mk",
            "br",
            "eu",
            "is",
            "hy",
            "ne",
            "mn",
            "bs",
            "kk",
            "sq",
            "sw",
            "gl",
            "mr",
            "pa",
            "si",
            "km",
            "sn",
            "yo",
            "so",
            "af",
            "oc",
            "ka",
            "be",
            "tg",
            "sd",
            "gu",
            "am",
            "yi",
            "lo",
            "uz",
            "fo",
            "ht",
            "ps",
            "tk",
            "nn",
            "mt",
            "sa",
            "lb",
            "my",
            "bo",
            "tl",
            "mg",
            "as",
            "tt",
            "haw",
            "ln",
            "ha",
            "ba",
            "jw",
            "su",
            "yue",
        }
    )
    _ALLOWED_LANGUAGES_SOURCE = "hardcoded fallback (whisper not importable)"


# Reuse the existing string validator for the basic shape checks (type,
# length, control characters).  XZ-14-08 layers the language-code allowlist
# on top so the existing ``test_str_validator_via_ipc_rejects_nul_in_language``
# regression test (which expects the error to contain the word "control")
# continues to pass.
_LANGUAGE_BASE_VALIDATOR = _make_str_validator(max_len=16)


def _validate_language(value: object) -> str | None:
    """Validate a Whisper language code.

    XZ-14-08: previously ``_VALIDATOR_LANGUAGE`` was just
    ``_make_str_validator(max_len=16)`` which accepted any string up to
    16 chars.  A typo like ``"english"`` or ``"zzzzz"`` would pass
    validation, persist to config.json, and surface as a cryptic Whisper
    load error at transcription time.

    This validator:

    1. Reuses :data:`_LANGUAGE_BASE_VALIDATOR` for type / length /
       control-character checks (so the existing
       ``test_str_validator_via_ipc_rejects_nul_in_language`` regression
       test still passes — the error must contain the word "control").
    2. Accepts the empty string as valid (interpreted as "auto-detect" —
       the renderer's ``value={config.language || "auto"}`` fallback relies
       on this).
    3. Rejects any non-empty string that is not a 2-letter ISO 639-1 code
       in :data:`_ALLOWED_LANGUAGES` with a clear, actionable error.
    """
    err = _LANGUAGE_BASE_VALIDATOR(value)
    if err is not None:
        return err
    # ``err is None`` implies ``value`` is a str (per _make_str_validator).
    assert isinstance(value, str)
    # Empty string is interpreted as "auto-detect" — accept it.
    if value == "":
        return None
    if value not in _ALLOWED_LANGUAGES:
        return f"Invalid language code {value!r} — expected a 2-letter ISO 639-1 code like 'en', 'zh', 'ja'"
    return None


_VALIDATOR_HOTKEY = _validate_hotkey
_VALIDATOR_LANGUAGE = _validate_language
_VALIDATOR_API_KEY = _make_str_validator(max_len=_MAX_API_KEY_LEN)
_VALIDATOR_API_URL = _make_url_validator(allow_empty=True)
_VALIDATOR_LLM_API_URL = _make_url_validator(allow_empty=False)
_VALIDATOR_LLM_MODEL = _make_str_validator(max_len=256)
_VALIDATOR_REPASTE_HOTKEY = _validate_hotkey
_VALIDATOR_MICROPHONE = _make_optional_str_validator(max_len=512)
_VALIDATOR_PUSH_TO_TALK_HOTKEY = _validate_hotkey
_VALIDATOR_CLOUD_MODEL = _make_str_validator(max_len=256)


# GT-D1-6: typed as ``dict[str, FieldSpec]`` (previously a bare ``dict``)
# so static checkers can verify that every entry is a (type, validator)
# pair. ``FieldSpec`` is the tuple alias defined above.
IPC_CONFIG_ALLOWLIST: dict[str, FieldSpec] = {
    # ── Hotkey ────────────────────────────────────────────────────────
    "hotkey": (str, _VALIDATOR_HOTKEY),
    # GT-F2-8: ``push_to_talk_hotkey`` removed from the IPC allowlist —
    # the TS-side contract (see voice_typer/client/src/renderer/src/types/config.ts)
    # documents it as a write-only back-compat field the renderer MUST NOT
    # write. Accepting it here would let a malicious IPC client mutate a
    # server field the renderer is forbidden to touch. Existing on-disk
    # config.json values are still loaded by ``Config.load()`` (the field
    # remains on the Config dataclass); only the IPC write path is closed.
    "repaste_hotkey": (str, _VALIDATOR_REPASTE_HOTKEY),
    # ── Recording ─────────────────────────────────────────────────────
    "microphone": ((str, type(None)), _VALIDATOR_MICROPHONE),
    # ── Transcription ─────────────────────────────────────────────────
    "model_size": (str, _make_enum_validator(ALLOWED_USER_MODELS)),
    "language": (str, _VALIDATOR_LANGUAGE),
    "device": (str, _make_enum_validator(frozenset({"cuda", "cpu"}))),
    "beam_size": (int, _make_int_validator(lo=1, hi=10)),
    "best_of": (int, _make_int_validator(lo=1, hi=10)),
    "condition_on_previous_text": (bool, _bool_validator),
    # ── Streaming (hidden) ────────────────────────────────────────────
    "streaming_transcription": (bool, _bool_validator),
    "streaming_chunk_seconds": (float, _make_float_validator(lo=0.1, hi=120.0)),
    "streaming_step_seconds": (float, _make_float_validator(lo=0.1, hi=60.0)),
    "streaming_left_overlap_seconds": (float, _make_float_validator(lo=0.0, hi=60.0)),
    "streaming_right_guard_seconds": (float, _make_float_validator(lo=0.0, hi=30.0)),
    "streaming_min_first_chunk_seconds": (float, _make_float_validator(lo=0.1, hi=60.0)),
    "streaming_silence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # ── Behavior ──────────────────────────────────────────────────────
    "autostart": (bool, _bool_validator),
    "paste_on_stop": (bool, _bool_validator),
    "unsafe_paste_on_unknown_focus": (bool, _bool_validator),
    "show_notifications": (bool, _bool_validator),
    # PW-3: prewarm scheduled-task master toggle. Surfaced in Settings →
    # General so users can opt out (e.g. gamers who want the RAM back).
    "fast_startup": (bool, _bool_validator),
    # ── Clipboard borrow/restore (ADR-0010) ───────────────────────────
    # ADR-0010 §2.11 / §8.3a: these keys MUST be in the IPC allowlist
    # or ``validate_config_update()`` drops them, ``service.apply_config()``
    # never setattrs them, ``config.save()`` does not persist them, and
    # ``refresh_config()`` never fires at runtime. Both are surfaced in
    # the renderer config schema so the Settings UI can reach them.
    "clipboard_save_restore": (bool, _bool_validator),
    "clipboard_restore_delay_ms": (int, _make_int_validator(lo=0, hi=2000)),
    # TY-11: idle-unload timer for the active ASR backend. 0 (default)
    # disables the feature; users with abundant VRAM can leave it at 0;
    # users who dictate intermittently and want the VRAM + GPU idle
    # power back can set it to e.g. 10 or 15. Upper bound 1440 = 24h
    # (anything above is almost certainly a typo).
    "model_idle_unload_minutes": (int, _make_int_validator(lo=0, hi=1440)),
    # ── ASR backend selection ─────────────────────────────────────────
    "asr_backend": (str, _make_enum_validator(frozenset({"whisper", "qwen", "parakeet"}))),
    # ── Text cleanup ──────────────────────────────────────────────────
    "text_cleanup_enabled": (bool, _bool_validator),
    "auto_punctuation": (bool, _bool_validator),
    # ── Logging ───────────────────────────────────────────────────────
    "log_transcriptions": (bool, _bool_validator),
    # ── P1 Features ───────────────────────────────────────────────────
    "recording_mode": (str, _make_enum_validator(frozenset({"toggle", "push_to_talk"}))),
    "esc_cancel_enabled": (bool, _bool_validator),
    # ── P2 Features ───────────────────────────────────────────────────
    "templates_enabled": (bool, _bool_validator),
    "vocabulary_enabled": (bool, _bool_validator),
    # Cloud ASR — secrets and URLs are sensitive but the renderer actively
    # manages them, so they are in the allowlist with strict validators.
    "cloud_api_key": (str, _VALIDATOR_API_KEY),
    "cloud_api_url": (str, _VALIDATOR_API_URL),
    "cloud_model": (str, _VALIDATOR_CLOUD_MODEL),
    "openai_api_key": (str, _VALIDATOR_API_KEY),
    "groq_api_key": (str, _VALIDATOR_API_KEY),
    "deepgram_api_key": (str, _VALIDATOR_API_KEY),
    # LLM polish — same rationale as cloud ASR.
    "llm_polish": (bool, _bool_validator),
    "llm_api_key": (str, _VALIDATOR_API_KEY),
    "llm_api_url": (str, _VALIDATOR_LLM_API_URL),
    "llm_model": (str, _VALIDATOR_LLM_MODEL),
    "llm_preset": (str, _make_enum_validator(frozenset({"professional", "casual", "email", "code"}))),
    # PRIVACY-001: consent flag is user-tunable (the consent dialog
    # itself sets this), but it's still subject to type validation.
    "llm_polish_consent": (bool, _bool_validator),
    # NEW-PRIV-005/006/009: privacy consent flags.  All user-tunable
    # via the consent dialogs in the renderer; all subject to type
    # validation so a malicious IPC client can't set them to non-bool
    # values to bypass the consent UI.
    "huggingface_consent": (bool, _bool_validator),
    "cloud_openai_consent": (bool, _bool_validator),
    "cloud_groq_consent": (bool, _bool_validator),
    "cloud_deepgram_consent": (bool, _bool_validator),
    "voice_biometric_consent": (bool, _bool_validator),
    # NEW-UX-029: sound feedback toggle.
    "sound_feedback_enabled": (bool, _bool_validator),
    # ── Crash recovery ────────────────────────────────────────────────
    "crash_recovery_enabled": (bool, _bool_validator),
    # ── Audio quality ─────────────────────────────────────────────────
    "audio_quality_warnings": (bool, _bool_validator),
    # ── P4: AI grammar / punctuation / capitalization ───────────────
    # All four toggles are user-tunable via Settings → AI Enhancement.
    # The master toggle (``ai_enhancement_enabled``) defaults OFF;
    # the three sub-toggles default ON.  Subject to type validation
    # so a malicious IPC client can't set them to non-bool values.
    "ai_enhancement_enabled": (bool, _bool_validator),
    "auto_capitalize": (bool, _bool_validator),
    "auto_punctuate": (bool, _bool_validator),
    "fix_grammar_basics": (bool, _bool_validator),
    # ── P5: Vocabulary automation ───────────────────────────────────
    # Master toggle + two float thresholds.  The confidence threshold
    # range is [0.0, 1.0] — values outside that range are nonsense
    # (a confidence can't be negative or above 1).  The auto-apply
    # threshold must be >= the suggest threshold to be meaningful,
    # but we don't enforce that here — the user may want to set
    # ``auto_apply_threshold = 1.0`` to effectively disable auto-apply
    # while still queueing suggestions for review.
    "vocabulary_automation_enabled": (bool, _bool_validator),
    "vocabulary_auto_confidence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "vocabulary_auto_apply_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # ── Waveform bubble ───────────────────────────────────────────────
    "waveform_bubble": (bool, _bool_validator),
    "bubble_position": (str, _make_enum_validator(frozenset({"top", "bottom"}))),
    "bubble_behavior": (str, _make_enum_validator(frozenset({"show_on_record", "always_visible"}))),
    "bubble_draggable": (bool, _bool_validator),
    "bubble_show_on_startup": (bool, _bool_validator),
    # UX-10: mic button + click-to-toggle for the always-visible bubble.
    "bubble_click_to_toggle": (bool, _bool_validator),
    "bubble_mic_button": (bool, _bool_validator),
    # ── History database ──────────────────────────────────────────────
    "history_retention_days": (int, _make_int_validator(lo=0, hi=36500)),
    "history_retention_count": (int, _make_int_validator(lo=0, hi=1_000_000)),
    "history_max_entries": (int, _make_int_validator(lo=0, hi=1_000_000)),
    # ── P3 Features / UX ──────────────────────────────────────────────
    "tray_left_click_action": (str, _make_enum_validator(frozenset({"open_app", "toggle_dictation"}))),
    "theme_mode": (str, _make_enum_validator(frozenset({"system", "light", "dark"}))),
    "theme_preset": (
        str,
        _make_enum_validator(
            frozenset(
                {
                    "default",
                    "amoled",
                    "nord",
                    "dracula",
                    "sepia",
                    "solarized",
                    "monokai",
                    "ayu",
                    "github",
                    "catppuccin",
                    "tokyo-night",
                    "custom",
                }
            )
        ),
    ),
    "custom_theme": (dict, _make_custom_theme_validator()),
    "text_size": (int, _make_int_validator(lo=8, hi=72)),
    # ── Silent mic disconnection (H12) ────────────────────────────────
    "silence_warning_seconds": (float, _make_float_validator(lo=0.0, hi=600.0)),
    "stop_on_silence_seconds": (float, _make_float_validator(lo=0.0, hi=3600.0)),
    # XZ-14-09: lower bound lowered from 300 to 30 (the prior 5-minute
    # minimum was an arbitrary / likely-typo value; 30 seconds still
    # guards against accidentally-zero values while allowing short
    # recordings for testing).
    "max_recording_time_seconds": (int, _make_int_validator(lo=30, hi=3600)),
    # GT-58: silence_rms_threshold / silence_peak_threshold REMOVED from
    # the IPC allowlist — they were also removed from the Config dataclass
    # (declared, validated, persisted, never read at runtime per ADR 0007
    # §4.3). Existing config.json values are silently scrubbed by the v3
    # schema migration.
    # AUDIO-013: Silero VAD configuration
    "use_silero_vad": (bool, _bool_validator),
    "vad_speech_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "vad_silence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # AUDIO-CH: recording channels (XZ-14-09: lower bound raised from
    # 0 to 1 — 0 channels is nonsensical and would crash the recorder at
    # open-stream time with an obscure PyAudio / sounddevice error).
    "recording_channels": (int, _make_int_validator(lo=1, hi=8)),
    # AUDIO-PRE: pre-roll buffer
    "pre_roll_buffer_seconds": (float, _make_float_validator(lo=0.0, hi=30.0)),
    # GT-58: normalize_audio / normalize_target_peak REMOVED from the IPC
    # allowlist — also removed from the Config dataclass (replaced by the
    # Compressor filter per ADR 0007 §5.2). Existing config.json values
    # are silently scrubbed by the v3 schema migration.
    # PLAT-013/014: paste safety warnings
    "warn_elevated_paste": (bool, _bool_validator),
    "warn_password_paste": (bool, _bool_validator),
    # ── Volume ducking (v1.1.0) ───────────────────────────────────────
    "volume_duck_enabled": (bool, _bool_validator),
    "volume_duck_level": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "volume_duck_fade_ms": (int, _make_int_validator(lo=0, hi=1000)),
    "volume_duck_smart_poll_interval_ms": (int, _make_int_validator(lo=50, hi=5000)),
    # ── Audio enhancement preset (ADR 0007) ───────────────────────────
    # G4-M-12 (partial): legacy aliases ``"none"`` and ``"recommended"``
    # are NO LONGER accepted by the IPC ``set_config`` validator. The
    # ``_migrate_to_v2`` schema migration in ``config.py`` (run inside
    # ``Config.load()``) still rewrites them to ``"off"`` and ``"auto"``
    # respectively for existing on-disk configs, so a stale
    # ``config.json`` written by an older app version keeps loading —
    # but the renderer can no longer introduce them via IPC. Agent 2-a
    # owns the load-side migration and is coordinated to emit a
    # deprecation log when the migration rewrites either legacy value.
    "audio_preset": (
        str,
        _make_enum_validator(
            frozenset(
                {
                    "auto",
                    "studio",
                    "noisy_room",
                    "off",
                    "custom",
                }
            )
        ),
    ),
    # ── Noise filtering (ADR 0007 — filter chain) ────────────────────
    # CR-32: Removed deprecated fields: noise_filter_enabled,
    # noise_filter_gate_threshold, noise_filter_rnnoise,
    # noise_filter_post_capture. Use noise_suppression_method + the
    # gate_*_db fields below instead.
    "noise_filter_highpass": (bool, _bool_validator),
    "noise_filter_highpass_cutoff_hz": (float, _make_float_validator(lo=20.0, hi=500.0)),
    "noise_filter_gate": (bool, _bool_validator),
    "noise_filter_gate_hold_ms": (float, _make_float_validator(lo=0.0, hi=1000.0)),
    # ADR 0007 §5.1: New filter chain fields
    # G4-M-12: enum literal is now sourced from the shared
    # ``NOISE_SUPPRESSION_METHODS`` constant defined above so the IPC
    # validator, the dataclass comment in ``config.py``, and the
    # runtime fallback in ``audio_filters/noise_suppressor.py`` all
    # agree on the canonical set.
    "noise_suppression_method": (str, _make_enum_validator(NOISE_SUPPRESSION_METHODS)),
    "noise_filter_gate_open_threshold_db": (float, _make_float_validator(lo=-96.0, hi=0.0)),
    "noise_filter_gate_close_threshold_db": (float, _make_float_validator(lo=-96.0, hi=0.0)),
    "noise_filter_gate_attack_ms": (float, _make_float_validator(lo=0.0, hi=10000.0)),
    "noise_filter_gate_release_ms": (float, _make_float_validator(lo=0.0, hi=10000.0)),
    "noise_filter_eq": (bool, _bool_validator),
    "noise_filter_eq_low_db": (float, _make_float_validator(lo=-20.0, hi=20.0)),
    "noise_filter_eq_mid_db": (float, _make_float_validator(lo=-20.0, hi=20.0)),
    "noise_filter_eq_high_db": (float, _make_float_validator(lo=-20.0, hi=20.0)),
    "noise_filter_compressor": (bool, _bool_validator),
    "noise_filter_compressor_threshold_db": (float, _make_float_validator(lo=-60.0, hi=0.0)),
    "noise_filter_compressor_ratio": (float, _make_float_validator(lo=1.0, hi=32.0)),
    "noise_filter_compressor_attack_ms": (float, _make_float_validator(lo=1.0, hi=500.0)),
    "noise_filter_compressor_release_ms": (float, _make_float_validator(lo=1.0, hi=1000.0)),
    "noise_filter_compressor_output_gain_db": (float, _make_float_validator(lo=-32.0, hi=32.0)),
    "noise_filter_limiter": (bool, _bool_validator),
    "noise_filter_limiter_ceiling_db": (float, _make_float_validator(lo=-60.0, hi=0.0)),
    "noise_filter_limiter_release_ms": (float, _make_float_validator(lo=1.0, hi=1000.0)),
    "noise_filter_notch": (bool, _bool_validator),
    "noise_filter_notch_frequency_hz": (float, _make_float_validator(lo=0.0, hi=500.0)),
}


def validate_config_update(data: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Validate a caller-supplied config update payload.

    Parameters
    ----------
    data : dict
        The raw ``data`` field from an IPC ``set_config`` command.  Must
        be a dict — callers should check before invoking.

    Returns
    -------
    (validated, errors) : (dict, list[str])
        ``validated`` is the subset of ``data`` whose keys are in
        :data:`IPC_CONFIG_ALLOWLIST` and whose values passed their
        validators.  ``errors`` is a list of human-readable error
        strings for ALL invalid fields encountered (CR-25: the function
        accumulates all errors rather than stopping at the first — the
        dispatcher treats the entire payload atomically, see
        ``ipc_server.set_config``).

        Unknown keys are silently dropped (no error, no log entry beyond
        a debug-level message) to preserve the existing
        "test_ignores_unknown_fields_without_crashing" contract.

    Notes
    -----
    The function is pure: it does not touch the Config object or perform
    any I/O.  This makes it trivially testable.
    """
    validated: dict[str, object] = {}
    errors: list[str] = []
    for k, v in data.items():
        spec = IPC_CONFIG_ALLOWLIST.get(k)
        if spec is None:
            # Unknown key — silently drop.  Debug-level so devs can
            # diagnose "why isn't my setting saving" without leaking
            # field-name existence to attackers (debug logs aren't
            # visible to end users by default).
            log.debug("[CONFIG] set_config dropped unknown key %r", k)
            continue
        expected_type, validator = spec
        # Type-check first (cheap), then run the field-specific validator
        # (which may do range/enum checks).  The expected_type is a
        # redundant guard against the validator being too lenient —
        # defense in depth.
        #
        # expected_type may be a single type (``str``, ``int``, ``bool``,
        # ``float``) or a tuple of types (e.g. ``(str, type(None))`` for
        # Optional[str] fields like ``microphone``).
        type_ok: bool
        if isinstance(expected_type, tuple):
            type_ok = isinstance(v, expected_type)
        elif expected_type is bool:
            type_ok = isinstance(v, bool)
        elif expected_type is int:
            type_ok = isinstance(v, int) and not isinstance(v, bool)
        elif expected_type is float:
            type_ok = isinstance(v, (int, float)) and not isinstance(v, bool)
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
            # CR-25: accumulate ALL errors, do not break on first.
            continue
        err = validator(v)
        if err is not None:
            errors.append(f"field {k!r} {err}")
            # CR-25: accumulate ALL errors, do not break on first.
            continue
        validated[k] = v
    # XZ-14-04: cross-field hotkey conflict check.  Only fields that
    # passed their per-field validator are in ``validated`` — invalid
    # hotkeys don't participate in the cross-field check (they already
    # produced their own per-field error and would just add noise).
    # Note: ``push_to_talk_hotkey`` is NOT in IPC_CONFIG_ALLOWLIST
    # (removed per GT-F2-8), so it's silently dropped above and never
    # appears in ``validated`` — the IPC path can only catch conflicts
    # between ``hotkey`` and ``repaste_hotkey``.  Conflicts involving
    # ``push_to_talk_hotkey`` are caught by :func:`validate_config`
    # at config-load time (it sees all 3 fields via getattr).
    #
    # YJ-FIX-B2: apply the same isinstance narrowing as YJ-24 so the
    # ``hotkey_values`` dict (typed ``dict[str, str | None]``) actually
    # matches its annotation. ``validated[name]`` is ``object`` (the
    # ``validated`` dict's value type), so without the narrow the dict
    # comprehension would produce ``dict[str, object | None]`` and
    # pyrefly would flag the assignment. The narrow is a no-op at
    # runtime because ``_check_cross_field_hotkey_conflicts`` skips
    # non-string values anyway.
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        if name in validated:
            raw = validated[name]
            hotkey_values[name] = raw if isinstance(raw, str) else None
        else:
            hotkey_values[name] = None
    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # PI-18 / PI-24: cross-field cloud/LLM config consistency check.
    # Only fields that passed their per-field validator are in
    # ``validated`` — invalid cloud/LLM fields don't participate in
    # the cross-field check (they already produced their own per-field
    # error and would just add noise).
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
    """Validate an already-loaded :class:`Config` instance against
    :data:`IPC_CONFIG_ALLOWLIST`.

    G4-M-12 (Task 2-x): the IPC ``set_config`` validator
    (:func:`validate_config_update`) only sees the *delta* a renderer
    pushes; it never re-checks the *whole* config that lives on disk
    after migration / manual edits / scripted writes. A migrated
    config can therefore hold values that the IPC validator would
    reject (e.g. a ``noise_suppression_method`` value of ``"speex"``
    left over from a hand-edited file before the enum was tightened,
    or a future ``audio_preset`` legacy alias surviving a botched
    migration). Until now there was no single choke-point that
    cross-checked the loaded config against the same rules the IPC
    layer enforces.

    This function is that choke-point. Agent 2-a is coordinated (via
    the worklog) to call it at the end of ``Config.load()`` and append
    any returned error strings to ``Config.last_load_warnings`` so the
    UI can surface "your config has invalid values" instead of
    silently running with a malformed state.

    Parameters
    ----------
    cfg
        A :class:`Config` dataclass instance (duck-typed — only
        ``getattr`` is used, so any object exposing the allowlisted
        fields as attributes works for testing).

    Returns
    -------
    list[str]
        A list of human-readable error strings, one per invalid
        field. Empty list means the config is valid. Each entry is
        formatted as ``"<field_name>: <error>"`` so the caller can
        display them line-by-line.

    Notes
    -----
    - Fields absent from ``cfg`` (``getattr`` returns ``None`` or
      raises ``AttributeError``) are SKIPPED — this function does not
      require every allowlisted field to be present on the object.
      This matches the IPC semantics where the renderer may push a
      partial update.
    - The validators are the SAME ones used by
      :func:`validate_config_update`, so the two paths can't drift.
    """
    errors: list[str] = []
    for key, (_field_type, validator) in IPC_CONFIG_ALLOWLIST.items():
        try:
            value = getattr(cfg, key)
        except AttributeError:
            # Field isn't present on the object — treat as "not set"
            # and skip (mirrors the IPC validator's None handling).
            continue
        if value is None:
            continue
        err = validator(value)
        if err:
            errors.append(f"{key}: {err}")
    # XZ-14-04: cross-field hotkey conflict check on the FULL config.
    # Unlike :func:`validate_config_update` (which can only see fields
    # the renderer pushed), this function sees ALL 3 hotkey fields via
    # getattr — so it catches conflicts involving ``push_to_talk_hotkey``
    # (which is NOT in IPC_CONFIG_ALLOWLIST and therefore not settable
    # via IPC, but IS a Config dataclass field that can be set by a
    # hand-edited config.json).
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        try:
            # YJ-24: narrow the ``getattr`` result explicitly so the
            # type-checker sees ``str | None`` (matching ``hotkey_values``'s
            # value type) instead of ``Any`` from the dynamic-name lookup.
            raw = getattr(cfg, name)
            hotkey_values[name] = raw if isinstance(raw, str) else None
        except AttributeError:
            hotkey_values[name] = None
    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # PI-18 / PI-24: cross-field cloud/LLM config consistency check
    # on the FULL config. Unlike :func:`validate_config_update` (which
    # only sees fields the renderer pushed), this function sees ALL
    # cloud/LLM fields via getattr — so it catches inconsistencies
    # introduced by hand-edited config.json files.
    cloud_field_values: dict[str, object] = {}
    for cloud_name in (
        "cloud_api_url",
        "cloud_api_key",
        "llm_polish",
        "llm_api_key",
        "llm_polish_consent",
        *_CLOUD_CONSENT_FIELD_NAMES,
    ):
        # Field isn't present on the object — treat as "not set"
        # and skip (mirrors the IPC validator's None handling).
        with contextlib.suppress(AttributeError):
            cloud_field_values[cloud_name] = getattr(cfg, cloud_name)
    errors.extend(_check_cross_field_cloud_config(cloud_field_values))
    return errors


# ──────────────────────────────────────────────────────────────────────────
# ARCH-REFAC-001: explicit ``__all__`` so the wildcard re-export in
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
    "_make_float_validator",
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
    # ARCH-14: extracted hotkey validation stage helpers (CR-29 / CR-22:
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
    # XZ-14-04: cross-field hotkey conflict check.
    "_HOTKEY_FIELD_NAMES",
    "_check_cross_field_hotkey_conflicts",
    # XZ-14-05: cross-platform hotkey portability warnings.
    "_cross_platform_hotkey_warning",
    "cross_platform_hotkey_warnings",
    # XZ-14-08: language code validator + allowlist.
    "_ALLOWED_LANGUAGES",
    "_ALLOWED_LANGUAGES_SOURCE",
    "_LANGUAGE_BASE_VALIDATOR",
    "_validate_language",
]
