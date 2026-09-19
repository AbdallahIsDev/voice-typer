"""Waveform data helpers for the bubble UI."""

import contextlib
import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class WaveformBubble:
    """State + listener registry for the waveform bubble overlay."""

    def __init__(self) -> None:
        self._visible: bool = False
        self._rms_level: float = 0.0
        self._peak_level: float = 0.0
        self._is_speaking: bool = False
        self._lock = threading.Lock()

        # Listener slots, set by app.py after IPC server is up.
        self.on_show: Callable[[], None] | None = None
        self.on_hide: Callable[[], None] | None = None
        self.on_level: Callable[[float, float], None] | None = None
        # ``on_set_state`` is called when the
        self.on_set_state: Callable[[str], None] | None = None

        # `on_config` is called with the app Config when the bubble
        self.on_config: Callable[[object], None] | None = None

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        with self._lock:
            if self._visible:
                return
            self._visible = True
            cb = self.on_show
        log.info("[WAVEFORM] Bubble shown")
        if cb is not None:
            try:
                cb()
            except Exception:
                log.debug("[WAVEFORM] on_show callback raised", exc_info=True)

    def hide(self) -> None:
        with self._lock:
            if not self._visible:
                return
            self._visible = False
            self._rms_level = 0.0
            self._peak_level = 0.0
            self._is_speaking = False
            cb = self.on_hide
        log.info("[WAVEFORM] Bubble hidden")
        if cb is not None:
            try:
                cb()
            except Exception:
                log.debug("[WAVEFORM] on_hide callback raised", exc_info=True)

    def set_state(self, state: str) -> None:
        """Change the bubble's visual state."""
        with self._lock:
            cb = self.on_set_state
        log.info("[WAVEFORM] Bubble state -> %s", state)
        if cb is not None:
            try:
                cb(state)
            except Exception:
                log.debug("[WAVEFORM] on_set_state callback raised", exc_info=True)

    def reset_level(self) -> None:
        """Reset the level to zero and push a final event to the renderer."""
        with self._lock:
            self._rms_level = 0.0
            self._peak_level = 0.0
            self._is_speaking = False
            cb = self.on_level
        if cb is not None:
            try:
                cb(0.0, 0.0)
            except Exception:
                log.debug("[WAVEFORM] reset_level callback raised", exc_info=True)

    def update_level(self, rms: float, peak: float = 0.0) -> None:
        """Push a new RMS/peak sample to subscribers."""
        with self._lock:
            # Cheap low-pass smoothing so the bubble doesn't jitter
            self._rms_level = (self._rms_level * 0.50) + (rms * 0.50)
            self._peak_level = max(self._peak_level * 0.85, peak)
            self._is_speaking = self._rms_level > 0.005
            cb = self.on_level
            rms_out = self._rms_level
            peak_out = self._peak_level
        if cb is not None:
            with contextlib.suppress(Exception):
                # Drop frame, audio path must not stall
                cb(rms_out, peak_out)

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._is_speaking

    @property
    def rms_level(self) -> float:
        with self._lock:
            return self._rms_level

    @property
    def peak_level(self) -> float:
        with self._lock:
            return self._peak_level
