"""Microphone-test / level-monitor domain mixin for VoiceTyperService."""

import contextlib
import logging

from voice_typer.server.service._app_internals import app_microphones, set_app_microphones
from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class MicrophoneTestMixin(ServiceMixinBase):
    """Microphone / level-monitor service methods."""

    def __init__(self) -> None:
        """initialize MicrophoneTestMixin's own state."""
        self._microphones_cache: list | None = None
        self._microphones_cache_ts: float = 0.0

    def get_microphones(self) -> list[dict]:
        """Return available microphones."""
        return app_microphones(self._app)

    # AUDIO-MIC: refresh the microphone list by re-querying PortAudio.
    def refresh_microphones(self, force: bool = False) -> list[dict]:
        """AUDIO-MIC: Re-query PortAudio for available microphones."""
        import time

        from voice_typer.server.server_platform.microphone_list import list_microphones

        now = time.monotonic()
        # PERF-: serve from cache if fresher than 5s.
        if not force and self._microphones_cache is not None and (now - self._microphones_cache_ts) < 5.0:
            return self._microphones_cache

        try:
            mics = list_microphones()
            set_app_microphones(self._app, mics)
            # PERF-: update the short-TTL cache.
            self._microphones_cache = mics
            self._microphones_cache_ts = now
            with contextlib.suppress(Exception):
                self._app.tray.set_microphones(mics)
            return mics
        except Exception as e:
            log.exception("[SERVICE] refresh_microphones failed: %s", e)
            return app_microphones(self._app)

    # AUDIO-RMS: IPC endpoint for real-time RMS level.
    def get_rms_level(self) -> dict[str, object]:
        """AUDIO-RMS: Return the current RMS level from the recorder.

        Returns dict with 'rms' (float, 0.0 if not recording) and
        """
        try:
            recorder = getattr(self._app, "recorder", None)
            if recorder is None:
                return {"rms": 0.0, "recording": False}
            return {
                "rms": recorder.last_rms,
                "recording": recorder.recording,
            }
        except Exception as e:
            log.debug("[SERVICE] get_rms_level failed: %s", e)
            return {"rms": 0.0, "recording": False}

    def microphone_test_start(
        self, mic_id: str | None = None, duration: float = 10.0, filters: dict | None = None
    ) -> dict[str, object]:
        """Start a microphone test recording.

        Returns:
        """
        from voice_typer.server.level_monitor import start_test_recording as start_test

        return start_test(mic_id=mic_id, duration=duration, filters=filters)

    def microphone_test_stop(self) -> dict[str, object]:
        """Stop the microphone test and persist its WAVs to disk.

        Returns:
        """
        from voice_typer.server.level_monitor import stop_test_recording as stop_test

        result = stop_test()

        # Best-effort auto-transcription of the test recording from the
        audio_file = result.get("audio_file") or {}
        wav_path = audio_file.get("path") if isinstance(audio_file, dict) else None
        if result.get("success") and wav_path:
            try:
                import io
                import wave

                import numpy as np

                with open(wav_path, "rb") as fh:
                    wav_bytes = fh.read()

                # Decode WAV to float32
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    frames = wf.readframes(wf.getnframes())
                    audio_f32 = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767

                # Use the app's already-loaded active engine
                models = getattr(self._app, "models", None)
                if models is None:
                    # HONEST-METRIC INVARIANT: no model subsystem at all
                    result.setdefault("transcription_unavailable", True)
                    result["transcription_reason"] = "no_engine_loaded"
                    log.debug("[SERVICE] Test transcription: no model subsystem")
                else:
                    engine = models.active_transcriber()
                    if engine is not None and getattr(engine, "is_loaded", False):
                        try:
                            transcription = engine.transcribe(audio_f32)
                            text = str(transcription) if transcription else ""
                            if text.strip():
                                result["transcription"] = text
                                result["transcription_confidence"] = None
                                # HU-21: the test-transcription text is the
                                log.debug(
                                    "[SERVICE] Test transcription: %d chars",
                                    len(text),
                                )
                            else:
                                log.debug("[SERVICE] Test transcription: no speech detected")
                        except Exception as tx_err:
                            log.debug("[SERVICE] Test transcription failed: %s", tx_err)
                            # Engine threw mid-transcription: the recording
                            result.setdefault("transcription_unavailable", True)
                            result["transcription_reason"] = "transcription_failed"
                    else:
                        log.debug("[SERVICE] Active engine not loaded, skipping transcription")
                        # Phase 2d degradation matrix (§8.10): the mic
                        result.setdefault("transcription_unavailable", True)
                        result["transcription_reason"] = "no_engine_loaded"
            except Exception as transcribe_err:
                log.debug("[SERVICE] Test transcription setup failed: %s", transcribe_err)
                # An exception here means the recording is non-transcribable
                result.setdefault("transcription_unavailable", True)
                result["transcription_reason"] = "transcription_failed"

        return result

    def microphone_test_read_audio(self, path: str, offset: int, length: int) -> dict[str, object]:
        """Read a chunked slice of a persisted mic-test WAV."""
        from voice_typer.server.level_monitor import read_test_recording_slice

        return read_test_recording_slice(path=path, offset=offset, length=length)

    def microphone_test_cancel(self) -> dict[str, object]:
        """Cancel a running microphone test without returning audio."""
        from voice_typer.server.level_monitor import cancel_test_recording as cancel_test

        return cancel_test()

    def microphone_test_status(self) -> dict[str, object]:
        """Check if a microphone test is currently active."""
        from voice_typer.server.level_monitor import is_test_active

        return {"active": is_test_active()}

    def microphone_test_get_level(self) -> dict[str, object]:
        """Get the current real-time audio level.

        Returns dict with level (0-1), peak (0-1), and active (bool).
        """
        from voice_typer.server.level_monitor import get_level

        return get_level()

    def level_monitor_start(self, mic_id: str | None = None) -> dict[str, object]:
        """Start continuous audio level monitoring.

        Returns:
        """
        from voice_typer.server.level_monitor import (
            start_monitoring,
            update_level_processor,
        )

        result = start_monitoring(mic_id=mic_id)
        # Seed the level processor from the current config. Use the
        try:
            from voice_typer.server.config_applier import to_filter_dict

            update_level_processor(to_filter_dict(self._app.config))
        except Exception:
            log.debug(
                "[SERVICE] level_monitor_start: update_level_processor failed",
                exc_info=True,
            )
        return result

    def level_monitor_stop(self) -> dict[str, object]:
        """Stop continuous audio level monitoring."""
        from voice_typer.server.level_monitor import stop_monitoring

        return stop_monitoring()

    def level_monitor_status(self) -> dict[str, object]:
        """Check if continuous level monitoring is active."""
        from voice_typer.server.level_monitor import is_monitoring

        return {"active": is_monitoring()}
