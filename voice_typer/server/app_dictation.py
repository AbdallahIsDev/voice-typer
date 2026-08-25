"""AppDictation — dictation-control mixin extracted from VoiceTyperApp.

Owns the dictation start/stop/cancel/toggle delegates, the undo/repaste
entry points, the audio-quality chunk delegation (with the one-shot
delegate-loss warning latch), and the volume duck/restore delegates:

    - ``toggle_dictation`` / ``_start_dictation`` / ``_stop_dictation`` /
      ``_cancel_dictation`` / ``_cancel_streaming_session`` — thin
      delegates to ``RecordingController`` (``self.recording``).
    - ``_on_audio_quality_chunk`` / ``_rebuild_audio_processor`` /
      ``_finalize_audio_quality_report`` — delegates to
      ``AudioQualityController`` (``self.audio_quality``), each with the
      None-guard for a failed lazy init.
    - ``undo_last`` / ``repaste_last`` — delegates to
      ``UndoRepasteController`` (``self.undo``), each with the
      None-guard.
    - ``push_bubble_config`` — pushes config changes to the waveform
      bubble renderer.
    - ``_on_volume_crash_restore`` / ``_duck_volume`` /
      ``_restore_volume`` — delegates to ``VolumeController``
      (``self.volume``); ducking happens at dictation start and the
      restore at dictation stop.

Previously all of this lived on ``VoiceTyperApp`` in ``app.py``. The
behaviour is preserved verbatim — only the class boundary moved.
``VoiceTyperApp(AppDictation)`` inherits every method, so instance-level
monkeypatching (``monkeypatch.setattr(app, "_stop_dictation", spy)``)
and direct calls (``app.toggle_dictation()``) keep working unchanged.

A note on logging (mirrors the convention in ``app_lifecycle.py`` and
``app_undo.py``): this module uses
``logging.getLogger("voice_typer.server.app")`` rather than the
conventional ``__name__`` so caplog captures in tests (e.g. the
audio-quality delegate-loss WARNING) route to the same logger as the
original VoiceTyperApp methods.
"""

from __future__ import annotations

import logging
from typing import Any

# Tests capture the delegate-loss / controller-unavailable warnings at
# this logger name — see module docstring.
log = logging.getLogger("voice_typer.server.app")


class AppDictation:
    """Dictation-control mixin for ``VoiceTyperApp``.

    Declares NO ``__init__`` — construction order and the backing
    attributes stay entirely in ``app.py``; only the accessors live
    here.
    """

    # ─── Dictation ─────────────────────────────────────────────────────

    def toggle_dictation(self):
        """Delegate to RecordingController.toggle()."""
        self.recording.toggle()

    def _start_dictation(self):
        """Delegate to RecordingController.start()."""
        self.recording.start()

    # One-shot latch for audio-quality chunk delegation loss. The audio
    # pipeline delivers chunks at ~94 Hz, so when ``audio_quality`` is
    # None every chunk would otherwise re-log the WARNING (~94 lines per
    # second of recording). Warn ONCE per delegate-loss episode: the
    # latch resets on the first successful delegation, so a recovered
    # delegate re-arms the warning for any future loss episode. Class
    # attribute default keeps ``__new__``-constructed instances working.
    _audio_quality_delegate_warned = False

    def _on_audio_quality_chunk(self, rms: float, peak: float) -> None:
        """Delegate to AudioQualityController."""
        delegate = self.audio_quality
        if delegate is None:
            if not self._audio_quality_delegate_warned:
                log.warning("[APP] audio_quality controller unavailable — lazy-init failed earlier; skipping chunk")
                self._audio_quality_delegate_warned = True
            else:
                log.debug("[APP] audio_quality controller unavailable — skipping chunk")
            return None
        # Delegate is back: reset the latch so the NEXT loss episode
        # warns again (only write when latched — this runs at ~94 Hz).
        if self._audio_quality_delegate_warned:
            self._audio_quality_delegate_warned = False
        return delegate._on_audio_quality_chunk(rms, peak)

    def _rebuild_audio_processor(self, force_sr: int | None = None) -> None:
        """Delegate to AudioQualityController."""
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable — lazy-init failed earlier; skipping rebuild")
            return None
        return delegate._rebuild_audio_processor(force_sr=force_sr)

    def _finalize_audio_quality_report(self, audio: Any) -> None:
        """Delegate to AudioQualityController.

        parameter annotated as ``Any`` (not ``np.ndarray``) so the
        annotation does NOT depend on ``from __future__ import annotations``
        staying in place.
        """
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable — lazy-init failed earlier; skipping finalize")
            return None
        return delegate._finalize_audio_quality_report(audio)

    def _stop_dictation(self):
        """Stop recording and transcribe in background.

        this method is now a thin delegate to
        ``RecordingController.stop()``. Previously it was a 125-line
        duplicate of ``RecordingController.stop()`` that was missing
        three critical side effects:

        1. It never emitted the ``recording_stopped`` IPC push event,
           so the renderer's ``useSoundFeedback`` hook never received
           the stop cue and the stop beep never played.
        2. It never reset ``keyboard_ownership`` back to ``"normal"``,
           so the ESC cancel hotkey kept firing after a normal stop.
        3. It never started the Event-based watchdog thread
           (``_start_watchdog_thread``), so transcription hangs (>60s)
           never auto-recovered.

        ``RecordingController.stop()`` already contains the full,
        correct implementation — including all three missing side
        effects — but was unreachable from production call sites
        (``toggle``, ``on_silence_auto_stop``, ``on_max_duration_auto_stop``
        all called ``app._stop_dictation`` directly). Making this method
        a delegate routes all production stop traffic through the
        correct implementation and eliminates the duplication.
        """
        self.recording.stop()

    def _cancel_streaming_session(self):
        """Delegate to RecordingController._cancel_streaming_session()."""
        self.recording._cancel_streaming_session()

    # ─── Undo / Repaste ────────────────────────────────────────────────

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey).

        delegates directly to the canonical ``UndoRepasteController``
        (``self.undo``) — the thin ``RepasteController`` wrapper in
        ``controllers/`` was deleted as a parallel-system delegator.
        Behaviour preserved verbatim — only the call chain shortened.
        """
        delegate = self.undo
        if delegate is None:
            log.warning("[APP] undo controller unavailable — lazy-init failed earlier; skipping repaste")
            return None
        return delegate.repaste_last()

    def undo_last(self) -> None:
        """Undo last transcription by sending backspace keystrokes.

        delegates directly to the canonical ``UndoRepasteController``
        (``self.undo``) — the thin ``UndoController`` wrapper in
        ``controllers/`` was deleted as a parallel-system delegator.
        Behaviour preserved verbatim — only the call chain shortened.
        """
        delegate = self.undo
        if delegate is None:
            log.warning("[APP] undo controller unavailable — lazy-init failed earlier; skipping undo")
            return None
        return delegate.undo_last()

    # ─── Waveform bubble config push ───────────────────────────────────

    def push_bubble_config(self, config: Any) -> None:
        """Push a config-changed event to the waveform bubble renderer.

        replaces the private ``getattr(self,
        "_waveform_bubble", None)`` access that lived inline in
        :mod:`voice_typer.server.handlers.config_handlers`'s
        ``apply_config`` side-effect path. The handler now calls this
        public method instead of reaching into the app's private
        ``_waveform_bubble`` attribute.

        Behaviour preserved verbatim from the prior inline block: read
        ``self._waveform_bubble`` (which is ``None`` until
        ``_wire_waveform_bubble`` has run, e.g. during very-early
        config pushes), and if both the bubble and its ``on_config``
        callback are non-None, invoke ``bubble.on_config(config)`` so
        the sandboxed bubble renderer re-reads ``bubble_behavior`` /
        ``bubble_click_to_toggle`` / ``bubble_mic_button`` and
        redraws. ``config`` is the app's :class:`Config` object.
        """
        bubble = getattr(self, "_waveform_bubble", None)
        if bubble is not None and bubble.on_config is not None:
            bubble.on_config(config)

    def _cancel_dictation(self):
        """Delegate to RecordingController.cancel().

        while the frontend HotkeyPicker is in hotkey capture
        mode, the ESC cancel is a no-op — the frontend owns the Escape key
        while capturing.

        NOTE: this reads the *canonical* KeyboardOwnership state via
        ``is_hotkey_capture_active()`` rather than the legacy
        ``self._esc_cancel_paused`` alias. ``_esc_cancel_paused`` is only
        written by the set_esc_cancel_paused IPC handler and could drift out
        of sync with the real ownership (the ESC-release path resets the
        canonical owner but relied on a frontend round-trip to clear the
        alias). Trusting the stale alias made ESC a permanent no-op whenever
        the two diverged — see the ESC-cancel regression fix.
        """
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[CANCEL] ESC cancel paused (frontend hotkey capture) — no-op")
                return
        except Exception:  # pragma: no cover - defensive
            log.debug("[CANCEL] keyboard ownership check failed", exc_info=True)
        self.recording.cancel()

    # ─── Volume Ducking ────────────────────────────────────────────────

    def _on_volume_crash_restore(self, state) -> None:
        """Delegate to VolumeController."""
        self.volume._on_volume_crash_restore(state)

    def _duck_volume(self) -> None:
        """Delegate to VolumeController."""
        self.volume._duck_volume()

    def _restore_volume(self, fade_ms: int | None = None) -> None:
        """Delegate to VolumeController."""
        self.volume._restore_volume(fade_ms=fade_ms)
