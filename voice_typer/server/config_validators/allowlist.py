"""IPC ``set_config`` allowlist + supporting constants.

This submodule was split out of the original monolithic
``config_validators/__init__.py`` so the security-critical allowlist
has its own focused home.  It owns:

* :data:`MAX_RECORDING_TIME_SECONDS_DEFAULT` / ``_MIN`` / ``_MAX`` —
  canonical bounds for ``max_recording_time_seconds`` (shared with
  ``config/coercion.py`` so the IPC validator and the post-load clamp
  can never drift).
* :data:`STREAMING_LEFT_OVERLAP_SECONDS_MIN` /
  :data:`STREAMING_RIGHT_GUARD_SECONDS_MIN` — canonical lower bounds
  for the streaming-overlap / -guard fields (same shared-source
  rationale).
* :data:`ALLOWED_USER_MODELS` — derived from
  :data:`model_registry.MODEL_REGISTRY` at import time so the two
  cannot drift.
* :data:`NOISE_SUPPRESSION_METHODS` — the canonical noise-suppression
  backend enum (``"rnnoise" | "gtcrn" | "none"``); imported by
  ``audio_filters/noise_suppressor.py`` and re-exported via
  ``config/__init__.py``.
* The pre-built ``_VALIDATOR_*`` instances used inside
  :data:`IPC_CONFIG_ALLOWLIST`.
* :data:`IPC_CONFIG_ALLOWLIST` — the explicit, reviewed map of fields
  the Electron renderer is permitted to mutate via the IPC
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


# ALLOWED_USER_MODELS is DERIVED from
# :data:`model_registry.MODEL_REGISTRY` at import time so the two cannot
# drift. The catalog was pruned 2026-08-15 to `tiny`, `large-v3`,
# `large-v3-turbo`, `parakeet`, and `qwen` (`large-v3` was restored the
# same day at the user's request); any other value (stale configs,
# removed models like `small.en` / `base` / `turbo` / distil-*) is
# reset to ``DEFAULT_MODEL_SIZE`` (currently `tiny`) on Config.load()
# by ``_validate_model_path`` in ``config/coercion.py``.

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
NOISE_SUPPRESSION_METHODS: frozenset[str] = frozenset({"rnnoise", "gtcrn", "none"})


# ──────────────────────────────────────────────────────────────────────────
# IPC `set_config` allowlist
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
    "repaste_hotkey": (str, _VALIDATOR_REPASTE_HOTKEY),
    # ── Recording ─────────────────────────────────────────────────────
    "microphone": ((str, type(None)), _VALIDATOR_MICROPHONE),
    # ── Transcription ─────────────────────────────────────────────────
    # ``model_size`` additionally accepts ``NO_MODEL_SIZE`` ("") — the
    # genuine "no model selected" state (see
    # ``model_registry.NO_MODEL_SIZE``). The server enters it when a
    # stale selection is cleared with no downloaded fallback; the
    # renderer may also write it (a future "deselect" affordance).
    "model_size": (str, _make_enum_validator(ALLOWED_USER_MODELS | {_NO_MODEL_SIZE})),
    "language": (str, _VALIDATOR_LANGUAGE),
    "device": (str, _make_enum_validator(frozenset({"cuda", "cpu"}))),
    "beam_size": (int, _make_int_validator(lo=1, hi=10)),
    "best_of": (int, _make_int_validator(lo=1, hi=10)),
    # Whisper-specific beam width (preferred over the legacy
    # ``beam_size`` field). 1 keeps the automatic device/model-aware
    # default resolved by the engine; values >1 pin the width explicitly.
    "whisper_beam_size": (int, _make_int_validator(lo=1, hi=10)),
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
    # Re-run setup wizard (Settings → Troubleshooting). The renderer
    # writes ``false`` via set_config so App.tsx's onboarding route
    # guard admits the wizard page and the flag persists across config
    # refreshes; completing the wizard still flows through the
    # dedicated ``complete_onboarding`` IPC command. Reset-to-defaults
    # paths keep excluding this key from their bulk writes.
    "onboarding_completed": (bool, _bool_validator),
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
    # for non-None values. Range [-100000, 100000] covers any plausible
    # screen coordinate (8K monitors are 7680px wide) INCLUDING the
    # negative coordinates that are normal and expected in multi-monitor
    # layouts: displays positioned left of / above the primary monitor
    # have negative origins on Windows/macOS/Linux virtual screens (the
    # primary display anchors (0,0); the lower bound leaves ~12x headroom
    # beyond a wall of 8K displays). The upper bound still rejects absurd
    # values that would place the bubble off-screen. On-screen validity
    # is enforced at RESTORE time by the hosts (Electron's
    # isPositionOnAnyDisplay / the Tauri work-area check), not here —
    # a coordinate can be temporarily off-screen when a monitor is
    # unplugged and must survive that transient state.
    "bubble_x": ((int, type(None)), _make_optional_int_validator(lo=-100_000, hi=100_000)),
    "bubble_y": ((int, type(None)), _make_optional_int_validator(lo=-100_000, hi=100_000)),
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
    # Linux title-bar window-button customization (Settings → Appearance,
    # Linux only). Shape contract pinned by
    # _make_linux_window_buttons_validator (mode/side + 3 bools — all
    # required, unknown keys rejected). Ignored on Windows/macOS.
    "linux_window_buttons": (dict, _make_linux_window_buttons_validator()),
    "text_size": (int, _make_int_validator(lo=8, hi=72)),
    # ── Silent mic disconnection ────────────────────────────────
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
    # Auto-calibrate VAD thresholds from ambient noise. Must stay
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
