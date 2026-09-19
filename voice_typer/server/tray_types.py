"""Tray type definitions."""

import os
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from voice_typer.server.recording_controller import RecordingController


def is_tauri_sidecar() -> bool:
    """Return True when running as the Tauri sidecar (``TAURI_SIDECAR=1``)."""
    return os.environ.get("TAURI_SIDECAR") == "1"


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

    # build_tray_menu_model reads controller._microphones via getattr.
    microphones: list[dict]

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model_size: str) -> None: ...
    def quit_app(self) -> None: ...
    def restart_app(self) -> None: ...
    def undo_last(self) -> None: ...

    # Tauri-side ``maybe_publish_tray_menu`` consumes these two
    @property
    def active_microphone_id(self) -> str | None: ...

    def refresh_microphones(self) -> None: ...
