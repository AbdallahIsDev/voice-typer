"""Teardown helpers for sounddevice (PortAudio) streams."""

from __future__ import annotations

import contextlib
import logging

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_sounddevice(controller) -> None:
    """safety-net ``sd.stop()``: skipped when"""
    # parallel budget for a defensive case. Because ``teardown_recorder``
    # the recorder.stop() worker), a rare defensive case. 1.0s is
    _teardown_done = controller._recorder_teardown_done.wait(timeout=1.0)
    if not _teardown_done:
        log.warning(
            "[SHUTDOWN] recorder teardown did NOT signal completion "
            "within 1.0s, skipping sd.stop() to avoid PortAudio "
            "deadlock (leaked worker may still be accessing the stream)"
        )
        return
    if controller._recorder_force_closed:
        log.warning(
            "[SHUTDOWN] skipping sd.stop() because "
            "recorder.stop()/discard() timed out (leaked worker may "
            "still be accessing the PortAudio stream)"
        )
        return
    try:
        import sounddevice as sd

        # ``sd.stop()`` is the non-blocking signal; wrap it
        _stop_result = _run_with_timeout(
            "sounddevice.stop",
            sd.stop,
            timeout=3.0,
        )
        if _stop_result is TIMEOUT:
            log.error(
                "[SHUTDOWN] sd.stop() did not return within 3s, "
                "PortAudio may be deadlocked (stream lock held by a "
                "leaked callback on backends like WASAPI); force-"
                "aborting active streams to release resources"
            )
            abort_sounddevice_streams(controller, sd)
            return

        # ``sd.wait()`` blocks until every active stream has
        _wait_result = _run_with_timeout(
            "sounddevice.wait",
            sd.wait,
            timeout=2.0,
        )
        if _wait_result is TIMEOUT:
            log.error(
                "[SHUTDOWN] sd.wait() did not return within 2s, "
                "PortAudio streams did not drain (potential deadlock "
                "on backends like WASAPI); force-aborting active "
                "streams to release the audio device"
            )
            abort_sounddevice_streams(controller, sd)
    except Exception:
        log.debug("[CLEANUP] sd.stop()/wait() failed", exc_info=True)


def abort_sounddevice_streams(controller, sd_module) -> None:
    """force-abort every active sounddevice stream."""
    try:
        streams = [s for s in getattr(sd_module, "_streams", []) if s is not None]
        for stream in streams:
            with contextlib.suppress(Exception):
                stream.abort()
    except Exception:
        log.debug(
            "[SHUTDOWN] _abort_sounddevice_streams fallback failed",
            exc_info=True,
        )


__all__ = ["teardown_sounddevice", "abort_sounddevice_streams"]
