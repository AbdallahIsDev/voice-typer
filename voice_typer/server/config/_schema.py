"""Config schema: the ``_ConfigSchema`` dataclass base + schema impls."""

import logging
import types
from dataclasses import dataclass, field
from typing import ClassVar, Literal, cast

from voice_typer.server._audio_constants import (
    _DEFAULT_SMART_DUCK_POLL_MS,
    WHISPER_SAMPLE_RATE,
)
from voice_typer.server._paths import DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL
from voice_typer.server.config._defaults import (
    DEFAULT_CLIPBOARD_RESTORE_DELAY_MS,
    _default_hotkey_for_platform,
)
from voice_typer.server.config_internals.migrations import _CURRENT_SCHEMA_VERSION
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

log = logging.getLogger("voice_typer.server.config")

# High-impact ``Literal[...]`` enum fields whose invalid values
_ENUM_FIELDS_TO_RESET_ON_LOAD: frozenset[str] = frozenset(
    {
        "asr_backend",
        "noise_suppression_method",
        "audio_preset",
        "theme_mode",
        "theme_preset",
        "bubble_position",
        "bubble_behavior",
        "tray_left_click_action",
        "recording_mode",
    }
)

# The set of Config dataclass field names that hold secret material
_SECRET_FIELD_NAMES_FALLBACK: frozenset[str] = frozenset(
    {
        "openai_api_key",
        "groq_api_key",
        "deepgram_api_key",
        "cloud_api_key",
        "llm_api_key",
    }
)


@dataclass
class _ConfigSchema:
    """Dataclass base holding ALL ``Config`` field declarations."""

    schema_version: int = _CURRENT_SCHEMA_VERSION
    # ``last_load_warnings`` was previously a

    # marks that plaintext API keys in config.json have been
    secrets_migrated: bool = False

    # Hotkey
    hotkey: str = _default_hotkey_for_platform()

    # Recording
    sample_rate: int = WHISPER_SAMPLE_RATE
    microphone: str | None = None  # None = system default

    # Transcription
    model_size: str = DEFAULT_MODEL_SIZE
    language: str = "en"
    device: str = "cuda"  # cuda, cpu
    beam_size: int = 1  # 1 = fastest greedy decoding; higher values trade speed for accuracy
    best_of: int = 1
    condition_on_previous_text: bool = False
    # In the SEC-002 IPC allowlist so the Settings UI toggle persists it.
    vad_filter_enabled: bool = True
    # Whisper-specific beam size override. Defaults to 1 (matching the
    whisper_beam_size: int = 1

    # Hidden streaming transcription
    streaming_transcription: bool = True
    streaming_chunk_seconds: float = 12.0
    streaming_step_seconds: float = 5.0
    streaming_left_overlap_seconds: float = 3.0
    streaming_right_guard_seconds: float = 1.5
    streaming_min_first_chunk_seconds: float = 6.0
    streaming_silence_threshold: float = 0.003

    # Behavior
    autostart: bool = True
    paste_on_stop: bool = True
    # client-side field now has a server counterpart
    unsafe_paste_on_unknown_focus: bool = False  # paste even when focus detection fails
    show_notifications: bool = True
    # warn when pasting into an elevated process from non-elevated
    warn_elevated_paste: bool = True
    # warn when pasting into a password field
    warn_password_paste: bool = True
    # Master toggle for the OS-level prewarm scheduled task.
    fast_startup: bool = True
    # Always-on pack auto-update (user product decision): no Settings
    # toggle, not in SEC-002 IPC allowlist, load path forces True.
    offline_pack_consent: bool = True

    # ASR backend selection
    asr_backend: Literal["whisper", "qwen", "parakeet"] = "whisper"
    qwen_model_path: str | None = None  # local path to Qwen3-ASR weights
    parakeet_model_path: str | None = None  # local override for Parakeet weights (None = HF cache)

    # re-enabled). NOT in ``IPC_CONFIG_ALLOWLIST`` because it is
    disabled_backends: list[str] = field(default_factory=list)

    # User-configured URL-allowlist extensions for self-hosted
    trusted_extra_hosts: list[str] = field(default_factory=list)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: str | None = None

    # Logging
    log_transcriptions: bool = False

    # Clipboard security settings.
    clipboard_save_restore: bool = True  # save/restore previous clipboard content after paste
    clipboard_restore_delay_ms: int = (
        DEFAULT_CLIPBOARD_RESTORE_DELAY_MS  # delay between paste keystroke and clipboard restore (ms)
    )

    # Push-to-talk mode (hold to record, release to stop)
    recording_mode: Literal["toggle", "push_to_talk"] = "toggle"

    # ESC to cancel at any stage
    esc_cancel_enabled: bool = True

    # Repaste last transcription
    repaste_hotkey: str = "<ctrl>+<alt>+v"  # Hotkey for repasting last

    # Auto-punctuation (runs AFTER template matching)
    auto_punctuation: bool = True

    # Templates
    templates_enabled: bool = True

    # Vocabulary
    vocabulary_enabled: bool = True

    # Cloud ASR backends
    cloud_api_key: str = ""
    cloud_api_url: str = ""
    cloud_model: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    deepgram_api_key: str = ""

    # LLM text polishing
    llm_polish: bool = False
    llm_api_key: str = ""
    llm_api_url: str = DEFAULT_LLM_API_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_preset: str = "professional"  # professional/casual/email/code

    # Explicit user consent that text may leave the
    llm_polish_consent: bool = False

    # explicit consent that model weights are downloaded
    huggingface_consent: bool = False

    # explicit per-provider consent for cloud ASR.
    cloud_openai_consent: bool = False
    cloud_groq_consent: bool = False
    cloud_deepgram_consent: bool = False

    # explicit consent that voice recordings (which may
    voice_biometric_consent: bool = False

    # play a short audio cue when recording starts/stops.
    sound_feedback_enabled: bool = True

    # volume multiplier applied to the renderer's sound-feedback cues
    sound_volume: float = 1.0

    # Crash recovery
    crash_recovery_enabled: bool = True

    # Superseded: an earlier draft removed AudioQualityAnalyzer as
    audio_quality_warnings: bool = False

    # Waveform visualization bubble
    waveform_bubble: bool = False

    # Bubble screen position (top / bottom).  Default "bottom", the
    bubble_position: Literal["top", "bottom"] = "bottom"

    # Bubble behavior: show on record, or always visible
    bubble_behavior: Literal["show_on_record", "always_visible"] = "show_on_record"

    # Whether the bubble can be dragged by the user
    bubble_draggable: bool = True

    # Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
    bubble_show_on_startup: bool = True

    # when in `always_visible` mode, show a mic button next to the
    bubble_click_to_toggle: bool = True

    # explicit mic-button visibility toggle (independent of
    bubble_mic_button: bool = True

    # Persisted bubble window position (screen-space pixel coords) and
    bubble_x: int | None = None
    bubble_y: int | None = None
    bubble_scale: float | None = None

    # Persisted microphone-test duration (seconds). The Microphone
    test_duration_seconds: int | None = None

    # History database
    history_enabled: bool = True
    history_retention_days: int = 90  # 0 = keep forever
    history_retention_count: int = 0  # 0 = unlimited
    history_max_entries: int = 1000

    # Onboarding
    onboarding_completed: bool = False
    # marks that onboarding was force-completed after repeated
    onboarding_failed: bool = False

    # Tray icon left-click behavior
    tray_left_click_action: Literal["open_app", "toggle_dictation"] = "open_app"

    # Theme mode (system/light/dark)
    theme_mode: Literal["system", "light", "dark"] = "system"
    # Theme preset, a built-in colour scheme applied on top of the
    theme_preset: Literal[
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
    ] = "default"
    # User-customised theme colours (only used when theme_preset == "custom").
    custom_theme: dict[str, dict[str, str]] | None = None

    # Linux title-bar window-button customization (Settings → Appearance).
    linux_window_buttons: dict[str, object] = field(
        default_factory=lambda: cast(
            "dict[str, object]",
            {
                "mode": "system",
                "side": "right",
                "show_minimize": True,
                "show_maximize": True,
                "show_close": True,
            },
        )
    )

    # Accessibility
    text_size: int = 14

    # Wayland hotkey fallback warning
    wayland_warned: bool = False

    # Silent mic disconnection
    silence_warning_seconds: float = 20.0
    stop_on_silence_seconds: float = 60.0
    # Single explicit field replaces the previous 3-field split
    max_recording_time_seconds: int = 900  # 15 minutes

    # NOTE: dead_air_timeout (float) was REMOVED in

    # silence_rms_threshold / silence_peak_threshold were REMOVED

    # Idle-unload timer for the active ASR backend. After this
    model_idle_unload_minutes: int = 30

    # VAD configuration for the recording callback.
    use_silero_vad: bool = True  # ADR 0007: was False, now True (torch available)
    vad_speech_threshold: float = 0.5  # Silero VAD prob > this → speech candidate
    vad_silence_threshold: float = 0.3  # Silero VAD prob < this → silence candidate
    # Auto-calibrate VAD thresholds from the ambient noise floor
    vad_auto_calibrate: bool = False

    # AUDIO-CH: number of channels to request from the input device.
    recording_channels: int = 1

    # AUDIO-PRE: pre-roll buffer captures audio before recording starts.
    pre_roll_buffer_seconds: float = 0.0

    # ADR 0007 §5.2: normalize_audio and normalize_target_peak REMOVED.

    # Reduces system volume during dictation to prevent speaker output
    volume_duck_enabled: bool = True
    volume_duck_level: float = 0.20  # 0.0–1.0 perceptual-linear (20% duck)
    #  ``volume_duck_per_session`` REMOVED from the Config
    volume_duck_fade_ms: int = 200  # 0–1000, 0 = instant
    #  ``volume_duck_smart`` REMOVED from the Config dataclass —
    volume_duck_smart_poll_interval_ms: int = _DEFAULT_SMART_DUCK_POLL_MS

    # Preset name that controls the entire filter chain:
    audio_preset: Literal[
        "auto",
        "studio",
        "noisy_room",
        "off",
        "custom",
        "none",
        "recommended",
    ] = "auto"

    # Each filter has an enable flag + parameters. The filter chain
    noise_filter_enabled: bool = True  # runtime switch, see ADR 0009
    noise_filter_highpass: bool = True
    noise_filter_highpass_cutoff_hz: float = 80.0  # 20–500
    noise_filter_gate: bool = True
    # ``noise_filter_gate_threshold`` REMOVED from the Config
    noise_filter_gate_hold_ms: float = 200.0  # ADR 0007: was 150, now 200 (matches OBS)
    noise_filter_rnnoise: bool = True  # ADR 0007: was False, now True (RNNoise is default dep)
    noise_filter_post_capture: bool = True  # runtime switch, see ADR 0009

    # ADR 0007 §5.1: New filter chain fields
    noise_suppression_method: Literal["rnnoise", "gtcrn", "none"] = "rnnoise"

    # NoiseGate (OBS-style, replaces single threshold)
    noise_filter_gate_open_threshold_db: float = -26.0
    noise_filter_gate_close_threshold_db: float = -32.0
    noise_filter_gate_attack_ms: float = 25.0
    noise_filter_gate_release_ms: float = 150.0
    # when True, gate samples the first ~500ms of audio to estimate
    noise_filter_gate_adaptive: bool = False

    # Equalizer (3-band)
    noise_filter_eq: bool = True
    noise_filter_eq_low_db: float = -3.0
    noise_filter_eq_mid_db: float = 3.0
    noise_filter_eq_high_db: float = 2.0

    # Compressor (replaces normalize_audio + _agc_update)
    noise_filter_compressor: bool = True
    noise_filter_compressor_threshold_db: float = -18.0
    noise_filter_compressor_ratio: float = 3.0
    noise_filter_compressor_attack_ms: float = 6.0
    noise_filter_compressor_release_ms: float = 60.0
    noise_filter_compressor_output_gain_db: float = 0.0

    # Limiter (brick-wall)
    noise_filter_limiter: bool = True
    noise_filter_limiter_ceiling_db: float = -6.0
    noise_filter_limiter_release_ms: float = 60.0

    # Notch filter (50/60Hz hum), optional, default OFF
    noise_filter_notch: bool = False
    noise_filter_notch_frequency_hz: float = 0.0  # 0 = auto-detect (60Hz Americas default)

    # Rule-based, offline enhancement applied AFTER LLM polish and
    ai_enhancement_enabled: bool = False  # master toggle (opt-in)
    auto_capitalize: bool = True  # capitalize sentence starts + proper nouns
    auto_punctuate: bool = True  # add periods at sentence boundaries
    fix_grammar_basics: bool = True  # fix bare "i", contractions, double spaces

    # Confidence-score-based auto-correction suggestions.  When the
    vocabulary_automation_enabled: bool = False  # master toggle (opt-in)
    # Below this segment-confidence, suggest corrections.  0.7 is a
    vocabulary_auto_confidence_threshold: float = 0.7
    # Above this confidence, auto-apply suggestions without asking.
    vocabulary_auto_apply_threshold: float = 0.95

    # ``ClassVar`` bindings of the module-level constants above —
    _ENUM_FIELDS_TO_RESET_ON_LOAD: ClassVar[frozenset[str]] = _ENUM_FIELDS_TO_RESET_ON_LOAD
    _SECRET_FIELD_NAMES_FALLBACK: ClassVar[frozenset[str]] = _SECRET_FIELD_NAMES_FALLBACK


def _reset_invalid_enum_fields_impl(cls, instance) -> None:
    """Reset invalid ``Literal[...]`` enum fields to their defaults.

    Module-level impl behind the ``Config._reset_invalid_enum_fields``
    classmethod delegator (``config/_lifecycle.py``).

    ``validate_config(instance)`` (called from :meth:`load` just
    before this helper) flags invalid enum values and appends
    human-readable errors to ``instance.last_load_warnings``, but
    it does NOT mutate the field, the invalid value remains on
    the instance and propagates to runtime code, which either
    crashes (KeyError in a dispatch dict) or silently takes the
    wrong branch.

    This helper closes that gap. For each field in
    :data:`_ENUM_FIELDS_TO_RESET_ON_LOAD`:

    1. Look up the field's ``Literal[...]`` annotation via
       :func:`typing.get_type_hints`.
    2. Read the current value from ``instance`` via ``getattr``.
    3. If the value is not in the Literal's allowed set (via
       :func:`typing.get_args`), reset to the default from a
       freshly-constructed ``Config()`` and append a warning to
       ``instance.last_load_warnings``.

    Non-str values (e.g. a hand-edited ``"asr_backend": 123``)
    are also reset, they can never be in a ``Literal[str, ...]``
    allowed set. The ``_validate_non_numeric_fields`` pre-pass
    normally coerces such values to ``str`` first, but this
    helper is defensive against a value that slipped through
    (e.g. a complex type that the str branch didn't catch).

    The reset is idempotent: a value already at the default is a
    no-op (it's in the allowed set). The reset is also safe to
    re-run, calling it twice produces no extra warnings.

    Warnings are appended to ``instance.last_load_warnings`` (NOT
    ``data["_load_warnings"]``, which has already been popped and
    transferred to the instance by the time this runs, see the
    :meth:`load` orchestrator). The warning text mirrors the
    format used by the per-field reset helpers
    (``_validate_model_path`` etc.) so the renderer can display
    them with the same UI treatment.
    """
    import typing

    try:
        hints = typing.get_type_hints(cls)
    except Exception:
        # ``typing.get_type_hints`` resolves forward refs and can
        hints = dict(getattr(cls, "__annotations__", {}))

    # Build the defaults instance ONCE (not per-field), Config()
    defaults = cls()

    for field_name in cls._ENUM_FIELDS_TO_RESET_ON_LOAD:
        ann = hints.get(field_name)
        if ann is None:
            # Field was removed or renamed, skip silently (the
            continue
        # Unwrap ``T | None`` / ``Optional[T]``, none of the 9
        if typing.get_origin(ann) in (typing.Union, types.UnionType):
            args = [a for a in typing.get_args(ann) if a is not type(None)]
            if len(args) == 1:
                ann = args[0]
        if typing.get_origin(ann) is not typing.Literal:
            # Field's annotation isn't a Literal (e.g. it was
            continue
        allowed = set(typing.get_args(ann))
        current = getattr(instance, field_name, None)
        if current in allowed:
            continue
        default_value = getattr(defaults, field_name)
        # Defensive: if the default ITSELF isn't in the allowed
        if default_value not in allowed and allowed:
            default_value = sorted(allowed)[0]
        log.warning(
            "[CONFIG] %s=%r not in Literal allowed values %s; resetting to default %r",
            field_name,
            current,
            sorted(allowed),
            default_value,
        )
        # Use ``object.__setattr__`` to mirror the ``__post_init__``
        object.__setattr__(instance, field_name, default_value)
        # Append to ``last_load_warnings``: initialize the list
        warnings = getattr(instance, "last_load_warnings", None)
        if warnings is None:
            warnings = []
            object.__setattr__(instance, "last_load_warnings", warnings)
        warnings.append(
            f"Config field {field_name!r}={current!r} not in allowed values "
            f"{sorted(allowed)}, reset to default {default_value!r}"
        )


def _secret_field_names_impl() -> frozenset[str]:
    """return the set of Config field names holding secrets.

    Module-level impl behind the ``Config._secret_field_names``
    classmethod delegator (``config/_lifecycle.py``).

    Lazily imports ``credential_store.PROVIDER_TO_CONFIG_FIELD``
    (the canonical provider→field map) so the secret-field list
    stays in sync with the credential-store definition.

    SECURITY (fail-closed): if the import of
    ``PROVIDER_TO_CONFIG_FIELD`` fails for ANY reason (broken
    install, sandbox without the package, partial-import during
    test collection, future refactor that breaks the import path),
    we log ``CRITICAL`` and RE-RAISE. We do NOT fall back to the
    historical ``_SECRET_FIELD_NAMES_FALLBACK`` literal: a silent
    fallback to a stale 5-field set would leave any newly added
    provider's API key un-redacted in ``_warn_and_reset`` /
    ``_warn_and_coerce`` log lines (``val_repr = repr(val)``)
    whenever the fallback kicks in (SEC-003 regression analog).
    Failing the import loudly surfaces the breakage at the first
    call site (typically ``Config.load()`` redaction), which is
    strictly safer than silently degrading the redaction
    boundary. Mirrors the fail-closed pattern in
    ``voice_typer.server.config_sanitizer._derive_secret_fields``
    so the two paths handle the SAME failure identically.
    """
    try:
        from voice_typer.server import credential_store

        return frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.values())
    except Exception as exc:
        # Fail-closed: do NOT fall back to the hardcoded
        log.critical(
            "[CONFIG] could not import credential_store for "
            "_secret_field_names, secret-field redaction may be "
            "incomplete. Refusing to fall back to a hardcoded "
            "literal (fail-closed). Original error: %s",
            exc,
        )
        raise
