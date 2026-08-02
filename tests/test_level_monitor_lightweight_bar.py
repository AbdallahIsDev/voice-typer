"""lightweight level-bar mode — skip RNNoise filter chain for
the cosmetic level bar.

The bug (the fix)
--------------
When ``_level_processor`` is set (which happens whenever
``noise_filter_enabled=True``), every monitor chunk (31.25 Hz @ 16
kHz/512, 93.75 Hz @ 48 kHz/512) was passed through
``processor.process_chunk(indata.reshape(-1, 1))`` which may include
RNNoise (5-50 ms per chunk on CPU). This ran continuously while the
monitor was active — pegging a core at 15-100% for a COSMETIC level
bar.

The fix (the fix)
--------------
Add a "lightweight level-bar mode" that computes RMS on raw audio only
(skip RNNoise) for the cosmetic bar. The filter chain STILL runs when
``_test_mode`` is True (the test's "after" WAV needs the filtered
audio) OR when the user has explicitly opted in via
``_level_bar_filtered = True`` (set through
``update_level_processor``'s ``level_bar_filtered`` config key).

This test verifies:
1. By default (``_level_bar_filtered == False``), the filter chain is
   NOT invoked for the cosmetic bar even when ``_level_processor`` is
   set.
2. When ``_level_bar_filtered == True``, the filter chain IS invoked.
3. ``update_level_processor`` propagates the ``level_bar_filtered``
   config key to ``_state._level_bar_filtered``.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Mock sounddevice so tests run on any platform without real
    audio hardware."""
    holder = {"callback": None}

    class _Stream:
        def __init__(self, *args, **kwargs):
            holder["callback"] = kwargs.get("callback")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    import sounddevice as sd

    monkeypatch.setattr(sd, "InputStream", _Stream)
    monkeypatch.setattr(
        sd,
        "query_devices",
        lambda *a, **k: {
            "name": "Mock Mic",
            "default_samplerate": 16000,
            "max_input_channels": 1,
            "hostapi": 0,
        },
    )
    yield holder


class TestLightweightLevelBarMode:
    """the filter chain is skipped for the cosmetic level bar by
    default."""

    def test_filter_chain_skipped_by_default_for_cosmetic_bar(self, _mock_sounddevice):
        """When ``_level_bar_filtered`` is False (default) AND
        ``_test_mode`` is False, the filter chain MUST NOT be invoked
        — RMS is computed on raw audio only."""
        import voice_typer.server.level_monitor as lm

        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor
        # Default: _level_bar_filtered is False
        lm._level_bar_filtered = False
        # Not in test mode
        lm._test_mode = False

        lm.start_monitoring(mic_id=None)
        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            _mock_sounddevice["callback"](chunk, 512, None, None)

            # Wait for the worker to process.
            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                if lm._monitor_level > 0:
                    break
                time.sleep(0.01)

            # contract: processor.process_chunk MUST NOT have been
            # called (the filter chain was skipped for the cosmetic bar).
            processor.process_chunk.assert_not_called()
            # The RMS should still be computed (on raw audio) so the bar
            # moves — _monitor_level should be non-zero.
            assert lm._monitor_level > 0, (
                "even with the filter chain skipped, the cosmetic bar "
                "must still show a non-zero level (RMS computed on raw audio)"
            )
        finally:
            lm.stop_monitoring()

    def test_filter_chain_runs_when_level_bar_filtered_opted_in(self, _mock_sounddevice):
        """When ``_level_bar_filtered`` is True (user opted in), the
        filter chain IS invoked for the cosmetic bar."""
        import voice_typer.server.level_monitor as lm

        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor
        # Opt in to filtered bar
        lm._level_bar_filtered = True
        lm._test_mode = False

        lm.start_monitoring(mic_id=None)
        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            _mock_sounddevice["callback"](chunk, 512, None, None)

            # Wait for the worker to process.
            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                if lm._monitor_level > 0:
                    break
                time.sleep(0.01)

            # contract: processor.process_chunk MUST have been
            # called (the user opted in to the filtered bar).
            processor.process_chunk.assert_called()
        finally:
            lm.stop_monitoring()

    def test_filter_chain_runs_in_test_mode_regardless_of_flag(self, _mock_sounddevice):
        """When ``_test_mode`` is True, the filter chain runs EVEN IF
        ``_level_bar_filtered`` is False — the test's "after" WAV needs
        the filtered audio."""
        import voice_typer.server.level_monitor as lm

        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor
        lm._level_bar_filtered = False
        # In test mode — filter chain must run regardless
        lm._test_mode = True

        lm.start_monitoring(mic_id=None)
        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            _mock_sounddevice["callback"](chunk, 512, None, None)

            # Wait for the worker to process.
            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                if lm._monitor_level > 0:
                    break
                time.sleep(0.01)

            # contract: processor.process_chunk MUST have been
            # called (test mode forces the filter chain so the "after"
            # WAV has filtered audio).
            processor.process_chunk.assert_called()
        finally:
            # Reset test_mode + stop
            lm._test_mode = False
            lm.stop_monitoring()


class TestUpdateLevelProcessorPropagatesFlag:
    """``update_level_processor`` must propagate the
    ``level_bar_filtered`` config key to ``_state._level_bar_filtered``."""

    def test_update_level_processor_sets_flag_false_by_default(self):
        """When ``level_bar_filtered`` is NOT in the config dict, the
        flag defaults to False (cosmetic bar uses raw audio)."""
        import voice_typer.server.level_monitor as lm

        # Reset
        lm._level_bar_filtered = True
        # Call update_level_processor with a config dict that doesn't
        # have level_bar_filtered — should reset to False.
        lm.update_level_processor({"noise_filter_enabled": False})
        assert lm._level_bar_filtered is False, "update_level_processor must default _level_bar_filtered to False"

    def test_update_level_processor_sets_flag_true_when_opted_in(self):
        """When ``level_bar_filtered=True`` is in the config dict, the
        flag is set to True (user opted in to filtered bar)."""
        import voice_typer.server.level_monitor as lm

        lm.update_level_processor({"noise_filter_enabled": False, "level_bar_filtered": True})
        assert lm._level_bar_filtered is True, (
            "update_level_processor must set _level_bar_filtered=True when the config dict opts in"
        )
