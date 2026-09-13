"""Silent capture + ring-buffer overflow (device mismatch / worker overload).

Covers the minimal fix for a session that recorded 13.2 s of
``RMS=0.000000`` silence while dropping ~300 chunks to ring-buffer
overflow:

- The recorder opened PortAudio's raw ``device=None`` default (MME on
  Windows) while the canonical enumeration listed the live WASAPI
  endpoint, so the stream delivered ~silence and the native-rate filter
  chain could not keep up with the 32 ms callback cadence.
- ``_same_physical_microphone_candidates(None)`` now resolves the
  System Default through the canonical ``list_microphones`` default
  (WASAPI-first), falling back to ``[None]`` when the lookup fails.
- ``_fallback_host_rank`` now prefers WASAPI over MME (matches the
  canonical host-API selection).
- ``AudioPipeline.apply_filter_chain`` bypasses the chain for
  near-silence chunks when no resample would have run, so silent input
  costs microseconds instead of a full RNNoise + IIR pass per chunk.

All audio is synthetic (numpy zeros / constants); no real PortAudio /
sounddevice stream is touched.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording.audio_pipeline import (
    _NEAR_SILENCE_BYPASS_PEAK,
    AudioPipeline,
)

from tests.fixtures.recorder_test_helpers import make_recorder


def _pipeline_stub(*, effective_sr: int = 44100, processor=None):
    """Real ``AudioPipeline`` on a stub recorder (mirrors the disconnect tests)."""
    recorder = MagicMock(name="RecorderStub")
    recorder._effective_sr = effective_sr
    recorder._audio_processor = processor
    pipeline = AudioPipeline(recorder)
    recorder._audio_pipeline = pipeline
    return recorder, pipeline


def _chain_processor(chain_sr: int):
    """Mock ``AudioProcessor`` whose ``process_chunk`` echoes its input."""
    processor = MagicMock(name="AudioProcessorStub")
    processor._sample_rate = chain_sr
    processor.process_chunk.side_effect = lambda chunk, input_sample_rate=None: chunk
    return processor


class TestNearSilenceBypass:
    def test_bypass_threshold_is_sane(self):
        assert 0.0 < _NEAR_SILENCE_BYPASS_PEAK < 0.01

    def test_digital_silence_skips_chain(self):
        recorder, pipeline = _pipeline_stub(effective_sr=44100, processor=_chain_processor(44100))
        out = pipeline.apply_filter_chain(np.zeros(1411, dtype=np.float32))
        recorder._audio_processor.process_chunk.assert_not_called()
        assert out.shape == (1411,)
        assert not np.any(out)
        assert pipeline._buffer_sr == 44100

    def test_idle_noise_floor_skips_chain(self):
        """A muted-device floor (tiny but nonzero) also bypasses."""
        recorder, pipeline = _pipeline_stub(effective_sr=44100, processor=_chain_processor(44100))
        floor = np.full(1411, 1e-4, dtype=np.float32)
        assert float(np.abs(floor).max()) < _NEAR_SILENCE_BYPASS_PEAK
        out = pipeline.apply_filter_chain(floor)
        recorder._audio_processor.process_chunk.assert_not_called()
        assert not np.any(out)
        assert pipeline._buffer_sr == 44100

    def test_speech_runs_chain(self):
        recorder, pipeline = _pipeline_stub(effective_sr=44100, processor=_chain_processor(44100))
        speech = np.full(1411, 0.5, dtype=np.float32)
        out = pipeline.apply_filter_chain(speech)
        recorder._audio_processor.process_chunk.assert_called_once()
        assert np.array_equal(out, speech)
        assert pipeline._buffer_sr == 44100

    def test_below_threshold_bypasses_at_threshold_runs(self):
        below = np.full(512, _NEAR_SILENCE_BYPASS_PEAK * 0.95, dtype=np.float32)
        at = np.full(512, _NEAR_SILENCE_BYPASS_PEAK, dtype=np.float32)
        recorder, pipeline = _pipeline_stub(effective_sr=16000, processor=_chain_processor(16000))
        pipeline.apply_filter_chain(below)
        recorder._audio_processor.process_chunk.assert_not_called()
        pipeline.apply_filter_chain(at)
        recorder._audio_processor.process_chunk.assert_called_once()

    def test_bypass_deferred_while_chain_rate_differs(self):
        """When the chain is mistuned (retune failed), silence still runs
        the chain so the buffer keeps the chain-rate tag (no mixed rates)."""
        recorder, pipeline = _pipeline_stub(effective_sr=44100, processor=_chain_processor(16000))
        pipeline.apply_filter_chain(np.zeros(1411, dtype=np.float32))
        recorder._audio_processor.process_chunk.assert_called_once()

    def test_bypass_without_processor(self):
        recorder, pipeline = _pipeline_stub(effective_sr=44100, processor=None)
        out = pipeline.apply_filter_chain(np.zeros(1411, dtype=np.float32))
        assert not np.any(out)
        assert pipeline._buffer_sr == 44100

    def test_bypass_preserves_stereo_downmix_shape(self):
        recorder, pipeline = _pipeline_stub(effective_sr=44100, processor=_chain_processor(44100))
        out = pipeline.apply_filter_chain(np.zeros((1411, 2), dtype=np.float32))
        recorder._audio_processor.process_chunk.assert_not_called()
        assert out.shape == (1411,)
        assert not np.any(out)


@pytest.fixture()
def _mock_sounddevice(monkeypatch):
    """Headless ``sounddevice`` so recorder construction touches no audio HW."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    mock_sd.query_hostapis.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    return mock_sd


def _canonical_mics():
    return [
        {
            "id": "Windows WASAPI|Microphone (Realtek(R) Audio)",
            "index": 27,
            "name": "Microphone (Realtek(R) Audio)",
            "host_api": "Windows WASAPI",
            "channels": 2,
            "default": True,
            "is_bluetooth": False,
        },
        {
            "id": "Windows WASAPI|USB Mic",
            "index": 30,
            "name": "USB Mic",
            "host_api": "Windows WASAPI",
            "channels": 1,
            "default": False,
            "is_bluetooth": False,
        },
    ]


class TestCanonicalDefaultResolution:
    def test_none_resolves_to_canonical_default(self, _mock_sounddevice, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: _canonical_mics(),
        )
        recorder = make_recorder()
        assert recorder.config.microphone is None
        assert recorder._devices._same_physical_microphone_candidates(None) == [27]

    def test_none_falls_back_when_no_default_flag(self, _mock_sounddevice, monkeypatch):
        mics = _canonical_mics()
        for mic in mics:
            mic["default"] = False
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: mics,
        )
        recorder = make_recorder()
        assert recorder._devices._same_physical_microphone_candidates(None) == [None]

    def test_none_falls_back_when_lookup_raises(self, _mock_sounddevice, monkeypatch):
        def _boom():
            raise RuntimeError("PortAudio unavailable")

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            _boom,
        )
        recorder = make_recorder()
        assert recorder._devices._same_physical_microphone_candidates(None) == [None]

    def test_explicit_device_untouched(self, _mock_sounddevice, monkeypatch):
        """Non-null selections keep the legacy candidate path (the
        canonical lookup only runs for the null System Default)."""
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: _canonical_mics(),
        )
        recorder = make_recorder()
        assert recorder._devices._same_physical_microphone_candidates("some-id") == ["some-id"]


class TestFallbackHostRank:
    def test_wasapi_preferred_over_mme(self, _mock_sounddevice):
        recorder = make_recorder()
        assert recorder._devices._fallback_host_rank("Windows WASAPI") < recorder._devices._fallback_host_rank("MME")

    def test_windows_ordering(self, _mock_sounddevice):
        recorder = make_recorder()
        rank = recorder._devices._fallback_host_rank
        assert rank("Windows WASAPI") < rank("MME") < rank("WDM-KS") < rank("Windows DirectSound")


class TestRecorderEnumerationParity:
    def test_refresh_skips_placeholder_endpoints(self, _mock_sounddevice):
        _mock_sounddevice.query_devices.return_value = [
            {"name": "Input ()", "max_input_channels": 2},
            {"name": "   ", "max_input_channels": 2},
            {
                "index": 27,
                "name": "Microphone (Realtek(R) Audio)",
                "max_input_channels": 2,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
        ]
        recorder = make_recorder()
        devices = recorder._devices._refresh_device_list()
        names = [dev["name"] for dev in devices]
        assert names == ["Microphone (Realtek(R) Audio)"]
