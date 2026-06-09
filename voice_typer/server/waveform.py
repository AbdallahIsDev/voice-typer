"""Waveform visualization bubble — scaffolding module.

A small floating overlay that appears during recording showing a
waveform visualizer responding to voice input. Currently scaffolding;
full implementation requires platform-specific overlay APIs.

Planned behavior:
- Appears when user presses hotkey to start recording
- Waveform moves/changes when user is speaking
- Flat line when silent
- Disappears when recording stops
- Always on top, draggable, semi-transparent
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


class WaveformBubble:
    """Scaffolding for the waveform visualization overlay.

    The full implementation requires:
    - Windows: transparent borderless window with SetWindowPos(TOPMOST)
    - macOS: NSPanel with NSFloatingWindowLevel
    - Linux: GTK overlay window

    This scaffolding provides the API surface and state management
    without requiring any GUI toolkit at import time.
    """

    def __init__(self):
        self._visible = False
        self._rms_level = 0.0
        self._is_speaking = False

    @property
    def visible(self) -> bool:
        """Whether the bubble is currently shown."""
        return self._visible

    def show(self) -> None:
        """Show the waveform bubble overlay."""
        if self._visible:
            return
        self._visible = True
        log.info("[WAVEFORM] Bubble shown")
        # TODO: Create platform-specific overlay window

    def hide(self) -> None:
        """Hide the waveform bubble overlay."""
        if not self._visible:
            return
        self._visible = False
        log.info("[WAVEFORM] Bubble hidden")
        # TODO: Destroy platform-specific overlay window

    def update_level(self, rms: float) -> None:
        """Update the waveform display with the current RMS level.

        Called from the audio callback or a consumer thread.
        """
        self._rms_level = rms
        self._is_speaking = rms > 0.01
        # TODO: Update the waveform visualization in the overlay

    @property
    def is_speaking(self) -> bool:
        """Whether speech is currently detected."""
        return self._is_speaking

    @property
    def rms_level(self) -> float:
        """Current RMS level."""
        return self._rms_level
