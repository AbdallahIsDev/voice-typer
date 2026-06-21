"""Tray types: AppState enum and TrayController protocol.

ARCH-003: extracted from tray.py to separate the type definitions
from the icon rendering and menu logic.  This allows tests to import
AppState without pulling in pystray/PIL.
"""

from enum import Enum
from typing import Protocol


class AppState(Enum):
    """Tray icon + app lifecycle states.

    ARCH-035: the tray icon only renders 4 distinct icons (IDLE,
    RECORDING, TRANSCRIBING, ERROR). The other states (PAUSED,
    WARMING_UP, DOWNLOADING, etc.) are used for status text /
    accessibility labels and don't have unique icons. They're kept
    as enum values so callers can ask "is the app in a non-idle
    state" without inspecting the status string. Removing them would
    lose semantic information; reducing to 4 would force callers to
    overload the status text.
    """
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
    """Protocol that the tray controller (typically VoiceTyperApp) must implement.

    ARCH-036: previously the Protocol declared 8 methods but VoiceTyperApp
    only implements some. ``Protocol`` allows missing methods at runtime
    (it's structural typing), but ``@abstractmethod`` would force
    VoiceTyperApp to declare them. We keep the Protocol permissive —
    the tray only calls the methods it needs; if a method is missing,
    the tray raises AttributeError at the call site (clear failure).
    """

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model: str) -> None: ...
    def change_hotkey(self, hotkey: str) -> None: ...
    def quit_app(self) -> None: ...
    def set_hotkey(self, hotkey: str) -> None: ...
    def restart_app(self) -> None: ...
    def repaste_last(self) -> None: ...
