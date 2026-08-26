"""FA5-FIX / XV-31 (CRITICAL): ``AudioProcessor.set_sample_rate`` updates
the internal sample rate AND rebuilds the filter chain at the new rate.

Regression background
---------------------
Before this fix, ``AudioQualityController._rebuild_audio_processor``'s
AUDIO-6 / AUDIO-9 path did::

    set_sr = getattr(self._app._audio_processor, "set_sample_rate", None)
    if callable(set_sr):
        set_sr(force_sr)
    else:
        log.debug("[APP] AudioProcessor lacks set_sample_rate — skipping AUDIO-6 rebuild")

but ``AudioProcessor`` never defined ``set_sample_rate``. So ``getattr``
always returned ``None`` and the controller fell through to the
``else`` log line every time — the actual rate change was never
propagated. All filter coefficients stayed tuned to the original
``config.sample_rate`` (16 kHz), so a hot-plugged device running at
48 kHz (or 44.1 kHz) silently mistuned the entire chain:

  - 80 Hz Butterworth high-pass built at 16 kHz → actually cuts at 240 Hz
    when fed 48 kHz audio (removes male speech fundamentals).
  - Notch frequencies, EQ crossovers, and compressor attack/release
    ballistics all drift in lockstep.

This module pins the contract added by FA5-FIX:

  1. ``set_sample_rate(new_sr)`` updates ``processor._sample_rate``.
  2. ``set_sample_rate(new_sr)`` triggers a real rebuild — the chain's
     filter instances are reconstructed at the new rate, evidenced by
     the HighPass IIR ``b`` coefficients changing and the filter's
     internal ``_sample_rate`` matching the new rate.
  3. The filter SET (which filters are active) is preserved across the
     rate change — only the coefficients change.
  4. A subsequent ``rebuild_from_config`` uses the NEW rate (AUDIO-6 /
     AUDIO-9 contract: controller calls ``set_sample_rate(force_sr)``
     BEFORE ``rebuild_from_config(config)``).
"""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server.audio_filters import HighPassFilter
from voice_typer.server.audio_processor import AudioProcessor

from tests.fixtures.config_helpers import FakeConfig

# ═══════════════════════════════════════════════════════════════════════════
# Test config — the shared minimal config stand-in from
# tests/fixtures/config_helpers.py (previously a local copy of
# test_audio_processor.py's FakeConfig; consolidated so the two files
# cannot drift again).
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def config() -> FakeConfig:
    return FakeConfig()


@pytest.fixture
def processor(config: FakeConfig) -> AudioProcessor:
    return AudioProcessor(config, sample_rate=16000)


def _find_highpass(p: AudioProcessor) -> HighPassFilter:
    """Return the (single) HighPassFilter instance in the chain."""
    return next(f for f in p.chain.filters if isinstance(f, HighPassFilter))


# ═══════════════════════════════════════════════════════════════════════════
# _sample_rate must change
# ═══════════════════════════════════════════════════════════════════════════


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
        """Defensive: pass-through int() so a numpy int64 / float doesn't
        leak into the IIR coefficient math (butter() requires int sr)."""
        processor.set_sample_rate(np.int64(48000))
        assert processor._sample_rate == 48000
        assert isinstance(processor._sample_rate, int)


# ═══════════════════════════════════════════════════════════════════════════
# chain filters must reflect the new rate
# ═══════════════════════════════════════════════════════════════════════════


class TestSetSampleRateRebuildsFilters:
    """XV-31: ``set_sample_rate(new_sr)`` must rebuild the chain's filters
    at the new rate — not just update the bookkeeping field."""

    def test_highpass_b_coefficients_change(self, processor):
        """An order-4 Butterworth high-pass at 80 Hz produces different
        ``b`` coefficients for 16 kHz vs 48 kHz. If the chain is NOT
        rebuilt (the XV-31 bug), the coefficients stay tuned to the
        original rate."""
        initial_hp = _find_highpass(processor)
        initial_b = initial_hp._state[0].copy()

        processor.set_sample_rate(48000)

        new_hp = _find_highpass(processor)
        new_b = new_hp._state[0]

        assert not np.allclose(initial_b, new_b), (
            "XV-31: set_sample_rate must rebuild the HighPass filter with "
            f"new coefficients (16kHz b={initial_b}, 48kHz b={new_b})"
        )

    def test_highpass_a_coefficients_change(self, processor):
        """Same as above but for the ``a`` (denominator) coefficients —
        another independent witness that the filter was actually rebuilt."""
        initial_hp = _find_highpass(processor)
        initial_a = initial_hp._state[1].copy()

        processor.set_sample_rate(48000)

        new_hp = _find_highpass(processor)
        new_a = new_hp._state[1]

        assert not np.allclose(initial_a, new_a), (
            "XV-31: set_sample_rate must rebuild the HighPass filter with "
            f"new denominator coefficients (16kHz a={initial_a}, 48kHz a={new_a})"
        )

    def test_highpass_internal_sample_rate_matches_new_rate(self, processor):
        """The HighPass filter's own ``_sample_rate`` field must reflect
        the new rate after ``set_sample_rate`` — proves the chain was
        rebuilt at the new rate, not just the processor's bookkeeping
        field updated in isolation."""
        processor.set_sample_rate(48000)
        hp = _find_highpass(processor)
        assert hp._sample_rate == 48000

    def test_highpass_internal_sample_rate_matches_after_downward(self, processor):
        processor.set_sample_rate(48000)
        assert _find_highpass(processor)._sample_rate == 48000
        processor.set_sample_rate(16000)
        assert _find_highpass(processor)._sample_rate == 16000

    def test_filter_set_preserved_across_rate_change(self, processor):
        """XV-31: the filter configuration is preserved across the rate
        change — only the coefficients change, not which filters are
        active. (A regression that swapped in a wrong/different filter
        set would still pass the coefficient-change tests above.)"""
        initial_names = processor.filter_names
        processor.set_sample_rate(44100)
        assert processor.filter_names == initial_names, (
            f"Filter set must be preserved across rate change: initial={initial_names}, after={processor.filter_names}"
        )

    def test_chain_object_identity_preserved(self, processor):
        """The :class:`FilterChain` object itself is preserved across
        the rebuild (``rebuild_from_config`` swaps the internal filter
        list in place via :meth:`FilterChain.swap` rather than replacing
        the chain object), so callers holding a reference to
        ``processor.chain`` keep working across the rate change."""
        initial_chain = processor.chain
        processor.set_sample_rate(48000)
        assert processor.chain is initial_chain


# ═══════════════════════════════════════════════════════════════════════════
# subsequent rebuild_from_config uses the NEW rate (/
# contract: controller calls set_sample_rate(force_sr) BEFORE
# rebuild_from_config(config))
# ═══════════════════════════════════════════════════════════════════════════


class TestSetSampleRateThenRebuildFromConfig:
    def test_rebuild_from_config_uses_new_rate(self, processor, config):
        processor.set_sample_rate(48000)
        processor.rebuild_from_config(config)
        assert _find_highpass(processor)._sample_rate == 48000
        assert processor._sample_rate == 48000

    def test_rebuild_from_config_does_not_revert_rate(self, processor, config):
        """Even though ``config.sample_rate == 16000``, a
        ``rebuild_from_config`` after ``set_sample_rate(48000)`` must
        NOT revert the chain to 16 kHz — the controller's intent
        (``force_sr``) wins over the stale config value."""
        assert config.sample_rate == 16000
        processor.set_sample_rate(48000)
        processor.rebuild_from_config(config)
        assert processor._sample_rate == 48000, (
            "XV-31: rebuild_from_config must use the processor's current "
            "self._sample_rate (48000 after set_sample_rate), NOT revert "
            "to config.sample_rate (16000)."
        )


# ═══════════════════════════════════════════════════════════════════════════
# smoke test — the rebuilt chain must be functional
# ═══════════════════════════════════════════════════════════════════════════


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
