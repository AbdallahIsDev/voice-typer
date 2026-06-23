"""Tray types: AppState enum and TrayController protocol.

ARCH-003: extracted from tray.py to separate the type definitions
from the icon rendering and menu logic.  This allows tests to import
AppState without pulling in pystray/PIL.
"""

from enum import Enum
from typing import Protocol


class AppState(Enum):
    """Tray icon + app lifecycle states.

    NEW-CQ-002: removed dead values PAUSED, WARMING_UP, DOWNLOADING,
    PROCESSING, SETUP, NOT_CONFIGURED — they were never set in
    production code, only referenced in the icon color dict. The
    icon colors for those states are also removed from tray_icon.py.
    CANCELLING is kept because it IS set during the cancel path
    (recording_controller.py:381).
    """
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    LOADING = "loading"
    ERROR = "error"
    CANCELLING = "cancelling"


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
