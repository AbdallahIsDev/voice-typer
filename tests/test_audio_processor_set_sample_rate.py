"""FA5-FIX / XV-31 (CRITICAL): ``AudioProcessor.set_sample_rate`` updates"""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server.audio_filters import HighPassFilter
from voice_typer.server.audio_processor import AudioProcessor

from tests.fixtures.config_helpers import FakeConfig


@pytest.fixture
def config() -> FakeConfig:
    return FakeConfig()


@pytest.fixture
def processor(config: FakeConfig) -> AudioProcessor:
    return AudioProcessor(config, sample_rate=16000)


def _find_highpass(p: AudioProcessor) -> HighPassFilter:
    """Return the (single) HighPassFilter instance in the chain."""
    return next(f for f in p.chain.filters if isinstance(f, HighPassFilter))


class TestSetSampleRateUpdatesInternalRate:
    """XV-31: ``set_sample_rate(new_sr)`` updates ``processor._sample_rate``."""

    def test_internal_sample_rate_changes_to_48000(self, processor):
        assert processor._sample_rate == 16000
        processor.set_sample_rate(48000)
        assert processor._sample_rate == 48000

    def test_internal_sample_rate_changes_to_44100(self, processor):
        processor.set_sample_rate(44100)
        assert processor._sample_rate == 44100

    def test_internal_sample_rate_changes_downward(self, processor):
        processor.set_sample_rate(48000)
        assert processor._sample_rate == 48000
        processor.set_sample_rate(16000)
        assert processor._sample_rate == 16000

    def test_sample_rate_property_tracks_internal_field(self, processor):
        """The read-only ``sample_rate`` property mirrors ``_sample_rate``."""
        assert processor.sample_rate == processor._sample_rate == 16000
        processor.set_sample_rate(48000)
        assert processor.sample_rate == processor._sample_rate == 48000

    def test_set_sample_rate_coerces_to_int(self, processor):
        """Defensive: pass-through int() so a numpy int64 / float doesn't"""
        processor.set_sample_rate(np.int64(48000))
        assert processor._sample_rate == 48000
        assert isinstance(processor._sample_rate, int)


class TestSetSampleRateRebuildsFilters:
    """XV-31: ``set_sample_rate(new_sr)`` must rebuild the chain's filters"""

    def test_highpass_b_coefficients_change(self, processor):
        """An order-4 Butterworth high-pass at 80 Hz produces different"""
        initial_hp = _find_highpass(processor)
        initial_b = initial_hp._state[0].copy()

        processor.set_sample_rate(48000)

        new_hp = _find_highpass(processor)
        new_b = new_hp._state[0]

        assert not np.allclose(initial_b, new_b), (
            "XV-31: set_sample_rate must rebuild the HighPass filter with "
            f"new coefficients (16kHz b={initial_b}, 48kHz b={new_b})"
        )

    def test_highpass_sos_state_layout_after_rebuild(self, processor):
        """SOS-form witness that the chain was rebuilt at the new rate."""
        processor.set_sample_rate(48000)

        hp = _find_highpass(processor)
        assert isinstance(hp._state, tuple) and len(hp._state) == 2, "high-pass state must be the (sos, zi) pair"
        sos, zi = hp._state
        assert sos.ndim == 2 and sos.shape[1] == 6, f"SOS section matrix must be Nx6 (got shape {sos.shape})"
        assert zi.shape == (sos.shape[0], 2), (
            f"zi must be a per-section 2-tap (got {zi.shape} for {sos.shape[0]} sections)"
        )
        assert not np.allclose(sos[:, 3:6], np.zeros_like(sos[:, 3:6])), (
            "SOS denominator columns must be non-zero, the section matrix "
            "carries the denominator that the old ``a`` array held"
        )

    def test_highpass_internal_sample_rate_matches_new_rate(self, processor):
        """The HighPass filter's own ``_sample_rate`` field must reflect"""
        processor.set_sample_rate(48000)
        hp = _find_highpass(processor)
        assert hp._sample_rate == 48000

    def test_highpass_internal_sample_rate_matches_after_downward(self, processor):
        processor.set_sample_rate(48000)
        assert _find_highpass(processor)._sample_rate == 48000
        processor.set_sample_rate(16000)
        assert _find_highpass(processor)._sample_rate == 16000

    def test_filter_set_preserved_across_rate_change(self, processor):
        """XV-31: the filter configuration is preserved across the rate"""
        initial_names = processor.filter_names
        processor.set_sample_rate(44100)
        assert processor.filter_names == initial_names, (
            f"Filter set must be preserved across rate change: initial={initial_names}, after={processor.filter_names}"
        )

    def test_chain_object_identity_preserved(self, processor):
        """The :class:`FilterChain` object itself is preserved across"""
        initial_chain = processor.chain
        processor.set_sample_rate(48000)
        assert processor.chain is initial_chain


class TestSetSampleRateThenRebuildFromConfig:
    def test_rebuild_from_config_uses_new_rate(self, processor, config):
        processor.set_sample_rate(48000)
        processor.rebuild_from_config(config)
        assert _find_highpass(processor)._sample_rate == 48000
        assert processor._sample_rate == 48000

    def test_rebuild_from_config_does_not_revert_rate(self, processor, config):
        """``rebuild_from_config`` after ``set_sample_rate(48000)`` must"""
        assert config.sample_rate == 16000
        processor.set_sample_rate(48000)
        processor.rebuild_from_config(config)
        assert processor._sample_rate == 48000, (
            "XV-31: rebuild_from_config must use the processor's current "
            "self._sample_rate (48000 after set_sample_rate), NOT revert "
            "to config.sample_rate (16000)."
        )


class TestSetSampleRateProcessChunk:
    def test_process_chunk_at_new_rate_does_not_crash(self, processor):
        processor.set_sample_rate(48000)
        t = np.linspace(0, 0.1, 4800, endpoint=False)
        audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.shape == audio.shape
        assert result.dtype == np.float32

    def test_process_chunk_at_44100(self, processor):
        processor.set_sample_rate(44100)
        t = np.linspace(0, 0.1, 4410, endpoint=False)
        audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.shape == audio.shape
