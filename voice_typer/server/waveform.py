"""Waveform visualization bubble — Electron-side overlay controller.

The bubble itself is a small, frameless, always-on-top ``BrowserWindow``
that the Electron main process creates on demand.  This module owns the
state and the listener wiring on the Python side:

- ``show()`` / ``hide()`` notify listeners that the bubble should
  appear/disappear in the user's primary display.
- ``update_level(rms, peak)`` is fired from the audio callback on every
  chunk (called by ``recorder.on_rms_level``) so the bubble can render a
  live waveform that responds to the user's voice.

The listeners (registered by ``app.py``) forward these events through
the IPC server so Electron can drive the renderer.
"""

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


class WaveformBubble:
    """State + listener registry for the waveform bubble overlay.

    The actual UI is rendered by the Electron renderer process.  This
    class is a thin coordinator that:

    - tracks whether the bubble should currently be visible
    - tracks the latest RMS/peak level
    - emits show/hide/level notifications to subscribed listeners
    - is thread-safe (callbacks may fire from the audio callback thread)
    """

    def __init__(self) -> None:
        self._visible: bool = False
        self._rms_level: float = 0.0
        self._peak_level: float = 0.0
        self._is_speaking: bool = False
        self._lock = threading.Lock()

        # Listener slots, set by app.py after IPC server is up.
        # Each is ``Optional[Callable[[dict], None]]`` and runs on the
        # calling thread; they should be cheap and non-blocking.
        self.on_show: Optional[Callable[[], None]] = None
        self.on_hide: Optional[Callable[[], None]] = None
        self.on_level: Optional[Callable[[float, float], None]] = None

    # ── Visibility ───────────────────────────────────────────────────

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

    # ── Level reset (called when recording stops) ────────────────

    def reset_level(self) -> None:
        """Reset the level to zero and push a final event to the renderer.

        Called after detaching the audio callback so the renderer's animation
        envelope decays back to idle (dots shrink to minimum size) instead of
        staying frozen at the last active level.
        """
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

    # ── Live level updates (called from the audio callback thread) ──

    def update_level(self, rms: float, peak: float = 0.0, audio_chunk=None) -> None:
        """Push a new RMS/peak sample to subscribers.

        The ``rms`` value is typically in ``[0, ~0.3]`` for speech and
        ``0.0`` for silence.  ``peak`` is the per-chunk absolute max in
        ``[0, 1.0]`` and is used by the renderer to spike the waveform
        on transients.

        T021: If an audio_chunk is provided, runs Silero VAD to gate
        the visualizer — only updates when speech is detected.  When
        VAD indicates non-speech, the level decays smoothly.
        """
        # T021: VAD gate — only update visualizer if speech is detected
        if audio_chunk is not None:
            try:
                from voice_typer.server.vad import is_speech
                if not is_speech(audio_chunk):
                    # Non-speech: decay the level smoothly instead of
                    # letting ambient noise animate the visualizer
                    with self._lock:
                        self._rms_level *= 0.85
                        self._peak_level *= 0.8
                        self._is_speaking = False
                        cb = self.on_level
                        rms_out = self._rms_level
                        peak_out = self._peak_level
                    if cb is not None:
                        try:
                            cb(rms_out, peak_out)
                        except Exception:
                            pass
                    return
            except ImportError:
                pass  # VAD not available, fall through to RMS-only path

        with self._lock:
            # Cheap low-pass smoothing so the bubble doesn't jitter
            # chunk-to-chunk; the visualizer still reacts quickly to
            # voice onset because we lerp toward the new value.
            self._rms_level = (self._rms_level * 0.55) + (rms * 0.45)
            self._peak_level = max(self._peak_level * 0.7, peak)
            self._is_speaking = self._rms_level > 0.01
            cb = self.on_level
            rms_out = self._rms_level
            peak_out = self._peak_level
        if cb is not None:
            try:
                cb(rms_out, peak_out)
            except Exception:
                # Drop frame, audio path must not stall
                pass

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
