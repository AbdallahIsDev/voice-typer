"""regression: ``_teardown_sounddevice`` checks ``wait()``
return value + force-aborts streams on timeout.

The bug (follow-up)
--------------------------------
``shutdown_controller._teardown_sounddevice`` calls ``sd.stop()`` to
signal active PortAudio streams to stop, then wraps it via
``_run_with_timeout``. However:

  1. ``sd.stop()`` is the non-blocking SIGNAL — it just sets the stop
     flag on each active stream and returns. The streams may still be
     mid-drain.
  2. The previous code did NOT call ``sd.wait()`` (the bounded drain
     that blocks until each stream has actually closed), so the
     cleanup thread could proceed to the next teardown while a
     PortAudio stream was still alive — racing the recorder / DB
     teardown and leaking the audio device (the next process launch
     fails with "Device unavailable").
  3. PortAudio's stream-close handshake can DEADLOCK on backends
     like WASAPI, where the audio callback holds the stream lock.
     Without a bounded ``wait()`` + force-abort fallback, the cleanup
     thread blocks indefinitely on the deadlock.

The fix (the fix)
--------------
``_teardown_sounddevice`` now does BOTH:

  1. ``sd.stop()`` wrapped in ``_run_with_timeout(timeout=3.0)`` —
     if the signal itself doesn't return within 3s (e.g. the
     PortAudio backend is wedged), log at ERROR and force-abort.
  2. ``sd.wait()`` wrapped in ``_run_with_timeout(timeout=2.0)`` —
     the bounded drain that blocks until streams close. If it times
     out (the dangerous case — PortAudio deadlock), log at ERROR and
     call ``stream.abort()`` on every active stream via the new
     ``_abort_sounddevice_streams`` helper.

The ``_run_with_timeout`` return value is checked explicitly against
the ``TIMEOUT`` sentinel — this is the contract: "wait() return
value is checked". ``Stream.abort()`` is documented as "terminate
the stream immediately" — it bypasses the orderly stop handshake and
invokes ``Pa_AbortStream`` under the hood, releasing the PortAudio
resources the deadlock was holding.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# sounddevice is an optional dependency — skip the entire module
# gracefully if it's not installed (matches the lazy-import pattern
# in shutdown_controller._teardown_sounddevice).
sounddevice = pytest.importorskip("sounddevice")

from voice_typer.server._timeout_utils import TIMEOUT  # noqa: E402
from voice_typer.server.shutdown_controller import ShutdownController  # noqa: E402

# The ``_teardown_sounddevice`` body was extracted to
# ``voice_typer/server/shutdown/teardowns/sounddevice.py`` (Phase 4.5
# god-module decomposition). The source-inspection tests below read the
# body from the extracted module; the dynamic / behavioural tests still
# drive the delegate on ``ShutdownController`` (which forwards to the
# extracted function).
_TEARDOWNS_SOUNDDEVICE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "teardowns",
    "sounddevice.py",
)
_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller.py",
)


def _src() -> str:
    """Read the source of the extracted teardown_sounddevice body.

    Returns the contents of ``shutdown/teardowns/sounddevice.py`` (where
    the body lives post Phase-4.5 extraction). The
    ``_teardown_sounddevice`` method on ``ShutdownController`` is now a
    thin delegate that forwards to ``teardown_sounddevice(controller)``
    defined in that module.
    """
    with open(_TEARDOWNS_SOUNDDEVICE_PATH, encoding="utf-8") as f:
        return f.read()


def _teardown_sounddevice_body() -> str:
    """Return the source slice of the ``teardown_sounddevice`` function
    (the body that used to live inline on ``_teardown_sounddevice``)."""
    src = _src()
    idx = src.find("def teardown_sounddevice(controller) -> None:")
    assert idx > -1, "teardown_sounddevice function must exist in the extracted module"
    next_def = src.find("\ndef ", idx + 1)
    if next_def == -1:
        # Last function in the module — slice to end.
        return src[idx:]
    return src[idx:next_def]


def _make_controller() -> ShutdownController:
    """Build a ShutdownController with a MagicMock app, with the
    recorder-teardown shared state pre-initialised so
    ``_teardown_sounddevice`` can run in isolation."""
    app = MagicMock()
    app._cleanup_done = False
    app._shutting_down = False
    controller = ShutdownController(app)
    # Pre-set the recorder-teardown shared state so
    # _teardown_sounddevice does NOT block on
    # _recorder_teardown_done.wait(). By default, force_closed is
    # False so the sd.stop()/sd.wait() path is exercised.
    controller._recorder_teardown_done = threading.Event()
    controller._recorder_teardown_done.set()
    controller._recorder_force_closed = False
    return controller


# source-level contract ────────────────────────────────────


class TestSounddeviceWaitSource:
    """source-level contract for ``_teardown_sounddevice``."""

    def test_teardown_sounddevice_calls_sd_wait(self):
        """``_teardown_sounddevice`` MUST call ``sd.wait`` (the bounded
        drain) — not just ``sd.stop``. Previously only ``sd.stop`` was
        called; the streams could still be mid-drain when the next
        teardown step proceeded."""
        body = _teardown_sounddevice_body()
        assert "sd.wait" in body, (
            "_teardown_sounddevice must call sd.wait() (the bounded "
            "drain that blocks until streams close) — previously only "
            "sd.stop() was called, leaving streams mid-drain"
        )

    def test_teardown_sounddevice_checks_wait_return_value(self):
        """The ``sd.wait()`` call MUST be wrapped in
        ``_run_with_timeout`` and the return value MUST be checked
        against ``TIMEOUT``. This is the contract: 'wait()
        return value is checked'."""
        body = _teardown_sounddevice_body()
        # The wait call is wrapped in _run_with_timeout.
        assert "_run_with_timeout" in body and "sd.wait" in body, "sd.wait() must be wrapped in _run_with_timeout"
        # The return value is captured into a variable.
        assert "_wait_result" in body, (
            "the sd.wait() return value must be captured into a local variable so it can be checked against TIMEOUT"
        )
        # The return value is checked against TIMEOUT.
        assert "_wait_result is TIMEOUT" in body, (
            "the sd.wait() return value MUST be checked against "
            "TIMEOUT — this is the explicit 'wait() return value is "
            "checked' contract"
        )

    def test_teardown_sounddevice_aborts_on_timeout(self):
        """When ``sd.wait()`` times out (returns ``TIMEOUT``),
        ``_teardown_sounddevice`` MUST call
        ``_abort_sounddevice_streams(sd)`` to force-abort every active
        stream — breaking the PortAudio deadlock that wait() timed out
        on."""
        body = _teardown_sounddevice_body()
        assert "abort_sounddevice_streams" in body, (
            "_teardown_sounddevice must call abort_sounddevice_streams "
            "when sd.wait() or sd.stop() times out — force-abort breaks the "
            "PortAudio deadlock"
        )

    def test_abort_sounddevice_streams_method_exists(self):
        """``abort_sounddevice_streams`` must be defined as a function
        in the extracted teardowns module (post Phase-4.5 the body
        lives there; ``ShutdownController._abort_sounddevice_streams``
        is a thin delegate)."""
        src = _src()
        assert "def abort_sounddevice_streams(controller, sd_module) -> None:" in src, (
            "abort_sounddevice_streams(controller, sd_module) function must be defined"
        )

    def test_abort_sounddevice_streams_calls_stream_abort(self):
        """``abort_sounddevice_streams`` must iterate ``sd._streams``
        (the module-level registry of active streams) and call
        ``stream.abort()`` on each."""
        src = _src()
        idx = src.find("def abort_sounddevice_streams(controller, sd_module) -> None:")
        assert idx > -1
        next_def = src.find("\ndef ", idx + 1)
        body = src[idx:] if next_def == -1 else src[idx:next_def]
        assert "_streams" in body, "abort_sounddevice_streams must iterate the sd._streams registry of active streams"
        assert ".abort()" in body, (
            "abort_sounddevice_streams must call stream.abort() "
            "on each active stream — 'terminate the stream immediately' "
            "(Pa_AbortStream under the hood)"
        )

    def test_timeout_logged_at_error_level(self):
        """When ``sd.wait()`` times out, the log MUST be at ERROR
        level (not DEBUG/WARNING) — PortAudio deadlock is a serious
        condition that operators need to see."""
        body = _teardown_sounddevice_body()
        # Find the wait-timeout block (the ``if _wait_result is TIMEOUT:``
        # branch) and check it logs at ERROR.
        wait_timeout_idx = body.find("if _wait_result is TIMEOUT:")
        assert wait_timeout_idx > -1
        # Slice a generous window for the block.
        block = body[wait_timeout_idx : wait_timeout_idx + 800]
        assert "log.error" in block, (
            "the sd.wait() timeout branch must log at ERROR level (PortAudio deadlock is a serious condition)"
        )


# behavioral tests ─────────────────────────────────────────


class TestSounddeviceWaitBehavior:
    """behavioral verification that ``_teardown_sounddevice``
    checks the ``wait()`` return value and force-aborts on timeout."""

    def test_sd_stop_and_wait_called_when_recorder_not_force_closed(self, monkeypatch):
        """When ``_recorder_force_closed`` is False (recorder teardown
        succeeded), ``_teardown_sounddevice`` must call BOTH
        ``sd.stop()`` and ``sd.wait()``."""
        controller = _make_controller()
        # Build a fake sounddevice module with stop/wait mocks.
        fake_sd = MagicMock()
        stop_calls: list = []
        wait_calls: list = []

        def _track_stop():
            stop_calls.append(1)

        def _track_wait(*args, **kwargs):
            wait_calls.append(1)

        fake_sd.stop = _track_stop
        fake_sd.wait = _track_wait
        fake_sd._streams = []
        # Inject the fake module into sys.modules so ``import
        # sounddevice as sd`` inside _teardown_sounddevice picks it up.
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        assert stop_calls == [1], "_teardown_sounddevice must call sd.stop() when recorder teardown did not time out"
        assert wait_calls == [1], "_teardown_sounddevice must call sd.wait() (the bounded drain) after sd.stop()"

    def test_sd_skipped_when_recorder_force_closed(self, monkeypatch):
        """(preserved): when ``_recorder_force_closed`` is True
        (recorder.stop() / discard() timed out), ``_teardown_sounddevice``
        must SKIP ``sd.stop()`` / ``sd.wait()`` entirely — the leaked
        recorder worker thread is still accessing the PortAudio stream,
        and concurrent sd.stop() can deadlock."""
        controller = _make_controller()
        controller._recorder_force_closed = True
        fake_sd = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        fake_sd.stop.assert_not_called()
        fake_sd.wait.assert_not_called()

    def test_abort_called_when_sd_wait_times_out(self, monkeypatch):
        """when ``sd.wait()`` does not return within the bounded
        timeout (simulated by making wait() block forever),
        ``_teardown_sounddevice`` MUST call
        ``_abort_sounddevice_streams(sd)`` which iterates
        ``sd._streams`` and calls ``stream.abort()`` on each."""
        controller = _make_controller()
        # sd.stop returns immediately; sd.wait blocks forever.
        fake_sd = MagicMock()

        def _blocking_wait(*args, **kwargs):
            # Block long enough for the 2s timeout to fire.
            time.sleep(10)

        fake_sd.stop = MagicMock()
        fake_sd.wait = _blocking_wait
        # Build the active-streams registry: two fake streams whose
        # abort() calls are tracked.
        stream_a = MagicMock()
        stream_b = MagicMock()
        fake_sd._streams = [stream_a, stream_b]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        start = time.monotonic()
        controller._teardown_sounddevice()
        elapsed = time.monotonic() - start

        # The wait must have timed out (~2s) and aborted both streams.
        assert elapsed < 5.0, (
            f"_teardown_sounddevice must not block >5s when "
            f"sd.wait() hangs (the bounded _run_with_timeout must "
            f"fire); took {elapsed:.2f}s"
        )
        (
            stream_a.abort.assert_called_once(),
            ("_abort_sounddevice_streams must call .abort() on stream A when sd.wait() times out"),
        )
        (
            stream_b.abort.assert_called_once(),
            ("_abort_sounddevice_streams must call .abort() on stream B when sd.wait() times out"),
        )

    def test_abort_called_when_sd_stop_times_out(self, monkeypatch):
        """when ``sd.stop()`` itself times out (rare — the
        signal non-blocking call hangs because PortAudio is wedged),
        ``_teardown_sounddevice`` MUST abort streams and return early
        (skip the wait)."""
        controller = _make_controller()
        fake_sd = MagicMock()

        def _blocking_stop():
            time.sleep(10)

        fake_sd.stop = _blocking_stop
        fake_sd.wait = MagicMock()
        stream_a = MagicMock()
        fake_sd._streams = [stream_a]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        start = time.monotonic()
        controller._teardown_sounddevice()
        elapsed = time.monotonic() - start

        assert elapsed < 6.0, (
            f"_teardown_sounddevice must not block >6s when sd.stop() hangs (3s timeout + abort); took {elapsed:.2f}s"
        )
        (
            stream_a.abort.assert_called_once(),
            ("_abort_sounddevice_streams must be called when sd.stop() times out"),
        )
        # sd.wait MUST NOT be called when sd.stop already timed out
        # (we returned early).
        fake_sd.wait.assert_not_called()

    def test_no_abort_when_drain_succeeds(self, monkeypatch):
        """when both ``sd.stop()`` and ``sd.wait()`` return
        successfully (within their timeouts), ``stream.abort()`` MUST
        NOT be called — the orderly drain worked, no force-abort
        needed."""
        controller = _make_controller()
        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        fake_sd.wait = MagicMock()
        stream_a = MagicMock()
        stream_b = MagicMock()
        fake_sd._streams = [stream_a, stream_b]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        stream_a.abort.assert_not_called()
        stream_b.abort.assert_not_called()

    def test_abort_swallows_per_stream_exceptions(self, monkeypatch):
        """``_abort_sounddevice_streams`` is best-effort — if
        one ``stream.abort()`` raises, the others MUST still be
        aborted (one bad stream must not prevent the rest from
        releasing their PortAudio resources)."""
        controller = _make_controller()
        # Make sd.wait time out so the abort path is exercised.
        fake_sd = MagicMock()

        def _blocking_wait(*args, **kwargs):
            time.sleep(10)

        fake_sd.stop = MagicMock()
        fake_sd.wait = _blocking_wait
        # Stream A raises on abort; stream B succeeds.
        stream_a = MagicMock()
        stream_a.abort.side_effect = RuntimeError("simulated abort failure")
        stream_b = MagicMock()
        fake_sd._streams = [stream_a, stream_b]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        # Must not raise (best-effort).
        controller._teardown_sounddevice()

        stream_a.abort.assert_called_once()
        (
            stream_b.abort.assert_called_once(),
            (
                "_abort_sounddevice_streams must continue to stream B "
                "even if stream A's abort() raised (best-effort cleanup)"
            ),
        )

    def test_teardown_never_raises(self, monkeypatch):
        """``_teardown_sounddevice`` must NEVER propagate
        exceptions — every step is guarded by try/except so a failure
        in the sounddevice teardown does not prevent the rest of
        ``_do_cleanup`` from running."""
        controller = _make_controller()
        # Make ``import sounddevice`` raise — exercises the outer
        # try/except in _teardown_sounddevice.
        fake_sd = MagicMock()
        fake_sd.stop.side_effect = RuntimeError("simulated stop failure")
        fake_sd.wait = MagicMock()
        fake_sd._streams = []
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        # Must not raise — _run_with_timeout re-raises the func's
        # exception, but _teardown_sounddevice's outer try/except
        # swallows it.
        controller._teardown_sounddevice()


# TIMEOUT sentinel integration ──────────────────────────────


class TestTimeoutSentinelIntegration:
    """``_run_with_timeout`` returns the ``TIMEOUT`` sentinel
    when the worker doesn't finish in time. ``_teardown_sounddevice``
    must check against this sentinel (NOT against ``None`` or falsy)."""

    def test_timeout_sentinel_is_distinct_from_none(self):
        """The ``TIMEOUT`` sentinel must NOT be ``None`` — callers
        must be able to distinguish 'timed out' from 'returned None'."""
        assert TIMEOUT is not None
        assert TIMEOUT is not False

    def test_wait_result_is_compared_with_is_timeout(self):
        """The source MUST use ``is TIMEOUT`` (identity check) — not
        ``== TIMEOUT`` or truthiness — because TIMEOUT is a singleton
        sentinel."""
        body = _teardown_sounddevice_body()
        assert "is TIMEOUT" in body, (
            "the TIMEOUT check must use `is TIMEOUT` (identity check on the singleton sentinel)"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
