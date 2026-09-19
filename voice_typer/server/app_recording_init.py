"""Recording-subsystem init mixin for VoiceTyperApp. Logger: voice_typer.server.app."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server.app_lazy_hub import _RECORDER_MISSING

if TYPE_CHECKING:
    # Type-only import (no runtime cycle): this mixin only ever runs on
    from voice_typer.server.app import VoiceTyperApp

# name: see module docstring.
log = logging.getLogger("voice_typer.server.app")


class AppRecordingInit:
    """Declares NO ``__init__``: construction order and the backing
    attributes stay entirely in ``app.py``; only the builders live here.
    """

    def _init_recording(self: VoiceTyperApp) -> None:
        """``Recorder`` + ``RecordingController`` construction is deferred
        to a background thread. The
        """
        self._recorder_backing: Any = _RECORDER_MISSING
        self._recording_backing: Any = _RECORDER_MISSING
        self._recorder_build_error: BaseException | None = None
        self._recorder_build_ready = threading.Event()

        def _build_recorder_subsystem() -> None:
            try:
                # Setter guard: a test (or a later caller) may have
                if self._recorder_backing is not _RECORDER_MISSING:
                    return
                from voice_typer.server.recording import Recorder

                recorder = Recorder(
                    self.config,
                    audio_processor=self._audio_processor,
                    thread_registry=self._thread_registry,
                )
                if self._recorder_backing is not _RECORDER_MISSING:
                    return  # setter raced us between import + construction
                self._recorder_backing = recorder
                # Recording lifecycle extracted to RecordingController.
                from voice_typer.server.recording_controller import RecordingController

                controller: Any = RecordingController(self)
                if self._recorder_backing is not recorder:
                    return  # setter raced us during controller construction
                self._recording_backing = controller
                # wire xrun threshold callback for tray
                recorder.on_xrun_threshold = controller.on_xrun_threshold
            except Exception as exc:  # noqa: BLE001, surfaced on first access
                self._recorder_build_error = exc
                log.warning(
                    "[INIT] background recorder construction failed (%s)",
                    type(exc).__name__,
                    exc_info=True,
                )
            finally:
                self._recorder_build_ready.set()

        # Eagerly resolve numpy (and the recording package) on the MAIN
        try:
            import numpy  # noqa: F401

            from voice_typer.server.recording import Recorder as _RecorderType  # noqa: F401
        except Exception as exc:  # noqa: BLE001, recorder still retries in its thread
            log.warning(
                "[INIT] eager numpy/recorder pre-import failed (%s), recorder-init thread will retry on demand",
                type(exc).__name__,
            )

        self._thread_registry.spawn_and_register(
            "recorder-init",
            _build_recorder_subsystem,
            daemon=True,
            join_timeout=10.0,
        )
