"""regression: ``_teardown_sounddevice`` checks ``wait()``"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

sounddevice = pytest.importorskip("sounddevice")

from voice_typer.server._timeout_utils import TIMEOUT  # noqa: E402
from voice_typer.server.shutdown_controller import ShutdownController  # noqa: E402

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
    """Read the source of the extracted teardown_sounddevice body."""
    with open(_TEARDOWNS_SOUNDDEVICE_PATH, encoding="utf-8") as f:
        return f.read()


def _teardown_sounddevice_body() -> str:
    """Return the source slice of the ``teardown_sounddevice`` function"""
    src = _src()
    idx = src.find("def teardown_sounddevice(controller) -> None:")
    assert idx > -1, "teardown_sounddevice function must exist in the extracted module"
    next_def = src.find("\ndef ", idx + 1)
    if next_def == -1:
        # Last function in the module, slice to end.
        return src[idx:]
    return src[idx:next_def]


def _make_controller() -> ShutdownController:
    """recorder-teardown shared state pre-initialised so"""
    app = MagicMock()
    app._cleanup_done = False
    app._shutting_down = False
    controller = ShutdownController(app)
    controller._recorder_teardown_done = threading.Event()
    controller._recorder_teardown_done.set()
    controller._recorder_force_closed = False
    return controller


class TestSounddeviceWaitSource:
    """source-level contract for ``_teardown_sounddevice``."""

    def test_teardown_sounddevice_calls_sd_wait(self):
        """``_teardown_sounddevice`` MUST call ``sd.wait`` (the bounded"""
        body = _teardown_sounddevice_body()
        assert "sd.wait" in body, (
            "_teardown_sounddevice must call sd.wait() (the bounded "
            "drain that blocks until streams close), previously only "
            "sd.stop() was called, leaving streams mid-drain"
        )

    def test_teardown_sounddevice_checks_wait_return_value(self):
        """``_run_with_timeout`` and the return value MUST be checked"""
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
            "TIMEOUT, this is the explicit 'wait() return value is "
            "checked' contract"
        )

    def test_teardown_sounddevice_aborts_on_timeout(self):
        """When ``sd.wait()`` times out (returns ``TIMEOUT``),"""
        body = _teardown_sounddevice_body()
        assert "abort_sounddevice_streams" in body, (
            "_teardown_sounddevice must call abort_sounddevice_streams "
            "when sd.wait() or sd.stop() times out, force-abort breaks the "
            "PortAudio deadlock"
        )

    def test_abort_sounddevice_streams_method_exists(self):
        """``abort_sounddevice_streams`` must be defined as a function"""
        src = _src()
        assert "def abort_sounddevice_streams(controller, sd_module) -> None:" in src, (
            "abort_sounddevice_streams(controller, sd_module) function must be defined"
        )

    def test_abort_sounddevice_streams_calls_stream_abort(self):
        """``abort_sounddevice_streams`` must iterate ``sd._streams``"""
        src = _src()
        idx = src.find("def abort_sounddevice_streams(controller, sd_module) -> None:")
        assert idx > -1
        next_def = src.find("\ndef ", idx + 1)
        body = src[idx:] if next_def == -1 else src[idx:next_def]
        assert "_streams" in body, "abort_sounddevice_streams must iterate the sd._streams registry of active streams"
        assert ".abort()" in body, (
            "abort_sounddevice_streams must call stream.abort() "
            "on each active stream: 'terminate the stream immediately' "
            "(Pa_AbortStream under the hood)"
        )

    def test_timeout_logged_at_error_level(self):
        """When ``sd.wait()`` times out, the log MUST be at ERROR"""
        body = _teardown_sounddevice_body()
        # Find the wait-timeout block (the ``if _wait_result is TIMEOUT:``
        wait_timeout_idx = body.find("if _wait_result is TIMEOUT:")
        assert wait_timeout_idx > -1
        # Slice a generous window for the block.
        block = body[wait_timeout_idx : wait_timeout_idx + 800]
        assert "log.error" in block, (
            "the sd.wait() timeout branch must log at ERROR level (PortAudio deadlock is a serious condition)"
        )


class TestSounddeviceWaitBehavior:
    """behavioral verification that ``_teardown_sounddevice``"""

    def test_sd_stop_and_wait_called_when_recorder_not_force_closed(self, monkeypatch):
        """When ``_recorder_force_closed`` is False (recorder teardown"""
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
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        assert stop_calls == [1], "_teardown_sounddevice must call sd.stop() when recorder teardown did not time out"
        assert wait_calls == [1], "_teardown_sounddevice must call sd.wait() (the bounded drain) after sd.stop()"

    def test_sd_skipped_when_recorder_force_closed(self, monkeypatch):
        """(preserved): when ``_recorder_force_closed`` is True"""
        controller = _make_controller()
        controller._recorder_force_closed = True
        fake_sd = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        fake_sd.stop.assert_not_called()
        fake_sd.wait.assert_not_called()

    def test_abort_called_when_sd_wait_times_out(self, monkeypatch):
        """when ``sd.wait()`` does not return within the bounded"""
        controller = _make_controller()
        fake_sd = MagicMock()

        def _blocking_wait(*args, **kwargs):
            # Block long enough for the 2s timeout to fire, but not
            time.sleep(3)

        fake_sd.stop = MagicMock()
        fake_sd.wait = _blocking_wait
        # Build the active-streams registry: two fake streams whose
        stream_a = MagicMock()
        stream_b = MagicMock()
        fake_sd._streams = [stream_a, stream_b]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        start = time.monotonic()
        controller._teardown_sounddevice()
        elapsed = time.monotonic() - start

        # The wait must have timed out (~2s) and aborted both streams.
        assert elapsed < 8.0, (
            f"_teardown_sounddevice must not block >8s when "
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
        """signal non-blocking call hangs because PortAudio is wedged),"""
        controller = _make_controller()
        fake_sd = MagicMock()

        def _blocking_stop():
            # MO-85: just above the product's 3s stop timeout.
            time.sleep(4)

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
        fake_sd.wait.assert_not_called()

    def test_no_abort_when_drain_succeeds(self, monkeypatch):
        """when both ``sd.stop()`` and ``sd.wait()`` return"""
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
        """``_abort_sounddevice_streams`` is best-effort, if"""
        controller = _make_controller()
        # Make sd.wait time out so the abort path is exercised.
        fake_sd = MagicMock()

        def _blocking_wait(*args, **kwargs):
            # MO-85: just above the 2s product timeout.
            time.sleep(3)

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
        """``_teardown_sounddevice`` must NEVER propagate"""
        controller = _make_controller()
        # Make ``import sounddevice`` raise, exercises the outer
        fake_sd = MagicMock()
        fake_sd.stop.side_effect = RuntimeError("simulated stop failure")
        fake_sd.wait = MagicMock()
        fake_sd._streams = []
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        # Must not raise, _run_with_timeout re-raises the func's
        controller._teardown_sounddevice()


# TIMEOUT sentinel integration ──────────────────────────────


class TestTimeoutSentinelIntegration:
    """``_run_with_timeout`` returns the ``TIMEOUT`` sentinel"""

    def test_timeout_sentinel_is_distinct_from_none(self):
        """The ``TIMEOUT`` sentinel must NOT be ``None``, callers"""
        assert TIMEOUT is not None
        assert TIMEOUT is not False

    def test_wait_result_is_compared_with_is_timeout(self):
        """The source MUST use ``is TIMEOUT`` (identity check), not"""
        body = _teardown_sounddevice_body()
        assert "is TIMEOUT" in body, (
            "the TIMEOUT check must use `is TIMEOUT` (identity check on the singleton sentinel)"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
