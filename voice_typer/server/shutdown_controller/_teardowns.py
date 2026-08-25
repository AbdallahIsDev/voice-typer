"""TeardownsMixin — thin teardown delegates on ``ShutdownController``.

Split verbatim out of the pre-split ``shutdown_controller`` module.
Each helper is a thin delegate that calls the standalone function in
:mod:`voice_typer.server.shutdown.teardowns` (extracted so the
controller class body shrinks to orchestration only). The delegate
indirection is kept so:

  * tests that ``monkeypatch.setattr(controller, "_teardown_X",
    spy)`` still intercept the call (see
    ``tests/test_shutdown_parallel.py``); and
  * the sequenced-phase list and parallel-batch list in
    ``_do_cleanup`` keep referencing ``self._teardown_X`` (the
    callable attribute must remain on the controller instance).

The standalone functions all take ``controller`` as their first
positional argument so they can read ``controller._app`` and
(in two cases) the shared synchronization state
(``_recorder_teardown_done`` / ``_recorder_force_closed`` /
``_electron_pid_lock``) initialized in ``__init__``.
"""

from __future__ import annotations


class TeardownsMixin:
    """Thin teardown-delegate mixin for :class:`ShutdownController`."""

    def _teardown_timers_and_recording(self) -> None:
        """cancel pending timers + drain in-flight timer threads,
        stop the recording watchdog, and atomically pop the streaming
        session.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.timers_and_recording.teardown_timers_and_recording`.
        """
        from voice_typer.server.shutdown.teardowns.timers_and_recording import (
            teardown_timers_and_recording,
        )

        teardown_timers_and_recording(self)

    def _teardown_recorder(self) -> None:
        """stop the PortAudio stream (recorder.stop / discard) and
        the mic watcher; join the transcription thread.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.recorder.teardown_recorder`.
        Publishes ``_recorder_force_closed`` / ``_recorder_teardown_done``
        on this controller for the sounddevice helper's happens-before
        guarantee.
        """
        from voice_typer.server.shutdown.teardowns.recorder import (
            teardown_recorder,
        )

        teardown_recorder(self)

    def _teardown_level_monitor(self) -> None:
        """stop the level_monitor module's PortAudio InputStream +
        worker thread.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.level_monitor.teardown_level_monitor`.
        """
        from voice_typer.server.shutdown.teardowns.level_monitor import (
            teardown_level_monitor,
        )

        teardown_level_monitor(self)

    def _teardown_restore_volume(self) -> None:
        """restore OS volume if it was ducked when the app quit.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.volume.teardown_restore_volume`.
        """
        from voice_typer.server.shutdown.teardowns.volume import (
            teardown_restore_volume,
        )

        teardown_restore_volume(self)

    def _teardown_hotkeys(self) -> None:
        """stop all three hotkey backends (dictation / ESC / repaste)
        in a nested parallel batch.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.hotkeys.teardown_hotkeys`.
        """
        from voice_typer.server.shutdown.teardowns.hotkeys import (
            teardown_hotkeys,
        )

        teardown_hotkeys(self)

    def _teardown_crash_recovery(self) -> None:
        """flush pending crash-recovery writes + shutdown the writer.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.crash_recovery.teardown_crash_recovery`.
        """
        from voice_typer.server.shutdown.teardowns.crash_recovery import (
            teardown_crash_recovery,
        )

        teardown_crash_recovery(self)

    def _teardown_history_db(self) -> None:
        """flush pending fire-and-forget history DB writes + close
        the DB (joins the writer thread).

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.history_db.teardown_history_db`.
        """
        from voice_typer.server.shutdown.teardowns.history_db import (
            teardown_history_db,
        )

        teardown_history_db(self)

    def _teardown_waveform_wiring(self) -> None:
        """stop the bubble level / waveform worker so it doesn't
        try to push to a torn-down IPC server during shutdown.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.waveform.teardown_waveform_wiring`.
        """
        from voice_typer.server.shutdown.teardowns.waveform import (
            teardown_waveform_wiring,
        )

        teardown_waveform_wiring(self)

    def _teardown_sounddevice(self) -> None:
        """safety-net ``sd.stop()`` — skipped when
        ``recorder.stop()`` (or ``discard()``) timed out.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.sounddevice.teardown_sounddevice`.
        Reads ``self._recorder_teardown_done`` / ``_recorder_force_closed``
        (set by :meth:`_teardown_recorder`) for the happens-before
        guarantee.
        """
        from voice_typer.server.shutdown.teardowns.sounddevice import (
            teardown_sounddevice,
        )

        teardown_sounddevice(self)

    def _abort_sounddevice_streams(self, sd_module) -> None:
        """force-abort every active sounddevice stream.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.sounddevice.abort_sounddevice_streams`.
        """
        from voice_typer.server.shutdown.teardowns.sounddevice import (
            abort_sounddevice_streams,
        )

        abort_sounddevice_streams(self, sd_module)

    def _teardown_electron(self) -> None:
        """terminate the Electron subprocess.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.electron.teardown_electron`.
        Acquires ``self._electron_pid_lock`` (initialized in ``__init__``)
        around the read-terminate-clear critical section.
        """
        from voice_typer.server.shutdown.teardowns.electron import (
            teardown_electron,
        )

        teardown_electron(self)

    def _teardown_pid_file(self) -> None:
        """clear the backend PID file so a subsequent launch isn't
        falsely blocked by the single-instance check.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.pid_file.teardown_pid_file`.
        """
        from voice_typer.server.shutdown.teardowns.pid_file import (
            teardown_pid_file,
        )

        teardown_pid_file(self)

    def _teardown_session_marker(self) -> None:
        """clear the session-active marker so the next launch treats the
        previous session as clean (no crash notification).

        Runs as the FIRST sequenced teardown (see
        ``_build_sequenced_plan``). Body lives in
        :func:`voice_typer.server.shutdown.teardowns.session_marker.teardown_session_marker`.
        """
        from voice_typer.server.shutdown.teardowns.session_marker import (
            teardown_session_marker,
        )

        teardown_session_marker(self)

    def _teardown_mutex_handle(self) -> None:
        """release the single-instance mutex handle.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.mutex.teardown_mutex_handle`.
        """
        from voice_typer.server.shutdown.teardowns.mutex import (
            teardown_mutex_handle,
        )

        teardown_mutex_handle(self)

    def _teardown_devnull_files(self) -> None:
        """close devnull streams opened during logging setup.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.devnull.teardown_devnull_files`.
        """
        from voice_typer.server.shutdown.teardowns.devnull import (
            teardown_devnull_files,
        )

        teardown_devnull_files(self)

    def _teardown_asr_models(self) -> None:
        """unload active ASR backend + release CUDA caching allocator
        blocks so torch's VRAM is returned to the OS before process exit.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.asr_models.teardown_asr_models`.
        """
        from voice_typer.server.shutdown.teardowns.asr_models import (
            teardown_asr_models,
        )

        teardown_asr_models(self)

    def _teardown_event_bus(self) -> None:
        """shut down the event_bus deferred-publish executor.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.event_bus.teardown_event_bus`.
        """
        from voice_typer.server.shutdown.teardowns.event_bus import (
            teardown_event_bus,
        )

        teardown_event_bus(self)
