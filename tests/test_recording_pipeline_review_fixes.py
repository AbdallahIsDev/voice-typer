"""Regression tests for the recording-pipeline refactor (WAVE3-A00).

Pins the REC-1 / REC-2 / REC-8 contracts that were lost when
``Recorder.start`` / ``stop`` / ``discard`` were extracted into
``voice_typer/server/recording/_recorder_split.py`` (Phase 4.5
god-module split). The contracts are:

* REC-1: ``_stop_audio_worker`` must NOT clear the stop event or null
  ``_worker_thread`` when the worker is still alive after the join
  timeout (the stale worker would resume looping on the ring buffer,
  and the next ``_start_audio_worker`` would spawn a duplicate).
  ``_start_audio_worker`` must detect the stale-alive case and create
  fresh stop/wake events (so the dying worker keeps its set stop event
  and the new worker gets cleared events) plus start a fresh worker
  thread (the stale one exits on its next iteration).

* REC-2: ``Recorder.start`` must roll back (tear down the PortAudio
  stream + stop the audio worker + clear ``_recording_event`` + bump
  ``_stop_generation``) when ``_start_audio_worker`` or
  ``_start_event_worker`` raises, so the stream does not leak.

* REC-8: ``_buffer.clear()`` and the ``_buffer`` rebind
  (``recorder._buffer = collections.deque(...)``) must be wrapped in
  ``with recorder._lock:`` at every site that performs them
  (``discard_recording``, ``stop_recording``).

These tests run on every platform (the production code paths are
platform-neutral; the PortAudio stream is mocked).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _patch_ok_stream(monkeypatch, recording_mod):
    class _OkStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(recording_mod.sd, "InputStream", _OkStream)
    monkeypatch.setattr(
        recording_mod.sd,
        "query_devices",
        lambda **kw: {
            "max_input_channels": 1,
            "default_samplerate": 16000,
            "hostapi": 0,
            "index": 0,
            "name": "Mock Input",
        },
    )
    monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})


class TestRec1StaleWorkerGuardWrapper:
    """REC-1 contract pinned at the ``Recorder._start_audio_worker`` /
    ``_stop_audio_worker`` wrapper layer (the collaborator
    ``capture.py`` does NOT enforce this — the wrapper restores it)."""

    def test_stop_keeps_stop_event_and_thread_when_still_alive(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        stale_thread = MagicMock()
        stale_thread.is_alive.return_value = True
        stale_thread.join = MagicMock()
        r._worker_thread = stale_thread
        r._worker_stop_event.clear()

        r._stop_audio_worker(timeout=0.01, drain=False)

        assert r._worker_stop_event.is_set(), (
            "REC-1: _stop_audio_worker cleared the stop event even though "
            "the worker is still alive — the stale worker would resume looping."
        )
        assert r._worker_thread is not None, (
            "REC-1: _stop_audio_worker nulled the thread reference even though "
            "the worker is still alive — the next _start_audio_worker would "
            "spawn a duplicate (SPSC invariant violation)."
        )

    def test_start_creates_fresh_events_for_stale_alive_worker(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        stale_stop_event = r._worker_stop_event
        stale_wake_event = r._worker_wake_event
        stale_thread = MagicMock()
        stale_thread.is_alive.return_value = True
        r._worker_thread = stale_thread
        r._worker_stop_event.set()

        r._start_audio_worker()

        try:
            assert r._worker_stop_event is not stale_stop_event, (
                "REC-1: _start_audio_worker reused the stale worker's stop event."
            )
            assert r._worker_wake_event is not stale_wake_event, (
                "REC-1: _start_audio_worker reused the stale worker's wake event."
            )
            assert r._worker_thread is not None
            assert r._worker_thread is not stale_thread, (
                "REC-1: _start_audio_worker did not start a fresh worker thread."
            )
        finally:
            # Clean up the freshly-started worker so the test doesn't leak.
            with pytest.MonkeyPatch().context() as mp:
                mp.setattr(r._worker_thread, "is_alive", lambda: False)
                r._stop_audio_worker(timeout=0.01, drain=False)


class TestRec2RollbackOnWorkerStartFailure:
    """REC-2 contract: ``Recorder.start`` must roll back the PortAudio
    stream + audio worker when ``_start_audio_worker`` or
    ``_start_event_worker`` raises, so the stream does not leak."""

    def test_start_rolls_back_stream_when_audio_worker_raises(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        teardown_calls = []
        original_teardown = r._teardown_stream
        r._teardown_stream = lambda: (teardown_calls.append(1), original_teardown())[1]

        def raising_start_audio_worker():
            raise RuntimeError("simulated worker-start failure")

        monkeypatch.setattr(r, "_start_audio_worker", raising_start_audio_worker)
        gen_before = r._stop_generation

        with pytest.raises(RuntimeError, match="simulated worker-start failure"):
            r.start()

        assert len(teardown_calls) >= 1, (
            "REC-2: start() did not call _teardown_stream() on audio-worker failure — leaked PortAudio stream."
        )
        assert not r._recording_event.is_set(), (
            "REC-2: start() did not clear _recording_event on failure — "
            "the next start()'s is_set() early-return would mask the retry."
        )
        assert r._stop_generation == gen_before + 1, (
            "REC-2: start() did not bump _stop_generation on failure — in-flight disconnect handlers won't bail out."
        )

    def test_start_rolls_back_stream_and_audio_worker_when_event_worker_raises(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        teardown_calls = []
        original_teardown = r._teardown_stream
        r._teardown_stream = lambda: (teardown_calls.append(1), original_teardown())[1]

        stop_calls = []
        original_stop_worker = r._stop_audio_worker

        def tracking_stop_worker(*, timeout, drain=True):
            stop_calls.append((timeout, drain))
            return original_stop_worker(timeout=timeout, drain=drain)

        r._stop_audio_worker = tracking_stop_worker

        def raising_event_worker():
            raise MemoryError("simulated OOM in event worker start")

        monkeypatch.setattr(r, "_start_event_worker", raising_event_worker)
        gen_before = r._stop_generation

        with pytest.raises(MemoryError, match="simulated OOM"):
            r.start()

        assert len(teardown_calls) >= 1, (
            "REC-2: start() did not call _teardown_stream() on event-worker failure — leaked PortAudio stream."
        )
        assert len(stop_calls) >= 1, (
            "REC-2: start() did not call _stop_audio_worker() on event-worker failure — leaked audio worker thread."
        )
        assert not r._recording_event.is_set()
        assert r._stop_generation == gen_before + 1


class TestRec8BufferOpsLockContract:
    """REC-8: the buffer-clear / buffer-rebind operations must be wrapped
    in ``with recorder._lock:`` at every site that performs them."""

    def test_discard_recording_locks_buffer_rebind(self):
        import inspect

        from voice_typer.server.recording._recorder_split import discard_recording

        src = inspect.getsource(discard_recording)
        assert "with recorder._lock:" in src, "REC-8: discard_recording does not acquire recorder._lock"
        assert "recorder._buffer = collections.deque(" in src, (
            "REC-8: discard_recording does not rebind recorder._buffer"
        )

    def test_stop_recording_locks_buffer_rebind(self):
        import inspect

        from voice_typer.server.recording._recorder_split import stop_recording

        src = inspect.getsource(stop_recording)
        assert "with recorder._lock:" in src, "REC-8: stop_recording does not acquire recorder._lock"
        assert "recorder._buffer = collections.deque(" in src, "REC-8: stop_recording does not rebind recorder._buffer"

    def test_start_acquires_start_lock(self):
        import inspect

        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder.start)
        assert "with self._start_lock:" in src, "REC-8: Recorder.start no longer acquires self._start_lock"
