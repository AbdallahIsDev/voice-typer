"""Config schema: the ``_ConfigSchema`` dataclass base + schema impls.

Continuation of the config monolith split: ALL ``Config``
dataclass field declarations live here (moved verbatim from
``config/__init__.py``), on a ``@dataclass`` base class named
``_ConfigSchema``. The final ``Config`` class in
``config/__init__.py`` combines this base with the
``_ConfigLifecycleMixin`` (``config/_lifecycle.py``) via multiple
inheritance — the ``@dataclass`` decorator on ``Config`` picks up the
inherited field declarations via ``__dataclass_fields__`` so callers
see the same public API (``Config(schema_version=1, hotkey='x', ...)``).

This module also hosts the schema-adjacent implementation helpers:

- ``_ENUM_FIELDS_TO_RESET_ON_LOAD`` / ``_SECRET_FIELD_NAMES_FALLBACK``
  (ClassVars on the base class),
- ``_reset_invalid_enum_fields_impl`` (the load-time enum reset),
- ``_secret_field_names_impl`` (the fail-closed secret-field lookup).

Import-safety: this module is imported at the TOP of
``config/__init__.py`` (via the lifecycle mixin and the re-export
block). It must NOT import from ``voice_typer.server.config``
(circular) — every heavy consumer (``credential_store``,
``config_validators.validate_config``, ...) is imported lazily inside
the function bodies below.
"""

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
# should be reset to defaults on load (rather than merely warned
# about). These are the user-facing enum choices that drive
# discrete runtime behavior branches (ASR backend selection,
# recording mode, bubble placement, tray click action, theme,
# audio preset, noise suppressor). An invalid value here causes
# downstream code to either crash (KeyError in a dispatch dict) or
# silently take the wrong branch (a stale ``"speex"`` value for
# ``noise_suppression_method`` would fall through the
# ``noise_suppressor.py`` dispatch and produce no filter at all).
#
# The set is intentionally a hardcoded allowlist rather than
# "every Literal field on Config" — ``audio_preset`` is the one
# Literal field whose Literal ALSO includes the legacy
# ``"none"`` / ``"recommended"`` values (kept for static-typing
# backward-compat with pre-migration config.json). The migration
# already rewrites those before this reset runs, but to be safe we
# only reset fields explicitly in this list and rely on the
# Literal's own allowed-values set for the truth — so if
# ``audio_preset="none"`` somehow survives migration, this reset
# will NOT touch it (the migration handles it; touching it here
# would mask a migration bug).
#
# Bound as a ``ClassVar`` on :class:`_ConfigSchema` below AND
# re-exported at module level (``config/__init__.py`` imports it
# from here so the legacy ``from voice_typer.server.config import
# _ENUM_FIELDS_TO_RESET_ON_LOAD`` path keeps resolving).
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
# (API keys / tokens). Used by ``_warn_and_reset`` to redact ``val``
# before logging so a malformed-on-disk api_key value doesn't get
# echoed into log files at WARNING level.
#
# (fail-closed): this historical fallback literal (a 5-field hardcoded
# set) is RETAINED for parity assertions in tests, but
# :func:`_secret_field_names_impl` NO LONGER returns it on import
# failure — it logs ``CRITICAL`` and RE-RAISES instead. A silent
# fallback to a stale literal would leave any newly added provider's
# API key un-redacted in warning log lines whenever the fallback kicks
# in — a security degradation. Failing loudly surfaces the breakage at
# the first call site (typically ``Config.load()`` redaction).
#
# Bound as a ``ClassVar`` on :class:`_ConfigSchema` below AND
# re-exported at module level (same reason as
# ``_ENUM_FIELDS_TO_RESET_ON_LOAD`` above).
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
    """Dataclass base holding ALL ``Config`` field declarations.

    The field declarations below were moved verbatim from the
    pre-split ``config/__init__.py`` monolith. ``Config`` (in
    ``config/__init__.py``) inherits them via
    ``class Config(_ConfigSchema, _ConfigLifecycleMixin)`` — the
    ``@dataclass`` decorator on ``Config`` merges the inherited
    ``__dataclass_fields__`` so ``Config(**kwargs)`` /
    ``dataclasses.asdict(cfg)`` behave exactly as before the split.

    The two ``ClassVar`` attributes are NOT fields (``asdict`` skips
    them) — they are schema-adjacent constants consumed by the
    load-time helpers (``_reset_invalid_enum_fields_impl`` /
    ``_warn_and_reset`` redaction).
    """

    schema_version: int = _CURRENT_SCHEMA_VERSION
    # SCHEMA-1 (MED-I): ``last_load_warnings`` was previously a
    # dataclass field, which meant ``asdict(self)`` (used by ``save()``)
    # serialized it into ``config.json``.  On the next load the stale
    # warnings would be read back as if they applied to THIS load,
    # producing a confusing "your config was corrected" notice for a
    # problem that no longer exists.  It's now a plain instance
    # attribute set in :meth:`load` (and ``__post_init__``) — since
    # ``asdict()`` only serializes declared dataclass fields, the
    # attribute is excluded from ``config.json`` automatically.

    # marks that plaintext API keys in config.json have been
    # migrated to the OS keychain (via credential_store). When False
    # (or absent, for legacy config files), Config.load() calls
    # ``credential_store.migrate_secrets_to_keyring()`` once to move
    # any plaintext keys to keyring and replace them with
    # ``keyring://<provider>`` reference tokens. The flag is then set
    # to True so the migration doesn't run again on every launch
    # (idempotent — see credential_store.migrate_secrets_to_keyring).
    secrets_migrated: bool = False

    # Hotkey
    # NATIVE-001 / FIX-HOTKEY-ARCHITECTURE: default hotkey is now
    # ``<caps_lock>`` on ALL platforms (was previously <fn> on macOS
    # and <f2> on unknown platforms). Caps Lock is universally present,
    # isolated, and easy to remap. See ``_default_hotkey_for_platform``
    # for platform-specific suppression notes.
    hotkey: str = _default_hotkey_for_platform()

    # Recording
    sample_rate: int = WHISPER_SAMPLE_RATE
    microphone: str | None = None  # None = system default

    # Transcription
    # Default comes from the canonical ``DEFAULT_MODEL_SIZE`` constant
    # in ``model_registry.py`` — change the default in ONE place there.
    model_size: str = DEFAULT_MODEL_SIZE
    language: str = "en"
    device: str = "cuda"  # cuda, cpu
    beam_size: int = 1  # 1 = fastest greedy decoding; higher values trade speed for accuracy
    best_of: int = 1
    condition_on_previous_text: bool = False
    # Whisper-specific beam size override. Defaults to 1 (matching the
    # legacy ``beam_size`` field above) for backwards compat — existing
    # config files without this key continue to behave identically.
    # When set to a non-default value (e.g. 3 or 5),
    # ``TranscriptionEngine.__init__`` picks it up via the ``config``
    # object and uses it for the ``beam_size`` argument passed to
    # ``model.transcribe(...)`` (see ``_transcribe_unlocked`` /
    # ``_transcribe_words_unlocked`` / ``_probe_cuda_runtime``).
    #
    # WER (word error rate) tradeoff: ``beam_size=1`` (greedy decoding)
    # is ~1-3% worse than ``beam_size=3-5`` on common benchmarks
    # (LibriSpeech, Common Voice), but ~2x faster on commodity
    # hardware. The speed-biased default of 1 keeps transcription
    # snappy on CPU and low-end GPUs; users who prioritise accuracy
    # over latency can bump this to 3 or 5.
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
    # Defaults ON so existing users keep fast cold-boot behaviour.
    # When False, the prewarm task is unregistered at startup and the
    # prewarm entrypoint exits early with EXIT_DISABLED. The "Run
    # Prewarm Now" button in the About page remains usable for
    # on-demand warming even when scheduled prewarm is disabled.
    fast_startup: bool = True
    # Auto-update feature (docs/auto-update-feature.md §8.4): user
    # opt-in for the offline-pack background download from GitHub
    # Releases. Defaults OFF — the pack is never downloaded without
    # explicit consent (C-DATA-1 category-3 model-download consent
    # gate; ``check_offline_pack_update`` refuses to start the download
    # when this is False). In the SEC-002 IPC allowlist so the Settings
    # UI toggle can persist it. Renamed from ``runtime_pack_consent``
    # on 2026-08-14 (the legacy key is migrated in ``config/loader.py``).
    offline_pack_consent: bool = False

    # ASR backend selection
    # ``Literal[...]`` instead of bare ``str`` so static
    # checkers catch typos and the IPC validator can cross-check the
    # allowed values against the type annotation.  ``Literal`` is a
    # subtype of ``str``, so existing string assignments and JSON
    # round-tripping remain backward-compatible.
    asr_backend: Literal["whisper", "qwen", "parakeet"] = "whisper"
    qwen_model_path: str | None = None  # local path to Qwen3-ASR weights
    parakeet_model_path: str | None = None  # local override for Parakeet weights (None = HF cache)

    # list of ASR backend names the registry's circuit breaker
    # has disabled after repeated load failures. The registry
    # (``asr_registry.AsrBackendRegistry``) self-manages this list via
    # ``_persist_disabled``. Persisted to ``config.json`` so disabled
    # backends survive a restart (previously: the field was missing
    # from the dataclass, so ``asdict(self)`` skipped it and the list
    # reset to empty on every app launch — disabled backends silently
    # re-enabled). NOT in ``IPC_CONFIG_ALLOWLIST`` because it is
    # backend-managed state, not a renderer-writable setting.
    disabled_backends: list[str] = field(default_factory=list)

    # User-configured URL-allowlist extensions for self-hosted
    # LLM/ASR endpoints on non-loopback hosts (e.g. ``my-vllm.lan``).
    # Hostnames are normalized (lowercase, port stripped) and fed into
    # ``_secrets.extend_url_allowlist`` on every ``Config.load()`` and on
    # ``set_config`` (see ``Config.load`` + the ``config_handlers``
    # mixin). Hosts remain subject to the SSRF IP-literal blocklist and
    # the DNS-rebinding check in ``_secrets.assert_url_allowed``.
    trusted_extra_hosts: list[str] = field(default_factory=list)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: str | None = None

    # Logging
    log_transcriptions: bool = False

    # Clipboard security settings.
    # ADR-0010 §8.2: removed ``clipboard_clear_delay_seconds`` (dead —
    # was only read by the now-deleted ``schedule_clipboard_clear``).
    # Added ``clipboard_restore_delay_ms`` (now actually consulted in
    # ``clipboard.py:paste()`` and refreshed at runtime via
    # ``refresh_config()`` when the user changes settings).
    clipboard_save_restore: bool = True  # save/restore previous clipboard content after paste
    clipboard_restore_delay_ms: int = (
        DEFAULT_CLIPBOARD_RESTORE_DELAY_MS  # delay between paste keystroke and clipboard restore (ms)
    )

    # ─── P1 Features ───────────────────────────────────────────────

    # Push-to-talk mode (hold to record, release to stop)
    recording_mode: Literal["toggle", "push_to_talk"] = "toggle"

    # ESC to cancel at any stage
    # Esc-to-cancel defaults ON so users can cancel a
    # recording they started by mistake.  Previously OFF and hidden in
    # Settings, so the only way to cancel was to wait for silence
    # auto-stop or toggle the hotkey again.
    esc_cancel_enabled: bool = True

    # Repaste last transcription
    repaste_hotkey: str = "<ctrl>+<alt>+v"  # Hotkey for repasting last

    # Auto-punctuation (runs AFTER template matching)
    # Auto-punctuation defaults ON.  The #1 voice-typing
    # complaint is missing punctuation.  This feature adds periods,
    # commas, and capitalization automatically.  Previously OFF and
    # undocumented in-app.
    auto_punctuation: bool = True

    # ─── P2 Features ───────────────────────────────────────────────

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

    # PRIVACY-001: explicit user consent that text may leave the
    # machine for LLM polishing.  Separate from ``llm_polish`` so that
    # turning the toggle off doesn't silently revoke consent (and
    # turning it back on doesn't bypass the consent dialog).
    llm_polish_consent: bool = False

    # explicit consent that model weights are downloaded
    # from HuggingFace on first use.  The download reveals the user's
    # IP to a US-headquartered third party — GDPR Art. 13/44 require
    # disclosure + consent for this.  When False, the first model
    # download shows a consent dialog in the renderer; only after the
    # user accepts does the download proceed.
    huggingface_consent: bool = False

    # explicit per-provider consent for cloud ASR.
    # Storing an API key alone is NOT consent — the user must
    # explicitly agree that audio will be sent to that provider.
    # Each provider has its own flag so consent is granular.
    cloud_openai_consent: bool = False
    cloud_groq_consent: bool = False
    cloud_deepgram_consent: bool = False

    # explicit consent that voice recordings (which may
    # constitute biometric data under BIPA / GDPR Art. 9) are
    # processed locally for transcription.  Required for compliance
    # in jurisdictions that classify voice as biometric.
    voice_biometric_consent: bool = False

    # play a short audio cue when recording starts/stops.
    # Many users (especially blind users) prefer an auditory signal
    # instead of (or in addition to) the visual indicator.  Default
    # ON — most users benefit from the audible start/stop cue; those
    # who prefer silence can disable it in Settings → Behavior.
    sound_feedback_enabled: bool = True

    # volume multiplier applied to the renderer's sound-feedback cues
    # (see ``voice_typer/client/src/renderer/src/lib/sound-manager.ts``).
    # 1.0 = the cues' baked-in level; 0.0 = silent.  Validated at the
    # IPC boundary to [0.0, 1.0] and surfaced in Settings → Recording
    # next to the Sound Feedback toggle (volume slider + Test Sound
    # preview button).
    sound_volume: float = 1.0

    # Crash recovery
    crash_recovery_enabled: bool = True

    # Superseded: an earlier draft removed AudioQualityAnalyzer as
    # dead code and archived a stale copy to archive/. The analyzer was
    # subsequently revived and is actively used — see app.py:208
    # (instantiation), app.py:_on_audio_quality_chunk and
    # _finalize_audio_quality_report (per-chunk + post-stop analysis),
    # and recording_controller.py:403 (invocation after stop()).
    # the user-facing tray notification that
    # reported "Low volume / High noise" after each dictation was deemed
    # annoying. The default is now False, AND the app-side code path that
    # shows the notification is short-circuited (see
    # ``_finalize_audio_quality_report`` in app.py — early return at the
    # top so no tray notification is EVER shown, even if a user manually
    # flips this flag to True in their config file). The quality analysis
    # may still run for internal logging, but NEVER surfaces a tray
    # notification. The field is kept for backward compatibility with
    # existing config files.
    audio_quality_warnings: bool = False

    # Waveform visualization bubble
    waveform_bubble: bool = False

    # Bubble screen position (top / bottom).  Default "bottom" — the
    # recording bubble sits at bottom-center, out of the way of most
    # app title bars and camera notches.
    bubble_position: Literal["top", "bottom"] = "bottom"

    # Bubble behavior: show on record, or always visible
    # ``Literal[...]`` for static-type narrowing.
    bubble_behavior: Literal["show_on_record", "always_visible"] = "show_on_record"

    # Whether the bubble can be dragged by the user
    bubble_draggable: bool = True

    # Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
    bubble_show_on_startup: bool = True

    # when in `always_visible` mode, show a mic button next to the
    # waveform that toggles dictation on click. Default ON — primary
    # remediation for  (the always-visible bubble was non-interactive).
    bubble_click_to_toggle: bool = True

    # explicit mic-button visibility toggle (independent of
    # bubble_click_to_toggle). Default ON. When OFF, the bubble stays
    # non-interactive even in always_visible mode (original behaviour).
    bubble_mic_button: bool = True

    # Persisted bubble window position (screen-space pixel coords) and
    # scale factor. The renderer writes these via ``set_config`` after
    # the user drags / resizes the bubble window so the choice survives
    # across restarts. Both default to ``None`` (meaning "not set — let
    # the renderer pick a sensible default position / 1.0x scale"), and
    # older config.json files that predate the fields are treated the
    # same way. ``bubble_scale`` is a multiplier on the base DPI (so
    # ``1.0`` is no scaling, ``2.0`` is double-size); the renderer
    # clamps the visible range to ``[0.5, 2.0]`` but the server-side
    # validator accepts the wider ``[0.5, 3.0]`` so a future renderer
    # change can loosen the visible range without a server-side
    # allowlist edit.
    bubble_x: int | None = None
    bubble_y: int | None = None
    bubble_scale: float | None = None

    # Persisted microphone-test duration (seconds). The Microphone
    # page's "Test" button records for this many seconds before
    # auto-stopping. Default ``None`` — the renderer treats absence as
    # the in-app default of 5s, and the server-side validator accepts
    # the range ``[1, 60]`` (wider than the renderer's visible
    # ``[1, 30]`` clamp so a future renderer change can loosen the
    # visible range without a server-side allowlist edit).
    test_duration_seconds: int | None = None

    # History database
    # master toggle for whether dictated text is persisted to the
    # history SQLite DB. When False, dictation_pipeline._store_result
    # skips the ``add_transcription`` call entirely — nothing is written
    # to disk for the current session. Defaults True to preserve the
    # existing "history on" behavior for upgrades; users who dictate
    # sensitive content (passwords, medical/financial/PII) can toggle
    # this off via Settings → Privacy → "Disable history" (renderer
    # wiring owned by P4-A6; pipeline gate owned by P4-A4).
    history_enabled: bool = True
    history_retention_days: int = 90  # 0 = keep forever
    history_retention_count: int = 0  # 0 = unlimited
    history_max_entries: int = 1000

    # ─── P3 Features ───────────────────────────────────────────────

    # Onboarding
    onboarding_completed: bool = False
    # marks that onboarding was force-completed after repeated
    # setup failures so the app remains usable. Lets the UI show a
    # "configure manually" hint instead of looping the wizard.
    onboarding_failed: bool = False

    # Tray icon left-click behavior
    # ``Literal[...]`` for static-type narrowing.
    tray_left_click_action: Literal["open_app", "toggle_dictation"] = "open_app"

    # Theme mode (system/light/dark)
    # ``Literal[...]`` for static-type narrowing.
    theme_mode: Literal["system", "light", "dark"] = "system"
    # Theme preset — a built-in colour scheme applied on top of the
    # current theme_mode. "default" means no overrides.
    # ``Literal[...]`` enumerates the built-in presets.
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
    # Stored as nested dict: {"light": {var: val, ...}, "dark": {var: val, ...}}
    # parameterised the bare ``dict`` annotation so static checkers
    # can verify the nested structure that the renderer writes.
    custom_theme: dict[str, dict[str, str]] | None = None

    # Linux title-bar window-button customization (Settings → Appearance).
    # Shape: {"mode": "system"|"custom", "side": "left"|"right",
    #         "show_minimize": bool, "show_maximize": bool, "show_close": bool}
    # ``mode: "system"`` follows the desktop's own button-layout
    # (gsettings org.gnome.desktop.wm.preferences button-layout); the
    # side/show_* keys only apply when mode == "custom". Ignored on
    # Windows (fixed native convention) and macOS (OS-drawn traffic
    # lights). Validated by _make_linux_window_buttons_validator.
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
    #  SIMPLIFY-001: single explicit field replaces the previous 3-field split
    # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu, and
    # max_recording_time_seconds=0). The old GPU/CPU auto-selection was invisible
    # to users and the "0 = automatic" convention was user-hostile. Now the field
    # is always a concrete value with min 300 (5 min) / max 3600 (60 min).
    max_recording_time_seconds: int = 900  # 15 minutes

    # NOTE: dead_air_timeout (float) was REMOVED in
    # It was redundant with stop_on_silence_seconds — both called the same
    # on_silence_auto_stop callback. Auto-stop already resets on every speech
    # detection, so the "only after speech" condition dead air added was
    # unnecessary. Do NOT re-add. See RecordingSettingsSection.tsx comment.

    # silence_rms_threshold / silence_peak_threshold were REMOVED
    # from the Config dataclass — they were declared, validated, and
    # persisted, but never read by any runtime code path (ADR 0007 §4.3).
    # Existing config.json files that still carry these keys are silently
    # scrubbed by the v3 schema migration (``_migrate_to_v3``), so loading
    # an old config does NOT raise — the keys are simply dropped before
    # construction. Do NOT re-add.

    # Idle-unload timer for the active ASR backend. After this
    # many minutes with no dictation activity (no ``touch_active_model``
    # call from the transcription pipeline), ModelManager unloads the
    # active backend and calls ``release_gpu_memory()`` to return the
    # ~2.4 GB of VRAM held by Parakeet (or the Whisper weights / CUDA
    # caching allocator blocks) to the OS. The model is reloaded on the
    # next ``toggle_dictation`` via the existing
    # ``ensure_active_engine_loaded()`` lazy-init path.
    #
    # The default is 30 minutes — keeps the model warm for short
    # conversational gaps (sub-30-minute silences) while still
    # unloading it for genuinely long idle periods (lunch breaks,
    # meetings, overnight). This is the right tradeoff for the typical
    # tray-app usage pattern on laptops where GPU/CPU memory and ~5-15 W
    # of idle GPU power are worth reclaiming after a real "stepped away
    # from keyboard" gap. Users with abundant VRAM who want the model
    # resident for the lifetime of the process can set this to 0
    # (disables the feature — current "always loaded" behaviour is
    # preserved exactly). Cold-reload latency (2-5 s warm, 5-15 s cold)
    # is off the critical path of the next dictation because the
    # ``ensure_active_engine_loaded()`` reload path runs on
    # ``toggle_dictation`` before recording starts.
    # default bumped from 0 (disabled) to 30 minutes. The
    # idle-unload path arms a threading.Timer that calls
    # release_gpu_memory() after the configured idle period. On laptops
    # this frees GPU/CPU memory during long no-dictation periods (the
    # common case for a tray app). Users who need always-loaded behavior
    # (e.g. always-on desktop) can set this back to 0.
    model_idle_unload_minutes: int = 30

    # VAD configuration for the recording callback.
    # ADR 0007 §4.1: use_silero_vad defaults to True (torch is installed).
    # Falls back to RMS if Silero is unavailable.
    use_silero_vad: bool = True  # ADR 0007: was False, now True (torch available)
    vad_speech_threshold: float = 0.5  # Silero VAD prob > this → speech candidate
    vad_silence_threshold: float = 0.3  # Silero VAD prob < this → silence candidate
    # Auto-calibrate VAD thresholds from the ambient noise floor
    # during the first ~1.5s of each session (RMS path; Silero-prob path
    # when use_silero_vad is active). Consumed by VadProcessor
    # (vad_processor.py) via Recorder._vad_auto_calibrate. Was previously
    # read via getattr() fallback while unregistered here — the flag could
    # never be enabled, leaving the calibration feature dead.
    vad_auto_calibrate: bool = False

    # AUDIO-CH: number of channels to request from the input device.
    # Default 1 (mono) — appropriate for dictation. Set to 0 for
    # device default (auto-detect from device's max_input_channels).
    recording_channels: int = 1

    # AUDIO-PRE: pre-roll buffer captures audio before recording starts.
    # 0 = disabled (default, for privacy). When > 0, continuously
    # records N seconds of audio into a ring buffer and prepends it
    # when the user presses the hotkey, reducing cold-start latency.
    pre_roll_buffer_seconds: float = 0.0

    # ADR 0007 §5.2: normalize_audio and normalize_target_peak REMOVED.
    # Replaced by the Compressor filter in the audio filter chain.
    # the dataclass fields themselves were removed — they were
    # declared, validated, and persisted, but never read at runtime (the
    # Compressor filter supersedes them entirely). Existing config.json
    # files that still carry these keys are silently scrubbed by the v3
    # schema migration (``_migrate_to_v3``). Do NOT re-add.

    # ─── Volume ducking (v1.1.0) ────────────────────────────────────
    # Reduces system volume during dictation to prevent speaker output
    # from bleeding into the microphone.
    #
    # the Settings UI was simplified to just two controls:
    #   1. Auto Duck Volume (on/off)
    #   2. Duck Level (0–50%)
    # The remaining fields are internal (not exposed in the UI) and have
    # sensible defaults. They're kept in the config for backward compat
    # (existing user configs with custom values still load) and for
    # power users who edit config.json directly.
    volume_duck_enabled: bool = True
    volume_duck_level: float = 0.20  # 0.0–1.0 perceptual-linear (20% duck)
    #  ``volume_duck_per_session`` REMOVED from the Config
    # dataclass — ducking now always applies to the master volume
    # cross-platform. Existing config.json files that still carry the key
    # are silently scrubbed by the v3 schema migration. Do NOT re-add.
    # fade duration is now a fixed 200ms default (was 150ms).
    # Not exposed in the UI. Power users can override in config.json.
    volume_duck_fade_ms: int = 200  # 0–1000, 0 = instant
    #  ``volume_duck_smart`` REMOVED from the Config dataclass —
    # smart duck is now ALWAYS ON when ``volume_duck_enabled`` is True.
    # Existing config.json files that still carry the key are silently
    # scrubbed by the v3 schema migration. Do NOT re-add.
    # smart-duck poll interval is now a fixed 500ms default.
    # Not exposed in the UI. Power users can override in config.json.
    # the canonical default lives in
    # ``volume_ducker._DEFAULT_SMART_DUCK_POLL_MS``; imported here so
    # the dataclass default and the ``VolumeDucker`` constructor stay
    # in sync.
    volume_duck_smart_poll_interval_ms: int = _DEFAULT_SMART_DUCK_POLL_MS

    # ─── Audio enhancement preset (ADR 0007) ─────────────────────────
    # Preset name that controls the entire filter chain:
    #   "auto"        — all filters ON, RNNoise (best for 90% of users)
    #   "studio"      — minimal processing (quiet room, good mic)
    #   "noisy_room"  — aggressive, DeepFilterNet
    #   "off"         — all filters OFF
    #   "custom"      — user controls each filter individually
    # The preset is applied at startup (Config.load) and on explicit
    # set_config. See voice_typer/server/audio_presets.py for the
    # single source of truth.
    # ``Literal[...]`` includes legacy values
    # ("recommended", "none") so a stale config.json loaded BEFORE
    # the v2 migration renames them is still statically typed; the
    # migration then rewrites them to "auto"/"off".
    audio_preset: Literal[
        "auto",
        "studio",
        "noisy_room",
        "off",
        "custom",
        "none",
        "recommended",
    ] = "auto"

    # ─── Noise filtering (ADR 0007 — filter chain) ───────────────────
    # Each filter has an enable flag + parameters. The filter chain
    # (voice_typer/server/audio_filters/) is built from these fields
    # by audio_chain_builder.build_chain(). Chain order:
    #   HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter
    #
    #  ADR 0009: ``noise_filter_enabled`` and
    # ``noise_filter_post_capture`` are RUNTIME switches, NOT deprecated.
    # They are actively read by ``level_monitor.py`` and synced by
    # ``config_applier.py`` (which sets ``noise_filter_enabled =
    # audio_preset != "off"``). The legacy ``noise_filter_rnnoise`` field
    # is still kept for backward compat with old config.json files but is
    # migrated/ignored per ADR 0007 §5.
    noise_filter_enabled: bool = True  # runtime switch — see ADR 0009
    noise_filter_highpass: bool = True
    noise_filter_highpass_cutoff_hz: float = 80.0  # 20–500
    noise_filter_gate: bool = True
    # ``noise_filter_gate_threshold`` REMOVED from the Config
    # dataclass — replaced by the open/close threshold pair below per
    # ADR 0007. Existing config.json files that still carry the key are
    # silently scrubbed by the v3 schema migration. Do NOT re-add.
    noise_filter_gate_hold_ms: float = 200.0  # ADR 0007: was 150, now 200 (matches OBS)
    noise_filter_rnnoise: bool = True  # ADR 0007: was False, now True (RNNoise is default dep)
    noise_filter_post_capture: bool = True  # runtime switch — see ADR 0009

    # ADR 0007 §5.1: New filter chain fields
    # Noise suppressor backend selection.
    # ``Literal[...]`` matches ``NOISE_SUPPRESSION_METHODS``
    # in ``config_validators.py`` (the authoritative allowlist). The
    # historical ``"speex"`` option was never implemented — there is
    # no speex backend in ``audio_filters/noise_suppressor.py`` — and
    # is intentionally omitted so static type-checkers reject it.
    # (``"deepfilternet"`` was likewise retired when the bundled GTCRN
    # ONNX streaming model replaced it; ``Config.load()`` remaps the
    # legacy on-disk value to ``"gtcrn"``.)
    noise_suppression_method: Literal["rnnoise", "gtcrn", "none"] = "rnnoise"

    # NoiseGate (OBS-style, replaces single threshold)
    noise_filter_gate_open_threshold_db: float = -26.0
    noise_filter_gate_close_threshold_db: float = -32.0
    noise_filter_gate_attack_ms: float = 25.0
    noise_filter_gate_release_ms: float = 150.0
    # when True, gate samples the first ~500ms of audio to estimate
    # the ambient noise floor and derives open/close thresholds from it.
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

    # Notch filter (50/60Hz hum) — optional, default OFF
    noise_filter_notch: bool = False
    noise_filter_notch_frequency_hz: float = 0.0  # 0 = auto-detect (60Hz Americas default)

    # ─── P4: AI grammar / punctuation / capitalization ─────────────
    # Rule-based, offline enhancement applied AFTER LLM polish and
    # BEFORE the result is stored to history / pasted.  See
    # ``voice_typer/server/ai_enhancement.py``.  The master toggle
    # defaults to OFF — the user must explicitly opt in via Settings
    # → AI Enhancement so existing users don't see behavior changes
    # after upgrading.  The three sub-toggles default to True so
    # that, once the master toggle is flipped, the feature "just
    # works" without further configuration.
    ai_enhancement_enabled: bool = False  # master toggle (opt-in)
    auto_capitalize: bool = True  # capitalize sentence starts + proper nouns
    auto_punctuate: bool = True  # add periods at sentence boundaries
    fix_grammar_basics: bool = True  # fix bare "i", contractions, double spaces

    # ─── P5: Vocabulary automation ─────────────────────────────────
    # Confidence-score-based auto-correction suggestions.  When the
    # master toggle is ON, the dictation pipeline analyzes each
    # transcription for low-confidence words and suggests vocabulary
    # corrections.  Suggestions above ``vocabulary_auto_apply_threshold``
    # are auto-applied; the rest are queued for the user to review.
    # Defaults to OFF — the user must explicitly opt in via Settings.
    vocabulary_automation_enabled: bool = False  # master toggle (opt-in)
    # Below this segment-confidence, suggest corrections.  0.7 is a
    # common Whisper "low confidence" threshold (the model emits
    # avg_logprob values around -1.0 for uncertain words; the
    # pipeline normalizes to a 0–1 confidence where 0.7 corresponds
    # to roughly avg_logprob -0.4).
    vocabulary_auto_confidence_threshold: float = 0.7
    # Above this confidence, auto-apply suggestions without asking.
    # 0.95 is high enough that false positives are rare but low
    # enough that the auto-apply path actually fires in practice.
    vocabulary_auto_apply_threshold: float = 0.95

    # ``ClassVar`` bindings of the module-level constants above —
    # ``asdict()`` skips ClassVars, and the reset impl reads them via
    # ``cls._ENUM_FIELDS_TO_RESET_ON_LOAD`` so a subclass could narrow
    # the set.
    _ENUM_FIELDS_TO_RESET_ON_LOAD: ClassVar[frozenset[str]] = _ENUM_FIELDS_TO_RESET_ON_LOAD
    _SECRET_FIELD_NAMES_FALLBACK: ClassVar[frozenset[str]] = _SECRET_FIELD_NAMES_FALLBACK


def _reset_invalid_enum_fields_impl(cls, instance) -> None:
    """Reset invalid ``Literal[...]`` enum fields to their defaults.

    Module-level impl behind the ``Config._reset_invalid_enum_fields``
    classmethod delegator (``config/_lifecycle.py``).

    ``validate_config(instance)`` (called from :meth:`load` just
    before this helper) flags invalid enum values and appends
    human-readable errors to ``instance.last_load_warnings``, but
    it does NOT mutate the field — the invalid value remains on
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
    are also reset — they can never be in a ``Literal[str, ...]``
    allowed set. The ``_validate_non_numeric_fields`` pre-pass
    normally coerces such values to ``str`` first, but this
    helper is defensive against a value that slipped through
    (e.g. a complex type that the str branch didn't catch).

    The reset is idempotent: a value already at the default is a
    no-op (it's in the allowed set). The reset is also safe to
    re-run — calling it twice produces no extra warnings.

    Warnings are appended to ``instance.last_load_warnings`` (NOT
    ``data["_load_warnings"]``, which has already been popped and
    transferred to the instance by the time this runs — see the
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
        # raise if a referenced name isn't importable in the
        # current sandbox. Fall back to the raw ``__annotations__``
        # (no forward-ref resolution) — for Literal[...] fields
        # the raw annotation IS the Literal, so this works.
        hints = dict(getattr(cls, "__annotations__", {}))

    # Build the defaults instance ONCE (not per-field) — Config()
    # construction is cheap but not free, and the per-field loop
    # may reset multiple values.
    defaults = cls()

    for field_name in cls._ENUM_FIELDS_TO_RESET_ON_LOAD:
        ann = hints.get(field_name)
        if ann is None:
            # Field was removed or renamed — skip silently (the
            # set is a ClassVar that should stay in sync with the
            # dataclass declaration, but a stale entry shouldn't
            # crash load).
            continue
        # Unwrap ``T | None`` / ``Optional[T]`` — none of the 9
        # fields are optional, but the unwrap is cheap insurance
        # against a future contributor adding an optional enum.
        if typing.get_origin(ann) in (typing.Union, types.UnionType):
            args = [a for a in typing.get_args(ann) if a is not type(None)]
            if len(args) == 1:
                ann = args[0]
        if typing.get_origin(ann) is not typing.Literal:
            # Field's annotation isn't a Literal (e.g. it was
            # widened to bare ``str`` in a future refactor). Skip
            # — we can't enumerate allowed values without a
            # Literal. ``validate_config`` (via the IPC
            # allowlist) still catches genuinely invalid values.
            continue
        allowed = set(typing.get_args(ann))
        current = getattr(instance, field_name, None)
        if current in allowed:
            continue
        default_value = getattr(defaults, field_name)
        # Defensive: if the default ITSELF isn't in the allowed
        # set (shouldn't happen — the dataclass declaration
        # defines both — but guards against a malformed Literal),
        # pick the first allowed value rather than resetting to
        # an invalid default.
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
        # pattern (Config is not frozen, but this is forward-
        # compatible and avoids triggering any future
        # ``__setattr__`` override).
        object.__setattr__(instance, field_name, default_value)
        # Append to ``last_load_warnings`` — initialize the list
        # if it's ``None`` (the ``__post_init__`` default).
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
        # ``_SECRET_FIELD_NAMES_FALLBACK`` literal. A silent
        # fallback would mask a broken install / sandbox and could
        # leave newly added provider API keys un-redacted in log
        # lines (the sanitizer fail-closed analog prevents the same
        # leak over IPC). Re-raise so the breakage is loud and
        # immediate at the first call site (Config.load()
        # redaction). The existing tests will surface any
        # early-startup path that relied on the silent fallback.
        log.critical(
            "[CONFIG] could not import credential_store for "
            "_secret_field_names — secret-field redaction may be "
            "incomplete. Refusing to fall back to a hardcoded "
            "literal (fail-closed). Original error: %s",
            exc,
        )
        raise
