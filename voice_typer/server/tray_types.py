"""Tray types: AppState enum and TrayController protocol.

ARCH-003: extracted from tray.py to separate the type definitions
from the icon rendering and menu logic.  This allows tests to import
AppState without pulling in pystray/PIL.
"""

from enum import Enum
from typing import Protocol


class AppState(Enum):
    """Tray icon + app lifecycle states."""
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    LOADING = "loading"
    ERROR = "error"
    PAUSED = "paused"
    WARMING_UP = "warming_up"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    CANCELLING = "cancelling"
    SETUP = "setup"
    NOT_CONFIGURED = "not_configured"


class TrayController(Protocol):
    """Protocol that the tray controller (typically VoiceTyperApp) must implement."""

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model: str) -> None: ...
    def change_hotkey(self, hotkey: str) -> None: ...
    def quit_app(self) -> None: ...
    def set_hotkey(self, hotkey: str) -> None: ...
    def restart_app(self) -> None: ...
    def repaste_last(self) -> None: ...
