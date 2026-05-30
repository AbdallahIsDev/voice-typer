"""Configuration management with platform-aware storage."""

import json
import logging
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger("voice_typer.config")


ALLOWED_USER_MODELS = {"small.en", "medium.en"}


def _config_dir() -> Path:
    """Get platform-specific config directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / "voice-typer"


@dataclass
class Config:
    """Application configuration."""

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
    asr_backend: str = "whisper"  # "whisper" or "qwen"
    qwen_model_path: Optional[str] = None  # local path to Qwen3-ASR weights

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # Safety: paste when focus detection is unavailable (macOS / Linux)
    # When False (default), paste is skipped if the app cannot determine
    # whether a text input is focused — prevents keystrokes reaching
    # non-text windows (games, media players, etc.).
    unsafe_paste_on_unknown_focus: bool = False

    # External corrections file
    corrections_path: Optional[str] = None

    # Logging
    log_transcriptions: bool = False

    # Silent mic disconnection (H12)
    silence_warning_seconds: float = 20.0
    silence_auto_stop_seconds: float = 120.0
    max_recording_seconds_gpu: int = 1200
    max_recording_seconds_cpu: int = 600

    def save(self):
        """Save config to disk atomically via temp file + os.replace."""
        path = _config_dir()
        path.mkdir(parents=True, exist_ok=True)
        config_file = path / "config.json"
        tmp_file = config_file.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(str(tmp_file), str(config_file))

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults."""
        config_file = _config_dir() / "config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    data = json.load(f)
                data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
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
                            "Config qwen_model_path=%s does not exist or is not a directory, resetting to None",
                            qwen_path,
                        )
                        data["qwen_model_path"] = None

                # Validate corrections_path: must be an existing file if set
                corrections = data.get("corrections_path")
                if corrections is not None:
                    cp = Path(corrections)
                    if not cp.exists() or not cp.is_file():
                        log.warning(
                            "Config corrections_path=%s does not exist or is not a file, resetting to None",
                            corrections,
                        )
                        data["corrections_path"] = None

                # H1: Validate non-numeric fields before construction
                data = cls._validate_non_numeric_fields(data)

                return cls(**data)
            except json.JSONDecodeError as e:
                log.error("Config file corrupted: %s. Using defaults.", e)
                return cls()
            except Exception as e:
                log.error("Failed to load config: %s. Using defaults.", e)
                return cls()
        return cls()

    @classmethod
    def _validate_non_numeric_fields(cls, data: dict) -> dict:
        """Validate and coerce bool and str fields in loaded config data."""
        bool_fields = {
            "autostart", "paste_on_stop", "show_notifications",
            "text_cleanup_enabled", "unsafe_paste_on_unknown_focus",
            "streaming_transcription", "log_transcriptions",
            "condition_on_previous_text",
        }
        str_fields = {"hotkey", "language", "device", "asr_backend"}
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
                    "Config field '%s' had non-bool value %r, coercing to True",
                    field_name, val,
                )
                data[field_name] = True
            elif val in (0, "0", "false", "False", "no", ""):
                log.warning(
                    "Config field '%s' had non-bool value %r, coercing to False",
                    field_name, val,
                )
                data[field_name] = False
            else:
                log.warning(
                    "Config field '%s' had invalid value %r, resetting to default %r",
                    field_name, val, getattr(defaults, field_name),
                )
                data[field_name] = getattr(defaults, field_name)

        for field_name in str_fields:
            if field_name not in data:
                continue
            val = data[field_name]
            if isinstance(val, str):
                continue
            log.warning(
                "Config field '%s' had non-string value %r, resetting to default %r",
                field_name, val, getattr(defaults, field_name),
            )
            data[field_name] = getattr(defaults, field_name)

        return data

    @property
    def config_dir(self) -> Path:
        return _config_dir()
