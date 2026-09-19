"""TeardownsMixin, thin teardown delegates on ``ShutdownController``."""

from __future__ import annotations


class TeardownsMixin:
    """Thin teardown-delegate mixin for :class:`ShutdownController`."""

    def _teardown_timers_and_recording(self) -> None:
        """cancel pending timers + drain in-flight timer threads,"""
        from voice_typer.server.shutdown.teardowns.timers_and_recording import (
            teardown_timers_and_recording,
        )

        teardown_timers_and_recording(self)

    def _teardown_recorder(self) -> None:
        """stop the PortAudio stream (recorder.stop / discard) and"""
        from voice_typer.server.shutdown.teardowns.recorder import (
            teardown_recorder,
        )

        teardown_recorder(self)

    def _teardown_level_monitor(self) -> None:
        """stop the level_monitor module's PortAudio InputStream +"""
        from voice_typer.server.shutdown.teardowns.level_monitor import (
            teardown_level_monitor,
        )

        teardown_level_monitor(self)

    def _teardown_restore_volume(self) -> None:
        """restore OS volume if it was ducked when the app quit."""
        from voice_typer.server.shutdown.teardowns.volume import (
            teardown_restore_volume,
        )

        teardown_restore_volume(self)

    def _teardown_hotkeys(self) -> None:
        """stop all three hotkey backends (dictation / ESC / repaste)"""
        from voice_typer.server.shutdown.teardowns.hotkeys import (
            teardown_hotkeys,
        )

        teardown_hotkeys(self)

    def _teardown_crash_recovery(self) -> None:
        """flush pending crash-recovery writes + shutdown the writer."""
        from voice_typer.server.shutdown.teardowns.crash_recovery import (
            teardown_crash_recovery,
        )

        teardown_crash_recovery(self)

    def _teardown_history_db(self) -> None:
        """flush pending fire-and-forget history DB writes + close"""
        from voice_typer.server.shutdown.teardowns.history_db import (
            teardown_history_db,
        )

        teardown_history_db(self)

    def _teardown_waveform_wiring(self) -> None:
        """stop the bubble level / waveform worker so it doesn't"""
        from voice_typer.server.shutdown.teardowns.waveform import (
            teardown_waveform_wiring,
        )

        teardown_waveform_wiring(self)

    def _teardown_sounddevice(self) -> None:
        """safety-net ``sd.stop()``: skipped when"""
        from voice_typer.server.shutdown.teardowns.sounddevice import (
            teardown_sounddevice,
        )

        teardown_sounddevice(self)

    def _abort_sounddevice_streams(self, sd_module) -> None:
        """force-abort every active sounddevice stream."""
        from voice_typer.server.shutdown.teardowns.sounddevice import (
            abort_sounddevice_streams,
        )

        abort_sounddevice_streams(self, sd_module)

    def _teardown_host_child(self) -> None:
        """No-op placeholder: the child-process teardown path no longer exists."""
        return None

    def _teardown_pid_file(self) -> None:
        """clear the backend PID file so a subsequent launch isn't"""
        from voice_typer.server.shutdown.teardowns.pid_file import (
            teardown_pid_file,
        )

        teardown_pid_file(self)

    def _teardown_session_marker(self) -> None:
        """clear the session-active marker so the next launch treats the"""
        from voice_typer.server.shutdown.teardowns.session_marker import (
            teardown_session_marker,
        )

        teardown_session_marker(self)

    def _teardown_mutex_handle(self) -> None:
        """release the single-instance mutex handle."""
        from voice_typer.server.shutdown.teardowns.mutex import (
            teardown_mutex_handle,
        )

        teardown_mutex_handle(self)

    def _teardown_devnull_files(self) -> None:
        """close devnull streams opened during logging setup."""
        from voice_typer.server.shutdown.teardowns.devnull import (
            teardown_devnull_files,
        )

        teardown_devnull_files(self)

    def _teardown_asr_models(self) -> None:
        """unload active ASR backend + release CUDA caching allocator"""
        from voice_typer.server.shutdown.teardowns.asr_models import (
            teardown_asr_models,
        )

        teardown_asr_models(self)

    def _teardown_event_bus(self) -> None:
        """shut down the event_bus deferred-publish executor."""
        from voice_typer.server.shutdown.teardowns.event_bus import (
            teardown_event_bus,
        )

        teardown_event_bus(self)
