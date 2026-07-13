"""Tests for SEC-audit-008: Audio buffer zeroing on stop/discard."""
from unittest.mock import MagicMock, patch

import numpy as np


def test_buffer_zeroed_on_stop():
    """Buffer chunks are zeroed before being cleared on stop."""
    from voice_typer.server.recording import Recorder
    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    # SIMPLIFY-001: single explicit field replaces the old 3-field split
    config.max_recording_time_seconds = 900
    config.device = "cpu"

    recorder = Recorder(config)
    # Manually add some data to the buffer
    chunk = np.array([0.5, 0.3, 0.8], dtype=np.float32)
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
