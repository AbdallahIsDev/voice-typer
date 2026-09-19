"""Streaming session lifecycle coordination."""

from __future__ import annotations

import logging
import os

from voice_typer.server import event_bus
from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession

log = logging.getLogger(__name__)


def _publish_live_preview_unsupported(cycle_id: str) -> None:
    """Publish the ONE-TIME per-recording "live preview unavailable" signal."""
    try:
        event_bus.publish(
            {
                "type": "transcription_partial",
                "data": {
                    "text": "",
                    "cycle_id": cycle_id,
                    "supported": False,
                },
            },
        )
    except Exception:
        log.debug(
            "[STREAMING] Failed to publish live-preview-unavailable signal",
            exc_info=True,
        )
    # (SEC-026, no python bridge) can show a localized hint instead of
    try:
        event_bus.publish(
            {
                "type": "bubble_set_state",
                "data": {
                    "state": "recording",
                    "live_preview_supported": False,
                },
            },
        )
    except Exception:
        log.debug(
            "[STREAMING] Failed to mirror live-preview-unavailable to bubble",
            exc_info=True,
        )


class StreamingSessionCoordinator:
    """Streaming-session startup + config helpers."""

    def __init__(self) -> None:
        # Stateless helper, all state lives on the controller.
        pass

    def streaming_enabled(self, controller) -> bool:
        """Return whether hidden streaming should run for the next recording."""
        if os.environ.get("VOICE_TYPER_STREAMING") == "0":
            return False
        return controller._app.config.streaming_transcription

    def streaming_config(self, controller) -> StreamingConfig:
        cfg = controller._app.config
        return StreamingConfig(
            enabled=self.streaming_enabled(controller),
            chunk_seconds=cfg.streaming_chunk_seconds,
            step_seconds=cfg.streaming_step_seconds,
            left_overlap_seconds=cfg.streaming_left_overlap_seconds,
            right_guard_seconds=cfg.streaming_right_guard_seconds,
            min_first_chunk_seconds=cfg.streaming_min_first_chunk_seconds,
            silence_threshold=cfg.streaming_silence_threshold,
        )

    def start_streaming_session_if_enabled(self, controller) -> None:
        """Start hidden streaming work for the active recording if enabled."""
        app = controller._app
        controller.set_streaming_session(None)
        if not self.streaming_enabled(controller):
            return

        # Streaming requires ``transcribe_words`` (word-level timestamps).
        active = app.models.active_transcriber()
        if active is not None:
            log.info(
                "[STREAMING] Checking transcriber: %s has transcribe_words=%s",
                type(active).__name__,
                hasattr(active, "transcribe_words"),
            )
            if not hasattr(active, "transcribe_words"):
                log.info(
                    "[STREAMING] Transcriber lacks transcribe_words, skipping streaming (cycle=%s)",
                    app._cycle_id,
                )
                _publish_live_preview_unsupported(getattr(app, "_cycle_id", "") or "")
                return
        else:
            log.info("[STREAMING] No active transcriber, skipping streaming (cycle=%s)", app._cycle_id)
            return

        try:
            session = StreamingTranscriptionSession(
                recorder=app.recorder,
                transcriber=app.models.active_transcriber(),
                config=self.streaming_config(controller),
                sample_rate=app.config.sample_rate,
                # THREAD-REGISTRY: pass the app's registry so the
                thread_registry=getattr(app, "_thread_registry", None),
                # Correlation id echoed in every transcription_partial
                cycle_id=getattr(app, "_cycle_id", "") or "",
                # Residual fence: let the session check whether the
                busy_check=lambda: (
                    app.models.registry.is_busy(app.models.registry.active_name)
                    if getattr(getattr(app, "models", None), "registry", None) is not None
                    else False
                ),
            )
            session.start()
            controller.set_streaming_session(session)
            log.info("[STREAMING] Hidden streaming session started (cycle=%s)", app._cycle_id)
        except Exception as e:
            log.exception("[STREAMING] Failed to start streaming session: %s", e)
            controller.set_streaming_session(None)
