"""Dictation start/stop helpers mixin for VoiceTyperApp. Logger: voice_typer.server.app."""

from __future__ import annotations

import logging
from typing import Any

# this logger name: see module docstring.
log = logging.getLogger("voice_typer.server.app")


class AppDictation:
    """Declares NO ``__init__``: construction order and the backing
    attributes stay entirely in ``app.py``; only the accessors live
    """

    def toggle_dictation(self):
        """Delegate to RecordingController.toggle()."""
        self.recording.toggle()

    def _start_dictation(self):
        """Delegate to RecordingController.start()."""
        self.recording.start()

    # One-shot latch for audio-quality chunk delegation loss. The audio
    _audio_quality_delegate_warned = False

    def _on_audio_quality_chunk(self, rms: float, peak: float) -> None:
        """Delegate to AudioQualityController."""
        delegate = self.audio_quality
        if delegate is None:
            if not self._audio_quality_delegate_warned:
                log.warning("[APP] audio_quality controller unavailable, lazy-init failed earlier; skipping chunk")
                self._audio_quality_delegate_warned = True
            else:
                log.debug("[APP] audio_quality controller unavailable, skipping chunk")
            return None
        # Delegate is back: reset the latch so the NEXT loss episode
        if self._audio_quality_delegate_warned:
            self._audio_quality_delegate_warned = False
        return delegate._on_audio_quality_chunk(rms, peak)

    def _rebuild_audio_processor(self, force_sr: int | None = None) -> None:
        """Delegate to AudioQualityController."""
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable, lazy-init failed earlier; skipping rebuild")
            return None
        return delegate._rebuild_audio_processor(force_sr=force_sr)

    def _finalize_audio_quality_report(self, audio: Any) -> None:
        """parameter annotated as ``Any`` (not ``np.ndarray``) so the
        annotation does NOT depend on ``from __future__ import annotations``
        """
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable, lazy-init failed earlier; skipping finalize")
            return None
        return delegate._finalize_audio_quality_report(audio)

    def _stop_dictation(self):
        """this method is now a thin delegate to
        duplicate of ``RecordingController.stop()`` that was missing
        """
        self.recording.stop()

    def _cancel_streaming_session(self):
        """Delegate to RecordingController._cancel_streaming_session()."""
        self.recording._cancel_streaming_session()

    def repaste_last(self) -> None:
        """delegates directly to the canonical ``UndoRepasteController``
        (``self.undo``), the thin ``RepasteController`` wrapper in
        """
        delegate = self.undo
        if delegate is None:
            log.warning("[APP] undo controller unavailable, lazy-init failed earlier; skipping repaste")
            return None
        return delegate.repaste_last()

    def undo_last(self) -> None:
        """delegates directly to the canonical ``UndoRepasteController``
        (``self.undo``), the thin ``UndoController`` wrapper in
        """
        delegate = self.undo
        if delegate is None:
            log.warning("[APP] undo controller unavailable, lazy-init failed earlier; skipping undo")
            return None
        return delegate.undo_last()

    def push_bubble_config(self, config: Any) -> None:
        """replaces the private ``getattr(self,
        "_waveform_bubble", None)`` access that lived inline in
        """
        bubble = getattr(self, "_waveform_bubble", None)
        if bubble is not None and bubble.on_config is not None:
            bubble.on_config(config)

    def _cancel_dictation(self):
        """while the frontend HotkeyPicker is in hotkey capture
        mode, the ESC cancel is a no-op, the frontend owns the Escape key
        """
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[CANCEL] ESC cancel paused (frontend hotkey capture), no-op")
                return
        except Exception:  # pragma: no cover - defensive
            log.debug("[CANCEL] keyboard ownership check failed", exc_info=True)
        self.recording.cancel()

    def _on_volume_crash_restore(self, state) -> None:
        """Delegate to VolumeController."""
        self.volume._on_volume_crash_restore(state)

    def _duck_volume(self) -> None:
        """Delegate to VolumeController."""
        self.volume._duck_volume()

    def _restore_volume(self, fade_ms: int | None = None) -> None:
        """Delegate to VolumeController."""
        self.volume._restore_volume(fade_ms=fade_ms)
