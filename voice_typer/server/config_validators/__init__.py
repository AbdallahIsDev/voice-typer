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

This ``__init__.py`` is the assembly point: it pulls every public name
back up to the ``voice_typer.server.config_validators`` namespace so
existing imports (``from voice_typer.server.config_validators import
validate_config``, etc.) continue to work unchanged.  It also defines
the module-level constants (``ALLOWED_USER_MODELS``,
``NOISE_SUPPRESSION_METHODS``, the ``MAX_RECORDING_TIME_SECONDS_*`` /
``STREAMING_*`` bounds, the pre-built ``_VALIDATOR_*`` instances), the
:data:`IPC_CONFIG_ALLOWLIST` registry, and the two main entry points
:func:`validate_config_update` and :func:`validate_config`.

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

import contextlib
import logging
import sys as _sys  # noqa: F401  # test patch target (tests mutate cv._sys.platform → sys.platform)

# ──────────────────────────────────────────────────────────────────────────
# Submodule re-exports.  Importing these names into the package namespace
# means callers can keep using
# ``from voice_typer.server.config_validators import _validate_hotkey``
# (or any other symbol) exactly as before.  It also means
# :func:`validate_config` / :func:`validate_config_update` (defined below)
# can reference the cross-field helpers via the package globals — which is
# essential because the regression tests in
# ``tests/test_config_validators_hotkey_nonstring.py`` monkeypatch
# ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
# and expect :func:`validate_config` to see the patched binding.
# ──────────────────────────────────────────────────────────────────────────
from voice_typer.server.config_validators.cross_field import (
    _CLOUD_CONSENT_FIELD_NAMES,
    _HOTKEY_FIELD_NAMES,
    _check_cross_field_cloud_config,
    _check_cross_field_hotkey_conflicts,
    _cross_platform_hotkey_warning,
    cross_platform_hotkey_warnings,
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
from voice_typer.server.config_validators.scalar import (
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
from voice_typer.server.model_registry import (
    MODEL_REGISTRY as _MODEL_REGISTRY_FOR_ALLOWLIST,
)

log = logging.getLogger("voice_typer.server.config_validators")


# canonical bounds + default for ``max_recording_time_seconds``.
# Defined here (the import-safe leaf package) so ``config.py`` can import
# them without participating in a circular import. Both the IPC validator
# below and ``Config._coerce_max_recording_time`` read from these
# constants — closing the split-brain bug where the IPC validator's
# ``lo=30`` disagreed with the post-load clamp's ``lo=300``, causing a
# user-set 30-second value to be silently bumped to 300 on the next
# ``Config.load()``.
MAX_RECORDING_TIME_SECONDS_DEFAULT: int = 900  # 15 minutes
MAX_RECORDING_TIME_SECONDS_MIN: int = 300  # 5 minutes
MAX_RECORDING_TIME_SECONDS_MAX: int = 3600  # 60 minutes

# shared streaming-field minimums (mirrors  pattern).
# Pre-fix the IPC validator used ``lo=0.0`` while
# ``Config._coerce_streaming_fields`` clamped to ``3.0`` / ``1.5`` — a
# value the renderer persisted (``0.5``) silently changed across
# save/load cycles (split-brain validation). Defined here (the import-safe
# leaf package) so ``config.py`` can mirror the values without participating
# in a circular import; both the IPC validator below and
# ``Config._coerce_streaming_fields`` use the same bound.
STREAMING_LEFT_OVERLAP_SECONDS_MIN: float = 3.0
STREAMING_RIGHT_GUARD_SECONDS_MIN: float = 1.5


# extended to include the multilingual variants (tiny/small/medium,
# no .en suffix) that OnboardingController.MODEL_OPTIONS offers to users.
# Without these, non-English users who pick a multilingual model in
# onboarding silently get English-only Whisper after the first restart
# (Config.load() resets model_size to "small.en" because the multilingual
# name is not in the allowlist).
#
# ALLOWED_USER_MODELS is now DERIVED from
# :data:`model_registry.MODEL_REGISTRY` at import time so the two cannot
# drift (the previous hand-maintained set had 8 entries while
# MODEL_REGISTRY had 12 — `base`, `base.en`, `large`, and `turbo` were
# silently reset to "small.en" on every Config.load()). ``large-v3``
# remains unsupported because it is NOT in MODEL_REGISTRY (only the
# unversioned ``large`` is) — the existing
# ``test_load_normalizes_legacy_or_unsupported_model_to_small_en``
# regression test was updated to drop ``base.en`` from its parametrize
# list (``base.en`` is now a valid model).

ALLOWED_USER_MODELS: frozenset[str] = frozenset(_MODEL_REGISTRY_FOR_ALLOWLIST.keys())


# ──────────────────────────────────────────────────────────────────────────
# canonical noise-suppression backend enum.
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

# Pre-built validator instances — built once at import time so the
# IPC_CONFIG_ALLOWLIST can reference them by name.  Mirrors the original
# module's `_VALIDATOR_*` block verbatim.
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
_VALIDATOR_TRUSTED_HOSTS = _validate_trusted_extra_hosts


# typed as ``dict[str, FieldSpec]`` (previously a bare ``dict``)
# so static checkers can verify that every entry is a (type, validator)
# pair. ``FieldSpec`` is the tuple alias defined above.
IPC_CONFIG_ALLOWLIST: dict[str, FieldSpec] = {
    # ── Hotkey ────────────────────────────────────────────────────────
    "hotkey": (str, _VALIDATOR_HOTKEY),
    # ``push_to_talk_hotkey`` removed from the IPC allowlist —
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
    "streaming_left_overlap_seconds": (
        float,
        _make_float_validator(lo=STREAMING_LEFT_OVERLAP_SECONDS_MIN, hi=60.0),
    ),
    "streaming_right_guard_seconds": (
        float,
        _make_float_validator(lo=STREAMING_RIGHT_GUARD_SECONDS_MIN, hi=30.0),
    ),
    "streaming_min_first_chunk_seconds": (float, _make_float_validator(lo=0.1, hi=60.0)),
    "streaming_silence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # ── Behavior ──────────────────────────────────────────────────────
    "autostart": (bool, _bool_validator),
    "paste_on_stop": (bool, _bool_validator),
    "unsafe_paste_on_unknown_focus": (bool, _bool_validator),
    "show_notifications": (bool, _bool_validator),
    # prewarm scheduled-task master toggle. Surfaced in Settings →
    # General so users can opt out (e.g. gamers who want the RAM back).
    "fast_startup": (bool, _bool_validator),
    # auto-update feature — offline-pack download consent toggle
    # (docs/auto-update-feature.md §8.4). Renderer-writable via
    # set_config so the Settings UI can persist the opt-in. Renamed
    # from ``runtime_pack_consent`` 2026-08-14 (legacy key migrated in
    # config/loader.py).
    "offline_pack_consent": (bool, _bool_validator),
    # ── Clipboard borrow/restore (ADR-0010) ───────────────────────────
    # ADR-0010 §2.11 / §8.3a: these keys MUST be in the IPC allowlist
    # or ``validate_config_update()`` drops them, ``service.apply_config()``
    # never setattrs them, ``config.save()`` does not persist them, and
    # ``refresh_config()`` never fires at runtime. Both are surfaced in
    # the renderer config schema so the Settings UI can reach them.
    "clipboard_save_restore": (bool, _bool_validator),
    "clipboard_restore_delay_ms": (int, _make_int_validator(lo=0, hi=2000)),
    # User-configured URL-allowlist extensions for self-hosted
    # LLM/ASR endpoints. Renderer-writable via set_config so the Settings
    # UI (when the affordance lands) can persist trusted hosts; the
    # value is re-applied to the runtime allowlist on Config.load() and
    # on every set_config that carries the key.
    "trusted_extra_hosts": (list, _VALIDATOR_TRUSTED_HOSTS),
    # idle-unload timer for the active ASR backend. 0 (default)
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
    # 006/009: privacy consent flags.  All user-tunable
    # via the consent dialogs in the renderer; all subject to type
    # validation so a malicious IPC client can't set them to non-bool
    # values to bypass the consent UI.
    "huggingface_consent": (bool, _bool_validator),
    "cloud_openai_consent": (bool, _bool_validator),
    "cloud_groq_consent": (bool, _bool_validator),
    "cloud_deepgram_consent": (bool, _bool_validator),
    "voice_biometric_consent": (bool, _bool_validator),
    # sound feedback toggle.
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
    # mic button + click-to-toggle for the always-visible bubble.
    "bubble_click_to_toggle": (bool, _bool_validator),
    "bubble_mic_button": (bool, _bool_validator),
    # Persisted bubble window position (screen-space pixel coords).
    # ``expected_type`` widened to ``(int, type(None))`` so the IPC
    # pre-check accepts the "not set" sentinel the renderer writes when
    # the user has never dragged the bubble (matching the dataclass
    # default of ``None``). The validator itself (returned by
    # ``_make_optional_int_validator``) short-circuits ``None`` to
    # success and delegates the range check to ``_make_int_validator``
    # for non-None values. Range [0, 10000] covers any plausible screen
    # coordinate (8K monitors are 7680px wide) while rejecting negative
    # / absurd values that would place the bubble off-screen.
    "bubble_x": ((int, type(None)), _make_optional_int_validator(lo=0, hi=10000)),
    "bubble_y": ((int, type(None)), _make_optional_int_validator(lo=0, hi=10000)),
    # Persisted bubble scale factor (multiplier on the base DPI).
    # Range [0.5, 3.0] — wider than the renderer's visible [0.5, 2.0]
    # clamp so a future renderer change can loosen the visible range
    # without a server-side allowlist edit. Accepts ``None`` for the
    # same "not set" reason as ``bubble_x`` / ``bubble_y`` above.
    "bubble_scale": ((float, type(None)), _make_optional_float_validator(lo=0.5, hi=3.0)),
    # Persisted microphone-test duration (seconds). Range [1, 60] —
    # wider than the renderer's visible [1, 30] clamp (same rationale
    # as ``bubble_scale``). Accepts ``None`` so the renderer can clear
    # the field back to "use the in-app default of 5s".
    "test_duration_seconds": ((int, type(None)), _make_optional_int_validator(lo=1, hi=60)),
    # ── History database ──────────────────────────────────────────────
    # ``history_enabled`` is the master toggle for whether dictated
    # text is persisted to the history SQLite DB. Defaults True; users
    # who dictate sensitive content can toggle it off via Settings →
    # Privacy. The dictation_pipeline gate (owned by P4-A4) reads
    # ``self._app.config.history_enabled`` to skip the add_transcription
    # call when False.
    "history_enabled": (bool, _bool_validator),
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
    # ``expected_type`` widened from bare ``dict`` to
    # ``(dict, type(None))`` so the IPC validator's pre-check accepts
    # ``None`` (the "clear custom theme" sentinel sent by useTheme.ts
    # when the user reverts to preset). The validator itself (returned
    # by ``_make_custom_theme_validator``) short-circuits ``None`` to
    # success.
    "custom_theme": ((dict, type(None)), _make_custom_theme_validator()),
    "text_size": (int, _make_int_validator(lo=8, hi=72)),
    # ── Silent mic disconnection (H12) ────────────────────────────────
    "silence_warning_seconds": (float, _make_float_validator(lo=0.0, hi=600.0)),
    "stop_on_silence_seconds": (float, _make_float_validator(lo=0.0, hi=3600.0)),
    # lower bound lowered from 300 to 30 (the prior 5-minute
    # minimum was an arbitrary / likely-typo value; 30 seconds still
    # guards against accidentally-zero values while allowing short
    # recordings for testing).
    # REVERTED — the IPC validator's ``lo=30`` disagreed with
    # ``Config._coerce_max_recording_time``'s post-load clamp (``lo=300``),
    # causing a split-brain bug where a user could set 30 seconds via IPC
    # but the next ``Config.load()`` silently bumped it back to 300. Both
    # sides now read from the shared module-level constants
    # ``MAX_RECORDING_TIME_SECONDS_MIN`` / ``_MAX`` defined in
    # ``voice_typer.server.config``.
    "max_recording_time_seconds": (
        int,
        _make_int_validator(
            lo=MAX_RECORDING_TIME_SECONDS_MIN,
            hi=MAX_RECORDING_TIME_SECONDS_MAX,
        ),
    ),
    # silence_rms_threshold / silence_peak_threshold REMOVED from
    # the IPC allowlist — they were also removed from the Config dataclass
    # (declared, validated, persisted, never read at runtime per ADR 0007
    # §4.3). Existing config.json values are silently scrubbed by the v3
    # schema migration.
    # Silero VAD configuration
    "use_silero_vad": (bool, _bool_validator),
    "vad_speech_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "vad_silence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # ER-42: auto-calibrate VAD thresholds from ambient noise. Must stay
    # in the allowlist so set_config can enable the implemented (but
    # previously unreachable) calibration path in vad_processor.py.
    "vad_auto_calibrate": (bool, _bool_validator),
    # AUDIO-CH: recording channels (: lower bound raised from
    # 0 to 1 — 0 channels is nonsensical and would crash the recorder at
    # open-stream time with an obscure PyAudio / sounddevice error).
    "recording_channels": (int, _make_int_validator(lo=1, hi=8)),
    # AUDIO-PRE: pre-roll buffer
    "pre_roll_buffer_seconds": (float, _make_float_validator(lo=0.0, hi=30.0)),
    # normalize_audio / normalize_target_peak REMOVED from the IPC
    # allowlist — also removed from the Config dataclass (replaced by the
    # Compressor filter per ADR 0007 §5.2). Existing config.json values
    # are silently scrubbed by the v3 schema migration.
    # 014: paste safety warnings
    "warn_elevated_paste": (bool, _bool_validator),
    "warn_password_paste": (bool, _bool_validator),
    # ── Volume ducking (v1.1.0) ───────────────────────────────────────
    "volume_duck_enabled": (bool, _bool_validator),
    "volume_duck_level": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "volume_duck_fade_ms": (int, _make_int_validator(lo=0, hi=1000)),
    "volume_duck_smart_poll_interval_ms": (int, _make_int_validator(lo=50, hi=5000)),
    # ── Audio enhancement preset (ADR 0007) ───────────────────────────
    # (partial): legacy aliases ``"none"`` and ``"recommended"``
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
    # Removed deprecated fields: noise_filter_enabled,
    # noise_filter_gate_threshold, noise_filter_rnnoise,
    # noise_filter_post_capture. Use noise_suppression_method + the
    # gate_*_db fields below instead.
    "noise_filter_highpass": (bool, _bool_validator),
    "noise_filter_highpass_cutoff_hz": (float, _make_float_validator(lo=20.0, hi=500.0)),
    "noise_filter_gate": (bool, _bool_validator),
    "noise_filter_gate_hold_ms": (float, _make_float_validator(lo=0.0, hi=1000.0)),
    # ADR 0007 §5.1: New filter chain fields
    # enum literal is now sourced from the shared
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
    strings for ALL invalid fields encountered (: the function
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
            # Unknown key — silently drop.  : promoted to
            # WARNING (was DEBUG) to match ``Config._filter_unknown_keys``
            # in ``config.py``. Previously the two paths diverged:
            # on-disk load logged WARNING for unknown keys while the
            # IPC ``set_config`` path logged DEBUG, so a user editing
            # settings via the UI saw no signal when a stale client
            # sent a field the server's allowlist didn't recognize.
            # Field-name existence is not sensitive (the allowlist is
            # public source), and WARNING is gated by the same logging
            # config as the load path.
            log.warning("[CONFIG] set_config dropped unknown key %r", k)
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
    # passed their per-field validator are in ``validated`` — invalid
    # hotkeys don't participate in the cross-field check (they already
    # produced their own per-field error and would just add noise).
    # Note: ``push_to_talk_hotkey`` is NOT in IPC_CONFIG_ALLOWLIST
    # (removed per ), so it's silently dropped above and never
    # appears in ``validated`` — the IPC path can only catch conflicts
    # between ``hotkey`` and ``repaste_hotkey``.  Conflicts involving
    # ``push_to_talk_hotkey`` are caught by :func:`validate_config`
    # at config-load time (it sees all 3 fields via getattr).
    #
    # apply the same isinstance narrowing as  so the
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
    # cross-field cloud/LLM config consistency check.
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

    (Task 2-x): the IPC ``set_config`` validator
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
    # cross-field hotkey conflict check on the FULL config.
    # Unlike :func:`validate_config_update` (which can only see fields
    # the renderer pushed), this function sees ALL 3 hotkey fields via
    # getattr — so it catches conflicts involving ``push_to_talk_hotkey``
    # (which is NOT in IPC_CONFIG_ALLOWLIST and therefore not settable
    # via IPC, but IS a Config dataclass field that can be set by a
    # hand-edited config.json).
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        try:
            # narrow the ``getattr`` result explicitly so the
            # type-checker sees ``str | None`` (matching ``hotkey_values``'s
            # value type) instead of ``Any`` from the dynamic-name lookup.
            raw = getattr(cfg, name)
            hotkey_values[name] = raw if isinstance(raw, str) else None
        except AttributeError:
            hotkey_values[name] = None
    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # cross-field cloud/LLM config consistency check
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
