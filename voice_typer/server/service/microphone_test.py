"""Microphone-test / level-monitor domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( split). Mic enumeration, refresh + caching, RMS level,
mic-test recording lifecycle, and continuous level monitoring.
"""

import contextlib
import logging

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class MicrophoneTestMixin(ServiceMixinBase):
    """Microphone / level-monitor service methods.

    Wraps :mod:`voice_typer.server.level_monitor` and
    :mod:`voice_typer.server.server_platform` so the IPC layer doesn't
    import those modules directly.
    """

    def __init__(self) -> None:
        """initialize MicrophoneTestMixin's own state.

                Previously ``_microphones_cache`` / ``_microphones_cache_ts``
                were initialised in ``VoiceTyperService.__init__`` even
                though they are used ONLY by MicrophoneTestMixin (the
        fat-base-class smell called out in ). They are now
                owned by MicrophoneTestMixin so each mixin is the single
                source of truth for its own state.

        initialised to ``None`` (not ``[]``) so the cache check
                can distinguish "never queried" from "queried and got 0 mics"
                via an ``is not None`` guard. A bare-truthiness check would
                bypass the cache when PortAudio legitimately returned an empty
                list, re-querying PortAudio on every refresh call.
        """
        self._microphones_cache: list | None = None
        self._microphones_cache_ts: float = 0.0

    # ── Microphones ─────────────────────────────────────────────

    def get_microphones(self) -> list[dict]:
        """Return available microphones."""
        return getattr(self._app, "_microphones")  # noqa: B009 — attr not on AppProtocol; getattr returns Any (see providers.py)

    # AUDIO-MIC: refresh the microphone list by re-querying PortAudio.
    def refresh_microphones(self, force: bool = False) -> list[dict]:
        """AUDIO-MIC: Re-query PortAudio for available microphones.

                Called when the user clicks "Refresh Microphones" in the UI
                after plugging in a new USB/BT device. Updates the cached list
                and the tray menu.

        PERF-: a 5s short-TTL cache avoids re-querying PortAudio
                on rapid refresh clicks. The first call after the TTL elapses
                re-queries and refreshes the cache; subsequent calls within
                the window return the cached list. Errors fall back to the
                previously-known list (or the cache if available).

                SVC-8: ``force=True`` bypasses the TTL cache so callers that
                *know* a hot-plug event happened (e.g. the OS device-change
                watcher) can refresh immediately without waiting up to 5 s.

        use ``is not None`` (not bare truthiness) so a cached
                empty list (PortAudio returned 0 mics) is still served from
                cache instead of re-querying PortAudio on every call.
        """
        import time

        from voice_typer.server.server_platform.microphone_list import list_microphones

        now = time.monotonic()
        # PERF-: serve from cache if fresher than 5s.
        # SVC-8: skip the cache check when force=True.
        if not force and self._microphones_cache is not None and (now - self._microphones_cache_ts) < 5.0:
            return self._microphones_cache

        try:
            mics = list_microphones()
            self._app._microphones = mics
            # PERF-: update the short-TTL cache.
            self._microphones_cache = mics
            self._microphones_cache_ts = now
            with contextlib.suppress(Exception):
                self._app.tray.set_microphones(mics)
            return mics
        except Exception as e:
            log.error("[SERVICE] refresh_microphones failed: %s", e)
            return getattr(self._app, "_microphones")  # noqa: B009 — attr not on AppProtocol; getattr returns Any (see providers.py)

    # AUDIO-RMS: IPC endpoint for real-time RMS level.
    def get_rms_level(self) -> dict[str, object]:
        """AUDIO-RMS: Return the current RMS level from the recorder.

        Returns dict with 'rms' (float, 0.0 if not recording) and
        'recording' (bool).
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

        Args:
            mic_id: Device index string or None for system default.
            duration: Recording duration in seconds (default 10).
            filters: Optional dict of audio enhancement filter overrides.

        Returns:
            dict with success, message, duration, sample_rate.
        """
        from voice_typer.server.level_monitor import start_test_recording as start_test

        return start_test(mic_id=mic_id, duration=duration, filters=filters)

    def microphone_test_stop(self) -> dict[str, object]:
        """Stop the microphone test and persist its WAVs to disk.

        The completed WAVs (~1 MB each, over the 1 MiB single-frame IPC
        cap when base64-encoded twice over) are written under
        ``<config>/mic-test-recordings/`` and referenced by path; the
        renderer fetches bytes via the chunked ``microphone_test_read_audio``
        command. Auto-transcription (best-effort) reads the filtered WAV
        file directly — no base64 round-trip.

        Returns:
            dict with success, audio_file, raw_audio_file, duration_ms,
            sample_rate, quality, message, and optionally transcription and
            transcription_confidence.
        """
        from voice_typer.server.level_monitor import stop_test_recording as stop_test

        result = stop_test()

        # Best-effort auto-transcription of the test recording from the
        # persisted filtered WAV (uses the already-loaded active engine).
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
                if models is not None:
                    engine = models.active_transcriber()
                    if engine is not None and getattr(engine, "is_loaded", False):
                        try:
                            transcription = engine.transcribe(audio_f32)
                            text = str(transcription) if transcription else ""
                            if text.strip():
                                result["transcription"] = text
                                result["transcription_confidence"] = None
                                # HU-21: the test-transcription text is the
                                # user's dictated voice content (biometric
                                # PII under GDPR Art. 9) — never log it,
                                # not even truncated. Mirror the dictation
                                # path (dictation_pipeline.py): log only
                                # the char count, at DEBUG (the success is
                                # observable via the IPC response).
                                log.debug(
                                    "[SERVICE] Test transcription: %d chars",
                                    len(text),
                                )
                            else:
                                log.debug("[SERVICE] Test transcription: no speech detected")
                        except Exception as tx_err:
                            log.debug("[SERVICE] Test transcription failed: %s", tx_err)
                    else:
                        log.debug("[SERVICE] Active engine not loaded — skipping transcription")
                        # Phase 2d degradation matrix (§8.10): the mic
                        # test must tell the user WHY the "You said:"
                        # line is missing instead of silently omitting
                        # it. Marker is factual — ``no_engine_loaded``
                        # covers pack-missing (no offline engine) AND
                        # an engine that is still warming up / a cloud
                        # engine mid-connect; the renderer maps it to
                        # the user-facing degradation message.
                        result.setdefault("transcription_unavailable", True)
                        result["transcription_reason"] = "no_engine_loaded"
            except Exception as transcribe_err:
                log.debug("[SERVICE] Test transcription setup failed: %s", transcribe_err)

        return result

    def microphone_test_read_audio(self, path: str, offset: int, length: int) -> dict[str, object]:
        """Read a chunked slice of a persisted mic-test WAV.

        Delegates to :func:`level_monitor.read_test_recording_slice`
        which enforces the recordings-dir containment (SEC boundary —
        this endpoint hands raw bytes back over IPC).
        """
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

        Both the test and the level monitor use the same single PortAudio
        stream (see :mod:`level_monitor`), so there is a single source of
        truth.  The level is always from the level monitor.

        Returns dict with level (0-1), peak (0-1), and active (bool).
        """
        from voice_typer.server.level_monitor import get_level

        return get_level()

    def level_monitor_start(self, mic_id: str | None = None) -> dict[str, object]:
        """Start continuous audio level monitoring.

        Also initialises the audio processor for the live level bar
        from the current noise-filter config so the bar reflects
        enabled filters immediately.

        Args:
            mic_id: Device index string or None for system default.

        Returns:
            dict with success, message, sample_rate.
        """
        from voice_typer.server.level_monitor import (
            start_monitoring,
            update_level_processor,
        )

        result = start_monitoring(mic_id=mic_id)
        # Seed the level processor from the current config. Use the
        # shared ``to_filter_dict`` helper (single source of truth — the
        # same one ``config_applier`` uses) so the dict is COMPLETE. The
        # previous hand-rolled 5-key dict omitted ``noise_filter_notch``
        # (+ eq/compressor/limiter/gate-* keys): ``AudioProcessor``
        # reads them directly, so construction crashed with
        # ``'SimpleNamespace' object has no attribute
        # 'noise_filter_notch'`` — and the partial dict was ALSO stashed
        # as ``_state._level_processor_config``, breaking every later
        # processor rebuild after a device hot-swap.
        try:
            from voice_typer.server.config_applier import to_filter_dict

            update_level_processor(to_filter_dict(self._app.config))
        except Exception:
            # previously pass — silently swallowed update_level_processor failures
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
