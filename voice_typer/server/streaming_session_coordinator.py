"""Streaming-session coordinator — extracted from ``RecordingController``
(Phase 4.5 split).

Owns the streaming-session **startup** path: checking whether hidden
streaming is enabled for the active recording, building the
``StreamingConfig``, and constructing + starting the
``StreamingTranscriptionSession``.

What stays on ``RecordingController``
--------------------------------------
The streaming-session **accessors** (``get_streaming_session`` /
``set_streaming_session`` / ``pop_streaming_session``) and the
**cancel** helper (``_cancel_streaming_session``) remain on the
controller. They are tightly coupled to the controller's
``_streaming_session`` / ``_streaming_session_lock`` /
``_pending_finalize_session`` state, and several static-source checks
in the test-suite pin their source to the controller module
(``inspect.getsource(RecordingController.pop_streaming_session)`` etc.).
Moving them here would break those tripwires; keeping them on the
controller preserves the ARCH-018 atomic-pop contract verbatim.

Collaborator pattern
--------------------
:class:`StreamingSessionCoordinator` is constructed by
``RecordingController.__init__`` with NO arguments (stateless). Each
method takes a back-reference to the owning ``RecordingController``
(``controller``) and reads ``controller._app``,
``controller._streaming_session_lock``, etc.

Originally lines 1611–1675 of ``recording_controller.py`` (the
``_streaming_enabled`` / ``_streaming_config`` /
``_start_streaming_session_if_enabled`` methods).
"""

from __future__ import annotations

import logging
import os

from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession

log = logging.getLogger(__name__)


class StreamingSessionCoordinator:
    """Streaming-session startup + config helpers.

    Extracted from the former ``RecordingController._streaming_enabled``
    / ``_streaming_config`` / ``_start_streaming_session_if_enabled``
    methods. Each method's body is the moved implementation, with
    ``self.X`` references rewritten to ``controller.X`` for shared state.
    ``RecordingController`` keeps 1-line delegators on each method name so
    existing call sites and source-inspection checks continue to work.
    """

    def __init__(self) -> None:
        # Stateless helper — all state lives on the controller.
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
        # Only Whisper supports this; skip for Parakeet/Qwen.
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
                # streaming worker is tracked for coordinated shutdown.
                # ``getattr`` with default ``None`` keeps this robust
                # if a test constructs RecordingController with a mock
                # app that doesn't have ``_thread_registry``.
                thread_registry=getattr(app, "_thread_registry", None),
            )
            session.start()
            controller.set_streaming_session(session)
            log.info("[STREAMING] Hidden streaming session started (cycle=%s)", app._cycle_id)
        except Exception as e:
            log.exception("[STREAMING] Failed to start streaming session: %s", e)
            controller.set_streaming_session(None)
