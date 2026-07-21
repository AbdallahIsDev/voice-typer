"""Tray types: AppState enum and TrayController protocol.

ARCH-003: extracted from tray.py to separate the type definitions
from the icon rendering and menu logic.  This allows tests to import
AppState without pulling in pystray/PIL.
"""

from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from voice_typer.server.recording_controller import RecordingController


class AppState(Enum):
    """Tray icon + app lifecycle states."""

    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    LOADING = "loading"
    ERROR = "error"
    CANCELLING = "cancelling"


class TrayController(Protocol):
    """Protocol that the tray controller (typically VoiceTyperApp) must implement."""

    recording: "RecordingController"

    # CR-144: build_tray_menu_model reads controller._microphones via getattr.
    # Promote to the Protocol so pyrefly verifies VoiceTyperApp exposes it.
    microphones: list[dict]

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model_size: str) -> None: ...
    def quit_app(self) -> None: ...
    def restart_app(self) -> None: ...
    def repaste_last(self) -> None: ...
    def undo_last(self) -> None: ...
