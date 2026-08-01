"""Teardown helpers for sounddevice (PortAudio) streams.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_sounddevice` and
:meth:`ShutdownController._abort_sounddevice_streams`. The bodies are
unchanged; only the class boundary moved.

Cross-helper state
------------------
:func:`teardown_sounddevice` reads two attributes off the owning
:class:`ShutdownController` (set by
:mod:`voice_typer.server.shutdown.teardowns.recorder`):

* ``controller._recorder_teardown_done`` — :class:`threading.Event`
  signaled when the recorder teardown helper finished.
* ``controller._recorder_force_closed`` — True when
  ``recorder.stop()`` / ``recorder.discard()`` timed out. When True,
  this helper SKIPS ``sd.stop()`` / ``sd.wait()`` (the leaked worker
  thread is still accessing the PortAudio stream; calling ``sd.stop``
  can deadlock on backends like WASAPI where the stream lock is held).

:func:`abort_sounddevice_streams` is called from
:func:`teardown_sounddevice` when ``sd.stop`` or ``sd.wait`` times out
— it force-aborts every active PortAudio stream to release the audio
device.
"""

from __future__ import annotations

import contextlib
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


def teardown_sounddevice(controller) -> None:
    """safety-net ``sd.stop()`` — skipped when
    ``recorder.stop()`` (or ``discard()``) timed out.

    if recorder.stop() above failed or an audio callback
    leaked a stream, this ensures sounddevice doesn't hold the
    microphone. : SKIP this call when the recorder teardown
    timed out — the leaked recorder.stop() worker thread is still
    holding the PortAudio stream lock, and calling ``sd.stop()``
    while that lock is held deadlocks the cleanup thread on
    PortAudio backends (notably WASAPI).

    This helper waits for ``_teardown_recorder`` to finish (via
    ``_recorder_teardown_done``) before reading the
    ``_recorder_force_closed`` flag, giving a happens-before
    guarantee even though both helpers run concurrently in the
    parallel batch.

    ``sd.stop()`` is the non-blocking signal that asks every
    active PortAudio stream to stop; ``sd.wait()`` is the bounded
    drain that blocks until each stream has actually closed. Both
    are wrapped via :func:`_run_with_timeout` so the cleanup thread
    is never blocked indefinitely. The ``_run_with_timeout`` return
    value is checked against :data:`TIMEOUT` — if either call times
    out (the ``wait()`` case is the dangerous one because
    PortAudio's stream-close handshake can deadlock on backends
    like WASAPI where the audio callback holds the stream lock),
    we log at ERROR and force-abort every active stream via
    :func:`abort_sounddevice_streams` (which calls
    ``stream.abort()`` on each — ``abort()`` is documented to
    "terminate the stream immediately", bypassing the orderly
    stop handshake and releasing the PortAudio resources the
    deadlock was holding).
    """
    # Wait for recorder teardown to complete (it sets
    # _recorder_force_closed). Bound the wait at 9.5s so the outer
    # _run_with_timeout(10.0) wrapper still has 0.5s slack to log
    # and return if the recorder helper genuinely finishes near the
    # shared deadline.
    controller._recorder_teardown_done.wait(timeout=9.5)
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
        # so a wedged PortAudio backend (e.g. WASAPI stream lock
        # held by a leaked callback) cannot block the cleanup
        # thread indefinitely. If the call times out, force-abort
        # every active stream — ``abort()`` bypasses the orderly
        # stop handshake and breaks the deadlock.
        _stop_result = _run_with_timeout(
            "sounddevice.stop",
            sd.stop,
            timeout=3.0,
        )
        if _stop_result is TIMEOUT:
            log.error(
                "[SHUTDOWN] sd.stop() did not return within 3s — "
                "PortAudio may be deadlocked (stream lock held by a "
                "leaked callback on backends like WASAPI); force-"
                "aborting active streams to release resources"
            )
            abort_sounddevice_streams(sd)
            return

        # ``sd.wait()`` blocks until every active stream has
        # actually drained. PortAudio's stream-close handshake can
        # deadlock on backends where the audio callback holds the
        # stream lock; without a bounded wait, this would block
        # shutdown indefinitely. Wrap it; on timeout, log at ERROR
        # and force-abort the streams (the wait() return value is
        # checked explicitly against TIMEOUT).
        _wait_result = _run_with_timeout(
            "sounddevice.wait",
            sd.wait,
            timeout=2.0,
        )
        if _wait_result is TIMEOUT:
            log.error(
                "[SHUTDOWN] sd.wait() did not return within 2s — "
                "PortAudio stream(s) did not drain (potential deadlock "
                "on backends like WASAPI); force-aborting active "
                "streams to release the audio device"
            )
            abort_sounddevice_streams(sd)
    except Exception:
        log.debug("[CLEANUP] sd.stop()/wait() failed", exc_info=True)


def abort_sounddevice_streams(controller, sd_module) -> None:
    """force-abort every active sounddevice stream.

    ``sounddevice._streams`` is the module-level registry of active
    ``sd.Stream`` / ``sd.InputStream`` / ``sd.OutputStream`` instances
    that ``sd.stop()`` and ``sd.wait()`` operate on. When the
    orderly drain times out (a PortAudio deadlock — the audio
    callback is holding the stream lock and the close handshake
    cannot complete), iterate a snapshot of the registry and call
    ``stream.abort()`` on each.

    ``Stream.abort()`` is documented as "Terminate the stream
    immediately" — it sets the stream's ``_CallbackFlags`` and
    invokes ``Pa_AbortStream`` under the hood, which closes the
    stream without waiting for in-flight audio callbacks to drain.
    This breaks the deadlock by releasing the PortAudio resources
    the leaked callback was holding, so the audio device is
    available for the next process launch (without this, the next
    launch fails with "Device unavailable" because the OS still
    sees the stream as in-use).

    Best-effort: per-stream failures are suppressed
    (``contextlib.suppress(Exception)``) so one bad stream does
    not prevent the abort of the others. The ``_streams`` list is
    snapshotted before iteration to avoid mutation-during-iteration
    if ``abort()`` removes the stream from the registry.

    The ``controller`` argument is unused but kept for API symmetry
    with the other teardown helpers (all take ``controller`` as the
    first positional arg so the :class:`ShutdownController` delegate
    methods can call ``<helper>(self, ...)`` uniformly).
    """
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
