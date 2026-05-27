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
    text_cleanup_enabled: bool = True

    # External corrections file
    corrections_path: Optional[str] = None

    def save(self):
        """Save config to disk."""
        path = _config_dir()
        path.mkdir(parents=True, exist_ok=True)
        config_file = path / "config.json"
        with open(config_file, "w") as f:
            json.dump(asdict(self), f, indent=2)

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
                return cls(**data)
            except json.JSONDecodeError as e:
                log.error("Config file corrupted: %s. Using defaults.", e)
                return cls()
            except Exception as e:
                log.error("Failed to load config: %s. Using defaults.", e)
                return cls()
        return cls()

    @property
    def config_dir(self) -> Path:
        return _config_dir()
