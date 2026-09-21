"""Shared audio-statistic helpers used by the recording and transcription paths."""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server._audio_constants import (
    AUDIO_SILENCE_AMPLITUDE,
    peak_amplitude,
    silence_percent,
)


def test_peak_amplitude_matches_abs_max():
    for audio in (
        np.array([0.0, -0.9, 0.5], dtype=np.float32),
        np.array([-0.25, -0.25], dtype=np.float32),
        np.array([[0.1, -0.7], [0.3, 0.2]], dtype=np.float32),
    ):
        assert peak_amplitude(audio) == pytest.approx(float(np.max(np.abs(audio))))


def test_peak_amplitude_of_empty_audio_is_zero():
    assert peak_amplitude(np.array([], dtype=np.float32)) == 0.0


def test_silence_percent_matches_the_abs_threshold_definition():
    audio = np.array([0.0, 0.0005, -0.0005, 0.5, -0.9, 0.001], dtype=np.float32)
    expected = float(np.sum(np.abs(audio) < AUDIO_SILENCE_AMPLITUDE) / audio.size * 100)
    assert silence_percent(audio) == pytest.approx(expected)


def test_silence_percent_is_100_for_all_silent_and_0_for_all_loud():
    assert silence_percent(np.zeros(64, dtype=np.float32)) == 100.0
    assert silence_percent(np.full(64, 0.5, dtype=np.float32)) == 0.0


def test_silence_percent_accepts_multichannel_shapes():
    audio = np.zeros((4, 2), dtype=np.float32)
    assert silence_percent(audio) == 100.0
    assert silence_percent(np.array([], dtype=np.float32)) == 0.0
