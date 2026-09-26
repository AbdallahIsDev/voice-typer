"""IPC ``set_config`` allowlist + supporting constants.

This submodule was split out of the original monolithic
``config_validators/__init__.py`` so the security-critical allowlist
has its own focused home.  It owns:

* :data:`MAX_RECORDING_TIME_SECONDS_DEFAULT` / ``_MIN`` / ``_MAX`` —
  canonical bounds for ``max_recording_time_seconds`` (shared with
  ``config/coercion.py`` so the IPC validator and the post-load clamp
  can never drift).
* :data:`STREAMING_LEFT_OVERLAP_SECONDS_MIN` /
  :data:`STREAMING_RIGHT_GUARD_SECONDS_MIN`: canonical lower bounds
  for the streaming-overlap / -guard fields (same shared-source
  rationale).
* :data:`ALLOWED_USER_MODELS`: derived from
  :data:`model_registry.MODEL_REGISTRY` at import time so the two
  cannot drift.
* :data:`NOISE_SUPPRESSION_METHODS`: the canonical noise-suppression
  backend enum (``"rnnoise" | "gtcrn" | "none"``); imported by
  ``audio_filters/noise_suppressor.py`` and re-exported via
  ``config/__init__.py``.
* The pre-built ``_VALIDATOR_*`` instances used inside
  :data:`IPC_CONFIG_ALLOWLIST`.
* :data:`IPC_CONFIG_ALLOWLIST`: the explicit, reviewed map of fields
  the predecessor renderer is permitted to mutate via the IPC
  ``set_config`` command, together with their per-field validators.

The :data:`IPC_CONFIG_ALLOWLIST` is a NON-NEGOTIABLE security contract
(see ``AGENTS.md`` §6.3 / ``CONTRIBUTING.md`` §6.3, SEC-002).  Its
public import path stays put: callers continue to write
``from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST``
because ``config_validators/__init__.py`` re-exports it from here.

Every constant defined in this module is also re-exported from
``config_validators/__init__.py`` so existing imports
(``from voice_typer.server.config_validators import
ALLOWED_USER_MODELS``, ``… import MAX_RECORDING_TIME_SECONDS_MIN``,
etc.) continue to work unchanged.

This module is import-safe: it does **not** import from
:mod:`voice_typer.server.config`, so it cannot participate in a
circular import.
"""

from __future__ import annotations

from voice_typer.server.config_validators.hotkey import _validate_hotkey
from voice_typer.server.config_validators.language import _validate_language
from voice_typer.server.config_validators.scalar import (
    _MAX_API_KEY_LEN,
    FieldSpec,
    _bool_validator,
    _make_custom_theme_validator,
    _make_enum_validator,
    _make_float_validator,
    _make_int_validator,
    _make_linux_window_buttons_validator,
    _make_optional_float_validator,
    _make_optional_int_validator,
    _make_optional_str_validator,
    _make_str_validator,
    _make_url_validator,
    _validate_trusted_extra_hosts,
)
from voice_typer.server.model_registry import (
    MODEL_REGISTRY as _MODEL_REGISTRY_FOR_ALLOWLIST,
    NO_MODEL_SIZE as _NO_MODEL_SIZE,
)

# canonical bounds + default for ``max_recording_time_seconds``.
MAX_RECORDING_TIME_SECONDS_DEFAULT: int = 900  # 15 minutes
MAX_RECORDING_TIME_SECONDS_MIN: int = 300  # 5 minutes
MAX_RECORDING_TIME_SECONDS_MAX: int = 3600  # 60 minutes

# shared streaming-field minimums (mirrors  pattern).
STREAMING_LEFT_OVERLAP_SECONDS_MIN: float = 3.0
STREAMING_RIGHT_GUARD_SECONDS_MIN: float = 1.5


# ALLOWED_USER_MODELS is DERIVED from

ALLOWED_USER_MODELS: frozenset[str] = frozenset(_MODEL_REGISTRY_FOR_ALLOWLIST.keys())


# canonical noise-suppression backend enum.
NOISE_SUPPRESSION_METHODS: frozenset[str] = frozenset({"rnnoise", "gtcrn", "none"})


# `IPC_CONFIG_ALLOWLIST` is the explicit, reviewed list of fields the

# IPC_CONFIG_ALLOWLIST can reference them by name.  Mirrors the original
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
IPC_CONFIG_ALLOWLIST: dict[str, FieldSpec] = {
    "hotkey": (str, _VALIDATOR_HOTKEY),
    "repaste_hotkey": (str, _VALIDATOR_REPASTE_HOTKEY),
    "microphone": ((str, type(None)), _VALIDATOR_MICROPHONE),
    # ``model_size`` additionally accepts ``NO_MODEL_SIZE`` (""), the
    "model_size": (str, _make_enum_validator(ALLOWED_USER_MODELS | {_NO_MODEL_SIZE})),
    "language": (str, _VALIDATOR_LANGUAGE),
    "device": (str, _make_enum_validator(frozenset({"cuda", "cpu"}))),
    "beam_size": (int, _make_int_validator(lo=1, hi=10)),
    "best_of": (int, _make_int_validator(lo=1, hi=10)),
    # Whisper-specific beam width (preferred over the legacy
    "whisper_beam_size": (int, _make_int_validator(lo=1, hi=10)),
    "condition_on_previous_text": (bool, _bool_validator),
    # Master switch for voice-activity (silence) filtering before
    "vad_filter_enabled": (bool, _bool_validator),
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
    "autostart": (bool, _bool_validator),
    "paste_on_stop": (bool, _bool_validator),
    "unsafe_paste_on_unknown_focus": (bool, _bool_validator),
    "show_notifications": (bool, _bool_validator),
    # prewarm scheduled-task master toggle. Surfaced in Settings →
    "fast_startup": (bool, _bool_validator),
    # offline_pack_consent intentionally NOT allowlisted: pack updates
    # are always-on; renderer must not disable them via set_config.
    # Re-run setup wizard (Settings → Troubleshooting). The renderer
    "onboarding_completed": (bool, _bool_validator),
    # ADR-0010 §2.11 / §8.3a: these keys MUST be in the IPC allowlist
    "clipboard_save_restore": (bool, _bool_validator),
    "clipboard_restore_delay_ms": (int, _make_int_validator(lo=0, hi=2000)),
    # User-configured URL-allowlist extensions for self-hosted
    "trusted_extra_hosts": (list, _VALIDATOR_TRUSTED_HOSTS),
    # idle-unload timer for the active ASR backend. 0 (default)
    "model_idle_unload_minutes": (int, _make_int_validator(lo=0, hi=1440)),
    "asr_backend": (str, _make_enum_validator(frozenset({"whisper", "qwen", "parakeet"}))),
    "text_cleanup_enabled": (bool, _bool_validator),
    "auto_punctuation": (bool, _bool_validator),
    "log_transcriptions": (bool, _bool_validator),
    "recording_mode": (str, _make_enum_validator(frozenset({"toggle", "push_to_talk"}))),
    "esc_cancel_enabled": (bool, _bool_validator),
    "templates_enabled": (bool, _bool_validator),
    "vocabulary_enabled": (bool, _bool_validator),
    # Cloud ASR, secrets and URLs are sensitive but the renderer actively
    "cloud_api_key": (str, _VALIDATOR_API_KEY),
    "cloud_api_url": (str, _VALIDATOR_API_URL),
    "cloud_model": (str, _VALIDATOR_CLOUD_MODEL),
    "openai_api_key": (str, _VALIDATOR_API_KEY),
    "groq_api_key": (str, _VALIDATOR_API_KEY),
    "deepgram_api_key": (str, _VALIDATOR_API_KEY),
    # LLM polish, same rationale as cloud ASR.
    "llm_polish": (bool, _bool_validator),
    "llm_api_key": (str, _VALIDATOR_API_KEY),
    "llm_api_url": (str, _VALIDATOR_LLM_API_URL),
    "llm_model": (str, _VALIDATOR_LLM_MODEL),
    "llm_preset": (str, _make_enum_validator(frozenset({"professional", "casual", "email", "code"}))),
    # Consent flag is user-tunable (the consent dialog
    "llm_polish_consent": (bool, _bool_validator),
    # 006/009: privacy consent flags.  All user-tunable
    "huggingface_consent": (bool, _bool_validator),
    "cloud_openai_consent": (bool, _bool_validator),
    "cloud_groq_consent": (bool, _bool_validator),
    "cloud_deepgram_consent": (bool, _bool_validator),
    "voice_biometric_consent": (bool, _bool_validator),
    # ADR-0023: consent to send media URLs to the yt-dlp extractor.
    "media_url_consent": (bool, _bool_validator),
    # sound feedback toggle.
    "sound_feedback_enabled": (bool, _bool_validator),
    # Volume multiplier for the renderer's sound-feedback cues
    "sound_volume": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "crash_recovery_enabled": (bool, _bool_validator),
    "audio_quality_warnings": (bool, _bool_validator),
    # All four toggles are user-tunable via Settings → AI Enhancement.
    "ai_enhancement_enabled": (bool, _bool_validator),
    "auto_capitalize": (bool, _bool_validator),
    "auto_punctuate": (bool, _bool_validator),
    "fix_grammar_basics": (bool, _bool_validator),
    # Master toggle + two float thresholds.  The confidence threshold
    "vocabulary_automation_enabled": (bool, _bool_validator),
    "vocabulary_auto_confidence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "vocabulary_auto_apply_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "waveform_bubble": (bool, _bool_validator),
    "bubble_position": (str, _make_enum_validator(frozenset({"top", "bottom"}))),
    "bubble_behavior": (str, _make_enum_validator(frozenset({"show_on_record", "always_visible"}))),
    "bubble_draggable": (bool, _bool_validator),
    "bubble_show_on_startup": (bool, _bool_validator),
    # mic button + click-to-toggle for the always-visible bubble.
    "bubble_click_to_toggle": (bool, _bool_validator),
    "bubble_mic_button": (bool, _bool_validator),
    # Persisted bubble window position (screen-space pixel coords).
    "bubble_x": ((int, type(None)), _make_optional_int_validator(lo=-100_000, hi=100_000)),
    "bubble_y": ((int, type(None)), _make_optional_int_validator(lo=-100_000, hi=100_000)),
    # Persisted bubble scale factor (multiplier on the base DPI).
    "bubble_scale": ((float, type(None)), _make_optional_float_validator(lo=0.5, hi=3.0)),
    # Persisted microphone-test duration (seconds). Range [1, 60] —
    "test_duration_seconds": ((int, type(None)), _make_optional_int_validator(lo=1, hi=60)),
    # ``history_enabled`` is the master toggle for whether dictated
    "history_enabled": (bool, _bool_validator),
    "history_retention_days": (int, _make_int_validator(lo=0, hi=36500)),
    "history_retention_count": (int, _make_int_validator(lo=0, hi=1_000_000)),
    "history_max_entries": (int, _make_int_validator(lo=0, hi=1_000_000)),
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
    "custom_theme": ((dict, type(None)), _make_custom_theme_validator()),
    # Linux title-bar window-button customization (Settings → Appearance,
    "linux_window_buttons": (dict, _make_linux_window_buttons_validator()),
    "text_size": (int, _make_int_validator(lo=8, hi=72)),
    "silence_warning_seconds": (float, _make_float_validator(lo=0.0, hi=600.0)),
    "stop_on_silence_seconds": (float, _make_float_validator(lo=0.0, hi=3600.0)),
    # lower bound lowered from 300 to 30 (the prior 5-minute
    "max_recording_time_seconds": (
        int,
        _make_int_validator(
            lo=MAX_RECORDING_TIME_SECONDS_MIN,
            hi=MAX_RECORDING_TIME_SECONDS_MAX,
        ),
    ),
    # silence_rms_threshold / silence_peak_threshold REMOVED from
    "use_silero_vad": (bool, _bool_validator),
    "vad_speech_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "vad_silence_threshold": (float, _make_float_validator(lo=0.0, hi=1.0)),
    # Auto-calibrate VAD thresholds from ambient noise. Must stay
    "vad_auto_calibrate": (bool, _bool_validator),
    # AUDIO-CH: recording channels (: lower bound raised from
    "recording_channels": (int, _make_int_validator(lo=1, hi=8)),
    # AUDIO-PRE: pre-roll buffer
    "pre_roll_buffer_seconds": (float, _make_float_validator(lo=0.0, hi=30.0)),
    # normalize_audio / normalize_target_peak REMOVED from the IPC
    "warn_elevated_paste": (bool, _bool_validator),
    "warn_password_paste": (bool, _bool_validator),
    "volume_duck_enabled": (bool, _bool_validator),
    "volume_duck_level": (float, _make_float_validator(lo=0.0, hi=1.0)),
    "volume_duck_fade_ms": (int, _make_int_validator(lo=0, hi=1000)),
    "volume_duck_smart_poll_interval_ms": (int, _make_int_validator(lo=50, hi=5000)),
    # (partial): legacy aliases ``"none"`` and ``"recommended"``
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
    # Removed deprecated fields: noise_filter_enabled,
    "noise_filter_highpass": (bool, _bool_validator),
    "noise_filter_highpass_cutoff_hz": (float, _make_float_validator(lo=20.0, hi=500.0)),
    "noise_filter_gate": (bool, _bool_validator),
    "noise_filter_gate_hold_ms": (float, _make_float_validator(lo=0.0, hi=1000.0)),
    # ADR 0007 §5.1: New filter chain fields
    "noise_suppression_method": (str, _make_enum_validator(NOISE_SUPPRESSION_METHODS)),
    "noise_filter_gate_open_threshold_db": (float, _make_float_validator(lo=-96.0, hi=0.0)),
    "noise_filter_gate_close_threshold_db": (float, _make_float_validator(lo=-96.0, hi=0.0)),
    "noise_filter_gate_attack_ms": (float, _make_float_validator(lo=0.0, hi=10000.0)),
    "noise_filter_gate_release_ms": (float, _make_float_validator(lo=0.0, hi=10000.0)),
    # Adaptive noise-floor calibration for the NoiseGate (see
    "noise_filter_gate_adaptive": (bool, _bool_validator),
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
