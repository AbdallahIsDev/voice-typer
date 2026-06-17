"""Configuration management with platform-aware storage."""

import json
import logging
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger("voice_typer.server.config")


ALLOWED_USER_MODELS = {"tiny.en", "small.en", "medium.en", "qwen", "parakeet"}


def _legacy_config_dir() -> Path | None:
    """Get the legacy platform-specific config directory, if different from new one."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    return legacy if legacy != _config_dir() else None


def _config_dir() -> Path:
    """Get the voice-typer data directory in user home."""
    return Path.home() / ".voice-typer"


def _migrate_from_legacy():
    """One-time migration from old platform-specific location (e.g. %APPDATA%)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    if not legacy.exists() or legacy.resolve() == _config_dir().resolve():
        return
    target = _config_dir()
    if target.exists():
        return
    import shutil
    shutil.copytree(legacy, target, dirs_exist_ok=True)
    log.info("[CONFIG] Migrated data from %s to %s", legacy, target)


_CURRENT_SCHEMA_VERSION = 1

_MIGRATIONS = {}


@dataclass
class Config:
    """Application configuration."""

    schema_version: int = _CURRENT_SCHEMA_VERSION

    # Hotkey
    hotkey: str = "<f2>"

    # Recording
    sample_rate: int = 16000
    microphone: Optional[str] = None  # None = system default

    # Transcription
    model_size: str = "small.en"
    language: str = "en"
    device: str = "cuda"  # cuda, cpu
    beam_size: int = 1  # 1 = fastest greedy decoding; higher values trade speed for accuracy
    best_of: int = 1
    condition_on_previous_text: bool = False

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
    show_notifications: bool = True

    # ASR backend selection
    asr_backend: str = "whisper"  # "whisper", "qwen", or "parakeet"
    qwen_model_path: Optional[str] = None  # local path to Qwen3-ASR weights
    parakeet_model_path: Optional[str] = None  # local override for Parakeet weights (None = HF cache)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: Optional[str] = None

    # Logging
    log_transcriptions: bool = False

    # ─── P1 Features ───────────────────────────────────────────────

    # Push-to-talk mode (hold to record, release to stop)
    recording_mode: str = "toggle"  # "toggle" or "push_to_talk"
    push_to_talk_hotkey: str = ""  # Separate hotkey for PTT (empty = same as toggle)

    # ESC to cancel at any stage
    esc_cancel_enabled: bool = False

    # Repaste last transcription
    repaste_hotkey: str = "<ctrl>+<alt>+v"  # Hotkey for repasting last

    # Auto-punctuation (runs AFTER template matching)
    auto_punctuation: bool = False

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
    llm_api_url: str = "https://api.openai.com/v1/chat/completions"
    llm_model: str = "gpt-4o-mini"
    llm_preset: str = "professional"  # professional/casual/email/code

    # Crash recovery
    crash_recovery_enabled: bool = True

    # Audio quality analysis
    audio_quality_warnings: bool = True
    audio_clipping_warning: bool = True
    audio_low_volume_warning: bool = True
    audio_noise_warning: bool = True

    # Waveform visualization bubble
    waveform_bubble: bool = False

    # Bubble screen position (top / bottom)
    bubble_position: str = "top"

    # Bubble behavior: show on record, or always visible
    bubble_behavior: str = "show_on_record"  # "show_on_record" or "always_visible"

    # Whether the bubble can be dragged by the user
    bubble_draggable: bool = True

    # Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
    bubble_show_on_startup: bool = True

    # History database
    history_retention_days: int = 90  # 0 = keep forever
    history_retention_count: int = 0  # 0 = unlimited
    history_max_entries: int = 1000

    # ─── P3 Features ───────────────────────────────────────────────

    # Onboarding
    onboarding_completed: bool = False

    # Tray icon left-click behavior
    tray_left_click_action: str = "open_app"  # "open_app" or "toggle_dictation"

    # UX-008: Theme mode (system/light/dark)
    theme_mode: str = "system"

    # UX-036: Accessibility
    high_contrast: bool = False
    text_size: int = 14

    # Wayland hotkey fallback warning
    wayland_warned: bool = False

    # Fast startup: keep torch + transformers + model weights in the OS
    # file cache by running a low-priority prewarm on login/idle.  Cuts
    # cold-boot startup from ~45s to a few seconds.  Disable on low-RAM
    # machines where pinning ~6 GB of file cache is undesirable.
    fast_startup: bool = True

    # Silent mic disconnection (H12)
    silence_warning_seconds: float = 20.0
    silence_auto_stop_seconds: float = 120.0
    max_recording_seconds_gpu: int = 1200
    max_recording_seconds_cpu: int = 600
    max_recording_seconds: int = 0  # 0 = use device-specific default (gpu/cpu)

    def save(self) -> bool:
        """Save config to disk atomically via temp file + os.replace.

        Returns True on success, False on failure. Errors are logged but not raised.
        """
        try:
            path = _config_dir()
            path.mkdir(parents=True, exist_ok=True)
            config_file = path / "config.json"
            tmp_file = config_file.with_suffix(".tmp")
            with open(tmp_file, "w") as f:
                json.dump(asdict(self), f, indent=2)
            os.replace(str(tmp_file), str(config_file))
            return True
        except (OSError, PermissionError) as e:
            log.error("[CONFIG] Failed to save config: %s", e)
            return False

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults."""
        config_file = _config_dir() / "config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    data = json.load(f)
                data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}

                # M3: Schema versioning and migration
                loaded_version = data.get("schema_version", 0)
                for version in range(loaded_version + 1, _CURRENT_SCHEMA_VERSION + 1):
                    migrator = _MIGRATIONS.get(version)
                    if migrator is not None:
                        data = migrator(data)
                data["schema_version"] = _CURRENT_SCHEMA_VERSION

                data["streaming_left_overlap_seconds"] = max(
                    float(data.get("streaming_left_overlap_seconds", 3.0)),
                    3.0,
                )
                data["streaming_right_guard_seconds"] = max(
                    float(data.get("streaming_right_guard_seconds", 1.5)),
                    1.5,
                )
                if data.get("model_size") not in ALLOWED_USER_MODELS:
                    data["model_size"] = "small.en"

                # Validate qwen_model_path: must be an existing directory if set
                qwen_path = data.get("qwen_model_path")
                if qwen_path is not None:
                    p = Path(qwen_path)
                    if not p.exists() or not p.is_dir():
                        log.warning(
                            "[CONFIG] Config qwen_model_path=%s does not exist or is not a directory, resetting to None",
                            qwen_path,
                        )
                        data["qwen_model_path"] = None

                # Validate corrections_path: must be an existing file if set
                corrections = data.get("corrections_path")
                if corrections is not None:
                    cp = Path(corrections)
                    if not cp.exists() or not cp.is_file():
                        log.warning(
                            "[CONFIG] Config corrections_path=%s does not exist or is not a file, resetting to None",
                            corrections,
                        )
                        data["corrections_path"] = None

                # H1: Validate non-numeric fields before construction
                data = cls._validate_non_numeric_fields(data)

                return cls(**data)
            except json.JSONDecodeError as e:
                log.error("[CONFIG] Config file corrupted: %s. Using defaults.", e)
                return cls()
            except Exception as e:
                log.error("[CONFIG] Failed to load config: %s. Using defaults.", e)
                return cls()
        return cls()

    @classmethod
    def _validate_non_numeric_fields(cls, data: dict) -> dict:
        """Validate and coerce bool and str fields in loaded config data."""
        bool_fields = {
            "autostart", "paste_on_stop", "show_notifications",
            "text_cleanup_enabled",
            "streaming_transcription", "log_transcriptions",
            "condition_on_previous_text",
            "esc_cancel_enabled", "auto_punctuation", "llm_polish",
            "crash_recovery_enabled", "audio_quality_warnings",
            "audio_clipping_warning", "audio_low_volume_warning",
            "audio_noise_warning",
            "templates_enabled", "vocabulary_enabled",
            "waveform_bubble", "onboarding_completed", "wayland_warned",
            "fast_startup",
            "bubble_draggable", "bubble_show_on_startup",
        }
        str_fields = {
            "hotkey", "language", "device", "asr_backend",
            "recording_mode", "push_to_talk_hotkey",
            "cloud_api_key", "cloud_api_url", "cloud_model",
            "openai_api_key", "groq_api_key", "deepgram_api_key",
            "llm_api_key", "llm_api_url", "llm_model", "llm_preset",
            "repaste_hotkey",
            "tray_left_click_action",
            "parakeet_model_path",
            "bubble_position", "bubble_behavior",
        }
        defaults = cls()

        for field_name in bool_fields:
            if field_name not in data:
                continue
            val = data[field_name]
            if isinstance(val, bool):
                continue
            # Coerce truthy/falsy values
            if val in (1, "1", "true", "True", "yes"):
                log.warning(
                    "[CONFIG] Config field '%s' had non-bool value %r, coercing to True",
                    field_name, val,
                )
                data[field_name] = True
            elif val in (0, "0", "false", "False", "no", ""):
                log.warning(
                    "[CONFIG] Config field '%s' had non-bool value %r, coercing to False",
                    field_name, val,
                )
                data[field_name] = False
            else:
                log.warning(
                    "[CONFIG] Config field '%s' had invalid value %r, resetting to default %r",
                    field_name, val, getattr(defaults, field_name),
                )
                data[field_name] = getattr(defaults, field_name)

        optional_str_fields = {"parakeet_model_path"}

        for field_name in str_fields:
            if field_name not in data:
                continue
            val = data[field_name]
            if isinstance(val, str):
                continue
            if val is None and field_name in optional_str_fields:
                continue
            log.warning(
                "[CONFIG] Config field '%s' had non-string value %r, resetting to default %r",
                field_name, val, getattr(defaults, field_name),
            )
            data[field_name] = getattr(defaults, field_name)

        return data

    @property
    def config_dir(self) -> Path:
        return _config_dir()
