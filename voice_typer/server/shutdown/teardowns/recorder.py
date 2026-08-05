"""Teardown helper for the PortAudio recorder + mic watcher.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_recorder`. The body is unchanged;
only the class boundary moved.

Cross-helper state
------------------
This helper publishes two pieces of state consumed by
:mod:`voice_typer.server.shutdown.teardowns.sounddevice`:

* ``controller._recorder_force_closed`` — True when
  ``recorder.stop()`` / ``recorder.discard()`` timed out (the leaked
  worker thread is still accessing the PortAudio stream).
* ``controller._recorder_teardown_done`` — :class:`threading.Event`
  set when this helper finishes; gives the sounddevice helper a
  happens-before guarantee on the flag read.

Both attributes are owned by :class:`ShutdownController` (initialized in
``__init__``) and are NOT moved here — the sounddevice helper reads them
off the controller.
"""

from __future__ import annotations

import logging

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_recorder(controller) -> None:
    """stop the PortAudio stream (recorder.stop / discard) and
    the mic watcher; join the transcription thread.

    if ``recorder.stop()`` (or ``discard()``) times out, the
    leaked worker thread is still accessing the PortAudio stream.
    We set a local ``recorder_force_closed`` flag, mirror it onto
    ``app.recorder._force_closed`` so the recorder itself can
    short-circuit any later access, and SKIP the downstream
    ``shutdown_mic_watcher`` call. We also signal
    ``controller._recorder_teardown_done`` and set
    ``controller._recorder_force_closed`` so ``_teardown_sounddevice``
    (running concurrently in the parallel batch) can SKIP
    ``sd.stop()`` to avoid a double-stop deadlock.
    """
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
                    # ``Recorder.__init__`` (always present on any real
                    # ``Recorder`` instance), so the write is safe without
                    # ``contextlib.suppress`` — the suppress wrapper would
                    # only mask a real bug.
                    app.recorder._force_closed = True
                    controller._recorder_force_closed = True
                    log.warning(
                        "[SHUTDOWN] recorder.stop() timed out — "
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
                        # present on a real ``Recorder`` instance.
                        app.recorder._force_closed = True
                        controller._recorder_force_closed = True
                        log.warning(
                            "[SHUTDOWN] recorder.discard() timed out — "
                            "marking recorder as force-closed; downstream "
                            "recorder.shutdown_mic_watcher will be skipped"
                        )
                except Exception as e2:
                    log.warning("[SHUTDOWN] recorder.discard() also failed: %s", e2)
    except Exception:
        log.debug("[CLEANUP] recorder stop/discard failed", exc_info=True)

    # PERF-MIC-001: stop the OS-event device watcher. : SKIP
    # this step if ``recorder.stop`` / ``recorder.discard`` timed
    # out above — the leaked worker thread is still accessing the
    # PortAudio stream, and concurrent ``shutdown_mic_watcher``
    # calls can segfault or leave the audio device inconsistent.
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
    # read directly from RecordingController (was a
    # @property delegate previously).
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
    # ``_teardown_sounddevice`` (running concurrently in the parallel
    # batch) and signal that recorder teardown is done. The Event
    # gives the sounddevice helper a happens-before guarantee on the
    # flag read even though both helpers run in the same
    # ThreadPoolExecutor wave.
    controller._recorder_force_closed = recorder_force_closed
    controller._recorder_teardown_done.set()


__all__ = ["teardown_recorder"]
