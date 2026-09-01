"""Tests for SEC-audit-008: Audio buffer zeroing on stop/discard."""

from unittest.mock import MagicMock, patch

import numpy as np


def test_buffer_zeroed_on_stop():
    """Buffer contents are zeroed before being released on stop.

    WR-8: the previous test only asserted ``len(recorder._audio_pipeline._buffer) == 0``
    after ``stop()`` — which passes trivially even if the chunks were
    never zeroed (the buffer is replaced with a fresh empty container
    regardless). We now capture a reference to the recorded audio's
    backing storage BEFORE stop(), then drain the buffer-clear background
    worker (so the asynchronous ``fill(0)`` has actually executed) and
    assert the storage reads all zeros. Contiguous storage: appended
    chunks are COPIED into one growable array, so the privacy contract
    ("the recorded audio must be zeroed in-place by
    ``_secure_clear_array_background`` during stop()") is pinned against
    that storage object instead of an individual chunk.
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
    # Manually add some data to the buffer, then capture a reference to
    # the SAME storage object the buffer-clear worker will fill(0)
    # in-place — so we can verify the zeroing actually happened on the
    # audio we appended (not on a fresh copy).
    chunk = np.array([0.5, 0.3, 0.8], dtype=np.float32)
    recorder._audio_pipeline._buffer.append(chunk)
    storage_ref = recorder._audio_pipeline._buffer.storage
    recorder._recording_event.set()

    # Mock the stream
    recorder._stream_lifecycle._stream = MagicMock()

    # Stop should zero the buffer before clearing
    # We can verify by checking that fill(0) was called
    # stop_recording invokes the module-level prepare_audio free function,
    # so the patch targets its import site in _recorder_split (the
    # historical Recorder._prepare_audio delegator was removed).
    with patch(
        "voice_typer.server.recording._recorder_split.prepare_audio",
        return_value=np.array([], dtype=np.float32),
    ):
        recorder.stop()

    # Buffer should be empty after stop
    assert len(recorder._audio_pipeline._buffer) == 0

    # drain the buffer-clear background worker so the
    # asynchronous fill(0) has actually executed on storage_ref, then
    # assert the storage contents were zeroed in-place. Previously
    # this test only checked the buffer length, which passes even if
    # the secure-clear path was a no-op.
    _stop_buffer_clear_worker(timeout=2.0)
    assert np.all(storage_ref[:3] == 0), (
        "SEC-audit-008: the recording storage behind recorder._audio_pipeline._buffer must be "
        "zeroed in-place by _secure_clear_array_background during "
        "stop(); the storage contents were not all zeros after the "
        "buffer-clear worker drained."
    )
