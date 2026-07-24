"""Tests for SEC-audit-008: Audio buffer zeroing on stop/discard."""
from unittest.mock import MagicMock, patch

import numpy as np


def test_buffer_zeroed_on_stop():
    """Buffer chunks are zeroed before being cleared on stop.

    WR-8: the previous test only asserted ``len(recorder._buffer) == 0``
    after ``stop()`` — which passes trivially even if the chunks were
    never zeroed (the buffer is replaced with a fresh empty deque
    regardless). We now capture a reference to the chunk BEFORE stop(),
    then drain the buffer-clear background worker (so the asynchronous
    ``fill(0)`` has actually executed) and assert ``np.all(chunk == 0)``.
    """
    from voice_typer.server.recording import Recorder
    from voice_typer.server.recording.buffer import _stop_buffer_clear_worker

    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    # SIMPLIFY-001: single explicit field replaces the old 3-field split
    config.max_recording_time_seconds = 900
    config.device = "cpu"

    recorder = Recorder(config)
    # Manually add some data to the buffer.
    # Capture a reference to the SAME chunk object that the
    # buffer-clear worker will fill(0) in-place — so we can verify the
    # zeroing actually happened on the chunk we appended (not on a
    # fresh copy).
    chunk = np.array([0.5, 0.3, 0.8], dtype=np.float32)
    chunk_ref = chunk  # same ndarray object; fill(0) mutates in-place
    recorder._buffer.append(chunk)
    recorder._recording_event.set()

    # Mock the stream
    recorder._stream = MagicMock()

    # Stop should zero the buffer before clearing
    # We can verify by checking that fill(0) was called
    with patch.object(recorder, '_prepare_audio', return_value=np.array([], dtype=np.float32)):
        recorder.stop()

    # Buffer should be empty after stop
    assert len(recorder._buffer) == 0

    # WR-8: drain the buffer-clear background worker so the
    # asynchronous fill(0) has actually executed on chunk_ref, then
    # assert the chunk's contents were zeroed in-place. Previously
    # this test only checked the buffer length, which passes even if
    # the secure-clear path was a no-op.
    _stop_buffer_clear_worker(timeout=2.0)
    assert np.all(chunk_ref == 0), (
        "SEC-audit-008: the chunk appended to recorder._buffer must be "
        "zeroed in-place by _secure_clear_array_background during "
        "stop(); the chunk contents were not all zeros after the "
        "buffer-clear worker drained."
    )
