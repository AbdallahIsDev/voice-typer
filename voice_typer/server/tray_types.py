"""Tray types: AppState enum and TrayController protocol.

extracted from tray.py to separate the type definitions
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

    # build_tray_menu_model reads controller._microphones via getattr.
    # Promote to the Protocol so pyrefly verifies VoiceTyperApp exposes it.
    microphones: list[dict]

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model_size: str) -> None: ...
    def quit_app(self) -> None: ...
    def restart_app(self) -> None: ...
    def undo_last(self) -> None: ...

    # Tauri-side ``maybe_publish_tray_menu`` consumes these two
    # members to mark the active mic in the Microphones submenu and to
    # wire the "Refresh mics" menu item. Previously the call sites used
    # ``getattr(controller, "active_microphone_id", None)`` /
    # ``getattr(controller, "refresh_microphones", None)`` against names
    # that were NEVER defined on ``VoiceTyperApp`` — the defensive
    # ``getattr`` silently returned ``None`` and the Tauri tray submenu
    # never marked the active mic nor offered "Refresh mics". Promoting
    # to the Protocol lets pyrefly verify the contract; the call sites
    # in ``tray_menu.maybe_publish_tray_menu`` now use direct attribute
    # access (drop the ``getattr`` defensive calls).
    @property
    def active_microphone_id(self) -> str | None: ...

    def refresh_microphones(self) -> None: ...
