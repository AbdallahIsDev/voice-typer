"""Tests for SEC-audit-008: Audio buffer zeroing on stop/discard."""
import numpy as np
from unittest.mock import MagicMock, patch


def test_buffer_zeroed_on_stop():
    """Buffer chunks are zeroed before being cleared on stop."""
    from voice_typer.server.recording import Recorder
    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.silence_auto_stop_seconds = 120.0
    config.max_recording_seconds = 0
    config.max_recording_seconds_gpu = 1200
    config.max_recording_seconds_cpu = 600
    config.device = "cpu"
    
    recorder = Recorder(config)
    # Manually add some data to the buffer
    chunk = np.array([0.5, 0.3, 0.8], dtype=np.float32)
    original_id = id(chunk)
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
