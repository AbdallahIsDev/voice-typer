"""Teardown helper for the PortAudio recorder + mic watcher."""

from __future__ import annotations

import logging

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_recorder(controller) -> None:
    """stop the PortAudio stream (recorder.stop / discard) and"""
    app = controller._app
    recorder_force_closed = False
    try:
        if app.recorder is not None and app.recorder.recording:
            try:
                _stop_result = _run_with_timeout(
                    "recorder.stop",
                    app.recorder.stop,
                    timeout=5.0,
                )
                if _stop_result is TIMEOUT:
                    recorder_force_closed = True
                    # The ``_force_closed`` field is declared on
                    app.recorder._force_closed = True
                    controller._recorder_force_closed = True
                    log.warning(
                        "[SHUTDOWN] recorder.stop() timed out, "
                        "marking recorder as force-closed; downstream "
                        "recorder.shutdown_mic_watcher will be skipped"
                    )
            except Exception as e:
                log.warning("[SHUTDOWN] recorder.stop() failed: %s, trying discard()", e)
                try:
                    _discard_result = _run_with_timeout(
                        "recorder.discard",
                        app.recorder.discard,
                        timeout=5.0,
                    )
                    if _discard_result is TIMEOUT:
                        recorder_force_closed = True
                        # See note above: ``_force_closed`` is always
                        app.recorder._force_closed = True
                        controller._recorder_force_closed = True
                        log.warning(
                            "[SHUTDOWN] recorder.discard() timed out, "
                            "marking recorder as force-closed; downstream "
                            "recorder.shutdown_mic_watcher will be skipped"
                        )
                except Exception as e2:
                    log.warning("[SHUTDOWN] recorder.discard() also failed: %s", e2)
    except Exception:
        log.debug("[CLEANUP] recorder stop/discard failed", exc_info=True)

    # PERF-MIC-001: stop the OS-event device watcher. : SKIP
    try:
        if app.recorder is not None and not recorder_force_closed:
            _mic_watcher_result = _run_with_timeout(
                "recorder.shutdown_mic_watcher",
                app.recorder.shutdown_mic_watcher,
                timeout=5.0,
            )
            if _mic_watcher_result is TIMEOUT:
                log.warning("[SHUTDOWN] recorder.shutdown_mic_watcher timed out")
        elif recorder_force_closed:
            log.warning(
                "[SHUTDOWN] skipping recorder.shutdown_mic_watcher "
                "because recorder.stop()/discard() timed out (leaked worker "
                "may still be accessing the PortAudio stream)"
            )
    except Exception as e:
        log.debug("[SHUTDOWN] mic watcher shutdown failed: %s", e)

    # Wait for any running transcription thread to finish (short timeout).
    try:
        if hasattr(app, "recording") and app.recording is not None:
            t = app.recording._transcription_thread
            if t is not None and t.is_alive():
                log.info("[SHUTDOWN] Waiting for transcription thread to finish...")
                t.join(timeout=3.0)
                if t.is_alive():
                    log.warning("[SHUTDOWN] Transcription thread did not finish in time, continuing shutdown")
    except Exception:
        log.debug("[CLEANUP] transcription thread join failed", exc_info=True)

    # publish the force-closed flag for
    controller._recorder_force_closed = recorder_force_closed
    controller._recorder_teardown_done.set()


__all__ = ["teardown_recorder"]
