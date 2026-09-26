"""Tests for SEC-audit-008: Audio buffer zeroing on stop/discard."""

from unittest.mock import MagicMock, patch

import numpy as np


def test_buffer_zeroed_on_stop():
    """Buffer contents are zeroed before being released on stop."""
    from voice_typer.server.recording import Recorder
    from voice_typer.server.recording.buffer import _stop_buffer_clear_worker

    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    config.max_recording_time_seconds = 900
    config.device = "cpu"

    recorder = Recorder(config)
    chunk = np.array([0.5, 0.3, 0.8], dtype=np.float32)
    recorder._audio_pipeline._buffer.append(chunk)
    storage_ref = recorder._audio_pipeline._buffer.storage
    recorder._recording_event.set()

    # Mock the stream
    recorder._stream_lifecycle._stream = MagicMock()

    # Stop should zero the buffer before clearing
    with patch(
        "voice_typer.server.recording.recording_lifecycle.prepare_audio",
        return_value=np.array([], dtype=np.float32),
    ):
        recorder.stop()

    # Buffer should be empty after stop
    assert len(recorder._audio_pipeline._buffer) == 0

    _stop_buffer_clear_worker(timeout=2.0)
    assert np.all(storage_ref[:3] == 0), (
        "SEC-audit-008: the recording storage behind recorder._audio_pipeline._buffer must be "
        "zeroed in-place by _secure_clear_array_background during "
        "stop(); the storage contents were not all zeros after the "
        "buffer-clear worker drained."
    )
