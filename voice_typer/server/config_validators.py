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

import json as _json
import logging
import sys as _sys
from collections.abc import Callable
from pathlib import Path as _Path
from typing import TypeGuard
from urllib.parse import urlparse

log = logging.getLogger("voice_typer.server.config_validators")


ALLOWED_USER_MODELS = {"tiny.en", "small.en", "medium.en", "qwen", "parakeet"}


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
FieldSpec = tuple[type, ValidatorFn]


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
            return f"exceeds maximum length {max_len}"
        return None

    return _validate


def _make_optional_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if v is None:
            return None
        if not _is_str(v):
            return f"must be a string or null, got {type(v).__name__}"
        if len(v) > max_len:
            return f"exceeds maximum length {max_len}"
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
            return f"must be in [{lo}, {hi}]"
        return None

    return _validate


def _make_float_validator(*, lo: float, hi: float) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_float_or_int_not_bool(v):
            return f"must be a number, got {type(v).__name__}"
        if v < lo or v > hi:
            return f"must be in [{lo}, {hi}]"
        return None

    return _validate


def _make_enum_validator(allowed: set) -> ValidatorFn:
    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if v not in allowed:
            return f"must be one of {sorted(allowed)}"
        return None

    return _validate


def _make_custom_theme_validator() -> ValidatorFn:
    """Validate a custom-theme dict: {light: {var: val, ...}, dark: {var: val, ...}}."""
    key_keys = {"--background", "--foreground", "--primary", "--bg-subtle", "--border", "--text-muted"}

    def _validate(v: object) -> str | None:
        if not isinstance(v, dict):
            return "must be a dict"
        for mode in ("light", "dark"):
            mode_dict = v.get(mode)
            if not isinstance(mode_dict, dict):
                return f"field {mode!r} must be a dict"
            for key in key_keys:
                val = mode_dict.get(key)
                if not isinstance(val, str):
                    return f"{mode}.{key} must be a string, got {type(val).__name__}"
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


def _make_url_validator(*, allow_empty: bool = False, max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    """Validate an HTTP(S) URL.

    Rejects non-string values, oversized values, and any URL whose scheme
    is not ``http`` or ``https``.  Empty string is accepted iff ``allow_empty``
    (used for fields where empty means "feature disabled").
    """

    def _validate(v: object) -> str | None:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if len(v) > max_len:
            return f"exceeds maximum length {max_len}"
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
        if not parsed.netloc:
            return "must include a network location (host)"
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


def _validate_hotkey(value: object) -> str | None:
    """Validate a hotkey string against the reserved-shortcut denylist.

    Returns ``None`` if valid, or a human-readable error string if invalid.
    Mirrors the frontend ``validateHotkey`` in ``hotkey-validation.ts``.

    HOTKEY-VALIDATION-001: this replaces the previous length-only check
    (``_make_str_validator(max_len=256)``) which accepted OS-reserved
    shortcuts like ``<alt>+<tab>`` and conflict-prone combos like
    ``<ctrl>+<c>``.
    """
    if not isinstance(value, str):
        return f"must be a string, got {type(value).__name__}"
    if len(value) > 256:
        return "exceeds maximum length 256"
    if not value.strip():
        return "must not be empty"

    parts = _parse_hotkey_parts(value)
    if not parts:
        return "hotkey has no keys"

    # Check the universal reserved denylist (applies to all platforms).
    # Includes window-management shortcuts (Alt+Tab/F4/Esc/Space) and
    # Enter-based combos (Enter, Ctrl+Enter, Shift+Enter) which
    # interfere with typing, form submission, and messaging shortcuts.
    normalized = value.lower()
    if normalized in _UNIVERSAL_RESERVED_HOTKEYS:
        return "reserved — conflicts with operating system or common app shortcuts"

    # Check the per-platform denylist.
    platform = _platform_key()
    reserved = _RESERVED_HOTKEYS.get(platform, set())
    for r in reserved:
        if r == normalized:
            return f"reserved by operating system ({platform})"

    # HOTKEY-VALIDATION-002 (Task 2.2.5): single letter/digit rejection.
    # The prior fix (HOTKEY-FIX-002) added letters and digits to the
    # frontend KEY_CODE_TO_PYNPUT table so that combos like Alt+Q parse
    # correctly, but forgot to add a validation rule preventing single
    # letters/digits from being assigned as standalone hotkeys. A
    # standalone <a> would trigger dictation every time the user types
    # 'a' — clearly unusable. Reject any single-part hotkey that is a
    # single alphanumeric character. Multi-key combos (Alt+Q, Ctrl+V)
    # are NOT affected — they have 2+ parts and are checked by the
    # rules below.
    if len(parts) == 1:
        sole = parts[0]
        if len(sole) == 1 and sole.isalnum():
            return f"single letters and digits can't be used as hotkeys — '{sole}' would interfere with typing"

    # Win+* / Super+* / Cmd+* blanket blocks for system shell shortcuts.
    # HOTKEY-VALIDATION-002 (Task 2.2.5): the prior code blanket-blocked
    # Super+anything on Linux, which incorrectly rejected <super>+<space>
    # (a combo most Linux desktop environments allow reassigning). The
    # blanket block now applies only on Windows (where the Win key is
    # heavily reserved by the OS shell — Win+E, Win+L, Win+D, etc.). On
    # Linux, Super combos are checked against the per-platform reserved
    # list (_RESERVED_HOTKEYS["linux"] = Super+L, Super+D, Super+Tab) —
    # all other Super combos are allowed.
    has_win = any(p in ("win", "super") for p in parts)
    has_cmd = any(p in ("cmd", "cmd_l", "cmd_r") for p in parts)
    if has_win and platform == "win32":
        return "Windows key combinations are reserved by the OS shell"
    if has_cmd and platform == "darwin" and len(parts) > 1:
        # On macOS, Cmd+<letter> is heavily used by the system and apps.
        # Block Cmd+<letter> but allow Cmd+<F-key> and Cmd+<special-key>.
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        for nm in non_mods:
            if len(nm) == 1 and nm.isalpha():
                return f"Cmd+{nm.upper()} is reserved by macOS / common apps"

    # Alt+Shift blanket block (Windows language switching).
    if platform == "win32":
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        has_alt = any(p.startswith("alt") for p in parts)
        has_shift = any(p.startswith("shift") for p in parts)
        if has_alt and has_shift and not non_mods:
            return "Alt+Shift is reserved by Windows for language switching"

    # Ctrl+<common-letter> blanket block (Copy/Paste/Undo/Save/etc.).
    # Only applies to PURE Ctrl+<letter> — if another modifier is present
    # (e.g. Ctrl+Alt+U), the combo is allowed because it doesn't conflict
    # with the common app shortcuts.
    has_ctrl = any(p.startswith("ctrl") for p in parts)
    if has_ctrl:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        modifiers_non_ctrl = [p for p in parts if p in _HOTKEY_MODIFIERS and not p.startswith("ctrl")]
        if not modifiers_non_ctrl:
            # Pure Ctrl+<key> — apply the letter block.
            for nm in non_mods:
                if nm in _BLOCKED_CTRL_LETTERS:
                    return f"Ctrl+{nm.upper()} is a reserved application shortcut"

    # Shift+<letter> blanket block (interferes with capitalization).
    # Only applies to PURE Shift+<letter> — if another modifier is present,
    # the combo is allowed (e.g. Ctrl+Shift+Z = redo in many apps).
    has_shift = any(p.startswith("shift") for p in parts)
    if has_shift:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        modifiers_non_shift = [p for p in parts if p in _HOTKEY_MODIFIERS and not p.startswith("shift")]
        if not modifiers_non_shift:
            # Pure Shift+<key> — apply the letter block.
            for nm in non_mods:
                if len(nm) == 1 and (nm.isalpha() or nm.isdigit()):
                    return f"Shift+{nm.upper()} interferes with text capitalization or symbol input"

    return None


_VALIDATOR_HOTKEY = _validate_hotkey
_VALIDATOR_LANGUAGE = _make_str_validator(max_len=16)
_VALIDATOR_API_KEY = _make_str_validator(max_len=_MAX_API_KEY_LEN)
_VALIDATOR_API_URL = _make_url_validator(allow_empty=True)
_VALIDATOR_LLM_API_URL = _make_url_validator(allow_empty=False)
_VALIDATOR_LLM_MODEL = _make_str_validator(max_len=256)
_VALIDATOR_REPASTE_HOTKEY = _validate_hotkey
_VALIDATOR_MICROPHONE = _make_optional_str_validator(max_len=512)
_VALIDATOR_PUSH_TO_TALK_HOTKEY = _validate_hotkey
_VALIDATOR_CLOUD_MODEL = _make_str_validator(max_len=256)


IPC_CONFIG_ALLOWLIST: dict = {
    # ── Hotkey ────────────────────────────────────────────────────────
    "hotkey": (str, _VALIDATOR_HOTKEY),
    "push_to_talk_hotkey": (str, _VALIDATOR_PUSH_TO_TALK_HOTKEY),
    "repaste_hotkey": (str, _VALIDATOR_REPASTE_HOTKEY),
    # ── Recording ─────────────────────────────────────────────────────
    "microphone": ((str, type(None)), _VALIDATOR_MICROPHONE),
    # ── Transcription ─────────────────────────────────────────────────
    "model_size": (str, _make_enum_validator(ALLOWED_USER_MODELS)),
    "language": (str, _VALIDATOR_LANGUAGE),
    "device": (str, _make_enum_validator({"cuda", "cpu"})),
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
    # ── ASR backend selection ─────────────────────────────────────────
    "asr_backend": (str, _make_enum_validator({"whisper", "qwen", "parakeet"})),
    # ── Text cleanup ──────────────────────────────────────────────────
    "text_cleanup_enabled": (bool, _bool_validator),
    "auto_punctuation": (bool, _bool_validator),
    # ── Logging ───────────────────────────────────────────────────────
    "log_transcriptions": (bool, _bool_validator),
    # ── P1 Features ───────────────────────────────────────────────────
    "recording_mode": (str, _make_enum_validator({"toggle", "push_to_talk"})),
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
    "llm_preset": (str, _make_enum_validator({"professional", "casual", "email", "code"})),
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
    "bubble_position": (str, _make_enum_validator({"top", "bottom"})),
    "bubble_behavior": (str, _make_enum_validator({"show_on_record", "always_visible"})),
    "bubble_draggable": (bool, _bool_validator),
    "bubble_show_on_startup": (bool, _bool_validator),
    # ── History database ──────────────────────────────────────────────
    "history_retention_days": (int, _make_int_validator(lo=0, hi=36500)),
    "history_retention_count": (int, _make_int_validator(lo=0, hi=1_000_000)),
    "history_max_entries": (int, _make_int_validator(lo=10, hi=1_000_000)),
    # ── P3 Features / UX ──────────────────────────────────────────────
    "tray_left_click_action": (str, _make_enum_validator({"open_app", "toggle_dictation"})),
    "theme_mode": (str, _make_enum_validator({"system", "light", "dark"})),
    "theme_preset": (
        str,
        _make_enum_validator(
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
        ),
    ),
    "custom_theme": (dict, _make_custom_theme_validator()),
    "high_contrast": (bool, _bool_validator),
    "text_size": (int, _make_int_validator(lo=8, hi=72)),
    # ── Silent mic disconnection (H12) ────────────────────────────────
    "silence_warning_seconds": (float, _make_float_validator(lo=0.0, hi=600.0)),
    "stop_on_silence_seconds": (float, _make_float_validator(lo=0.0, hi=3600.0)),
    "max_recording_time_seconds": (int, _make_int_validator(lo=300, hi=3600)),
    # AUDIO-014: configurable VAD/silence thresholds
    "silence_rms_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "silence_peak_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # AUDIO-013: Silero VAD configuration
    "use_silero_vad": (bool, _bool_validator),
    "vad_speech_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "vad_silence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # AUDIO-CH: recording channels
    "recording_channels": (int, _make_int_validator(lo=0, hi=8)),
    # AUDIO-PRE: pre-roll buffer
    "pre_roll_buffer_seconds": (float, _make_float_validator(lo=0.0, hi=30.0)),
    # AUDIO-AGC: peak normalization
    "normalize_audio": (bool, _bool_validator),
    "normalize_target_peak": (float, _make_float_validator(lo=0.1, hi=1.0)),
    # PLAT-013/014: paste safety warnings
    "warn_elevated_paste": (bool, _bool_validator),
    "warn_password_paste": (bool, _bool_validator),
    # ── Volume ducking (v1.1.0) ───────────────────────────────────────
    "volume_duck_enabled": (bool, _bool_validator),
    "volume_duck_level": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "volume_duck_per_session": (bool, _bool_validator),
    "volume_duck_fade_ms": (int, _make_int_validator(lo=0, hi=1000)),
    "volume_duck_smart": (bool, _bool_validator),
    "volume_duck_smart_poll_interval_ms": (int, _make_int_validator(lo=50, hi=5000)),
    # ── Audio enhancement preset (ADR 0007) ───────────────────────────
    "audio_preset": (
        str,
        _make_enum_validator(
            {
                "auto",
                "studio",
                "noisy_room",
                "off",
                "custom",
                "none",
                "recommended",
            }
        ),
    ),
    # ── Noise filtering (ADR 0007 — filter chain) ────────────────────
    "noise_filter_enabled": (bool, _bool_validator),  # DEPRECATED
    "noise_filter_highpass": (bool, _bool_validator),
    "noise_filter_highpass_cutoff_hz": (float, _make_float_validator(lo=20.0, hi=500.0)),
    "noise_filter_gate": (bool, _bool_validator),
    "noise_filter_gate_threshold": (float, _make_float_validator(lo=0.0, hi=0.1)),  # DEPRECATED
    "noise_filter_gate_hold_ms": (float, _make_float_validator(lo=0.0, hi=1000.0)),
    "noise_filter_rnnoise": (bool, _bool_validator),  # DEPRECATED
    "noise_filter_post_capture": (bool, _bool_validator),  # DEPRECATED
    # ADR 0007 §5.1: New filter chain fields
    "noise_suppression_method": (str, _make_enum_validator({"rnnoise", "deepfilternet", "speex", "none"})),
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


def validate_config_update(data: dict) -> tuple[dict, list]:
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
        strings for the first invalid field encountered (the function
        stops at the first error to keep messages actionable; the
        dispatcher treats the entire payload atomically — see
        ``ipc_server.set_config``).

        Unknown keys are silently dropped (no error, no log entry beyond
        a debug-level message) to preserve the existing
        "test_ignores_unknown_fields_without_crashing" contract.

    Notes
    -----
    The function is pure: it does not touch the Config object or perform
    any I/O.  This makes it trivially testable.
    """
    validated: dict = {}
    errors: list = []
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
            break
        err = validator(v)
        if err is not None:
            errors.append(f"field {k!r} {err}")
            break
        validated[k] = v
    return validated, errors


# ──────────────────────────────────────────────────────────────────────────
# ARCH-REFAC-001: explicit ``__all__`` so the wildcard re-export in
# ``config.py`` (``from .config_validators import *``) brings through
# every validator symbol — including the underscore-prefixed factory
# helpers — preserving the pre-refactor import surface.
# ──────────────────────────────────────────────────────────────────────────
__all__ = [
    # Constants
    "ALLOWED_USER_MODELS",
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
]
