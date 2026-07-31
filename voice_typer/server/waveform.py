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

import contextlib
import logging
import threading
from collections.abc import Callable

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
        self.on_show: Callable[[], None] | None = None
        self.on_hide: Callable[[], None] | None = None
        self.on_level: Callable[[float, float], None] | None = None
        # NEW-BUBBLE-TRANSCRIBING: ``on_set_state`` is called when the
        # bubble should change its visual state (e.g. from "recording"
        # visualizer to "transcribing" text).  The state string is one of:
        #   "recording" — show waveform visualizer
        #   "transcribing" — hide visualizer, show "Transcribing…" text
        #   "idle" — hide everything (bubble visible but showing nothing)
        self.on_set_state: Callable[[str], None] | None = None

        # `on_config` is called with the app Config when the bubble
        # should learn its relevant settings (e.g. whether to show the mic
        # button). The bubble renderer is sandboxed and receives no
        # get_config, so this is how it gets bubble_behavior /
        # bubble_click_to_toggle / bubble_mic_button.
        self.on_config: Callable[[object], None] | None = None

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

    # ── State change (e.g. recording → transcribing) ────────────

    def set_state(self, state: str) -> None:
        """Change the bubble's visual state.

                States:
                  - ``"recording"`` — waveform visualizer active (default when shown)
                  - ``"transcribing"`` — hide visualizer, show "Transcribing…" text
                  - ``"idle"`` — bubble visible but showing nothing (no visualizer,
                    no text)

                Called from ``RecordingController.stop()`` when recording ends and
                transcription begins, and from ``DictationPipeline`` when
        transcription completes.
        """
        with self._lock:
            cb = self.on_set_state
        log.info("[WAVEFORM] Bubble state -> %s", state)
        if cb is not None:
            try:
                cb(state)
            except Exception:
                log.debug("[WAVEFORM] on_set_state callback raised", exc_info=True)

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

        BUBBLE-: the previous VAD gate (T021) called
                ``compute_vad_prob(audio_chunk)`` with the device's native
                sample-rate audio (often 44.1/48 kHz) but ``compute_vad_prob``
                assumes 16 kHz.  Silero VAD then received an 11 ms slice
                interpreted as 32 ms, systematically biasing probabilities low
                and collapsing the bars on most chunks.  The VAD gate is
                cosmetic (the renderer's attack/release smoothing already
                handles ambient noise), so we disable it entirely and rely on
                the RMS-only path below.  This is simpler and more robust than
                resampling on the audio thread, and removes a torch dependency
                from the visualizer critical path.
        """
        # VAD gate intentionally removed (BUBBLE-).  See docstring.
        del audio_chunk  # accepted for backward-compat with callers

        with self._lock:
            # Cheap low-pass smoothing so the bubble doesn't jitter
            # chunk-to-chunk; the visualizer still reacts quickly to
            # voice onset because we lerp toward the new value.
            # BUGFIX 2026-06-25: increased forward weight from 0.45→0.5
            # so the visualizer responds faster to voice onset. Increased
            # peak persistence from 0.7→0.85 so transients linger longer
            # on the bars. Lowered is_speaking threshold from 0.01→0.005
            # so quiet speech is still considered active speech.
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
