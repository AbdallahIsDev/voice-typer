"""Tray types: AppState enum and TrayController protocol.

ARCH-003: extracted from tray.py to separate the type definitions
from the icon rendering and menu logic.  This allows tests to import
AppState without pulling in pystray/PIL.
"""

from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    # TYPE_CHECKING-only import avoids a runtime cycle:
    #   recording_controller -> tray_types (via app)
    # At runtime only the string annotation below is needed for
    # pyrefly's structural Protocol check.
    from voice_typer.server.recording_controller import RecordingController


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

    PYREFLY-TASK-16: ``set_hotkey`` was previously declared here but
    is NOT called on the controller from anywhere in tray.py (the
    tray calls its own ``TrayIcon.set_hotkey`` at tray.py:244 — a
    different method on a different object). VoiceTyperApp never
    implemented ``set_hotkey``, so the declaration made the Protocol
    unsatisfiable and caused a pyrefly ``bad-argument-type`` error at
    ``TrayIcon.__init__`` (app.py:262). Removed.

    PYREFLY-TASK-16: ``recording`` attribute added — tray.py:599
    accesses ``self._controller.recording._force_recover_from_stuck_transcription``
    in the "Force cancel transcription" tray menu callback. The
    attribute is typed as ``RecordingController`` (the type
    VoiceTyperApp assigns at app.py:240: ``self.recording:
    RecordingController = RecordingController(self)``).
    """

    # Tray menu's "Force cancel transcription" item accesses
    # ``controller.recording._force_recover_from_stuck_transcription``.
    recording: "RecordingController"

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model_size: str) -> None: ...
    def quit_app(self) -> None: ...
    def restart_app(self) -> None: ...
    def repaste_last(self) -> None: ...
