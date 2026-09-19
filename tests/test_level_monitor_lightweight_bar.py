"""the cosmetic level bar."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Mock sounddevice so tests run on any platform without real"""
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
    """the filter chain is skipped for the cosmetic level bar by"""

    def test_filter_chain_skipped_by_default_for_cosmetic_bar(self, _mock_sounddevice):
        """``_test_mode`` is False, the filter chain MUST NOT be invoked"""
        import voice_typer.server.level_monitor as lm

        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor
        lm._level_processor_config = None
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

            processor.process_chunk.assert_not_called()
            # The RMS should still be computed (on raw audio) so the bar
            assert lm._monitor_level > 0, (
                "even with the filter chain skipped, the cosmetic bar "
                "must still show a non-zero level (RMS computed on raw audio)"
            )
        finally:
            lm.stop_monitoring()

    def test_filter_chain_runs_when_level_bar_filtered_opted_in(self, _mock_sounddevice):
        """When ``_level_bar_filtered`` is True (user opted in), the"""
        import voice_typer.server.level_monitor as lm

        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor
        lm._level_processor_config = None
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

            processor.process_chunk.assert_called()
        finally:
            lm.stop_monitoring()

    def test_filter_chain_runs_in_test_mode_regardless_of_flag(self, _mock_sounddevice):
        """When ``_test_mode`` is True, the filter chain runs EVEN IF"""
        import voice_typer.server.level_monitor as lm

        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor
        lm._level_processor_config = None
        lm._level_bar_filtered = False
        # In test mode, filter chain must run regardless
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

            processor.process_chunk.assert_called()
        finally:
            # Reset test_mode + stop
            lm._test_mode = False
            lm.stop_monitoring()


class TestUpdateLevelProcessorPropagatesFlag:
    """``update_level_processor`` must propagate the"""

    def test_update_level_processor_sets_flag_false_by_default(self):
        """When ``level_bar_filtered`` is NOT in the config dict, the"""
        import voice_typer.server.level_monitor as lm

        # Reset
        lm._level_bar_filtered = True
        # Call update_level_processor with a config dict that doesn't
        lm.update_level_processor({"noise_filter_enabled": False})
        assert lm._level_bar_filtered is False, "update_level_processor must default _level_bar_filtered to False"

    def test_update_level_processor_sets_flag_true_when_opted_in(self):
        """When ``level_bar_filtered=True`` is in the config dict, the"""
        import voice_typer.server.level_monitor as lm

        lm.update_level_processor({"noise_filter_enabled": False, "level_bar_filtered": True})
        assert lm._level_bar_filtered is True, (
            "update_level_processor must set _level_bar_filtered=True when the config dict opts in"
        )
